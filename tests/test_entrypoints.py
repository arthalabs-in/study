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


def test_main_can_skip_auto_setup_for_hosted_demo(monkeypatch) -> None:
    events: list[tuple[str, str | None]] = []

    class FakeSettings:
        settings_file = 'missing-settings.json'

        def get(self, key, default=None):
            return ''

    class FakeApp:
        def __init__(self, file_path=None, debug=False):
            events.append(('init', file_path))
            events.append(('debug', str(debug)))

        def run(self):
            events.append(('run', None))

    monkeypatch.setenv('STUDY_SKIP_AUTO_SETUP', '1')
    monkeypatch.setattr(app_module, 'SettingsManager', FakeSettings)
    monkeypatch.setattr(app_module, 'run_setup_wizard', lambda: events.append(('setup', None)))
    monkeypatch.setattr(app_module, 'StudyTUI', FakeApp)
    monkeypatch.setattr(app_module.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(
        app_module.argparse.ArgumentParser,
        'parse_args',
        lambda self: SimpleNamespace(file='demo/leph101.pdf', file_flag=None, setup=False, debug=False),
    )

    app_module.main()

    assert events == [('init', 'demo/leph101.pdf'), ('debug', 'False'), ('run', None)]
