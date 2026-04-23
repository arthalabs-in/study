"""Tests for card_formats.py."""

import pytest
from src.card_formats import (
    normalize_card,
    normalize_cards,
    compute_card_key,
    card_payload_hash,
    render_source_ref_text,
    is_cloze_card,
)


class TestNormalizeCard:
    def test_plain_qa_normalizes(self):
        raw = {"question": "What is 2+2?", "answer": "4"}
        out = normalize_card(raw)
        assert out["question"] == "What is 2+2?"
        assert out["answer"] == "4"
        assert out["card_type"] == "basic"
        assert out["cloze_text"] is None
        assert out["source_refs"] == []
        assert out["tags"] == []
        assert out["focus"] == "new_material"
        assert out["difficulty"] is None
        assert out["card_key"]
        assert out["payload_hash"]

    def test_cloze_card(self):
        raw = {
            "question": "What is the capital of France?",
            "answer": "Paris",
            "card_type": "cloze",
            "cloze_text": "The capital of France is {{c1::Paris}}.",
            "tags": ["geography"],
            "focus": "weak_area",
            "difficulty": "medium",
        }
        out = normalize_card(raw)
        assert out["card_type"] == "cloze"
        assert out["cloze_text"] == "The capital of France is {{c1::Paris}}."
        assert out["tags"] == ["geography"]
        assert out["focus"] == "weak_area"
        assert out["difficulty"] == "medium"

    def test_invalid_card_type_defaults_to_basic(self):
        out = normalize_card({"question": "Q", "answer": "A", "card_type": "nonsense"})
        assert out["card_type"] == "basic"

    def test_source_refs_normalized(self):
        raw = {
            "question": "Q",
            "answer": "A",
            "source_refs": [{"doc_id": "doc1", "page": 3, "chunk_id": "c1"}],
        }
        out = normalize_card(raw)
        assert out["source_refs"] == [{"doc_id": "doc1", "page": 3, "chunk_id": "c1"}]


class TestNormalizeCards:
    def test_list_of_cards(self):
        cards = [
            {"question": "Q1", "answer": "A1"},
            {"question": "Q2", "answer": "A2"},
        ]
        out = normalize_cards(cards)
        assert len(out) == 2
        assert out[0]["question"] == "Q1"

    def test_non_list_returns_empty(self):
        assert normalize_cards("nope") == []


class TestComputeCardKey:
    def test_stable(self):
        c = {"question": "Q", "answer": "A", "card_type": "basic", "cloze_text": None}
        assert compute_card_key(c) == compute_card_key(c)

    def test_different_cards_different_keys(self):
        assert compute_card_key({"question": "Q1", "answer": "A"}) != compute_card_key({"question": "Q2", "answer": "A"})


class TestCardPayloadHash:
    def test_stable(self):
        c = {"question": "Q", "answer": "A"}
        assert card_payload_hash(c) == card_payload_hash(c)


class TestRenderSourceRefText:
    def test_empty(self):
        assert render_source_ref_text([]) == ""

    def test_renders(self):
        refs = [{"doc_id": "doc1", "page": 2, "chunk_id": "c1"}]
        assert render_source_ref_text(refs) == "doc1 p2 c1"


class TestIsClozeCard:
    def test_basic(self):
        assert is_cloze_card({"card_type": "basic"}) is False

    def test_cloze(self):
        assert is_cloze_card({"card_type": "cloze"}) is True


class TestHostileCardFormats:
    def test_none_input_normalizes_to_empty_card(self):
        out = normalize_card(None)
        assert out["question"] == ""
        assert out["answer"] == ""
        assert out["card_type"] == "basic"

    def test_empty_dict_normalizes(self):
        out = normalize_card({})
        assert out["card_key"]
        assert out["payload_hash"]

    def test_extremely_long_strings(self):
        q = "A" * 10000
        a = "B" * 10000
        out = normalize_card({"question": q, "answer": a})
        assert out["question"] == q
        assert out["answer"] == a
        assert len(out["payload_hash"]) == 16

    def test_formula_injection_in_fields(self):
        out = normalize_card({"question": "=SUM(A1)", "answer": "+cmd|' eval"})
        assert out["question"] == "=SUM(A1)"
        assert out["answer"] == "+cmd|' eval"

    def test_unicode_and_special_chars(self):
        out = normalize_card({"question": "日本語 🎉 \x00\x01", "answer": "<script>alert(1)</script>"})
        assert "日本語" in out["question"]
        assert "<script>" in out["answer"]

    def test_invalid_focus_defaults_safely(self):
        out = normalize_card({"question": "Q", "answer": "A", "focus": "malicious_focus'}); DROP TABLE--"})
        assert out["focus"] == "new_material"

    def test_invalid_difficulty_defaults_to_none(self):
        out = normalize_card({"question": "Q", "answer": "A", "difficulty": "impossible"})
        assert out["difficulty"] is None

    def test_malformed_source_refs_filtered(self):
        out = normalize_card({
            "question": "Q",
            "answer": "A",
            "source_refs": [
                {"doc_id": "d1", "page": 5},
                "not_a_dict",
                {"page": "not_an_int"},
                {},
            ],
        })
        assert len(out["source_refs"]) == 1
        assert out["source_refs"][0]["doc_id"] == "d1"
        assert out["source_refs"][0]["page"] == 5

    def test_payload_hash_stability_excludes_itself(self):
        c1 = normalize_card({"question": "Q", "answer": "A"})
        c2 = normalize_card({"question": "Q", "answer": "A"})
        assert c1["payload_hash"] == c2["payload_hash"]

    def test_normalize_cards_filters_non_dicts(self):
        out = normalize_cards([{"question": "Q", "answer": "A"}, None, "string", 123])
        assert len(out) == 1

    def test_tags_deduplicated_and_lowercased(self):
        out = normalize_card({"question": "Q", "answer": "A", "tags": ["A", "a", " B ", "b"]})
        assert out["tags"] == ["a", "b"]
