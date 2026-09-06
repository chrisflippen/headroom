"""Tool-result aging: retire old ``tool_result`` blocks into fixed stubs.

Problem this solves
--------------------
A day-long Claude Code session can reach 600k+ tokens per request, almost
all of it old ``tool_result`` blocks the model read hours ago and is very
unlikely to need again. Christopher rejected the two obvious fixes —
summarizing (Claude Code's own auto-compact / server-side compaction) and
server-side ``clear_tool_uses`` — because both are lossy and out of
headroom's control. The ruling: headroom retires old tool results itself,
into a fixed three-line stub, and keeps the real bytes retrievable through
the CCR (compress-cache-retrieve) store so the model can ask for them back
with the ``headroom_retrieve`` tool if it genuinely needs them.

Purity contract
----------------
:func:`age_tool_results` is a PURE function of the message list: no
per-session state, nothing keyed on a request or session id. Feed it the
same conversation twice and it ages exactly the same blocks the exact same
way. This matters because the proxy's prompt-cache machinery
(``headroom/cache/prefix_tracker.py``) replays previously-forwarded bytes
across turns whenever the content looks unchanged from last turn; a
stateful aging pass would either flip an already-aged stub back to raw
text (undoing its own savings) or never settle into a stable, cacheable
prefix.

Rung rule, in plain words
--------------------------
Every change to the aged set rewrites the leading part of the request,
and every turn that rewrites a byte the provider already has cached busts
the provider's prompt cache from that point on: the whole conversation is
re-sent at the cache-write price instead of the cache-read price, about
twelve times dearer. So the rule is not "how much can we retire" but "how
rarely can we change what is retired while still retiring enough".

The first version of this module aged in fixed 10,000-token batches. On
the night of 5–6 September 2026 that rewrote a 300k-token conversation
every three to six turns: the aging saved about 2 million base-priced
tokens of reads and cost about 10 million in cache rewrites. Fixed
batches make the number of rewrites grow in step with the conversation.

Instead, the boundary climbs a ladder of doubling rungs:
``first_batch_tokens``, twice that, four times, and so on. Concretely:

1. List every aging candidate oldest to newest and take a running token
   total (a cumulative sum) as you walk the list.
2. Take the grand total across every candidate. The boundary is the
   largest rung that fits under it; below the first rung nothing is aged.
3. Walk the running total from the front and stop at the first candidate
   whose running total reaches that boundary. Every candidate up to and
   including that one is aged; everything after it is left as raw text
   until the next rung is reached.

Because the running total only grows as the conversation grows, the rung
only ever moves up, and the walk in step 3 always lands on the same
candidate or a later one than it did last time. So a block that becomes a
stub never flips back to raw text, and the aged set changes only when the
candidates double — the cached prefix is rewritten a logarithmic number
of times over the life of a session, not once per batch.

Why the first rung is 128,000 tokens: a rewrite of a conversation of C
tokens costs about 1.15·C base-priced tokens (a cache write at 1.25× in
place of a cache read at 0.1×). Aging T tokens then saves 0.1·T per turn
until the next rung, which arrives after roughly T/g turns where g is the
tool-result tokens a turn adds (about 5k in a Claude Code session). The
rewrite pays for itself when T² ≥ 11.5·C·g. With the 150k trigger, the
first rung is reached at C ≈ 280k, and 128k² ≈ 11.5·280k·5k, so the first
rewrite breaks even and every later rung, being twice as large, pays
back more than twice as much.

Scope
-----
Anthropic message shape only (the Claude Code request path):
``{"role": "user", "content": [{"type": "tool_result", ...}, ...]}``.
"""

from __future__ import annotations

import copy
import hashlib
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from headroom.ccr.tool_injection import CCR_TOOL_NAME

logger = logging.getLogger("headroom.proxy")

# ── config ──────────────────────────────────────────────────────────────

