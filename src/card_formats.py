"""Flashcard normalization — supports basic and cloze cards with metadata."""

from __future__ import annotations

import hashlib
import json
from typing import Any


_VALID_CARD_TYPES = {"basic", "cloze"}
_VALID_FOCI = {"new_material", "weak_area", "exam_cram", "review"}
_VALID_DIFFICULTIES = {"easy", "medium", "hard", None}


def normalize_card(card: dict, *, default_focus: str = "new_material") -> dict:
    """Normalize a single flashcard into the enriched schema."""
    if not isinstance(card, dict):
        card = {}

    question = str(card.get("question", "")).strip()
    answer = str(card.get("answer", "")).strip()
    card_type = str(card.get("card_type", "basic")).strip().lower()
    if card_type not in _VALID_CARD_TYPES:
        card_type = "basic"

    cloze_text = card.get("cloze_text")
    if cloze_text is not None:
        cloze_text = str(cloze_text).strip() or None

    source_refs = _normalize_source_refs(card.get("source_refs"))
    tags = _normalize_tags(card.get("tags"))

    focus = str(card.get("focus", default_focus)).strip().lower()
    if focus not in _VALID_FOCI:
        focus = default_focus

    difficulty = card.get("difficulty")
    if difficulty is not None:
        difficulty = str(difficulty).strip().lower()
        if difficulty not in {"easy", "medium", "hard"}:
            difficulty = None

    card_key = str(card.get("card_key", "")).strip()
    if not card_key:
        card_key = compute_card_key({"question": question, "answer": answer, "card_type": card_type, "cloze_text": cloze_text})

    normalized = {
        "card_key": card_key,
        "question": question,
        "answer": answer,
        "card_type": card_type,
        "cloze_text": cloze_text,
        "source_refs": source_refs,
        "tags": tags,
        "focus": focus,
        "difficulty": difficulty,
    }
    normalized["payload_hash"] = card_payload_hash(normalized)
    return normalized


def normalize_cards(cards: list[dict], *, default_focus: str = "new_material") -> list[dict]:
    """Normalize a list of flashcards."""
    if not isinstance(cards, list):
        return []
    return [normalize_card(card, default_focus=default_focus) for card in cards if isinstance(card, dict)]


def compute_card_key(card: dict) -> str:
    """Compute a stable card key from core fields."""
    payload = json.dumps(
        {
            "q": str(card.get("question", "")).strip(),
            "a": str(card.get("answer", "")).strip(),
            "t": str(card.get("card_type", "basic")).strip().lower(),
            "c": str(card.get("cloze_text", "") or "").strip(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def card_payload_hash(card: dict) -> str:
    """Compute a hash over the full normalized card for sync deduplication."""
    payload_dict = dict(card)
    payload_dict.pop("payload_hash", None)
    payload = json.dumps(payload_dict, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def render_source_ref_text(source_refs: list[dict]) -> str:
    """Render source references as a human-readable string."""
    if not source_refs:
        return ""
    parts: list[str] = []
    for ref in source_refs:
        if not isinstance(ref, dict):
            continue
        doc_id = str(ref.get("doc_id") or "").strip()
        page = ref.get("page")
        chunk_id = str(ref.get("chunk_id") or "").strip()
        pieces: list[str] = []
        if doc_id:
            pieces.append(doc_id)
        if page is not None:
            pieces.append(f"p{page}")
        if chunk_id:
            pieces.append(chunk_id)
        if pieces:
            parts.append(" ".join(pieces))
    return "; ".join(parts)


def is_cloze_card(card: dict) -> bool:
    """Check if a card is a cloze card."""
    return str(card.get("card_type", "")).strip().lower() == "cloze"


def _normalize_source_refs(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    refs: list[dict] = []
    for item in value:
        if isinstance(item, dict):
            ref: dict[str, Any] = {}
            if "doc_id" in item and item["doc_id"] is not None:
                doc_id = str(item["doc_id"]).strip()
                if doc_id:
                    ref["doc_id"] = doc_id
            if "page" in item and item["page"] is not None:
                try:
                    ref["page"] = int(item["page"])
                except Exception:
                    pass
            if "chunk_id" in item and item["chunk_id"] is not None:
                chunk_id = str(item["chunk_id"]).strip()
                if chunk_id:
                    ref["chunk_id"] = chunk_id
            if ref:
                refs.append(ref)
    return refs


def _normalize_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    tags: list[str] = []
    for tag in value:
        t = str(tag).strip().lower()
        if t and t not in tags:
            tags.append(t)
    return tags
