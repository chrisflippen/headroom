from __future__ import annotations

import asyncio
import base64
import builtins
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, NoReturn
from unittest.mock import patch

import httpx
import pytest
from fastapi import Request
from fastapi.responses import Response, StreamingResponse
from starlette.datastructures import URL, Headers

from headroom.proxy.handlers.anthropic import AnthropicHandlerMixin, _is_googleapis_endpoint
from headroom.proxy.handlers.openai import (
    OpenAIHandlerMixin,
    _decode_openai_bearer_payload,
    _passthrough_usage_from_json,
    _prefers_http1_passthrough,
)
from headroom.proxy.helpers import (
    _headroom_bypass_enabled,
    relocate_system_messages_to_top_level,
)
from headroom.proxy.models import ProxyConfig
from headroom.proxy.outcome import RequestOutcome
from headroom.proxy.server import HeadroomProxy


def _fake_url(path: str, query: str = "") -> URL:
    """Build a real ``starlette`` ``URL`` for a fake ``Request`` test double.

    ``Request.url`` is a read-only property typed ``URL``; overriding it with
    a plain ``SimpleNamespace`` (as these test doubles used to) only worked
    because the doubles did not actually subclass ``Request``. Now that they
    do (see ``_FakeRequest`` below), the override must be a real ``URL``.
    """
    url = URL(f"http://testserver{path}")
    return url.replace(query=query) if query else url


class _FakeRequest(Request):
    """Base class for lightweight ``Request`` test doubles.

    The real ``Request.__init__`` requires a full ASGI ``scope`` dict, which
    none of the handlers under test actually need. Overriding both ``__new__``
    and ``__init__`` to skip ``Request.__init__`` (Python still calls
    ``__init__`` after ``__new__`` returns an instance of ``cls``, so
    ``__new__`` alone is not enough) keeps these as genuine ``Request``
    subclasses (so ``isinstance`` checks and static type checkers see the
    real class) while each subclass below fills in only the attributes and
    methods its handler under test actually reads.
    """

    def __new__(cls, *args: object, **kwargs: object) -> _FakeRequest:
        return object.__new__(cls)

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass


def _jwt(payload: object) -> str:
    header = {"alg": "none", "typ": "JWT"}

    def encode(part: object) -> str:
        raw = json.dumps(part, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode(header)}.{encode(payload)}."


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://us-central1-aiplatform.googleapis.com/v1", True),
        ("https://googleapis.com/v1", True),
        ("https://AIPLATFORM.GOOGLEAPIS.COM./v1", True),
        ("https://googleapis.com.example.test/v1", False),
        ("https://notgoogleapis.com/v1", False),
        ("https://googleapis.com@attacker.test/v1", False),
        ("not a url", False),
        ("", False),
    ],
)
def test_googleapis_endpoint_gate_uses_hostname_boundary(url: str, expected: bool) -> None:
    assert _is_googleapis_endpoint(url) is expected


class _ImageCompressor:
    def __init__(self, compressed_message: dict[str, Any]) -> None:
        self._compressed_message = compressed_message

    def compress(self, messages: list[dict[str, Any]], provider: str) -> list[dict[str, Any]]:
        assert provider == "anthropic"
        return [self._compressed_message]


class _FreshCompressor:
    instances = 0
    # Set dynamically by the real `_get_image_compressor()` (see
    # headroom/proxy/helpers.py) on whatever `ImageCompressor` instance it
    # creates; declared here so this test double has the same shape.
    _is_singleton = False

    def __init__(self) -> None:
        type(self).instances += 1


class _TimeoutHttpClient(httpx.AsyncClient):
    async def request(self, *args: Any, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectTimeout("connect timed out")


class _RecordingHttpClient(httpx.AsyncClient):
    def __init__(self, label: str) -> None:
        super().__init__()
        self.label = label
        self.calls = 0

    async def request(self, *args: Any, **kwargs: Any) -> httpx.Response:
        self.calls += 1
        request = httpx.Request(kwargs["method"], kwargs["url"])
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "application/json"},
            json={"client": self.label},
        )


class _ChatGPTAccountRequest(_FakeRequest):
    method = "GET"
    headers: Headers = Headers({})
    url: URL = _fake_url("/backend-api/me")

    async def body(self) -> bytes:
        return b""


class _PassthroughRequest(_FakeRequest):
    method = "GET"
    headers: Headers = Headers({})
    url: URL = _fake_url("/some/other/path")

    async def body(self) -> bytes:
        return b""


class _VertexPassthroughRequest(_FakeRequest):
    method = "POST"
    headers: Headers = Headers({})
    url: URL = _fake_url(
        "/v1/projects/p/locations/us-central1/publishers/google/models/gemini-2.0-flash:generateContent"
    )

    async def body(self) -> bytes:
        return b'{"contents":[]}'


class _VertexStreamPassthroughRequest(_FakeRequest):
    method = "POST"
    headers: Headers = Headers({})
    url: URL = _fake_url(
        "/v1/projects/p/locations/us-central1/publishers/google/models/gemini-2.0-flash:streamGenerateContent",
        query="alt=sse",
    )

    async def body(self) -> bytes:
        return b'{"contents":[]}'


class _VertexGeminiImageRequest(_FakeRequest):
    method = "POST"
    headers: Headers = Headers({})
    scope: dict[str, Any] = {"type": "http", "method": "POST", "query_string": b""}
    url: URL = _fake_url(
        "/v1/projects/p/locations/us-central1/publishers/google/models/gemini-2.0-flash:generateContent"
    )

    async def body(self) -> bytes:
        return json.dumps(
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": "aW1hZ2U=",
                                }
                            }
                        ],
                    }
                ]
            }
        ).encode("utf-8")


