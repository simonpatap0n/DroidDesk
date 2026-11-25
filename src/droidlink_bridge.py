"""Local HTTP bridge used by the DroidLink Android companion."""

import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PORT = 17891
PAIRING_LIFETIME = 120
MAX_BODY = 1_048_576
SESSION_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "droiddesk" / "droidlink-session.json"


class DroidLinkBridge:
    """Receive a one-time pairing and notification events over adb reverse."""

    def __init__(self, on_change=None, on_event=None):
        self.on_change = on_change
        self.on_event = on_event
        self.lock = threading.RLock()
        self.code = None
        self.code_expires = 0
        self.token = None
        self.phone = {}
        self._load_saved_session()
        self.commands = []
        self.pair_attempts = 0
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

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    def _load_saved_session(self):
        try:
            saved = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            token = saved.get("token") if isinstance(saved, dict) else None
            phone = saved.get("phone", {}) if isinstance(saved, dict) else {}
            if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
                return
            if not isinstance(phone, dict):
                phone = {}
            self.token = token
            self.phone = {
                "manufacturer": str(phone.get("manufacturer", ""))[:80],
                "model": str(phone.get("model", ""))[:100],
                "android": str(phone.get("android", ""))[:40],
                "notificationAccess": bool(phone.get("notificationAccess", False)),
                "batteryPercent": self._bounded_int(phone.get("batteryPercent", -1), -1, 100),
            }
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return

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
                json.dump({"token": self.token, "phone": self.phone}, stream, ensure_ascii=False)
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
            self.code = f"{secrets.randbelow(1_000_000):06d}"
            self.code_expires = time.monotonic() + PAIRING_LIFETIME
            self.pair_attempts = 0
            self.token = None
            self.phone = {}
            self.commands.clear()
            self._save_session_locked()
            code = self.code
        self._changed()
        return code

    def cancel_pairing(self):
        with self.lock:
            self.code = None
            self.code_expires = 0
        self._changed()

    def disconnect(self):
        with self.lock:
            self.code = None
            self.code_expires = 0
            self.token = None
            self.phone = {}
            self._save_session_locked()
        self._changed()

    def snapshot(self):
        with self.lock:
            seconds = max(0, int(self.code_expires - time.monotonic())) if self.code else 0
            if self.code and seconds == 0:
                self.code = None
            cache_id = hashlib.sha256(self.token.encode("utf-8")).hexdigest()[:24] if self.token else ""
            return {
                "code": self.code,
                "seconds": seconds,
                "paired": self.token is not None,
                "cache_id": cache_id,
                "phone": dict(self.phone),
            }

    def enqueue_command(self, action, key, **details):
        with self.lock:
            if not self.token:
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
        return True

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
