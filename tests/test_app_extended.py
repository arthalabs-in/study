from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.app as app_module
from src.app import (
    ApiKeyStore,
    CodexAuthStore,
    GenerationCancelled,
    NUMERIC_QUIZ_GRADER_PROMPT,
    PendingToolApproval,
    _ReasoningStreamParser,
    SettingsManager,
    StudyTUI,
    _build_model_messages,
    _compact_assistant_context,
    _estimate_chat_tokens,
    _estimate_text_tokens,
    _parse_numeric_quiz_verdict,
    _parse_quiz_json,
    _prompt_choice,
    _prompt_text,
    _prompt_yes_no,
    _resolve_default_documents_dir,
    run_setup_wizard,
)
from src.parsers.doc_store import Chunk, Document, DocStore


class FakeInput:
    def __init__(self) -> None:
        self.value = ''
        self.cursor_position = 0
        self.placeholder = 'Ask anything...    /help for commands'
        self.disabled = False
        self.focused = False

    def focus(self) -> None:
        self.focused = True


def test_compact_assistant_context_summarizes_flashcards() -> None:
    content = (
        "Here are flashcards:\n\n"
        "1) Q: What is heat?\nA: Energy transfer.\n\n"
        "2) Q: What is temperature?\nA: Measure of hotness.\n"
    )
    compacted = _compact_assistant_context(content)
    assert "generated 2 flashcards" in compacted.lower()
    assert "What is heat?" in compacted
    assert "Energy transfer." not in compacted


def test_build_model_messages_compacts_older_history() -> None:
    messages = []
    for idx in range(30):
        messages.append({"role": "user", "content": f"user message {idx}"})
        messages.append({"role": "assistant", "content": f"assistant message {idx} " + ("x" * 1800)})

    compacted = _build_model_messages(messages)
    assert len(compacted) < len(messages)
    assert compacted[-1]["content"].startswith("assistant message 29")
    assert any("pruned to stay within the context budget" in msg["content"] for msg in compacted)


class FakeTimer:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class FakeChat:
    def __init__(self) -> None:
        self.system_messages: list[str] = []
        self.tool_done: list[str] = []
        self.errors: list[str] = []
        self.user_messages: list[str] = []
        self.assistant_messages: list[str] = []
        self.info_blocks: list[tuple[str, list[str]]] = []
        self.pickers: list[tuple[str, list[tuple[str, str, str]]]] = []
        self.response_tokens: list[str] = []
        self.tool_start: list[str] = []
        self.started_quizzes: list[list[dict]] = []
        self.started_flashcards: list[tuple[list[dict], list[str], list[str]]] = []
        self.completed_numeric_answers: list[tuple[int, bool, str]] = []
        self.hide_typing_calls = 0
        self.show_typing_calls = 0
        self.start_response_calls = 0
        self.end_response_calls = 0
        self.start_thinking_calls = 0
        self.end_thinking_calls = 0
        self.cleared = False
        self.welcome_written = False
        self.focused = False
        self._last_response = ''
        self.input = FakeInput()

    def add_system_message(self, text: str) -> None:
        self.system_messages.append(text)

    def add_tool_done(self, text: str) -> None:
        self.tool_done.append(text)

    def add_tool_start(self, text: str) -> None:
        self.tool_start.append(text)

    def add_error(self, text: str) -> None:
        self.errors.append(text)

    def add_user_message(self, text: str) -> None:
        self.user_messages.append(text)

    def add_assistant_message(self, text: str) -> None:
        self.assistant_messages.append(text)
        self._last_response = text

    def add_info_block(self, title: str, lines: list[str]) -> None:
        self.info_blocks.append((title, lines))

    def show_nested_picker(self, prompt, options) -> None:
        self.pickers.append((prompt, options))

    def clear_log(self) -> None:
        self.cleared = True

    def write_welcome(self, overview=None) -> None:
        self.welcome_written = True

    def focus_input(self) -> None:
        self.focused = True

    def show_typing(self) -> None:
        self.show_typing_calls += 1

    def hide_typing(self) -> None:
        self.hide_typing_calls += 1

    def start_response(self) -> None:
        self.start_response_calls += 1

    def stream_token(self, token: str) -> None:
        self.response_tokens.append(token)

    def end_response(self) -> str:
        self.end_response_calls += 1
        text = ''.join(self.response_tokens)
        self._last_response = text
        return text

    def start_thinking(self) -> None:
        self.start_thinking_calls += 1

    def end_thinking(self) -> None:
        self.end_thinking_calls += 1

    def stream_thinking_token(self, token: str) -> None:
        self.response_tokens.append(f'THINK:{token}')

    def start_quiz(self, questions: list[dict]) -> None:
        self.started_quizzes.append(questions)

    def start_flashcards(
        self,
        cards: list[dict],
        intro_lines: list[str] | None = None,
        outro_lines: list[str] | None = None,
        review_mode: bool = False,
    ) -> None:
        self.started_flashcards.append((list(cards), list(intro_lines or []), list(outro_lines or [])))

    def complete_pending_numeric_answer(self, quiz_index: int, is_correct: bool, feedback: str = "") -> None:
        self.completed_numeric_answers.append((quiz_index, is_correct, feedback))

    def query_one(self, selector, _type=None):
        if selector == '#chat-input':
            return self.input
        raise KeyError(selector)


class FakeSettings:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {'theme': 'midnight'}
        self.secrets: dict[str, str] = {}
        self.saved = 0
        self.deleted: list[str] = []
        self.deleted_secrets: list[str] = []

    def get(self, key: str, default):
        return self.values.get(key, default)

    def set(self, key: str, value) -> None:
        self.values[key] = value
        self.saved += 1

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.values.pop(key, None)

    def save(self) -> None:
        self.saved += 1

    def get_secret(self, key: str, default=''):
        return self.secrets.get(key, default)

    def set_secret(self, key: str, value: str) -> None:
        self.secrets[key] = value
        self.saved += 1

    def delete_secret(self, key: str) -> None:
        self.deleted_secrets.append(key)
        self.secrets.pop(key, None)


class FakeHistory:
    def __init__(self) -> None:
        self.session_id = 1
        self.session_title = 'Recovered session'
        self.saved_payloads: list[list[dict]] = []
        self.saved_states: list[dict] = []
        self.new_sessions = 0
        self.session_states: dict[int, dict] = {
            1: {},
            2: {
                'compact_memories': [{'id': 'mem_1', 'summary': 'Older session summary', 'source_count': 2}],
                'compacted_transcript_count': 1,
                'last_context_stats': {'prompt_tokens': 42},
            },
        }
        self.loaded_sessions: dict[int, list[dict]] = {
            1: [{'role': 'user', 'content': 'current'}],
            2: [
                {'role': 'user', 'content': 'older question'},
                {'role': 'assistant', 'content': 'older answer'},
            ],
        }

    def list_sessions(self, limit: int = 10):
        return [
            {'id': 1, 'title': 'Current', 'messages': 1, 'updated': 1.0},
            {'id': 2, 'title': 'Previous', 'messages': 3, 'updated': 2.0},
        ]

    def load_session(self, sid: int):
        return list(self.loaded_sessions[sid])

    def load_latest(self):
        return self.loaded_sessions[2], 2

    def get_session_title(self, sid: int):
        return 'Recovered session'

    def new_session(self):
        self.new_sessions += 1
        self.session_id += 1
        return self.session_id

    def save(self, payload):
        self.saved_payloads.append(list(payload))

    def load_session_state(self, sid: int):
        return dict(self.session_states.get(sid, {}))

    def save_session_state(self, state: dict):
        self.saved_states.append(dict(state))
        self.session_states[self.session_id] = dict(state)


class FakeKeyStore:
    def __init__(self, keys: dict[str, str] | None = None, secure: bool = False) -> None:
        self.keys = keys or {}
        self.has_secure_persistence = secure
        self.set_calls: list[tuple[str, str, bool]] = []

    def get(self, provider: str) -> str:
        return self.keys.get(provider, '')

    def set(self, provider: str, key: str, persist: bool = True):
        self.set_calls.append((provider, key, persist))
        self.keys[provider] = key
        return (persist and self.has_secure_persistence), None if persist and self.has_secure_persistence else 'session only'


class FakeCodexStore:
    def __init__(self, token: str = '', model: str = 'gpt-5.4') -> None:
        self.token = token
        self.model = model
        self.login_calls = 0
        self.import_calls: list[str] = []

    def get_access_token(self) -> str:
        return self.token

    def has_token(self) -> bool:
        return bool(self.token)

    def get_configured_model(self) -> str:
        return self.model

    def default_auth_json_path(self) -> Path:
        return Path.home() / '.codex' / 'auth.json'

    def login_with_codex_cli(self):
        self.login_calls += 1
        self.token = self.token or 'oauth-token'
        return True, 'ok'

    def import_auth_json(self, source_path: str | Path | None = None):
        self.import_calls.append(str(source_path))
        self.token = self.token or 'oauth-token'
        return True, 'imported'


class FakeProvider:
    def __init__(self, name='openai', model='gpt-4o', models: list[str] | None = None, chat_result='ok') -> None:
        self.name = name
        self.model = model
        self._models = models or [model]
        self.chat_result = chat_result
        self.chat_calls: list[dict] = []

    async def get_models_async(self):
        return list(self._models)

    async def chat(self, **kwargs):
        self.chat_calls.append(kwargs)
        return self.chat_result


