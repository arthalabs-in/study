from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.app as app_module
from src.app import StudyTUI


class FakeChat:
    def __init__(self) -> None:
        self.system_messages = []
        self.tool_done = []
        self.errors = []
        self.pickers = []

    def add_system_message(self, text: str) -> None:
        self.system_messages.append(text)

    def add_tool_done(self, text: str) -> None:
        self.tool_done.append(text)

    def add_error(self, text: str) -> None:
        self.errors.append(text)

    def show_nested_picker(self, prompt, options) -> None:
        self.pickers.append((prompt, options))


class FakeSettings:
    def __init__(self) -> None:
        self.values = {'theme': 'midnight'}
        self.secrets = {}

    def get(self, key: str, default):
        return self.values.get(key, default)

    def set(self, key: str, value) -> None:
        self.values[key] = value

    def get_secret(self, key: str, default=''):
        return self.secrets.get(key, default)

    def set_secret(self, key: str, value) -> None:
        self.secrets[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)

    def delete_secret(self, key: str) -> None:
        self.secrets.pop(key, None)


class CliSettings(FakeSettings):
    def __init__(self) -> None:
        super().__init__()
        self.settings_file = Path("C:/fake/settings.json")


class FakeKeyStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}
        self.saved: list[tuple[str, str, bool]] = []

    def get(self, provider: str) -> str:
        return self.values.get(provider, "")

    def set(self, provider: str, key: str, persist: bool = True):
        self.values[provider] = key
        self.saved.append((provider, key, persist))
        return True, None


class FakeCodexAuthStore:
    def __init__(self, token: str = "", configured_model: str = "gpt-5") -> None:
        self.token = token
        self.configured_model = configured_model
        self.imported: list[str] = []
        self.logged_in = False

    def get_access_token(self) -> str:
        return self.token

    def has_token(self) -> bool:
        return bool(self.token)

    def get_configured_model(self) -> str:
        return self.configured_model

    def default_auth_json_path(self) -> Path:
        return Path("C:/fake/auth.json")

    def import_auth_json(self, path: str):
        self.imported.append(path)
        self.token = "oauth-token"
        return True, f"Imported {path}"

    def login_with_codex_cli(self):
        self.logged_in = True
        self.token = "oauth-token"
        return True, "Logged in"


class FakeHistory:
    def __init__(self) -> None:
        self.session_id = 1

    def list_sessions(self, limit: int = 10):
        return [
            {'id': 1, 'title': 'Current', 'messages': 1, 'updated': 1.0},
            {'id': 2, 'title': 'Previous', 'messages': 3, 'updated': 2.0},
        ]


def build_stub_app():
    app = StudyTUI.__new__(StudyTUI)
    app._provider_name = 'openai'
    app._model_name = 'gpt-4o'
    app._provider_models_cache = {}
    app._allow_web_tools = False
    app._privacy_mode = 'confirm_remote_docs'
    app._export_privacy = 'readable'
    app._remote_docs_approved = False
    app._pending_tool_approval = None
    app._settings = FakeSettings()
    app._history_mgr = FakeHistory()
    app._key_store = SimpleNamespace(get=lambda provider: '')
    app._codex_auth_store = SimpleNamespace(get_access_token=lambda: '', has_token=lambda: False)
    app._resolve_api_key = lambda provider: ''
    app._init_provider = lambda: None
    app._active_model_label = lambda: 'gpt-4o'
    app._documents_dir = 'C:/docs'
    app._agent_manager = SimpleNamespace(allow_web_tools=False, default_export_dir='C:/Users/test/Documents/StudyTUI-Exports')
    app._documents_loaded = lambda: False
    app._provider_is_remote = lambda: True
    app._apply_privacy_mode = StudyTUI._apply_privacy_mode.__get__(app, StudyTUI)
    app._apply_export_privacy = StudyTUI._apply_export_privacy.__get__(app, StudyTUI)
    app._open_choice_picker = StudyTUI._open_choice_picker.__get__(app, StudyTUI)
    app._privacy_picker_options = StudyTUI._privacy_picker_options.__get__(app, StudyTUI)
    app._export_privacy_picker_options = StudyTUI._export_privacy_picker_options.__get__(app, StudyTUI)
    app._normalize_privacy_mode = StudyTUI._normalize_privacy_mode
    app._normalize_export_privacy = StudyTUI._normalize_export_privacy
    app._default_export_dir = StudyTUI._default_export_dir.__get__(app, StudyTUI)
    chat = FakeChat()
    app.query_one = lambda *args, **kwargs: chat
    return app, chat


