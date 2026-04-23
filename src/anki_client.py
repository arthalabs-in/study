"""AnkiConnect client for live Anki integration."""

from __future__ import annotations

import json
import urllib.request
from typing import Any


DEFAULT_ENDPOINT = "http://127.0.0.1:8765"


class AnkiClient:
    def __init__(self, endpoint: str | None = None) -> None:
        self.endpoint = (endpoint or DEFAULT_ENDPOINT).rstrip("/")

    def is_available(self, timeout: float = 2.0) -> bool:
        try:
            resp = self.invoke("version", _timeout=timeout)
            return isinstance(resp, int) or isinstance(resp, float)
        except Exception:
            return False

    def invoke(self, action: str, **params: Any) -> Any:
        payload = json.dumps({"action": action, "version": 6, "params": params}).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=params.pop("_timeout", 30)) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data.get("result")

    def create_deck(self, deck_name: str) -> dict:
        try:
            self.invoke("createDeck", deck=deck_name)
            return {"status": "ok", "deck": deck_name}
        except Exception as e:
            return {"error": str(e)}

    def find_notes(self, query: str) -> list[int]:
        try:
            result = self.invoke("findNotes", query=query)
            return list(result) if isinstance(result, list) else []
        except Exception:
            return []

    def notes_info(self, note_ids: list[int]) -> list[dict]:
        if not note_ids:
            return []
        try:
            result = self.invoke("notesInfo", notes=note_ids)
            return list(result) if isinstance(result, list) else []
        except Exception:
            return []

    def add_or_update_notes(
        self,
        *,
        cards: list[dict],
        deck_name: str,
        note_type: str,
        tags: list[str] | None = None,
        sync_state_lookup: dict | None = None,
    ) -> dict:
        if not cards:
            return {"error": "No cards to sync"}

        added = 0
        updated = 0
        errors: list[str] = []
        tags = tags or []

        for card in cards:
            try:
                model_name = _anki_model_name(note_type)
                fields: dict[str, str] = {}
                if note_type == "cloze" and card.get("cloze_text"):
                    fields["Text"] = str(card["cloze_text"])
                    fields["Extra"] = str(card.get("answer", ""))
                else:
                    fields["Front"] = str(card.get("question", ""))
                    back = str(card.get("answer", ""))
                    if card.get("source_refs"):
                        back += "\n\nRefs: " + _render_source_refs(card["source_refs"])
                    fields["Back"] = back

                note = {
                    "deckName": deck_name,
                    "modelName": model_name,
                    "fields": fields,
                    "tags": tags + [str(t).strip() for t in (card.get("tags") or []) if str(t).strip()],
                }

                # Avoid duplicates by searching on the primary field for the selected model.
                existing = self.find_notes(_duplicate_search_query(deck_name, note_type, fields))
                if existing:
                    # Update existing note fields
                    self.invoke("updateNoteFields", note={"id": existing[0], "fields": fields})
                    updated += 1
                else:
                    self.invoke("addNote", note=note)
                    added += 1
            except Exception as e:
                errors.append(str(e))

        return {
            "status": "ok",
            "added": added,
            "updated": updated,
            "errors": errors,
            "deck": deck_name,
        }

    def find_duplicates(self, cards: list[dict], deck_name: str) -> dict:
        dupes: list[dict] = []
        for card in cards:
            front = str(card.get("question", ""))
            try:
                existing = self.find_notes(f'deck:"{deck_name}" front:"{front}"')
                if existing:
                    dupes.append({"card_key": card.get("card_key"), "front": front, "anki_note_ids": existing})
            except Exception:
                pass
        return {"duplicates": dupes, "count": len(dupes)}


def _anki_model_name(note_type: str) -> str:
    if note_type == "cloze":
        return "Cloze"
    return "Basic"


def _render_source_refs(source_refs: Any) -> str:
    if not source_refs:
        return ""
    parts: list[str] = []
    for ref in source_refs:
        if isinstance(ref, dict):
            pieces = []
            if ref.get("doc_id"):
                pieces.append(str(ref["doc_id"]))
            if ref.get("page") is not None:
                pieces.append(f"p{ref['page']}")
            if ref.get("chunk_id"):
                pieces.append(str(ref["chunk_id"]))
            if pieces:
                parts.append(" ".join(pieces))
    return "; ".join(parts)


def _duplicate_search_query(deck_name: str, note_type: str, fields: dict[str, str]) -> str:
    field_name = "text" if note_type == "cloze" and fields.get("Text") else "front"
    query_value = fields.get("Text") if field_name == "text" else fields.get("Front", "")
    escaped_deck = str(deck_name).replace('"', '\\"')
    escaped_value = str(query_value or "").replace('"', '\\"')
    return f'deck:"{escaped_deck}" {field_name}:"{escaped_value}"'
