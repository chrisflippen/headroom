"""429/529 pass straight through to the client by default (D1G-2249).

Anthropic answering 429 (rate limit) or 529 (overloaded) used to trigger the
proxy's own retry loop, honoring ``Retry-After`` with exponential backoff on
top. A client fan-out that hit a real rate limit could sleep 60s x N attempts
per request while Claude Code sat frozen -- even though Claude Code already
handles 429/529 itself (countdown, cancel), as long as it sees the status
promptly.

Rate limiting is Anthropic's job and the client's job, not the proxy's. The
new default (``retry_overload_enabled=False``) forwards the overload status,
headers (including ``Retry-After``), and body verbatim and immediately, with
no proxy-side sleep. Flipping ``retry_overload_enabled=True`` (or setting
``HEADROOM_RETRY_OVERLOAD=1`` at the CLI) restores the old retry-with-backoff
behavior unchanged.

Covers both forwarders that talk to upstream directly:
  * ``server.py: HeadroomProxy._retry_request`` -- buffered/non-streaming,
    also the path taken by the CCR buffered ``stream:false`` turn.
  * ``streaming.py: HeadroomProxy._stream_response`` -- ``stream:true``.

A regular 503 (not an overload status) must keep retrying exactly as before,
regardless of the new knob -- only 429/529 are affected.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import cast

import httpx
import pytest

from headroom.proxy.server import HeadroomProxy, ProxyConfig, create_app


class _OverloadTransport(httpx.AsyncBaseTransport):
    """Returns ``fail_status`` for the first ``fail_times`` calls, then 200.

    Records ``calls`` so a test can assert whether a retry happened.
    """

    def __init__(
        self,
        *,
        fail_status: int = 429,
        fail_times: int = 1,
        retry_after: str | None = "60",
        sse: bool = False,
    ) -> None:
        self.fail_status = fail_status
        self.fail_times = fail_times
        self.retry_after = retry_after
        self.sse = sse
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        # httpx always hands an async transport an AsyncByteStream on the
        # async request path; the declared attribute type is the sync/async
        # union shared with the sync Transport ABC.
        stream = cast(AsyncIterator[bytes], request.stream)
        async for _ in stream:  # drain the request body
            pass
        if self.calls <= self.fail_times:
            headers = {"request-id": "req_upstream_123"}
            if self.retry_after is not None:
                headers["retry-after"] = self.retry_after
            return httpx.Response(
                self.fail_status,
                headers=headers,
                json={
                    "type": "error",
                    "error": {"type": "rate_limit_error", "message": "slow down"},
                },
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


def _proxy_with(
    transport: _OverloadTransport,
    *,
    retry_overload_enabled: bool = False,
    max_attempts: int = 3,
) -> HeadroomProxy:
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
        retry_overload_enabled=retry_overload_enabled,
        retry_max_attempts=max_attempts,
        # Deliberately large so the old-behavior assertion (below) proves it is
        # NOT honored in the passthrough default and IS honored when the knob
        # flips on and Retry-After is small (real assertion uses retry_after
        # small too, see test_knob_on_...).
        retry_base_delay_ms=1000,
        retry_max_delay_ms=60000,
    )
    proxy = cast(HeadroomProxy, create_app(config).state.proxy)
    proxy.http_client = httpx.AsyncClient(transport=transport)
    return proxy


# --- server.py: _retry_request (non-streaming / buffered CCR path) -------


def test_retry_request_forwards_429_verbatim_by_default() -> None:
    transport = _OverloadTransport(fail_status=429, fail_times=99, retry_after="60")
    proxy = _proxy_with(transport)

    start = time.monotonic()
    resp = asyncio.run(proxy._retry_request("POST", "https://up/v1/messages", {}, {"messages": []}))
    elapsed = time.monotonic() - start

    assert resp.status_code == 429
    assert resp.headers.get("retry-after") == "60"
    assert resp.headers.get("request-id") == "req_upstream_123"
    assert transport.calls == 1  # no retry — upstream called exactly once
    assert elapsed < 0.5  # well under a second — no 60s sleep


def test_retry_request_forwards_529_verbatim_by_default() -> None:
    transport = _OverloadTransport(fail_status=529, fail_times=99, retry_after="60")
    proxy = _proxy_with(transport)

    start = time.monotonic()
    resp = asyncio.run(proxy._retry_request("POST", "https://up/v1/messages", {}, {"messages": []}))
    elapsed = time.monotonic() - start

    assert resp.status_code == 529
    assert resp.headers.get("retry-after") == "60"
    assert transport.calls == 1
    assert elapsed < 0.5


def test_retry_request_still_retries_503_by_default() -> None:
    # Regression guard: only 429/529 skip retry. A plain 5xx keeps retrying.
    transport = _OverloadTransport(fail_status=503, fail_times=1, retry_after=None)
    proxy = _proxy_with(transport)

    resp = asyncio.run(proxy._retry_request("POST", "https://up/v1/messages", {}, {"messages": []}))

    assert resp.status_code == 200
    assert transport.calls == 2  # 503 still retried once, then succeeded


def test_retry_request_knob_on_retries_429_then_succeeds() -> None:
    # HEADROOM_RETRY_OVERLOAD=1 (retry_overload_enabled=True) restores the old
    # behavior: retry honoring Retry-After, and a later success is returned.
    transport = _OverloadTransport(fail_status=429, fail_times=1, retry_after="0")
    proxy = _proxy_with(transport, retry_overload_enabled=True)

    resp = asyncio.run(proxy._retry_request("POST", "https://up/v1/messages", {}, {"messages": []}))

    assert resp.status_code == 200
    assert transport.calls == 2  # one 429 + one retry that succeeded


# --- streaming.py: _stream_response (stream:true) -------------------------


def test_stream_response_forwards_429_verbatim_by_default(caplog: pytest.LogCaptureFixture) -> None:
    transport = _OverloadTransport(fail_status=429, fail_times=99, retry_after="60", sse=True)
    proxy = _proxy_with(transport)

    start = time.monotonic()
    with caplog.at_level("WARNING"):
        response = asyncio.run(
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
    elapsed = time.monotonic() - start

    assert transport.calls == 1  # forwarded once, not retried
    assert elapsed < 0.5  # no sleep
    assert response.status_code == 429
    assert response.headers.get("retry-after") == "60"
    assert not any("retrying in" in rec.message for rec in caplog.records)


def test_stream_response_forwards_529_verbatim_by_default(caplog: pytest.LogCaptureFixture) -> None:
    transport = _OverloadTransport(fail_status=529, fail_times=99, retry_after="60", sse=True)
    proxy = _proxy_with(transport)

    start = time.monotonic()
    with caplog.at_level("WARNING"):
        response = asyncio.run(
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
    elapsed = time.monotonic() - start

    assert transport.calls == 1
    assert elapsed < 0.5
    assert response.status_code == 529
    assert response.headers.get("retry-after") == "60"
    assert not any("retrying in" in rec.message for rec in caplog.records)


def test_stream_response_knob_on_still_retries_429() -> None:
    transport = _OverloadTransport(fail_status=429, fail_times=1, retry_after="0", sse=True)
    proxy = _proxy_with(transport, retry_overload_enabled=True)

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

    assert transport.calls == 2  # old behavior restored: retried once, then succeeded