class FakeAgentManager(SimpleNamespace):
    async def execute_tool(self, name, args):
        return {'name': name, 'args': args}


class FakeProgressManager:
    def __init__(self) -> None:
        self.sources: dict[str, dict] = {}
        self.flashcard_calls: list[dict] = []
        self.quiz_calls: list[dict] = []
        self.note_calls: list[dict] = []
        self.linked_notes: list[dict] = []
        self.review_calls: list[dict] = []

    def upsert_source(self, *, source_hash: str, doc_id: str | None, title: str, path: str) -> None:
        self.sources[source_hash] = {"doc_id": doc_id, "title": title, "path": path}

    def source_hash_for_doc(self, doc_id: str | None) -> str | None:
        for source_hash, payload in self.sources.items():
            if payload.get("doc_id") == doc_id:
                return source_hash
        return None

    def record_flashcards(self, **kwargs):
        self.flashcard_calls.append(kwargs)
        return {"status": "saved"}

    def record_quiz_attempt(self, **kwargs):
        self.quiz_calls.append(kwargs)
        return {
            "status": "saved",
            "weak_topics": ["weak topic"],
            "strong_topics": ["strong topic"],
            "grasp_level": 0.5,
        }

    def record_progress_note(self, **kwargs):
        self.note_calls.append(kwargs)
        return {"status": "saved"}

    def record_flashcard_review(self, **kwargs):
        self.review_calls.append(kwargs)
        return {"status": "saved", "grade": kwargs.get("grade")}

    def link_note(self, **kwargs) -> None:
        self.linked_notes.append(kwargs)

    def get_progress(self, *, source_hash: str | None = None, doc_id: str | None = None) -> dict:
        return {
            "source_hash": source_hash or "hash",
            "doc_id": doc_id or "doc1",
            "title": "Progress",
            "grasp_level": 0.5,
            "review_count": 1,
            "last_quiz_score": 0.5,
            "weak_topics": ["weak topic"],
            "strong_topics": ["strong topic"],
            "linked_counts": {"flashcard_decks": 1, "quiz_attempts": 1, "notes": 1},
            "recent_progress_notes": [],
            "recent_quizzes": [],
        }


def make_app() -> tuple[StudyTUI, FakeChat, FakeHistory, list]:
    app = StudyTUI.__new__(StudyTUI)
    chat = FakeChat()
    history = FakeHistory()
    progress = FakeProgressManager()
    workers = []
    classes: list[tuple[str, str]] = []
    app._provider_name = 'openai'
    app._model_name = 'gpt-4o'
    app._provider_models_cache = {}
    app._allow_web_tools = False
    app._privacy_mode = 'confirm_remote_docs'
    app._export_privacy = 'readable'
    app._remote_docs_approved = False
    app._pending_tool_approval = None
    app._session_prompt_tokens = 0
    app._session_completion_tokens = 0
    app._settings = FakeSettings({'theme': 'midnight', 'provider': 'openai', 'model': 'gpt-4o'})
    app._history_mgr = history
    app._progress_mgr = progress
    app._key_store = FakeKeyStore({}, secure=False)
    app._codex_auth_store = FakeCodexStore()
    app._documents_dir = 'C:/docs'
    app._chat_history = []
    app._last_flashcards = []
    app._provider = FakeProvider()
    app._agent_manager = FakeAgentManager(
        allow_web_tools=False,
        documents_dir='C:/docs',
        default_export_dir='C:/Users/test/Documents/StudyTUI-Exports',
    )
    app.doc_store = DocStore()
    app._generating = False
    app._cancel_event = asyncio.Event()
    app._model_history = []
    app._compact_memories = []
    app._compacted_transcript_count = 0
    app._last_context_stats = {}
    app._last_tool_result_chars = 0
    app._last_context_limit = None
    app._tool_artifacts = []
    app._request_turn_index = 0
    app._active_request_turn_index = 0
    app._last_selected_tools = []
    app._last_tool_schema_tokens = 0
    app._last_dropped_artifact_count = 0
    app._last_request_system_prompt = app_module.SYSTEM_PROMPT
    app._doc_source_hashes = {}
    app._latest_source_hash = None
    app._pending_study_workflow = None
    app._resolve_api_key = lambda provider: ''
    app._init_provider = lambda: None
    app._documents_loaded = lambda: bool(app.doc_store.documents)
    app._provider_is_remote = lambda: True
    app._current_prompt_turn_index = lambda: app._active_request_turn_index or app._request_turn_index
    app._tool_lookup = lambda: {
        str(tool.get('name', '')).strip(): tool
        for tool in app_module.ALL_TOOLS
        if str(tool.get('name', '')).strip()
    }
    app.query_one = lambda *args, **kwargs: chat
    def _run_worker(work, **kwargs):
        name = getattr(getattr(work, 'cr_code', None), 'co_name', repr(work))
        workers.append((SimpleNamespace(name=name), kwargs))
        if asyncio.iscoroutine(work):
            work.close()
        return SimpleNamespace(cancel=lambda: workers.append(('cancelled', kwargs)), is_finished=False)
    app.run_worker = _run_worker
    app.add_class = lambda value: classes.append(('add', value))
    app.remove_class = lambda value: classes.append(('remove', value))
    app._class_calls = classes
    return app, chat, history, workers


def test_settings_manager_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_module.Path, 'home', staticmethod(lambda: tmp_path))
    settings = SettingsManager()
    assert settings.get('theme', 'x') == 'midnight'
    settings.set('provider', 'openai')
    settings.delete('missing')
    settings.delete('provider')

    raw = json.loads((tmp_path / '.study-tui' / 'settings.json').read_text(encoding='utf-8'))
    assert 'provider' not in raw


def test_settings_manager_secret_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_module.Path, 'home', staticmethod(lambda: tmp_path))
    monkeypatch.setattr(app_module, 'encrypt_text', lambda value: f'enc:{value}')
    monkeypatch.setattr(app_module, 'decrypt_text', lambda value: value.removeprefix('enc:'))
    settings = SettingsManager()
    settings.set_secret('zotero_webhook_secret', 'super-secret')
    assert settings.get_secret('zotero_webhook_secret') == 'super-secret'
    settings.delete_secret('zotero_webhook_secret')
    assert settings.get_secret('zotero_webhook_secret', 'missing') == 'missing'


def test_api_key_store_handles_memory_and_keyring(monkeypatch) -> None:
    class FakeKeyring:
        def __init__(self):
            self.saved = {}

        def get_password(self, service, provider):
            return self.saved.get((service, provider), '')

        def set_password(self, service, provider, key):
            self.saved[(service, provider)] = key

    fake_keyring = FakeKeyring()
    monkeypatch.setitem(__import__('sys').modules, 'keyring', fake_keyring)
    store = ApiKeyStore()
    assert store.has_secure_persistence is True
    saved, warn = store.set('openai', 'sk-test', persist=True)
    assert saved is True and warn is None
    assert store.get('openai') == 'sk-test'

    memory_only, warn = store.set('openai', 'tmp', persist=False)
    assert memory_only is False and warn is None


def test_remote_doc_privacy_gate_requires_session_approval() -> None:
    app, chat, _history, _workers = make_app()
    app.doc_store.documents['doc1'] = object()
    app._privacy_mode = 'confirm_remote_docs'
    app._remote_docs_approved = False

    allowed = StudyTUI._ensure_remote_doc_access_allowed(app)

    assert allowed is False
    assert any('privacy approval required' in msg.lower() for msg in chat.system_messages)


def test_remote_doc_privacy_gate_blocks_local_only() -> None:
    app, chat, _history, _workers = make_app()
    app.doc_store.documents['doc1'] = object()
    app._privacy_mode = 'local_only'

    allowed = StudyTUI._ensure_remote_doc_access_allowed(app)

    assert allowed is False
    assert any('blocked in local_only privacy mode' in msg.lower() for msg in chat.errors)


@pytest.mark.asyncio
async def test_generation_flushes_normalized_table_after_response_started(monkeypatch) -> None:
    app, chat, _history, _workers = make_app()
    app._chat_history = [{'role': 'user', 'content': 'compare these'}]
    app._provider = FakeProvider()
    app._agent_manager = FakeAgentManager()
    app._privacy_mode = 'standard'
    app._remote_docs_approved = True

    async def table_after_started_stream(**kwargs):
        chunks = [
            "Intro paragraph.\n\n",
            "| Topic | Status |\n| --- | --- |\n| Quiz | Complete |\n",
            "\nNext step: review.",
        ]
        for chunk in chunks:
            kwargs['on_text'](chunk)
        return ''.join(chunks)

    monkeypatch.setattr(app_module, 'stream_chat', table_after_started_stream)

    await StudyTUI._run_generation(app)

    rendered_tokens = ''.join(chat.response_tokens)
    assert "| Topic | Status |" not in rendered_tokens
    assert "- Topic: Quiz; Status: Complete" in rendered_tokens
    assert "Next step: review." in rendered_tokens
    assert app._chat_history[-1]['content'] == rendered_tokens


