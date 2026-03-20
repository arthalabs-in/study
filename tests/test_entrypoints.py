from __future__ import annotations

from types import SimpleNamespace

import src.app as app_module


def test_main_honors_setup_and_file_flag(monkeypatch) -> None:
    events: list[tuple[str, str | None]] = []

    monkeypatch.setattr(app_module, 'run_setup_wizard', lambda: events.append(('setup', None)))

    class FakeApp:
        def __init__(self, file_path=None, debug=False):
            events.append(('init', file_path))
            events.append(('debug', debug))

        def run(self):
            events.append(('run', None))

    monkeypatch.setattr(app_module, 'StudyTUI', FakeApp)
    monkeypatch.setattr(app_module.argparse.ArgumentParser, 'parse_args', lambda self: SimpleNamespace(file=None, file_flag='notes.pdf', setup=True))

    app_module.main()

    assert events == [('setup', None), ('init', 'notes.pdf'), ('debug', False), ('run', None)]
