from __future__ import annotations

from pathlib import Path

from src import exporter


def test_ensure_dir_and_empty_exports(tmp_path: Path) -> None:
    custom = exporter._ensure_dir(tmp_path / "exports")
    assert custom.exists()
    assert exporter.export_flashcards([], export_dir=str(tmp_path)) == {"error": "No flashcards to export"}
    assert exporter.export_summary("   ", export_dir=str(tmp_path)) == {"error": "Nothing to export"}
    assert exporter.export_chat([], export_dir=str(tmp_path)) == {"error": "No messages to export"}


def test_export_flashcards_markdown_and_chat_unknown_role(tmp_path: Path) -> None:
    flashcards = exporter.export_flashcards(
        [{"question": "What is entropy?", "answer": "A disorder measure"}],
        fmt="markdown",
        export_dir=str(tmp_path),
    )
    flashcard_text = Path(flashcards["exported"]).read_text(encoding="utf-8")
    assert flashcards["format"] == "markdown"
    assert "### Card 1" in flashcard_text

    chat = exporter.export_chat(
        [
            {"role": "user", "content": "hi"},
            {"role": "tool", "content": "hidden"},
            {"role": "assistant", "content": "hello"},
        ],
        export_dir=str(tmp_path),
    )
    chat_text = Path(chat["exported"]).read_text(encoding="utf-8")
    assert "### You" in chat_text
    assert "### Assistant" in chat_text
    assert "hidden" not in chat_text


def test_export_flashcards_csv_and_anki_use_distinct_output_names(tmp_path: Path) -> None:
    csv_result = exporter.export_flashcards(
        [{"question": "Q", "answer": "A"}],
        fmt="csv",
        export_dir=str(tmp_path),
    )

    assert csv_result["exported"].endswith(".csv")
