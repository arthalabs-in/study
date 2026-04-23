"""Tests for anki_client.py."""

from src.anki_client import AnkiClient, _render_source_refs, _anki_model_name, _duplicate_search_query


class TestAnkiModelName:
    def test_basic(self):
        assert _anki_model_name("basic") == "Basic"

    def test_cloze(self):
        assert _anki_model_name("cloze") == "Cloze"


class TestRenderSourceRefs:
    def test_empty(self):
        assert _render_source_refs([]) == ""

    def test_renders(self):
        refs = [{"doc_id": "doc1", "page": 2, "chunk_id": "c1"}]
        assert _render_source_refs(refs) == "doc1 p2 c1"


class TestAnkiClient:
    def test_is_available_false_when_no_anki(self):
        client = AnkiClient(endpoint="http://127.0.0.1:9999")
        assert client.is_available(timeout=0.5) is False

    def test_create_deck_returns_error_when_unavailable(self):
        client = AnkiClient(endpoint="http://127.0.0.1:9999")
        result = client.create_deck("test")
        assert "error" in result

    def test_add_or_update_notes_no_cards(self):
        client = AnkiClient(endpoint="http://127.0.0.1:9999")
        result = client.add_or_update_notes(cards=[], deck_name="test", note_type="basic")
        assert "error" in result

    def test_find_duplicates_empty(self):
        client = AnkiClient(endpoint="http://127.0.0.1:9999")
        result = client.find_duplicates([], "test")
        assert result["count"] == 0

    def test_duplicate_search_query_uses_text_for_cloze(self):
        query = _duplicate_search_query(
            "Study TUI",
            "cloze",
            {"Text": "The capital is {{c1::Paris}}."},
        )
        assert 'deck:"Study TUI"' in query
        assert 'text:"The capital is {{c1::Paris}}."' in query

    def test_add_or_update_notes_uses_cloze_query_field(self):
        client = AnkiClient(endpoint="http://127.0.0.1:9999")
        seen_queries: list[str] = []
        invoked: list[tuple[str, dict]] = []

        client.find_notes = lambda query: seen_queries.append(query) or [123]
        client.invoke = lambda action, **params: invoked.append((action, params)) or None

        result = client.add_or_update_notes(
            cards=[{"card_type": "cloze", "cloze_text": "The capital is {{c1::Paris}}.", "answer": "Paris"}],
            deck_name="Study TUI",
            note_type="cloze",
        )

        assert result["updated"] == 1
        assert seen_queries == ['deck:"Study TUI" text:"The capital is {{c1::Paris}}."']
        assert invoked[0][0] == "updateNoteFields"


class TestHostileAnkiClient:
    def test_malformed_endpoint_graceful(self):
        client = AnkiClient(endpoint="not_a_valid_url")
        assert client.is_available(timeout=0.1) is False

    def test_add_notes_with_malformed_cards(self):
        client = AnkiClient(endpoint="http://127.0.0.1:9999")
        result = client.add_or_update_notes(
            cards=[
                {"question": "", "answer": ""},  # empty fields
                {"question": "Q", "answer": "A", "cloze_text": "{{c1::x}}"},  # cloze without card_type
            ],
            deck_name="test",
            note_type="basic",
        )
        # Should not crash even if AnkiConnect is unavailable
        assert "error" in result or result.get("status") == "ok"

    def test_anki_model_name_unknown_defaults_to_basic(self):
        assert _anki_model_name("unknown_type") == "Basic"

    def test_render_source_refs_with_malformed_data(self):
        assert _render_source_refs(["not_a_dict", None, {}]) == ""
        assert _render_source_refs([{"doc_id": None, "page": None}]) == ""