class _VertexUsageClient(httpx.AsyncClient):
    async def request(self, *args: Any, **kwargs: Any) -> httpx.Response:
        request = httpx.Request(kwargs["method"], kwargs["url"], content=kwargs["content"])
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "application/json"},
            json={
                "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
                "usageMetadata": {
                    "promptTokenCount": 11,
                    "candidatesTokenCount": 7,
                    "cachedContentTokenCount": 3,
                },
            },
        )


class _AsyncChunks(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _VertexStreamClient(httpx.AsyncClient):
    def __init__(self) -> None:
        super().__init__()
        self.sent_url = ""

    def build_request(self, *args: Any, **kwargs: Any) -> httpx.Request:
        method = args[0] if args else kwargs["method"]
        url = args[1] if len(args) > 1 else kwargs["url"]
        self.sent_url = str(url)
        return httpx.Request(
            method, url, headers=kwargs.get("headers"), content=kwargs.get("content")
        )

    async def send(self, request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
        assert kwargs.get("stream") is True
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream"},
            stream=_AsyncChunks(
                [
                    b'data: {"candidates":[{"content":{"parts":[{"text":"hello"}]}}]}\n\n',
                    b'data: {"usageMetadata":{"promptTokenCount":13,'
                    b'"candidatesTokenCount":5,"cachedContentTokenCount":2}}\n\n',
                ]
            ),
        )


class _RetryThenSuccessClient(httpx.AsyncClient):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    async def post(self, *args: Any, **kwargs: Any) -> httpx.Response:
        self.attempts += 1
        if self.attempts == 1:
            raise httpx.ConnectTimeout("connect timed out")
        url = args[0] if args else kwargs["url"]
        headers = kwargs.get("headers")
        content = kwargs.get("content")
        request = httpx.Request("POST", url, headers=headers, content=content)
        return httpx.Response(200, request=request, content=b"{}")


def test_decode_openai_bearer_payload_handles_missing_and_non_mapping_payloads() -> None:
    assert _decode_openai_bearer_payload({}) is None
    assert _decode_openai_bearer_payload({"authorization": "Basic abc"}) is None
    assert (
        _decode_openai_bearer_payload({"authorization": f"Bearer {_jwt(['not', 'a', 'dict'])}"})
        is None
    )


def test_openai_handler_prefix_helpers_cover_edge_cases() -> None:
    assert OpenAIHandlerMixin._strict_previous_turn_frozen_count([], 2) == 2
    assert (
        OpenAIHandlerMixin._strict_previous_turn_frozen_count(
            [{"role": "assistant"}, {"role": "user"}],
            0,
        )
        == 1
    )
    assert (
        OpenAIHandlerMixin._strict_previous_turn_frozen_count(
            [{"role": "assistant"}, {"role": "tool", "content": "observation"}],
            0,
        )
        == 1
    )
    assert (
        OpenAIHandlerMixin._strict_previous_turn_frozen_count(
            [{"role": "user"}, {"role": "assistant"}, {"role": "tool", "content": "obs"}],
            3,
        )
        == 2
    )
    assert (
        OpenAIHandlerMixin._strict_previous_turn_frozen_count(
            [{"role": "assistant"}, {"role": "function", "content": "legacy observation"}],
            0,
        )
        == 1
    )
    assert (
        OpenAIHandlerMixin._strict_previous_turn_frozen_count(
            [{"role": "user"}, {"role": "assistant"}],
            0,
        )
        == 2
    )

    original = [{"role": "system", "content": "keep"}, {"role": "user", "content": "hello"}]
    restored, changed = OpenAIHandlerMixin._restore_frozen_prefix(
        original,
        [],
        frozen_message_count=1,
    )
    assert restored == [{"role": "system", "content": "keep"}]
    assert changed == 1

    restored, changed = OpenAIHandlerMixin._restore_frozen_prefix(
        original,
        [{"role": "system", "content": "changed"}, {"role": "user", "content": "hello"}],
        frozen_message_count=1,
    )
    assert restored == original
    assert changed == 1


def test_relocate_system_messages_moves_stray_system_into_top_level() -> None:
    # Issue #765: compression relocated the harness system block into
    # messages[0] as a role="system" entry, which Anthropic rejects with a 400.
    # The forwarder guard must move it back to the top-level `system` parameter.
    messages = [
        {"role": "system", "content": "You are a harness."},
        {"role": "user", "content": "hi"},
    ]
    clean, system, changed = relocate_system_messages_to_top_level(messages, None)

    assert changed is True
    # No role="system" entry may survive in messages[] — that is the wire-contract violation.
    assert all(m.get("role") != "system" for m in clean)
    assert clean == [{"role": "user", "content": "hi"}]
    # The relocated content lands in the top-level system parameter.
    assert system == [{"type": "text", "text": "You are a harness."}]


def test_relocate_system_messages_appends_to_existing_system() -> None:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": [{"type": "text", "text": "B"}]},
        {"role": "user", "content": "hi"},
    ]
    clean, system, changed = relocate_system_messages_to_top_level(messages, "A")

    assert changed is True
    assert clean == [{"role": "user", "content": "hi"}]
    # Existing system first, relocated content after — wire order preserved.
    assert system == [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]


def test_relocate_system_messages_noop_without_system_entry() -> None:
    messages = [{"role": "user", "content": "hi"}]
    clean, system, changed = relocate_system_messages_to_top_level(messages, "A")

    assert changed is False
    assert clean is messages
    assert system == "A"


