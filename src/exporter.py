"""
Exporter - export study materials to Markdown, CSV, and PDF.
Supports flashcards, notes, summaries, and chat transcripts.
"""

from __future__ import annotations

import csv
import io
import sys
from hashlib import sha1
from datetime import datetime
from pathlib import Path


DEFAULT_EXPORT_DIR = Path.home() / "Documents" / "StudyTUI-Exports"
_DANGEROUS_SPREADSHEET_PREFIXES = ("=", "+", "-", "@")
_ANKI_BASIC_MODEL_ID = 2048671937
_ANKI_CLOZE_MODEL_ID = 2048671938


def _ensure_dir(path: Path | str | None = None) -> Path:
    export_dir = Path(path) if path else DEFAULT_EXPORT_DIR
    export_dir.mkdir(parents=True, exist_ok=True)
    return export_dir


def _sanitize_spreadsheet_cell(value: object) -> str:
    text = "" if value is None else str(value)
    stripped = text.lstrip(" \t\r\n")
    if stripped.startswith(_DANGEROUS_SPREADSHEET_PREFIXES):
        return "'" + text
    return text


def _render_source_refs(source_refs: list[dict] | None) -> str:
    if not source_refs:
        return ""
    parts: list[str] = []
    for ref in source_refs or []:
        if not isinstance(ref, dict):
            continue
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


