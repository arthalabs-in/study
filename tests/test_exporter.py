from __future__ import annotations

import types
from pathlib import Path

import pytest

import src.exporter as exporter_module
from src.exporter import _sanitize_spreadsheet_cell, export_chat, export_flashcards, export_summary


def test_sanitize_spreadsheet_cell_prefixes() -> None:
    assert _sanitize_spreadsheet_cell('=SUM(A1:A2)') == "'=SUM(A1:A2)"
    assert _sanitize_spreadsheet_cell('+cmd') == "'+cmd"
    assert _sanitize_spreadsheet_cell('-danger') == "'-danger"
    assert _sanitize_spreadsheet_cell('@formula') == "'@formula"
    assert _sanitize_spreadsheet_cell('plain text') == 'plain text'


def test_export_flashcards_csv_sanitizes_formula_cells(tmp_path: Path) -> None:
    result = export_flashcards(
        [
            {'question': '=2+2', 'answer': '+answer'},
            {'question': 'normal', 'answer': '@risky'},
        ],
        fmt='csv',
        export_dir=str(tmp_path),
    )

    exported = Path(result['exported'])
    content = exported.read_text(encoding='utf-8')
    assert "'=2+2" in content
    assert "'+answer" in content
    assert "'@risky" in content


def test_export_flashcards_anki_returns_clean_error_when_genanki_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(exporter_module.sys.modules, "genanki", None)
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "genanki":
            raise ImportError("missing genanki")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    result = export_flashcards(
        [
            {'question': 'Front', 'answer': '=Back'},
        ],
        fmt='anki',
        export_dir=str(tmp_path),
    )
    assert "genanki" in result["error"]


def test_export_flashcards_anki_writes_apkg_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeNote:
        def __init__(self, model, fields):
            self.model = model
            self.fields = fields

    class FakeModel:
        def __init__(self, model_id, name, fields, templates):
            self.model_id = model_id
            self.name = name
            self.fields = fields
            self.templates = templates

    class FakeDeck:
        def __init__(self, deck_id, name):
            self.deck_id = deck_id
            self.name = name
            self.notes = []

        def add_note(self, note):
            self.notes.append(note)

    class FakePackage:
        def __init__(self, deck):
            self.deck = deck

        def write_to_file(self, path):
            Path(path).write_bytes(b"fake-apkg")

    fake_genanki = types.SimpleNamespace(
        Model=FakeModel,
        Deck=FakeDeck,
        Note=FakeNote,
        Package=FakePackage,
    )
    monkeypatch.setitem(exporter_module.sys.modules, "genanki", fake_genanki)

    result = export_flashcards(
        [
            {'question': 'Front', 'answer': 'Back'},
        ],
        fmt='anki',
        export_dir=str(tmp_path),
    )

    exported = Path(result['exported'])
    assert exported.suffix == '.apkg'
    assert exported.read_bytes() == b"fake-apkg"
    assert result['format'] == 'anki'


def test_export_summary_and_chat_round_trip(tmp_path: Path) -> None:
    summary = export_summary('Important summary', title='Exam Notes', export_dir=str(tmp_path))
    chat = export_chat(
        [
            {'role': 'user', 'content': 'hello'},
            {'role': 'assistant', 'content': 'world'},
        ],
        export_dir=str(tmp_path),
    )

    assert Path(summary['exported']).read_text(encoding='utf-8').startswith('# Exam Notes')
    chat_text = Path(chat['exported']).read_text(encoding='utf-8')
    assert '### You' in chat_text
    assert '### Assistant' in chat_text
