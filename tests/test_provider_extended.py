from __future__ import annotations

import asyncio

import json
from types import SimpleNamespace

import pytest

import src.agents.provider as provider_module
import src.agents.tools as tools_module
from src.agents.provider import (
    AnthropicProvider,
    MAX_TOOL_CALL_ROUNDS,
    OpenAIProvider,
    get_provider,
    list_providers,
)


class FakeAsyncIterator:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        self._iter = iter(self._items)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class FakeAnthropicStream:
    def __init__(self, events, response):
        self._events = list(events)
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __aiter__(self):
        self._iter = iter(self._events)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration

    async def get_final_message(self):
        return self._response


def test_provider_tool_loop_limit_is_raised() -> None:
    assert MAX_TOOL_CALL_ROUNDS == 25


def test_structured_flashcard_results_stay_full_inside_active_tool_loop() -> None:
    flashcard_result = {
        "tool": "generate_flashcards",
        "result": "[FLASHCARDS]\nQ: One?\nA: First.\n\nQ: Two?\nA: Second.\n[/FLASHCARDS]",
    }
    assert AnthropicProvider._tool_result_for_active_loop("generate_flashcards", flashcard_result) == flashcard_result
    assert AnthropicProvider._tool_result_for_active_loop("get_recent_flashcards", {"cards": [{"question": "Q", "answer": "A"}]}) == {
        "cards": [{"question": "Q", "answer": "A"}]
    }
    compacted = AnthropicProvider._tool_result_for_active_loop("search_chunks", {"results": [{"text": "x" * 900}]})
    assert compacted != {"results": [{"text": "x" * 900}]}


class FakeResponsesStream(FakeAnthropicStream):
    async def get_final_response(self):
        return self._response


class FakeOpenAIStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class FakeResponseItem(SimpleNamespace):
    pass


def make_anthropic_provider(name='anthropic', model='claude'):
    provider = AnthropicProvider(name, api_key='token', model=model)
    return provider


def make_openai_provider(name='openai', model='gpt-4o'):
    provider = OpenAIProvider(name, api_key='token', model=model)
    return provider


def test_anthropic_extract_helpers() -> None:
    content = [
        SimpleNamespace(text='alpha'),
        {'type': 'text', 'text': 'beta'},
        SimpleNamespace(type='tool_use', id='1', name='search', input={'q': 'entropy'}),
        {'type': 'tool_use', 'id': '2', 'name': 'outline', 'input': {'doc_id': 'd1'}},
    ]
    assert AnthropicProvider._extract_text(content) == 'alpha\nbeta'
    assert AnthropicProvider._extract_tool_calls(content) == [
        {'id': '1', 'name': 'search', 'input': {'q': 'entropy'}},
        {'id': '2', 'name': 'outline', 'input': {'doc_id': 'd1'}},
    ]


@pytest.mark.asyncio
async def test_anthropic_get_models_async_success_and_fallback() -> None:
    provider = make_anthropic_provider()
    provider._client = SimpleNamespace(models=SimpleNamespace(list=lambda limit=100: FakeAsyncIterator([SimpleNamespace(id='claude-1'), SimpleNamespace(id='claude-2')])))
    assert await provider.get_models_async() == ['claude', 'claude-1', 'claude-2']

    provider._client = SimpleNamespace(models=SimpleNamespace(list=lambda limit=100: (_ for _ in ()).throw(RuntimeError('boom'))))
    assert await provider.get_models_async() == ['claude']


@pytest.mark.asyncio
async def test_anthropic_stream_chat_text_and_tool_roundtrip() -> None:
    provider = make_anthropic_provider(model='claude-3')
    text_delta = SimpleNamespace(type='content_block_delta', delta=SimpleNamespace(type='text_delta', text='hello '))
    thinking_delta = SimpleNamespace(type='content_block_delta', delta=SimpleNamespace(type='thinking_delta', thinking='hmm'))
    tool_response = SimpleNamespace(
        stop_reason='tool_use',
        content=[SimpleNamespace(type='tool_use', id='tool_1', name='search_chunks', input={'query': 'entropy'})],
    )
    final_response = SimpleNamespace(stop_reason='end_turn', content=[SimpleNamespace(text='done')])

    stream_calls = [
        FakeAnthropicStream([thinking_delta, text_delta], tool_response),
        FakeAnthropicStream([], final_response),
    ]

    class Messages:
        def stream(self, **kwargs):
            return stream_calls.pop(0)

    provider._client = SimpleNamespace(messages=Messages())
    seen_text: list[str] = []
    seen_thinking: list[str] = []
    seen_tools: list[tuple[str, dict]] = []

    async def tool_executor(name, args):
        return {'ok': True, 'name': name, 'args': args}

    result = await provider.stream_chat(
        messages=[{'role': 'user', 'content': 'hi'}],
        tools=[{'name': 'search_chunks'}],
        tool_executor=tool_executor,
        system='system',
        on_text=seen_text.append,
        on_thinking=seen_thinking.append,
        on_tool_call=lambda name, args: seen_tools.append((name, args)),
    )
    assert result == 'hello done'
    assert seen_text == ['hello ', 'done']
    assert seen_thinking == ['hmm']
    assert seen_tools == [('search_chunks', {'query': 'entropy'})]