def test_codex_auth_store_reads_and_logs_in(tmp_path, monkeypatch) -> None:
    codex_home = tmp_path / '.codex'
    codex_home.mkdir()
    (codex_home / 'auth.json').write_text(
        json.dumps(
            {
                'auth_mode': 'chatgptAuthTokens',
                'tokens': {
                    'access_token': ' token ',
                    'refresh_token': 'refresh-token',
                    'expires_at': 0,
                },
            }
        ),
        encoding='utf-8',
    )
    (codex_home / 'config.toml').write_text('model = \"gpt-5.4\"\n', encoding='utf-8')
    monkeypatch.setenv('CODEX_HOME', str(codex_home))

    store = CodexAuthStore()
    monkeypatch.setattr(store, '_refresh_access_token', lambda refresh_token: {'access_token': 'fresh-token', 'refresh_token': refresh_token, 'expires_in': 3600})
    assert store.has_token() is True
    assert store.get_access_token() == 'fresh-token'
    assert store.auth_mode() == 'chatgptAuthTokens'
    assert store.get_configured_model() == 'gpt-5.4'

    monkeypatch.setattr(store, '_authorize_and_get_code', lambda auth_url, expected_state, timeout_seconds=180: ('auth-code', None))
    monkeypatch.setattr(
        store,
        '_exchange_authorization_code',
        lambda code, verifier: {
            'access_token': 'new-access',
            'refresh_token': 'new-refresh',
            'expires_in': 3600,
        },
    )
    ok, message = store.login_with_codex_cli()
    assert ok is True
    assert 'completed' in message.lower()
    assert store.get_access_token() == 'new-access'

    other_auth = tmp_path / 'other-auth.json'
    other_auth.write_text(json.dumps({'access': 'imported-access', 'refresh': 'imported-refresh', 'accountId': 'acct_imported'}), encoding='utf-8')
    ok, message = store.import_auth_json(other_auth)
    assert ok is True
    assert 'Imported' in message
    assert store._current_tokens()['access_token'] == 'imported-access'
    assert store.get_account_id() == 'acct_imported'


def test_codex_auth_store_extracts_account_id_from_token(monkeypatch) -> None:
    store = CodexAuthStore()
    payload = {'account_id': 'acct_123'}
    token = 'header.' + app_module.base64.urlsafe_b64encode(json.dumps(payload).encode('utf-8')).decode('utf-8').rstrip('=') + '.sig'
    assert store._extract_account_id(token) == 'acct_123'


def test_parse_quiz_json_accepts_direct_fenced_and_embedded_blocks() -> None:
    direct = _parse_quiz_json('[{"question": "Q1"}]')
    fenced = _parse_quiz_json('```json\n[{"question": "Q2"}]\n```')
    embedded = _parse_quiz_json('noise\n[{"question": "Q3"}]\nmore noise')
    missing = _parse_quiz_json('not json')

    assert direct == [{'question': 'Q1'}]
    assert fenced == [{'question': 'Q2'}]
    assert embedded == [{'question': 'Q3'}]
    assert missing is None


def test_parse_numeric_quiz_verdict_accepts_direct_and_fenced_json() -> None:
    direct = _parse_numeric_quiz_verdict('{"correct": true, "feedback": "close enough"}')
    fenced = _parse_numeric_quiz_verdict('```json\n{"correct": false, "feedback": "wrong magnitude"}\n```')
    missing = _parse_numeric_quiz_verdict('not json')

    assert direct == {'correct': True, 'feedback': 'close enough'}
    assert fenced == {'correct': False, 'feedback': 'wrong magnitude'}
    assert missing is None


def test_reasoning_stream_parser_handles_inline_and_channel_reasoning() -> None:
    visible: list[str] = []
    thinking: list[str] = []
    parser = _ReasoningStreamParser(visible.append, thinking.append)

    parser.feed('Visible <think>hidden steps')
    parser.feed('</think> done')
    parser.feed('<|channel|>analysis<|message|>secret trace<|end|> final')
    parser.flush()

    assert ''.join(thinking) == 'hidden stepssecret trace'
    assert ''.join(visible) == 'Visible  done final'


def test_token_estimators_return_positive_counts() -> None:
    assert _estimate_text_tokens('hello world', 'gpt-4o') > 0
    assert _estimate_chat_tokens([{'role': 'user', 'content': 'hello'}], 'system', 'gpt-4o') > 0


def test_prompt_helpers(monkeypatch, capsys) -> None:
    inputs = iter(['', '3', 'maybe', 'yes'])
    import builtins
    monkeypatch.setattr(builtins, 'input', lambda prompt='': next(inputs))
    assert _prompt_choice('Choose', ['a', 'b', 'c'], 1) == 1
    assert _prompt_yes_no('Proceed?', default=False) is True

    monkeypatch.setattr(builtins, 'input', lambda prompt='': ' typed ')
    monkeypatch.setattr(app_module, 'getpass', lambda prompt='': ' secret ')
    assert _prompt_text('Label', 'fallback') == 'typed'
    assert _prompt_text('Secret', 'fallback', secret=True) == 'secret'
    out = capsys.readouterr().out
    assert 'Choose' in out


def test_resolve_default_documents_dir_prefers_settings(monkeypatch) -> None:
    settings = FakeSettings({'documents_dir': 'D:/Study'})
    monkeypatch.setenv('STUDY_DOCS_DIR', 'E:/EnvDocs')
    assert _resolve_default_documents_dir(settings) == 'D:/Study'


def test_run_setup_wizard_saves_expected_defaults(monkeypatch, tmp_path, capsys) -> None:
    settings = FakeSettings({'provider': 'openai', 'theme': 'midnight', 'allow_web_tools': 'false'})
    key_store = FakeKeyStore({}, secure=True)
    codex_store = FakeCodexStore(token='oauth-token', model='gpt-5.4')
    provider = FakeProvider(name='openai', model='gpt-4o', models=['gpt-4o', 'gpt-4.1'])

    monkeypatch.setattr(app_module, 'SettingsManager', lambda: settings)
    monkeypatch.setattr(app_module, 'ApiKeyStore', lambda: key_store)
    monkeypatch.setattr(app_module, 'CodexAuthStore', lambda: codex_store)
    monkeypatch.setattr(app_module, 'list_providers', lambda: [
        {'name': 'openai', 'display_name': 'OpenAI API', 'auth_mode': 'api_key'},
        {'name': 'ollama', 'display_name': 'Ollama', 'auth_mode': 'none'},
    ])
    monkeypatch.setattr(app_module, 'create_provider', lambda *args, **kwargs: provider)
    monkeypatch.setattr(app_module, '_prompt_choice', lambda title, options, default_index=0: 0 if 'provider' in title.lower() else 1 if title == 'Primary model' else 2 if title == 'Theme' else default_index)
    monkeypatch.setattr(app_module, '_prompt_text', lambda label, default='', secret=False: 'sk-setup' if 'API key' in label else str(tmp_path / 'Docs') if 'Documents directory' in label else default)
    monkeypatch.setattr(app_module, '_prompt_yes_no', lambda label, default=True: True if 'Enable web search' in label else False)

    run_setup_wizard()

    assert settings.values['provider'] == 'openai'
    assert settings.values['model'] == 'gpt-4o'
    assert settings.values['theme'] == 'focus'
    assert settings.values['allow_web_tools'] == 'true'
    assert Path(settings.values['documents_dir']).name == 'Docs'
    assert key_store.set_calls[0][0] == 'openai'
    assert 'Saved setup' in capsys.readouterr().out


def test_run_setup_wizard_supports_codex_auth_json_import(monkeypatch, tmp_path, capsys) -> None:
    settings = FakeSettings({'provider': 'openai-codex', 'theme': 'midnight', 'allow_web_tools': 'false'})
    key_store = FakeKeyStore({}, secure=True)
    codex_store = FakeCodexStore(token='', model='gpt-5.4')
    provider = FakeProvider(name='openai-codex', model='gpt-5.4', models=['gpt-5.4'])
    auth_path = tmp_path / 'auth.json'

    monkeypatch.setattr(app_module, 'SettingsManager', lambda: settings)
    monkeypatch.setattr(app_module, 'ApiKeyStore', lambda: key_store)
    monkeypatch.setattr(app_module, 'CodexAuthStore', lambda: codex_store)
    monkeypatch.setattr(app_module, 'list_providers', lambda: [
        {'name': 'openai-codex', 'display_name': 'OpenAI Codex (ChatGPT OAuth)', 'auth_mode': 'codex_oauth'},
    ])
    monkeypatch.setattr(app_module, 'create_provider', lambda *args, **kwargs: provider)
    monkeypatch.setattr(
        app_module,
        '_prompt_choice',
        lambda title, options, default_index=0: 0 if title == 'Primary provider' else 0 if title == 'Codex auth source' else 0 if title == 'Primary model' else 0 if title == 'Theme' else default_index,
    )
    monkeypatch.setattr(
        app_module,
        '_prompt_text',
        lambda label, default='', secret=False: str(auth_path) if 'Path to Codex auth.json' in label else str(tmp_path / 'Docs') if 'Documents directory' in label else default,
    )
    monkeypatch.setattr(app_module, '_prompt_yes_no', lambda label, default=True: False)

    run_setup_wizard()

    assert codex_store.import_calls == [str(auth_path)]
    assert codex_store.login_calls == 0
    assert settings.values['provider'] == 'openai-codex'
    assert settings.values['model'] == 'gpt-5.4'
    assert 'Saved setup' in capsys.readouterr().out


