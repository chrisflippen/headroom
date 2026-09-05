"""Restart-survival tests for the SQLite-backed session tool store.

`SessionToolTracker` and `SessionCcrTracker` are otherwise pure
in-memory bounded LRUs — every proxy restart used to forget which
sessions had already received sticky tool injections, flipping the
tool list on the next turn and busting Anthropic's prompt cache. These
tests pin the fix: a `SessionToolStore` backing both trackers, and a
freshly constructed tracker (simulating a restart) that hydrates back
to the state a previous tracker instance wrote.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest

from headroom import paths
from headroom.proxy.ccr_session_tracker import SessionCcrTracker
from headroom.proxy.helpers import (
    _reset_session_ccr_tracker_for_test,
    _reset_session_tool_tracker_for_test,
    apply_session_sticky_ccr_tool,
    apply_session_sticky_memory_tools,
)
from headroom.proxy.session_tool_store import SessionToolStore
from headroom.proxy.tool_injection_tracker import SessionToolTracker


@pytest.fixture(autouse=True)
def _reset_singletons_and_stateless_flag() -> Generator[None, None, None]:
    _reset_session_tool_tracker_for_test()
    _reset_session_ccr_tracker_for_test()
    yield
    _reset_session_tool_tracker_for_test()
    _reset_session_ccr_tracker_for_test()
    paths.set_process_stateless(False)


def _memory_tools_payload() -> list[dict[str, object]]:
    return [
        {"name": "memory_save", "description": "save", "input_schema": {"type": "object"}},
        {"name": "memory_search", "description": "search", "input_schema": {"type": "object"}},
    ]


# ---------------------------------------------------------------------------
# Pure tracker + store: SessionToolTracker
# ---------------------------------------------------------------------------


def test_memory_tool_tracker_survives_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "session_tools.db"
    store_a = SessionToolStore(db_path)
    tracker_a = SessionToolTracker(max_sessions=10, store=store_a)

    tracker_a.record_injection("anthropic", "s-1", "memory_save", b'{"name":"memory_save"}')
    tracker_a.record_injection("anthropic", "s-1", "memory_search", b'{"name":"memory_search"}')

    # Simulate a restart: brand new tracker, new store handle, same path.
    store_b = SessionToolStore(db_path)
    tracker_b = SessionToolTracker(max_sessions=10, store=store_b)

    assert tracker_b.should_inject("anthropic", "s-1") is True
    assert tracker_b.get_golden_definitions("anthropic", "s-1") == [
        ("memory_save", b'{"name":"memory_save"}'),
        ("memory_search", b'{"name":"memory_search"}'),
    ]


# ---------------------------------------------------------------------------
# Pure tracker + store: SessionCcrTracker
# ---------------------------------------------------------------------------


def test_ccr_tracker_survives_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "session_tools.db"
    store_a = SessionToolStore(db_path)
    tracker_a = SessionCcrTracker(max_sessions=10, store=store_a)

    tracker_a.record_ccr_done("anthropic", "s-1", b'{"name":"headroom_retrieve"}')

    store_b = SessionToolStore(db_path)
    tracker_b = SessionCcrTracker(max_sessions=10, store=store_b)

    assert tracker_b.has_done_ccr("anthropic", "s-1") is True
    assert tracker_b.get_golden_tool_bytes("anthropic", "s-1") == b'{"name":"headroom_retrieve"}'


# ---------------------------------------------------------------------------
# Public helper: apply_session_sticky_memory_tools survives a "restart"
# ---------------------------------------------------------------------------


def test_apply_session_sticky_memory_tools_survives_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEADROOM_WORKSPACE_DIR", str(tmp_path))

    tools1, was1 = apply_session_sticky_memory_tools(
        provider="anthropic",
        session_id="s-1",
        request_id="r-1",
        existing_tools=[],
        memory_tools_to_inject=_memory_tools_payload(),
        inject_this_turn=True,
    )
    assert was1 is True
    assert {"memory_save", "memory_search"}.issubset({t["name"] for t in tools1})

    # "Restart": drop the process-wide singleton so the next call rebuilds
    # a fresh tracker from disk, same workspace dir.
    _reset_session_tool_tracker_for_test()

    tools2, was2 = apply_session_sticky_memory_tools(
        provider="anthropic",
        session_id="s-1",
        request_id="r-2",
        existing_tools=[],
        memory_tools_to_inject=[],
        inject_this_turn=False,
    )
    assert was2 is True

    bytes1 = json.dumps(tools1, sort_keys=True).encode()
    bytes2 = json.dumps(tools2, sort_keys=True).encode()
    assert bytes1 == bytes2


# ---------------------------------------------------------------------------
# Public helper: apply_session_sticky_ccr_tool survives a "restart"
# ---------------------------------------------------------------------------


def test_apply_session_sticky_ccr_tool_survives_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEADROOM_WORKSPACE_DIR", str(tmp_path))

    tools1, was1 = apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id="s-1",
        request_id="r-1",
        existing_tools=[],
        has_compressed_content_this_turn=True,
    )
    assert was1 is True

    _reset_session_ccr_tracker_for_test()

    tools2, was2 = apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id="s-1",
        request_id="r-2",
        existing_tools=[],
        has_compressed_content_this_turn=False,
    )
    assert was2 is True

    bytes1 = json.dumps(tools1, sort_keys=True).encode()
    bytes2 = json.dumps(tools2, sort_keys=True).encode()
    assert bytes1 == bytes2


# ---------------------------------------------------------------------------
# Stateless mode: no db file created
# ---------------------------------------------------------------------------


def test_stateless_mode_creates_no_db_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADROOM_WORKSPACE_DIR", str(tmp_path))
    paths.set_process_stateless(True)

    tools1, was1 = apply_session_sticky_memory_tools(
        provider="anthropic",
        session_id="s-1",
        request_id="r-1",
        existing_tools=[],
        memory_tools_to_inject=_memory_tools_payload(),
        inject_this_turn=True,
    )
    assert was1 is True

    tools2, was2 = apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id="s-1",
        request_id="r-2",
        existing_tools=[],
        has_compressed_content_this_turn=True,
    )
    assert was2 is True

    assert not (tmp_path / "session_tools.db").exists()


def test_stateless_env_var_creates_no_db_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEADROOM_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv("HEADROOM_STATELESS", "1")

    apply_session_sticky_memory_tools(
        provider="anthropic",
        session_id="s-1",
        request_id="r-1",
        existing_tools=[],
        memory_tools_to_inject=_memory_tools_payload(),
        inject_this_turn=True,
    )

    assert not (tmp_path / "session_tools.db").exists()


# ---------------------------------------------------------------------------
# Unwritable store path: no exception, one warning, memory still works
# ---------------------------------------------------------------------------


def test_unwritable_store_path_falls_back_to_memory_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A plain file sitting where the store's parent directory must be.
    blocked_dir = tmp_path / "blocked"
    blocked_dir.write_text("not a directory")
    db_path = blocked_dir / "session_tools.db"

    with caplog.at_level("WARNING", logger="headroom.proxy.session_tool_store"):
        store = SessionToolStore(db_path)
        tracker = SessionToolTracker(max_sessions=10, store=store)

        tracker.record_injection("anthropic", "s-1", "memory_save", b'{"name":"memory_save"}')

        assert tracker.should_inject("anthropic", "s-1") is True
        assert tracker.get_golden_definitions("anthropic", "s-1") == [
            ("memory_save", b'{"name":"memory_save"}')
        ]

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert not store.available


# ---------------------------------------------------------------------------
# LRU bound survives hydration
# ---------------------------------------------------------------------------


def test_lru_bound_survives_hydration(tmp_path: Path) -> None:
    db_path = tmp_path / "session_tools.db"
    store_a = SessionToolStore(db_path)
    tracker_a = SessionToolTracker(max_sessions=2, store=store_a)

    tracker_a.record_injection("anthropic", "s-1", "memory_save", b"a")
    tracker_a.record_injection("anthropic", "s-2", "memory_save", b"b")
    tracker_a.record_injection("anthropic", "s-3", "memory_save", b"c")

    store_b = SessionToolStore(db_path)
    tracker_b = SessionToolTracker(max_sessions=2, store=store_b)

    assert tracker_b.active_sessions == 2
    assert tracker_b.should_inject("anthropic", "s-1") is False
    assert tracker_b.should_inject("anthropic", "s-2") is True
    assert tracker_b.should_inject("anthropic", "s-3") is True


def test_lru_bound_survives_hydration_ccr(tmp_path: Path) -> None:
    db_path = tmp_path / "session_tools.db"
    store_a = SessionToolStore(db_path)
    tracker_a = SessionCcrTracker(max_sessions=2, store=store_a)

    tracker_a.record_ccr_done("anthropic", "s-1", b"a")
    tracker_a.record_ccr_done("anthropic", "s-2", b"b")
    tracker_a.record_ccr_done("anthropic", "s-3", b"c")

    store_b = SessionToolStore(db_path)
    tracker_b = SessionCcrTracker(max_sessions=2, store=store_b)

    assert tracker_b.active_sessions == 2
    assert tracker_b.has_done_ccr("anthropic", "s-1") is False
    assert tracker_b.has_done_ccr("anthropic", "s-2") is True
    assert tracker_b.has_done_ccr("anthropic", "s-3") is True


# ---------------------------------------------------------------------------
# Sanity: the store itself is a real sqlite file with the expected shape
# ---------------------------------------------------------------------------


def test_store_writes_a_real_sqlite_wal_database(tmp_path: Path) -> None:
    db_path = tmp_path / "session_tools.db"
    store = SessionToolStore(db_path)
    tracker = SessionToolTracker(max_sessions=10, store=store)
    tracker.record_injection("anthropic", "s-1", "memory_save", b"a")

    assert db_path.exists()
    conn = sqlite3.connect(db_path)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        rows = conn.execute(
            "SELECT provider, session_id, tool_name FROM memory_tool_defs"
        ).fetchall()
        assert rows == [("anthropic", "s-1", "memory_save")]
    finally:
        conn.close()
