"""Bounds and readability contract for the dashboard's transformations feed.

D1G-2249: enabling ``HEADROOM_LOG_MESSAGES=true`` used to be unbounded —
``RequestLogger`` kept full request/compressed/response bodies on all
``MAX_LOG_ENTRIES`` (10,000) entries, and a single long-running conversation
could be megabytes on its own. This file locks in the three seams that keep
it bounded:

* ``RequestLogger`` only keeps message bodies on the most recent
  ``MESSAGE_BODY_RETENTION`` entries (older entries keep every other field).
* ``truncate_stored_text`` caps any one stored string leaf.
* ``/transformations/feed`` reports a ``passthrough`` flag (and the other
  fields the dashboard needs) so the UI can filter out count_tokens noise.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from headroom.proxy.helpers import is_passthrough_model
from headroom.proxy.models import ProxyConfig, RequestLog
from headroom.proxy.request_logger import (
    MAX_STORED_TEXT_CHARS,
    RequestLogger,
    truncate_stored_text,
)
from headroom.proxy.server import create_app


def _log_entry(**overrides: object) -> RequestLog:
    defaults: dict[str, object] = {
        "request_id": "req-1",
        "timestamp": "2026-09-05T00:00:00Z",
        "provider": "anthropic",
        "model": "claude-sonnet-4",
        "input_tokens_original": 1000,
        "input_tokens_optimized": 300,
        "output_tokens": 50,
        "tokens_saved": 700,
        "savings_percent": 70.0,
        "optimization_latency_ms": 1.5,
        "total_latency_ms": 200.0,
        "tags": {},
        "cache_hit": False,
        "transforms_applied": [],
    }
    defaults.update(overrides)
    return RequestLog(**defaults)  # type: ignore[arg-type]


# ── truncate_stored_text ──────────────────────────────────────────────


def test_truncate_stored_text_leaves_short_strings_untouched() -> None:
    assert truncate_stored_text("hello") == "hello"


def test_truncate_stored_text_truncates_long_string_with_marker() -> None:
    text = "x" * (MAX_STORED_TEXT_CHARS + 1234)
    result = truncate_stored_text(text)
    assert result.startswith("x" * MAX_STORED_TEXT_CHARS)
    assert result.endswith("…[truncated 1234 chars]")
    assert len(result) < len(text)


def test_truncate_stored_text_walks_nested_message_structures() -> None:
    long_text = "y" * (MAX_STORED_TEXT_CHARS + 500)
    messages = [
        {"role": "user", "content": "short"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": long_text},
                {"type": "tool_use", "input": {"query": "short"}},
            ],
        },
    ]
    result = truncate_stored_text(messages)
    assert result[0]["content"] == "short"
    assert result[1]["content"][0]["text"].endswith("…[truncated 500 chars]")
    assert result[1]["content"][1]["input"]["query"] == "short"


def test_truncate_stored_text_leaves_non_string_leaves_alone() -> None:
    assert truncate_stored_text(42) == 42
    assert truncate_stored_text(None) is None
    assert truncate_stored_text([1, 2, {"a": True}]) == [1, 2, {"a": True}]


# ── RequestLogger: truncation applied at store time ──────────────────


def test_log_truncates_request_messages_at_store_time() -> None:
    logger = RequestLogger(log_file=None, log_full_messages=True)
    long_text = "z" * (MAX_STORED_TEXT_CHARS + 10)
    entry = _log_entry(request_messages=[{"role": "user", "content": long_text}])
    logger.log(entry)
    stored = logger.get_recent_with_messages(1)[0]
    assert stored["request_messages"][0]["content"].endswith("…[truncated 10 chars]")


def test_log_truncates_response_content_at_store_time() -> None:
    logger = RequestLogger(log_file=None, log_full_messages=True)
    long_text = "w" * (MAX_STORED_TEXT_CHARS + 7)
    entry = _log_entry(response_content=long_text)
    logger.log(entry)
    stored = logger.get_recent_with_messages(1)[0]
    assert stored["response_content"].endswith("…[truncated 7 chars]")


# ── RequestLogger: MESSAGE_BODY_RETENTION ─────────────────────────────


def test_message_body_retention_keeps_only_most_recent_window() -> None:
    logger = RequestLogger(log_file=None, log_full_messages=True)
    total = RequestLogger.MESSAGE_BODY_RETENTION + 5

    for i in range(total):
        logger.log(
            _log_entry(
                request_id=f"req-{i}",
                request_messages=[{"role": "user", "content": f"msg {i}"}],
                compressed_messages=[{"role": "user", "content": f"msg {i}"}],
                response_content=f"resp {i}",
            )
        )

    entries = logger.get_recent_with_messages(total)
    assert len(entries) == total

    # The oldest 5 aged out of the retention window: bodies gone, but every
    # other field (request_id, tokens) is untouched.
    for i in range(5):
        aged = entries[i]
        assert aged["request_id"] == f"req-{i}"
        assert aged["request_messages"] is None
        assert aged["compressed_messages"] is None
        assert aged["response_content"] is None
        assert aged["tokens_saved"] == 700

    # The most recent MESSAGE_BODY_RETENTION entries still carry bodies.
    for i in range(5, total):
        kept = entries[i]
        assert kept["request_messages"] is not None
        assert kept["compressed_messages"] is not None
        assert kept["response_content"] is not None


def test_message_body_retention_ignores_entries_without_bodies() -> None:
    """Entries logged with no message bodies (log_full_messages disabled)
    must not consume a slot in the retention window — otherwise a run of
    bodyless entries would evict real bodies for no reason."""
    logger = RequestLogger(log_file=None, log_full_messages=False)

    # One entry with a body, seeded first.
    logger.log(
        _log_entry(request_id="with-body", request_messages=[{"role": "user", "content": "hi"}])
    )

    # Many bodyless entries after it — should not evict the body above.
    for i in range(RequestLogger.MESSAGE_BODY_RETENTION * 2):
        logger.log(_log_entry(request_id=f"bodyless-{i}"))

    entries = {e["request_id"]: e for e in logger.get_recent_with_messages(10_000)}
    assert entries["with-body"]["request_messages"] is not None


def test_message_body_retention_keeps_entry_numbers_stable() -> None:
    """Older entries keep their own identity (request_id, tokens) even after
    their bodies are cleared — retention drops content, not the entry."""
    logger = RequestLogger(log_file=None, log_full_messages=True)
    logger.log(
        _log_entry(
            request_id="original",
            request_messages=[{"role": "user", "content": "hi"}],
            tokens_saved=42,
        )
    )
    for i in range(RequestLogger.MESSAGE_BODY_RETENTION):
        logger.log(
            _log_entry(
                request_id=f"filler-{i}",
                request_messages=[{"role": "user", "content": "filler"}],
            )
        )

    entries = logger.get_recent_with_messages(10_000)
    original = next(e for e in entries if e["request_id"] == "original")
    assert original["request_messages"] is None
    assert original["tokens_saved"] == 42


# ── is_passthrough_model ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "model,expected",
    [
        ("passthrough:count_tokens", True),
        ("passthrough:batches", True),
        ("passthrough:embeddings", True),
        ("claude-sonnet-4-20250514", False),
        ("gpt-4o", False),
        (None, False),
        ("", False),
    ],
)
def test_is_passthrough_model(model: str | None, expected: bool) -> None:
    assert is_passthrough_model(model) is expected


# ── /transformations/feed: fields the dashboard needs ─────────────────


def _loopback_app_and_client() -> tuple[FastAPI, TestClient]:
    app = create_app(
        ProxyConfig(
            optimize=False,
            cache_enabled=False,
            rate_limit_enabled=False,
            cost_tracking_enabled=False,
            log_requests=True,
            log_full_messages=True,
            ccr_inject_tool=False,
            ccr_handle_responses=False,
            ccr_context_tracking=False,
            image_optimize=False,
        )
    )
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    return app, client


def test_feed_reports_passthrough_and_cache_fields() -> None:
    app, client = _loopback_app_and_client()
    proxy = app.state.proxy
    proxy.logger.log(
        _log_entry(
            request_id="normal-1",
            cache_read_tokens=500,
            cache_write_tokens=100,
            tags={"tool_search_deferred_tokens": 25},
        )
    )
    proxy.logger.log(
        _log_entry(
            request_id="counted-1",
            model="passthrough:count_tokens",
            tokens_saved=0,
            savings_percent=0.0,
        )
    )

    resp = client.get("/transformations/feed?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    by_id = {t["request_id"]: t for t in body["transformations"]}

    normal = by_id["normal-1"]
    assert normal["passthrough"] is False
    assert normal["cache_read_tokens"] == 500
    assert normal["cache_write_tokens"] == 100
    assert normal["tool_schema_saved_tokens"] == 25

    counted = by_id["counted-1"]
    assert counted["passthrough"] is True
