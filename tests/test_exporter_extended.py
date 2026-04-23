"""Tests for exporter extended behavior (cloze, tags, source refs)."""

import pytest
import tempfile
from pathlib import Path

from src.exporter import export_flashcards


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


class TestMarkdownExport:
    def test_basic_cards(self, tmp_dir):
        cards = [{"question": "Q1", "answer": "A1"}]
        result = export_flashcards(cards, fmt="markdown", export_dir=tmp_dir)
        assert "error" not in result
        text = Path(result["exported"]).read_text(encoding="utf-8")
        assert "Q1" in text
        assert "A1" in text

    def test_cloze_cards(self, tmp_dir):
        cards = [{"question": "Q", "answer": "A", "card_type": "cloze", "cloze_text": "{{c1::x}}"}]
        result = export_flashcards(cards, fmt="markdown", export_dir=tmp_dir)
        text = Path(result["exported"]).read_text(encoding="utf-8")
        assert "Cloze:" in text
        assert "{{c1::x}}" in text

    def test_tags_and_refs(self, tmp_dir):
        cards = [{"question": "Q", "answer": "A", "tags": ["physics"], "source_refs": [{"doc_id": "d1", "page": 2}]}]
        result = export_flashcards(cards, fmt="markdown", export_dir=tmp_dir, include_source_refs=True)
        text = Path(result["exported"]).read_text(encoding="utf-8")
        assert "physics" in text
        assert "d1 p2" in text


class TestCsvExport:
    def test_basic(self, tmp_dir):
        cards = [{"question": "Q1", "answer": "A1"}]
        result = export_flashcards(cards, fmt="csv", export_dir=tmp_dir)
        assert "error" not in result
        text = Path(result["exported"]).read_text(encoding="utf-8")
        assert "Q1" in text
        assert "basic" in text

    def test_cloze(self, tmp_dir):
        cards = [{"question": "Q", "answer": "A", "card_type": "cloze", "cloze_text": "{{c1::x}}"}]
        result = export_flashcards(cards, fmt="csv", export_dir=tmp_dir)
        text = Path(result["exported"]).read_text(encoding="utf-8")
        assert "cloze" in text
        assert "{{c1::x}}" in text


class TestAnkiExport:
    def test_basic_apkg(self, tmp_dir):
        pytest.importorskip("genanki")
        cards = [{"question": "Q1", "answer": "A1"}]
        result = export_flashcards(cards, fmt="anki", export_dir=tmp_dir, deck_name="TestDeck")
        assert "error" not in result
        assert Path(result["exported"]).exists()
        assert result["deck"] == "TestDeck"

    def test_cloze_apkg(self, tmp_dir):
        pytest.importorskip("genanki")
        cards = [{"question": "Q", "answer": "A", "card_type": "cloze", "cloze_text": "{{c1::x}}"}]
        result = export_flashcards(cards, fmt="anki", export_dir=tmp_dir, note_type="cloze")
        assert "error" not in result
        assert Path(result["exported"]).exists()

    def test_missing_genanki_graceful(self, tmp_dir, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "genanki", None)
        cards = [{"question": "Q", "answer": "A"}]
        result = export_flashcards(cards, fmt="anki", export_dir=tmp_dir)
        assert "error" in result


class TestHostileExporter:
    def test_empty_cards_returns_error(self, tmp_dir):
        result = export_flashcards([], fmt="markdown", export_dir=tmp_dir)
        assert "error" in result

    def test_csv_formula_injection_sanitized(self, tmp_dir):
        cards = [
            {"question": "=2+2", "answer": "+cmd", "card_type": "basic"},
            {"question": "Q", "answer": "A", "card_type": "cloze", "cloze_text": "={{c1::x}}"},
        ]
        result = export_flashcards(cards, fmt="csv", export_dir=tmp_dir)
        text = Path(result["exported"]).read_text(encoding="utf-8")
        assert "'=2+2" in text
        assert "'+cmd" in text
        assert "'={{c1::x}}" in text

    def test_csv_formula_injection_sanitizes_export_tags(self, tmp_dir):
        cards = [{"question": "Q", "answer": "A"}]

        result = export_flashcards(cards, fmt="csv", export_dir=tmp_dir, tags=["=cmd"])

        text = Path(result["exported"]).read_text(encoding="utf-8")
        assert "'=cmd" in text

    def test_very_large_deck_name(self, tmp_dir):
        cards = [{"question": "Q", "answer": "A"}]
        long_name = "Deck" * 500
        result = export_flashcards(cards, fmt="markdown", export_dir=tmp_dir, deck_name=long_name)
        assert "error" not in result

    def test_mixed_card_types_in_single_export(self, tmp_dir):
        pytest.importorskip("genanki")
        cards = [
            {"question": "Q1", "answer": "A1", "card_type": "basic"},
            {"question": "Q2", "answer": "A2", "card_type": "cloze", "cloze_text": "{{c1::x}}"},
        ]
        result = export_flashcards(cards, fmt="anki", export_dir=tmp_dir, note_type="mixed")
        assert "error" not in result
        assert Path(result["exported"]).exists()

    def test_source_refs_with_missing_fields(self, tmp_dir):
        cards = [{"question": "Q", "answer": "A", "source_refs": [{}, {"page": None}, {"doc_id": ""}]}]
        result = export_flashcards(cards, fmt="markdown", export_dir=tmp_dir, include_source_refs=True)
        text = Path(result["exported"]).read_text(encoding="utf-8")
        assert "Q" in text