DEFAULT_TRIGGER_TOKENS = 150_000
DEFAULT_KEEP_NEWEST = 20
# See "Why the first rung is 128,000 tokens" in the module docstring.
DEFAULT_FIRST_BATCH_TOKENS = 128_000
# 36 hours — long enough to outlive a day-long session. The CCR store's own
# default TTL (30 minutes, DEFAULT_CCR_TTL_SECONDS in compression_store.py)
# is sized for ordinary compression entries and would expire long before a
# long-running agent session ever asks for the content back. The store is
# SQLite-backed on disk (~/.headroom/ccr_store.db), so a long TTL costs disk
# space, not memory.
DEFAULT_TTL_SECONDS = 36 * 60 * 60


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning(
            "tool_result_aging: %s=%r is not an integer; using default %d",
            name,
            raw,
            default,
        )
        return default
    if value <= 0:
        logger.warning(
            "tool_result_aging: %s=%r must be positive; using default %d",
            name,
            raw,
            default,
        )
        return default
    return value


@dataclass(frozen=True)
class ToolResultAgingConfig:
    """Config for :func:`age_tool_results`. Env overrides are read once via
    :meth:`from_env` — this module owns its own env vars and does not touch
    ``headroom/config.py``."""

    enabled: bool = True
    trigger_tokens: int = DEFAULT_TRIGGER_TOKENS
    keep_newest: int = DEFAULT_KEEP_NEWEST
    first_batch_tokens: int = DEFAULT_FIRST_BATCH_TOKENS
    ttl_seconds: int = DEFAULT_TTL_SECONDS

    @classmethod
    def from_env(cls) -> ToolResultAgingConfig:
        return cls(
            enabled=_env_bool("HEADROOM_AGING_ENABLED", True),
            trigger_tokens=_env_int("HEADROOM_AGING_TRIGGER_TOKENS", DEFAULT_TRIGGER_TOKENS),
            keep_newest=_env_int("HEADROOM_AGING_KEEP_NEWEST", DEFAULT_KEEP_NEWEST),
            first_batch_tokens=_env_int(
                "HEADROOM_AGING_FIRST_BATCH_TOKENS", DEFAULT_FIRST_BATCH_TOKENS
            ),
            ttl_seconds=_env_int("HEADROOM_AGING_TTL_SECONDS", DEFAULT_TTL_SECONDS),
        )


@dataclass(frozen=True)
class AgingResult:
    """Result of :func:`age_tool_results`.

    ``messages`` is always a fresh list (the input is never mutated), even
    when nothing was aged. ``aged_hashes`` is ``(tool_use_id, hash)`` for
    every block that was actually stubbed, oldest first.
    """

    messages: list[dict[str, Any]]
    aged_block_count: int = 0
    aged_tokens: int = 0
    aged_hashes: list[tuple[str, str]] = field(default_factory=list)


# ── tool_use_id -> (name, input) index ─────────────────────────────────
#
# Copied (not imported) from headroom/proxy/interceptors/base.py's
# _build_tool_use_index: importing it here would pull in
# headroom.proxy.interceptors.base -> headroom.config -> ... at module load
# time, and headroom/transforms/ is a lower layer than headroom/proxy/ in
# every other import direction in this codebase. Keeping this copy avoids
# introducing the first transforms-import-proxy edge. Only the Anthropic
# branch is needed here (this module is Anthropic-only).


def _build_tool_use_index(
    messages: list[dict[str, Any]],
) -> dict[str, tuple[str | None, dict[str, Any]]]:
    index: dict[str, tuple[str | None, dict[str, Any]]] = {}
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                bid = block.get("id")
                if isinstance(bid, str):
                    index[bid] = (block.get("name"), block.get("input") or {})
    return index


# ── candidate extraction ────────────────────────────────────────────────

_MARKER_SNIPPETS = ("Retrieve original: hash=", "<<ccr:")

_ASK_SUMMARY_KEYS = ("path", "file_path", "pattern", "command", "query")


def _extract_result_text(block: dict[str, Any]) -> str | None:
    inner = block.get("content")
    if isinstance(inner, str):
        return inner or None
    if isinstance(inner, list):
        texts = [
            b.get("text", "") for b in inner if isinstance(b, dict) and b.get("type") == "text"
        ]
        joined = "\n".join(t for t in texts if t)
        return joined or None
    return None