@pytest.mark.asyncio
async def test_anthropic_stream_chat_falls_back_when_thinking_unsupported() -> None:
    provider = make_anthropic_provider(model='claude-3')
    response = SimpleNamespace(stop_reason='end_turn', content=[SimpleNamespace(text='fallback text')])
    calls = {'count': 0}

    class Messages:
        def stream(self, **kwargs):
            calls['count'] += 1
            if calls['count'] == 1:
                raise RuntimeError('thinking not supported')
            return FakeAnthropicStream([], response)

        async def create(self, **kwargs):
            return response

    provider._client = SimpleNamespace(messages=Messages())
    chunks: list[str] = []
    result = await provider.stream_chat(messages=[{'role': 'user', 'content': 'hi'}], on_text=chunks.append)
    assert result == 'fallback text'
    assert chunks == ['fallback text']


@pytest.mark.asyncio
async def test_anthropic_chat_handles_tool_use_roundtrip() -> None:
    provider = make_anthropic_provider()
    responses = [
        SimpleNamespace(stop_reason='tool_use', content=[{'type': 'tool_use', 'id': 'tool_1', 'name': 'search_chunks', 'input': {'query': 'energy'}}]),
        SimpleNamespace(stop_reason='end_turn', content=[SimpleNamespace(text='answer')]),
    ]

    class Messages:
        async def create(self, **kwargs):
            return responses.pop(0)

    provider._client = SimpleNamespace(messages=Messages())
    calls = []

    async def tool_executor(name, args):
        return {'name': name, 'args': args}

    result = await provider.chat(
        messages=[{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'hi'}],
        tools=[{'name': 'search_chunks'}],
        tool_executor=tool_executor,
        on_tool_call=lambda name, args: calls.append((name, args)),
    )
    assert result == 'answer'
    assert calls == [('search_chunks', {'query': 'energy'})]


def test_openai_helper_conversions_and_extractors(tmp_path, monkeypatch) -> None:
    provider = make_openai_provider()
    tools = [{'name': 'search_chunks', 'description': 'Search', 'input_schema': {'type': 'object'}}]
    assert provider._convert_tools_to_openai(tools)[0]['function']['name'] == 'search_chunks'
    assert provider._convert_tools_to_responses(tools)[0]['name'] == 'search_chunks'

    instructions, items = provider._build_responses_input([
        {'role': 'system', 'content': 'be helpful'},
        {'role': 'user', 'content': 'hi'},
        {'role': 'assistant', 'content': 'hello'},
        {'role': 'developer', 'content': 'internal'},
    ])
    assert instructions == 'be helpful'
    assert items[0]['role'] == 'user'
    assert items[1]['role'] == 'assistant'
    assert items[2]['role'] == 'developer'

    response = SimpleNamespace(
        output_text='',
        output=[
            SimpleNamespace(type='message', content=[SimpleNamespace(type='output_text', text='alpha')]),
            SimpleNamespace(type='function_call', call_id='call_1', name='search', arguments='{"query":"q"}'),
        ],
    )
    assert provider._extract_response_text(response) == 'alpha'
    assert provider._extract_response_tool_calls(response) == [{'call_id': 'call_1', 'name': 'search', 'arguments': '{"query":"q"}'}]

    codex_home = tmp_path / '.codex'
    codex_home.mkdir()
    (codex_home / 'models_cache.json').write_text(json.dumps({'models': [{'slug': 'gpt-5.4', 'visibility': 'list'}, {'slug': 'hidden', 'visibility': 'hidden'}]}), encoding='utf-8')
    monkeypatch.setattr(provider_module.Path, 'home', staticmethod(lambda: tmp_path))
    assert provider._codex_models_from_cache() == ['gpt-5.4']

    prompt = provider._build_codex_prompt([{'role': 'user', 'content': 'Hello'}], system='Follow rules')
    assert 'System instructions:' in prompt
    assert 'USER: Hello' in prompt


