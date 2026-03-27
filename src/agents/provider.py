"""
Provider Abstraction — unified interface for multiple LLM providers.

Two provider families:
  - AnthropicProvider: Kimi, Anthropic (shared streaming, thinking blocks, tool_use)
  - OpenAIProvider: OpenAI, Gemini, Ollama, llama.cpp (shared OpenAI-compatible API)

All providers expose the same `stream_chat()` interface so the rest of the app
is completely model-agnostic.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from src.context_engine import compact_tool_result


_TOOL_TEXT_LIMIT = 500
_TOOL_LIST_LIMIT = 6
MAX_TOOL_CALL_ROUNDS = 25
_FULL_TOOL_LOOP_RESULT_NAMES = {"generate_flashcards", "generate_quiz", "get_recent_flashcards"}


# ── Provider registry ───────────────────────────────────────────────

PROVIDER_CONFIGS: dict[str, dict] = {
    "kimi": {
        "display_name": "Kimi K2.5",
        "family": "anthropic",
        "base_url": "https://api.kimi.com/coding/",
        "default_model": "kimi-k2.5",
        "env_key": "KIMI_API_KEY",
        "auth_mode": "api_key",
        "supports_thinking": True,
        "supports_tools": True,
    },
    "anthropic": {
        "display_name": "Anthropic (Claude)",
        "family": "anthropic",
        "base_url": None,  # default
        "default_model": "claude-sonnet-4-20250514",
        "env_key": "ANTHROPIC_API_KEY",
        "auth_mode": "api_key",
        "supports_thinking": True,
        "supports_tools": True,
    },
    "openai": {
        "display_name": "OpenAI API",
        "family": "openai",
        "base_url": None,  # default
        "default_model": "gpt-4o",
        "env_key": "OPENAI_API_KEY",
        "auth_mode": "api_key",
        "supports_thinking": False,
        "supports_tools": True,
    },
    "groq": {
        "display_name": "Groq",
        "family": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "env_key": "GROQ_API_KEY",
        "auth_mode": "api_key",
        "supports_thinking": False,
        "supports_tools": True,
    },
    "openai-codex": {
        "display_name": "OpenAI Codex (ChatGPT OAuth)",
        "family": "openai",
        "base_url": None,
        "default_model": "gpt-5.4",
        "env_key": None,
        "auth_mode": "codex_oauth",
        "supports_thinking": False,
        "supports_tools": True,
    },
    "gemini": {
        "display_name": "Google Gemini",
        "family": "openai",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-2.5-flash",
        "env_key": "GEMINI_API_KEY",
        "auth_mode": "api_key",
        "supports_thinking": True,
        "supports_tools": True,
    },
    "ollama": {
        "display_name": "Ollama (Local)",
        "family": "openai",
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3.2",
        "env_key": None,  # no key needed
        "auth_mode": "none",
        "supports_thinking": False,
        "supports_tools": True,
    },
    "llamacpp": {
        "display_name": "llama.cpp (Local)",
        "family": "openai",
        "base_url": "http://localhost:8080/v1",
        "default_model": "local-model",
        "env_key": None,
        "auth_mode": "none",
        "supports_thinking": False,
        "supports_tools": False,
    },
    "lmstudio": {
        "display_name": "LM Studio (Local)",
        "family": "openai",
        "base_url": "http://localhost:1234/v1",
        "default_model": "local-model",
        "env_key": None,
        "auth_mode": "none",
        "supports_thinking": False,
        "supports_tools": True,
    },
}

KNOWN_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-5.4": 400000,
    "gpt-5": 400000,
    "gpt-4.1": 1047576,
    "gpt-4o": 128000,
    "llama-3.3-70b-versatile": 131072,
    "moonshotai/kimi-k2-instruct-0905": 262144,
    "claude-sonnet-4": 200000,
    "claude-3-7-sonnet": 200000,
    "claude-3-5-sonnet": 200000,
    "kimi-k2.5": 128000,
    "gemini-2.5-flash": 1048576,
    "gemini-2.5-pro": 1048576,
    "llama3.2": 131072,
    "local-model": 32768,
}


# ── Base class ──────────────────────────────────────────────────────

class LLMProvider(ABC):
    """Base class for all LLM providers."""

    name: str
    display_name: str
    model: str
    supports_thinking: bool
    supports_tools: bool

    @abstractmethod
    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_executor: Callable | None = None,
        system: str = "",
        max_tokens: int = 16384,
        on_text: Callable | None = None,
        on_thinking: Callable | None = None,
        on_tool_call: Callable | None = None,
        on_tool_result: Callable | None = None,
    ) -> str:
        """Stream a chat completion. Returns the final full text."""
        ...

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_executor: Callable | None = None,
        system: str = "",
        max_tokens: int = 4096,
        on_tool_call: Callable | None = None,
    ) -> str:
        """Non-streaming chat completion. Returns the full text."""
        ...

    def get_models(self) -> list[str]:
        """Return available models (override for dynamic listing)."""
        return [self.model]

    async def get_models_async(self) -> list[str]:
        """Return available models asynchronously."""
        return self.get_models()

    async def get_context_window_async(self) -> int | None:
        """Return model context window when known."""
        return None


# ── Anthropic family (Kimi + Anthropic) ─────────────────────────────

class AnthropicProvider(LLMProvider):
    """Provider for Anthropic-compatible APIs (Kimi, Claude)."""

    def __init__(
        self,
        name: str,
        api_key: str,
        model: str | None = None,
        base_url: str | None = None,
    ):
        cfg = PROVIDER_CONFIGS[name]
        self.name = name
        self.display_name = cfg["display_name"]
        self.model = model or cfg["default_model"]
        self.api_key = api_key or ""
        self.auth_mode = cfg.get("auth_mode", "api_key" if cfg.get("env_key") else "none")
        self.supports_thinking = cfg["supports_thinking"]
        self.supports_tools = cfg["supports_tools"]

        kwargs: dict = {"api_key": api_key}
        if base_url or cfg["base_url"]:
            kwargs["base_url"] = base_url or cfg["base_url"]
        self._client = AsyncAnthropic(**kwargs)

    # ── helpers ──

    @staticmethod
    def _extract_text(content_blocks: list) -> str:
        texts = []
        for block in content_blocks:
            if hasattr(block, "text"):
                texts.append(block.text)
            elif isinstance(block, dict) and block.get("type") == "text":
                texts.append(block["text"])
        return "\n".join(texts)

    @staticmethod
    def _extract_tool_calls(content_blocks: list) -> list[dict]:
        calls = []
        for block in content_blocks:
            if hasattr(block, "type") and block.type == "tool_use":
                calls.append({"id": block.id, "name": block.name, "input": block.input})
            elif isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append({"id": block["id"], "name": block["name"], "input": block["input"]})
        return calls

    async def get_models_async(self) -> list[str]:
        """Fetch available models from Anthropic-compatible providers."""
        try:
            models: list[str] = []
            async for model_info in self._client.models.list(limit=100):
                model_id = getattr(model_info, "id", None)
                if model_id:
                    models.append(model_id)
            if self.model not in models:
                models.append(self.model)
            return sorted(set(models))
        except Exception:
            return [self.model]

    @staticmethod
    def _truncate_for_tool_context(text: str, limit: int = _TOOL_TEXT_LIMIT) -> str:
        text = str(text or "").strip()
        if len(text) <= limit:
            return text
        head = text[: limit // 2].rstrip()
        tail = text[-(limit // 3):].lstrip()
        omitted = max(len(text) - len(head) - len(tail), 0)
        return f"{head} ... [{omitted} chars omitted] ... {tail}"

    @classmethod
    def _compact_tool_result(cls, tool_name: str, result: Any) -> Any:
        return compact_tool_result(tool_name, result)

    @classmethod
    def _tool_result_for_active_loop(cls, tool_name: str, result: Any) -> Any:
        if str(tool_name or "").strip().lower() in _FULL_TOOL_LOOP_RESULT_NAMES:
            return result
        return cls._compact_tool_result(tool_name, result)

    async def get_context_window_async(self) -> int | None:
        model_key = self.model.lower()
        for prefix, limit in KNOWN_CONTEXT_WINDOWS.items():
            if model_key.startswith(prefix):
                return limit
        return None

    # ── streaming ──

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_executor: Callable | None = None,
        system: str = "",
        max_tokens: int = 16384,
        on_text: Callable | None = None,
        on_thinking: Callable | None = None,
        on_tool_call: Callable | None = None,
        on_tool_result: Callable | None = None,
    ) -> str:
        working_messages = []
        system_text = system
        for msg in messages:
            if msg["role"] == "system":
                system_text = msg["content"]
            else:
                working_messages.append(msg)

        full_text = ""
        max_rounds = MAX_TOOL_CALL_ROUNDS

        for _ in range(max_rounds):
            kwargs: dict = {
                "model": self.model,
                "messages": working_messages,
                "max_tokens": max_tokens,
            }
            if system_text:
                kwargs["system"] = system_text
            if tools and self.supports_tools:
                kwargs["tools"] = tools

            # Enable extended thinking if supported
            use_thinking = self.supports_thinking
            if use_thinking:
                kwargs["temperature"] = 1  # required for thinking
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": 8192}

            round_text = ""

            try:
                async with self._client.messages.stream(**kwargs) as stream:
                    async for event in stream:
                        if hasattr(event, "type") and event.type == "content_block_delta":
                            delta = getattr(event, "delta", None)
                            if delta:
                                delta_type = getattr(delta, "type", "")
                                if delta_type == "thinking_delta":
                                    thinking_text = getattr(delta, "thinking", "")
                                    if thinking_text and on_thinking:
                                        on_thinking(thinking_text)
                                elif delta_type == "text_delta":
                                    text = getattr(delta, "text", "")
                                    if text:
                                        round_text += text
                                        full_text += text
                                        if on_text:
                                            on_text(text)
                    response = await stream.get_final_message()
                    if not round_text:
                        final_text = self._extract_text(response.content)
                        if final_text:
                            round_text = final_text
                            full_text += final_text
                            if on_text:
                                on_text(final_text)
            except Exception as e:
                err_str = str(e).lower()
                if "thinking" in err_str or "temperature" in err_str or "not supported" in err_str:
                    kwargs.pop("thinking", None)
                    kwargs.pop("temperature", None)
                    try:
                        async with self._client.messages.stream(**kwargs) as stream:
                            async for event in stream:
                                if hasattr(event, "type") and event.type == "content_block_delta":
                                    delta = getattr(event, "delta", None)
                                    if delta and getattr(delta, "type", "") == "text_delta":
                                        text = getattr(delta, "text", "")
                                        if text:
                                            round_text += text
                                            full_text += text
                                            if on_text:
                                                on_text(text)
                            response = await stream.get_final_message()
                            if not round_text:
                                final_text = self._extract_text(response.content)
                                if final_text:
                                    round_text = final_text
                                    full_text += final_text
                                    if on_text:
                                        on_text(final_text)
                    except Exception:
                        response = await self._client.messages.create(**kwargs)
                        round_text = self._extract_text(response.content)
                        full_text += round_text
                        if on_text and round_text:
                            on_text(round_text)
                else:
                    kwargs.pop("thinking", None)
                    kwargs.pop("temperature", None)
                    response = await self._client.messages.create(**kwargs)
                    round_text = self._extract_text(response.content)
                    full_text += round_text
                    if on_text and round_text:
                        on_text(round_text)

            # Handle tool calls
            if response.stop_reason == "tool_use":
                tool_calls = self._extract_tool_calls(response.content)

                working_messages.append({
                    "role": "assistant",
                    "content": [
                        block.model_dump() if hasattr(block, "model_dump") else block
                        for block in response.content
                    ],
                })

                tool_results = []
                for tc in tool_calls:
                    if on_tool_call:
                        on_tool_call(tc["name"], tc["input"])
                    if tool_executor:
                        result = await tool_executor(tc["name"], tc["input"])
                    else:
                        result = {"error": f"No executor for {tc['name']}"}
                    compact_result = self._tool_result_for_active_loop(tc["name"], result)
                    if on_tool_result:
                        on_tool_result(tc["name"], compact_result)
                    result_str = json.dumps(compact_result, ensure_ascii=False) if isinstance(compact_result, (dict, list)) else str(compact_result)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": result_str,
                    })

                working_messages.append({"role": "user", "content": tool_results})
                continue

            return full_text

        return full_text or "[Max tool-call rounds exceeded]"

    # ── non-streaming ──

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_executor: Callable | None = None,
        system: str = "",
        max_tokens: int = 4096,
        on_tool_call: Callable | None = None,
        on_tool_result: Callable | None = None,
    ) -> str:
        working_messages = []
        system_text = system
        for msg in messages:
            if msg["role"] == "system":
                system_text = msg["content"]
            else:
                working_messages.append(msg)

        full_text = ""
        max_rounds = MAX_TOOL_CALL_ROUNDS

        for _ in range(max_rounds):
            kwargs: dict = {
                "model": self.model,
                "messages": working_messages,
                "max_tokens": max_tokens,
                "temperature": 0.7,
            }
            if system_text:
                kwargs["system"] = system_text
            if tools and self.supports_tools:
                kwargs["tools"] = tools

            response = await self._client.messages.create(**kwargs)
            text = self._extract_text(response.content)
            full_text += text

            if response.stop_reason == "tool_use":
                tool_calls = self._extract_tool_calls(response.content)
                working_messages.append({
                    "role": "assistant",
                    "content": [
                        block.model_dump() if hasattr(block, "model_dump") else block
                        for block in response.content
                    ],
                })
                tool_results = []
                for tc in tool_calls:
                    if on_tool_call:
                        on_tool_call(tc["name"], tc["input"])
                    if tool_executor:
                        result = await tool_executor(tc["name"], tc["input"])
                    else:
                        result = {"error": f"No executor for {tc['name']}"}
                    compact_result = self._tool_result_for_active_loop(tc["name"], result)
                    if on_tool_result:
                        on_tool_result(tc["name"], compact_result)
                    result_str = json.dumps(compact_result, ensure_ascii=False) if isinstance(compact_result, (dict, list)) else str(compact_result)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": result_str,
                    })
                working_messages.append({"role": "user", "content": tool_results})
                continue
            return full_text

        return full_text or "[Max tool-call rounds exceeded]"


# ── OpenAI family (OpenAI + Gemini + Ollama + llama.cpp) ────────────

class OpenAIProvider(LLMProvider):
    """Provider for OpenAI-compatible APIs (OpenAI, Gemini, Ollama, llama.cpp)."""

    def __init__(
        self,
        name: str,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        cfg = PROVIDER_CONFIGS[name]
        self.name = name
        self.display_name = cfg["display_name"]
        self.model = model or cfg["default_model"]
        self.api_key = api_key or ""
        self.auth_mode = cfg.get("auth_mode", "api_key" if cfg.get("env_key") else "none")
        self.supports_thinking = cfg["supports_thinking"]
        self.supports_tools = cfg["supports_tools"]

        kwargs: dict = {"api_key": api_key or "not-needed"}
        url = base_url or cfg["base_url"]
        if url:
            kwargs["base_url"] = url
        self._client = AsyncOpenAI(**kwargs)

    @staticmethod
    def _codex_executable() -> str:
        return os.environ.get("CODEX_EXECUTABLE") or ("codex.cmd" if os.name == "nt" else "codex")

    @staticmethod
    def _codex_models_from_cache() -> list[str]:
        cache_path = Path.home() / ".codex" / "models_cache.json"
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            return []

        models: list[str] = []
        for item in payload.get("models", []):
            slug = item.get("slug")
            if not slug:
                continue
            if item.get("visibility") == "hidden":
                continue
            models.append(slug)
        return sorted(set(models))

    @staticmethod
    def _build_codex_prompt(messages: list[dict], system: str = "") -> str:
        lines = [
            "You are the model backend for a study assistant application.",
            "Answer the user directly and conversationally.",
            "Do not act as an autonomous coding agent.",
            "Do not run commands, inspect the filesystem, or edit files.",
            "Use the conversation below as your only context.",
        ]
        if system.strip():
            lines.extend(["", "System instructions:", system.strip()])

        lines.append("")
        lines.append("Conversation:")
        for msg in messages:
            role = str(msg.get("role", "user")).upper()
            content = msg.get("content", "")
            if isinstance(content, list):
                content_text = json.dumps(content, ensure_ascii=False)
            else:
                content_text = str(content)
            content_text = content_text.strip()
            if not content_text:
                continue
            lines.append(f"{role}: {content_text}")

        lines.extend(["", "ASSISTANT:"])
        return "\n".join(lines)

    @staticmethod
    def _build_codex_tool_prompt(messages: list[dict], tools: list[dict], system: str = "") -> str:
        tool_lines = []
        for tool in tools:
            tool_lines.append(
                json.dumps(
                    {
                        "name": tool.get("name"),
                        "description": tool.get("description", ""),
                        "input_schema": tool.get("input_schema", {}),
                    },
                    ensure_ascii=False,
                )
            )

        lines = [
            "You are the model backend for a study assistant application.",
            "You can ask the host app to run local study tools for you.",
            "Ignore any built-in Codex CLI tools such as shell, apply_patch, or filesystem access.",
            "Those Codex CLI tools are NOT available for this task and must never be mentioned to the user.",
            "The ONLY tools you may use are the study tools listed below via JSON tool_call output.",
            "Return ONLY valid JSON. No markdown fences. No extra prose.",
            '{"type":"tool_call","name":"tool_name","arguments":{...}} means run a tool.',
            '{"type":"final","content":"your answer"} means answer directly.',
            "Never invent tool results.",
            "Use tools before answering when documents, files, notes, or web results are relevant.",
            "When you return final content, make it clean user-facing prose.",
            "Do not expose internal tool calls, JSON, function syntax, or scratchpad steps in the final answer.",
            "If the user asks to load a file, make flashcards, quiz them, or summarize something, you should use the listed study tools instead of refusing.",
        ]
        if system.strip():
            lines.extend(["", "System instructions:", system.strip()])
        if tool_lines:
            lines.extend(["", "Available tools:"])
            lines.extend(tool_lines)

        conversation_lines: list[str] = []
        tool_result_lines: list[str] = []

        lines.append("")
        lines.append("Conversation:")
        for msg in messages:
            role = str(msg.get("role", "user")).strip().lower()
            content = msg.get("content", "")
            if isinstance(content, list):
                content_text = json.dumps(content, ensure_ascii=False).strip()
            else:
                content_text = str(content).strip()
            if not content_text:
                continue
            if role == "tool":
                tool_name = str(msg.get("name", "tool")).strip() or "tool"
                tool_result_lines.append(f"{tool_name}: {content_text}")
                continue
            if role == "assistant":
                parsed = OpenAIProvider._extract_codex_json(content_text)
                if isinstance(parsed, dict) and str(parsed.get("type", "")).strip().lower() == "tool_call":
                    continue
            conversation_lines.append(f"{role.upper()}: {content_text}")

        lines.extend(conversation_lines)
        if tool_result_lines:
            lines.extend(["", "Tool results so far (internal context, do not repeat verbatim unless useful):"])
            lines.extend(tool_result_lines)

        lines.extend(["", "Return one JSON object only."])
        return "\n".join(lines)

    @staticmethod
    def _build_codex_tool_repair_prompt(
        raw_response: str,
        messages: list[dict],
        tools: list[dict],
        system: str = "",
    ) -> str:
        tool_lines = []
        for tool in tools:
            tool_lines.append(
                json.dumps(
                    {
                        "name": tool.get("name"),
                        "description": tool.get("description", ""),
                        "input_schema": tool.get("input_schema", {}),
                    },
                    ensure_ascii=False,
                )
            )

        lines = [
            "You are validating the previous model step for a study assistant application.",
            "The host app has already provided the available study tools and prior tool results.",
            "Your job is to produce the NEXT valid protocol action.",
            "Return ONLY valid JSON. No markdown fences. No extra prose.",
            '{"type":"tool_call","name":"tool_name","arguments":{...}} means continue with a tool.',
            '{"type":"final","content":"your answer"} means the task can now be answered cleanly.',
            "Do not mention Codex CLI tools, shell, apply_patch, or filesystem access.",
            "Do not expose JSON, tool syntax, or internal reasoning in the final answer.",
            "If the previous response was a refusal, protocol error, or premature answer, correct it by returning the proper next action.",
        ]
        if system.strip():
            lines.extend(["", "System instructions:", system.strip()])
        if tool_lines:
            lines.extend(["", "Available tools:"])
            lines.extend(tool_lines)

        conversation_lines: list[str] = []
        tool_result_lines: list[str] = []
        for msg in messages:
            role = str(msg.get("role", "user")).strip().lower()
            content = msg.get("content", "")
            if isinstance(content, list):
                content_text = json.dumps(content, ensure_ascii=False).strip()
            else:
                content_text = str(content).strip()
            if not content_text:
                continue
            if role == "tool":
                tool_name = str(msg.get("name", "tool")).strip() or "tool"
                tool_result_lines.append(f"{tool_name}: {content_text}")
                continue
            conversation_lines.append(f"{role.upper()}: {content_text}")

        lines.extend(["", "Conversation so far:"])
        lines.extend(conversation_lines)
        if tool_result_lines:
            lines.extend(["", "Tool results so far:"])
            lines.extend(tool_result_lines)

        lines.extend(
            [
                "",
                "Previous invalid or questionable assistant response:",
                raw_response.strip() or "(empty)",
                "",
                "Return one JSON object only.",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _extract_codex_json(raw: str) -> dict[str, Any] | None:
        text = (raw or "").strip()
        if not text:
            return None

        candidates = [text]
        if "```" in text:
            for chunk in text.split("```"):
                cleaned = chunk.strip()
                if cleaned.lower().startswith("json"):
                    cleaned = cleaned[4:].strip()
                if cleaned:
                    candidates.append(cleaned)
        if "{" in text and "}" in text:
            candidates.append(text[text.find("{"): text.rfind("}") + 1])

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    @staticmethod
    def _extract_reasoning_text(payload: Any) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            return payload
        if isinstance(payload, list):
            parts = [OpenAIProvider._extract_reasoning_text(item) for item in payload]
            return "".join(part for part in parts if part)
        if isinstance(payload, dict):
            for key in ("text", "content", "summary"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    return value
            return ""

        text = getattr(payload, "text", None)
        if isinstance(text, str) and text:
            return text
        content = getattr(payload, "content", None)
        if isinstance(content, str) and content:
            return content
        summary = getattr(payload, "summary", None)
        if isinstance(summary, str) and summary:
            return summary
        if summary is not None:
            return OpenAIProvider._extract_reasoning_text(summary)
        return ""

    async def get_context_window_async(self) -> int | None:
        if self.auth_mode == "codex_oauth":
            model_key = self.model.lower()
            for prefix, limit in KNOWN_CONTEXT_WINDOWS.items():
                if model_key.startswith(prefix):
                    return limit
            return None

        try:
            info = await self._client.models.retrieve(self.model)
            for attr in ("context_window", "input_token_limit", "inputTokenLimit", "context_length"):
                value = getattr(info, attr, None)
                if value is None and isinstance(info, dict):
                    value = info.get(attr)
                if isinstance(value, int) and value > 0:
                    return value
        except Exception:
            pass

        model_key = self.model.lower()
        for prefix, limit in KNOWN_CONTEXT_WINDOWS.items():
            if model_key.startswith(prefix):
                return limit
        return None

    async def _run_codex_prompt_impl(self, prompt: str, on_text: Callable | None = None) -> str:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as tmp:
            output_path = tmp.name

        try:
            try:
                process = await asyncio.create_subprocess_exec(
                    self._codex_executable(),
                    "exec",
                    "--skip-git-repo-check",
                    "--ephemeral",
                    "--sandbox",
                    "read-only",
                    "--color",
                    "never",
                    "--model",
                    self.model,
                    "--output-last-message",
                    output_path,
                    "-",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise RuntimeError(
                    f"Codex CLI executable was not found ({self._codex_executable()}). Install Codex or set CODEX_EXECUTABLE."
                ) from exc

            stdout_bytes, stderr_bytes = await process.communicate(prompt.encode("utf-8"))

            response_text = ""
            output_file = Path(output_path)
            if output_file.exists():
                response_text = output_file.read_text(encoding="utf-8").strip()

            if response_text:
                if on_text:
                    on_text(response_text)
                return response_text

            stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
            stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
            detail = stderr_text or stdout_text or "Codex CLI request failed."
            raise RuntimeError(detail)
        finally:
            try:
                Path(output_path).unlink(missing_ok=True)
            except Exception:
                pass


    async def _run_via_codex_cli(
        self,
        messages: list[dict],
        system: str = "",
        on_text: Callable | None = None,
    ) -> str:
        prompt = self._build_codex_prompt(messages, system=system)
        return await self._run_codex_prompt_impl(prompt, on_text=on_text)

    async def _run_via_codex_cli_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_executor: Callable | None = None,
        system: str = "",
        on_text: Callable | None = None,
        on_tool_call: Callable | None = None,
        on_tool_result: Callable | None = None,
    ) -> str:
        working_messages = [dict(msg) for msg in messages if msg.get("role") in {"user", "assistant", "system", "tool"}]

        async def review_final_content(content: str) -> dict[str, Any] | None:
            review_prompt = self._build_codex_tool_repair_prompt(
                json.dumps({"type": "final", "content": content}, ensure_ascii=False),
                messages=working_messages,
                tools=tools,
                system=system,
            )
            reviewed_raw = await self._run_codex_prompt_impl(review_prompt, on_text=None)
            reviewed_action = self._extract_codex_json(reviewed_raw)
            if isinstance(reviewed_action, dict) and str(reviewed_action.get("type", "")).strip().lower() in {"tool_call", "final"}:
                return reviewed_action
            return None

        async def resolve_action(raw_response: str) -> dict[str, Any] | None:
            action = self._extract_codex_json(raw_response)
            if isinstance(action, dict) and str(action.get("type", "")).strip().lower() in {"tool_call", "final"}:
                return action

            repair_prompt = self._build_codex_tool_repair_prompt(
                raw_response,
                messages=working_messages,
                tools=tools,
                system=system,
            )
            repaired_raw = await self._run_codex_prompt_impl(repair_prompt, on_text=None)
            repaired_action = self._extract_codex_json(repaired_raw)
            if isinstance(repaired_action, dict) and str(repaired_action.get("type", "")).strip().lower() in {"tool_call", "final"}:
                return repaired_action
            return None

        for _ in range(MAX_TOOL_CALL_ROUNDS):
            prompt = self._build_codex_tool_prompt(working_messages, tools=tools, system=system)
            raw = await self._run_codex_prompt_impl(prompt, on_text=None)
            action = await resolve_action(raw)

            if not action:
                if on_text:
                    on_text(raw)
                return raw

            action_type = str(action.get("type", "")).strip().lower()
            if action_type == "final":
                content = str(action.get("content", "")).strip() or raw
                reviewed_action = await review_final_content(content)
                if reviewed_action and str(reviewed_action.get("type", "")).strip().lower() == "tool_call":
                    action = reviewed_action
                    action_type = "tool_call"
                else:
                    if reviewed_action and str(reviewed_action.get("type", "")).strip().lower() == "final":
                        content = str(reviewed_action.get("content", "")).strip() or content
                    if on_text:
                        on_text(content)
                    return content

            if action_type != "tool_call":
                if on_text:
                    on_text(raw)
                return raw

            name = str(action.get("name", "")).strip()
            arguments = action.get("arguments", {})
            if not name:
                if on_text:
                    on_text(raw)
                return raw
            if not isinstance(arguments, dict):
                arguments = {}

            if on_tool_call:
                on_tool_call(name, arguments)
            if tool_executor:
                result = await tool_executor(name, arguments)
            else:
                result = {"error": f"No executor for {name}"}

            compact_result = AnthropicProvider._tool_result_for_active_loop(name, result)
            if on_tool_result:
                on_tool_result(name, compact_result)
            result_str = json.dumps(compact_result, ensure_ascii=False) if isinstance(compact_result, (dict, list)) else str(compact_result)
            working_messages.append({
                "role": "tool",
                "name": name,
                "content": result_str,
            })

        fallback = await self._run_via_codex_cli(messages=working_messages, system=system, on_text=None)
        if on_text:
            on_text(fallback)
        return fallback

    def _convert_tools_to_openai(self, tools: list[dict]) -> list[dict]:
        """Convert Anthropic-format tool schema to OpenAI function calling format."""
        openai_tools = []
        for tool in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            })
        return openai_tools

    def _convert_tools_to_responses(self, tools: list[dict]) -> list[dict]:
        """Convert Anthropic-format tool schema to Responses API function tools."""
        response_tools = []
        for tool in tools:
            response_tools.append({
                "type": "function",
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {}),
                "strict": False,
            })
        return response_tools

    def _convert_tools_to_gemini(self, tools: list[dict]) -> list[dict]:
        declarations = []
        for tool in tools:
            declarations.append({
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            })
        return [{"function_declarations": declarations}] if declarations else []

    def _build_gemini_contents(self, messages: list[dict]) -> list[dict]:
        contents: list[dict] = []
        for msg in messages:
            role = str(msg.get("role", "user")).strip().lower()
            content = str(msg.get("content", "")).strip()
            if not content:
                continue
            if role == "assistant":
                contents.append({"role": "model", "parts": [{"text": content}]})
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": content}]})
        return contents

    @staticmethod
    def _normalize_gemini_model_name(model: str) -> str:
        normalized = str(model or "").strip()
        while normalized.startswith("models/"):
            normalized = normalized[len("models/"):]
        return normalized

    async def _gemini_generate_content(
        self,
        *,
        contents: list[dict],
        tools: list[dict] | None = None,
        system: str = "",
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        if tools:
            body["tools"] = self._convert_tools_to_gemini(tools)
        if system.strip():
            body["system_instruction"] = {"parts": [{"text": system.strip()}]}

        model_name = self._normalize_gemini_model_name(self.model)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model_name, safe='')}:generateContent?key={urllib.parse.quote(self.api_key)}"
        data = json.dumps(body).encode("utf-8")

        def _post() -> dict[str, Any]:
            request = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    payload = response.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(detail or str(exc)) from exc
            parsed = json.loads(payload)
            if not isinstance(parsed, dict):
                raise RuntimeError("Unexpected Gemini response payload.")
            return parsed

        return await asyncio.to_thread(_post)

    @staticmethod
    def _extract_gemini_candidate(response: dict[str, Any]) -> dict[str, Any]:
        candidates = response.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return {}
        candidate = candidates[0]
        return candidate if isinstance(candidate, dict) else {}

    @staticmethod
    def _extract_gemini_text(candidate: dict[str, Any]) -> str:
        content = candidate.get("content")
        if not isinstance(content, dict):
            return ""
        parts = content.get("parts")
        if not isinstance(parts, list):
            return ""
        texts: list[str] = []
        for part in parts:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text:
                    texts.append(text)
        return "".join(texts)

    @staticmethod
    def _extract_gemini_function_calls(candidate: dict[str, Any]) -> list[dict[str, Any]]:
        content = candidate.get("content")
        if not isinstance(content, dict):
            return []
        parts = content.get("parts")
        if not isinstance(parts, list):
            return []
        calls: list[dict[str, Any]] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            fn_call = part.get("functionCall")
            if not isinstance(fn_call, dict):
                continue
            name = str(fn_call.get("name", "")).strip()
            args = fn_call.get("args", {})
            if name:
                calls.append({"name": name, "args": args if isinstance(args, dict) else {}})
        return calls

    async def _run_via_gemini_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_executor: Callable | None = None,
        system: str = "",
        max_tokens: int = 4096,
        on_text: Callable | None = None,
        on_tool_call: Callable | None = None,
        on_tool_result: Callable | None = None,
    ) -> str:
        contents = self._build_gemini_contents(messages)
        full_text = ""

        for _ in range(MAX_TOOL_CALL_ROUNDS):
            response = await self._gemini_generate_content(
                contents=contents,
                tools=tools,
                system=system,
                max_tokens=max_tokens,
            )
            candidate = self._extract_gemini_candidate(response)
            candidate_content = candidate.get("content")
            if isinstance(candidate_content, dict):
                contents.append(candidate_content)

            tool_calls = self._extract_gemini_function_calls(candidate)
            if tool_calls:
                function_response_parts: list[dict[str, Any]] = []
                for tool_call in tool_calls:
                    fn_name = tool_call["name"]
                    fn_args = tool_call["args"]
                    if on_tool_call:
                        on_tool_call(fn_name, fn_args)
                    if tool_executor:
                        result = await tool_executor(fn_name, fn_args)
                    else:
                        result = {"error": f"No executor for {fn_name}"}
                    compact_result = AnthropicProvider._tool_result_for_active_loop(fn_name, result)
                    if on_tool_result:
                        on_tool_result(fn_name, compact_result)
                    function_response_parts.append({
                        "functionResponse": {
                            "name": fn_name,
                            "response": {"result": compact_result},
                        }
                    })
                contents.append({"role": "user", "parts": function_response_parts})
                continue

            text = self._extract_gemini_text(candidate)
            if text and on_text:
                on_text(text)
            return text

        return full_text or "[Max tool-call rounds exceeded]"

    def _build_responses_input(self, messages: list[dict], system: str = "") -> tuple[str, list[dict]]:
        instructions = system
        input_items: list[dict] = []
        assistant_index = 0
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "system":
                instructions = str(content)
                continue
            if role == "assistant":
                input_items.append({
                    "type": "message",
                    "id": f"assistant_{assistant_index}",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": str(content), "annotations": []}],
                })
                assistant_index += 1
                continue
            if role in {"user", "developer"}:
                input_items.append({
                    "type": "message",
                    "role": role,
                    "content": [{"type": "input_text", "text": str(content)}],
                })
        return instructions, input_items

    @staticmethod
    def _extract_response_text(response) -> str:
        if hasattr(response, "output_text") and response.output_text:
            return str(response.output_text)
        texts: list[str] = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) != "message":
                continue
            for content in getattr(item, "content", []) or []:
                if getattr(content, "type", None) == "output_text":
                    texts.append(getattr(content, "text", ""))
        return "\n".join(text for text in texts if text)

    @staticmethod
    def _extract_response_tool_calls(response) -> list[dict]:
        tool_calls: list[dict] = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) != "function_call":
                continue
            tool_calls.append({
                "call_id": getattr(item, "call_id", ""),
                "name": getattr(item, "name", ""),
                "arguments": getattr(item, "arguments", "") or "",
            })
        return tool_calls

    async def _stream_via_responses(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_executor: Callable | None = None,
        system: str = "",
        max_tokens: int = 16384,
        on_text: Callable | None = None,
        on_tool_call: Callable | None = None,
        on_tool_result: Callable | None = None,
    ) -> str:
        instructions, base_input = self._build_responses_input(messages, system=system)
        full_text = ""
        previous_response_id: str | None = None
        pending_input: list[dict] = base_input
        response_tools = self._convert_tools_to_responses(tools) if tools and self.supports_tools else None

        for _ in range(MAX_TOOL_CALL_ROUNDS):
            kwargs: dict[str, Any] = {
                "model": self.model,
                "input": pending_input,
                "max_output_tokens": max_tokens,
            }
            if instructions:
                kwargs["instructions"] = instructions
            if previous_response_id:
                kwargs["previous_response_id"] = previous_response_id
            if response_tools:
                kwargs["tools"] = response_tools
                kwargs["parallel_tool_calls"] = True

            round_text = ""
            try:
                async with self._client.responses.stream(**kwargs) as stream:
                    async for event in stream:
                        if event.type == "response.output_text.delta":
                            delta_text = getattr(event, "delta", "")
                            if delta_text:
                                round_text += delta_text
                                full_text += delta_text
                                if on_text:
                                    on_text(delta_text)
                    response = await stream.get_final_response()
                    if not round_text:
                        final_text = self._extract_response_text(response)
                        if final_text:
                            round_text = final_text
                            full_text += final_text
                            if on_text:
                                on_text(final_text)
            except Exception:
                response = await self._client.responses.create(**kwargs)
                round_text = self._extract_response_text(response)
                if round_text:
                    full_text += round_text
                    if on_text:
                        on_text(round_text)

            previous_response_id = getattr(response, "id", previous_response_id)
            tool_calls = self._extract_response_tool_calls(response)
            if not tool_calls:
                return full_text or self._extract_response_text(response)

            pending_input = []
            for tool_call in tool_calls:
                try:
                    fn_args = json.loads(tool_call["arguments"]) if tool_call["arguments"] else {}
                except json.JSONDecodeError:
                    fn_args = {}
                if on_tool_call:
                    on_tool_call(tool_call["name"], fn_args)
                if tool_executor:
                    result = await tool_executor(tool_call["name"], fn_args)
                else:
                    result = {"error": f"No executor for {tool_call['name']}"}
                compact_result = AnthropicProvider._tool_result_for_active_loop(tool_call["name"], result)
                if on_tool_result:
                    on_tool_result(tool_call["name"], compact_result)
                result_str = json.dumps(compact_result) if isinstance(compact_result, (dict, list)) else str(compact_result)
                pending_input.append({
                    "type": "function_call_output",
                    "call_id": tool_call["call_id"],
                    "output": result_str,
                })

        return full_text or "[Max tool-call rounds exceeded]"

    async def _chat_via_responses(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_executor: Callable | None = None,
        system: str = "",
        max_tokens: int = 4096,
        on_tool_call: Callable | None = None,
        on_tool_result: Callable | None = None,
    ) -> str:
        return await self._stream_via_responses(
            messages=messages,
            tools=tools,
            tool_executor=tool_executor,
            system=system,
            max_tokens=max_tokens,
            on_text=None,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
        )

    async def get_models_async(self) -> list[str]:
        """Fetch available models from the provider."""
        if self.auth_mode == "codex_oauth":
            models = self._codex_models_from_cache()
            if self.model not in models:
                models.append(self.model)
            return models or [self.model]

        try:
            response = await self._client.models.list()
            models = sorted({m.id for m in response.data if getattr(m, "id", None)})
            if self.model not in models:
                models.append(self.model)
            return models
        except Exception:
            return [self.model]

    # ── streaming ──

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_executor: Callable | None = None,
        system: str = "",
        max_tokens: int = 16384,
        on_text: Callable | None = None,
        on_thinking: Callable | None = None,
        on_tool_call: Callable | None = None,
        on_tool_result: Callable | None = None,
    ) -> str:
        if self.auth_mode == "codex_oauth":
            if tools and self.supports_tools:
                codex_kwargs = {
                    "messages": messages,
                    "tools": tools,
                    "tool_executor": tool_executor,
                    "system": system,
                    "on_text": on_text,
                    "on_tool_call": on_tool_call,
                }
                if on_tool_result:
                    codex_kwargs["on_tool_result"] = on_tool_result
                return await self._run_via_codex_cli_tools(**codex_kwargs)
            return await self._run_via_codex_cli(
                messages=messages,
                system=system,
                on_text=on_text,
            )
        if self.name == "gemini" and tools and self.supports_tools:
            return await self._run_via_gemini_tools(
                messages=messages,
                tools=tools,
                tool_executor=tool_executor,
                system=system,
                max_tokens=max_tokens,
                on_text=on_text,
                on_tool_call=on_tool_call,
                on_tool_result=on_tool_result,
            )

        # Build OpenAI-format messages
        working_messages = []
        if system:
            working_messages.append({"role": "system", "content": system})
        for msg in messages:
            if msg["role"] == "system":
                if working_messages and working_messages[0].get("role") == "system":
                    working_messages[0] = {"role": "system", "content": msg["content"]}
                else:
                    working_messages.insert(0, {"role": "system", "content": msg["content"]})
            elif msg["role"] in ("user", "assistant"):
                working_messages.append(msg)

        full_text = ""
        max_rounds = MAX_TOOL_CALL_ROUNDS
        openai_tools = self._convert_tools_to_openai(tools) if tools and self.supports_tools else None

        for _ in range(max_rounds):
            kwargs: dict = {
                "model": self.model,
                "messages": working_messages,
                "max_tokens": max_tokens,
                "stream": True,
            }
            if openai_tools:
                kwargs["tools"] = openai_tools

            round_text = ""
            tool_calls_accum: dict[int, dict] = {}  # index -> {id, name, arguments}

            try:
                stream = await self._client.chat.completions.create(**kwargs)

                async for chunk in stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if not delta:
                        continue

                    # Text content
                    if delta.content:
                        round_text += delta.content
                        full_text += delta.content
                        if on_text:
                            on_text(delta.content)

                    # Thinking / reasoning summaries across OpenAI-compatible providers
                    reasoning_text = self._extract_reasoning_text(getattr(delta, "reasoning_content", None))
                    if not reasoning_text:
                        reasoning_text = self._extract_reasoning_text(getattr(delta, "reasoning", None))
                    if not reasoning_text:
                        reasoning_text = self._extract_reasoning_text(getattr(delta, "summary", None))
                    if reasoning_text and on_thinking:
                        on_thinking(reasoning_text)

                    # Tool calls (streamed incrementally)
                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tool_calls_accum:
                                tool_calls_accum[idx] = {
                                    "id": tc_delta.id or "",
                                    "name": tc_delta.function.name or "" if tc_delta.function else "",
                                    "arguments": "",
                                }
                            if tc_delta.id:
                                tool_calls_accum[idx]["id"] = tc_delta.id
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    tool_calls_accum[idx]["name"] = tc_delta.function.name
                                if tc_delta.function.arguments:
                                    tool_calls_accum[idx]["arguments"] += tc_delta.function.arguments

            except Exception as e:
                # Non-streaming fallback
                kwargs["stream"] = False
                response = await self._client.chat.completions.create(**kwargs)
                msg = response.choices[0].message
                if msg.content:
                    round_text = msg.content
                    full_text += round_text
                    if on_text:
                        on_text(round_text)
                if msg.tool_calls:
                    for i, tc in enumerate(msg.tool_calls):
                        tool_calls_accum[i] = {
                            "id": tc.id,
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }

            # Handle tool calls
            if tool_calls_accum:
                # Build assistant message with tool calls
                assistant_tool_calls = []
                for idx in sorted(tool_calls_accum.keys()):
                    tc = tool_calls_accum[idx]
                    assistant_tool_calls.append({
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["arguments"],
                        },
                    })

                working_messages.append({
                    "role": "assistant",
                    "content": round_text or None,
                    "tool_calls": assistant_tool_calls,
                })

                for tc_data in tool_calls_accum.values():
                    fn_name = tc_data["name"]
                    try:
                        fn_args = json.loads(tc_data["arguments"]) if tc_data["arguments"] else {}
                    except json.JSONDecodeError:
                        fn_args = {}

                    if on_tool_call:
                        on_tool_call(fn_name, fn_args)

                    if tool_executor:
                        result = await tool_executor(fn_name, fn_args)
                    else:
                        result = {"error": f"No executor for {fn_name}"}

                    compact_result = AnthropicProvider._tool_result_for_active_loop(fn_name, result)
                    if on_tool_result:
                        on_tool_result(fn_name, compact_result)
                    result_str = json.dumps(compact_result, ensure_ascii=False) if isinstance(compact_result, (dict, list)) else str(compact_result)
                    working_messages.append({
                        "role": "tool",
                        "tool_call_id": tc_data["id"],
                        "content": result_str,
                    })

                continue

            return full_text

        return full_text or "[Max tool-call rounds exceeded]"

    # ── non-streaming ──

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_executor: Callable | None = None,
        system: str = "",
        max_tokens: int = 4096,
        on_tool_call: Callable | None = None,
        on_tool_result: Callable | None = None,
    ) -> str:
        if self.auth_mode == "codex_oauth":
            if tools and self.supports_tools:
                codex_kwargs = {
                    "messages": messages,
                    "tools": tools,
                    "tool_executor": tool_executor,
                    "system": system,
                    "on_text": None,
                    "on_tool_call": on_tool_call,
                }
                if on_tool_result:
                    codex_kwargs["on_tool_result"] = on_tool_result
                return await self._run_via_codex_cli_tools(**codex_kwargs)
            return await self._run_via_codex_cli(
                messages=messages,
                system=system,
                on_text=None,
            )
        if self.name == "gemini" and tools and self.supports_tools:
            return await self._run_via_gemini_tools(
                messages=messages,
                tools=tools,
                tool_executor=tool_executor,
                system=system,
                max_tokens=max_tokens,
                on_text=None,
                on_tool_call=on_tool_call,
                on_tool_result=on_tool_result,
            )

        working_messages = []
        if system:
            working_messages.append({"role": "system", "content": system})
        for msg in messages:
            if msg["role"] in ("user", "assistant"):
                working_messages.append(msg)

        full_text = ""
        max_rounds = MAX_TOOL_CALL_ROUNDS
        openai_tools = self._convert_tools_to_openai(tools) if tools and self.supports_tools else None

        for _ in range(max_rounds):
            kwargs: dict = {
                "model": self.model,
                "messages": working_messages,
                "max_tokens": max_tokens,
            }
            if openai_tools:
                kwargs["tools"] = openai_tools

            response = await self._client.chat.completions.create(**kwargs)
            msg = response.choices[0].message

            if msg.content:
                full_text += msg.content

            if msg.tool_calls:
                working_messages.append({
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                        for tc in msg.tool_calls
                    ],
                })
                for tc in msg.tool_calls:
                    fn_name = tc.function.name
                    try:
                        fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    except json.JSONDecodeError:
                        fn_args = {}
                    if on_tool_call:
                        on_tool_call(fn_name, fn_args)
                    if tool_executor:
                        result = await tool_executor(fn_name, fn_args)
                    else:
                        result = {"error": f"No executor for {fn_name}"}
                    compact_result = AnthropicProvider._tool_result_for_active_loop(fn_name, result)
                    if on_tool_result:
                        on_tool_result(fn_name, compact_result)
                    result_str = json.dumps(compact_result, ensure_ascii=False) if isinstance(compact_result, (dict, list)) else str(compact_result)
                    working_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_str,
                    })
                continue

            return full_text

        return full_text or "[Max tool-call rounds exceeded]"


# ── Factory ─────────────────────────────────────────────────────────

def get_provider(
    name: str,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> LLMProvider:
    """Create a provider by name."""
    if name not in PROVIDER_CONFIGS:
        raise ValueError(f"Unknown provider: {name}. Available: {', '.join(PROVIDER_CONFIGS.keys())}")

    cfg = PROVIDER_CONFIGS[name]
    key = api_key or (os.environ.get(cfg["env_key"]) if cfg["env_key"] else None)

    if cfg["family"] == "anthropic":
        if not key:
            raise ValueError(f"No API key for {name}. Set {cfg['env_key']} or pass api_key.")
        return AnthropicProvider(name=name, api_key=key, model=model, base_url=base_url)
    else:
        return OpenAIProvider(name=name, api_key=key, model=model, base_url=base_url)


def list_providers() -> list[dict]:
    """Return info about all available providers."""
    return [
        {
            "name": name,
            "display_name": cfg["display_name"],
            "family": cfg["family"],
            "default_model": cfg["default_model"],
            "needs_key": cfg["auth_mode"] == "api_key",
            "auth_mode": cfg.get("auth_mode", "api_key" if cfg["env_key"] is not None else "none"),
        }
        for name, cfg in PROVIDER_CONFIGS.items()
    ]