def _is_non_text_result(block: dict[str, Any]) -> bool:
    """True when the tool_result carries a non-text (e.g. image) block."""
    inner = block.get("content")
    if isinstance(inner, list):
        return any(isinstance(b, dict) and b.get("type") != "text" for b in inner)
    return False


def _already_marked(text: str) -> bool:
    return any(snippet in text for snippet in _MARKER_SNIPPETS)


def _summarize_ask(tool_input: dict[str, Any]) -> str:
    if not isinstance(tool_input, dict):
        return ""
    for key in _ASK_SUMMARY_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().replace("\n", " ")[:100]
    return ""


def _first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:120]
    return ""


def _build_stub(
    tool_name: str | None,
    tool_input: dict[str, Any],
    original_text: str,
    token_count: int,
    hash_key: str,
) -> str:
    header = f"[aged tool result · {tool_name or 'unknown'} · {token_count} tokens]"
    ask = _summarize_ask(tool_input)
    orig_line = _first_nonempty_line(original_text)
    if ask and orig_line:
        line2 = f"{ask} — {orig_line}"
    else:
        line2 = ask or orig_line or "(no preview available)"
    footer = f"Retrieve original: hash={hash_key}"
    return "\n".join([header, line2, footer])


@dataclass
class _Candidate:
    message_index: int
    block_index: int
    tool_use_id: str | None
    tool_name: str | None
    tool_input: dict[str, Any]
    text: str
    tokens: int


def _collect_candidates(
    messages: list[dict[str, Any]],
    *,
    tokenizer: Any,
    keep_newest: int,
) -> list[_Candidate]:
    tool_use_index = _build_tool_use_index(messages)

    all_results: list[tuple[int, int, dict[str, Any]]] = []
    for m_idx, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for b_idx, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                all_results.append((m_idx, b_idx, block))

    if not all_results:
        return []

    # Never touch the newest `keep_newest` tool_results, regardless of size.
    keep_from = max(0, len(all_results) - max(keep_newest, 0))
    eligible = all_results[:keep_from]

    candidates: list[_Candidate] = []
    for m_idx, b_idx, block in eligible:
        text = _extract_result_text(block)
        if not text:
            continue
        if _is_non_text_result(block):
            continue
        if _already_marked(text):
            continue

        tool_use_id = block.get("tool_use_id")
        tool_use_id = tool_use_id if isinstance(tool_use_id, str) else None
        tool_name, tool_input = (
            tool_use_index.get(tool_use_id, (None, {})) if tool_use_id else (None, {})
        )
        if tool_name == CCR_TOOL_NAME:
            continue

        try:
            token_count = tokenizer.count_text(text)
        except Exception:  # noqa: BLE001 — a bad tokenizer call must not break the turn
            logger.debug(
                "tool_result_aging: count_text failed for tool_use_id=%s; skipping candidate",
                tool_use_id,
                exc_info=True,
            )
            continue

        candidates.append(
            _Candidate(
                message_index=m_idx,
                block_index=b_idx,
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                tool_input=tool_input,
                text=text,
                tokens=token_count,
            )
        )

    return candidates


def _largest_rung_at_or_below(total: int, first_rung: int) -> int:
    """The largest of ``first_rung, 2*first_rung, 4*first_rung, ...`` that is
    at most ``total``; ``0`` when ``total`` is below the first rung."""
    if first_rung <= 0 or total < first_rung:
        return 0
    rung = first_rung
    while rung * 2 <= total:
        rung *= 2
    return rung


def _select_aged_batch(candidates: list[_Candidate], first_batch_tokens: int) -> list[_Candidate]:
    """Rung-boundary walk — see the module docstring for the plain-words rule."""
    if not candidates:
        return []

    cumulative = 0
    cumsum: list[int] = []
    for c in candidates:
        cumulative += c.tokens
        cumsum.append(cumulative)

    boundary = _largest_rung_at_or_below(cumsum[-1], first_batch_tokens)
    if boundary == 0:
        return []

    cutoff = 0
    for i, running in enumerate(cumsum):
        if running >= boundary:
            cutoff = i + 1
            break
    return candidates[:cutoff]


