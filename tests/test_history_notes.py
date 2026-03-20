from __future__ import annotations

from pathlib import Path

from src.chat_history import ChatHistoryManager
from src.notes import NotesManager


def test_chat_history_round_trip(tmp_path: Path) -> None:
    manager = ChatHistoryManager(tmp_path / 'history.db')
    session_id = manager.new_session()
    manager.save_message('user', 'What is entropy?')
    manager.save_message('assistant', 'A measure of disorder.')

    latest = manager.load_latest()
    assert latest is not None
    messages, latest_session_id = latest
    assert latest_session_id == session_id
    assert messages == [
        {'role': 'user', 'content': 'What is entropy?'},
        {'role': 'assistant', 'content': 'A measure of disorder.'},
    ]

    sessions = manager.list_sessions()
    assert sessions[0]['title'] == 'What is entropy?'
    assert manager.search_messages('disorder')[0]['role'] == 'assistant'
    assert manager.delete_session(session_id) is True


def test_notes_search_and_markdown_export(tmp_path: Path) -> None:
    manager = NotesManager(tmp_path / 'notes.db')
    saved = manager.save_note(
        title='Thermodynamics',
        content='Entropy tends to increase.',
        doc_id='chapter_1',
        page=12,
        tags=['physics', 'entropy'],
    )

    note = manager.get_note(saved['id'])
    assert note is not None
    assert note['title'] == 'Thermodynamics'
    assert manager.search_notes('increase')[0]['id'] == saved['id']

    export_dir = tmp_path / 'exports'
    result = manager.export_notes_markdown(path=str(export_dir))
    exported = Path(result['exported']).read_text(encoding='utf-8')
    assert 'Thermodynamics' in exported
    assert 'Entropy tends to increase.' in exported