@pytest.mark.asyncio
async def test_openai_responses_paths_and_model_listing() -> None:
    provider = make_openai_provider()
    streamed_response = SimpleNamespace(id='resp_1', output=[FakeResponseItem(type='function_call', call_id='call_1', name='search', arguments='{"query":"entropy"}')], output_text='')
    final_response = SimpleNamespace(id='resp_2', output=[], output_text='final answer')
    stream_calls = [
        FakeResponsesStream([SimpleNamespace(type='response.output_text.delta', delta='partial ')], streamed_response),
        FakeResponsesStream([], final_response),
    ]

    class Responses:
        def stream(self, **kwargs):
            return stream_calls.pop(0)

        async def create(self, **kwargs):
            return final_response

    class Models:
        async def list(self):
            return SimpleNamespace(data=[SimpleNamespace(id='gpt-4o'), SimpleNamespace(id='gpt-4.1')])

    provider._client = SimpleNamespace(
        responses=Responses(),
        models=Models(),
    )
    calls = []

    async def tool_executor(name, args):
        return {'name': name, 'args': args}

    text = await provider._stream_via_responses(
        messages=[{'role': 'user', 'content': 'hi'}],
        tools=[{'name': 'search'}],
        tool_executor=tool_executor,
        system='system',
        on_text=calls.append,
        on_tool_call=lambda name, args: calls.append((name, args)),
    )
    assert text == 'partial final answer'
    assert 'partial ' in calls
    assert ('search', {'query': 'entropy'}) in calls
    assert await provider._chat_via_responses(messages=[{'role': 'user', 'content': 'hi'}]) == 'final answer'
    assert await provider.get_models_async() == ['gpt-4.1', 'gpt-4o']


@pytest.mark.asyncio
async def test_openai_stream_chat_streaming_fallback_and_chat_tool_roundtrip() -> None:
    provider = make_openai_provider(model='gpt-4o')
    delta_tool = SimpleNamespace(index=0, id='tool_1', function=SimpleNamespace(name='search', arguments='{"query":'))
    delta_tool_2 = SimpleNamespace(index=0, id='tool_1', function=SimpleNamespace(name='', arguments='"entropy"}'))
    chunks = [
        [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='hello ', reasoning_content='think', tool_calls=None))]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, reasoning_content=None, tool_calls=[delta_tool]))]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, reasoning_content=None, tool_calls=[delta_tool_2]))]),
        ],
        [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='done', reasoning_content=None, tool_calls=None))]),
        ],
    ]
    final_chat = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='done', tool_calls=[]))])
    nonstream_with_tools = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='final ', tool_calls=[SimpleNamespace(id='tool_2', function=SimpleNamespace(name='outline', arguments='{"doc_id":"d1"}'))]))])
    nonstream_terminal = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='answer', tool_calls=[]))])
    create_calls = {'count': 0}

    class ChatCompletions:
        async def create(self, **kwargs):
            if kwargs.get('stream'):
                return FakeOpenAIStream(chunks.pop(0))
            create_calls['count'] += 1
            return nonstream_with_tools if create_calls['count'] == 1 else nonstream_terminal

    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=ChatCompletions()))
    seen_text: list[str] = []
    seen_thinking: list[str] = []
    seen_tools: list[tuple[str, dict]] = []

    async def tool_executor(name, args):
        return {'name': name, 'args': args}

    streamed = await provider.stream_chat(
        messages=[{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'hi'}],
        tools=[{'name': 'search'}],
        tool_executor=tool_executor,
        on_text=seen_text.append,
        on_thinking=seen_thinking.append,
        on_tool_call=lambda name, args: seen_tools.append((name, args)),
    )
    assert streamed.startswith('hello ')
    assert seen_thinking == ['think']
    assert ('search', {'query': 'entropy'}) in seen_tools

    chatted = await provider.chat(
        messages=[{'role': 'user', 'content': 'hi'}],
        tools=[{'name': 'outline'}],
        tool_executor=tool_executor,
        on_tool_call=lambda name, args: seen_tools.append((name, args)),
    )
    assert chatted == 'final answer'
    assert ('outline', {'doc_id': 'd1'}) in seen_tools