def age_tool_results(
    messages: list[dict[str, Any]],
    *,
    tokenizer: Any,
    config: ToolResultAgingConfig,
    total_tokens: int | None = None,
) -> AgingResult:
    """Retire old ``tool_result`` blocks into retrievable stubs.

    Pure function of ``messages`` — see the module docstring's purity
    contract. Never mutates the input; ``messages`` on the returned
    :class:`AgingResult` is always a distinct list (a copy when anything
    was aged, the same list reference when nothing changed).

    ``total_tokens`` lets a caller that has already counted the whole
    conversation (the proxy counts every request once, off the event loop)
    skip a second full count; when omitted it is counted here.
    """
    if not config.enabled:
        return AgingResult(messages=messages)

    if total_tokens is None:
        try:
            total_tokens = tokenizer.count_messages(messages)
        except Exception:  # noqa: BLE001 — fail open, never break the turn on a bad count
            logger.warning(
                "tool_result_aging: count_messages failed; skipping this turn", exc_info=True
            )
            return AgingResult(messages=messages)

    if total_tokens <= config.trigger_tokens:
        return AgingResult(messages=messages)

    candidates = _collect_candidates(messages, tokenizer=tokenizer, keep_newest=config.keep_newest)
    if not candidates:
        return AgingResult(messages=messages)

    aged_candidates = _select_aged_batch(candidates, config.first_batch_tokens)
    if not aged_candidates:
        return AgingResult(messages=messages)

    # Local import: avoids importing the CCR store (and its transitive
    # backend-selection machinery) for every caller of this module — most
    # turns never reach this line (below trigger_tokens).
    from headroom.cache.compression_store import get_compression_store

    store = get_compression_store()

    aged_by_position: dict[tuple[int, int], str] = {}
    aged_hashes: list[tuple[str, str]] = []
    aged_tokens_total = 0

    for c in aged_candidates:
        tool_use_id = c.tool_use_id or ""
        explicit_hash = hashlib.sha256(f"{tool_use_id}:{c.text}".encode()).hexdigest()[:24]
        try:
            hash_key = store.store(
                original=c.text,
                compressed="",
                tool_name=c.tool_name,
                tool_call_id=c.tool_use_id,
                compression_strategy="tool_result_aging",
                ttl=config.ttl_seconds,
                explicit_hash=explicit_hash,
            )
        except Exception as e:  # noqa: BLE001 — storage failure must never produce
            # an unrecoverable stub: leave this block exactly as it was.
            logger.warning(
                "tool_result_aging: CCR store failed for tool_use_id=%s (%s: %s); "
                "leaving this tool_result untouched",
                c.tool_use_id,
                type(e).__name__,
                e,
            )
            continue

        stub = _build_stub(c.tool_name, c.tool_input, c.text, c.tokens, hash_key)
        aged_by_position[(c.message_index, c.block_index)] = stub
        aged_hashes.append((tool_use_id, hash_key))
        aged_tokens_total += c.tokens

    if not aged_by_position:
        return AgingResult(messages=messages)

    # Copy only the messages that change: the input is never mutated, and a
    # deep copy of an 800-message conversation to touch a handful of blocks
    # would cost more than the aging saves.
    new_messages = list(messages)
    for m_idx in {m for m, _ in aged_by_position}:
        new_messages[m_idx] = copy.deepcopy(messages[m_idx])
    for (m_idx, b_idx), stub in aged_by_position.items():
        block = new_messages[m_idx]["content"][b_idx]
        inner = block.get("content")
        if isinstance(inner, list):
            block["content"] = [{"type": "text", "text": stub}]
        else:
            block["content"] = stub

    return AgingResult(
        messages=new_messages,
        aged_block_count=len(aged_by_position),
        aged_tokens=aged_tokens_total,
        aged_hashes=aged_hashes,
    )