def test_write_approval_helpers_and_picker_options() -> None:
    app, chat, history, _workers = make_app()
    app._chat_history = [{'role': 'user', 'content': 'one'}, {'role': 'assistant', 'content': 'two'}]

    title, lines = StudyTUI._build_write_approval_summary(app, 'save_note', {'title': 'Entropy', 'doc_id': 'physics', 'page': 3, 'tags': ['thermo']})
    assert title.startswith('Requested write')
    assert 'Title: Entropy' in lines
    assert 'Document: physics' in lines

    title, lines = StudyTUI._build_write_approval_summary(app, 'export_content', {'type': 'chat', 'format': 'markdown', 'content': 'abc', 'cards': [1, 2]})
    assert 'Action: export chat' in lines
    assert 'Messages: 2 in current session' in lines

    providers = StudyTUI._provider_picker_options(app)
    themes = StudyTUI._theme_picker_options(app)
    approvals = StudyTUI._approval_picker_options()
    sessions = StudyTUI._session_picker_options(app)
    web = StudyTUI._web_picker_options(app)
    models = StudyTUI._model_picker_options(app, ['gpt-4o', 'gpt-4.1'])
    assert any('/provider openai' == option[2] for option in providers)
    assert any('/theme midnight' == option[2] for option in themes)
    assert any('/theme aurora' == option[2] for option in themes)
    assert any('/theme paper' == option[2] for option in themes)
    assert any('/theme aurora' == option[2] and 'Cool polar glow' in option[1] for option in themes)
    assert approvals == [
        ('approval:approve', ' approve Continue with the write', '/approve'),
        ('approval:deny', ' deny Cancel the write', '/deny'),
    ]
    assert any('/resume 1' == option[2] for option in sessions)
    assert any('/web on' == option[2] for option in web)
    assert models[0][2] == '/model gpt-4o'


@pytest.mark.asyncio
async def test_request_and_resolve_pending_tool_approval() -> None:
    app, chat, _history, _workers = make_app()

    task = asyncio.create_task(StudyTUI._request_write_tool_approval(app, 'save_note', {'title': 'Entropy', 'tags': ['physics']}))
    await asyncio.sleep(0)
    pending = app._pending_tool_approval
    assert pending is not None
    assert 'Approval required before writing to disk or running a local render.' in chat.system_messages[0]
    assert chat.pickers[-1][0] == 'Resolve this write request.'
    assert chat.pickers[-1][1][0][2] == '/approve'
    assert chat.pickers[-1][1][1][2] == '/deny'

    resolved = StudyTUI._resolve_pending_tool_approval(app, True)
    assert resolved is pending
    assert await task is True
    assert app._pending_tool_approval is None


def test_deny_pending_tool_approval_message() -> None:
    app, chat, _history, _workers = make_app()
    loop = asyncio.new_event_loop()
    future = loop.create_future()
    app._pending_tool_approval = PendingToolApproval('save_note', 'title', ['line'], future)
    assert StudyTUI._deny_pending_tool_approval(app, 'Denied by user.') is True
    assert future.result() is False
    assert chat.system_messages[-1] == 'Denied by user.'
    loop.close()


def test_handle_slash_commands_cover_primary_branches(tmp_path, monkeypatch) -> None:
    app, chat, history, workers = make_app()
    app._documents_dir = str(tmp_path)
    app._agent_manager.documents_dir = str(tmp_path)
    app.doc_store.add_document(Document(id='doc1', title='Physics', path='physics.pdf', total_pages=2, chunks=[Chunk(id='c1', doc_id='doc1', page=1, text='entropy line\nmomentum line', summary='sum')]))

    assert StudyTUI._handle_slash_command(app, '/docdir') is True
    assert str(tmp_path) in chat.system_messages[-1]

    bad_dir = tmp_path / 'missing'
    assert StudyTUI._handle_slash_command(app, f'/docdir {bad_dir}') is True
    assert 'Not a valid directory' in chat.errors[-1]

    new_dir = tmp_path / 'docs'
    new_dir.mkdir()
    assert StudyTUI._handle_slash_command(app, f'/docdir {new_dir}') is True
    assert app._settings.values['documents_dir'] == str(new_dir)

    assert StudyTUI._handle_slash_command(app, '/docs') is True
    assert chat.info_blocks[-1][0] == 'Loaded Documents'

    assert StudyTUI._handle_slash_command(app, '/page 1 doc1') is True
    assert chat.info_blocks[-1][0].startswith('Page 1')

    assert StudyTUI._handle_slash_command(app, '/page bad') is True
    assert 'Usage: /page <number> [doc_id]' in chat.system_messages[-1]

    assert StudyTUI._handle_slash_command(app, '/copy') is True
    assert 'Nothing to copy yet.' in chat.system_messages[-1]

    chat._last_response = 'assistant reply'
    system_root = tmp_path / 'Windows'
    clip = system_root / 'System32' / 'clip.exe'
    clip.parent.mkdir(parents=True)
    clip.write_text('clip', encoding='utf-8')
    monkeypatch.setenv('SystemRoot', str(system_root))
    monkeypatch.setattr(app_module.subprocess, 'run', lambda *args, **kwargs: None)
    assert StudyTUI._handle_slash_command(app, '/copy') is True
    assert chat.tool_done[-1] == 'Copied to clipboard!'

    assert StudyTUI._handle_slash_command(app, '/clear') is True
    assert app._chat_history == [] and chat.cleared is True and chat.welcome_written is True

    chat.cleared = False
    chat.welcome_written = False
    assert StudyTUI._handle_slash_command(app, '/new') is True
    assert history.new_sessions == 1

    assert StudyTUI._handle_slash_command(app, '/history') is True
    assert chat.pickers[-1][0] == 'Choose a session to resume.'

    assert StudyTUI._handle_slash_command(app, '/resume 2') is True
    assert chat.user_messages[-1] == 'older question'
    assert chat.assistant_messages[-1] == 'older answer'

    assert StudyTUI._handle_slash_command(app, '/resume nope') is True
    assert 'Use a number' in chat.errors[-1]

    app._previous_session_id = 2
    assert StudyTUI._handle_slash_command(app, '/continue') is True
    assert chat.system_messages[-1].startswith('↻ Resumed:')

    chat._last_response = 'first paragraph\n\nsecond paragraph'
    assert StudyTUI._handle_slash_command(app, '/q') is True
    assert any('Paragraphs from last response:' in msg for msg in chat.system_messages)
    assert StudyTUI._handle_slash_command(app, '/q 1-2') is True
    assert chat.input.value.startswith('> "first paragraph')

    assert StudyTUI._handle_slash_command(app, '/help') is True
    assert chat.welcome_written is True

    assert StudyTUI._handle_slash_command(app, '/animate') is True
    assert workers[-1][0].name == '_run_study_action'

    assert StudyTUI._handle_slash_command(app, '/load') is True
    assert workers[-1][0].name == '_pick_and_load_file'

    assert StudyTUI._handle_slash_command(app, f'/load {new_dir / "doc.pdf"}') is True
    assert workers[-1][0].name == '_load_file'


def test_handle_slash_commands_for_provider_key_theme_and_model(monkeypatch) -> None:
    app, chat, _history, workers = make_app()
    app._key_store = FakeKeyStore({'anthropic': 'abcd1234'}, secure=True)
    init_calls = []
    app._init_provider = lambda: init_calls.append((app._provider_name, app._model_name))
    app._active_model_label = lambda: 'gpt-4.1'

    assert StudyTUI._handle_slash_command(app, '/web maybe') is True
    assert 'Usage: /web on|off' in chat.errors[-1]

    assert StudyTUI._handle_slash_command(app, '/key') is True
    assert 'API keys:' in chat.system_messages[-2]

    assert StudyTUI._handle_slash_command(app, '/key openai:sk-live') is True
    assert init_calls[-1] == ('openai', 'gpt-4o')
    assert 'API key saved securely for OpenAI API' in chat.tool_done[-1]

    app._resolve_api_key = lambda provider: ''
    assert StudyTUI._handle_slash_command(app, '/provider missing') is True
    assert 'Unknown provider' in chat.errors[-1]

    assert StudyTUI._handle_slash_command(app, '/provider anthropic') is True
    assert 'Set an API key for Anthropic' in chat.system_messages[-1]

    app._resolve_api_key = lambda provider: 'oauth' if provider == 'openai-codex' else 'token'
    assert StudyTUI._handle_slash_command(app, '/provider openai-codex') is True
    assert init_calls[-1][0] == 'openai-codex'

    assert StudyTUI._handle_slash_command(app, '/model gpt-4.1-mini') is True
    assert init_calls[-1] == ('openai-codex', 'gpt-4.1-mini')

    assert StudyTUI._handle_slash_command(app, '/model') is True
    assert workers[-1][0].name == '_show_model_picker'

    assert StudyTUI._handle_slash_command(app, '/theme nope') is True
    assert 'Unknown theme' in chat.errors[-1]

    assert StudyTUI._handle_slash_command(app, '/theme paper') is True
    assert ('add', 'theme-paper') in app._class_calls
    assert app._settings.values['theme'] == 'paper'


@pytest.mark.asyncio
async def test_fetch_provider_models_and_show_model_picker(monkeypatch) -> None:
    app, chat, _history, _workers = make_app()
    app._provider = FakeProvider(name='openai', model='gpt-4o', models=['gpt-4o', 'gpt-4.1'])
    app._resolve_api_key = lambda provider: 'token'

    models = await StudyTUI._fetch_provider_models(app, 'openai')
    assert models == ['gpt-4.1', 'gpt-4o']

    await StudyTUI._show_model_picker(app)
    assert app._provider_models_cache['openai'] == ['gpt-4.1', 'gpt-4o']
    assert chat.pickers[-1][0].startswith('Choose a model')

    app._resolve_api_key = lambda provider: ''
    await StudyTUI._show_model_picker(app)
    assert 'Set an API key' in chat.system_messages[-1]