def test_openai_extract_reasoning_text_handles_common_payload_shapes() -> None:
    provider = make_openai_provider(model='gpt-4o')
    assert provider._extract_reasoning_text('trace') == 'trace'
    assert provider._extract_reasoning_text([{'text': 'a'}, {'summary': 'b'}]) == 'ab'
    payload = SimpleNamespace(summary=[SimpleNamespace(text='c')])
    assert provider._extract_reasoning_text(payload) == 'c'


def test_compact_tool_result_truncates_large_grounding_payloads() -> None:
    payload = {
        'chunks': [
            {'chunk_id': 'c1', 'text': 'x' * 2000, 'page_number': 1},
            {'chunk_id': 'c2', 'text': 'y' * 2000, 'page_number': 2},
        ],
        'results': [{'content': 'z' * 2000}],
    }
    compacted = AnthropicProvider._compact_tool_result('search_chunks', payload)
    assert len(compacted['chunks']) == 2
    assert 'chars omitted' in compacted['chunks'][0]['text']
    assert 'chars omitted' in compacted['results'][0]['content']


@pytest.mark.asyncio
async def test_openai_provider_reports_compacted_tool_results(monkeypatch) -> None:
    provider = make_openai_provider(model='gpt-4o')
    seen_results = []

    class FakeMessage:
        content = None
        tool_calls = [
            SimpleNamespace(
                id='call_1',
                function=SimpleNamespace(name='search_chunks', arguments='{"query":"entropy"}'),
            )
        ]

    class FakeResponse:
        choices = [SimpleNamespace(message=FakeMessage())]

    async def fake_create(**kwargs):
        return FakeResponse()

    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))

    async def tool_executor(name, args):
        return {'chunks': [{'text': 'x' * 2000}]}

    result = await provider.chat(
        messages=[{'role': 'user', 'content': 'find entropy'}],
        tools=[{'name': 'search_chunks', 'input_schema': {'type': 'object'}}],
        tool_executor=tool_executor,
        on_tool_result=lambda name, payload: seen_results.append((name, payload)),
    )

    assert result == "[Max tool-call rounds exceeded]"
    assert seen_results
    assert seen_results[0][0] == 'search_chunks'
    assert 'chars omitted' in seen_results[0][1]['chunks'][0]['text']


@pytest.mark.asyncio
async def test_provider_context_window_uses_metadata_or_fallback() -> None:
    openai_provider = make_openai_provider(model='gpt-4o')

    class ModelsApi:
        async def retrieve(self, model):
            return SimpleNamespace(context_window=123456)

    openai_provider._client = SimpleNamespace(models=ModelsApi())
    assert await openai_provider.get_context_window_async() == 123456

    anthropic_provider = AnthropicProvider('anthropic', api_key='key', model='claude-sonnet-4-20250514')
    assert await anthropic_provider.get_context_window_async() == 200000


@pytest.mark.asyncio
async def test_openai_codex_helpers_and_factory(monkeypatch) -> None:
    provider = make_openai_provider(name='openai-codex', model='gpt-5.4')

    async def fake_codex(messages, system='', on_text=None):
        if on_text:
            on_text('via codex')
        return 'via codex'

    async def fake_codex_tools(messages, tools, tool_executor=None, system='', on_text=None, on_tool_call=None):
        if on_text:
            on_text('via codex tools')
        return 'via codex tools'

    monkeypatch.setattr(provider, '_run_via_codex_cli', fake_codex)
    monkeypatch.setattr(provider, '_run_via_codex_cli_tools', fake_codex_tools)
    assert await provider.stream_chat(messages=[{'role': 'user', 'content': 'hi'}], system='system') == 'via codex'
    assert await provider.stream_chat(messages=[{'role': 'user', 'content': 'hi'}], tools=[{'name': 'list_documents'}], system='system') == 'via codex tools'
    assert await provider.chat(messages=[{'role': 'user', 'content': 'hi'}]) == 'via codex'
    assert await provider.chat(messages=[{'role': 'user', 'content': 'hi'}], tools=[{'name': 'list_documents'}]) == 'via codex tools'

    created = get_provider('ollama', model='llama3.2')
    assert isinstance(created, OpenAIProvider)
    assert any(item['name'] == 'openai-codex' and item['auth_mode'] == 'codex_oauth' for item in list_providers())
    assert any(item['name'] == 'groq' and item['auth_mode'] == 'api_key' for item in list_providers())
    with pytest.raises(ValueError):
        get_provider('missing-provider')


