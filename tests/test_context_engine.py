from __future__ import annotations

from src.context_engine import (
    build_context_snapshot,
    compact_assistant_context,
    compact_model_history,
    compact_tool_result,
    make_model_history_entry,
)


def test_compact_assistant_context_detects_flashcards() -> None:
    summary, category = compact_assistant_context(
        "Here are cards\n[FLASHCARDS]\nQ: What is heat?\nA: Energy transfer.\nQ: What is entropy?\nA: Disorder.\n[/FLASHCARDS]"
    )
    assert category == "flashcards"
    assert "generated 2 flashcards" in summary.lower()
    assert "What is heat?" in summary
    assert "Energy transfer." not in summary


def test_build_context_snapshot_tracks_memory_and_contributors() -> None:
    model_history = [
        make_model_history_entry("user", "Explain entropy simply."),
        make_model_history_entry("assistant", "Entropy is a measure of disorder in a system."),
    ]
    snapshot = build_context_snapshot(
        model_history=[entry for entry in model_history if entry],
        compact_memories=[{"id": "mem_1", "summary": "Earlier thermodynamics discussion", "source_count": 4}],
        transcript_messages=6,
        model_name="gpt-4o",
        system_prompt="system",
        context_limit=128000,
        tool_result_chars=321,
    )
    assert snapshot.compact_memory_blocks == 1
    assert snapshot.tool_result_chars == 321
    assert snapshot.category_sizes["memory"] > 0
    assert snapshot.category_sizes["chat"] > 0
    assert snapshot.category_sizes["tool_results"] == 321
    assert snapshot.largest_contributors
    assert any(msg["content"].startswith("[Compacted session memory") for msg in snapshot.messages)


def test_compact_model_history_keeps_recent_tail() -> None:
    model_history = []
    for idx in range(12):
        model_history.append(make_model_history_entry("user", f"user {idx}"))
        model_history.append(make_model_history_entry("assistant", "assistant " + ("x" * 800)))

    result = compact_model_history(
        model_history=[entry for entry in model_history if entry],
        compact_memories=[],
        compacted_transcript_count=0,
    )
    assert result.compacted is True
    assert result.memory_block is not None
    assert len(result.kept_model_history) == 8
    assert result.compacted_count == len(model_history) - 8


def test_compact_tool_result_limits_large_lists_and_text() -> None:
    compacted = compact_tool_result(
        "search_chunks",
        {"chunks": [{"text": "x" * 2000}] * 10, "results": [{"content": "y" * 2000}]},
    )
    assert len(compacted["chunks"]) == 5
    assert "chars omitted" in compacted["chunks"][0]["text"]
    assert "chars omitted" in compacted["results"][0]["content"]


def test_compact_assistant_context_detects_library_metadata() -> None:
    summary, category = compact_assistant_context(
        "Zotero results: found 12 library matches for thermodynamics with notes and attachments."
    )
    assert category == "library_metadata"
    assert "Zotero results" in summary


def test_compact_tool_result_uses_stricter_limits_for_library_sources() -> None:
    compacted = compact_tool_result(
        "zotero_search",
        {"results": [{"content": "z" * 1200}] * 8},
    )
    assert len(compacted["results"]) == 5
    assert "chars omitted" in compacted["results"][0]["content"]


def test_compact_tool_result_preserves_generate_quiz_json_structure() -> None:
    raw = {
        "tool": "generate_quiz",
        "result": (
            '[{"type":"mcq","question":"What is SI unit?","options":["a) force","b) energy","c) electric current","d) density"],'
            '"answer":"c","explanation":"Electric current is a base SI quantity."}]'
        ),
    }

    compacted = compact_tool_result("generate_quiz", raw)

    assert isinstance(compacted["result"], list)
    assert compacted["result"][0]["type"] == "mcq"
    assert compacted["result"][0]["question"] == "What is SI unit?"
