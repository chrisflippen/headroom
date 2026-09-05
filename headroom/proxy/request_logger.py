"""Request logger for the Headroom proxy.

Logs requests to an in-memory deque and optionally to a JSONL file.

Extracted from server.py for maintainability.

Phase G PR-G3 (P4-45): base64-encoded image payloads in the
``request_messages`` / ``response_content`` are redacted before
write to keep request logs small. Multi-MB base64 strings would
otherwise saturate the JSONL log and the in-memory deque.

Remediation (M2, M5): the redactor now ONLY fires inside known
image-bearing JSON paths or against strings that carry an explicit
``data:image/...;base64,`` URL prefix. The earlier "density
heuristic" over-fired on encrypted blobs, signed tokens, minified
JSON, and tool outputs. The replacement placeholder now reports
the UTF-8 byte length under a ``bytes=`` label (was character
length; for the ASCII base64 alphabet the two happen to coincide
but the label is now accurate for any future Unicode payload).
"""

from __future__ import annotations

import json
import logging
import sys
from collections import deque
from dataclasses import asdict
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..memory.tracker import ComponentStats

from headroom.proxy import request_log_redaction_policy
from headroom.proxy.models import RequestLog

IMAGE_BASE64_REDACT_THRESHOLD_BYTES = (
    request_log_redaction_policy.IMAGE_BASE64_REDACT_THRESHOLD_BYTES
)
IMAGE_BASE64_REPLACEMENT_TEMPLATE = request_log_redaction_policy.IMAGE_BASE64_REPLACEMENT_TEMPLATE
IMAGE_BEARING_FIELD_NAMES = request_log_redaction_policy.IMAGE_BEARING_FIELD_NAMES
_is_base64_image_payload = request_log_redaction_policy.is_base64_image_payload

# D1G-2249: bound at store time how much stored text a single message body
# can hold. Without this, one 900k-token conversation stored under
# ``log_full_messages`` is ~2.6MB on its own; this caps any one string leaf
# (a message's ``content``, a text block's ``text``, a tool_result's nested
# text, ...) so a single huge entry cannot dominate memory even before the
# MESSAGE_BODY_RETENTION window below kicks in.
MAX_STORED_TEXT_CHARS = 4_000


def truncate_stored_text(value: Any, *, limit: int = MAX_STORED_TEXT_CHARS) -> Any:
    """Recursively truncate every string leaf in ``value`` to ``limit`` chars.

    Walks dicts and lists (the shape of a messages array or a single
    ``response_content`` string) and truncates any string longer than
    ``limit``, appending a visible ``…[truncated N chars]`` marker so the
    dashboard and ``headroom inspect`` never render a silently-clipped body
    as if it were complete. Idempotent — truncating twice is a no-op because
    the marker keeps the result under the limit... except the marker itself
    adds a few characters, which is fine: it never grows unbounded.
    """
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        overflow = len(value) - limit
        return f"{value[:limit]}…[truncated {overflow} chars]"
    if isinstance(value, list):
        return [truncate_stored_text(v, limit=limit) for v in value]
    if isinstance(value, dict):
        return {k: truncate_stored_text(v, limit=limit) for k, v in value.items()}
    return value


logger = logging.getLogger(__name__)

# Constants for log redaction counter export (Prometheus). The
# Python proxy's ``/metrics`` exporter surfaces
# ``proxy_image_generation_call_log_redacted_total`` from this
# module-level counter. C3 remediation: the Rust proxy previously
# held a dead counter; that's been removed in favour of this
# Python-side counter, which is the natural owner.
_redactions_total: int = 0
_redactions_lock = Lock()


def redactions_total() -> int:
    """Return the running count of base64 redactions performed.

    Exposed for unit tests, the legacy Python ``/stats`` endpoint,
    and the Prometheus exporter
    (``proxy_image_generation_call_log_redacted_total``).
    """
    with _redactions_lock:
        return _redactions_total


def redact_image_base64(payload: Any) -> Any:
    """Public entry point for base64-image redaction.

    Walks ``payload`` (a dict, list, or string) and replaces any
    over-threshold base64 string with a size-only placeholder.
    Idempotent — applying twice yields the same structure.
    """
    global _redactions_total

    result = request_log_redaction_policy.redact_image_base64_value(payload)
    if result.redactions:
        with _redactions_lock:
            _redactions_total += result.redactions
    return result.value