def test_groq_provider_uses_openai_compatible_base_url(monkeypatch) -> None:
    monkeypatch.setenv('GROQ_API_KEY', 'groq-token')
    provider = get_provider('groq')
    assert isinstance(provider, OpenAIProvider)
    assert provider.name == 'groq'
    assert provider.model == 'llama-3.3-70b-versatile'
    assert provider.api_key == 'groq-token'
    assert str(provider._client.base_url) == 'https://api.groq.com/openai/v1/'



def test_openai_codex_tool_prompt_hides_internal_tool_trace() -> None:
    provider = make_openai_provider(name='openai-codex', model='gpt-5.4')
    prompt = provider._build_codex_tool_prompt(
        messages=[
            {'role': 'user', 'content': 'make flashcards'},
            {'role': 'assistant', 'content': '{"type":"tool_call","name":"internal_probe","arguments":{"topic":"units"}}'},
            {'role': 'tool', 'name': 'internal_probe', 'content': '{"cards": 10}'},
        ],
        tools=[{'name': 'generate_flashcards', 'description': 'Generate flashcards', 'input_schema': {'type': 'object'}}],
        system='system',
    )
    assert 'ASSISTANT: {"type":"tool_call","name":"internal_probe"' not in prompt
    assert 'Tool results so far (internal context, do not repeat verbatim unless useful):' in prompt
    assert 'internal_probe: {"cards": 10}' in prompt

@pytest.mark.asyncio
async def test_openai_codex_tool_loop(monkeypatch) -> None:
    provider = make_openai_provider(name='openai-codex', model='gpt-5.4')
    responses = iter([
        '{"type":"tool_call","name":"list_documents","arguments":{}}',
        '{"type":"final","content":"There is one loaded document."}',
        '{"type":"final","content":"There is one loaded document."}',
    ])
    seen_tools = []

    async def fake_prompt(prompt, on_text=None):
        return next(responses)

    async def tool_executor(name, args):
        seen_tools.append((name, args))
        return {'documents': [{'doc_id': 'bio', 'title': 'Biology'}]}

    monkeypatch.setattr(provider, '_run_codex_prompt_impl', fake_prompt)
    result = await provider._run_via_codex_cli_tools(
        messages=[{'role': 'user', 'content': 'what documents are loaded?'}],
        tools=[{'name': 'list_documents', 'description': 'List documents', 'input_schema': {'type': 'object'}}],
        tool_executor=tool_executor,
        system='system',
    )
    assert result == 'There is one loaded document.'
    assert seen_tools == [('list_documents', {})]


@pytest.mark.asyncio
async def test_openai_codex_tool_loop_retries_after_codex_cli_meta_refusal(monkeypatch) -> None:
    provider = make_openai_provider(name='openai-codex', model='gpt-5.4')
    responses = iter([
        "I can't run the Study TUI study tools in this session because I only have Codex CLI tools like shell/apply_patch.",
        '{"type":"tool_call","name":"list_documents","arguments":{}}',
        '{"type":"final","content":"I found the loaded document and can make flashcards next."}',
        '{"type":"tool_call","name":"generate_flashcards","arguments":{"topic":"thermal properties","count":10}}',
        '{"type":"final","content":"Flashcards are ready."}',
        '{"type":"final","content":"Flashcards are ready."}',
    ])
    seen_tools = []

    async def fake_prompt(prompt, on_text=None):
        return next(responses)

    async def tool_executor(name, args):
        seen_tools.append((name, args))
        if name == 'list_documents':
            return {'documents': [{'doc_id': 'keph203', 'title': 'Thermal Properties of Matter'}]}
        return {'cards': [{'question': 'Q1', 'answer': 'A1'}]}

    monkeypatch.setattr(provider, '_run_codex_prompt_impl', fake_prompt)
    result = await provider._run_via_codex_cli_tools(
        messages=[{'role': 'user', 'content': 'load keph203 and make flashcards'}],
        tools=[{'name': 'list_documents', 'description': 'List documents', 'input_schema': {'type': 'object'}}],
        tool_executor=tool_executor,
        system='system',
    )

    assert result == 'Flashcards are ready.'
    assert seen_tools == [('list_documents', {}), ('generate_flashcards', {'topic': 'thermal properties', 'count': 10})]


