"""Lightweight file-backed debug tracing for Study TUI sessions."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


DEBUG_DIR = Path.home() / ".study-tui" / "debug"


class DebugTracer:
    def __init__(self, enabled: bool = False, root_dir: Path | str | None = None) -> None:
        self.enabled = bool(enabled)
        base_dir = Path(root_dir) if root_dir else DEBUG_DIR
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_dir = base_dir / f"session_{timestamp}"
        self.events_path = self.session_dir / "events.jsonl"
        self.latest_request_path = self.session_dir / "latest_request.json"
        self.latest_response_path = self.session_dir / "latest_response.txt"

        if self.enabled:
            self.session_dir.mkdir(parents=True, exist_ok=True)

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": event_type,
            "payload": self._make_json_safe(payload),
        }
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def write_latest_request(self, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.latest_request_path.write_text(
            json.dumps(self._make_json_safe(payload), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def write_latest_response(self, text: str, *, header: str | None = None) -> None:
        if not self.enabled:
            return
        body = text or ""
        if header:
            body = f"{header}\n\n{body}"
        self.latest_response_path.write_text(body, encoding="utf-8")

    @staticmethod
    def _make_json_safe(value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(key): DebugTracer._make_json_safe(val) for key, val in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [DebugTracer._make_json_safe(item) for item in value]
        return str(value)
