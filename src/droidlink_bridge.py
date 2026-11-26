"""GNOME-side client for the self-hosted DroidLink HTTPS relay."""

import hashlib
import http.client
import json
import os
import re
import secrets
import ssl
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


PORT = 17891
PAIRING_LIFETIME = 120
MAX_BODY = 1_048_576
MAX_CAMERA_FRAME = 512_000
RELAY_URL = "https://droidlink.patapon.lol"
SESSION_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "droiddesk" / "droidlink-session.json"
RELAY_CONFIG_FILE = SESSION_FILE.with_name("droidlink-config.json")


def _normalize_relay_url(value):
    value = str(value or "").strip()
    if not value:
        raise ValueError("Entre le nom de domaine HTTPS du relais.")
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Le relais doit utiliser un nom d’hôte HTTPS valide.")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Entre uniquement le domaine du relais, sans chemin ni paramètres.")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Le port du relais est invalide.") from error
    hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    if ":" in hostname:
        host = f"[{hostname}]"
    else:
        host = hostname.rstrip(".")
        labels = host.split(".")
        if len(labels) < 2 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in labels
        ):
            raise ValueError("Utilise un nom de domaine complet, par exemple relay.example.net.")
    return f"https://{host}{f':{port}' if port else ''}"


class DroidLinkBridge:
    """Relay DroidLink events and commands between GNOME and Android."""

    def __init__(self, on_change=None, on_event=None):
        self.on_change = on_change
        self.on_event = on_event
        self.lock = threading.RLock()
        self.code = None
        self.code_expires = 0
        self.token = None
        self.session_id = None
        self.paired = False
        self.phone_online = False
        self.registered = False
        self.relay_error = ""
        self.relay_url = self._load_relay_url()
        self.relay_reachable = None
        self.relay_log = []
        self._last_relay_log_key = None
        self._last_relay_log_time = 0.0
        self._last_health_probe = 0.0
        self.phone = {}
        self.commands = []
        self.pair_attempts = 0
        self.stop_event = threading.Event()
        self.relay_wakeup = threading.Event()
        self.camera_stop_event = threading.Event()
        self.camera_thread = None
        self.camera_frame_callback = None
        self.camera_sequence = 0
        self._load_saved_session()
        self.server = None
        self.thread = None

        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                bridge._handle_post(self)

            def do_GET(self):
                bridge._handle_get(self)

            def log_message(self, _format, *_args):
                return

        class LocalServer(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = LocalServer(("127.0.0.1", PORT), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.relay_thread = threading.Thread(target=self._relay_loop, daemon=True)
        self.relay_thread.start()

    def close(self):
        self.stop_event.set()
        self.camera_stop_event.set()
        self.relay_wakeup.set()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if getattr(self, "relay_thread", None):
            self.relay_thread.join(timeout=2)

    def _load_saved_session(self):
        try:
            saved = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            token = saved.get("desktop_token") if isinstance(saved, dict) else None
            session_id = saved.get("session_id") if isinstance(saved, dict) else None
            phone = saved.get("phone", {}) if isinstance(saved, dict) else {}
            if (
                not isinstance(token, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{40,64}", token)
                or not isinstance(session_id, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", session_id)
            ):
                return
            if not isinstance(phone, dict):
                phone = {}
            self.token = token
            self.session_id = session_id
            self.paired = bool(saved.get("paired", False))
            self.phone_online = False
            self.registered = bool(saved.get("registered", True))
            pending_code = saved.get("code")
            remaining = float(saved.get("code_expires_at", 0)) - time.time()
            if isinstance(pending_code, str) and re.fullmatch(r"\d{6}", pending_code) and remaining > 0:
                self.code = pending_code
                self.code_expires = time.monotonic() + min(PAIRING_LIFETIME, remaining)
            saved_relay_url = saved.get("relay_url")
            if isinstance(saved_relay_url, str) and saved_relay_url.strip():
                self.relay_url = _normalize_relay_url(saved_relay_url)
            self.phone = {
                "manufacturer": str(phone.get("manufacturer", ""))[:80],
                "model": str(phone.get("model", ""))[:100],
                "android": str(phone.get("android", ""))[:40],
                "notificationAccess": bool(phone.get("notificationAccess", False)),
                "batteryPercent": self._bounded_int(phone.get("batteryPercent", -1), -1, 100),
            }
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return

    @staticmethod
    def _load_relay_url():
        try:
            config = json.loads(RELAY_CONFIG_FILE.read_text(encoding="utf-8"))
            return _normalize_relay_url(config.get("relay_url", "")) if isinstance(config, dict) else ""
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return ""

    def configure_relay_url(self, value):
        try:
            relay_url = _normalize_relay_url(value)
        except ValueError as error:
            return False, str(error)
        with self.lock:
            unchanged = self.relay_url == relay_url
            if not unchanged and self.session_id and self.token:
                return False, "Dissocie DroidLink avant de changer de domaine."
            try:
                RELAY_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.chmod(RELAY_CONFIG_FILE.parent, 0o700)
                temporary = RELAY_CONFIG_FILE.with_suffix(".tmp")
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump({"relay_url": relay_url}, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, RELAY_CONFIG_FILE)
            except OSError as error:
                return False, f"Impossible d’enregistrer la configuration : {error}"
            self.relay_url = relay_url
            self.relay_error = ""
        self.relay_wakeup.set()
        self._changed()
        return True, "Domaine du relais déjà configuré." if unchanged else "Domaine du relais enregistré."

    def _save_session_locked(self):
        if not self.token:
            try:
                SESSION_FILE.unlink(missing_ok=True)
            except OSError:
                pass
            return
        temporary = None
        try:
            SESSION_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(SESSION_FILE.parent, 0o700)
            descriptor, temporary = tempfile.mkstemp(prefix=".droidlink-", dir=SESSION_FILE.parent)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({
                    "desktop_token": self.token,
                    "session_id": self.session_id,
                    "relay_url": self.relay_url,
                    "paired": self.paired,
                    "registered": self.registered,
                    "code": self.code,
                    "code_expires_at": time.time() + max(0, self.code_expires - time.monotonic()) if self.code else 0,
                    "phone": self.phone,
                }, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, SESSION_FILE)
        except OSError:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def begin_pairing(self):
        with self.lock:
            if not self.relay_url:
                self.relay_error = "Configure le domaine HTTPS du relais avant l’association."
                code = None
            else:
                code = True
        if code is None:
            self._changed()
            return None
        previous = None
        with self.lock:
            if self.session_id and self.token:
                previous = (self.relay_url, self.session_id, self.token)
            self.code = f"{secrets.randbelow(1_000_000):06d}"
            self.code_expires = time.monotonic() + PAIRING_LIFETIME
            self.pair_attempts = 0
            self.token = secrets.token_urlsafe(32)
            self.session_id = secrets.token_urlsafe(24)
            self.paired = False
            self.phone_online = False
            self.registered = False
            self.relay_error = ""
            self.phone = {}
            self.commands.clear()
            self._save_session_locked()
            code = self.code
        if previous:
            threading.Thread(target=self._revoke_remote, args=previous, daemon=True).start()
        self.relay_wakeup.set()
        self._changed()
        return code

    def cancel_pairing(self):
        self.stop_camera_receive()
        with self.lock:
            previous = (self.relay_url, self.session_id, self.token) if self.session_id and self.token else None
            self.code = None
            self.code_expires = 0
            self.token = None
            self.session_id = None
            self.paired = False
            self.phone_online = False
            self.registered = False
            self.relay_error = ""
            self.phone = {}
            self._save_session_locked()
        if previous:
            threading.Thread(target=self._revoke_remote, args=previous, daemon=True).start()
        self.relay_wakeup.set()
        self._changed()

    def disconnect(self):
        self.stop_camera_receive()
        with self.lock:
            previous = (self.relay_url, self.session_id, self.token) if self.session_id and self.token else None
            self.code = None
            self.code_expires = 0
            self.token = None
            self.session_id = None
            self.paired = False
            self.phone_online = False
            self.registered = False
            self.relay_error = ""
            self.phone = {}
            self._save_session_locked()
        if previous:
            threading.Thread(target=self._revoke_remote, args=previous, daemon=True).start()
        self.relay_wakeup.set()
        self._changed()

    def start_camera_receive(self, callback):
        with self.lock:
            if not self.paired or not self.session_id or not self.token:
                return False
            self.camera_frame_callback = callback
            self.camera_stop_event.clear()
            if self.camera_thread and self.camera_thread.is_alive():
                return True
            self.camera_sequence = 0
            self.camera_thread = threading.Thread(target=self._camera_frame_loop, daemon=True)
            self.camera_thread.start()
        return True

    def stop_camera_receive(self):
        self.camera_stop_event.set()
        with self.lock:
            self.camera_frame_callback = None

    def _camera_frame_loop(self):
        connection = None
        connection_origin = None
        while not self.stop_event.is_set() and not self.camera_stop_event.is_set():
            with self.lock:
                session_id, token = self.session_id, self.token
                relay_url = self.relay_url
                sequence = self.camera_sequence
                callback = self.camera_frame_callback
                paired = self.paired
            if not paired or not session_id or not token or not callback:
                self.camera_stop_event.wait(0.5)
                continue
            try:
                parsed = urlsplit(relay_url)
                if parsed.scheme not in ("https", "http") or not parsed.hostname:
                    raise ValueError("Adresse du relais invalide")
                origin = (parsed.scheme, parsed.hostname, parsed.port)
                if connection is None or connection_origin != origin:
                    if connection is not None:
                        connection.close()
                    if parsed.scheme == "https":
                        connection = http.client.HTTPSConnection(
                            parsed.hostname,
                            parsed.port or 443,
                            timeout=9,
                            context=ssl.create_default_context(),
                        )
                    else:
                        connection = http.client.HTTPConnection(
                            parsed.hostname,
                            parsed.port or 80,
                            timeout=9,
                        )
                    connection_origin = origin
                path = (
                    f"{parsed.path.rstrip('/')}/v1/sessions/{session_id}/camera/frame"
                    f"?after={sequence}&wait=5"
                )
                connection.request(
                    "GET",
                    path,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "image/jpeg",
                        "Connection": "keep-alive",
                    },
                )
                response = connection.getresponse()
                frame = response.read(MAX_CAMERA_FRAME + 1)
                if response.status == 204:
                    continue
                if response.status != 200:
                    if response.status in (401, 404):
                        self.camera_stop_event.wait(1)
                    else:
                        self.camera_stop_event.wait(0.3)
                    continue
                if not frame or len(frame) > MAX_CAMERA_FRAME:
                    continue
                next_sequence = int(response.headers.get("X-Frame-Sequence", "0"))
                rotation = int(response.headers.get("X-Frame-Rotation", "0"))
                if next_sequence <= sequence or rotation not in (0, 90, 180, 270):
                    continue
                with self.lock:
                    if self.camera_stop_event.is_set():
                        break
                    self.camera_sequence = next_sequence
                    callback = self.camera_frame_callback
                if callback:
                    try:
                        callback(frame, rotation)
                    except Exception:
                        self.camera_stop_event.set()
                        break
            except (http.client.HTTPException, OSError, URLError, TimeoutError, ValueError):
                if connection is not None:
                    connection.close()
                    connection = None
                    connection_origin = None
                self.camera_stop_event.wait(0.3)
        if connection is not None:
            connection.close()

    def snapshot(self):
        with self.lock:
            seconds = max(0, int(self.code_expires - time.monotonic())) if self.code else 0
            if self.code and seconds == 0:
                self.code = None
                self.relay_error = "Le code a expiré. Relance l’association."
                self._save_session_locked()
            cache_id = hashlib.sha256(self.session_id.encode("utf-8")).hexdigest()[:24] if self.session_id else ""
            return {
                "code": self.code,
                "seconds": seconds,
                "paired": self.paired,
                "registered": self.registered,
                "online": self.paired and self.phone_online and not self.relay_error,
                "relay_error": self.relay_error,
                "relay_url": self.relay_url,
                "relay_reachable": self.relay_reachable,
                "relay_log": list(self.relay_log),
                "cache_id": cache_id,
                "phone": dict(self.phone),
            }

    def enqueue_command(self, action, key, **details):
        with self.lock:
            if not self.paired:
                return False
            command = {"action": action, "key": str(key)[:256]}
            for field, limit in (("remoteInputKey", 120), ("text", 2_000), ("actionTitle", 80)):
                if field in details:
                    command[field] = str(details[field])[:limit]
            if "packages" in details:
                packages = details["packages"]
                if not isinstance(packages, (list, tuple)):
                    return False
                command["packages"] = [
                    str(package)[:160]
                    for package in packages[:10]
                    if re.fullmatch(r"[A-Za-z0-9_.]+", str(package))
                ]
            if "photoId" in details:
                try:
                    command["photoId"] = max(1, int(details["photoId"]))
                except (TypeError, ValueError):
                    return False
            if "actionIndex" in details:
                try:
                    command["actionIndex"] = max(0, min(9, int(details["actionIndex"])))
                except (TypeError, ValueError):
                    return False
            self.commands.append(command)
            del self.commands[:-100]
        self.relay_wakeup.set()
        return True

    def _relay_request(self, method, path, payload=None, *, relay_url=None, token=None, timeout=8):
        url = (relay_url or self.relay_url).rstrip("/") + path
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read(MAX_BODY + 1)
                if len(raw) > MAX_BODY:
                    result = response.status, {"error": "Réponse du relais trop volumineuse."}
                else:
                    result = response.status, json.loads(raw.decode("utf-8")) if raw else {}
                self._record_relay_result(method, path, result[0], result[1])
                return result
        except HTTPError as error:
            try:
                payload = json.loads(error.read(MAX_BODY).decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                payload = {"error": "Le relais a refusé la requête."}
            self._record_relay_result(method, path, error.code, payload)
            return error.code, payload
        except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
            detail = str(error)[:240] or "Relais inaccessible."
            self._record_relay_result(method, path, 0, {"error": detail})
            return 0, {"error": detail}

    def _record_relay_result(self, method, path, status, response):
        route = re.sub(r"/v1/sessions/[A-Za-z0-9_-]{20,64}", "/v1/sessions/{id}", path.split("?", 1)[0])
        reachable = 200 <= status < 500
        detail = f"HTTP {status}" if status else str(response.get("error") or "Connexion impossible")[:180]
        now = time.time()
        key = (method, route, detail)
        changed = False
        with self.lock:
            if self.relay_reachable != reachable:
                self.relay_reachable = reachable
                changed = True
            if key != self._last_relay_log_key or now - self._last_relay_log_time >= 15:
                self.relay_log.insert(0, {
                    "timestamp": now,
                    "route": f"{method} {route}",
                    "detail": detail,
                    "success": 200 <= status < 300,
                })
                del self.relay_log[20:]
                self._last_relay_log_key = key
                self._last_relay_log_time = now
                changed = True
        if changed:
            self._changed()

    def _relay_loop(self):
        while not self.stop_event.is_set():
            with self.lock:
                session_id, token = self.session_id, self.token
                code = self.code
                registered = self.registered
                relay_url = self.relay_url
            if not session_id or not token:
                now = time.monotonic()
                if relay_url and now - self._last_health_probe >= 15:
                    self._last_health_probe = now
                    self._relay_request("GET", "/healthz", timeout=4)
                self.relay_wakeup.wait(1)
                self.relay_wakeup.clear()
                continue
            if not relay_url:
                with self.lock:
                    self.relay_error = "Configure le domaine HTTPS du relais avant l’association."
                self._changed()
                self.relay_wakeup.wait(3)
                self.relay_wakeup.clear()
                continue
            if not registered:
                if not code or time.monotonic() >= self.code_expires:
                    with self.lock:
                        if self.session_id == session_id and self.code:
                            self.code = None
                            self.relay_error = "Le code a expiré. Relance l’association."
                            self._save_session_locked()
                    self._changed()
                    self.relay_wakeup.wait(2)
                    self.relay_wakeup.clear()
                    continue
                status, response = self._relay_request(
                    "POST",
                    "/v1/sessions",
                    {"sessionId": session_id, "desktopToken": token, "code": code or ""},
                    relay_url=relay_url,
                )
                if status == 201:
                    with self.lock:
                        if self.session_id == session_id:
                            self.registered = True
                            self.relay_error = ""
                            self._save_session_locked()
                    self._changed()
                    continue
                with self.lock:
                    if self.session_id == session_id:
                        self.relay_error = str(response.get("error") or "Relais DroidLink inaccessible.")
                        if status == 409 and code:
                            self.code = f"{secrets.randbelow(1_000_000):06d}"
                            self.code_expires = time.monotonic() + PAIRING_LIFETIME
                            self.relay_error = ""
                            self._save_session_locked()
                        elif code and time.monotonic() >= self.code_expires:
                            self.code = None
                            self.relay_error = "Le code a expiré. Relance l’association."
                self._changed()
                self.relay_wakeup.wait(3)
                self.relay_wakeup.clear()
                continue

            self._flush_remote_commands(session_id, token, relay_url)
            status, response = self._relay_request(
                "GET",
                f"/v1/sessions/{session_id}/events?wait=2",
                relay_url=relay_url,
                token=token,
                timeout=7,
            )
            if status == 200:
                with self.lock:
                    if self.session_id == session_id:
                        new_paired = bool(response.get("paired", False))
                        online = bool(response.get("online", False))
                        changed = new_paired != self.paired or online != self.phone_online or response.get("phone", {}) != self.phone
                        self.paired = new_paired
                        self.phone_online = online
                        self.phone = response.get("phone", {}) if isinstance(response.get("phone"), dict) else {}
                        if self.paired:
                            self.code = None
                            self.code_expires = 0
                        self.relay_error = ""
                        if changed:
                            self._save_session_locked()
                    else:
                        changed = False
                if changed:
                    self._changed()
                for event in response.get("events", []) if isinstance(response.get("events"), list) else []:
                    self._deliver_relay_event(session_id, token, event)
                self._flush_remote_commands(session_id, token, relay_url)
                continue
            with self.lock:
                if self.session_id == session_id:
                    self.relay_error = str(response.get("error") or "Connexion au relais interrompue.")
                    if status in (401, 404):
                        self.paired = False
                        self.phone_online = False
                        self.registered = False
                        self.code = None
                        self.code_expires = 0
                        self._save_session_locked()
            self._changed()
            if status in (401, 404):
                self.relay_wakeup.wait(3)
                self.relay_wakeup.clear()
            else:
                self.relay_wakeup.wait(2)
                self.relay_wakeup.clear()

    def _flush_remote_commands(self, session_id, token, relay_url):
        while not self.stop_event.is_set():
            with self.lock:
                if self.session_id != session_id or not self.paired or not self.commands:
                    return
                command = self.commands[0]
            status, _response = self._relay_request(
                "POST",
                f"/v1/sessions/{session_id}/commands",
                command,
                relay_url=relay_url,
                token=token,
            )
            if status != 202:
                return
            with self.lock:
                if self.commands and self.commands[0] == command:
                    self.commands.pop(0)

    def _deliver_relay_event(self, session_id, token, event):
        if not isinstance(event, dict):
            return
        body = json.dumps(event, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"http://127.0.0.1:{PORT}/events",
            data=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=3) as response:
                response.read(1024)
        except (OSError, URLError, TimeoutError):
            return

    def _revoke_remote(self, relay_url, session_id, token):
        self._relay_request(
            "POST",
            f"/v1/sessions/{session_id}/unpair",
            {},
            relay_url=relay_url,
            token=token,
            timeout=3,
        )

    def _changed(self):
        if self.on_change:
            from gi.repository import GLib

            GLib.idle_add(self.on_change)

    def _event(self, payload):
        if self.on_event:
            from gi.repository import GLib

            GLib.idle_add(self.on_event, payload)

    @staticmethod
    def _respond(handler, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(body)

    def _read_json(self, handler):
        try:
            size = int(handler.headers.get("Content-Length", "0"))
            if size < 0 or size > MAX_BODY:
                return None
            return json.loads(handler.rfile.read(size).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _bounded_int(value, minimum, maximum):
        try:
            return max(minimum, min(maximum, int(value)))
        except (TypeError, ValueError):
            return minimum

    def _authorized(self, handler):
        supplied = handler.headers.get("Authorization", "")
        if not supplied.startswith("Bearer "):
            return False
        with self.lock:
            expected = self.token
        return expected is not None and secrets.compare_digest(supplied[7:], expected)

    def _handle_post(self, handler):
        if handler.path == "/pair":
            payload = self._read_json(handler)
            if not isinstance(payload, dict):
                self._respond(handler, 400, {"error": "Requête invalide."})
                return
            supplied = str(payload.get("code", ""))
            with self.lock:
                expired = not self.code or time.monotonic() >= self.code_expires
                if expired or self.pair_attempts >= 10 or not secrets.compare_digest(supplied, self.code):
                    self.pair_attempts += 1
                    if expired or self.pair_attempts >= 10:
                        self.code = None
                    accepted = False
                    token = None
                else:
                    token = secrets.token_urlsafe(32)
                    self.token = token
                    self.code = None
                    self.code_expires = 0
                    self.phone = {
                        "manufacturer": str(payload.get("manufacturer", ""))[:80],
                        "model": str(payload.get("model", ""))[:100],
                        "android": str(payload.get("android", ""))[:40],
                        "notificationAccess": bool(payload.get("notificationAccess", False)),
                        "batteryPercent": self._bounded_int(payload.get("batteryPercent", -1), -1, 100),
                    }
                    self._save_session_locked()
                    accepted = True
            if not accepted:
                self._respond(handler, 401, {"error": "Code incorrect ou expiré."})
                return
            self._respond(handler, 200, {"token": token})
            self._changed()
            return

        if not self._authorized(handler):
            self._respond(handler, 401, {"error": "DroidLink n’est pas associé."})
            return

        payload = self._read_json(handler)
        if not isinstance(payload, dict):
            self._respond(handler, 400, {"error": "Requête invalide."})
            return

        if handler.path == "/events":
            event_type = str(payload.get("type", ""))[:32]
            if event_type == "command_result":
                event = {
                    "type": event_type,
                    "action": str(payload.get("action", ""))[:40],
                    "key": str(payload.get("key", ""))[:256],
                    "success": bool(payload.get("success", False)),
                    "message": str(payload.get("message", ""))[:200],
                }
            elif event_type == "camera_status":
                event = {
                    "type": event_type,
                    "running": bool(payload.get("running", False)),
                    "message": str(payload.get("message", ""))[:240],
                }
            elif event_type == "photo_list":
                items = []
                source_items = payload.get("items", [])
                if isinstance(source_items, list):
                    for item in source_items[:2_000]:
                        if not isinstance(item, dict):
                            continue
                        image_id = self._bounded_int(item.get("id", 0), 0, 2**63 - 1)
                        if image_id <= 0:
                            continue
                        path = str(item.get("path", ""))[:800]
                        if path and (not path.startswith(("/storage/", "/sdcard/")) or "\x00" in path):
                            path = ""
                        items.append({
                            "id": image_id,
                            "name": str(item.get("name", ""))[:160],
                            "date": self._bounded_int(item.get("date", 0), 0, 2**63 - 1),
                            "size": self._bounded_int(item.get("size", 0), 0, 2**63 - 1),
                            "path": path,
                        })
                event = {"type": event_type, "items": items}
            elif event_type in ("photo_thumbnail", "photo_thumbnail_error", "photo_preview", "photo_preview_error"):
                image_id = self._bounded_int(payload.get("id", 0), 0, 2**63 - 1)
                event = {"type": event_type, "id": image_id}
                if event_type in ("photo_thumbnail", "photo_preview"):
                    encoded = str(payload.get("jpeg", ""))
                    maximum = 64_000 if event_type == "photo_thumbnail" else 700_000
                    if len(encoded) > maximum or not all(char.isalnum() or char in "+/=-_" for char in encoded):
                        self._respond(handler, 400, {"error": "Image invalide."})
                        return
                    event["jpeg"] = encoded
                else:
                    event["message"] = str(payload.get("message", ""))[:200]
            elif event_type == "photo_error":
                event = {"type": event_type, "message": str(payload.get("message", ""))[:240]}
            elif event_type == "photo_delete_result":
                event = {
                    "type": event_type,
                    "id": self._bounded_int(payload.get("id", 0), 0, 2**63 - 1),
                    "success": bool(payload.get("success", False)),
                    "message": str(payload.get("message", ""))[:240],
                }
            elif event_type == "app_icons":
                items = []
                source_items = payload.get("items", [])
                if isinstance(source_items, list):
                    for item in source_items[:10]:
                        if not isinstance(item, dict):
                            continue
                        package = str(item.get("package", ""))[:160]
                        encoded = str(item.get("png", ""))
                        if not re.fullmatch(r"[A-Za-z0-9_.]+", package):
                            continue
                        if len(encoded) > 80_000 or not all(char.isalnum() or char in "+/=-_" for char in encoded):
                            continue
                        items.append({
                            "package": package,
                            "label": str(item.get("label", ""))[:100],
                            "png": encoded,
                        })
                event = {"type": event_type, "items": items}
            elif event_type == "contacts_list":
                items = []
                source_items = payload.get("items", [])
                if isinstance(source_items, list):
                    for item in source_items[:5_000]:
                        if not isinstance(item, dict):
                            continue
                        source_numbers = item.get("numbers", [])
                        numbers = []
                        if isinstance(source_numbers, list):
                            numbers = [str(number)[:80] for number in source_numbers[:20] if str(number).strip()]
                        if numbers:
                            items.append({
                                "id": str(item.get("id", ""))[:80],
                                "name": str(item.get("name", "Contact sans nom"))[:160],
                                "numbers": numbers,
                            })
                event = {"type": event_type, "items": items}
            elif event_type == "contacts_error":
                event = {"type": event_type, "message": str(payload.get("message", ""))[:240]}
            else:
                event = {"key": str(payload.get("key", ""))[:256], "removed": bool(payload.get("removed", False))}
                if not event["removed"]:
                    actions = payload.get("actions", [])
                    normalized_actions = []
                    if isinstance(actions, list):
                        for action in actions[:10]:
                            if not isinstance(action, dict):
                                continue
                            try:
                                index = int(action.get("index", -1))
                            except (TypeError, ValueError):
                                continue
                            if index >= 0:
                                normalized_actions.append({
                                    "index": index,
                                    "title": str(action.get("title", ""))[:80],
                                    "remoteInputKey": str(action.get("remoteInputKey", ""))[:120],
                                })
                    event.update({
                        "app": str(payload.get("app", ""))[:160],
                        "title": str(payload.get("title", ""))[:180],
                        "text": str(payload.get("text", ""))[:800],
                        "isMessaging": bool(payload.get("isMessaging", False)),
                        "isSmsApp": bool(payload.get("isSmsApp", False)),
                        "conversationTitle": str(payload.get("conversationTitle", ""))[:180],
                        "isGroupConversation": bool(payload.get("isGroupConversation", False)),
                        "category": str(payload.get("category", ""))[:40],
                        "ongoing": bool(payload.get("ongoing", False)),
                        "isIncomingCall": bool(payload.get("isIncomingCall", False)),
                        "postedAt": self._bounded_int(payload.get("postedAt", 0), 0, 2**63 - 1),
                        "actions": normalized_actions,
                    })
                    messages = payload.get("messages", [])
                    normalized_messages = []
                    if isinstance(messages, list):
                        for message in messages[:20]:
                            if isinstance(message, dict):
                                normalized_messages.append({
                                    "sender": str(message.get("sender", ""))[:120],
                                    "text": str(message.get("text", ""))[:600],
                                    "timestamp": self._bounded_int(message.get("timestamp", 0), 0, 2**63 - 1),
                                    "mediaType": str(message.get("mediaType", ""))[:80],
                                })
                    event["messages"] = normalized_messages
            self._respond(handler, 202, {"accepted": True})
            self._event(event)
        elif handler.path == "/phone":
            with self.lock:
                self.phone.update({
                    "manufacturer": str(payload.get("manufacturer", self.phone.get("manufacturer", "")))[:80],
                    "model": str(payload.get("model", self.phone.get("model", "")))[:100],
                    "android": str(payload.get("android", self.phone.get("android", "")))[:40],
                    "notificationAccess": bool(payload.get("notificationAccess", self.phone.get("notificationAccess", False))),
                    "batteryPercent": self._bounded_int(payload.get("batteryPercent", self.phone.get("batteryPercent", -1)), -1, 100),
                })
                self._save_session_locked()
            self._respond(handler, 200, {"updated": True})
            self._changed()
        elif handler.path == "/unpair":
            self.disconnect()
            self._respond(handler, 200, {"paired": False})
        else:
            self._respond(handler, 404, {"error": "Route inconnue."})

    def _handle_get(self, handler):
        if handler.path != "/commands":
            self._respond(handler, 404, {"error": "Route inconnue."})
            return
        if not self._authorized(handler):
            self._respond(handler, 401, {"error": "DroidLink n’est pas associé."})
            return
        with self.lock:
            commands = self.commands[:20]
            del self.commands[:20]
        self._respond(handler, 200, {"commands": commands})
