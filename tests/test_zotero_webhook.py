from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

import src.zotero_webhook as zotero_webhook
from src.zotero_webhook import ZoteroWebhookServer


def test_zotero_webhook_accepts_local_post_and_rejects_bad_path() -> None:
    events: list[dict] = []
    server = ZoteroWebhookServer(secret="testsecret", port=0, on_event=events.append)
    ok, url = server.start()
    assert ok is True
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps({"event": "item-updated"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            assert resp.status == 202
        assert events[-1]["event"] == "item-updated"

        bad = urllib.request.Request(
            url.replace("testsecret", "wrong"),
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(bad, timeout=3)
            assert False, "expected HTTPError"
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.stop()


def test_zotero_webhook_rejects_wrong_content_type_and_invalid_schema() -> None:
    events: list[dict] = []
    server = ZoteroWebhookServer(secret="testsecret", port=0, on_event=events.append)
    ok, url = server.start()
    assert ok is True
    try:
        wrong_type = urllib.request.Request(
            url,
            data=b'{"event":"item-updated"}',
            headers={"Content-Type": "text/plain"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(wrong_type, timeout=3)
        assert excinfo.value.code == 415

        bad_schema = urllib.request.Request(
            url,
            data=json.dumps({"source": "zotero"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(bad_schema, timeout=3)
        assert excinfo.value.code == 400
        assert events == []
    finally:
        server.stop()


def test_zotero_webhook_deduplicates_recent_replays() -> None:
    events: list[dict] = []
    server = ZoteroWebhookServer(secret="testsecret", port=0, on_event=events.append)
    ok, url = server.start()
    assert ok is True
    try:
        payload = json.dumps({"event": "item-updated", "source": "zotero"}).encode("utf-8")
        for _ in range(2):
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                assert resp.status == 202
        assert len(events) == 1
    finally:
        server.stop()


def test_zotero_webhook_rate_limits_bursts(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[dict] = []
    monkeypatch.setattr(zotero_webhook, "MAX_EVENTS_PER_WINDOW", 1)
    server = ZoteroWebhookServer(secret="testsecret", port=0, on_event=events.append)
    ok, url = server.start()
    assert ok is True
    try:
        first = urllib.request.Request(
            url,
            data=json.dumps({"event": "item-updated", "source": "zotero"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(first, timeout=3) as resp:
            assert resp.status == 202

        second = urllib.request.Request(
            url,
            data=json.dumps({"event": "item-created", "source": "zotero"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(second, timeout=3)
        assert excinfo.value.code == 429
        assert len(events) == 1
    finally:
        server.stop()
