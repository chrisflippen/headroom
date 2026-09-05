"""Session-scoped state for sticky CCR retrieval tool injection."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from headroom.proxy._bounded_session_map import BoundedSessionMap

if TYPE_CHECKING:
    from headroom.proxy.session_tool_store import SessionToolStore


class SessionCcrTracker(BoundedSessionMap["tuple[bool, bytes | None]"]):
    """Bounded LRU tracker recording per-provider/session CCR state.

    When constructed with a `store`, the tracker hydrates its in-memory
    state from it (most recent `max_sessions` sessions, in last-seen
    order) and write-throughs every `record_ccr_done` call. The store is
    optional and purely additive: with none given, behavior is identical
    to the original pure in-memory tracker.
    """

    def _load_entries(
        self, store: SessionToolStore
    ) -> Iterable[tuple[tuple[str, str], tuple[bool, bytes | None]]]:
        for provider, session_id, golden_bytes in store.load_all_ccr_sessions(self._max_sessions):
            yield self._key(provider, session_id), (True, golden_bytes)

    def has_done_ccr(self, provider: str, session_id: str) -> bool:
        """Return True when this session has previously performed CCR."""

        if not provider:
            raise ValueError("provider must be non-empty")
        if not session_id:
            raise ValueError("session_id must be non-empty")
        key = self._key(provider, session_id)
        with self._lock:
            entry = self._sessions.get(key)
            if entry is None:
                return False
            self._sessions.move_to_end(key)
            return entry[0]

    def get_golden_tool_bytes(self, provider: str, session_id: str) -> bytes | None:
        """Return recorded golden CCR tool-definition bytes, if any."""

        if not provider:
            raise ValueError("provider must be non-empty")
        if not session_id:
            raise ValueError("session_id must be non-empty")
        key = self._key(provider, session_id)
        with self._lock:
            entry = self._sessions.get(key)
            if entry is None:
                return None
            self._sessions.move_to_end(key)
            return entry[1]

    def record_ccr_done(
        self,
        provider: str,
        session_id: str,
        golden_tool_bytes: bytes,
    ) -> None:
        """Mark the session as having performed CCR and pin golden tool bytes."""

        if not provider:
            raise ValueError("provider must be non-empty")
        if not session_id:
            raise ValueError("session_id must be non-empty")
        if not golden_tool_bytes:
            raise ValueError("golden_tool_bytes must be non-empty")
        key = self._key(provider, session_id)
        with self._lock:
            existing = self._sessions.get(key)
            if existing is None:
                pinned = golden_tool_bytes
            else:
                pinned = existing[1] if existing[1] is not None else golden_tool_bytes
            self._sessions[key] = (True, pinned)
            self._sessions.move_to_end(key)
            self._bound()
            if self._store is not None:
                self._store.record_ccr_done(
                    provider=provider,
                    session_id=session_id,
                    golden_bytes=pinned,
                    max_sessions=self._max_sessions,
                )
