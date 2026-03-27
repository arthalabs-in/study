from __future__ import annotations

import os
import shutil

import pytest

from src.agents.provider import get_provider


pytestmark = pytest.mark.live_provider


def _enabled() -> bool:
    return os.environ.get("RUN_LIVE_PROVIDER_TESTS") == "1"


def _skip_unless(condition: bool, reason: str) -> None:
    if not condition:
        pytest.skip(reason)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_name", "key_env", "model_env", "base_url_env"),
    [
        ("openai", "OPENAI_API_KEY", "LIVE_OPENAI_MODEL", None),
        ("anthropic", "ANTHROPIC_API_KEY", "LIVE_ANTHROPIC_MODEL", None),
        ("gemini", "GEMINI_API_KEY", "LIVE_GEMINI_MODEL", None),
        ("groq", "GROQ_API_KEY", "LIVE_GROQ_MODEL", None),
        ("kimi", "KIMI_API_KEY", "LIVE_KIMI_MODEL", None),
        ("ollama", None, "LIVE_OLLAMA_MODEL", "LIVE_OLLAMA_BASE_URL"),
        ("llamacpp", None, "LIVE_LLAMACPP_MODEL", "LIVE_LLAMACPP_BASE_URL"),
        ("lmstudio", None, "LIVE_LMSTUDIO_MODEL", "LIVE_LMSTUDIO_BASE_URL"),
    ],
)
async def test_live_provider_chat_and_model_listing(provider_name, key_env, model_env, base_url_env) -> None:
    _skip_unless(_enabled(), "Set RUN_LIVE_PROVIDER_TESTS=1 to enable live provider smoke tests.")

    api_key = os.environ.get(key_env) if key_env else None
    if key_env:
        _skip_unless(bool(api_key), f"Missing {key_env}")

    base_url = os.environ.get(base_url_env) if base_url_env else None
    if provider_name in {"ollama", "llamacpp", "lmstudio"} and not base_url:
        base_url = None

    model = os.environ.get(model_env)
    provider = get_provider(provider_name, api_key=api_key, model=model, base_url=base_url)

    models = await provider.get_models_async()
    assert models
    prompt = "Reply with the single word OK."
    result = await provider.chat(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=32,
    )
    assert "ok" in result.lower()


@pytest.mark.asyncio
async def test_live_openai_codex_via_cli() -> None:
    _skip_unless(_enabled(), "Set RUN_LIVE_PROVIDER_TESTS=1 to enable live provider smoke tests.")
    _skip_unless(shutil.which("codex") or shutil.which("codex.cmd"), "Codex CLI is not installed.")
    _skip_unless(os.environ.get("RUN_CODEX_LIVE_TESTS") == "1", "Set RUN_CODEX_LIVE_TESTS=1 to enable Codex OAuth smoke.")

    provider = get_provider("openai-codex", model=os.environ.get("LIVE_CODEX_MODEL"))
    result = await provider.chat(
        messages=[{"role": "user", "content": "Reply with the single word OK."}],
        max_tokens=32,
    )
    assert "ok" in result.lower()
