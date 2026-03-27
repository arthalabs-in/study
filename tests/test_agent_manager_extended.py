from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.agents.agent_manager as agent_manager_module
from src.agents.agent_manager import AgentManager
from src.agents.tools import NOTE_TOOLS
from src.manim_renderer import RenderResult
from src.notes import NotesManager
from src.parsers.doc_store import Chunk, Document, DocStore
from src.parsers.pdf_parser import ImageInfo


class DummyProvider:
    def __init__(self, result='ok', should_fail=False):
        self.result = result
        self.should_fail = should_fail
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.should_fail:
            raise RuntimeError('provider failed')
        return self.result


@pytest.fixture()
def populated_store() -> DocStore:
    store = DocStore()
    store.add_document(Document(
        id='doc1',
        title='Physics',
        path='physics.pdf',
        total_pages=2,
        chunks=[
            Chunk(id='doc1_c0', doc_id='doc1', page=1, text='entropy text', summary='entropy summary'),
            Chunk(id='doc1_c1', doc_id='doc1', page=2, text='momentum text', summary='momentum summary'),
        ],
        images=[ImageInfo(id='doc1_img1', doc_id='doc1', page=1, path='', width=800, height=600, size_bytes=2048, figure_count=1)],
    ))
    return store


@pytest.mark.asyncio
async def test_execute_tool_document_and_notes_and_pomodoro_branches(tmp_path, populated_store) -> None:
    provider = DummyProvider()
    manager = AgentManager(
        doc_store=populated_store,
        provider=provider,
        notes_manager=NotesManager(tmp_path / 'notes.db'),
    )

    assert (await manager.execute_tool('search_chunks', {'query': 'entropy', 'top_k': '2'}))[0]['doc_id'] == 'doc1'
    assert (await manager.execute_tool('get_chunk_by_id', {'chunk_id': 'doc1_c0'}))['text'] == 'entropy text'
    assert 'Chunk not found' in (await manager.execute_tool('get_chunk_by_id', {'chunk_id': 'missing'}))['error']
    assert len(await manager.execute_tool('get_chunks_by_page', {'doc_id': 'doc1', 'page_number': 2})) == 1
    assert (await manager.execute_tool('list_documents', {}))[0]['doc_id'] == 'doc1'
    assert (await manager.execute_tool('get_document_outline', {'doc_id': 'doc1'}))[0]['summary'] == 'entropy summary'
    assert (await manager.execute_tool('get_document_images', {'doc_id': 'doc1'}))[0]['page'] == 1

    note = await manager.execute_tool('save_note', {'title': 'Entropy', 'content': 'Disorder'})
    assert note['status'] == 'denied'
    assert (await manager.execute_tool('list_notes', {})) == []
    assert (await manager.execute_tool('search_notes', {'query': 'entropy'})) == []

    started = await manager.execute_tool('pomodoro_start', {'work_mins': 1})
    assert started['status'] == 'working'
    assert (await manager.execute_tool('pomodoro_status', {}))['status'] in {'working', 'short_break', 'long_break'}
    stopped = await manager.execute_tool('pomodoro_stop', {})
    assert stopped['status'] in {'stopped', 'idle'}


@pytest.mark.asyncio
async def test_page_image_subagent_and_study_tool_paths(tmp_path, populated_store, monkeypatch) -> None:
    image_path = tmp_path / 'page.jpg'
    image_bytes = b'jpeg-data'
    image_path.write_bytes(image_bytes)
    populated_store.documents['doc1'].images = [SimpleNamespace(id='doc1_p1', doc_id='doc1', page=1, path=str(image_path), figure_count=2)]
    populated_store.get_page_image = lambda doc_id, page_number: {'page': 1, 'path': str(image_path), 'figure_count': 2}

    provider = DummyProvider(result='study-result')
    manager = AgentManager(doc_store=populated_store, provider=provider)

    page = await manager.execute_tool('get_page_image', {'doc_id': 'doc1', 'page_number': 1})
    assert page['figure_count'] == 2
    assert base64.b64decode(page['base64_data']) == image_bytes

    populated_store.get_page_image = lambda doc_id, page_number: None
    assert 'Could not render page' in (await manager.execute_tool('get_page_image', {'doc_id': 'doc1', 'page_number': 99}))['error']

    result = await manager.execute_tool('spawn_subagent', {'task': 'Summarize entropy', 'context': 'physics'})
    assert result['success'] is True
    assert result['result'] == 'study-result'

    provider.should_fail = True
    failed = await manager.execute_tool('spawn_subagent', {'task': 'Broken task'})
    assert failed['success'] is False

    provider.should_fail = False
    summary = await manager.execute_tool('summarize_document', {'doc_id': 'doc1'})
    quiz = await manager.execute_tool('generate_quiz', {'topic': 'entropy', 'difficulty': 'hard', 'count': 3})
    cards = await manager.execute_tool('generate_flashcards', {'topic': 'entropy', 'count': 2})
    assert summary['result'] == 'study-result'
    assert quiz['tool'] == 'generate_quiz'
    assert cards['tool'] == 'generate_flashcards'
    assert len(provider.calls) >= 4