def test_relocate_system_messages_preserves_valid_mid_conversation_section() -> None:
    messages = [
        {"role": "user", "content": "Run the tests."},
        {
            "role": "system",
            "content": "The user added: update the changelog too.",
        },
        {"role": "assistant", "content": "I will do both."},
    ]

    clean, system, changed = relocate_system_messages_to_top_level(
        messages, "base", "claude-opus-5"
    )

    assert changed is False
    assert clean is messages
    assert system == "base"


def test_relocate_system_messages_preserves_consecutive_valid_section_at_end() -> None:
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
        {"role": "system", "content": "First update."},
        {"role": "system", "content": "Second update."},
    ]

    clean, system, changed = relocate_system_messages_to_top_level(
        messages, None, "global.anthropic.claude-sonnet-5-v1:0"
    )

    assert changed is False
    assert clean is messages
    assert system is None


@pytest.mark.parametrize(
    "messages",
    [
        [
            {"role": "assistant", "content": "answer"},
            {"role": "system", "content": "bad predecessor"},
        ],
        [
            {"role": "user", "content": "question"},
            {"role": "system", "content": "bad successor"},
            {"role": "user", "content": "another question"},
        ],
    ],
)
def test_relocate_system_messages_still_moves_invalid_mid_conversation_placement(
    messages: list[dict],
) -> None:
    clean, system, changed = relocate_system_messages_to_top_level(messages, None, "claude-fable-5")

    assert changed is True
    assert all(message.get("role") != "system" for message in clean)
    assert system == [{"type": "text", "text": messages[1]["content"]}]


def test_relocate_system_messages_moves_valid_shape_for_unsupported_model() -> None:
    messages = [
        {"role": "user", "content": "question"},
        {"role": "system", "content": "mid-turn instruction"},
    ]

    clean, system, changed = relocate_system_messages_to_top_level(
        messages, None, "claude-sonnet-4-6"
    )

    assert changed is True
    assert clean == [{"role": "user", "content": "question"}]
    assert system == [{"type": "text", "text": "mid-turn instruction"}]


def test_headroom_bypass_helper_is_transport_neutral() -> None:
    assert _headroom_bypass_enabled({"x-headroom-bypass": "true"}) is True
    assert _headroom_bypass_enabled({"x-headroom-bypass": " TRUE "}) is True
    assert _headroom_bypass_enabled({"x-headroom-mode": "passthrough"}) is True
    assert _headroom_bypass_enabled({"x-headroom-mode": " PASSTHROUGH "}) is True
    assert _headroom_bypass_enabled({"x-headroom-bypass": "false"}) is False
    assert _headroom_bypass_enabled({}) is False
    assert _headroom_bypass_enabled(None) is False
    assert OpenAIHandlerMixin._headroom_bypass_enabled({"x-headroom-bypass": "true"}) is True


def test_openai_passthrough_without_config_preserves_generic_request() -> None:
    handler = object.__new__(HeadroomProxy)
    handler.http_client = _RecordingHttpClient("h2")
    request = _PassthroughRequest()

    response = asyncio.run(handler.handle_passthrough(request, "https://api.openai.com"))

    assert response.status_code == 200
    assert json.loads(bytes(response.body))["client"] == "h2"


def test_openai_passthrough_connect_timeout_returns_502() -> None:
    handler = object.__new__(HeadroomProxy)
    handler.http_client = _TimeoutHttpClient()

    async def run() -> Response:
        return await handler.handle_passthrough(
            _PassthroughRequest(),
            "https://api.openai.com",
        )

    response = asyncio.run(run())

    assert response.status_code == 502
    payload = json.loads(bytes(response.body))
    assert payload["error"]["type"] == "connection_error"
    assert "Failed to connect to upstream API" in payload["error"]["message"]


def test_prefers_http1_passthrough_matches_chatgpt_hosts_only() -> None:
    assert _prefers_http1_passthrough("https://chatgpt.com") is True
    assert _prefers_http1_passthrough("https://chatgpt.com/backend-api/me") is True
    assert _prefers_http1_passthrough("https://api.chatgpt.com") is True
    assert _prefers_http1_passthrough("https://CHATGPT.COM/backend-api/me") is True
    assert _prefers_http1_passthrough("https://api.openai.com") is False
    assert _prefers_http1_passthrough("https://notchatgpt.com") is False
    assert _prefers_http1_passthrough("https://chatgpt.com.evil.com") is False
    assert _prefers_http1_passthrough("") is False


def test_chatgpt_passthrough_uses_http1_client() -> None:
    handler = object.__new__(HeadroomProxy)
    handler.http_client = _RecordingHttpClient("h2")
    handler.http_client_h1 = _RecordingHttpClient("h1")

    response = asyncio.run(
        handler.handle_passthrough(_ChatGPTAccountRequest(), "https://chatgpt.com")
    )

    assert response.status_code == 200
    assert json.loads(bytes(response.body))["client"] == "h1"
    assert handler.http_client.calls == 0
    assert handler.http_client_h1.calls == 1


def test_non_chatgpt_passthrough_uses_default_client() -> None:
    handler = object.__new__(HeadroomProxy)
    handler.http_client = _RecordingHttpClient("h2")
    handler.http_client_h1 = _RecordingHttpClient("h1")

    response = asyncio.run(
        handler.handle_passthrough(_PassthroughRequest(), "https://api.openai.com")
    )

    assert response.status_code == 200
    assert json.loads(bytes(response.body))["client"] == "h2"
    assert handler.http_client.calls == 1
    assert handler.http_client_h1.calls == 0


