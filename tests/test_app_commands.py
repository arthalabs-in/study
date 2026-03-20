from __future__ import annotations

from types import SimpleNamespace

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
    assert str(app._agent_manager.default_export_dir).endswith('.study-tui\\exports')


def test_privacy_approve_command() -> None:
    app, chat = build_stub_app()
    app._documents_loaded = lambda: True
    app._provider_is_remote = lambda: True

    assert StudyTUI._handle_slash_command(app, '/privacy-approve') is True
    assert app._remote_docs_approved is True
    assert 'approved' in chat.tool_done[-1].lower()