def _patch_cli_state(monkeypatch: pytest.MonkeyPatch, settings: CliSettings, *, key_store=None, codex_store=None) -> None:
    monkeypatch.setattr(app_module, "SettingsManager", lambda: settings)
    monkeypatch.setattr(app_module, "ApiKeyStore", lambda: key_store or FakeKeyStore())
    monkeypatch.setattr(app_module, "CodexAuthStore", lambda: codex_store or FakeCodexAuthStore())


def test_provider_command_opens_picker() -> None:
    app, chat = build_stub_app()
    result = StudyTUI._handle_slash_command(app, '/provider')
    assert result is True
    assert chat.pickers[0][0] == 'Choose a provider.'
    assert any('/provider openai' == option[2] for option in chat.pickers[0][1])


def test_web_command_updates_state() -> None:
    app, chat = build_stub_app()
    result = StudyTUI._handle_slash_command(app, '/web on')
    assert result is True
    assert app._allow_web_tools is True
    assert app._agent_manager.allow_web_tools is True
    assert app._settings.values['allow_web_tools'] == 'true'
    assert chat.tool_done[-1] == 'Web search tool enabled.'


def test_zotero_webhook_command_paths() -> None:
    app, chat = build_stub_app()
    app._zotero_webhook_enabled = False
    app._zotero_webhook_secret = ''
    app._zotero_webhook_port = 23121
    app._show_zotero_webhook_status = lambda: chat.add_system_message('status shown')
    app._start_zotero_webhook = lambda notify=True: (True, 'http://127.0.0.1:23121/zotero/webhook/secret')
    app._stop_zotero_webhook = lambda: chat.add_tool_done('Zotero webhook disabled.')

    assert StudyTUI._handle_slash_command(app, '/zotero-webhook') is True
    assert chat.system_messages[-1] == 'status shown'
    assert StudyTUI._handle_slash_command(app, '/zotero-webhook on') is True
    assert StudyTUI._handle_slash_command(app, '/zotero-webhook off') is True
    assert chat.tool_done[-1] == 'Zotero webhook disabled.'


def test_privacy_and_export_privacy_commands() -> None:
    app, chat = build_stub_app()

    assert StudyTUI._handle_slash_command(app, '/privacy') is True
    assert chat.pickers[0][0] == 'Choose a privacy mode.'

    assert StudyTUI._handle_slash_command(app, '/privacy local_only') is True
    assert app._privacy_mode == 'local_only'
    assert app._settings.values['privacy_mode'] == 'local_only'

    assert StudyTUI._handle_slash_command(app, '/export-privacy private') is True
    assert app._export_privacy == 'private'
    assert app._settings.values['export_privacy'] == 'private'
    assert Path(app._agent_manager.default_export_dir).parts[-2:] == ('.study-tui', 'exports')


def test_privacy_approve_command() -> None:
    app, chat = build_stub_app()
    app._documents_loaded = lambda: True
    app._provider_is_remote = lambda: True

    assert StudyTUI._handle_slash_command(app, '/privacy-approve') is True
    assert app._remote_docs_approved is True
    assert 'approved' in chat.tool_done[-1].lower()