@pytest.mark.asyncio
async def test_openai_codex_tool_loop_recovers_from_json_final_refusal(monkeypatch) -> None:
    provider = make_openai_provider(name='openai-codex', model='gpt-5.4')
    responses = iter([
        '{"type":"tool_call","name":"list_documents","arguments":{}}',
        '{"type":"final","content":"I cannot access the Study TUI tools from this chat, so I cannot generate flashcards."}',
        '{"type":"tool_call","name":"generate_flashcards","arguments":{"topic":"thermal properties","count":8}}',
        '{"type":"final","content":"Flashcards are ready."}',
        '{"type":"final","content":"Flashcards are ready."}',
    ])
    seen_tools = []

    async def fake_prompt(prompt, on_text=None):
        return next(responses)

    async def tool_executor(name, args):
        seen_tools.append((name, args))
        if name == 'list_documents':
            return {'documents': [{'doc_id': 'keph202', 'title': 'Thermal Properties of Matter'}]}
        return {'cards': [{'question': 'Q1', 'answer': 'A1'}]}

    monkeypatch.setattr(provider, '_run_codex_prompt_impl', fake_prompt)
    result = await provider._run_via_codex_cli_tools(
        messages=[{'role': 'user', 'content': 'load keph202 and make flashcards'}],
        tools=[
            {'name': 'list_documents', 'description': 'List documents', 'input_schema': {'type': 'object'}},
            {'name': 'generate_flashcards', 'description': 'Generate flashcards', 'input_schema': {'type': 'object'}},
        ],
        tool_executor=tool_executor,
        system='system',
    )

    assert result == 'Flashcards are ready.'
    assert seen_tools == [('list_documents', {}), ('generate_flashcards', {'topic': 'thermal properties', 'count': 8})]




def test_note_and_export_tool_descriptions_guide_arguments() -> None:
    save_note = next(tool for tool in tools_module.NOTE_TOOLS if tool['name'] == 'save_note')
    export_tool = next(tool for tool in tools_module.EXPORT_TOOLS if tool['name'] == 'export_content')

    assert 'Keep formulas in raw LaTeX' in save_note['description']
    assert 'preserve formulas as LaTeX' in save_note['input_schema']['properties']['content']['description']
    assert 'For summary export, pass the final summary text in content.' in export_tool['description']
    assert 'export the user\'s saved notes instead of inventing note content' in export_tool['description']


@pytest.mark.asyncio
async def test_openai_codex_missing_cli_gives_friendly_error(monkeypatch) -> None:
    provider = make_openai_provider(name='openai-codex', model='gpt-5.4')

    async def missing_exec(*args, **kwargs):
        raise FileNotFoundError(2, 'The system cannot find the file specified')

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', missing_exec)
    with pytest.raises(RuntimeError, match='Codex CLI executable was not found'):
        await provider._run_codex_prompt_impl('hello')


@pytest.mark.asyncio
async def test_gemini_native_tool_loop_preserves_model_content(monkeypatch) -> None:
    provider = make_openai_provider(name='gemini', model='models/gemini-2.5-flash')
    assert provider.api_key == 'token'
    seen_contents = []
    responses = iter([
        {
            'candidates': [
                {
                    'content': {
                        'role': 'model',
                        'parts': [
                            {
                                'functionCall': {
                                    'name': 'list_available_files',
                                    'args': {'filter': 'keph202'},
                                    'thoughtSignature': 'sig-123',
                                }
                            }
                        ],
                    }
                }
            ]
        },
        {
            'candidates': [
                {
                    'content': {
                        'role': 'model',
                        'parts': [{'text': 'Flashcards ready.'}],
                    }
                }
            ]
        },
    ])

    async def fake_generate(*, contents, tools=None, system='', max_tokens=4096):
        seen_contents.append(json.loads(json.dumps(contents)))
        return next(responses)

    async def tool_executor(name, args):
        assert name == 'list_available_files'
        return {'files': [{'relative_path': 'keph202.pdf', 'name': 'keph202.pdf'}]}

    monkeypatch.setattr(provider, '_gemini_generate_content', fake_generate)
    result = await provider.chat(
        messages=[{'role': 'user', 'content': 'load keph202 and make flashcards'}],
        tools=[{'name': 'list_available_files', 'description': 'List files', 'input_schema': {'type': 'object'}}],
        tool_executor=tool_executor,
        system='system',
    )

    assert result == 'Flashcards ready.'
    assert len(seen_contents) == 2
    assert seen_contents[1][1]['parts'][0]['functionCall']['thoughtSignature'] == 'sig-123'
    assert seen_contents[1][2]['parts'][0]['functionResponse']['name'] == 'list_available_files'


def test_normalize_gemini_model_name_strips_models_prefix() -> None:
    provider = make_openai_provider(name='gemini', model='models/gemini-2.5-flash')
    assert provider._normalize_gemini_model_name(provider.model) == 'gemini-2.5-flash'
