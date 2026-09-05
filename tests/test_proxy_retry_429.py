"""Upstream 429 rate-limit retry + Retry-After honoring (fixes #1221).

Both the non-streaming (``server.py:_retry_request``) and streaming
(``streaming.py:_stream_response``) forwarders retry an upstream 429 with
backoff instead of passing it straight back to the client, since a parallel
agent fan-out that exceeds the per-minute limit otherwise aborts every run.

This is opt-in behavior, not the default (D1G-2249): rate limiting is
Anthropic's job and the client's job, not the proxy's, so ``retry_overload_enabled``
defaults to ``False`` and a 429/529 is forwarded verbatim and immediately with
no proxy-side sleep — see ``tests/test_proxy/test_overload_passthrough.py`` for
that default-off contract. Every proxy built here opts back in with
``retry_overload_enabled=True`` to exercise the retry-with-backoff path this
file is about.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import cast

import httpx
import pytest

from headroom.proxy.server import HeadroomProxy, ProxyConfig, create_app


class _RateLimitTransport(httpx.AsyncBaseTransport):
    """Returns ``fail_status`` for the first ``fail_times`` calls, then 200.

    Records ``calls`` so a test can assert whether a retry happened.
    """

    def __init__(
        self,
        *,
        fail_status: int = 429,
        fail_times: int = 1,
        retry_after: str | None = None,
        sse: bool = False,
    ) -> None:
        self.fail_status = fail_status
        self.fail_times = fail_times
        self.retry_after = retry_after
        self.sse = sse
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        stream = cast(AsyncIterator[bytes], request.stream)
        async for _ in stream:  # drain the request body
            pass
        if self.calls <= self.fail_times:
            headers = {"retry-after": self.retry_after} if self.retry_after is not None else {}
            return httpx.Response(
                self.fail_status,
                headers=headers,
                json={"type": "error", "error": {"type": "rate_limit_error"}},
            )
        if self.sse:
            body = b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)
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


def _proxy_with(transport: _RateLimitTransport, *, max_attempts: int = 3) -> HeadroomProxy:
    config = ProxyConfig(
        optimize=False,
        cache_enabled=False,
        rate_limit_enabled=False,
        cost_tracking_enabled=False,
        log_requests=False,
        ccr_inject_tool=False,
        ccr_handle_responses=False,
        ccr_context_tracking=False,
        image_optimize=False,
        retry_enabled=True,
        # This file is about the retry-with-backoff path itself (#1221), which
        # is opt-in as of D1G-2249 (default False — see
        # tests/test_proxy/test_overload_passthrough.py for the default-off
        # contract), so every proxy built here opts back in explicitly.
        retry_overload_enabled=True,
        retry_max_attempts=max_attempts,
        retry_base_delay_ms=1,
        retry_max_delay_ms=5000,
    )
    proxy = cast(HeadroomProxy, create_app(config).state.proxy)
    proxy.http_client = httpx.AsyncClient(transport=transport)
    return proxy


# --- non-streaming: _retry_request ---------------------------------------


def test_retry_request_retries_429_then_succeeds() -> None:
    transport = _RateLimitTransport(fail_status=429, fail_times=1, retry_after="0")
    proxy = _proxy_with(transport)
    resp = asyncio.run(proxy._retry_request("POST", "https://up/v1/messages", {}, {"messages": []}))
    assert resp.status_code == 200
    assert transport.calls == 2  # one 429 + one success — the retry happened


def test_retry_request_returns_429_verbatim_on_exhaustion() -> None:
    # Always 429: must return the 429 to the client, NOT raise / convert to 5xx.
    transport = _RateLimitTransport(fail_status=429, fail_times=99, retry_after="0")
    proxy = _proxy_with(transport, max_attempts=3)
    resp = asyncio.run(proxy._retry_request("POST", "https://up/v1/messages", {}, {"messages": []}))
    assert resp.status_code == 429
    assert transport.calls == 3  # exhausted all attempts


def test_retry_request_honors_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def _fake_wait(self, seconds: float) -> bool:  # type: ignore[no-untyped-def]
        slept.append(seconds)
        return False

    monkeypatch.setattr(
        "headroom.proxy.server.HeadroomProxy._wait_for_retry_delay_or_shutdown", _fake_wait
    )
    transport = _RateLimitTransport(fail_status=429, fail_times=1, retry_after="2")
    proxy = _proxy_with(transport)
    asyncio.run(proxy._retry_request("POST", "https://up/v1/messages", {}, {"messages": []}))
    # Retry-After: 2s honored (not the ~1-10ms jittered exponential backoff).
    assert slept and abs(slept[0] - 2.0) < 0.01


def test_retry_request_does_not_retry_other_4xx() -> None:
    transport = _RateLimitTransport(fail_status=400, fail_times=99)
    proxy = _proxy_with(transport)
    resp = asyncio.run(proxy._retry_request("POST", "https://up/v1/messages", {}, {"messages": []}))
    assert resp.status_code == 400
    assert transport.calls == 1  # 4xx (non-429) still short-circuits — no retry


def test_retry_request_still_retries_5xx() -> None:
    transport = _RateLimitTransport(fail_status=503, fail_times=1)
    proxy = _proxy_with(transport)
    resp = asyncio.run(proxy._retry_request("POST", "https://up/v1/messages", {}, {"messages": []}))
    assert resp.status_code == 200
    assert transport.calls == 2  # 5xx retry path unchanged


# --- streaming: _stream_response -----------------------------------------


def test_stream_response_retries_429() -> None:
    transport = _RateLimitTransport(fail_status=429, fail_times=1, retry_after="0", sse=True)
    proxy = _proxy_with(transport)
    asyncio.run(
        proxy._stream_response(
            "https://up/v1/messages",
            {},
            {"messages": []},
            "anthropic",
            "claude-3",
            "r1",
            0,
            0,
            0,
            [],
            {},
            0.0,
        )
    )
    assert transport.calls == 2  # streaming 429 retried, not forwarded raw


# --- 529 overloaded: same transient-retry path as 429 --------------------
#
# 529 is Anthropic's ``overloaded_error``. Like 429 it means "try again
# shortly", so both forwarders must retry it honoring Retry-After. Before this
# fix the streaming path forwarded a 529 to the client raw (zero retries), and
# _retry_request retried it only via the generic 5xx path — raising on
# exhaustion instead of returning the 529 verbatim, and ignoring Retry-After.


def test_retry_request_retries_529_then_succeeds() -> None:
    transport = _RateLimitTransport(fail_status=529, fail_times=1, retry_after="0")
    proxy = _proxy_with(transport)
    resp = asyncio.run(proxy._retry_request("POST", "https://up/v1/messages", {}, {"messages": []}))
    assert resp.status_code == 200
    assert transport.calls == 2  # one 529 + one success — the retry happened


def test_retry_request_returns_529_verbatim_on_exhaustion() -> None:
    # Always 529: must return the 529 to the client, NOT raise / convert to 5xx.
    transport = _RateLimitTransport(fail_status=529, fail_times=99, retry_after="0")
    proxy = _proxy_with(transport, max_attempts=3)
    resp = asyncio.run(proxy._retry_request("POST", "https://up/v1/messages", {}, {"messages": []}))
    assert resp.status_code == 529
    assert transport.calls == 3  # exhausted all attempts, returned verbatim


def test_retry_request_honors_retry_after_on_529(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def _fake_wait(self, seconds: float) -> bool:  # type: ignore[no-untyped-def]
        slept.append(seconds)
        return False

    monkeypatch.setattr(
        "headroom.proxy.server.HeadroomProxy._wait_for_retry_delay_or_shutdown", _fake_wait
    )
    transport = _RateLimitTransport(fail_status=529, fail_times=1, retry_after="2")
    proxy = _proxy_with(transport)
    asyncio.run(proxy._retry_request("POST", "https://up/v1/messages", {}, {"messages": []}))
    # Retry-After: 2s honored for 529 just like 429.
    assert slept and abs(slept[0] - 2.0) < 0.01


def test_stream_response_retries_529() -> None:
    # The gap this PR closes: an interactive (streaming) session hitting a 529
    # used to get "Overloaded" surfaced immediately, with no retry.
    transport = _RateLimitTransport(fail_status=529, fail_times=1, retry_after="0", sse=True)
    proxy = _proxy_with(transport)
    asyncio.run(
        proxy._stream_response(
            "https://up/v1/messages",
            {},
            {"messages": []},
            "anthropic",
            "claude-3",
            "r1",
            0,
            0,
            0,
            [],
            {},
            0.0,
        )
    )
    assert transport.calls == 2  # streaming 529 retried, not forwarded raw
