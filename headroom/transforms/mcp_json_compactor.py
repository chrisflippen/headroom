"""Deterministic JSON compactor for MCP tool echoes.

MCP tools that answer in JSON — Linear's ``save_issue``/``get_issue``/
``list_issues`` echoing the whole issue back, Supabase, Firebase — land in
context verbatim at roughly a thousand tokens a call. The content router's
``json_bloat`` waste signal flags this traffic, but nothing compresses it:
SmartCrusher only folds JSON *arrays*, so a single object (or an array whose
items aren't rows of a uniform schema) falls through to the general lossy
prose path, or gets excluded/skipped outright.

This module is a small, fully deterministic transform, not a model call:

- drop keys whose value is empty (``null``, ``""``, ``[]``, ``{}``),
  recursively;
- shorten any string value longer than 300 chars to its first 240 chars plus
  an ``" …[+N chars]"`` suffix;
- cap arrays at 50 items, appending a trailing ``"…[+N items]"`` sentinel
  string;
- re-serialize compactly (``separators=(",", ":")``), preserving the
  server's original key order (no ``sort_keys``);
- store the ORIGINAL text in the CCR compression store and append the
  standard ``Retrieve original: hash=…`` marker, so this counts as a CCR
  *marked* strategy and is never treated as unrecoverable for tool ground
  truth;
- only emit a result when it saves at least 15% of characters — otherwise
  the block is returned untouched, matching the caller's already-compressed
  and "not worth it" conventions.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ..config import is_tool_excluded, mcp_json_compact_enabled

logger = logging.getLogger(__name__)

STRATEGY = "mcp_json"

_MAX_STRING_CHARS = 300
_STRING_TRUNCATE_TO = 240
_MAX_ARRAY_ITEMS = 50
_MIN_SAVINGS_FRACTION = 0.15


@dataclass
class McpJsonCompactResult:
    """Result of an MCP JSON compaction attempt."""

    compressed: str
    original: str
    was_modified: bool
    strategy: str = STRATEGY
    ccr_hash: str | None = None

    @property
    def compression_ratio(self) -> float:
        if not self.original:
            return 0.0
        return len(self.compressed) / len(self.original)


def _is_empty(value: Any) -> bool:
    """True for the empties this compactor prunes: null, "", [], {}."""
    if value is None:
        return True
    if isinstance(value, str | list | dict):
        return len(value) == 0
    return False


def _shorten_string(value: str) -> str:
    if len(value) <= _MAX_STRING_CHARS:
        return value
    overflow = len(value) - _STRING_TRUNCATE_TO
    return f"{value[:_STRING_TRUNCATE_TO]} …[+{overflow} chars]"


def _transform(node: Any) -> Any:
    """Recursively prune empties, shorten long strings, cap long arrays."""
    if isinstance(node, dict):
        pruned: dict[str, Any] = {}
        for key, value in node.items():
            if _is_empty(value):
                continue
            pruned[key] = _transform(value)
        return pruned
    if isinstance(node, list):
        capped = node[:_MAX_ARRAY_ITEMS]
        items = [_transform(item) for item in capped]
        overflow = len(node) - _MAX_ARRAY_ITEMS
        if overflow > 0:
            items.append(f"…[+{overflow} items]")
        return items
    if isinstance(node, str):
        return _shorten_string(node)
    return node


def _parse_json_object_or_array(text: str) -> Any | None:
    """Parse ``text`` as JSON, but only accept a top-level object or array."""
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict | list):
        return None
    return parsed


def _store_original(original: str, compressed: str) -> str | None:
    """Persist the original to the CompressionStore; returns its hash."""
    try:
        from ..cache.compression_store import get_compression_store
    except ImportError as e:  # pragma: no cover - store ships with headroom
        logger.warning("CCR store import failed; mcp_json compaction skipped: %s", e)
        return None
    try:
        store: Any = get_compression_store()
        stored = store.store(original, compressed, compression_strategy=STRATEGY)
        return str(stored) if stored else None
    except Exception as e:
        logger.warning("CCR store write failed; mcp_json compaction skipped: %s", e)
        return None


def compact_mcp_json(
    content: str,
    tool_name: str,
    exclude_tools: Iterable[str] = (),
) -> McpJsonCompactResult:
    """Compact a JSON ``tool_result`` echo from an MCP tool, if it qualifies.

    Only fires for tools whose name starts with ``mcp__`` and whose text
    parses as a JSON object or array. Leaves ``content`` untouched (returns
    ``was_modified=False``) when: the env opt-out is set, the tool name isn't
    an MCP tool, the tool is excluded, the text isn't JSON, or the savings
    from compacting fall under 15% of the original length.
    """
    if not mcp_json_compact_enabled():
        return McpJsonCompactResult(compressed=content, original=content, was_modified=False)
    if not tool_name.startswith("mcp__"):
        return McpJsonCompactResult(compressed=content, original=content, was_modified=False)
    if is_tool_excluded(tool_name, exclude_tools):
        return McpJsonCompactResult(compressed=content, original=content, was_modified=False)

    parsed = _parse_json_object_or_array(content)
    if parsed is None:
        return McpJsonCompactResult(compressed=content, original=content, was_modified=False)

    transformed = _transform(parsed)
    body = json.dumps(transformed, separators=(",", ":"), ensure_ascii=False)

    savings = len(content) - len(body)
    if savings < len(content) * _MIN_SAVINGS_FRACTION:
        return McpJsonCompactResult(compressed=content, original=content, was_modified=False)

    ccr_hash = _store_original(content, body)
    if ccr_hash is None:
        # Can't guarantee recovery of the original -- never emit a lossy form.
        return McpJsonCompactResult(compressed=content, original=content, was_modified=False)

    marker = f"Retrieve original: hash={ccr_hash}"
    compressed = f"{body}\n{marker}"

    # Re-check the gate against the final marked text: a marker can eat back
    # into small savings, and the "untouched unless it actually helps"
    # contract should hold for what the model actually sees.
    if len(content) - len(compressed) < len(content) * _MIN_SAVINGS_FRACTION:
        return McpJsonCompactResult(compressed=content, original=content, was_modified=False)

    return McpJsonCompactResult(
        compressed=compressed,
        original=content,
        was_modified=True,
        ccr_hash=ccr_hash,
    )


__all__ = [
    "STRATEGY",
    "McpJsonCompactResult",
    "compact_mcp_json",
]
