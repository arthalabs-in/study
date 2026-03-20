from __future__ import annotations

import json
from pathlib import Path

import src.app as app_module
from src.debug_trace import DebugTracer


def test_debug_tracer_writes_session_artifacts(tmp_path: Path) -> None:
    tracer = DebugTracer(enabled=True, root_dir=tmp_path)
    tracer.log_event("provider_request", {"messages": [{"role": "user", "content": "hello"}]})
    tracer.write_latest_request({"system": "prompt", "messages": [{"role": "user", "content": "hello"}]})
    tracer.write_latest_response("world", header="Purpose: test")

    assert tracer.events_path.exists()
    assert tracer.latest_request_path.exists()
    assert tracer.latest_response_path.exists()

    lines = tracer.events_path.read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(lines[-1])
    assert payload["event"] == "provider_request"
    assert payload["payload"]["messages"][0]["content"] == "hello"
    assert "Purpose: test" in tracer.latest_response_path.read_text(encoding="utf-8")


def test_main_passes_debug_flag(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeStudyTUI:
        def __init__(self, file_path=None, debug=False):
            captured["file_path"] = file_path
            captured["debug"] = debug

        def run(self):
            captured["ran"] = True

    monkeypatch.setattr(app_module, "StudyTUI", FakeStudyTUI)
    monkeypatch.setattr(app_module.sys, "argv", ["study", "--debug", "--file", "notes.pdf"])

    app_module.main()

    assert captured["debug"] is True
    assert captured["file_path"] == "notes.pdf"
    assert captured["ran"] is True