def export_flashcards(
    cards: list[dict],
    fmt: str = "markdown",
    export_dir: str | None = None,
    *,
    deck_name: str | None = None,
    note_type: str = "basic",
    tags: list[str] | None = None,
    include_source_refs: bool = False,
) -> dict:
    """Export flashcards as Markdown, Anki package, or CSV."""
    if not cards:
        return {"error": "No flashcards to export"}

    export_path = _ensure_dir(export_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    effective_deck_name = (deck_name or f"Study TUI {datetime.now().strftime('%Y-%m-%d %H:%M')}").strip()
    effective_tags = [str(t).strip() for t in (tags or []) if str(t).strip()]

    if fmt == "anki":
        try:
            import genanki  # type: ignore
        except Exception:
            return {"error": "Anki export requires the optional dependency 'genanki'. Install study-tui[anki] or pip install genanki."}

        out = export_path / f"flashcards_{ts}.apkg"
        deck_seed = f"{ts}:{len(cards)}:{cards[0].get('question', '') if cards else ''}"
        deck_id = int(sha1(deck_seed.encode("utf-8")).hexdigest()[:10], 16)

        basic_model = genanki.Model(
            _ANKI_BASIC_MODEL_ID,
            "Study TUI Basic",
            fields=[
                {"name": "Question"},
                {"name": "Answer"},
            ],
            templates=[
                {
                    "name": "Card 1",
                    "qfmt": "{{Question}}",
                    "afmt": "{{FrontSide}}<hr id=\"answer\">{{Answer}}",
                }
            ],
        )

        cloze_model = genanki.Model(
            _ANKI_CLOZE_MODEL_ID,
            "Study TUI Cloze",
            fields=[
                {"name": "Text"},
                {"name": "Extra"},
            ],
            templates=[
                {
                    "name": "Cloze",
                    "qfmt": "{{cloze:Text}}",
                    "afmt": "{{cloze:Text}}<br>{{Extra}}",
                }
            ],
            model_type=genanki.Model.CLOZE,
        )

        deck = genanki.Deck(deck_id, effective_deck_name)
        for card in cards:
            ctype = str(card.get("card_type", "basic") or "basic").strip().lower()
            if ctype == "cloze" and card.get("cloze_text"):
                text = str(card["cloze_text"])
                extra = str(card.get("answer", ""))
                if include_source_refs and card.get("source_refs"):
                    extra += "\n\nRefs: " + _render_source_refs(card["source_refs"])
                note = genanki.Note(
                    model=cloze_model,
                    fields=[text, extra],
                    tags=effective_tags + [str(t).strip() for t in (card.get("tags") or []) if str(t).strip()],
                )
            else:
                question = str(card.get("question", ""))
                answer = str(card.get("answer", ""))
                if include_source_refs and card.get("source_refs"):
                    answer += "\n\nRefs: " + _render_source_refs(card["source_refs"])
                note = genanki.Note(
                    model=basic_model,
                    fields=[question, answer],
                    tags=effective_tags + [str(t).strip() for t in (card.get("tags") or []) if str(t).strip()],
                )
            deck.add_note(note)
        genanki.Package(deck).write_to_file(str(out))
        return {"exported": str(out), "count": len(cards), "format": "anki", "deck": effective_deck_name}

    if fmt == "csv":
        out = export_path / f"flashcards_{ts}.csv"
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter="\t", lineterminator="\n")
        for card in cards:
            ctype = str(card.get("card_type", "basic") or "basic").strip().lower()
            if ctype == "cloze" and card.get("cloze_text"):
                row = [
                    _sanitize_spreadsheet_cell(card.get("cloze_text", "")),
                    _sanitize_spreadsheet_cell(card.get("answer", "")),
                    "cloze",
                ]
            else:
                row = [
                    _sanitize_spreadsheet_cell(card.get("question", "")),
                    _sanitize_spreadsheet_cell(card.get("answer", "")),
                    "basic",
                ]
            if effective_tags:
                row.append(_sanitize_spreadsheet_cell(", ".join(effective_tags)))
            writer.writerow(row)
        out.write_text(buffer.getvalue(), encoding="utf-8")
        return {"exported": str(out), "count": len(cards), "format": "csv (Anki-compatible)"}

    out = export_path / f"flashcards_{ts}.md"
    lines = [f"# Flashcards — {effective_deck_name}\n"]
    lines.append(f"*{len(cards)} cards - {datetime.now().strftime('%Y-%m-%d')}*\n")
    for index, card in enumerate(cards, 1):
        ctype = str(card.get("card_type", "basic") or "basic").strip().lower()
        lines.append(f"### Card {index}\n")
        if ctype == "cloze" and card.get("cloze_text"):
            lines.append(f"**Cloze:** {card.get('cloze_text', '')}\n")
            lines.append(f"**Extra:** {card.get('answer', '')}\n")
        else:
            lines.append(f"**Q:** {card.get('question', '')}\n")
            lines.append(f"**A:** {card.get('answer', '')}\n")
        if card.get("tags"):
            lines.append(f"Tags: {', '.join(str(t) for t in card['tags'])}\n")
        if include_source_refs and card.get("source_refs"):
            lines.append(f"Refs: {_render_source_refs(card['source_refs'])}\n")
        lines.append("---\n")
    out.write_text("\n".join(lines), encoding="utf-8")
    return {"exported": str(out), "count": len(cards), "format": "markdown"}


def export_summary(text: str, title: str = "Summary", export_dir: str | None = None) -> dict:
    """Export a summary as Markdown."""
    if not text.strip():
        return {"error": "Nothing to export"}

    export_path = _ensure_dir(export_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = export_path / f"summary_{ts}.md"
    content = f"# {title}\n\n*Exported {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n\n{text}\n"
    out.write_text(content, encoding="utf-8")
    return {"exported": str(out), "format": "markdown"}


def export_chat(messages: list[dict], export_dir: str | None = None) -> dict:
    """Export chat transcript as Markdown."""
    if not messages:
        return {"error": "No messages to export"}

    export_path = _ensure_dir(export_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = export_path / f"chat_{ts}.md"

    lines = ["# Chat Transcript\n"]
    lines.append(f"*{len(messages)} messages - {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")

    for msg in messages:
        role = msg.get("role", "unknown").title()
        text = msg.get("content", "")
        if role == "User":
            lines.append(f"### You\n\n{text}\n")
        elif role == "Assistant":
            lines.append(f"### Assistant\n\n{text}\n")
        lines.append("---\n")

    out.write_text("\n".join(lines), encoding="utf-8")
    return {"exported": str(out), "messages": len(messages), "format": "markdown"}
