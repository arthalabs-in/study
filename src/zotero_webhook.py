"""
Zotero webhook listener with localhost-only secure defaults.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 23121
DEFAULT_PATH_PREFIX = "/zotero/webhook/"
MAX_BODY_BYTES = 64 * 1024
RATE_WINDOW_SECONDS = 5.0
MAX_EVENTS_PER_WINDOW = 12
REPLAY_WINDOW_SECONDS = 30.0
MAX_STRING_FIELD_LENGTH = 256


def generate_webhook_secret() -> str:
    return secrets.token_urlsafe(24)


@dataclass
class ZoteroWebhookServer:
    secret: str
    on_event: Callable[[dict], None] | None = None
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    def __post_init__(self) -> None:
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._recent_event_times: deque[float] = deque()
        self._recent_payloads: dict[str, float] = {}

    @property
    def callback_path(self) -> str:
        return f"{DEFAULT_PATH_PREFIX}{self.secret}"

    @property
    def callback_url(self) -> str:
        return f"http://{self.host}:{self.port}{self.callback_path}"

    def _prune_recent_state(self, now: float) -> None:
        while self._recent_event_times and now - self._recent_event_times[0] > RATE_WINDOW_SECONDS:
            self._recent_event_times.popleft()
        stale = [
            fingerprint
            for fingerprint, seen_at in self._recent_payloads.items()
            if now - seen_at > REPLAY_WINDOW_SECONDS
        ]
        for fingerprint in stale:
            self._recent_payloads.pop(fingerprint, None)

    def _allow_rate(self, now: float) -> bool:
        self._prune_recent_state(now)
        if len(self._recent_event_times) >= MAX_EVENTS_PER_WINDOW:
            return False
        self._recent_event_times.append(now)
        return True

    @staticmethod
    def _payload_fingerprint(raw: bytes) -> str:
        return hashlib.sha256(raw).hexdigest()

    def _mark_payload_seen(self, fingerprint: str, now: float) -> bool:
        self._prune_recent_state(now)
        previous = self._recent_payloads.get(fingerprint)
        if previous is not None and now - previous <= REPLAY_WINDOW_SECONDS:
            return True
        self._recent_payloads[fingerprint] = now
        return False

    @staticmethod
    def _validate_field(value: object) -> bool:
        return isinstance(value, str) and 0 < len(value.strip()) <= MAX_STRING_FIELD_LENGTH

    def _validate_payload(self, payload: object) -> bool:
        if not isinstance(payload, dict):
            return False

        event_value = payload.get("event") or payload.get("type")
        if not self._validate_field(event_value):
            return False

        for key in ("source", "library", "item_key", "collection", "action"):
            value = payload.get(key)
            if value is not None and not self._validate_field(value):
                return False

        return True

    def start(self) -> tuple[bool, str]:
        if not self.secret:
            return False, "Webhook secret is required."
        if self._server is not None:
            return True, self.callback_url

        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                if self.path != owner.callback_path:
                    self.send_response(404)
                    self.end_headers()
                    return

                content_type = self.headers.get("Content-Type", "")
                if "application/json" not in content_type.lower():
                    self.send_response(415)
                    self.end_headers()
                    return

                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if length <= 0 or length > MAX_BODY_BYTES:
                    self.send_response(413)
                    self.end_headers()
                    return

                raw = self.rfile.read(length)
                now = time.time()
                if not owner._allow_rate(now):
                    self.send_response(429)
                    self.end_headers()
                    return

                fingerprint = owner._payload_fingerprint(raw)
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except Exception:
                    self.send_response(400)
                    self.end_headers()
                    return

                if not owner._validate_payload(payload):
                    self.send_response(400)
                    self.end_headers()
                    return

                duplicate = owner._mark_payload_seen(fingerprint, now)
                if owner.on_event and not duplicate:
                    owner.on_event(payload)

                body = b'{"ok":true}'
                self.send_response(202)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                self.send_response(405)
                self.end_headers()

            def log_message(self, format, *args):  # noqa: A003
                return

        try:
            self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        except OSError as exc:
            self._server = None
            return False, str(exc)

        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return True, self.callback_url

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self._thread = None
