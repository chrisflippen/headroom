"""End to end: long-session trim through the real Anthropic request path.

A conversation past the aging trigger goes through ``POST /v1/messages``.
The body the proxy forwards upstream must carry stubs for the old tool
results, leave the newest ones untouched, keep every original retrievable
from the CCR store under the stub's hash, and the PERF line must say how
much was aged. Thresholds are lowered through the module's own env vars so
the fixture conversation stays small.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from headroom.cache.compression_store import CompressionStore
from headroom.proxy.outcome import RequestOutcome, emit_request_outcome
from headroom.proxy.server import ProxyConfig, create_app

_HASH_RE = re.compile(r"Retrieve original: hash=([0-9a-f]+)")


class _CapturingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def messages(self) -> list[str]:
        return [record.getMessage() for record in self.records]


@pytest.fixture
def proxy_log_capture() -> Iterator[_CapturingHandler]:
    """``headroom.proxy`` has ``propagate = False`` once file logging is set
    up, so ``caplog`` (on the root) never sees it — attach our own handler,
    the same way tests/test_proxy_response_cache_replay.py does."""
    target = logging.getLogger("headroom.proxy")
    handler = _CapturingHandler()
    previous_level = target.level
    target.addHandler(handler)
    target.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        target.removeHandler(handler)
        target.setLevel(previous_level)


def _client() -> TestClient:
    config = ProxyConfig(
        optimize=False,
        cache_enabled=False,
        rate_limit_enabled=False,
        cost_tracking_enabled=False,
        log_requests=False,
        ccr_inject_tool=False,
        ccr_inject_system_instructions=False,
        image_optimize=False,
    )
    return TestClient(create_app(config))


def _conversation(n_results: int, chars_per_result: int) -> list[dict[str, Any]]:
    """``n_results`` tool_use/tool_result pairs, each result unique text."""
    messages: list[dict[str, Any]] = [{"role": "user", "content": "start"}]
    for i in range(n_results):
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": f"toolu_{i:03d}",
                        "name": "Bash",
                        "input": {"command": f"cat file{i}.txt"},
                    }
                ],
            }
        )
        body = " ".join(f"line{i}-{k} payload" for k in range(chars_per_result // 20))
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": f"toolu_{i:03d}", "content": body}
                ],
            }
        )
    messages.append({"role": "user", "content": "next"})
    return messages


def _tool_result_text(message: dict[str, Any]) -> str:
    block = message["content"][0]
    inner = block["content"]
    if isinstance(inner, list):
        return "".join(part.get("text", "") for part in inner)
    return str(inner)


def test_old_tool_results_are_aged_on_the_wire(
    monkeypatch: pytest.MonkeyPatch, proxy_log_capture: _CapturingHandler
) -> None:
    store = CompressionStore()
    monkeypatch.setattr("headroom.cache.compression_store.get_compression_store", lambda: store)
    monkeypatch.setenv("HEADROOM_AGING_TRIGGER_TOKENS", "2000")
    monkeypatch.setenv("HEADROOM_AGING_KEEP_NEWEST", "2")
    monkeypatch.setenv("HEADROOM_AGING_BATCH_TOKENS", "500")

    n_results = 8
    sent = _conversation(n_results, chars_per_result=3000)
    originals = {
        m["content"][0]["tool_use_id"]: m["content"][0]["content"]
        for m in sent
        if isinstance(m.get("content"), list) and m["content"][0].get("type") == "tool_result"
    }

    forwarded: dict[str, Any] = {}

    with _client() as client:
        app: Any = client.app
        proxy = app.state.proxy

        async def _fake_retry(
            method: str,
            url: str,
            headers: dict[str, str],
            body: Any,
            stream: bool = False,
            **kwargs: Any,
        ) -> httpx.Response:
            forwarded["body"] = json.loads(body) if isinstance(body, bytes | str) else body
            return httpx.Response(
                200,
                json={
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "ok"}],
                    "usage": {
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            )

        proxy._retry_request = _fake_retry

        response = client.post(
            "/v1/messages",
            headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01"},
            json={"model": "claude-sonnet-4-6", "max_tokens": 16, "messages": sent},
        )

    assert response.status_code == 200, response.text
    wire = forwarded["body"]["messages"]
    tool_results = [
        m
        for m in wire
        if isinstance(m.get("content"), list) and m["content"][0].get("type") == "tool_result"
    ]
    assert len(tool_results) == n_results

    aged, kept = tool_results[:-2], tool_results[-2:]
    for m in aged:
        text = _tool_result_text(m)
        assert text.startswith("[aged tool result"), text[:80]
        assert "Bash" in text
        match = _HASH_RE.search(text)
        assert match, text
        entry = store.retrieve(match.group(1))
        assert entry is not None
        assert entry.original_content == originals[m["content"][0]["tool_use_id"]]
    for m in kept:
        assert _tool_result_text(m) == originals[m["content"][0]["tool_use_id"]]

    aging_lines = [m for m in proxy_log_capture.messages() if " AGING " in m]
    assert len(aging_lines) == 1, proxy_log_capture.messages()
    assert "aged_blocks=6" in aging_lines[0]
    assert "trigger=2000 keep_newest=2" in aging_lines[0]


def test_below_trigger_nothing_is_aged(monkeypatch: pytest.MonkeyPatch) -> None:
    store = CompressionStore()
    monkeypatch.setattr("headroom.cache.compression_store.get_compression_store", lambda: store)
    monkeypatch.setenv("HEADROOM_AGING_TRIGGER_TOKENS", "1000000")

    sent = _conversation(4, chars_per_result=400)
    forwarded: dict[str, Any] = {}

    with _client() as client:
        app: Any = client.app
        proxy = app.state.proxy

        async def _fake_retry(
            method: str,
            url: str,
            headers: dict[str, str],
            body: Any,
            stream: bool = False,
            **kwargs: Any,
        ) -> httpx.Response:
            forwarded["body"] = json.loads(body) if isinstance(body, bytes | str) else body
            return httpx.Response(
                200,
                json={
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "ok"}],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )

        proxy._retry_request = _fake_retry
        response = client.post(
            "/v1/messages",
            headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01"},
            json={"model": "claude-sonnet-4-6", "max_tokens": 16, "messages": sent},
        )

    assert response.status_code == 200, response.text
    assert "Retrieve original: hash=" not in json.dumps(forwarded["body"]["messages"])


# ── the PERF line carries aged=<blocks>/<tokens> only when aging fired ──────


class _Metrics:
    async def record_request(self, **kwargs: Any) -> None:
        return None

    async def record_failed(self, provider: str) -> None:
        return None


class _Handler:
    def __init__(self) -> None:
        self.metrics = _Metrics()
        self.cost_tracker = None
        self.logger = None


def _outcome(tags: dict[str, Any]) -> RequestOutcome:
    return RequestOutcome(
        request_id="req-1",
        provider="anthropic",
        model="claude-sonnet-4-6",
        original_tokens=0,
        optimized_tokens=0,
        output_tokens=0,
        tokens_saved=0,
        attempted_input_tokens=0,
        tags=tags,
    )


def _perf_line(capture: _CapturingHandler) -> str:
    for message in capture.messages():
        if " PERF " in message:
            return message
    raise AssertionError("no PERF log line captured")


def test_perf_line_reports_aging(proxy_log_capture: _CapturingHandler) -> None:
    asyncio.run(emit_request_outcome(_Handler(), _outcome({"aged_blocks": 6, "aged_tokens": 4512})))
    assert _perf_line(proxy_log_capture).endswith(" aged=6/4512")


def test_perf_line_unchanged_when_nothing_aged(proxy_log_capture: _CapturingHandler) -> None:
    asyncio.run(emit_request_outcome(_Handler(), _outcome({})))
    assert "aged=" not in _perf_line(proxy_log_capture)


def test_perf_analyzer_reads_the_aged_field() -> None:
    from headroom.perf.analyzer import _parse_kv

    parsed = _parse_kv("model=claude-sonnet-4-6 transforms=none client=claude aged=6/4512")
    assert parsed["aged"] == "6/4512"
    assert parsed["transforms"] == "none"
