"""Tests for the deterministic MCP JSON compactor.

MCP tools that answer in JSON — Linear's save_issue/get_issue/list_issues
echoing whole issues back, Supabase, Firebase — land in context verbatim.
SmartCrusher only folds JSON *arrays*, so this shrinks the object/array
echo deterministically (prune empties, shorten long strings, cap array
length) and keeps the original CCR-retrievable behind the standard marker.

Covers the recursive prune/shorten/cap transform, the CCR store + marker
plumbing, and every gate that must leave content untouched: under-15%
savings, non-JSON text, a non-``mcp__`` tool, an excluded tool, and the
``HEADROOM_MCP_JSON_COMPACT=0`` opt-out.
"""

from __future__ import annotations

import json

import pytest

from headroom.cache.compression_store import CompressionStore
from headroom.parser import CCR_RETRIEVAL_MARKER_RE
from headroom.transforms.mcp_json_compactor import STRATEGY, compact_mcp_json

TOOL = "mcp__claude_ai_Linear__save_issue"


def _memory_store(monkeypatch: pytest.MonkeyPatch) -> CompressionStore:
    """A fresh, isolated CompressionStore wired in place of the process
    singleton — same monkeypatch shape ``test_transforms_config_compressor.py``
    uses for ``ConfigCompressor``'s CCR tier."""
    store = CompressionStore()
    monkeypatch.setattr("headroom.cache.compression_store.get_compression_store", lambda: store)
    return store


def _linear_issue_payload(desc_len: int = 4000, n_comments: int = 80) -> str:
    """A Linear-shaped issue echo: null/empty fields, a long description,
    and an oversized array -- every gate the compactor should trip."""
    return json.dumps(
        {
            "id": "ISSUE-123",
            "title": "Fix the thing",
            "description": "x" * desc_len,
            "assignee": None,
            "labels": [],
            "customFields": {},
            "url": "",
            "comments": [{"id": i, "body": f"comment {i}"} for i in range(n_comments)],
        }
    )


# ── recursive prune / shorten / cap ────────────────────────────────────────


def test_nulls_and_empties_dropped_recursively(monkeypatch: pytest.MonkeyPatch) -> None:
    _memory_store(monkeypatch)
    content = json.dumps(
        {
            "a": None,
            "b": "",
            "c": [],
            "d": {},
            "e": {"nested_null": None, "keep": "value that is fine"},
            "f": "x" * 2000,  # forces savings past the 15% gate
        }
    )
    result = compact_mcp_json(content, TOOL)
    assert result.was_modified
    body = result.compressed.rsplit("\n", 1)[0]
    parsed = json.loads(body)
    assert "a" not in parsed
    assert "b" not in parsed
    assert "c" not in parsed
    assert "d" not in parsed
    assert parsed["e"] == {"keep": "value that is fine"}


def test_long_strings_shortened_with_exact_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    _memory_store(monkeypatch)
    long_value = "y" * 500
    content = json.dumps({"description": long_value, "pad": "z" * 1000})
    result = compact_mcp_json(content, TOOL)
    assert result.was_modified
    body = result.compressed.rsplit("\n", 1)[0]
    parsed = json.loads(body)
    overflow = len(long_value) - 240
    assert parsed["description"] == long_value[:240] + f" …[+{overflow} chars]"


def test_arrays_capped_with_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    _memory_store(monkeypatch)
    items = [{"id": i, "body": "x" * 50} for i in range(80)]
    content = json.dumps({"comments": items})
    result = compact_mcp_json(content, TOOL)
    assert result.was_modified
    body = result.compressed.rsplit("\n", 1)[0]
    parsed = json.loads(body)
    assert len(parsed["comments"]) == 51
    assert parsed["comments"][-1] == "…[+30 items]"
    assert parsed["comments"][0] == {"id": 0, "body": "x" * 50}


def test_output_is_valid_json_before_marker_line(monkeypatch: pytest.MonkeyPatch) -> None:
    _memory_store(monkeypatch)
    content = _linear_issue_payload()
    result = compact_mcp_json(content, TOOL)
    assert result.was_modified
    lines = result.compressed.rsplit("\n", 1)
    assert len(lines) == 2
    json.loads(lines[0])  # must not raise
    assert "Retrieve original: hash=" in lines[1]


# ── CCR store + marker ─────────────────────────────────────────────────────


def test_marker_appended_and_original_retrievable(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _memory_store(monkeypatch)
    content = _linear_issue_payload()
    result = compact_mcp_json(content, TOOL)
    assert result.was_modified
    assert result.ccr_hash is not None
    assert CCR_RETRIEVAL_MARKER_RE.search(result.compressed)
    entry = store.retrieve(result.ccr_hash)
    assert entry is not None
    assert entry.original_content == content
    assert entry.compression_strategy == STRATEGY


# ── gates: everything that must leave content untouched ───────────────────


def test_under_15_percent_savings_returned_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    _memory_store(monkeypatch)
    # Already compact, nothing to prune/shorten/cap -- re-serializing saves
    # ~nothing.
    content = json.dumps({"id": 1, "title": "small"}, separators=(",", ":"))
    result = compact_mcp_json(content, TOOL)
    assert not result.was_modified
    assert result.compressed == content


def test_non_json_text_untouched() -> None:
    content = "just a plain text tool result, not JSON at all " * 20
    result = compact_mcp_json(content, TOOL)
    assert not result.was_modified
    assert result.compressed == content


def test_non_mcp_tool_untouched() -> None:
    content = _linear_issue_payload()
    result = compact_mcp_json(content, "Bash")
    assert not result.was_modified
    assert result.compressed == content


def test_excluded_tool_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    _memory_store(monkeypatch)
    content = _linear_issue_payload()
    result = compact_mcp_json(content, TOOL, exclude_tools=(TOOL,))
    assert not result.was_modified
    assert result.compressed == content


def test_env_opt_out_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    _memory_store(monkeypatch)
    monkeypatch.setenv("HEADROOM_MCP_JSON_COMPACT", "0")
    content = _linear_issue_payload()
    result = compact_mcp_json(content, TOOL)
    assert not result.was_modified
    assert result.compressed == content
