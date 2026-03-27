"""Shared context assembly, compaction, and pruning helpers for Study TUI."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any

try:
    import tiktoken
except Exception:  # pragma: no cover - optional at runtime
    tiktoken = None

from src.widgets.chat import _parse_flashcards


DEFAULT_CONTEXT_HARD_LIMIT_TOKENS = 24000
DEFAULT_AUTO_COMPACT_TRIGGER_TOKENS = 12000
MAX_ASSISTANT_CONTEXT_CHARS = 1200
MAX_USER_CONTEXT_CHARS = 4000
MAX_MEMORY_CHARS = 1800
MAX_MEMORY_BLOCKS = 4
MIN_RECENT_MODEL_MESSAGES = 6
RECENT_TAIL_TO_KEEP = 8
TOOL_TEXT_LIMIT = 500
TOOL_LIST_LIMIT = 6
LIBRARY_RESULT_TEXT_LIMIT = 240
LIBRARY_RESULT_LIST_LIMIT = 4
SEARCH_RESULT_TEXT_LIMIT = 320
SEARCH_RESULT_LIST_LIMIT = 4


@dataclass
class ContextSnapshot:
    messages: list[dict]
    prompt_tokens: int
    transcript_messages: int
    model_history_messages: int
    compact_memory_blocks: int
    compact_memory_chars: int
    tool_result_chars: int
    recent_turn_chars: int
    sent_messages: int
    stored_messages: int
    omitted_messages: int
    context_limit: int | None
    category_sizes: dict[str, int]
    largest_contributors: list[dict]
    retained_artifact_count: int
    durable_artifact_count: int
    full_artifact_count: int
    gist_artifact_count: int
    dropped_artifact_count: int
    selected_tool_count: int
    tool_schema_tokens: int

    def to_metadata(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("messages", None)
        return payload


@dataclass
class CompactionResult:
    compacted: bool
    memory_block: dict[str, Any] | None
    kept_model_history: list[dict]
    compacted_count: int
    report_lines: list[str]


@dataclass
class ToolArtifactSnapshot:
    kept: list[dict]
    full_count: int
    gist_count: int
    durable_count: int
    dropped_count: int


def stringify_message_content(content: Any) -> str:
    if isinstance(content, list):
        try:
            return json.dumps(content, ensure_ascii=False)
        except Exception:
            return str(content)
    return str(content or "")


def get_tiktoken_encoder(model_name: str):
    if tiktoken is None:
        return None
    try:
        return tiktoken.encoding_for_model(model_name)
    except Exception:
        for fallback in ("o200k_base", "cl100k_base"):
            try:
                return tiktoken.get_encoding(fallback)
            except Exception:
                continue
    return None


def estimate_text_tokens(text: str, model_name: str) -> int:
    if not text:
        return 0
    encoder = get_tiktoken_encoder(model_name)
    if encoder is not None:
        try:
            return len(encoder.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)


def estimate_chat_tokens(messages: list[dict], system: str, model_name: str) -> int:
    total = estimate_text_tokens(system, model_name)
    for msg in messages:
        role = str(msg.get("role", "user"))
        content = stringify_message_content(msg.get("content", ""))
        total += estimate_text_tokens(role, model_name)
        total += estimate_text_tokens(content, model_name)
        total += 4
    return total + 2


def estimate_payload_chars(payload: Any) -> int:
    try:
        return len(json.dumps(payload, ensure_ascii=False))
    except Exception:
        return len(str(payload or ""))


def truncate_context_text(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2].rstrip()
    tail = text[-(max_chars // 3):].lstrip()
    omitted = max(len(text) - len(head) - len(tail), 0)
    return f"{head}\n\n[...{omitted} chars omitted for context budget...]\n\n{tail}"


def compact_assistant_context(content: str) -> tuple[str, str]:
    text = stringify_message_content(content).strip()
    if not text:
        return "", "assistant"

    parsed_flashcards = _parse_flashcards(text)
    if parsed_flashcards:
        intro_lines, cards, outro_lines = parsed_flashcards
        lines = [f"[Assistant generated {len(cards)} flashcards.]"]
        if intro_lines:
            lines.append(f"Intro: {truncate_context_text(intro_lines[0], 220)}")
        if cards:
            sample_questions = " | ".join(q.strip() for q, _ in cards[:3] if q.strip())
            if sample_questions:
                lines.append(f"Sample questions: {sample_questions}")
        if outro_lines:
            lines.append(f"Outro: {truncate_context_text(outro_lines[0], 220)}")
        lines.append("Use the current deck or regenerate/export it if the user asks for flashcards again.")
        return "\n".join(lines), "flashcards"

    if text.startswith("[QUIZ COMPLETED]"):
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        score_line = lines[0]
        weak_lines = [line for line in lines if line.startswith("✗") or "Correct answer:" in line]
        summary = [score_line]
        if weak_lines:
            summary.append("Weak areas:")
            summary.extend(truncate_context_text(line, 180) for line in weak_lines[:6])
        return "\n".join(summary), "quiz_results"

    if len(text) > MAX_ASSISTANT_CONTEXT_CHARS:
        return truncate_context_text(text, MAX_ASSISTANT_CONTEXT_CHARS), "summary"

    lowered = text.lower()
    if any(marker in lowered for marker in ("zotero", "calibre", "library result", "library match")):
        return truncate_context_text(text, 520), "library_metadata"
    if any(marker in lowered for marker in ("available files", "found files", "document matches")):
        return truncate_context_text(text, 420), "file_listing"

    return text, "assistant"


def make_model_history_entry(role: str, content: Any) -> dict[str, Any] | None:
    normalized_role = str(role or "user").strip().lower()
    if normalized_role not in {"user", "assistant", "system"}:
        return None

    text = stringify_message_content(content).strip()
    if not text:
        return None

    category = "chat"
    if normalized_role == "assistant":
        text, category = compact_assistant_context(text)
    elif normalized_role == "user":
        if text.startswith("[System context"):
            category = "context"

    return {
        "role": normalized_role,
        "content": text,
        "category": category,
        "chars": len(text),
        "created_at": time.time(),
    }


def compact_tool_result(tool_name: str, result: Any) -> Any:
    lowered_tool = str(tool_name or "").strip().lower()
    if lowered_tool == "generate_quiz":
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
            except Exception:
                return result
            return parsed if isinstance(parsed, list) else result
        if isinstance(result, dict):
            raw_quiz = result.get("result")
            if isinstance(raw_quiz, str):
                try:
                    parsed = json.loads(raw_quiz)
                except Exception:
                    return result
                if isinstance(parsed, list):
                    compacted = dict(result)
                    compacted["result"] = parsed
                    return compacted
        return result
    if lowered_tool == "animate_concept" and isinstance(result, dict):
        keep = {
            "status",
            "topic",
            "attempt",
            "retryable",
            "quality",
            "scene_name",
            "duration_seconds",
            "video_path",
            "code_path",
            "error",
            "stderr_preview",
        }
        compacted = {key: result.get(key) for key in keep if key in result}
        if "error" in compacted and isinstance(compacted["error"], str):
            compacted["error"] = truncate_context_text(compacted["error"], TOOL_TEXT_LIMIT)
        if "stderr_preview" in compacted and isinstance(compacted["stderr_preview"], str):
            compacted["stderr_preview"] = truncate_context_text(compacted["stderr_preview"], TOOL_TEXT_LIMIT)
        return compacted

    text_limit = TOOL_TEXT_LIMIT
    list_limit = TOOL_LIST_LIMIT
    if lowered_tool in {"calibre_search", "zotero_search", "zotero_collections", "list_available_files"}:
        text_limit = LIBRARY_RESULT_TEXT_LIMIT
        list_limit = LIBRARY_RESULT_LIST_LIMIT
    elif lowered_tool in {"search_chunks", "web_search"}:
        text_limit = SEARCH_RESULT_TEXT_LIMIT
        list_limit = SEARCH_RESULT_LIST_LIMIT

    if isinstance(result, str):
        return truncate_context_text(result, text_limit)
    if isinstance(result, (int, float, bool)) or result is None:
        return result
    if isinstance(result, list):
        items = [compact_tool_result(tool_name, item) for item in result[:list_limit]]
        if len(result) > list_limit:
            items.append({"_truncated_items": len(result) - list_limit})
        return items
    if isinstance(result, dict):
        compacted: dict[str, Any] = {}
        for key, value in result.items():
            lowered = key.lower()
            if lowered in {"text", "content", "excerpt", "snippet", "answer", "explanation"}:
                compacted[key] = truncate_context_text(str(value or ""), text_limit)
            elif lowered in {"chunks", "results", "documents", "images", "cards", "notes"} and isinstance(value, list):
                compacted[key] = compact_tool_result(tool_name, value)
            else:
                compacted[key] = compact_tool_result(tool_name, value)
        return compacted
    return truncate_context_text(str(result), text_limit)


def estimate_tool_schema_tokens(tools: list[dict] | None, model_name: str) -> int:
    if not tools:
        return 0
    try:
        payload = json.dumps(tools, ensure_ascii=False)
    except Exception:
        payload = str(tools)
    return estimate_text_tokens(payload, model_name)


def _tool_retention_class(tool_name: str) -> str | None:
    lowered = str(tool_name or "").strip().lower()
    if lowered in {
        "list_documents",
        "list_available_files",
        "zotero_collections",
        "calibre_search",
        "zotero_search",
        "get_document_images",
    }:
        return "ephemeral"
    if lowered in {
        "search_chunks",
        "web_search",
        "get_chunk_by_id",
        "get_chunks_by_page",
        "get_page_image",
    }:
        return "conversation"
    if lowered in {
        "generate_flashcards",
        "generate_quiz",
        "summarize_document",
        "animate_concept",
        "get_study_progress",
        "save_progress_note",
        "get_review_queue",
        "save_note",
        "list_notes",
        "search_notes",
        "export_content",
    }:
        return "durable"
    return None


def _tool_artifact_category(tool_name: str) -> str:
    lowered = str(tool_name or "").strip().lower()
    if lowered in {
        "list_documents",
        "list_available_files",
        "zotero_collections",
        "calibre_search",
        "zotero_search",
        "get_document_images",
    }:
        return "tool_listing"
    if lowered in {"search_chunks", "web_search"}:
        return "tool_search"
    if lowered in {"get_chunk_by_id", "get_chunks_by_page", "get_page_image"}:
        return "tool_read"
    return "tool_memory"


def _extract_source_refs(payload: Any) -> list[str]:
    refs: list[str] = []

    def _visit(value: Any) -> None:
        if isinstance(value, dict):
            doc_id = str(value.get("doc_id", "")).strip()
            page = value.get("page")
            chunk_id = str(value.get("chunk_id", "") or value.get("id", "")).strip()
            if doc_id:
                ref = doc_id
                if page is not None:
                    ref += f":p{page}"
                refs.append(ref)
            elif chunk_id and "_c" in chunk_id:
                refs.append(chunk_id)
            for item in value.values():
                if len(refs) >= 6:
                    return
                _visit(item)
        elif isinstance(value, list):
            for item in value[:6]:
                if len(refs) >= 6:
                    return
                _visit(item)

    _visit(payload)
    seen: list[str] = []
    for ref in refs:
        if ref and ref not in seen:
            seen.append(ref)
    return seen[:6]


def _artifact_names(payload: Any, keys: tuple[str, ...], limit: int = 4) -> list[str]:
    names: list[str] = []
    items = payload if isinstance(payload, list) else payload.get("results") if isinstance(payload, dict) else []
    if not isinstance(items, list):
        return []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        for key in keys:
            value = str(item.get(key, "")).strip()
            if value:
                names.append(value)
                break
    return names


def _artifact_preview_lines(payload: Any, limit: int = 3) -> list[str]:
    lines: list[str] = []
    items = payload if isinstance(payload, list) else []
    if isinstance(payload, dict):
        for key in ("chunks", "results", "documents", "notes"):
            maybe = payload.get(key)
            if isinstance(maybe, list):
                items = maybe
                break
    if not isinstance(items, list):
        return []
    for item in items[:limit]:
        if isinstance(item, dict):
            doc_id = str(item.get("doc_id", "")).strip()
            page = item.get("page")
            title = str(
                item.get("title", "")
                or item.get("name", "")
                or item.get("source_name", "")
                or item.get("relative_path", "")
                or item.get("path", "")
            ).strip()
            snippet = str(
                item.get("summary", "")
                or item.get("snippet", "")
                or item.get("excerpt", "")
                or item.get("content", "")
                or item.get("text", "")
            ).strip()
            line = title or doc_id or "item"
            if doc_id and doc_id not in line:
                line = f"{line} ({doc_id})"
            if page is not None:
                line += f" p{page}"
            if snippet:
                line += f": {truncate_context_text(snippet, 100)}"
            lines.append(line)
        else:
            lines.append(truncate_context_text(stringify_message_content(item), 100))
    return lines


def _tool_gist_payload(tool_name: str, compacted_result: Any) -> str:
    lowered = str(tool_name or "").strip().lower()
    if lowered == "list_documents":
        names = _artifact_names(compacted_result, ("title", "source_name", "doc_id"))
        count = len(compacted_result) if isinstance(compacted_result, list) else 0
        joined = ", ".join(names) if names else "none"
        return f"Loaded documents ({count}): {joined}"
    if lowered == "list_available_files":
        names = _artifact_names(compacted_result, ("relative_path", "source_name", "name", "path"))
        count = len(compacted_result) if isinstance(compacted_result, list) else 0
        joined = ", ".join(names) if names else "none"
        return f"Available files ({count}): {joined}"
    if lowered in {"calibre_search", "zotero_search", "zotero_collections"}:
        names = _artifact_names(compacted_result, ("title", "name", "collection", "key"))
        count = len(compacted_result.get("results", compacted_result)) if isinstance(compacted_result, dict) else len(compacted_result) if isinstance(compacted_result, list) else 0
        joined = ", ".join(names) if names else "none"
        return f"{tool_name} matches ({count}): {joined}"
    if lowered == "get_document_images":
        previews = _artifact_preview_lines(compacted_result, limit=3)
        return "Figure pages: " + (" | ".join(previews) if previews else "none")
    if lowered in {"search_chunks", "web_search"}:
        previews = _artifact_preview_lines(compacted_result, limit=3)
        return "Top results: " + (" | ".join(previews) if previews else "none")
    if lowered in {"get_chunk_by_id", "get_chunks_by_page", "get_page_image"}:
        previews = _artifact_preview_lines(compacted_result, limit=2)
        if previews:
            return "Read context: " + " | ".join(previews)
        return truncate_context_text(stringify_message_content(compacted_result), 180)
    if lowered == "generate_flashcards":
        raw = compacted_result.get("result") if isinstance(compacted_result, dict) else compacted_result
        parsed = _parse_flashcards(stringify_message_content(raw))
        if parsed:
            _, cards, _ = parsed
            questions = " | ".join(question for question, _ in cards[:2])
            return f"Generated {len(cards)} flashcards" + (f": {questions}" if questions else "")
        return "Generated flashcards."
    if lowered == "generate_quiz":
        raw = compacted_result.get("result") if isinstance(compacted_result, dict) else compacted_result
        if isinstance(raw, list):
            return f"Generated {len(raw)} quiz questions."
        return "Generated a quiz."
    if lowered == "summarize_document":
        raw = compacted_result.get("result") if isinstance(compacted_result, dict) else compacted_result
        return "Generated summary: " + truncate_context_text(stringify_message_content(raw), 180)
    if lowered == "animate_concept" and isinstance(compacted_result, dict):
        status = str(compacted_result.get("status", "")).strip() or "unknown"
        topic = str(compacted_result.get("topic", "")).strip() or "concept"
        scene = str(compacted_result.get("scene_name", "")).strip()
        path = str(compacted_result.get("video_path", "")).strip()
        error = str(compacted_result.get("error", "")).strip()
        parts = [f"Animation {status} for {topic}."]
        if scene:
            parts.append(f"Scene: {scene}.")
        if path:
            parts.append(f"Video: {path}.")
        if error:
            parts.append(f"Error: {truncate_context_text(error, 120)}")
        return " ".join(parts)
    if lowered == "get_study_progress" and isinstance(compacted_result, dict):
        weak = compacted_result.get("weak_topics") or []
        strong = compacted_result.get("strong_topics") or []
        grasp = compacted_result.get("grasp_level")
        parts = ["Loaded study progress."]
        if grasp is not None:
            parts.append(f"Grasp: {grasp}")
        if weak:
            parts.append("Weak: " + ", ".join(str(topic) for topic in weak[:3]))
        if strong:
            parts.append("Strong: " + ", ".join(str(topic) for topic in strong[:3]))
        return " ".join(parts)
    if lowered == "save_progress_note":
        return "Saved a study progress note."
    if lowered == "get_review_queue" and isinstance(compacted_result, dict):
        due = compacted_result.get("due_count", 0)
        total = compacted_result.get("card_count", 0)
        weak = compacted_result.get("weak_topics") or []
        suffix = f" Weak focus: {', '.join(str(topic) for topic in weak[:3])}." if weak else ""
        return f"Loaded review queue: {due} due, {total} total.{suffix}"
    if lowered == "save_note" and isinstance(compacted_result, dict):
        title = str(compacted_result.get("title", "")).strip()
        return f"Saved note{f': {title}' if title else '.'}"
    if lowered in {"list_notes", "search_notes"}:
        previews = _artifact_preview_lines(compacted_result, limit=3)
        return "Notes: " + (" | ".join(previews) if previews else "none")
    if lowered == "export_content" and isinstance(compacted_result, dict):
        exported = str(compacted_result.get("exported", "") or compacted_result.get("path", "")).strip()
        fmt = str(compacted_result.get("format", "")).strip()
        return f"Exported content{f' as {fmt}' if fmt else ''}{f' to {exported}' if exported else '.'}"
    return truncate_context_text(stringify_message_content(compacted_result), 180)


def make_tool_artifact(tool_name: str, result: Any, turn_index: int) -> dict[str, Any] | None:
    retention_class = _tool_retention_class(tool_name)
    if not retention_class:
        return None
    compacted = compact_tool_result(tool_name, result)
    gist_payload = _tool_gist_payload(tool_name, compacted)
    lowered = str(tool_name or "").strip().lower()
    if retention_class == "durable":
        full_payload: Any = gist_payload
    else:
        full_payload = compacted
    if not stringify_message_content(full_payload).strip() and not gist_payload.strip():
        return None
    return {
        "tool_name": tool_name,
        "turn_index": int(turn_index),
        "retention_class": retention_class,
        "full_payload": full_payload,
        "gist_payload": gist_payload,
        "category": _tool_artifact_category(tool_name),
        "source_refs": _extract_source_refs(compacted),
        "created_at": time.time(),
    }


def prune_tool_artifacts(tool_artifacts: list[dict], current_turn_index: int) -> ToolArtifactSnapshot:
    kept: list[dict] = []
    full_count = 0
    gist_count = 0
    durable_count = 0
    dropped_count = 0
    for artifact in tool_artifacts:
        retention_class = str(artifact.get("retention_class", "")).strip().lower()
        turn_index = int(artifact.get("turn_index", 0) or 0)
        age = max(int(current_turn_index) - turn_index, 0)
        if retention_class == "durable":
            kept.append(dict(artifact))
            durable_count += 1
            continue
        if retention_class == "conversation":
            kept.append(dict(artifact))
            if age <= 0:
                full_count += 1
            else:
                gist_count += 1
            continue
        if age <= 0:
            kept.append(dict(artifact))
            full_count += 1
            continue
        if age == 1:
            kept.append(dict(artifact))
            gist_count += 1
            continue
        dropped_count += 1
    return ToolArtifactSnapshot(
        kept=kept,
        full_count=full_count,
        gist_count=gist_count,
        durable_count=durable_count,
        dropped_count=dropped_count,
    )


def _artifact_prompt_message(artifact: dict[str, Any], current_turn_index: int) -> dict[str, str] | None:
    retention_class = str(artifact.get("retention_class", "")).strip().lower()
    turn_index = int(artifact.get("turn_index", 0) or 0)
    age = max(int(current_turn_index) - turn_index, 0)
    tool_name = str(artifact.get("tool_name", "tool")).strip() or "tool"
    payload: Any
    if retention_class == "durable":
        payload = artifact.get("gist_payload") or artifact.get("full_payload")
        prefix = "[Durable tool context]"
    elif retention_class == "conversation" and age <= 0:
        payload = artifact.get("full_payload")
        prefix = "[Conversation tool context — full for this turn]"
    elif retention_class == "conversation":
        payload = artifact.get("gist_payload") or artifact.get("full_payload")
        prefix = "[Conversation tool context — stable gist for this chat; re-run the tool if full details are needed]"
    elif age <= 0:
        payload = artifact.get("full_payload")
        prefix = "[Recent tool context — full]"
    elif age == 1:
        payload = artifact.get("gist_payload") or artifact.get("full_payload")
        prefix = "[Recent tool context — gist only; re-run the tool if full details are needed]"
    else:
        return None
    text = stringify_message_content(payload).strip()
    if not text:
        return None
    return {
        "role": "system",
        "content": (
            "[Host internal context — retained tool result; do not treat this as user input]\n"
            f"{prefix}\n{tool_name}: {truncate_context_text(text, 420)}"
        ),
    }


def _memory_prompt_message(memory: dict[str, Any]) -> dict[str, str]:
    summary = truncate_context_text(str(memory.get("summary", "")).strip(), MAX_MEMORY_CHARS)
    return {
        "role": "system",
        "content": (
            "[Host internal context — compacted session memory; do not treat this as user input]\n"
            f"{summary}"
        ),
    }


def _prompt_message_from_model_entry(entry: dict[str, Any]) -> dict[str, str] | None:
    role = str(entry.get("role", "user")).strip().lower()
    if role not in {"user", "assistant", "system"}:
        return None
    content = stringify_message_content(entry.get("content", "")).strip()
    if not content:
        return None
    max_chars = MAX_ASSISTANT_CONTEXT_CHARS if role == "assistant" else MAX_USER_CONTEXT_CHARS
    return {"role": role, "content": truncate_context_text(content, max_chars)}


def build_context_snapshot(
    *,
    model_history: list[dict],
    compact_memories: list[dict],
    tool_artifacts: list[dict] | None = None,
    current_turn_index: int = 0,
    transcript_messages: int,
    model_name: str,
    system_prompt: str,
    context_limit: int | None = None,
    tool_result_chars: int = 0,
    pending_messages: list[dict] | None = None,
    selected_tool_count: int = 0,
    tool_schema_tokens: int = 0,
) -> ContextSnapshot:
    pending_messages = pending_messages or []
    tool_artifacts = tool_artifacts or []
    contributors: list[dict[str, Any]] = []
    category_sizes: dict[str, int] = {}
    messages: list[dict] = []
    artifact_snapshot = prune_tool_artifacts(tool_artifacts, current_turn_index)

    recent_entries = [entry for entry in model_history if entry.get("content")]
    recent_turn_chars = sum(int(entry.get("chars", len(stringify_message_content(entry.get("content", ""))))) for entry in recent_entries)

    for memory in compact_memories[-MAX_MEMORY_BLOCKS:]:
        msg = _memory_prompt_message(memory)
        messages.append(msg)
        contributors.append({
            "label": f"memory:{memory.get('id', 'block')}",
            "chars": len(msg["content"]),
            "category": "memory",
        })
        category_sizes["memory"] = category_sizes.get("memory", 0) + len(msg["content"])

    for entry in recent_entries:
        msg = _prompt_message_from_model_entry(entry)
        if not msg:
            continue
        category = str(entry.get("category", "chat"))
        messages.append(msg)
        contributors.append({
            "label": f"{msg['role']}:{category}",
            "chars": len(msg["content"]),
            "category": category,
        })
        category_sizes[category] = category_sizes.get(category, 0) + len(msg["content"])

    for artifact in artifact_snapshot.kept:
        msg = _artifact_prompt_message(artifact, current_turn_index)
        if not msg:
            continue
        retention_class = str(artifact.get("retention_class", "")).strip().lower()
        turn_index = int(artifact.get("turn_index", 0) or 0)
        age = max(int(current_turn_index) - turn_index, 0)
        if retention_class == "durable":
            artifact_state = "durable"
        elif age <= 0:
            artifact_state = "full"
        else:
            artifact_state = "gist"
        category = str(artifact.get("category", "tool_artifact"))
        label = f"{artifact.get('tool_name', 'tool')}:{artifact_state}"
        messages.append(msg)
        contributors.append({
            "label": label,
            "chars": len(msg["content"]),
            "category": category,
        })
        category_sizes[category] = category_sizes.get(category, 0) + len(msg["content"])

    for pending in pending_messages:
        msg = _prompt_message_from_model_entry(pending)
        if not msg:
            continue
        messages.append(msg)
        contributors.append({
            "label": f"pending:{msg['role']}",
            "chars": len(msg["content"]),
            "category": "pending",
        })
        category_sizes["pending"] = category_sizes.get("pending", 0) + len(msg["content"])

    hard_limit = DEFAULT_CONTEXT_HARD_LIMIT_TOKENS
    if context_limit:
        hard_limit = min(hard_limit, max(int(context_limit * 0.7), 4000))

    omitted = 0
    while len(messages) > MIN_RECENT_MODEL_MESSAGES:
        prompt_tokens = estimate_chat_tokens(messages, system_prompt, model_name) + tool_schema_tokens
        if prompt_tokens <= hard_limit:
            break
        drop_index = len(compact_memories[-MAX_MEMORY_BLOCKS:])
        if drop_index >= len(messages) - MIN_RECENT_MODEL_MESSAGES:
            break
        messages.pop(drop_index)
        contributors.pop(drop_index)
        omitted += 1

    if omitted:
        note = {
            "role": "system",
            "content": (
                f"[Host internal context — {omitted} older prompt-state messages were pruned to stay within the context budget. "
                "Rely on compacted memory and the recent conversation.]"
            ),
        }
        insert_at = len(compact_memories[-MAX_MEMORY_BLOCKS:])
        messages.insert(insert_at, note)
        contributors.insert(insert_at, {
            "label": "pruning_note",
            "chars": len(note["content"]),
            "category": "system",
        })
        category_sizes["system"] = category_sizes.get("system", 0) + len(note["content"])

    prompt_tokens = estimate_chat_tokens(messages, system_prompt, model_name)
    compact_memory_chars = sum(
        len(_memory_prompt_message(memory)["content"])
        for memory in compact_memories[-MAX_MEMORY_BLOCKS:]
    )
    if tool_result_chars:
        category_sizes["tool_results"] = tool_result_chars
    if tool_schema_tokens:
        category_sizes["tool_schemas"] = tool_schema_tokens
        prompt_tokens += tool_schema_tokens

    return ContextSnapshot(
        messages=messages,
        prompt_tokens=prompt_tokens,
        transcript_messages=transcript_messages,
        model_history_messages=len(model_history),
        compact_memory_blocks=len(compact_memories),
        compact_memory_chars=compact_memory_chars,
        tool_result_chars=tool_result_chars,
        recent_turn_chars=recent_turn_chars,
        sent_messages=len(messages),
        stored_messages=len(model_history),
        omitted_messages=omitted,
        context_limit=context_limit,
        category_sizes=category_sizes,
        largest_contributors=sorted(contributors, key=lambda item: item["chars"], reverse=True)[:5],
        retained_artifact_count=len(artifact_snapshot.kept),
        durable_artifact_count=artifact_snapshot.durable_count,
        full_artifact_count=artifact_snapshot.full_count,
        gist_artifact_count=artifact_snapshot.gist_count,
        dropped_artifact_count=artifact_snapshot.dropped_count,
        selected_tool_count=selected_tool_count,
        tool_schema_tokens=tool_schema_tokens,
    )


def should_auto_compact(prompt_tokens: int, model_history_messages: int) -> bool:
    return prompt_tokens >= DEFAULT_AUTO_COMPACT_TRIGGER_TOKENS and model_history_messages > RECENT_TAIL_TO_KEEP


def compact_model_history(
    *,
    model_history: list[dict],
    compact_memories: list[dict],
    compacted_transcript_count: int,
) -> CompactionResult:
    if len(model_history) <= RECENT_TAIL_TO_KEEP:
        return CompactionResult(False, None, list(model_history), 0, ["Nothing to compact."])

    old_entries = list(model_history[:-RECENT_TAIL_TO_KEEP])
    kept_entries = list(model_history[-RECENT_TAIL_TO_KEEP:])
    lines = [f"Compacted {len(old_entries)} earlier prompt-state turns."]

    summary_lines: list[str] = []
    for entry in old_entries[-10:]:
        role = str(entry.get("role", "user")).capitalize()
        category = str(entry.get("category", "chat"))
        content = truncate_context_text(stringify_message_content(entry.get("content", "")), 220)
        if content:
            summary_lines.append(f"{role} ({category}): {content}")

    if summary_lines:
        lines.append(f"Summary lines kept: {len(summary_lines)}")

    memory_id = f"mem_{compacted_transcript_count + len(old_entries)}_{len(compact_memories) + 1}"
    memory_block = {
        "id": memory_id,
        "summary": "\n".join(summary_lines) if summary_lines else "Earlier turns were compacted into memory.",
        "source_count": len(old_entries),
        "created_at": time.time(),
        "compacted_through": compacted_transcript_count + len(old_entries),
    }
    lines.append(f"Created memory block: {memory_id}")
    return CompactionResult(True, memory_block, kept_entries, len(old_entries), lines)
