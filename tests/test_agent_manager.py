from __future__ import annotations

from pathlib import Path

import pytest

from src.agents.agent_manager import AgentManager
from src.notes import NotesManager
from src.parsers.doc_store import DocStore
from src.study_progress import StudyProgressManager


class DummyProvider:
    async def chat(self, *args, **kwargs):
        return 'ok'


@pytest.mark.asyncio
async def test_save_note_requires_approval(tmp_path: Path) -> None:
    notes_manager = NotesManager(tmp_path / 'notes.db')
    approvals: list[tuple[str, dict]] = []

    async def deny(tool_name: str, args: dict) -> bool:
        approvals.append((tool_name, args))
        return False

    manager = AgentManager(
        doc_store=DocStore(),
        provider=DummyProvider(),
        notes_manager=notes_manager,
        request_tool_approval=deny,
    )

    result = await manager.execute_tool('save_note', {'title': 'A', 'content': 'B'})
    assert result['status'] == 'denied'
    assert approvals[0][0] == 'save_note'
    assert notes_manager.list_notes() == []


@pytest.mark.asyncio
async def test_animate_concept_requires_approval() -> None:
    approvals: list[tuple[str, dict]] = []

    async def deny(tool_name: str, args: dict) -> bool:
        approvals.append((tool_name, args))
        return False

    manager = AgentManager(
        doc_store=DocStore(),
        provider=DummyProvider(),
        request_tool_approval=deny,
    )

    result = await manager.execute_tool(
        'animate_concept',
        {'topic': 'Sine wave', 'code': 'from manim import *\nclass SineScene(Scene):\n    def construct(self):\n        self.wait()'},
    )
    assert result['status'] == 'denied'
    assert approvals[0][0] == 'animate_concept'


@pytest.mark.asyncio
async def test_load_file_stays_inside_documents_dir(documents_dir: Path) -> None:
    nested = documents_dir / 'nested'
    nested.mkdir()
    pdf_path = nested / 'study.pdf'
    pdf_path.write_bytes(b'%PDF-1.7 test')
    loaded: list[str] = []

    async def loader(path: str) -> None:
        loaded.append(path)

    manager = AgentManager(
        doc_store=DocStore(),
        provider=DummyProvider(),
        documents_dir=documents_dir,
        file_loader=loader,
    )

    result = await manager.execute_tool('load_file', {'file_path': 'nested/study.pdf'})
    assert result['success'] is True
    assert loaded == [str(pdf_path.resolve())]

    escape = await manager.execute_tool('load_file', {'file_path': '../outside.pdf'})
    assert 'escapes the configured documents folder' in escape['error']

    absolute = await manager.execute_tool('load_file', {'file_path': str(pdf_path.resolve())})
    assert ('relative_path returned by list_available_files' in absolute['error'] or 'UNC paths are not allowed' in absolute['error'])


def test_list_available_files_uses_relative_paths(documents_dir: Path) -> None:
    (documents_dir / 'chapter.pdf').write_bytes(b'%PDF-1.7 test')
    manager = AgentManager(doc_store=DocStore(), provider=DummyProvider(), documents_dir=documents_dir)
    results = manager.execute_tool
    listed = manager._list_available_files()
    assert listed['count'] == 1
    assert listed['files'][0]['relative_path'] == 'chapter.pdf'


@pytest.mark.asyncio
async def test_progress_tools_round_trip(tmp_path: Path) -> None:
    progress = StudyProgressManager(tmp_path / "progress.db")
    progress.upsert_source(
        source_hash="hash123",
        doc_id="doc1",
        title="Kinematics",
        path=str(tmp_path / "kinematics.pdf"),
    )
    manager = AgentManager(
        doc_store=DocStore(),
        provider=DummyProvider(),
        progress_manager=progress,
        source_hash_resolver=lambda doc_id: "hash123" if doc_id == "doc1" else None,
    )

    saved = await manager.execute_tool(
        "save_progress_note",
        {
            "doc_id": "doc1",
            "note": "The user understands average speed but not instantaneous velocity.",
            "weak_topics": ["instantaneous velocity"],
            "strong_topics": ["average speed"],
            "grasp_level": 0.55,
        },
    )
    assert saved["status"] == "saved"

    loaded = await manager.execute_tool("get_study_progress", {"doc_id": "doc1"})
    assert loaded["doc_id"] == "doc1"
    assert loaded["weak_topics"] == ["instantaneous velocity"]
    assert loaded["recent_progress_notes"][0]["author"] == "agent"
