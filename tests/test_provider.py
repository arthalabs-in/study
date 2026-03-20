from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import src.agents.provider as provider_module
from src.agents.provider import OpenAIProvider


class FakeResponses:
    def __init__(self) -> None:
        self.called = []

    async def create(self, **kwargs):
        self.called.append(kwargs)
        return SimpleNamespace(id='resp_1', output_text='hello from responses', output=[])


class FakeCompletions:
    def __init__(self) -> None:
        self.called = []

    async def create(self, **kwargs):
        self.called.append(kwargs)
        msg = SimpleNamespace(content='hello from chat', tool_calls=[])
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class FakeClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()
        self.chat = SimpleNamespace(completions=FakeCompletions())


@pytest.mark.asyncio
async def test_openai_codex_routes_via_cli(monkeypatch) -> None:
    provider = OpenAIProvider('openai-codex', api_key='token', model='gpt-5.3-codex')
    provider._client = FakeClient()

    async def fake_codex(messages, system='', on_text=None):
        if on_text:
            on_text('hello from codex cli')
        return 'hello from codex cli'

    monkeypatch.setattr(provider, '_run_via_codex_cli', fake_codex)
    chunks: list[str] = []
    result = await provider.stream_chat(messages=[{'role': 'user', 'content': 'hi'}], on_text=chunks.append)
    assert result == 'hello from codex cli'
    assert chunks == ['hello from codex cli']
    assert provider._client.chat.completions.called == []
    assert provider._client.responses.called == []


@pytest.mark.asyncio
async def test_openai_api_provider_uses_chat_completions() -> None:
    provider = OpenAIProvider('openai', api_key='token', model='gpt-4o')
    provider._client = FakeClient()
    result = await provider.chat(messages=[{'role': 'user', 'content': 'hi'}])
    assert result == 'hello from chat'
    assert provider._client.chat.completions.called


@pytest.mark.asyncio
async def test_openai_codex_models_come_from_models_cache(tmp_path, monkeypatch) -> None:
    codex_home = tmp_path / '.codex'
    codex_home.mkdir()
    (codex_home / 'models_cache.json').write_text(json.dumps({
        'models': [
            {'slug': 'gpt-5.4', 'visibility': 'list'},
            {'slug': 'gpt-5.3-codex', 'visibility': 'list'},
            {'slug': 'hidden-model', 'visibility': 'hidden'},
        ]
    }), encoding='utf-8')
    monkeypatch.setattr(provider_module.Path, 'home', staticmethod(lambda: tmp_path))
    provider = OpenAIProvider('openai-codex', api_key='token', model='gpt-5.4')
    models = await provider.get_models_async()
    assert models == ['gpt-5.3-codex', 'gpt-5.4']