def test_provider_cli_lists_providers(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    settings = CliSettings()
    settings.values.update({"provider": "groq"})
    _patch_cli_state(monkeypatch, settings, key_store=FakeKeyStore({"groq": "secret"}))

    code = app_module._run_provider_cli([])

    out = capsys.readouterr().out
    assert code == 0
    assert "Available providers" in out
    assert "groq" in out
    assert "Current" in out


def test_provider_cli_sets_provider_and_default_model(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    settings = CliSettings()
    _patch_cli_state(monkeypatch, settings)

    code = app_module._run_provider_cli(["groq"])

    out = capsys.readouterr().out
    assert code == 0
    assert settings.values["provider"] == "groq"
    assert settings.values["model"] == app_module.PROVIDER_CONFIGS["groq"]["default_model"]
    assert "Provider set to" in out


def test_model_cli_lists_models(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    settings = CliSettings()
    settings.values.update({"provider": "groq", "model": "llama-3.3-70b-versatile"})
    _patch_cli_state(monkeypatch, settings)
    monkeypatch.setattr(
        app_module,
        "_fetch_models_for_cli",
        lambda provider_name, **kwargs: (["llama-3.3-70b-versatile", "meta-llama/llama-4-scout-17b-16e-instruct"], None),
    )

    code = app_module._run_model_cli(["list"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Models for" in out
    assert "llama-3.3-70b-versatile" in out
    assert "Current" in out


def test_model_cli_uses_provider_model_target(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    settings = CliSettings()
    settings.values.update({"provider": "openai", "model": "gpt-4o"})
    _patch_cli_state(monkeypatch, settings)
    monkeypatch.setattr(
        app_module,
        "_fetch_models_for_cli",
        lambda provider_name, **kwargs: (["llama-3.3-70b-versatile"], None),
    )

    code = app_module._run_model_cli(["use", "groq:llama-3.3-70b-versatile"])

    out = capsys.readouterr().out
    assert code == 0
    assert settings.values["provider"] == "groq"
    assert settings.values["model"] == "llama-3.3-70b-versatile"
    assert "Model set to groq:llama-3.3-70b-versatile" in out


def test_status_cli_prints_summary(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    settings = CliSettings()
    settings.values.update(
        {
            "provider": "groq",
            "model": "llama-3.3-70b-versatile",
            "documents_dir": "C:/docs",
            "calibre_library": "C:/Calibre Library",
            "zotero_webhook_enabled": "true",
            "zotero_webhook_port": "23121",
            "theme": "aurora",
            "allow_web_tools": "true",
        }
    )
    _patch_cli_state(monkeypatch, settings, key_store=FakeKeyStore({"groq": "secret"}))
    monkeypatch.setattr(
        app_module,
        "_dependency_probe",
        lambda: {"animation": {"error": None}},
    )

    code = app_module._run_status_cli()

    out = capsys.readouterr().out
    assert code == 0
    assert "Study TUI status" in out
    assert "Provider:" in out
    assert "groq" in out
    assert "Calibre:" in out
    assert "Zotero webhook: enabled" in out


def test_doctor_cli_prints_probe(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    settings = CliSettings()
    settings.values.update({"provider": "groq", "model": "llama-3.3-70b-versatile"})
    _patch_cli_state(monkeypatch, settings, key_store=FakeKeyStore({"groq": "secret"}))
    monkeypatch.setattr(
        app_module,
        "_dependency_probe",
        lambda: {
            "python": {"version": "3.13", "executable": "python.exe"},
            "packages": {"textual": True, "manim": True},
            "binaries": {"manim": True, "latex": True, "dvisvgm": True, "codex": False},
            "animation": {"manim_available": True, "tex_available": True, "error": None},
        },
    )

    code = app_module._run_doctor_cli()

    out = capsys.readouterr().out
    assert code == 0
    assert "Study TUI doctor" in out
    assert "Python packages" in out
    assert "Animation" in out
    assert "ready" in out


def test_run_setup_wizard_configures_provider_model_and_integrations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = CliSettings()
    key_store = FakeKeyStore()
    _patch_cli_state(monkeypatch, settings, key_store=key_store, codex_store=FakeCodexAuthStore())
    monkeypatch.setattr(
        app_module,
        "list_providers",
        lambda: [
            {"name": "groq", "display_name": "Groq", "auth_mode": "api_key"},
            {"name": "openai", "display_name": "OpenAI", "auth_mode": "api_key"},
        ],
    )

    class FakeProvider:
        async def get_models_async(self):
            return ["llama-3.3-70b-versatile", "meta-llama/llama-4-scout-17b-16e-instruct"]

    monkeypatch.setattr(app_module, "create_provider", lambda *args, **kwargs: FakeProvider())
    monkeypatch.setattr(app_module, "_resolve_default_documents_dir", lambda settings: str(tmp_path / "docs"))
    monkeypatch.setattr(app_module, "is_manim_available", lambda: True)
    monkeypatch.setattr(app_module, "is_tex_available", lambda: True)
    monkeypatch.setattr(app_module, "get_animation_dependency_error", lambda: None)
    monkeypatch.setattr(app_module, "generate_webhook_secret", lambda: "generated-secret")

    choice_answers = iter([0, 1, 1])  # provider groq, model 2nd, theme 2nd
    text_answers = iter(
        [
            "groq-api-key",
            str(tmp_path / "DocsRoot"),
            str(tmp_path / "Calibre Library"),
            "24124",
        ]
    )
    yes_no_answers = iter([True, True])  # enable web, enable zotero
    monkeypatch.setattr(app_module, "_prompt_choice", lambda *args, **kwargs: next(choice_answers))
    monkeypatch.setattr(app_module, "_prompt_text", lambda *args, **kwargs: next(text_answers))
    monkeypatch.setattr(app_module, "_prompt_yes_no", lambda *args, **kwargs: next(yes_no_answers))

    app_module.run_setup_wizard()

    out = capsys.readouterr().out
    assert settings.values["provider"] == "groq"
    assert settings.values["model"] == "meta-llama/llama-4-scout-17b-16e-instruct"
    assert settings.values["allow_web_tools"] == "true"
    assert settings.values["calibre_library"] == str(tmp_path / "Calibre Library")
    assert settings.values["zotero_webhook_enabled"] == "true"
    assert settings.values["zotero_webhook_port"] == "24124"
    assert settings.secrets["zotero_webhook_secret"]
    assert key_store.values["groq"] == "groq-api-key"
    assert "Animation (Manim)" in out
    assert "Saved setup" in out


def test_main_dispatches_provider_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {}
    def _fake_provider_cli(args):
        called["args"] = list(args)
        return 0

    monkeypatch.setattr(app_module, "_run_provider_cli", _fake_provider_cli)
    monkeypatch.setattr(sys, "argv", ["study", "provider", "groq"])

    with pytest.raises(SystemExit) as exc:
        app_module.main()

    assert exc.value.code == 0
    assert called["args"] == ["groq"]


def test_should_auto_run_setup_when_settings_are_missing() -> None:
    settings = CliSettings()
    settings.settings_file = Path("C:/missing/settings.json")
    assert app_module._should_auto_run_setup(settings) is True


def test_main_auto_runs_setup_on_first_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = CliSettings()
    settings.values.clear()
    settings.settings_file = Path("C:/missing/settings.json")
    launched = {"setup": 0, "run": 0}

    class FakeApp:
        def __init__(self, file_path=None, debug=False) -> None:
            self.file_path = file_path
            self.debug = debug

        def run(self) -> None:
            launched["run"] += 1

    monkeypatch.setattr(app_module, "SettingsManager", lambda: settings)
    monkeypatch.setattr(app_module, "run_setup_wizard", lambda: launched.__setitem__("setup", launched["setup"] + 1))
    monkeypatch.setattr(app_module, "StudyTUI", FakeApp)
    monkeypatch.setattr(sys, "argv", ["study"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    app_module.main()

    assert launched["setup"] == 1
    assert launched["run"] == 1
