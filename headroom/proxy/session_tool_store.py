"""SQLite-backed persistence for the session tool-injection trackers.

`SessionToolTracker` (memory tools) and `SessionCcrTracker` (the CCR
`headroom_retrieve` tool) are both pure in-memory bounded LRUs: a
proxy restart forgets every session's sticky-injection state, which
flips the tool list on that session's next turn and busts Anthropic's
prompt cache for the whole prefix. This store gives both trackers a
write-through row per (provider, session_id) so a freshly constructed
tracker can hydrate itself back to where the last restart left off.

Mirrors `headroom/cache/backends/sqlite.py` conventions: WAL mode, a
generous busy timeout, one connection per store instance guarded by
the owning tracker's existing lock. Any sqlite failure (unwritable
directory, corrupt file, ...) is caught, logged once, and turns every
method into a silent no-op — a request must never fail because this
store is unavailable; the tracker keeps working in memory only.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_tool_defs (
    provider TEXT NOT NULL,
    session_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    golden_bytes BLOB NOT NULL,
    position INTEGER NOT NULL,
    last_seen REAL NOT NULL,
    PRIMARY KEY (provider, session_id, tool_name)
);
CREATE INDEX IF NOT EXISTS idx_memory_tool_defs_last_seen ON memory_tool_defs (last_seen);

CREATE TABLE IF NOT EXISTS ccr_sessions (
    provider TEXT NOT NULL,
    session_id TEXT NOT NULL,
    golden_bytes BLOB NOT NULL,
    last_seen REAL NOT NULL,
    PRIMARY KEY (provider, session_id)
);
CREATE INDEX IF NOT EXISTS idx_ccr_sessions_last_seen ON ccr_sessions (last_seen);
"""

# The only two tables `_prune` ever runs against — never interpolated from
# anything outside this module, so building the DELETE/SELECT statements
# with an f-string below is safe.
_MEMORY_TABLE: Literal["memory_tool_defs"] = "memory_tool_defs"
_CCR_TABLE: Literal["ccr_sessions"] = "ccr_sessions"


def default_db_path() -> Path:
    """Resolve the default database path: `workspace_dir()/session_tools.db`."""
    from ..paths import workspace_dir

    return workspace_dir() / "session_tools.db"