def test_chatgpt_passthrough_falls_back_when_h1_client_missing() -> None:
    handler = object.__new__(HeadroomProxy)
    handler.http_client = _RecordingHttpClient("h2")
    handler.http_client_h1 = None

    response = asyncio.run(
        handler.handle_passthrough(_ChatGPTAccountRequest(), "https://chatgpt.com")
    )

    assert response.status_code == 200
    assert json.loads(bytes(response.body))["client"] == "h2"
    assert handler.http_client.calls == 1


def test_passthrough_usage_normalizes_vertex_usage_metadata() -> None:
    usage = _passthrough_usage_from_json(
        {
            "usageMetadata": {
                "promptTokenCount": 11,
                "candidatesTokenCount": 7,
                "cachedContentTokenCount": 3,
            }
        }
    )

    assert usage == {
        "input_tokens": 11,
        "output_tokens": 7,
        "cache_read_input_tokens": 3,
    }


def test_gemini_output_tokens_includes_thinking_when_exclusive() -> None:
    """Gemini 2.5 thinking: when prompt + candidates != total, thoughtsTokenCount
    is a separate output bucket and must be added, or output cost undercounts."""
    from headroom.proxy.token_counting import gemini_output_tokens

    exclusive = {
        "promptTokenCount": 1000,
        "candidatesTokenCount": 200,
        "thoughtsTokenCount": 500,
        "totalTokenCount": 1700,
    }
    assert gemini_output_tokens(exclusive) == 700  # 200 visible + 500 thinking

    # Inclusive: candidatesTokenCount already covers thoughts (prompt+cand==total).
    inclusive = {
        "promptTokenCount": 1000,
        "candidatesTokenCount": 700,
        "thoughtsTokenCount": 500,
        "totalTokenCount": 1700,
    }
    assert gemini_output_tokens(inclusive) == 700

    # No thinking tokens: just the candidates count (common non-2.5 case).
    assert gemini_output_tokens({"candidatesTokenCount": 42, "totalTokenCount": 100}) == 42
    # Robust to empty / missing fields.
    assert gemini_output_tokens({}) == 0


def test_passthrough_usage_counts_gemini_thinking_tokens() -> None:
    """_passthrough_usage_from_json must include thinking tokens in output_tokens."""
    usage = _passthrough_usage_from_json(
        {
            "usageMetadata": {
                "promptTokenCount": 1000,
                "candidatesTokenCount": 200,
                "thoughtsTokenCount": 500,
                "totalTokenCount": 1700,
                "cachedContentTokenCount": 100,
            }
        }
    )
    assert usage["output_tokens"] == 700
    assert usage["input_tokens"] == 1000


class _DashboardRecordingProxy(HeadroomProxy):
    """``HeadroomProxy`` subclass used by tests that record dashboard outcomes.

    Real method overrides instead of monkeypatched instance attributes:
    assigning a plain function over an instance's bound-method slot trips both
    pyrefly's ``bad-assignment`` and mypy's ``method-assign`` checks (real
    safety checks, not false positives), since the assigned value is not a
    bound method of the right shape. A genuine method override in a subclass
    body is normal, fully-typed OOP and triggers neither.
    """

    def __init__(self, request_id: str = "req_test") -> None:
        self.recorded_outcomes: list[RequestOutcome] = []
        self._fake_request_id = request_id

    async def _next_request_id(self) -> str:
        return self._fake_request_id

    async def _record_request_outcome(self, outcome: RequestOutcome) -> None:
        self.recorded_outcomes.append(outcome)


def test_vertex_passthrough_records_usage_metadata_for_dashboard() -> None:
    handler = _DashboardRecordingProxy("req_vertex")
    handler.http_client = _VertexUsageClient()

    response = asyncio.run(
        handler.handle_passthrough(
            _VertexPassthroughRequest(),
            "https://vertex.test",
            "generateContent",
            "vertex:google",
        )
    )

    assert response.status_code == 200
    assert len(handler.recorded_outcomes) == 1
    outcome = handler.recorded_outcomes[0]
    assert outcome.provider == "vertex:google"
    assert outcome.model == "gemini-2.0-flash"
    assert outcome.optimized_tokens == 11
    assert outcome.output_tokens == 7
    assert outcome.cache_read_tokens == 3


def test_vertex_stream_passthrough_preserves_chunks_and_records_usage() -> None:
    handler = _DashboardRecordingProxy("req_vertex_stream")
    handler.http_client = _VertexStreamClient()

    response = asyncio.run(
        handler.handle_passthrough(
            _VertexStreamPassthroughRequest(),
            "https://vertex.test",
            "streamGenerateContent",
            "vertex:google",
        )
    )

    assert isinstance(response, StreamingResponse)

    async def collect(stream: StreamingResponse) -> list[bytes]:
        chunks: list[bytes] = []
        async for chunk in stream.body_iterator:
            assert isinstance(chunk, bytes)
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(collect(response))

    assert len(chunks) == 2
    assert chunks[0].startswith(b'data: {"candidates"')
    assert b'"usageMetadata"' in chunks[1]
    assert len(handler.recorded_outcomes) == 1
    outcome = handler.recorded_outcomes[0]
    assert outcome.provider == "vertex:google"
    assert outcome.model == "gemini-2.0-flash"
    assert outcome.optimized_tokens == 13
    assert outcome.output_tokens == 5
    assert outcome.cache_read_tokens == 2


