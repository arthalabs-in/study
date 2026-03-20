"""
Model Client — backward-compatible wrapper around the provider abstraction.

This replaces the old kimi_client.py. All provider-specific logic is in provider.py.
This module provides convenience functions that match the old API signature
for minimal refactor in existing code.
"""

from __future__ import annotations

from typing import Callable

from src.agents.provider import (
    LLMProvider,
    get_provider,
    list_providers,
    PROVIDER_CONFIGS,
)


# Re-export for backward compatibility
__all__ = [
    "get_provider",
    "list_providers",
    "LLMProvider",
    "PROVIDER_CONFIGS",
    "create_provider",
    "stream_chat",
    "chat",
]


def create_provider(
    provider_name: str = "kimi",
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> LLMProvider:
    """Create a provider instance. Convenience wrapper around get_provider."""
    return get_provider(provider_name, api_key=api_key, model=model, base_url=base_url)


async def stream_chat(
    provider: LLMProvider,
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
    """Stream chat completion via the given provider."""
    return await provider.stream_chat(
        messages=messages,
        tools=tools,
        tool_executor=tool_executor,
        system=system,
        max_tokens=max_tokens,
        on_text=on_text,
        on_thinking=on_thinking,
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
    )


async def chat(
    provider: LLMProvider,
    messages: list[dict],
    tools: list[dict] | None = None,
    tool_executor: Callable | None = None,
    system: str = "",
    max_tokens: int = 4096,
    on_tool_call: Callable | None = None,
    on_tool_result: Callable | None = None,
) -> str:
    """Non-streaming chat completion via the given provider."""
    return await provider.chat(
        messages=messages,
        tools=tools,
        tool_executor=tool_executor,
        system=system,
        max_tokens=max_tokens,
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
    )