@pytest.mark.asyncio
async def test_web_search_export_and_status_helpers(tmp_path, monkeypatch, populated_store) -> None:
    provider = DummyProvider()
    manager = AgentManager(doc_store=populated_store, provider=provider, allow_web_tools=False, chat_history_ref=[{'role': 'user', 'content': 'hello'}])
    statuses = []
    manager.on_status = statuses.append

    disabled = await manager.execute_tool('web_search', {'query': 'entropy'})
    assert 'disabled' in disabled['error']

    manager.allow_web_tools = True
    monkeypatch.setattr(agent_manager_module, 'web_search', lambda query, max_results: {'query': query, 'max_results': max_results})
    enabled = await manager.execute_tool('web_search', {'query': 'entropy', 'max_results': 50})
    assert enabled == {'query': 'entropy', 'max_results': 10}

    monkeypatch.setattr(agent_manager_module, 'export_flashcards', lambda cards, fmt='markdown', export_dir=None: {'cards': len(cards), 'fmt': fmt, 'export_dir': export_dir})
    monkeypatch.setattr(agent_manager_module, 'export_summary', lambda content, export_dir=None: {'summary': content})
    monkeypatch.setattr(agent_manager_module, 'export_chat', lambda msgs, export_dir=None: {'messages': len(msgs)})
    manager.notes_manager.export_notes_markdown = lambda path=None: {'notes': 'md', 'path': path}
    manager.notes_manager.export_notes_pdf = lambda path=None, note_id=None: {'notes': 'pdf', 'path': path, 'note_id': note_id}

    denied = await manager.execute_tool('export_content', {'type': 'summary', 'content': 'hello'})
    assert denied['status'] == 'denied'

    manager.request_tool_approval = lambda name, args: __import__('asyncio').sleep(0, result=True)
    exported_cards = await manager.execute_tool('export_content', {'type': 'flashcards', 'format': 'csv', 'cards': [{'question': 'Q', 'answer': 'A'}]})
    assert exported_cards['cards'] == 1 and exported_cards['fmt'] == 'csv'
    assert (await manager.execute_tool('export_content', {'type': 'notes'})) == {'notes': 'md', 'path': None}
    assert (await manager.execute_tool('export_content', {'type': 'notes_pdf'})) == {'notes': 'pdf', 'path': None, 'note_id': None}
    assert (await manager.execute_tool('export_content', {'type': 'notes_pdf', 'note_id': 7})) == {'notes': 'pdf', 'path': None, 'note_id': 7}
    assert (await manager.execute_tool('export_content', {'type': 'summary', 'content': 'hello'})) == {'summary': 'hello'}
    assert (await manager.execute_tool('export_content', {'type': 'chat'})) == {'messages': 1}
    assert 'Unknown export type' in (await manager.execute_tool('export_content', {'type': 'mystery'}))['error']

    assert manager._tool_status_message('save_note', {'title': 'Entropy'}) .startswith('📝')
    assert manager._tool_status_message('export_content', {'type': 'chat', 'format': 'markdown', 'content': 'abc'}) .startswith('💾')
    assert manager._tool_status_message('search_chunks', {'query': 'x'}) == 'Searching documents for "x"...'
    assert manager._tool_status_message('list_documents', {}) == 'Listing loaded documents...'
    assert manager._tool_status_message('generate_flashcards', {'topic': 'keph101 main concepts', 'count': 10}) == 'Creating 10 flashcards on "keph101 main concepts"...'
    assert manager._truncate('abcdef', 5) == 'ab...'
    assert manager._clamp_int('7', default=1, minimum=2, maximum=5) == 5
    assert any('Searching the web' in status for status in statuses)

    unknown = await manager.execute_tool('save_progress_notes', {'note': 'remember this'})
    assert unknown['attempted_tool'] == 'save_progress_notes'
    assert 'save_progress_note' in unknown['closest_tools']
    assert 'save_progress_note' in unknown['available_tools']
    assert 'Closest matches:' in unknown['error']
    assert 'Available tools:' in unknown['error']