def test_stream_finalizer_records_vertex_provider_for_dashboard() -> None:
    handler = _DashboardRecordingProxy()
    handler.config = ProxyConfig(log_full_messages=False)

    asyncio.run(
        handler._finalize_stream_response(
            body={"contents": [{"role": "user", "parts": [{"text": "hello"}]}]},
            provider="gemini",
            outcome_provider="vertex:google",
            model="gemini-2.0-flash",
            request_id="req_vertex_stream_final",
            original_tokens=20,
            optimized_tokens=12,
            tokens_saved=8,
            transforms_applied=["test-transform"],
            optimization_latency=3.0,
            stream_state={
                "input_tokens": 12,
                "output_tokens": 5,
                "cache_read_input_tokens": 2,
                "cache_creation_input_tokens": 0,
                "cache_creation_ephemeral_5m_input_tokens": 0,
                "cache_creation_ephemeral_1h_input_tokens": 0,
                "total_bytes": 100,
                "sse_buffer": bytearray(),
                "ttfb_ms": 4.0,
            },
            start_time=0.0,
            tags={"route": "vertex"},
        )
    )

    assert len(handler.recorded_outcomes) == 1
    outcome = handler.recorded_outcomes[0]
    assert outcome.provider == "vertex:google"
    assert outcome.model == "gemini-2.0-flash"
    assert outcome.optimized_tokens == 12
    assert outcome.output_tokens == 5
    assert outcome.tokens_saved == 8
    assert outcome.cache_read_tokens == 2


class _VertexImageRetryProxy(_DashboardRecordingProxy):
    """Adds a ``_retry_request`` override that records the upstream URL used.

    The override's parameter shape matches ``HeadroomProxy._retry_request``
    exactly (see ``headroom/proxy/server.py``) so it is a genuine, type-safe
    override rather than an incompatible signature that happens to work at
    runtime.
    """

    def __init__(self, request_id: str = "req_test") -> None:
        super().__init__(request_id)
        self.retry_urls: list[str] = []

    async def _retry_request(
        self,
        method: str,
        url: str,
        headers: dict,
        body: dict,
        stream: bool = False,
        *,
        original_body_bytes: bytes | None = None,
        body_mutated: bool = True,
        mutation_reasons: list[str] | None = None,
        request_id: str | None = None,
        forwarder_name: str = "server",
        path_for_log: str | None = None,
        timeout: httpx.Timeout | float | None = None,
    ) -> httpx.Response:
        self.retry_urls.append(url)
        request = httpx.Request(method, url, headers=headers)
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "application/json"},
            json={
                "usageMetadata": {
                    "promptTokenCount": 31,
                    "candidatesTokenCount": 4,
                    "cachedContentTokenCount": 6,
                }
            },
        )


def test_vertex_gemini_non_text_generate_records_dashboard_outcome() -> None:
    handler = _VertexImageRetryProxy("req_vertex_image")
    handler.memory_handler = None
    handler.rate_limiter = None

    response = asyncio.run(
        handler.handle_gemini_generate_content(
            _VertexGeminiImageRequest(),
            "gemini-2.0-flash",
            "https://vertex.test",
            "vertex:google",
        )
    )

    assert response.status_code == 200
    assert handler.retry_urls == [
        "https://vertex.test/v1/projects/p/locations/us-central1/publishers/google/models/gemini-2.0-flash:generateContent"
    ]
    assert response.headers["x-headroom-tokens-before"] == "31"
    assert response.headers["x-headroom-tokens-after"] == "31"
    assert response.headers["x-headroom-tokens-saved"] == "0"
    assert len(handler.recorded_outcomes) == 1
    outcome = handler.recorded_outcomes[0]
    assert outcome.provider == "vertex:google"
    assert outcome.model == "gemini-2.0-flash"
    assert outcome.original_tokens == 31
    assert outcome.optimized_tokens == 31
    assert outcome.output_tokens == 4
    assert outcome.cache_read_tokens == 6
    assert outcome.num_messages == 1


def test_retry_request_retries_connect_timeout() -> None:
    proxy = object.__new__(HeadroomProxy)
    proxy.http_client = _RetryThenSuccessClient()
    proxy.config = ProxyConfig(
        retry_enabled=True,
        retry_max_attempts=2,
        retry_base_delay_ms=0,
        retry_max_delay_ms=0,
    )

    response = asyncio.run(
        proxy._retry_request(
            "POST",
            "https://api.openai.com/v1/responses",
            {},
            {"model": "gpt-5"},
        )
    )

    assert response.status_code == 200
    assert isinstance(proxy.http_client, _RetryThenSuccessClient)
    assert proxy.http_client.attempts == 2


def test_retry_request_returns_503_when_shutdown_interrupts_retry_sleep() -> None:
    class _Always429Client(httpx.AsyncClient):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        async def post(self, *args: Any, **kwargs: Any) -> httpx.Response:
            self.attempts += 1
            url = args[0] if args else kwargs["url"]
            return httpx.Response(
                429,
                request=httpx.Request("POST", url),
                json={"error": {"message": "slow down"}},
                headers={"retry-after": "30"},
            )

    proxy = object.__new__(HeadroomProxy)
    proxy.http_client = _Always429Client()
    proxy.config = ProxyConfig(
        retry_enabled=True,
        # This test is about the shutdown-interrupts-retry-sleep path, which is
        # only reachable for a 429/529 when overload retry is opted in
        # (D1G-2249 default is False — see
        # tests/test_proxy/test_overload_passthrough.py).
        retry_overload_enabled=True,
        retry_max_attempts=3,
        retry_base_delay_ms=30000,
        retry_max_delay_ms=30000,
    )
    proxy._shutdown_event = asyncio.Event()
    proxy._shutdown_event.set()

    response = asyncio.run(
        proxy._retry_request(
            "POST",
            "https://api.anthropic.test/v1/messages",
            {},
            {"model": "claude-3-5-sonnet"},
        )
    )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "type": "shutdown",
            "message": "Proxy is shutting down; retry backoff cancelled.",
        }
    }
    assert response.headers["retry-after"] == "0"
    assert isinstance(proxy.http_client, _Always429Client)
    assert proxy.http_client.attempts == 1