@pytest.mark.asyncio
async def test_load_file_and_quiz_and_generation_paths(tmp_path, monkeypatch) -> None:
    app, chat, history, _workers = make_app()
    pdf_path = tmp_path / 'doc.pdf'
    pdf_path.write_text('pdf', encoding='utf-8')
    image_path = tmp_path / 'img.png'
    image_path.write_text('img', encoding='utf-8')
    txt_path = tmp_path / 'bad.txt'
    txt_path.write_text('bad', encoding='utf-8')
    parsed_doc = Document(id='docx', title='Loaded', path=str(pdf_path), total_pages=3, chunks=[Chunk(id='c1', doc_id='docx', page=1, text='text', summary='sum')])

    monkeypatch.setattr(app_module, 'parse_pdf', lambda path: parsed_doc)
    monkeypatch.setattr(app_module, 'parse_image', lambda path: parsed_doc)
    await StudyTUI._load_file(app, str(pdf_path))
    await StudyTUI._load_file(app, str(image_path))
    await StudyTUI._load_file(app, str(txt_path))
    await StudyTUI._load_file(app, str(tmp_path / 'missing.pdf'))
    assert any('Loaded Loaded' in item for item in chat.tool_done)
    assert any('Unsupported' in item for item in chat.errors)
    assert any('File not found' in item for item in chat.errors)

    app.doc_store.documents = {'docx': parsed_doc}
    app._provider = FakeProvider(chat_result='```json\n[{"type":"mcq","question":"Q?","options":["a) x","b) y"],"answer":"a","explanation":"Because"}]\n```')
    app._agent_manager = FakeAgentManager()
    await StudyTUI._run_interactive_quiz(app)
    assert chat.started_quizzes

    app._provider.chat_calls.clear()
    await StudyTUI._run_interactive_quiz(app, user_request='quiz me on definitions only')
    assert chat.user_messages[-1] == 'quiz me on definitions only'
    assert 'User request for this quiz: quiz me on definitions only' in app._provider.chat_calls[-1]['messages'][-1]['content']

    app._provider = FakeProvider(chat_result='not-json')
    await StudyTUI._run_interactive_quiz(app)
    assert 'Failed to parse quiz' in chat.errors[-1]

    event = SimpleNamespace(score=1, total=2, results=[{'question': 'A', 'correct': False, 'user_answer': 'x', 'expected_answer': 'y'}])
    await StudyTUI.on_chat_view_quiz_finished(app, event)
    assert any(
        msg['role'] == 'user' and '[System context' in msg['content']
        for msg in app._chat_history
    )

    app._chat_history = [{'role': 'user', 'content': 'hello'}]
    app._provider = FakeProvider()
    app._agent_manager = FakeAgentManager()
    app._privacy_mode = 'standard'
    app._remote_docs_approved = True

    async def fake_stream_chat(**kwargs):
        kwargs['on_thinking']('reasoning')
        kwargs['on_text']('answer')
        kwargs['on_tool_call']('search_chunks', {'query': 'entropy'})
        return 'answer'

    monkeypatch.setattr(app_module, 'stream_chat', fake_stream_chat)
    await StudyTUI._run_generation(app)
    assert history.saved_payloads
    assert chat.tool_start[-1].startswith('Searching documents')

    chat.response_tokens.clear()

    async def think_tag_stream(**kwargs):
        kwargs['on_text']('Before <think>hidden reasoning</think> after')
        return 'Before <think>hidden reasoning</think> after'

    monkeypatch.setattr(app_module, 'stream_chat', think_tag_stream)
    await StudyTUI._run_generation(app)
    rendered_tokens = ''.join(chat.response_tokens)
    assert '<think>' not in rendered_tokens
    assert '</think>' not in rendered_tokens
    assert 'THINK:hidden reasoning' in rendered_tokens

    normalized = app_module._normalize_terminal_output(
        "Comparison:\n\n"
        "| Topic | Status |\n"
        "| --- | --- |\n"
        "| Quiz | Complete |\n"
        "| Flashcards | Ready |\n"
    )
    assert "| Topic | Status |" not in normalized
    assert "- Topic: Quiz; Status: Complete" in normalized
    assert "- Topic: Flashcards; Status: Ready" in normalized

    chat.response_tokens.clear()

    async def table_stream(**kwargs):
        text = (
            "Current status:\n\n"
            "| Topic | Status |\n"
            "| --- | --- |\n"
            "| Quiz | Complete |\n"
            "| Flashcards | Ready |\n"
        )
        kwargs['on_text'](text)
        return text

    monkeypatch.setattr(app_module, 'stream_chat', table_stream)
    await StudyTUI._run_generation(app)
    rendered_tokens = ''.join(chat.response_tokens)
    assert "| Topic | Status |" not in rendered_tokens
    assert "- Topic: Quiz; Status: Complete" in rendered_tokens
    assert app._chat_history[-1]['content'] == rendered_tokens

    chat.response_tokens.clear()
    start_response_before_flashcards = chat.start_response_calls

    async def flashcard_stream(**kwargs):
        text = (
            "Yes, the chapter is loaded.\n\n"
            "[FLASHCARDS]\n"
            "Q: What is heat?\n"
            "A: Heat is energy transferred due to a temperature difference.\n\n"
            "Q: What is thermal equilibrium?\n"
            "A: It is the state where no net heat flows.\n\n"
            "[/FLASHCARDS]\n\n"
            "If you want, I can make a harder set next."
        )
        kwargs['on_text'](text)
        return text

    monkeypatch.setattr(app_module, 'stream_chat', flashcard_stream)
    await StudyTUI._run_generation(app)
    assert chat.started_flashcards
    cards, intro_lines, outro_lines = chat.started_flashcards[-1]
    assert cards[0]['question'] == 'What is heat?'
    assert intro_lines[0] == 'Yes, the chapter is loaded.'
    assert outro_lines == ['If you want, I can make a harder set next.']
    assert chat.start_response_calls == start_response_before_flashcards

    async def agentic_quiz_stream(**kwargs):
        kwargs['on_tool_result'](
            'generate_quiz',
            {
                'tool': 'generate_quiz',
                'result': (
                    '[{"type":"mcq","question":"What is SI unit?","options":["a) metre","b) kilogram","c) second","d) ampere"],'
                    '"answer":"d","explanation":"Electric current is a base quantity."}]'
                ),
            },
        )
        raw_json = (
            '[{"type":"mcq","question":"What is SI unit?","options":["a) metre","b) kilogram","c) second","d) ampere"],'
            '"answer":"d","explanation":"Electric current is a base quantity."}]'
        )
        kwargs['on_text'](raw_json)
        return raw_json

    monkeypatch.setattr(app_module, 'stream_chat', agentic_quiz_stream)
    await StudyTUI._run_generation(app)
    assert chat.started_quizzes
    assert chat.started_quizzes[-1][0]['question'] == 'What is SI unit?'
    assert chat.tool_done[-1] == 'Generated 1 questions'
    assert app._chat_history[-1]['content'] == '[QUIZ SESSION STARTED] Generated 1 interactive questions.'
    assert ''.join(chat.response_tokens) == ''

    chat.response_tokens.clear()
    start_response_before_tool_flashcards = chat.start_response_calls

    async def agentic_flashcard_stream(**kwargs):
        deck = (
            "Here are some flashcards to reinforce the weak points:\n\n"
            "[FLASHCARDS]\n"
            "Q: What is heat?\n"
            "A: Heat is energy transferred due to a temperature difference.\n\n"
            "Q: What is thermal equilibrium?\n"
            "A: It is the state where no net heat flows.\n"
            "[/FLASHCARDS]\n\n"
            "If you want, I can make a harder set next."
        )
        kwargs['on_tool_result'](
            'generate_flashcards',
            {
                'tool': 'generate_flashcards',
                'result': deck,
            },
        )
        kwargs['on_text'](deck)
        return deck

    monkeypatch.setattr(app_module, 'stream_chat', agentic_flashcard_stream)
    await StudyTUI._run_generation(app)
    assert chat.started_flashcards
    cards, intro_lines, outro_lines = chat.started_flashcards[-1]
    assert cards[0]['question'] == 'What is heat?'
    assert intro_lines == ['Here are some flashcards to reinforce the weak points:']
    assert outro_lines == ['If you want, I can make a harder set next.']
    assert chat.tool_done[-1] == 'Generated 2 flashcards'
    assert app._chat_history[-1]['content'] == '[FLASHCARD SESSION STARTED] Generated 2 flashcards.'
    assert ''.join(chat.response_tokens) == ''
    assert chat.start_response_calls == start_response_before_tool_flashcards

    async def agentic_cloze_flashcard_stream(**kwargs):
        deck = [
            {
                "question": "Fill the blank: {{c1::Heat}} moves from hot to cold.",
                "answer": "Heat moves from hot to cold.",
                "card_type": "cloze",
                "cloze_text": "{{c1::Heat}} moves from hot to cold.",
                "source_refs": [{"doc_id": "docx", "page": 2, "chunk_id": "c2"}],
                "tags": ["thermo"],
                "focus": "weak_area",
                "difficulty": "medium",
            }
        ]
        payload = json.dumps(deck)
        kwargs['on_tool_result'](
            'generate_flashcards',
            {
                'tool': 'generate_flashcards',
                'result': payload,
            },
        )
        kwargs['on_text'](payload)
        return payload

    monkeypatch.setattr(app_module, 'stream_chat', agentic_cloze_flashcard_stream)
    await StudyTUI._run_generation(app)
    cards, _intro_lines, _outro_lines = chat.started_flashcards[-1]
    assert cards[0]['card_type'] == 'cloze'
    assert cards[0]['cloze_text'] == '{{c1::Heat}} moves from hot to cold.'
    assert cards[0]['source_refs'] == [{"doc_id": "docx", "page": 2, "chunk_id": "c2"}]
    assert cards[0]['tags'] == ["thermo"]
    assert app._progress_mgr.flashcard_calls[-1]['cards'][0]['card_type'] == 'cloze'

    chat.response_tokens.clear()

    async def tool_first_flashcard_stream(**kwargs):
        kwargs['on_tool_call'](
            'generate_flashcards',
            {
                'topic': 'weak points',
                'count': 6,
            },
        )
        deck = (
            "Here are some flashcards to reinforce the points you missed:\n\n"
            "[FLASHCARDS]\n"
            "Q: What is heat?\n"
            "A: Heat is energy transferred due to a temperature difference.\n\n"
            "Q: What is thermal equilibrium?\n"
            "A: It is the state where no net heat flows.\n"
            "[/FLASHCARDS]\n\n"
            "If you want, I can make a harder set next."
        )
        kwargs['on_text'](deck)
        return deck

    monkeypatch.setattr(app_module, 'stream_chat', tool_first_flashcard_stream)
    await StudyTUI._run_generation(app)
    assert chat.started_flashcards
    cards, intro_lines, outro_lines = chat.started_flashcards[-1]
    assert cards[0]['question'] == 'What is heat?'
    assert intro_lines == ['Here are some flashcards to reinforce the points you missed:']
    assert outro_lines == ['If you want, I can make a harder set next.']
    assert app._chat_history[-1]['content'] == '[FLASHCARD SESSION STARTED] Generated 2 flashcards.'
    assert ''.join(chat.response_tokens) == ''
    assert chat.start_response_calls == start_response_before_tool_flashcards

    chat.response_tokens.clear()

    async def table_after_started_stream(**kwargs):
        chunks = [
            "Intro paragraph.\n\n",
            "| Topic | Status |\n| --- | --- |\n| Quiz | Complete |\n",
            "\nNext step: review.",
        ]
        for chunk in chunks:
            kwargs['on_text'](chunk)
        return ''.join(chunks)

    monkeypatch.setattr(app_module, 'stream_chat', table_after_started_stream)
    await StudyTUI._run_generation(app)
    rendered_tokens = ''.join(chat.response_tokens)
    assert "| Topic | Status |" not in rendered_tokens
    assert "- Topic: Quiz; Status: Complete" in rendered_tokens
    assert "Next step: review." in rendered_tokens
    assert app._chat_history[-1]['content'] == rendered_tokens

    chat.response_tokens.clear()

    async def animation_stream(**kwargs):
        kwargs['on_tool_result'](
            'animate_concept',
            {
                'status': 'success',
                'topic': 'Units and Measurement',
                'attempt': 1,
                'retryable': False,
                'backend': 'motion_canvas',
                'quality': 'low',
                'scene_name': 'UnitsScene',
                'duration_seconds': 1.2,
                'video_path': 'C:/exports/units.mp4',
                'code_path': 'C:/exports/units.py',
            },
        )
        kwargs['on_text']('Animation rendered successfully.')
        return 'Animation rendered successfully.'

    monkeypatch.setattr(app_module, 'stream_chat', animation_stream)
    await StudyTUI._run_generation(app)
    assert chat.tool_done[-1] == 'Rendered animation for Units and Measurement.'
    assert chat.info_blocks[-1][0] == 'Animation'
    assert any('Backend: motion_canvas' in line for line in chat.info_blocks[-1][1])
    assert any('Video: C:/exports/units.mp4' in line for line in chat.info_blocks[-1][1])
    assert app._chat_history[-1]['content'].startswith('[ANIMATION RENDERED]')

    async def cancel_stream(**kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(app_module, 'stream_chat', cancel_stream)
    await StudyTUI._run_generation(app)
    assert 'Generation cancelled.' in chat.system_messages[-1]

    async def error_stream(**kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr(app_module, 'stream_chat', error_stream)
    await StudyTUI._run_generation(app)
    assert 'Error: boom' in chat.errors[-1]


@pytest.mark.asyncio
async def test_numeric_quiz_verification_uses_fresh_provider(monkeypatch) -> None:
    app, chat, _history, _workers = make_app()
    app._provider_name = 'openai'
    app._model_name = 'gpt-5.4'
    provider = FakeProvider(chat_result='{"correct": true, "feedback": "Equivalent numeric value."}')
    create_calls = []

    def fake_create_provider(*args, **kwargs):
        create_calls.append((args, kwargs))
        return provider

    monkeypatch.setattr(app_module, 'create_provider', fake_create_provider)
    event = SimpleNamespace(
        quiz_index=2,
        question={'type': 'numeric', 'question': 'What is 6 x 7?', 'answer': '42', 'explanation': 'Multiply six by seven.'},
        user_answer='42.0',
    )

    await StudyTUI.on_chat_view_quiz_answer_submitted(app, event)

    assert create_calls
    assert provider.chat_calls
    assert provider.chat_calls[-1]['system'] == NUMERIC_QUIZ_GRADER_PROMPT
    assert chat.completed_numeric_answers[-1] == (2, True, 'Equivalent numeric value.')


@pytest.mark.asyncio
async def test_quiz_results_add_score_and_answers_to_context() -> None:
    app, _chat, _history, _workers = make_app()
    event = SimpleNamespace(
        score=2,
        total=3,
        results=[
            {'question': 'Q1', 'correct': True, 'user_answer': 'Paris', 'expected_answer': 'Paris', 'grading_feedback': ''},
            {'question': 'Q2', 'correct': False, 'user_answer': '4.1', 'expected_answer': '4.0', 'grading_feedback': 'Wrong rounding target.'},
        ],
    )

    await StudyTUI.on_chat_view_quiz_finished(app, event)

    summary = app._chat_history[-2]['content']
    assert 'Score: 2/3' in summary
    assert 'Student answered: Paris' in summary
    assert 'Correct answer: 4.0' in summary
    assert 'Grader note: Wrong rounding target.' in summary



@pytest.mark.asyncio
async def test_user_message_and_study_action_paths(monkeypatch) -> None:
    app, chat, _history, workers = make_app()
    app._provider = None
    app._agent_manager = None
    await StudyTUI.on_chat_view_user_message(app, SimpleNamespace(text='hello'))
    assert 'Set API key first' in chat.system_messages[-1]

    await StudyTUI.on_chat_view_user_message(app, SimpleNamespace(text='/unknown'))
    assert 'Unknown command' in chat.system_messages[-1]

    app._provider = FakeProvider()
    app._agent_manager = FakeAgentManager()
    await StudyTUI.on_chat_view_user_message(app, SimpleNamespace(text='hello'))
    assert app._chat_history[-1]['content'] == 'hello'
    assert workers[-1][0].name == '_run_generation'

    app.doc_store.documents = {'doc1': object()}
    await StudyTUI.on_chat_view_user_message(app, SimpleNamespace(text='make flashcards from this chapter'))
    assert app._chat_history[-1]['content'] == 'make flashcards from this chapter'
    assert workers[-1][0].name == '_run_generation'

    app._provider.chat_result = (
        "1. Q: What is chemistry?\n"
        "A: The study of matter.\n\n"
        "2. Q: What is matter?\n"
        "A: Anything that has mass and occupies space."
    )
    app._privacy_mode = 'standard'
    app._remote_docs_approved = True

    async def ok_stream(**kwargs):
        kwargs['on_text']('summary!')
        return 'summary!'

    monkeypatch.setattr(app_module, 'stream_chat', ok_stream)
    await StudyTUI._run_study_action(app, 'flashcards')
    assert app._chat_history[-1]['content'].startswith('1. Q: What is chemistry?')
    assert chat.started_flashcards
    cards, intro_lines, outro_lines = chat.started_flashcards[-1]
    assert cards[0]['question'] == 'What is chemistry?'
    assert cards[0]['answer'] == 'The study of matter.'
    assert intro_lines == []
    assert outro_lines == []
    assert chat.start_response_calls == 0

    await StudyTUI._run_study_action(app, 'summary')
    assert app._chat_history[-1]['content'] == 'summary!'
    assert chat.start_response_calls == 1

    app._provider.chat_calls.clear()
    await StudyTUI._run_study_action(app, 'flashcards', user_request='make flashcards on formulas only')
    assert chat.user_messages[-1] == 'make flashcards on formulas only'
    assert app._chat_history[-2]['content'] == 'make flashcards on formulas only'
    assert 'User request: make flashcards on formulas only' in app._provider.chat_calls[-1]['messages'][-1]['content']

    async def bad_stream(**kwargs):
        raise RuntimeError('study failed')

    monkeypatch.setattr(app_module, 'stream_chat', bad_stream)
    await StudyTUI._run_study_action(app, 'summary')
    assert 'study failed' in chat.errors[-1]


@pytest.mark.asyncio
async def test_review_uses_persistent_queue() -> None:
    app, chat, _history, _workers = make_app()
    app.doc_store.documents = {'doc1': object()}
    app._doc_source_hashes = {'doc1': 'hash123'}
    app._latest_source_hash = 'hash123'

    def fake_review_queue(*, source_hash=None, doc_id=None, limit=20):
        assert source_hash == 'hash123'
        assert doc_id == 'doc1'
        return {
            'title': 'Kinematics',
            'cards': [
                {'card_key': 'card-1', 'question': 'What is displacement?', 'answer': 'Change in position.'},
                {'card_key': 'card-2', 'question': 'What is instantaneous velocity?', 'answer': 'Velocity at an instant.'},
            ],
            'card_count': 2,
            'due_count': 2,
            'new_count': 2,
            'weak_topics': ['instantaneous velocity'],
        }

    app._progress_mgr.get_review_queue = fake_review_queue
    await StudyTUI._run_review(app)

    assert chat.user_messages[-1] == '/review'
    assert chat.started_flashcards
    cards, intro_lines, outro_lines = chat.started_flashcards[-1]
    assert cards[0]['question'] == 'What is displacement?'
    assert cards[0]['card_key'] == 'card-1'
    assert 'Prioritizing weak areas' in intro_lines[-1]
    assert outro_lines == []
    assert app._chat_history[-1]['content'].startswith('[REVIEW SESSION STARTED]')


@pytest.mark.asyncio
async def test_flashcard_review_events_persist_progress() -> None:
    app, _chat, history, _workers = make_app()
    app.doc_store.documents = {'doc1': object()}
    app._doc_source_hashes = {'doc1': 'hash123'}
    app._latest_source_hash = 'hash123'

    await StudyTUI.on_chat_view_flashcard_reviewed(
        app,
        SimpleNamespace(card={'card_key': 'card-1', 'question': 'What is displacement?', 'answer': 'Change in position.'}, grade='hard'),
    )
    assert app._progress_mgr.review_calls[-1]['card_key'] == 'card-1'
    assert app._progress_mgr.review_calls[-1]['grade'] == 'hard'

    await StudyTUI.on_chat_view_flashcard_review_finished(
        app,
        SimpleNamespace(total=3, grades={'again': 1, 'hard': 1, 'good': 1, 'easy': 0}),
    )
    assert app._progress_mgr.note_calls[-1]['metadata']['kind'] == 'flashcard_review_finished'
    assert history.saved_payloads


@pytest.mark.asyncio
async def test_usage_reports_session_tokens_and_context_limit() -> None:
    app, chat, _history, _workers = make_app()
    app._chat_history = [{'role': 'user', 'content': 'Explain entropy simply.'}]
    app._session_prompt_tokens = 120
    app._session_completion_tokens = 45
    app._last_selected_tools = ['list_documents', 'search_chunks']
    app._last_tool_schema_tokens = 321

    async def fake_context_window():
        return 128000

    app._provider.get_context_window_async = fake_context_window
    await StudyTUI._show_usage(app)

    title, lines = chat.info_blocks[-1]
    assert title == 'Usage'
    joined = '\n'.join(lines)
    assert 'Session prompt tokens' in joined
    assert 'Session completion tokens' in joined
    assert 'Context window' in joined
    assert 'Remaining before limit' in joined
    assert 'Compact memory blocks' in joined
    assert 'Selected tools in latest request' in joined
    assert 'Latest tool-schema tokens' in joined


@pytest.mark.asyncio
async def test_context_reports_prompt_state_breakdown() -> None:
    app, chat, _history, _workers = make_app()
    app._chat_history = [
        {'role': 'user', 'content': 'Please summarize entropy.'},
        {'role': 'assistant', 'content': 'Entropy is a measure of disorder.'},
    ]
    app._compact_memories = [{'id': 'mem_1', 'summary': 'Older thermodynamics discussion', 'source_count': 4}]
    app._compacted_transcript_count = 1
    app._tool_artifacts = [
        {
            'tool_name': 'list_documents',
            'turn_index': 1,
            'retention_class': 'ephemeral',
            'full_payload': [{'doc_id': 'doc1', 'title': 'Thermo'}],
            'gist_payload': 'Loaded documents (1): Thermo',
            'category': 'tool_listing',
            'source_refs': ['doc1'],
        }
    ]
    app._request_turn_index = 2
    app._last_selected_tools = ['list_documents', 'search_chunks']
    app._last_tool_schema_tokens = 456

    async def fake_context_window():
        return 32000

    app._provider.get_context_window_async = fake_context_window
    await StudyTUI._show_context(app)

    title, lines = chat.info_blocks[-1]
    assert title == 'Context'
    joined = '\n'.join(lines)
    assert 'Transcript messages' in joined
    assert 'Compact memory blocks' in joined
    assert 'Retained tool artifacts' in joined
    assert 'Selected tools in latest request' in joined
    assert 'Category pressure:' in joined
    assert 'Largest contributors' in joined


def test_select_tools_returns_persistent_full_toolset() -> None:
    app, _chat, _history, _workers = make_app()
    selected = StudyTUI._select_tools(app, "animate the concept and export to anki", flow="animate")
    names = [tool['name'] for tool in selected]
    assert 'animate_concept' in names
    assert 'get_study_progress' in names
    assert 'export_content' in names
    assert 'pomodoro_start' in names
    assert 'zotero_search' in names
    assert 'calibre_search' in names
    assert 'spawn_subagent' in names


def test_select_tools_keeps_core_study_tools_available() -> None:
    app, _chat, _history, _workers = make_app()
    selected = StudyTUI._select_tools(app, "load keph101", flow="chat")
    names = [tool["name"] for tool in selected]
    assert "generate_quiz" in names
    assert "generate_flashcards" in names
    assert "summarize_document" in names
    assert "get_study_progress" in names
    assert "get_review_queue" in names
    assert "animate_concept" in names
    assert "search_notes" in names
    assert "pomodoro_start" in names
    assert "export_content" in names


def test_select_tools_keeps_animation_for_confirmation_turns() -> None:
    app, _chat, _history, _workers = make_app()
    app._append_turn("assistant", "Would you like me to generate the animation now?")
    selected = StudyTUI._select_tools(app, "yes", flow="chat")
    names = [tool["name"] for tool in selected]
    assert "animate_concept" in names


def test_select_tools_keeps_animation_for_loaded_followup_turns() -> None:
    app, _chat, _history, _workers = make_app()
    app._append_turn("user", "load keph102, and please animate the difference of velocity and speed")
    app._append_turn("assistant", "I can animate that once the document is loaded.")
    selected = StudyTUI._select_tools(app, "it is loaded", flow="chat")
    names = [tool["name"] for tool in selected]
    assert "animate_concept" in names


def test_note_tool_result_persists_ephemeral_artifact() -> None:
    app, _chat, _history, _workers = make_app()
    app._active_request_turn_index = 1

    StudyTUI._note_tool_result(
        app,
        'list_documents',
        [{'doc_id': 'doc1', 'title': 'Thermo'}],
    )

    assert app._tool_artifacts
    assert app._tool_artifacts[0]['tool_name'] == 'list_documents'
    assert app._tool_artifacts[0]['retention_class'] == 'ephemeral'


def test_note_tool_result_keeps_chunk_context_stable_and_deduped_for_chat() -> None:
    app, _chat, _history, _workers = make_app()
    app._active_request_turn_index = 1

    StudyTUI._note_tool_result(
        app,
        'get_chunk_by_id',
        {'chunk_id': 'doc1_c1', 'doc_id': 'doc1', 'page': 3, 'text': 'Velocity is displacement over time.'},
    )
    StudyTUI._note_tool_result(
        app,
        'get_chunk_by_id',
        {'chunk_id': 'doc1_c1', 'doc_id': 'doc1', 'page': 3, 'text': 'Velocity is displacement over time.'},
    )

    assert len(app._tool_artifacts) == 1
    assert app._tool_artifacts[0]['retention_class'] == 'conversation'


def test_select_tools_keeps_library_fallback_tools_for_load_followup_workflow() -> None:
    app, _chat, _history, _workers = make_app()
    StudyTUI._mark_pending_study_workflow(app, "load keph102, and please animate the difference of velocity and speed")
    app._append_turn("assistant", "I don't see keph102 in the current documents folder yet.")
    selected = StudyTUI._select_tools(app, "yes", flow="chat")
    names = [tool["name"] for tool in selected]
    assert "calibre_search" in names
    assert "calibre_load" in names
    assert "zotero_search" in names
    assert "zotero_load" in names
    assert "zotero_collections" in names


def test_system_prompt_for_tools_loads_animation_skill_only_for_animation(monkeypatch) -> None:
    app, _chat, _history, _workers = make_app()
    selected = StudyTUI._select_tools(app, "animate velocity", flow="animate")
    plain = StudyTUI._system_prompt_for_tools(app, selected)
    monkeypatch.setattr(app_module, "_load_manim_skill_guidance", lambda: "SKILL BODY")
    animation_prompt = StudyTUI._system_prompt_for_tools(app, selected, include_animation_skill=True)
    assert "Tool availability for this request:" in plain
    assert "The complete persistent toolset for this conversation is available on this request:" in plain
    assert "Animation-specific execution guidance:" not in plain
    assert "references/manim-design-patterns.md" not in animation_prompt
    assert animation_prompt == plain + "\nWhen animate_concept is available, follow this compact animation guidance before producing code.\n\nSKILL BODY"
    assert "TeX" in app_module.SYSTEM_PROMPT


def test_animation_guidance_includes_motion_canvas_template_and_retry_hint() -> None:
    assert "supported scaffold" in app_module.SYSTEM_PROMPT
    assert "@motion-canvas/core" in app_module.SYSTEM_PROMPT
    assert "does not provide an export named" in app_module.COMPACT_ANIMATION_GUIDANCE


def test_system_prompt_for_tools_describes_persistent_toolset() -> None:
    app, _chat, _history, _workers = make_app()
    selected = StudyTUI._select_tools(app, "load keph101", flow="chat")
    prompt = StudyTUI._system_prompt_for_tools(app, selected)
    assert "animate_concept" in [tool["name"] for tool in selected]
    assert "Animation-specific execution guidance:" not in prompt
    assert "Never invent or call a tool that is not present in request.tools." in prompt
    assert "The complete persistent toolset for this conversation is available on this request:" in prompt
    assert "The host keeps this toolset available across follow-up turns" in prompt
    assert "unavailable on this turn" not in prompt


def test_system_prompt_for_tools_includes_pending_workflow() -> None:
    app, _chat, _history, _workers = make_app()
    StudyTUI._mark_pending_study_workflow(app, "load keph102, and please animate the difference of velocity and speed")
    selected = StudyTUI._select_tools(app, "yes", flow="chat")
    prompt = StudyTUI._system_prompt_for_tools(app, selected)
    assert "Pending workflow: load_then_animate." in prompt
    assert "Original request to preserve across follow-ups:" in prompt


def test_pending_workflow_prompt_message_is_internal_system_context() -> None:
    app, _chat, _history, _workers = make_app()
    StudyTUI._mark_pending_study_workflow(app, "load keph102 and animate the difference between speed and velocity")
    msg = StudyTUI._pending_workflow_prompt_message(app)
    assert msg is not None
    assert msg["role"] == "system"
    assert "pending study workflow" in msg["content"].lower()
    assert "load_then_animate" in msg["content"]


@pytest.mark.asyncio
async def test_study_setup_keeps_host_instruction_out_of_history() -> None:
    app, chat, _history, workers = make_app()

    await StudyTUI._run_study_setup(app)

    assert chat.user_messages[-1] == "/study-setup"
    assert app._chat_history == [{"role": "user", "content": "/study-setup"}]
    assert len(app._model_history) == 1
    assert app._model_history[0]["role"] == "user"
    assert app._model_history[0]["content"] == "/study-setup"
    assert workers[-1][0].name == "_run_generation"


@pytest.mark.asyncio
async def test_setup_answer_does_not_append_host_followup_instruction() -> None:
    app, _chat, _history, _workers = make_app()
    app._chat_history = [{"role": "user", "content": "/study-setup"}]
    app._model_history = [{"role": "user", "content": "/study-setup"}]
    app._setup_state = {"active": True, "index": 0, "answers": {}}

    await StudyTUI._handle_setup_answer(app, "exam performance")

    assert app._setup_state["answers"]["goal"] == "exam performance"
    assert app._chat_history == [{"role": "user", "content": "/study-setup"}]
    assert len(app._model_history) == 1
    assert app._model_history[0]["role"] == "user"
    assert app._model_history[0]["content"] == "/study-setup"


@pytest.mark.asyncio
async def test_setup_answer_cancel_aborts_setup() -> None:
    app, chat, _history, workers = make_app()
    app._setup_state = {"active": True, "index": 0, "answers": {}}

    await StudyTUI._handle_setup_answer(app, "/cancel")

    assert app._setup_state is None
    assert chat.system_messages[-1] == "Study setup cancelled."
    assert workers == []


def test_mark_pending_workflow_clears_on_clear_pivot() -> None:
    app, _chat, _history, _workers = make_app()
    StudyTUI._mark_pending_study_workflow(app, "load keph102 and animate velocity")
    assert app._pending_study_workflow is not None
    StudyTUI._mark_pending_study_workflow(app, "save a note about entropy")
    assert app._pending_study_workflow is None


@pytest.mark.asyncio
async def test_animate_study_action_uses_animation_skill_prompt(monkeypatch) -> None:
    app, chat, _history, _workers = make_app()
    doc = Document(id='doc1', title='Physics', path='physics.pdf', total_pages=1, chunks=[Chunk(id='c1', doc_id='doc1', page=1, text='text', summary='sum')])
    app.doc_store.add_document(doc)
    app._privacy_mode = 'standard'
    app._remote_docs_approved = True
    app._provider = FakeProvider(chat_result='Animation request accepted.')
    monkeypatch.setattr(app_module, "_load_manim_skill_guidance", lambda: "MANIM SKILL CONTENT")
    seen = {}

    async def fake_stream_chat(**kwargs):
        seen['system'] = kwargs['system']
        return 'Animation request accepted.'

    monkeypatch.setattr(app_module, 'stream_chat', fake_stream_chat)
    await StudyTUI._run_study_action(app, 'animate', user_request='animate vectors')

    assert app._provider.chat_calls == []
    assert 'MANIM SKILL CONTENT' in seen['system']
    assert 'MANIM SKILL CONTENT' in app._last_request_system_prompt


@pytest.mark.asyncio
async def test_manual_compact_persists_context_state() -> None:
    app, chat, history, _workers = make_app()
    for idx in range(12):
        app._append_turn('user', f'user message {idx}')
        app._append_turn('assistant', 'assistant message ' + ('x' * 1500))

    await StudyTUI._run_compact_command(app)

    title, lines = chat.info_blocks[-1]
    assert title == 'Compaction'
    assert any('Created memory block' in line for line in lines)
    assert history.saved_states
    assert history.saved_states[-1]['compact_memories']


def test_restore_context_state_rebuilds_model_history() -> None:
    app, _chat, _history, _workers = make_app()
    app._chat_history = [
        {'role': 'user', 'content': 'older question'},
        {'role': 'assistant', 'content': 'older answer'},
    ]
    StudyTUI._restore_context_state(app, 2)
    assert app._compact_memories
    assert app._compacted_transcript_count == 1
    assert len(app._model_history) == 1


def test_on_key_cancels_generation() -> None:
    app, chat, _history, workers = make_app()
    cancelled = []
    app._generating = True
    app._generation_worker = SimpleNamespace(is_finished=False, cancel=lambda: cancelled.append('cancelled'))
    app._deny_pending_tool_approval = lambda reason=None: cancelled.append(reason)
    event = SimpleNamespace(key='escape', prevent_default=lambda: cancelled.append('prevent'), stop=lambda: cancelled.append('stop'))
    StudyTUI.on_key(app, event)
    assert 'cancelled' in cancelled
    assert 'prevent' in cancelled and 'stop' in cancelled



def test_system_prompt_guides_tool_use_and_latex() -> None:
    assert 'Before answering questions about loaded material' in app_module.SYSTEM_PROMPT
    assert 'use list_available_files first, then load_file with the returned relative_path' in app_module.SYSTEM_PROMPT
    assert 'If the user asks what notes already exist, use list_notes or search_notes instead of guessing.' in app_module.SYSTEM_PROMPT
    assert 'keep the formulas as LaTeX so the app can render and export them well' in app_module.SYSTEM_PROMPT
    assert 'They do NOT need to type /flashcards, /quiz, /summary, or /animate.' in app_module.SYSTEM_PROMPT
    assert 'handle it end-to-end' in app_module.SYSTEM_PROMPT
    assert 'format=anki to create an .apkg package' in app_module.SYSTEM_PROMPT
    assert 'Use animate_concept when the user asks to animate' in app_module.SYSTEM_PROMPT
    assert '60-90 seconds' in app_module.SYSTEM_PROMPT
    assert 'overlapping text blocks' in app_module.SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_quiz_and_review_offer_animation_suggestions() -> None:
    app, chat, _history, _workers = make_app()
    doc = Document(id='doc1', title='Physics', path='physics.pdf', total_pages=1, chunks=[Chunk(id='c1', doc_id='doc1', page=1, text='text', summary='sum')])
    app.doc_store.add_document(doc)
    app._doc_source_hashes['doc1'] = 'hash-doc1'
    app._latest_source_hash = 'hash-doc1'

    quiz_event = SimpleNamespace(
        score=1,
        total=3,
        results=[{'question': 'Q1', 'correct': False, 'user_answer': 'x', 'expected_answer': 'y'}],
    )
    await StudyTUI.on_chat_view_quiz_finished(app, quiz_event)
    assert any('animate weak topic' in msg.lower() for msg in chat.assistant_messages)

    review_event = SimpleNamespace(total=4, grades={'again': 2, 'hard': 1, 'good': 1, 'easy': 0})
    await StudyTUI.on_chat_view_flashcard_review_finished(app, review_event)
    assert any('animate weak topic' in msg.lower() for msg in chat.assistant_messages)


def test_on_tool_status_suppresses_immediate_duplicate() -> None:
    app, chat, _history, _workers = make_app()
    app._skip_next_tool_status = 'Creating 12 flashcards on "kech101 main concepts"...'

    StudyTUI._on_tool_status(app, 'Creating 12 flashcards on "kech101 main concepts"...')
    assert chat.tool_start == []
    assert app._skip_next_tool_status is None

    StudyTUI._on_tool_status(app, 'Listing loaded documents...')
    assert chat.tool_start == ['Listing loaded documents...']