@pytest.mark.asyncio
async def test_notes_pdf_export_can_deliver_to_calibre_and_zotero(tmp_path, monkeypatch, populated_store) -> None:
    provider = DummyProvider()
    manager = AgentManager(doc_store=populated_store, provider=provider, notes_manager=NotesManager(tmp_path / 'notes.db'))
    manager.request_tool_approval = lambda name, args: __import__('asyncio').sleep(0, result=True)

    exported_pdf = tmp_path / "exports" / "notes.pdf"
    exported_pdf.parent.mkdir(parents=True, exist_ok=True)
    exported_pdf.write_text("pdf", encoding="utf-8")
    manager.notes_manager.export_notes_pdf = lambda path=None, note_id=None: {'exported': str(exported_pdf), 'count': 1, 'format': 'pdf'}

    monkeypatch.setattr(manager, "_resolve_calibre_library", lambda: tmp_path)
    monkeypatch.setattr(agent_manager_module.calibre_client, "attach_exported_pdf", lambda library_path, book_id, pdf_path: {'status': 'attached', 'book_id': book_id})
    calibre_result = await manager.execute_tool(
        'export_content',
        {'type': 'notes_pdf', 'destination': 'calibre', 'calibre_book_id': 42},
    )
    assert calibre_result['delivery'] == 'calibre'
    assert calibre_result['calibre']['book_id'] == 42

    monkeypatch.setattr(agent_manager_module.zotero_client, "attach_exported_pdf", lambda item_key, pdf_path: {'status': 'attached', 'item_key': item_key})
    zotero_result = await manager.execute_tool(
        'export_content',
        {'type': 'notes_pdf', 'destination': 'zotero', 'zotero_item_key': 'ABCD1234'},
    )
    assert zotero_result['delivery'] == 'zotero'
    assert zotero_result['zotero']['item_key'] == 'ABCD1234'


def test_note_tool_filters_allow_null_values_in_schema() -> None:
    list_notes_schema = next(tool for tool in NOTE_TOOLS if tool["name"] == "list_notes")["input_schema"]["properties"]
    assert "null" in list_notes_schema["doc_id"]["type"]
    assert "null" in list_notes_schema["tag"]["type"]


@pytest.mark.asyncio
async def test_flashcards_export_uses_last_generated_cards_and_documents_dir(tmp_path) -> None:
    store = DocStore()
    provider = DummyProvider()
    manager = AgentManager(
        doc_store=store,
        provider=provider,
        documents_dir=tmp_path,
        flashcards_ref=[{'question': 'Q1', 'answer': 'A1'}],
    )
    manager.request_tool_approval = lambda name, args: __import__('asyncio').sleep(0, result=True)

    result = await manager.execute_tool('export_content', {'type': 'flashcards', 'format': 'csv', 'destination': 'documents_dir'})
    assert result['count'] == 1
    assert result['exported'].endswith('.csv')
    assert str(tmp_path) in result['exported']


@pytest.mark.asyncio
async def test_get_recent_flashcards_returns_latest_session_cards() -> None:
    manager = AgentManager(
        doc_store=DocStore(),
        provider=DummyProvider(),
        flashcards_ref=[
            {'question': 'Q1', 'answer': 'A1'},
            {'question': 'Q2', 'answer': 'A2'},
        ],
    )

    result = await manager.execute_tool('get_recent_flashcards', {'limit': 1})
    assert result['count'] == 1
    assert result['total_count'] == 2
    assert result['cards'][0]['question'] == 'Q1'