def test_anthropic_tool_sort_and_context_append_helpers() -> None:
    tools: list[dict[str, Any]] = [
        {"type": "function", "function": {"name": "beta"}},
        {"name": "alpha"},
        {"type": "tool"},
    ]

    sorted_tools = AnthropicHandlerMixin._sort_tools_deterministically(tools)
    assert sorted_tools is not None

    assert [AnthropicHandlerMixin._tool_sort_key(tool)[0] for tool in sorted_tools] == [
        "alpha",
        "beta",
        "tool",
    ]
    assert AnthropicHandlerMixin._sort_tools_deterministically(None) is None
    assert AnthropicHandlerMixin._tools_for_forwarding(tools, preserve_order=True) == tools
    assert [
        AnthropicHandlerMixin._tool_sort_key(tool)[0]
        for tool in AnthropicHandlerMixin._tools_for_forwarding(tools, preserve_order=False) or []
    ] == [
        "alpha",
        "beta",
        "tool",
    ]
    assert (
        AnthropicHandlerMixin._append_context_to_latest_non_frozen_user_turn(
            [], "ctx", frozen_message_count=0
        )
        == []
    )
    assert AnthropicHandlerMixin._append_context_to_latest_non_frozen_user_turn(
        [{"role": "user", "content": "hello"}],
        "ctx",
        frozen_message_count=0,
    ) == [{"role": "user", "content": "hello\n\nctx"}]
    # PR-A2 semantics: list-content user messages get the context appended
    # to the first text block (live-zone-tail injection).
    assert AnthropicHandlerMixin._append_context_to_latest_non_frozen_user_turn(
        [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
        "ctx",
        frozen_message_count=0,
    ) == [{"role": "user", "content": [{"type": "text", "text": "hello\n\nctx"}]}]


def test_anthropic_image_compression_helper_only_rewrites_latest_eligible_turn() -> None:
    image_message = {
        "role": "user",
        "content": [{"type": "image", "source": {"type": "base64", "data": "abc"}}],
    }
    compressed = {
        "role": "user",
        "content": [{"type": "image", "source": {"type": "base64", "data": "xyz"}}],
    }

    assert (
        AnthropicHandlerMixin._compress_latest_user_turn_images_cache_safe(
            [],
            frozen_message_count=0,
            compressor=_ImageCompressor(compressed),
        )
        == []
    )
    assert AnthropicHandlerMixin._compress_latest_user_turn_images_cache_safe(
        [image_message],
        frozen_message_count=1,
        compressor=_ImageCompressor(compressed),
    ) == [image_message]
    assert AnthropicHandlerMixin._compress_latest_user_turn_images_cache_safe(
        [{"role": "assistant", "content": image_message["content"]}],
        frozen_message_count=0,
        compressor=_ImageCompressor(compressed),
    ) == [{"role": "assistant", "content": image_message["content"]}]
    assert AnthropicHandlerMixin._compress_latest_user_turn_images_cache_safe(
        [{"role": "user", "content": "no-image"}],
        frozen_message_count=0,
        compressor=_ImageCompressor(compressed),
    ) == [{"role": "user", "content": "no-image"}]
    assert AnthropicHandlerMixin._compress_latest_user_turn_images_cache_safe(
        [image_message],
        frozen_message_count=0,
        compressor=_ImageCompressor(image_message),
    ) == [image_message]
    assert AnthropicHandlerMixin._compress_latest_user_turn_images_cache_safe(
        [image_message],
        frozen_message_count=0,
        compressor=_ImageCompressor(compressed),
    ) == [compressed]


def test_proxy_helper_reuses_a_singleton_image_compressor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # #2513: the compressor caches heavyweight models, so it must be a
    # process-wide singleton rather than a fresh instance per request.
    from headroom.proxy import helpers

    monkeypatch.setattr(helpers, "_image_compressor_available", None)
    monkeypatch.setattr(helpers, "_image_compressor_instance", None)
    _FreshCompressor.instances = 0

    with patch("headroom.image.ImageCompressor", _FreshCompressor):
        first = helpers._get_image_compressor()
        second = helpers._get_image_compressor()

    assert isinstance(first, _FreshCompressor)
    assert first is second
    assert first._is_singleton is True
    assert _FreshCompressor.instances == 1


def test_proxy_helper_caches_image_stack_import_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from headroom.proxy import helpers

    real_import = builtins.__import__
    calls = 0

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        if name == "headroom.image":
            calls += 1
            raise ImportError("image extras unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(helpers, "_image_compressor_available", None)
    monkeypatch.setattr(helpers, "_image_compressor_instance", None)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert helpers._get_image_compressor() is None
    assert helpers._get_image_compressor() is None
    assert calls == 1
    assert helpers._image_compressor_available is False


def test_anthropic_cache_delta_helpers_cover_string_list_and_role_mismatch() -> None:
    previous_original = [{"role": "user", "content": "hello"}]
    previous_forwarded = [{"role": "user", "content": "HELLO"}]

    assert AnthropicHandlerMixin._extract_cache_stable_delta(
        [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "next"}],
        previous_original,
        previous_forwarded,
    ) == (previous_forwarded, [{"role": "assistant", "content": "next"}])
    assert (
        AnthropicHandlerMixin._extract_cache_stable_delta(
            [{"role": "assistant", "content": "hello"}],
            previous_original,
            previous_forwarded,
        )
        is None
    )

    string_suffix = AnthropicHandlerMixin._extract_cache_stable_last_message_suffix(
        [{"role": "user", "content": "hello world"}],
        previous_original,
        previous_forwarded,
    )
    assert string_suffix == ([], previous_forwarded[0], [{"role": "user", "content": " world"}])

    list_suffix = AnthropicHandlerMixin._extract_cache_stable_last_message_suffix(
        [
            {
                "role": "user",
                "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
            }
        ],
        [{"role": "user", "content": [{"type": "text", "text": "a"}]}],
        [{"role": "user", "content": [{"type": "text", "text": "A"}]}],
    )
    assert list_suffix == (
        [],
        {"role": "user", "content": [{"type": "text", "text": "A"}]},
        [{"role": "user", "content": [{"type": "text", "text": "b"}]}],
    )

    assert AnthropicHandlerMixin._merge_appended_message_delta(
        {"role": "user", "content": "HELLO"},
        {"role": "user", "content": " world"},
    ) == {"role": "user", "content": "HELLO world"}
    assert AnthropicHandlerMixin._merge_appended_message_delta(
        {"role": "user", "content": [{"type": "text", "text": "A"}]},
        {"role": "user", "content": [{"type": "text", "text": "b"}]},
    ) == {"role": "user", "content": [{"type": "text", "text": "A"}, {"type": "text", "text": "b"}]}
    assert (
        AnthropicHandlerMixin._merge_appended_message_delta(
            {"role": "user", "content": "A"},
            {"role": "assistant", "content": "B"},
        )
        is None
    )


def test_anthropic_assistant_message_helper_requires_assistant_role() -> None:
    assert AnthropicHandlerMixin._assistant_message_from_response_json(None) is None
    assert AnthropicHandlerMixin._assistant_message_from_response_json({"role": "user"}) is None
    assert AnthropicHandlerMixin._assistant_message_from_response_json(
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}
    ) == {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}


