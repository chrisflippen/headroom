"""Router-level tests: ContentRouter wires mcp_json_compactor into the
tool_result path for mcp__* tools.

Companion to ``test_mcp_json_compactor.py`` (unit tests for the transform
itself) and ``test_content_router_ccr_retrieve_exemption.py`` (fixture style
copied from there: ``ContentRouter.apply(messages, tokenizer)``, asserting on
``result.messages`` / ``result.transforms_applied``). Covers the wiring the
compactor's own unit tests can't see: that an mcp__* tool_result actually
reaches ``compact_mcp_json`` before the general content-type routing / lossy
text path, that a non-mcp__ tool and an excluded mcp__ tool are left alone,
and that the router's own strategy bookkeeping (``transforms_applied``)
records the ``mcp_json`` strategy.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from headroom.cache.compression_store import CompressionStore
from headroom.parser import CCR_RETRIEVAL_MARKER_RE
from headroom.transforms.content_router import ContentRouter, ContentRouterConfig
from headroom.transforms.mcp_json_compactor import STRATEGY

if TYPE_CHECKING:
    from headroom.tokenizer import Tokenizer

TOOL = "mcp__claude_ai_Linear__save_issue"


def _memory_store(monkeypatch: pytest.MonkeyPatch) -> CompressionStore:
    """A fresh, isolated CompressionStore wired in place of the process
    singleton -- same pattern ``test_mcp_json_compactor.py`` uses."""
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


def _get_tokenizer() -> Tokenizer:
    from headroom.providers import OpenAIProvider
    from headroom.tokenizer import Tokenizer

    provider = OpenAIProvider()
    token_counter = provider.get_token_counter("gpt-4o")
    return Tokenizer(token_counter, "gpt-4o")


def _anthropic_messages(tool_name: str, content: str) -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_mcpjson_1",
                    "name": tool_name,
                    "input": {"issueId": "ISSUE-123"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_mcpjson_1",
                    "content": content,
                }
            ],
        },
    ]


class TestContentRouterMcpJsonWiring:
    def test_mcp_tool_result_compacted_and_retrievable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An mcp__* tool_result with a Linear-shaped JSON object gets
        compacted, carries the CCR marker, and the original is retrievable
        from the CompressionStore under the marker's hash."""
        store = _memory_store(monkeypatch)
        content = _linear_issue_payload()
        router = ContentRouter(ContentRouterConfig(min_section_tokens=10))
        tokenizer = _get_tokenizer()

        messages = _anthropic_messages(TOOL, content)
        result = router.apply(messages, tokenizer)

        tool_result_block = result.messages[1]["content"][0]
        compressed = tool_result_block["content"]
        assert compressed != content
        assert len(compressed) < len(content)

        match = CCR_RETRIEVAL_MARKER_RE.search(compressed)
        assert match is not None, "compacted mcp_json output must carry a CCR retrieve marker"

        ccr_hash = compressed.rsplit("hash=", 1)[1].strip()
        entry = store.retrieve(ccr_hash)
        assert entry is not None
        assert entry.original_content == content
        assert entry.compression_strategy == STRATEGY

    def test_non_mcp_tool_untouched_by_mcp_json_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-mcp__ tool with the identical JSON payload never reaches the
        mcp_json path -- no mcp_json bookkeeping is recorded for it. (It may
        still be compressed by an unrelated strategy; this test only asserts
        the mcp_json path specifically is not the one that touched it.)"""
        _memory_store(monkeypatch)
        content = _linear_issue_payload()
        router = ContentRouter(ContentRouterConfig(min_section_tokens=10))
        tokenizer = _get_tokenizer()

        messages = _anthropic_messages("Bash", content)
        result = router.apply(messages, tokenizer)

        assert f"router:tool_result:{STRATEGY}" not in result.transforms_applied

    def test_excluded_mcp_tool_untouched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An mcp__* tool explicitly listed in exclude_tools is passed
        through unmodified by the mcp_json path."""
        _memory_store(monkeypatch)
        content = _linear_issue_payload()
        router = ContentRouter(ContentRouterConfig(min_section_tokens=10, exclude_tools={TOOL}))
        tokenizer = _get_tokenizer()

        messages = _anthropic_messages(TOOL, content)
        result = router.apply(messages, tokenizer)

        tool_result_block = result.messages[1]["content"][0]
        # Excluded from mcp_json specifically -- but exclude_tools also feeds
        # the router's separate lossless-JSON-minify fold for excluded tools
        # (whitespace-only re-serialization), so assert the *data* survives
        # byte-for-byte rather than requiring the exact source bytes: no key
        # was pruned, no string shortened, no array capped, and above all no
        # CCR retrieve marker was injected.
        assert json.loads(tool_result_block["content"]) == json.loads(content)
        assert not CCR_RETRIEVAL_MARKER_RE.search(tool_result_block["content"])
        assert f"router:tool_result:{STRATEGY}" not in result.transforms_applied

    def test_strategy_bookkeeping_reports_mcp_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The router's own strategy/transform bookkeeping (transforms_applied,
        counted via transforms_summary) records the mcp_json strategy for a
        compacted block, exactly like every other marked strategy."""
        _memory_store(monkeypatch)
        content = _linear_issue_payload()
        router = ContentRouter(ContentRouterConfig(min_section_tokens=10))
        tokenizer = _get_tokenizer()

        messages = _anthropic_messages(TOOL, content)
        result = router.apply(messages, tokenizer)

        expected = f"router:tool_result:{STRATEGY}"
        assert expected in result.transforms_applied
        assert result.transforms_summary[expected] == 1