@pytest.mark.asyncio
async def test_flashcards_anki_export_uses_last_generated_cards(tmp_path) -> None:
    store = DocStore()
    provider = DummyProvider()
    manager = AgentManager(
        doc_store=store,
        provider=provider,
        documents_dir=tmp_path,
        flashcards_ref=[{'question': 'Q1', 'answer': 'A1'}],
    )
    manager.request_tool_approval = lambda name, args: __import__('asyncio').sleep(0, result=True)
    original_export_flashcards = agent_manager_module.export_flashcards
    agent_manager_module.export_flashcards = lambda cards, fmt='markdown', export_dir=None: {
        'count': len(cards),
        'format': fmt,
        'exported': str(Path(export_dir or tmp_path) / 'flashcards.apkg'),
    }

    try:
        result = await manager.execute_tool('export_content', {'type': 'flashcards', 'format': 'anki'})
        assert result['count'] == 1
        assert result['format'] == 'anki'
        assert result['exported'].endswith('.apkg')
    finally:
        agent_manager_module.export_flashcards = original_export_flashcards


@pytest.mark.asyncio
async def test_calibre_and_zotero_validations(monkeypatch, tmp_path) -> None:
    store = DocStore()
    provider = DummyProvider()
    manager = AgentManager(doc_store=store, provider=provider)

    assert (await manager.execute_tool('calibre_load', {'book_id': 0}))['error'] == 'Calibre book ID must be a positive integer.'

    monkeypatch.setattr(agent_manager_module.zotero_client, 'is_available', lambda: True)
    invalid = await manager.execute_tool('zotero_load', {'item_key': '../bad'})
    assert invalid['error'] == 'Invalid Zotero item key.'


@pytest.mark.asyncio
async def test_animate_concept_handles_missing_manim(monkeypatch) -> None:
    manager = AgentManager(doc_store=DocStore(), provider=DummyProvider())
    manager.request_tool_approval = lambda name, args: __import__('asyncio').sleep(0, result=True)
    monkeypatch.setattr(
        agent_manager_module,
        "get_animation_dependency_error",
        lambda: "A LaTeX engine is required for animations. Install LaTeX (latex, pdflatex, xelatex, or lualatex).",
    )

    result = await manager.execute_tool(
        'animate_concept',
        {'topic': 'sine wave', 'code': 'from manim import *\nclass DemoScene(Scene):\n    def construct(self):\n        self.wait()'},
    )

    assert result['status'] == 'error'
    assert result['retryable'] is False
    assert 'LaTeX engine is required' in result['error']


@pytest.mark.asyncio
async def test_animate_concept_success_and_failure_shapes(monkeypatch, tmp_path) -> None:
    manager = AgentManager(doc_store=DocStore(), provider=DummyProvider(), default_export_dir=tmp_path)
    manager.request_tool_approval = lambda name, args: __import__('asyncio').sleep(0, result=True)
    monkeypatch.setattr(agent_manager_module, 'get_animation_dependency_error', lambda: None)

    async def fake_render_success(code, *, export_dir=None, quality='low', timeout=120):
        return RenderResult(
            success=True,
            video_path=str(tmp_path / 'demo.mp4'),
            code_path=str(tmp_path / 'demo.py'),
            scene_name='DemoScene',
            duration_seconds=1.25,
        )

    monkeypatch.setattr(agent_manager_module, 'render_animation', fake_render_success)
    success = await manager.execute_tool(
        'animate_concept',
        {
            'topic': 'vector addition',
            'code': 'from manim import *\nclass DemoScene(Scene):\n    def construct(self):\n        self.wait()',
            'quality': 'medium',
        },
    )
    assert success['status'] == 'success'
    assert success['scene_name'] == 'DemoScene'
    assert success['video_path'].endswith('demo.mp4')

    async def fake_render_failure(code, *, export_dir=None, quality='low', timeout=120):
        return RenderResult(
            success=False,
            error='Render error: bad mobject',
            stderr='Traceback: bad mobject',
            code_path=str(tmp_path / 'broken.py'),
            scene_name='BrokenScene',
            duration_seconds=0.4,
        )

    monkeypatch.setattr(agent_manager_module, 'render_animation', fake_render_failure)
    failure = await manager.execute_tool(
        'animate_concept',
        {
            'topic': 'vector addition',
            'code': 'from manim import *\nclass BrokenScene(Scene):\n    def construct(self):\n        self.wait()',
            'attempt': 2,
        },
    )
    assert failure['status'] == 'error'
    assert failure['retryable'] is True
    assert failure['attempt'] == 2
    assert failure['code_path'].endswith('broken.py')