# ============================================================================
# CCR workspace resolution (cross-project leak fix, 2026-05-26).
#
# These tests pin the `_resolve_ccr_workspace` static helper that the
# anthropic handler uses to scope the proactive-expansion cache by
# project identity. The resolver shares its tier order with the memory
# subsystem's ProjectResolver: x-headroom-project-id → x-headroom-cwd →
# system-prompt `cwd:` line. Returns `("", None)` on no signal — the
# fail-closed signal that callers gate on.
# ============================================================================


def _fake_request(headers: dict[str, str]) -> SimpleNamespace:
    """Minimal Starlette/FastAPI-shaped request object for resolver tests."""
    return SimpleNamespace(headers=headers)


def test_resolve_ccr_workspace_explicit_project_id_wins() -> None:
    """x-headroom-project-id is the highest-priority signal."""
    request = _fake_request({"x-headroom-project-id": "my-cool-project"})
    body: dict[str, Any] = {}
    key, label = AnthropicHandlerMixin._resolve_ccr_workspace(request, body)
    assert key.startswith("my-cool-project-")
    assert len(key.split("-")[-1]) == 16
    assert label == "my-cool-project"


def test_resolve_ccr_workspace_cwd_header() -> None:
    """x-headroom-cwd produces a stable per-cwd key + basename label."""
    request = _fake_request({"x-headroom-cwd": "/home/user/code/daphni-rails"})
    body: dict[str, Any] = {}
    key, label = AnthropicHandlerMixin._resolve_ccr_workspace(request, body)
    # Key format: "{basename}-{sha256[:16]}" — stable per absolute cwd.
    assert key.startswith("daphni-rails-")
    assert len(key) >= len("daphni-rails-") + 16
    assert label == "daphni-rails"


def test_resolve_ccr_workspace_two_cwds_get_distinct_keys() -> None:
    """Two different cwds produce different workspace keys (cross-leak prevention)."""
    key_a, _ = AnthropicHandlerMixin._resolve_ccr_workspace(
        _fake_request({"x-headroom-cwd": "/home/user/code/daphni-rails"}), {}
    )
    key_b, _ = AnthropicHandlerMixin._resolve_ccr_workspace(
        _fake_request({"x-headroom-cwd": "/home/user/code/tamag0"}), {}
    )
    assert key_a != key_b, "different cwds must yield different workspace keys"


def test_resolve_ccr_workspace_no_signal_returns_empty() -> None:
    """No project-id, no cwd header, no system prompt → fail-closed signal."""
    request = _fake_request({})
    body: dict[str, Any] = {}
    key, label = AnthropicHandlerMixin._resolve_ccr_workspace(request, body)
    assert key == ""
    assert label is None


def test_resolve_ccr_workspace_system_prompt_cwd_fallback() -> None:
    """System prompt with `cwd:` line is the lowest-tier fallback."""
    request = _fake_request({})
    body = {
        "system": [{"type": "text", "text": "You are helpful.\ncwd: /home/u/code/my-project\nGo."}]
    }
    key, label = AnthropicHandlerMixin._resolve_ccr_workspace(request, body)
    # The label is the basename of the cwd extracted from the prompt.
    assert label == "my-project"
    assert key.startswith("my-project-")


def test_resolve_ccr_workspace_malformed_request_returns_empty() -> None:
    """A request whose headers attribute can't be dict()-ed fails closed, not crashes."""

    class _BrokenHeaders:
        def __iter__(self) -> NoReturn:
            raise RuntimeError("boom")

    request = SimpleNamespace(headers=_BrokenHeaders())
    body: dict[str, Any] = {}
    # The helper catches the exception, logs it, and returns the fail-
    # closed sentinel ("", None). Critically, it does NOT raise — the
    # proxy must continue serving the request even if CCR scoping fails.
    key, label = AnthropicHandlerMixin._resolve_ccr_workspace(request, body)
    assert key == ""
    assert label is None


