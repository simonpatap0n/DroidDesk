#!/usr/bin/env python3
"""Small, self-hosted HTTPS relay for DroidLink."""

import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


MAX_BODY = 1_048_576
MAX_QUEUE = 100
PAIR_TTL = 120
PAIR_ATTEMPTS = 8
SESSION_RE = re.compile(r"[A-Za-z0-9_-]{20,64}")
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{40,64}")


def token_hash(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class Relay:
    def __init__(self):
        data_dir = Path(os.environ.get("DROIDLINK_DATA_DIR", "/data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(data_dir / "relay.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.pair_codes = {}
        self.events = defaultdict(lambda: deque(maxlen=MAX_QUEUE))
        self.commands = defaultdict(lambda: deque(maxlen=MAX_QUEUE))
        self.failed_pair_attempts = deque()
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    desktop_hash TEXT NOT NULL,
                    phone_hash TEXT,
                    phone_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL DEFAULT 0
                )"""
            )
            self.db.commit()

    def create_session(self, payload):
        session_id = str(payload.get("sessionId", ""))
        desktop_token = str(payload.get("desktopToken", ""))
        code = str(payload.get("code", ""))
        if not SESSION_RE.fullmatch(session_id) or not TOKEN_RE.fullmatch(desktop_token) or not re.fullmatch(r"\d{6}", code):
            return 400, {"error": "Paramètres d’association invalides."}
        now = time.time()
        with self.changed:
            self.db.execute(
                "DELETE FROM sessions WHERE COALESCE(NULLIF(last_seen_at, 0), created_at) < ?",
                (now - 180 * 24 * 60 * 60,),
            )
            for old_code, pairing in list(self.pair_codes.items()):
                if pairing["expires"] <= now:
                    del self.pair_codes[old_code]
            existing = self._session(session_id)
            if existing and not secrets.compare_digest(existing["desktop_hash"], token_hash(desktop_token)):
                return 409, {"error": "Identifiant de session déjà utilisé."}
            if existing and existing["phone_hash"]:
                self.db.commit()
                return 201, {"registered": True, "expiresIn": PAIR_TTL}
            active_code = next(
                (active for active, pairing in self.pair_codes.items() if pairing["session_id"] == session_id),
                None,
            )
            if active_code == code:
                self.db.commit()
                return 201, {"registered": True, "expiresIn": PAIR_TTL}
            if code in self.pair_codes:
                return 409, {"error": "Code déjà utilisé. Relance l’association."}
            if active_code:
                self.pair_codes.pop(active_code, None)
            if existing:
                self.db.execute(
                    "UPDATE sessions SET created_at=?, phone_json='{}', last_seen_at=0 WHERE session_id=?",
                    (now, session_id),
                )
            else:
                self.db.execute(
                    "INSERT INTO sessions(session_id, desktop_hash, phone_hash, phone_json, created_at, last_seen_at) VALUES(?, ?, NULL, '{}', ?, 0)",
                    (session_id, token_hash(desktop_token), now),
                )
            self.db.commit()
            self.pair_codes[code] = {"session_id": session_id, "expires": now + PAIR_TTL, "attempts": 0}
            self.events.pop(session_id, None)
            self.commands.pop(session_id, None)
            self.changed.notify_all()
        return 201, {"registered": True, "expiresIn": PAIR_TTL}

    def pair(self, payload):
        code = str(payload.get("code", ""))
        now = time.time()
        with self.changed:
            while self.failed_pair_attempts and now - self.failed_pair_attempts[0] > 60:
                self.failed_pair_attempts.popleft()
            if len(self.failed_pair_attempts) >= 30:
                return 429, {"error": "Trop de tentatives. Attends une minute avant de réessayer."}
            pairing = self.pair_codes.get(code)
            if not pairing or pairing["expires"] <= now or pairing["attempts"] >= PAIR_ATTEMPTS:
                self.failed_pair_attempts.append(now)
                self.pair_codes.pop(code, None)
                return 401, {"error": "Code incorrect ou expiré."}
            phone = payload.get("phone", {})
            if not isinstance(phone, dict):
                phone = {}
            pairing["attempts"] += 1
            row = self.db.execute(
                "SELECT session_id FROM sessions WHERE session_id=?",
                (pairing["session_id"],),
            ).fetchone()
            if row is None:
                self.pair_codes.pop(code, None)
                return 401, {"error": "Association expirée. Recommence depuis DroidDesk."}
            phone_token = secrets.token_urlsafe(32)
            safe_phone = self._phone_fields(phone)
            self.db.execute(
                "UPDATE sessions SET phone_hash=?, phone_json=?, last_seen_at=? WHERE session_id=?",
                (token_hash(phone_token), json.dumps(safe_phone, ensure_ascii=False), now, pairing["session_id"]),
            )
            self.db.commit()
            session_id = pairing["session_id"]
            self.pair_codes.pop(code, None)
            self.changed.notify_all()
        return 200, {"sessionId": session_id, "token": phone_token}

    @staticmethod
    def _phone_fields(phone):
        def bounded(value, length):
            return str(value or "")[:length]

        try:
            battery = max(-1, min(100, int(phone.get("batteryPercent", -1))))
        except (TypeError, ValueError):
            battery = -1
        return {
            "manufacturer": bounded(phone.get("manufacturer"), 80),
            "model": bounded(phone.get("model"), 100),
            "android": bounded(phone.get("android"), 40),
            "notificationAccess": bool(phone.get("notificationAccess", False)),
            "batteryPercent": battery,
        }

    def _session(self, session_id):
        return self.db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()

    def authenticate(self, session_id, supplied, role):
        if not SESSION_RE.fullmatch(session_id) or not TOKEN_RE.fullmatch(supplied):
            return False
        with self.lock:
            row = self._session(session_id)
            if row is None:
                return False
            field = "desktop_hash" if role == "desktop" else "phone_hash"
            expected = row[field]
            return bool(expected and secrets.compare_digest(expected, token_hash(supplied)))

    def status(self, session_id):
        with self.lock:
            row = self._session(session_id)
            if row is None:
                return {"paired": False, "online": False, "phone": {}}
            phone = json.loads(row["phone_json"] or "{}")
            return {
                "paired": bool(row["phone_hash"]),
                "online": bool(row["phone_hash"] and time.time() - row["last_seen_at"] < 45),
                "phone": phone,
            }

    def update_phone(self, session_id, payload):
        with self.changed:
            row = self._session(session_id)
            current = json.loads(row["phone_json"] or "{}") if row else {}
            current.update(self._phone_fields({**current, **payload}))
            self.db.execute(
                "UPDATE sessions SET phone_json=?, last_seen_at=? WHERE session_id=?",
                (json.dumps(current, ensure_ascii=False), time.time(), session_id),
            )
            self.db.commit()
            self.changed.notify_all()

    def add_event(self, session_id, payload):
        if not isinstance(payload, dict):
            return False
        with self.changed:
            if self._session(session_id) is None:
                return False
            self.events[session_id].append(payload)
            self.db.execute("UPDATE sessions SET last_seen_at=? WHERE session_id=?", (time.time(), session_id))
            self.db.commit()
            self.changed.notify_all()
        return True

    def add_command(self, session_id, payload):
        if not isinstance(payload, dict):
            return False
        with self.changed:
            if self._session(session_id) is None:
                return False
            self.commands[session_id].append(payload)
            self.changed.notify_all()
        return True

    def poll(self, session_id, queue_name, timeout):
        deadline = time.monotonic() + max(0, min(25, timeout))
        with self.changed:
            if queue_name == "commands":
                self.db.execute("UPDATE sessions SET last_seen_at=? WHERE session_id=?", (time.time(), session_id))
                self.db.commit()
            queue = self.commands[session_id] if queue_name == "commands" else self.events[session_id]
            while not queue:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.changed.wait(remaining)
            items = []
            encoded_size = 0
            while queue:
                item = queue[0]
                item_size = len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                if items and encoded_size + item_size > 900_000:
                    break
                items.append(queue.popleft())
                encoded_size += item_size
                if len(items) >= (4 if queue_name == "events" else MAX_QUEUE):
                    break
            if queue_name == "events":
                return {**self.status(session_id), "events": items}
            return {"commands": items}

    def unpair(self, session_id):
        with self.changed:
            self.db.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
            self.db.commit()
            self.events.pop(session_id, None)
            self.commands.pop(session_id, None)
            for code, pairing in list(self.pair_codes.items()):
                if pairing["session_id"] == session_id:
                    del self.pair_codes[code]
            self.changed.notify_all()


class Handler(BaseHTTPRequestHandler):
    server_version = "DroidLinkRelay/1"

    def log_message(self, _fmt, *_args):
        return

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/healthz":
            self._respond(200, {"ok": True})
            return
        if self._requires_https() and self.headers.get("X-Forwarded-Proto", "").lower() != "https":
            self._respond(426, {"error": "Utilise la connexion HTTPS publique."})
            return
        session_id, action = self._route(parsed.path)
        if not session_id:
            self._respond(404, {"error": "Route inconnue."})
            return
        if action == "status" and self._authorized(session_id, "desktop"):
            self._respond(200, self.server.relay.status(session_id))
        elif action == "events" and self._authorized(session_id, "desktop"):
            wait = self._wait_seconds(parsed.query)
            self._respond(200, self.server.relay.poll(session_id, "events", wait))
        elif action == "commands" and self._authorized(session_id, "phone"):
            wait = self._wait_seconds(parsed.query)
            self._respond(200, self.server.relay.poll(session_id, "commands", wait))
        else:
            self._respond(401, {"error": "Session DroidLink invalide."})

    def do_POST(self):
        parsed = urlsplit(self.path)
        if self._requires_https() and self.headers.get("X-Forwarded-Proto", "").lower() != "https":
            self._respond(426, {"error": "Utilise la connexion HTTPS publique."})
            return
        payload = self._read_json()
        if payload is None:
            self._respond(400, {"error": "Requête JSON invalide."})
            return
        if parsed.path == "/v1/sessions":
            status, response = self.server.relay.create_session(payload)
            self._respond(status, response)
            return
        if parsed.path == "/v1/pair":
            status, response = self.server.relay.pair(payload)
            self._respond(status, response)
            return
        session_id, action = self._route(parsed.path)
        if not session_id:
            self._respond(404, {"error": "Route inconnue."})
            return
        relay = self.server.relay
        if action == "events" and self._authorized(session_id, "phone"):
            accepted = relay.add_event(session_id, payload)
            self._respond(202 if accepted else 404, {"accepted": accepted})
        elif action == "commands" and self._authorized(session_id, "desktop"):
            accepted = relay.add_command(session_id, payload)
            self._respond(202 if accepted else 404, {"accepted": accepted})
        elif action == "phone" and self._authorized(session_id, "phone"):
            relay.update_phone(session_id, payload)
            self._respond(200, {"updated": True})
        elif action == "unpair" and (
            self._authorized(session_id, "desktop") or self._authorized(session_id, "phone")
        ):
            relay.unpair(session_id)
            self._respond(200, {"paired": False})
        else:
            self._respond(401, {"error": "Session DroidLink invalide."})

    def _read_json(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > MAX_BODY:
                return None
            value = json.loads(self.rfile.read(size).decode("utf-8"))
            return value if isinstance(value, dict) else None
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _authorized(self, session_id, role):
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            return False
        return self.server.relay.authenticate(session_id, authorization[7:], role)

    @staticmethod
    def _requires_https():
        return os.environ.get("DROIDLINK_REQUIRE_HTTPS", "1").lower() not in ("0", "false", "no")

    @staticmethod
    def _route(path):
        match = re.fullmatch(r"/v1/sessions/([A-Za-z0-9_-]{20,64})/(events|commands|status|phone|unpair)", path)
        return (match.group(1), match.group(2)) if match else (None, None)

    @staticmethod
    def _wait_seconds(query):
        try:
            return max(0, min(25, int(parse_qs(query).get("wait", ["20"])[0])))
        except (TypeError, ValueError):
            return 20

    def _respond(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, relay):
        super().__init__(address, Handler)
        self.relay = relay


def main():
    port = max(1, min(65535, int(os.environ.get("DROIDLINK_PORT", "8080"))))
    relay = Relay()
    server = Server(("0.0.0.0", port), relay)
    print(f"DroidLink relay listening on port {port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        relay.db.close()


if __name__ == "__main__":
    main()