class RequestLogger:
    """Log requests to JSONL file.

    Uses a deque with max 10,000 entries to prevent unbounded memory growth.
    Gracefully degrades to in-memory-only if the log file cannot be written
    (read-only filesystem, permissions error, etc.).
    """

    MAX_LOG_ENTRIES = 10_000

    # D1G-2249: even with MAX_LOG_ENTRIES bounding entry *count*, storing full
    # request/compressed/response bodies on all 10,000 entries under
    # ``log_full_messages`` is unbounded in bytes — a 900k-token conversation
    # is ~2.6MB per entry. Only the most recent MESSAGE_BODY_RETENTION entries
    # keep their bodies; older entries have request_messages/
    # compressed_messages/response_content dropped (set to None) as new
    # entries arrive, but keep every other field (token counts, transforms,
    # ...) so they still count towards stats.
    MESSAGE_BODY_RETENTION = 100

    def __init__(self, log_file: str | None = None, log_full_messages: bool = False):
        self.log_file = Path(log_file) if log_file else None
        self.log_full_messages = log_full_messages
        # Use deque with maxlen for automatic FIFO eviction
        self._logs: deque[RequestLog] = deque(maxlen=self.MAX_LOG_ENTRIES)
        # Tracks only the entries that currently hold message bodies, oldest
        # first, so we can clear the one about to fall out of the retention
        # window in O(1) instead of sweeping ``self._logs`` on every call.
        self._body_retention: deque[RequestLog] = deque(maxlen=self.MESSAGE_BODY_RETENTION)

        if self.log_file:
            try:
                self.log_file.parent.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                logger.warning(
                    "Cannot create log directory %s: %s — logging to memory only",
                    self.log_file.parent,
                    e,
                )
                self.log_file = None

    def log(self, entry: RequestLog):
        """Log a request. Oldest entries are automatically removed when limit reached.

        Phase G PR-G3 (P4-45): base64-encoded image payloads in
        ``request_messages`` / ``compressed_messages`` / ``response_content``
        are redacted before write. Redaction also applies to the in-memory
        deque so the ``/stats/recent_requests`` endpoint never serves a
        multi-MB image either.

        D1G-2249: after redaction, remaining text is truncated to
        ``MAX_STORED_TEXT_CHARS`` per string leaf, and bodies are dropped
        entirely from entries older than ``MESSAGE_BODY_RETENTION`` — see the
        class docstring constants above.
        """
        # Redact image payloads in-place on the deque entry so memory
        # use stays bounded. We mutate the dataclass fields rather
        # than wrapping the entry to keep ``get_recent`` /
        # ``get_recent_with_messages`` unchanged.
        if entry.request_messages is not None:
            entry.request_messages = truncate_stored_text(
                redact_image_base64(entry.request_messages)
            )
        if entry.compressed_messages is not None:
            entry.compressed_messages = truncate_stored_text(
                redact_image_base64(entry.compressed_messages)
            )
        if entry.response_content is not None:
            entry.response_content = truncate_stored_text(
                redact_image_base64(entry.response_content)
            )

        has_body = (
            entry.request_messages is not None
            or entry.compressed_messages is not None
            or entry.response_content is not None
        )
        if has_body:
            retention = self._body_retention
            if len(retention) == retention.maxlen:
                # About to be evicted by the append below — clear its bodies
                # now rather than relying on it being garbage collected,
                # since the same object is still referenced from self._logs.
                aged_out = retention[0]
                aged_out.request_messages = None
                aged_out.compressed_messages = None
                aged_out.response_content = None
            retention.append(entry)

        self._logs.append(entry)

        if self.log_file:
            try:
                with open(self.log_file, "a") as f:
                    log_dict = asdict(entry)
                    if not self.log_full_messages:
                        log_dict.pop("request_messages", None)
                        log_dict.pop("compressed_messages", None)
                        log_dict.pop("response_content", None)
                    f.write(json.dumps(log_dict) + "\n")
            except OSError:
                pass  # Graceful degradation: memory-only logging continues

    def get_recent(self, n: int = 100) -> list[dict]:
        """Get recent log entries (without request/compressed messages and response_content)."""
        # Convert deque to list for slicing (deque doesn't support slicing)
        entries = list(self._logs)[-n:]
        return [
            {
                k: v
                for k, v in asdict(e).items()
                if k not in ("request_messages", "compressed_messages", "response_content")
            }
            for e in entries
        ]

    def get_recent_with_messages(self, n: int = 20) -> list[dict]:
        """Get recent log entries including full request/response messages."""
        entries = list(self._logs)[-n:]
        return [asdict(e) for e in entries]

    def stats(self) -> dict:
        """Get logging statistics."""
        return {
            "total_logged": len(self._logs),
            "log_file": str(self.log_file) if self.log_file else None,
        }

    def get_memory_stats(self) -> ComponentStats:
        """Get memory statistics for the MemoryTracker.

        Returns:
            ComponentStats with current memory usage.
        """
        from ..memory.tracker import ComponentStats

        # Calculate size
        size_bytes = sys.getsizeof(self._logs)

        for log_entry in self._logs:
            size_bytes += sys.getsizeof(log_entry)
            # Add string fields
            if log_entry.request_id:
                size_bytes += len(log_entry.request_id)
            if log_entry.provider:
                size_bytes += len(log_entry.provider)
            if log_entry.model:
                size_bytes += len(log_entry.model)
            if log_entry.error:
                size_bytes += len(log_entry.error)
            # Messages and response can be large
            if log_entry.request_messages:
                size_bytes += sys.getsizeof(log_entry.request_messages)
            if log_entry.compressed_messages:
                size_bytes += sys.getsizeof(log_entry.compressed_messages)
            if log_entry.response_content:
                size_bytes += len(log_entry.response_content)

        return ComponentStats(
            name="request_logger",
            entry_count=len(self._logs),
            size_bytes=size_bytes,
            budget_bytes=None,
            hits=0,
            misses=0,
            evictions=0,
        )
