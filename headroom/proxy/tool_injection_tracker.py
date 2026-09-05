"""Session-scoped state for sticky memory tool injection."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from typing import TYPE_CHECKING

from headroom.proxy._bounded_session_map import BoundedSessionMap

if TYPE_CHECKING:
    from headroom.proxy.session_tool_store import SessionToolStore


class SessionToolTracker(BoundedSessionMap["OrderedDict[str, bytes]"]):
    """Bounded LRU tracker recording per-session memory-tool injection state.

    When constructed with a `store`, the tracker hydrates its in-memory
    state from it (most recent `max_sessions` sessions, in last-seen
    order) and write-throughs every `record_injection` call. The store is
    optional and purely additive: with none given, behavior is identical
    to the original pure in-memory tracker.
    """

    def _load_entries(
        self, store: SessionToolStore
    ) -> Iterable[tuple[tuple[str, str], OrderedDict[str, bytes]]]:
        for provider, session_id, tools in store.load_all_memory_tools(self._max_sessions):
            if not tools:
                continue
            entry: OrderedDict[str, bytes] = OrderedDict()
            for tool_name, golden_bytes in tools:
                entry[tool_name] = golden_bytes
            yield self._key(provider, session_id), entry

    def should_inject(self, provider: str, session_id: str) -> bool:
        """Return True when this session has previously injected memory tools."""

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
            return len(entry) > 0

    def get_golden_definitions(
        self, provider: str, session_id: str
    ) -> list[tuple[str, bytes]] | None:
        """Return the previously recorded (tool_name, bytes) pairs for a session."""

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
            return [(name, golden_bytes) for name, golden_bytes in entry.items()]

    def record_injection(
        self,
        provider: str,
        session_id: str,
        tool_name: str,
        tool_definition_bytes: bytes,
    ) -> None:
        """Record golden bytes for a memory tool in this provider/session."""

        if not provider:
            raise ValueError("provider must be non-empty")
        if not session_id:
            raise ValueError("session_id must be non-empty")
        if not tool_name:
            raise ValueError("tool_name must be non-empty")
        if not tool_definition_bytes:
            raise ValueError("tool_definition_bytes must be non-empty")

        key = self._key(provider, session_id)
        with self._lock:
            entry = self._sessions.get(key)
            if entry is None:
                entry = OrderedDict()
                self._sessions[key] = entry
            is_new = tool_name not in entry
            position = len(entry)
            if is_new:
                entry[tool_name] = tool_definition_bytes
            self._sessions.move_to_end(key)
            self._bound()
            if is_new and self._store is not None:
                self._store.record_memory_tool(
                    provider=provider,
                    session_id=session_id,
                    tool_name=tool_name,
                    golden_bytes=tool_definition_bytes,
                    position=position,
                    max_sessions=self._max_sessions,
                )