class TestHasNewCcrMarkers:
    """#1850: replayed (overlay) markers must not count as new-this-turn.

    ``overlay_cached_prefix`` replays the previously-forwarded compressed prefix
    byte-identical to keep the messages cache warm — which reintroduces its old
    ``hash=…`` markers. If those replayed markers counted as "new", the handler
    would re-inject the retrieve tool every frozen turn and bust the *tools*
    cache. ``has_new_ccr_markers`` filters them out.
    """

    @staticmethod
    def _hashes(*contents: str) -> list[str]:
        from headroom.ccr.tool_injection import CCRToolInjector

        inj = CCRToolInjector(
            provider="anthropic", inject_tool=False, inject_system_instructions=False
        )
        inj.scan_for_markers([{"role": "user", "content": c} for c in contents])
        return inj.detected_hashes

    def test_replayed_markers_are_not_new(self) -> None:
        from headroom.proxy.helpers import has_new_ccr_markers

        marker = "[100 items compressed to 10. Retrieve more: hash=abc123def456abc123def456]"
        current = self._hashes(marker)
        assert current, "sanity: the marker must be detected"
        # Every marker was already in what we forwarded last turn → nothing new.
        assert (
            has_new_ccr_markers(
                current_detected_hashes=current,
                previous_forwarded_messages=[{"role": "user", "content": marker}],
                provider="anthropic",
            )
            is False
        )

    def test_genuinely_new_marker_is_detected(self) -> None:
        from headroom.proxy.helpers import has_new_ccr_markers

        old = "[100 items compressed to 10. Retrieve more: hash=abc123def456abc123def456]"
        new = "[50 items compressed to 5. Retrieve more: hash=deadbeefdeadbeefdeadbeef]"
        current = self._hashes(old, new)
        # Only `old` was forwarded before; `new` is fresh → override must fire.
        assert (
            has_new_ccr_markers(
                current_detected_hashes=current,
                previous_forwarded_messages=[{"role": "user", "content": old}],
                provider="anthropic",
            )
            is True
        )

    def test_no_previous_forward_means_all_new(self) -> None:
        from headroom.proxy.helpers import has_new_ccr_markers

        marker = "[100 items compressed to 10. Retrieve more: hash=abc123def456abc123def456]"
        assert (
            has_new_ccr_markers(
                current_detected_hashes=self._hashes(marker),
                previous_forwarded_messages=None,
                provider="anthropic",
            )
            is True
        )

    def test_no_markers_means_nothing_new(self) -> None:
        from headroom.proxy.helpers import has_new_ccr_markers

        assert (
            has_new_ccr_markers(
                current_detected_hashes=[],
                previous_forwarded_messages=None,
                provider="anthropic",
            )
            is False
        )


def test_strict_frozen_count_tool_and_function_tail_are_mutable() -> None:
    # OpenAI function-calling harnesses (Kimi / fireworks) end each turn with a
    # role:"tool" (or legacy role:"function") observation — NOT role:"user".
    # Gating the mutable tail on role=="user" froze the whole conversation on
    # every such turn => zero compression. Tool/function observations must be
    # treated as the mutable delta (freeze all-but-last), like a user obs.
    from headroom.proxy.handlers.openai import OpenAIHandlerMixin as M

    # role:tool tail -> only the last message is mutable (frozen = final_idx)
    assert (
        M._strict_previous_turn_frozen_count(
            [{"role": "user"}, {"role": "assistant"}, {"role": "tool"}], 0
        )
        == 2
    )
    assert (
        M._strict_previous_turn_frozen_count(
            [{"role": "user"}, {"role": "assistant"}, {"role": "function"}], 0
        )
        == 2
    )
    # assistant/system tail is NOT an observation -> freeze everything
    assert (
        M._strict_previous_turn_frozen_count(
            [{"role": "user"}, {"role": "tool"}, {"role": "assistant"}], 0
        )
        == 3
    )


class _ClientDisconnectRequest(_FakeRequest):
    """Mock request whose body() raises ClientDisconnect to simulate mid-stream cancel."""

    method = "POST"
    headers: Headers = Headers({"content-type": "application/json"})
    url: URL = _fake_url("/v1/chat/completions")

    async def body(self) -> bytes:
        from starlette.requests import ClientDisconnect

        raise ClientDisconnect()


class _ClientDisconnectStreamRequest(_FakeRequest):
    """Mock request for streaming passthrough with ClientDisconnect."""

    method = "POST"
    headers: Headers = Headers({"content-type": "application/json"})
    url: URL = _fake_url(
        "/v1/projects/p/locations/us-central1/publishers/google/models/gemini-2.0-flash:streamGenerateContent",
        query="alt=sse",
    )

    async def body(self) -> bytes:
        from starlette.requests import ClientDisconnect

        raise ClientDisconnect()


def test_handle_passthrough_client_disconnect() -> None:
    """ClientDisconnect during body read returns 204 instead of crashing TaskGroup."""
    handler = object.__new__(OpenAIHandlerMixin)
    response = asyncio.run(
        handler.handle_passthrough(_ClientDisconnectRequest(), "https://api.openai.com")
    )
    assert response.status_code == 204


def test_handle_streaming_passthrough_client_disconnect() -> None:
    """ClientDisconnect during streaming body read returns 204."""
    handler = object.__new__(OpenAIHandlerMixin)
    response = asyncio.run(
        handler.handle_passthrough(
            _ClientDisconnectStreamRequest(),
            "https://us-central1-aiplatform.googleapis.com",
            endpoint_name="streamRawPredict",
            provider="vertex:google",
        )
    )
    assert response.status_code == 204