class SessionToolStore:
    """Write-through SQLite store backing both session tool trackers.

    A single instance holds both the memory-tool rows and the CCR rows
    (one db file, two tables) so `SessionToolTracker` and
    `SessionCcrTracker` can each be handed their own store pointed at
    the same path. Every public method catches its own errors: a
    failure to open or write never raises, it just logs one warning
    and leaves the caller's in-memory state as the source of truth.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._path = Path(db_path).expanduser() if db_path else default_db_path()
        self._conn: sqlite3.Connection | None = None
        self._warned = False
        self._open()

    def _open(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            conn.commit()
            self._conn = conn
        except Exception as exc:  # noqa: BLE001 - any sqlite/OS failure is non-fatal
            self._warn(exc)
            self._conn = None

    def _warn(self, exc: Exception) -> None:
        if self._warned:
            return
        self._warned = True
        logger.warning(
            "session tool store at %s unavailable (%s); continuing in memory only "
            "— sticky tool-injection state will not survive a restart",
            self._path,
            exc,
        )

    @property
    def available(self) -> bool:
        """True when the backing sqlite connection is open and usable."""
        return self._conn is not None

    @contextmanager
    def _guard(self) -> Iterator[None]:
        """Run a block of sqlite calls, turning any failure into the
        store's standard log-once-and-continue-in-memory-only behavior.

        Shared by every public method below instead of each repeating its
        own ``try: ... except Exception: self._warn(exc)``. A caller whose
        method returns a value initializes it before the ``with`` block;
        on failure the block is abandoned partway through and that
        already-initialized value is what gets returned.
        """
        try:
            yield
        except Exception as exc:  # noqa: BLE001 - any sqlite/OS failure is non-fatal
            self._warn(exc)

    # ------------------------------------------------------------------
    # Memory tool rows (`SessionToolTracker`)
    # ------------------------------------------------------------------

    def record_memory_tool(
        self,
        *,
        provider: str,
        session_id: str,
        tool_name: str,
        golden_bytes: bytes,
        position: int,
        max_sessions: int,
    ) -> None:
        """Write-through a single golden (tool_name, bytes) pair for a session.

        A no-op when the tool name already has a row (first-write-wins,
        matching the in-memory tracker's semantics) beyond bumping the
        session's `last_seen`.
        """
        if self._conn is None:
            return
        now = time.time()
        with self._guard():
            self._conn.execute(
                "INSERT OR IGNORE INTO memory_tool_defs "
                "(provider, session_id, tool_name, golden_bytes, position, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (provider, session_id, tool_name, golden_bytes, position, now),
            )
            self._conn.execute(
                "UPDATE memory_tool_defs SET last_seen = ? WHERE provider = ? AND session_id = ?",
                (now, provider, session_id),
            )
            self._conn.commit()
            self._prune(_MEMORY_TABLE, max_sessions)

    def load_all_memory_tools(
        self, max_sessions: int
    ) -> list[tuple[str, str, list[tuple[str, bytes]]]]:
        """Return the `max_sessions` most-recently-seen sessions, oldest first.

        Each entry is ``(provider, session_id, [(tool_name, golden_bytes), ...])``
        with tools in original insertion order. Oldest-first ordering lets a
        caller replay these into an `OrderedDict` and get the same LRU
        ordering a live tracker would have built up.
        """
        if self._conn is None:
            return []
        result: list[tuple[str, str, list[tuple[str, bytes]]]] = []
        with self._guard():
            session_rows = self._conn.execute(
                "SELECT provider, session_id FROM ("
                "  SELECT provider, session_id, MAX(last_seen) AS last_seen "
                "  FROM memory_tool_defs GROUP BY provider, session_id"
                ") ORDER BY last_seen DESC LIMIT ?",
                (max_sessions,),
            ).fetchall()
            for provider, session_id in reversed(session_rows):
                tool_rows = self._conn.execute(
                    "SELECT tool_name, golden_bytes FROM memory_tool_defs "
                    "WHERE provider = ? AND session_id = ? ORDER BY position ASC",
                    (provider, session_id),
                ).fetchall()
                result.append((provider, session_id, [(n, bytes(b)) for n, b in tool_rows]))
        return result

    def _prune(self, table: Literal["memory_tool_defs", "ccr_sessions"], max_sessions: int) -> None:
        """Delete every session in ``table`` past the ``max_sessions``
        most-recently-seen, shared by both the memory-tool and CCR rows."""

        if self._conn is None:
            return
        with self._guard():
            rows = self._conn.execute(
                "SELECT provider, session_id FROM ("
                "  SELECT provider, session_id, MAX(last_seen) AS last_seen "
                f"  FROM {table} GROUP BY provider, session_id"
                ") ORDER BY last_seen DESC"
            ).fetchall()
            if len(rows) <= max_sessions:
                return
            for provider, session_id in rows[max_sessions:]:
                self._conn.execute(
                    f"DELETE FROM {table} WHERE provider = ? AND session_id = ?",
                    (provider, session_id),
                )
            self._conn.commit()

    # ------------------------------------------------------------------
    # CCR session rows (`SessionCcrTracker`)
    # ------------------------------------------------------------------

    def record_ccr_done(
        self,
        *,
        provider: str,
        session_id: str,
        golden_bytes: bytes,
        max_sessions: int,
    ) -> None:
        """Write-through the (already-resolved) golden CCR tool bytes for a session.

        Callers pass the tracker's already-pinned bytes (first-write-wins is
        resolved in-memory before this is called), so this always overwrites
        the stored row — never a partial update.
        """
        if self._conn is None:
            return
        now = time.time()
        with self._guard():
            self._conn.execute(
                "INSERT OR REPLACE INTO ccr_sessions "
                "(provider, session_id, golden_bytes, last_seen) VALUES (?, ?, ?, ?)",
                (provider, session_id, golden_bytes, now),
            )
            self._conn.commit()
            self._prune(_CCR_TABLE, max_sessions)

    def load_all_ccr_sessions(self, max_sessions: int) -> list[tuple[str, str, bytes]]:
        """Return the `max_sessions` most-recently-seen CCR sessions, oldest first."""
        if self._conn is None:
            return []
        result: list[tuple[str, str, bytes]] = []
        with self._guard():
            rows = self._conn.execute(
                "SELECT provider, session_id, golden_bytes FROM ccr_sessions "
                "ORDER BY last_seen DESC LIMIT ?",
                (max_sessions,),
            ).fetchall()
            result = [(p, s, bytes(b)) for p, s, b in reversed(rows)]
        return result

    def close(self) -> None:
        """Close the backing connection, if open. Mainly for tests."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 - best-effort close
                pass
            self._conn = None
