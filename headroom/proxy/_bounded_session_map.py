"""Shared bounded, store-backed LRU session map.

``SessionToolTracker`` (memory-tool injection) and ``SessionCcrTracker``
(CCR retrieval-tool injection) both grew the exact same persistence shape:
``__init__(max_sessions, store=None)`` hydrates an in-memory ``OrderedDict``
from the store (most recent ``max_sessions`` sessions, oldest first), then
trims it back down to ``max_sessions`` entries — and every later mutation
that adds an entry runs the same trim-then-maybe-write-through pattern.
This module owns that shape once; each tracker supplies ``_load_entries``
(how to read its own value type back from the store) and calls ``_bound()``
after a mutation instead of re-writing the pop loop itself.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Iterable
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from headroom.proxy.session_tool_store import SessionToolStore

_V = TypeVar("_V")


class BoundedSessionMap(Generic[_V]):
    """Base class for a provider/session-keyed, bounded, optionally
    store-backed LRU map. Not meant to be used directly — subclasses add
    their own domain methods on top of ``_sessions``, ``_lock``, and
    ``_bound()``."""

    def __init__(self, max_sessions: int, store: SessionToolStore | None = None) -> None:
        if max_sessions <= 0:
            raise ValueError("max_sessions must be > 0")
        self._max_sessions = max_sessions
        self._lock = threading.RLock()
        self._store = store
        self._sessions: OrderedDict[tuple[str, str], _V] = OrderedDict()
        if store is not None:
            self._hydrate(store)

    def _load_entries(self, store: SessionToolStore) -> Iterable[tuple[tuple[str, str], _V]]:
        """Read this tracker's most recent ``max_sessions`` sessions back
        from the store, as ``(key, value)`` pairs in last-seen order.
        Subclasses must override."""

        raise NotImplementedError

    def _hydrate(self, store: SessionToolStore) -> None:
        for key, value in self._load_entries(store):
            self._sessions[key] = value
        self._bound()

    def _bound(self) -> None:
        """Pop the oldest sessions until the map is back within
        ``max_sessions``. Callers hold ``self._lock`` already."""

        while len(self._sessions) > self._max_sessions:
            self._sessions.popitem(last=False)

    @property
    def active_sessions(self) -> int:
        with self._lock:
            return len(self._sessions)

    @staticmethod
    def _key(provider: str, session_id: str) -> tuple[str, str]:
        return (provider, session_id)

    def reset(self) -> None:
        """Clear all session state."""

        with self._lock:
            self._sessions.clear()
