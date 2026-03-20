from __future__ import annotations

import runpy
from types import SimpleNamespace

import src.agents.model_client as model_client
import src.app as app_module


class FakeProvider:
    def __init__(self):
        self.calls = []

    async def stream_chat(self, **kwargs):
        self.calls.append(("stream", kwargs))
        return "streamed"

    async def chat(self, **kwargs):
        self.calls.append(("chat", kwargs))
        return "done"


def test_create_provider_delegates(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(
        model_client,
        "get_provider",
        lambda provider_name, api_key=None, model=None, base_url=None: captured.update(
            {
                "provider_name": provider_name,
                "api_key": api_key,
                "model": model,
                "base_url": base_url,
            }
        )
        or "provider",
    )
    assert model_client.create_provider("openai", api_key="k", model="m", base_url="u") == "provider"
    assert captured == {"provider_name": "openai", "api_key": "k", "model": "m", "base_url": "u"}


async def test_stream_chat_and_chat_delegate() -> None:
    provider = FakeProvider()
    assert await model_client.stream_chat(provider, [{"role": "user", "content": "hi"}], system="sys") == "streamed"
    assert await model_client.chat(provider, [{"role": "user", "content": "hi"}], system="sys") == "done"
    assert provider.calls[0][0] == "stream"
    assert provider.calls[1][0] == "chat"


def test_main_module_invokes_app_main(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(app_module, "main", lambda: calls.append("called"))
    runpy.run_module("src.__main__", run_name="__main__")
    assert calls == ["called"]
