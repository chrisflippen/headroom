from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

from headroom import paths
from headroom.cache import compression_store as compression_store_module
from headroom.cache.compression_store import (
    get_compression_store,
    reset_compression_store,
)
from headroom.code_tools import connections
from tests._mcp_stub import import_module_with_mcp_stub

mcp_server = import_module_with_mcp_stub("headroom.ccr.mcp_server")


def test_shared_stats_work_without_fcntl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(mcp_server, "_HAS_FCNTL", False)
    monkeypatch.setattr(mcp_server, "fcntl", None)
    monkeypatch.setattr(mcp_server, "SHARED_STATS_DIR", tmp_path)
    monkeypatch.setattr(mcp_server, "SHARED_STATS_FILE", tmp_path / "session_stats.jsonl")
    monkeypatch.setattr(mcp_server.os, "getpid", lambda: 4242)
    monkeypatch.setattr(mcp_server.time, "time", lambda: 1001.0)

    event = {"type": "compress", "timestamp": 1000.0}
    mcp_server._append_shared_event(event)

    raw_lines = mcp_server.SHARED_STATS_FILE.read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 1
    assert json.loads(raw_lines[0]) == {"type": "compress", "timestamp": 1000.0, "pid": 4242}

    events = mcp_server._read_shared_events(window_seconds=60)
    assert events == [{"type": "compress", "timestamp": 1000.0, "pid": 4242}]


# --- Shared compression store wiring ---------------------------------------
# MCP's _get_local_store() must return the get_compression_store() singleton —
# the same instance the proxy and response_handler use — so content compressed
# on either side is retrievable in-process. These pin that wiring so a private
# store can't creep back.


@pytest.fixture
def fresh_store() -> Iterator[None]:
    reset_compression_store()
    yield
    reset_compression_store()


def test_mcp_uses_shared_singleton_store(fresh_store: None) -> None:
    """MCP's store is the global singleton, not a private instance."""
    server = mcp_server.HeadroomMCPServer(check_proxy=False)
    assert server._get_local_store() is get_compression_store()


def test_mcp_retrieves_proxy_stored_content(fresh_store: None) -> None:
    """Content stored via the singleton (as the proxy does) is retrievable
    through MCP's local-store path. The HTTP fallback is disabled so this
    passes only via the shared store."""
    original = '{"some": "original proxy-compressed content"}'
    hash_key = get_compression_store().store(original, '{"compressed": true}')

    server = mcp_server.HeadroomMCPServer(check_proxy=False)
    result = asyncio.run(server._retrieve_content(hash_key))

    assert result.get("source") == "local"
    assert result["original_content"] == original


def test_compress_savings_percent_tracks_token_counts(fresh_store: None) -> None:
    """``savings_percent`` must be the *removed* percentage derived from the
    token counts — never the retained percentage. Regression for the inversion
    where ``(1 - compression_ratio)`` reported a no-op (0% saved) as 100%."""
    pytest.importorskip("mcp", reason="MCP SDK required")
    server = mcp_server.HeadroomMCPServer(check_proxy=False)

    # Repetitive JSON array — the shape the engine actually compresses.
    content = json.dumps([{"id": i, "status": "ok", "kind": "run"} for i in range(40)])
    result = server._compress_content(content)

    orig = result["original_tokens"]
    comp = result["compressed_tokens"]
    expected = round((1 - comp / orig) * 100, 1) if orig > 0 else 0

    # Reported savings agrees with the token fields (and with tokens_saved).
    assert result["savings_percent"] == expected
    assert 0.0 <= result["savings_percent"] <= 100.0
    if result["tokens_saved"] == 0:
        assert result["savings_percent"] == 0.0  # not inverted to 100
    else:
        assert result["savings_percent"] > 0.0


def test_mcp_compress_surfaces_unreachable_proxy(fresh_store: None) -> None:
    server = mcp_server.HeadroomMCPServer(
        proxy_url="http://127.0.0.1:9",
        check_proxy=True,
    )

    response = asyncio.run(server._handle_compress({"content": "dead proxy check"}))
    payload = json.loads(response[0].kwargs["text"])

    assert payload["proxy"]["status"] == "unreachable"
    assert payload["proxy"]["url"] == "http://127.0.0.1:9"
    assert "unreachable" in payload["warning"].lower()


def test_mcp_stats_surfaces_unreachable_proxy() -> None:
    server = mcp_server.HeadroomMCPServer(
        proxy_url="http://127.0.0.1:9",
        check_proxy=True,
    )

    response = asyncio.run(server._handle_stats())
    payload = json.loads(response[0].kwargs["text"])

    assert payload["proxy"]["status"] == "unreachable"
    assert payload["proxy"]["url"] == "http://127.0.0.1:9"
    assert "unreachable" in payload["warning"].lower()


def test_mcp_proxy_probe_preserves_shared_proxy_client(monkeypatch: pytest.MonkeyPatch) -> None:
    class ProbeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json() -> dict[str, object]:
            return {"status": "healthy", "alive": True}

    class ProbeClient:
        def __init__(self, *, timeout: float) -> None:
            seen["timeout"] = timeout

        async def __aenter__(self) -> ProbeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            seen["closed"] = True

        async def get(self, url: str) -> ProbeResponse:
            seen["url"] = url
            return ProbeResponse()

    seen: dict[str, object] = {}
    shared_client = object()
    monkeypatch.setattr(mcp_server.httpx, "AsyncClient", ProbeClient)

    server = mcp_server.HeadroomMCPServer(
        proxy_url="http://127.0.0.1:8765",
        check_proxy=True,
    )
    server._http_client = shared_client  # type: ignore[assignment]

    result = asyncio.run(server._probe_proxy_unreachable())

    assert result is None
    assert seen == {
        "timeout": 5.0,
        "url": "http://127.0.0.1:8765/livez",
        "closed": True,
    }
    assert server._http_client is shared_client


def test_mcp_local_mode_still_works_without_proxy_checking(fresh_store: None) -> None:
    server = mcp_server.HeadroomMCPServer(
        proxy_url="http://127.0.0.1:9",
        check_proxy=False,
    )

    response = asyncio.run(server._handle_compress({"content": "local mode stays available"}))
    payload = json.loads(response[0].kwargs["text"])

    assert "proxy" not in payload
    assert "warning" not in payload or "unreachable" not in payload["warning"].lower()


def test_mcp_retrieve_returns_full_content(fresh_store: None) -> None:
    """Retrieval is by hash: a stored, unexpired entry always returns its full
    original content (never empty, never a spurious "not found")."""
    original = "the the the the the the the the the the\n" * 5
    hash_key = get_compression_store().store(original, "<<small>>")

    server = mcp_server.HeadroomMCPServer(check_proxy=False)
    result = asyncio.run(server._retrieve_content(hash_key))

    assert "error" not in result
    assert result.get("source") == "local"
    assert result["original_content"] == original


def test_mcp_retrieve_expired_hash_returns_terminal_guidance(
    monkeypatch: pytest.MonkeyPatch,
    fresh_store: None,
) -> None:
    """An expired local hash should say it expired and tell the agent to stop retrying."""
    current_time = [1000.0]

    def fake_time() -> float:
        return current_time[0]

    monkeypatch.setattr(mcp_server.time, "time", fake_time)
    monkeypatch.setattr(compression_store_module.time, "time", fake_time)

    store = get_compression_store()
    hash_key = store.store("expired content", "<<small>>", ttl=1)
    current_time[0] = 1002.0

    server = mcp_server.HeadroomMCPServer(check_proxy=False)
    result = asyncio.run(server._retrieve_content(hash_key))

    assert result["status"] == "expired"
    assert result["ttl_seconds"] == 1
    assert result["age_seconds"] == pytest.approx(2.0)
    assert "Entry expired" in result["error"]
    assert "do not retry the same hash" in result["error"].lower()
    assert "re-run the command" in result["hint"].lower()


def test_mcp_retrieve_hash_expiring_during_lookup_returns_terminal_guidance(
    monkeypatch: pytest.MonkeyPatch,
    fresh_store: None,
) -> None:
    phase = "store"
    status_seen = False

    def fake_time() -> float:
        if phase == "store":
            return 1000.0
        return 1001.1 if status_seen else 1000.5

    monkeypatch.setattr(mcp_server.time, "time", fake_time)
    monkeypatch.setattr(compression_store_module.time, "time", fake_time)

    store = get_compression_store()
    hash_key = store.store("expired during retrieve", "<<small>>", ttl=1)
    phase = "retrieve"

    original_get_entry_status = store.get_entry_status
    original_retrieve = store.retrieve

    def get_entry_status_then_expire(*args: Any, **kwargs: Any) -> Any:
        nonlocal status_seen
        result = original_get_entry_status(*args, **kwargs)
        status_seen = True
        return result

    monkeypatch.setattr(store, "get_entry_status", get_entry_status_then_expire)
    monkeypatch.setattr(store, "retrieve", original_retrieve)

    server = mcp_server.HeadroomMCPServer(check_proxy=False)
    result = asyncio.run(server._retrieve_content(hash_key))

    assert result["status"] == "expired"
    assert result["ttl_seconds"] == 1
    assert result["age_seconds"] == pytest.approx(1.1)
    assert "Entry expired" in result["error"]
    assert "do not retry the same hash" in result["error"].lower()


def test_mcp_retrieve_missing_local_hash_can_still_hit_proxy(
    monkeypatch: pytest.MonkeyPatch,
    fresh_store: None,
) -> None:
    monkeypatch.setattr(mcp_server, "HTTPX_AVAILABLE", True)
    server = mcp_server.HeadroomMCPServer(check_proxy=True)

    async def retrieve_via_proxy(hash_key: str) -> dict[str, object]:
        return {"hash": hash_key, "original_content": "from proxy"}

    server._retrieve_via_proxy = retrieve_via_proxy

    result = asyncio.run(server._retrieve_content("proxy_hash"))

    assert result["source"] == "proxy"
    assert result["hash"] == "proxy_hash"
    assert result["original_content"] == "from proxy"


def test_mcp_retrieve_expired_local_hash_can_still_hit_proxy(
    monkeypatch: pytest.MonkeyPatch,
    fresh_store: None,
) -> None:
    current_time = [1000.0]

    def fake_time() -> float:
        return current_time[0]

    monkeypatch.setattr(mcp_server, "HTTPX_AVAILABLE", True)
    monkeypatch.setattr(mcp_server.time, "time", fake_time)
    monkeypatch.setattr(compression_store_module.time, "time", fake_time)

    store = get_compression_store()
    hash_key = store.store("expired local content", "<<small>>", ttl=1)
    current_time[0] = 1002.0

    server = mcp_server.HeadroomMCPServer(check_proxy=True)

    async def retrieve_via_proxy(proxy_hash_key: str) -> dict[str, object]:
        return {"hash": proxy_hash_key, "original_content": "from proxy"}

    server._retrieve_via_proxy = retrieve_via_proxy

    result = asyncio.run(server._retrieve_content(hash_key))

    assert result["source"] == "proxy"
    assert result["hash"] == hash_key
    assert result["original_content"] == "from proxy"


def test_mcp_retrieve_missing_hash_still_errors(fresh_store: None) -> None:
    """A never-stored hash must stay on the generic missing path, not expired guidance."""
    server = mcp_server.HeadroomMCPServer(check_proxy=False)
    result = asyncio.run(server._retrieve_content("nonexistent_hash"))
    assert result.get("status") is None
    assert result["error"] == "Content not found. It may have expired or the hash may be incorrect."
    assert "do not retry the same hash" not in result.get("hint", "").lower()


def test_handle_stats_session_output_is_window_scoped() -> None:
    """window-scoped stats output should be explicitly labeled after this change."""

    async def fetch_stats() -> dict[str, object]:
        return {
            "summary": {
                "mode": "token",
                "api_requests": 3,
                "compression": {},
            }
        }

    server = mcp_server.HeadroomMCPServer(check_proxy=True)
    server._fetch_full_proxy_stats = fetch_stats
    response = asyncio.run(server._handle_stats())
    text = response[0].kwargs["text"]

    assert "Headroom Window-Scoped Session Summary" in text
    assert "Headroom Session Summary" not in text


def test_handle_stats_includes_lifetime_totals_from_persistent_savings() -> None:
    """Lifetime savings are appended from /stats persistent_savings.lifetime."""

    async def fetch_stats() -> dict[str, object]:
        return {
            "summary": {
                "mode": "token",
                "api_requests": 3,
                "compression": {},
            },
            "persistent_savings": {
                "lifetime": {"tokens_saved": 12345, "compression_savings_usd": 7.25}
            },
        }

    server = mcp_server.HeadroomMCPServer(check_proxy=True)
    server._fetch_full_proxy_stats = fetch_stats
    response = asyncio.run(server._handle_stats())
    text = response[0].kwargs["text"]

    assert "Lifetime Savings:" in text
    assert "Tokens saved: 12,345" in text
    assert "Compression savings: $7.25" in text


def test_handle_stats_falls_back_gracefully_without_persistent_lifetime() -> None:
    """Missing lifetime data should still return a valid session summary."""

    async def fetch_stats() -> dict[str, object]:
        return {
            "summary": {
                "mode": "token",
                "api_requests": 3,
                "compression": {},
            },
            "persistent_savings": {"lifetime": None},
        }

    server = mcp_server.HeadroomMCPServer(check_proxy=True)
    server._fetch_full_proxy_stats = fetch_stats
    response = asyncio.run(server._handle_stats())
    text = response[0].kwargs["text"]

    assert "Headroom Window-Scoped Session Summary" in text
    assert "Lifetime Savings:" not in text


def test_handle_stats_shows_zero_lifetime_totals_when_present() -> None:
    """A present lifetime payload should still render explicit zero totals."""

    async def fetch_stats() -> dict[str, object]:
        return {
            "summary": {
                "mode": "token",
                "api_requests": 3,
                "compression": {},
            },
            "persistent_savings": {"lifetime": {"tokens_saved": 0, "compression_savings_usd": 0.0}},
        }

    server = mcp_server.HeadroomMCPServer(check_proxy=True)
    server._fetch_full_proxy_stats = fetch_stats
    response = asyncio.run(server._handle_stats())
    text = response[0].kwargs["text"]

    assert "Lifetime Savings:" in text
    assert "Tokens saved: 0" in text
    assert "Compression savings: $0.00" in text


# --- Parent-death watchdog: reap orphaned `mcp serve` on client death --------
# When the launching MCP client is SIGKILLed, stdin EOF may never arrive and the
# SDK's blocking stdin reader wedges server.run() forever, orphaning this process
# under init/launchd. run_stdio() runs a watchdog that detects the reparent and
# forces shutdown. Refs headroomlabs-ai/headroom#2185 (secondary), #1761.


def test_parent_death_watchdog_fires_when_reparented(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ppid changes (client died), the watchdog resolves promptly."""
    server = mcp_server.HeadroomMCPServer(check_proxy=False)
    calls = {"n": 0}

    def fake_getppid() -> int:
        calls["n"] += 1
        return 500 if calls["n"] == 1 else 1  # captured live, then reparented

    monkeypatch.setattr(mcp_server.os, "getppid", fake_getppid)

    async def run() -> None:
        await asyncio.wait_for(server._await_parent_death(0.001), timeout=1.0)

    asyncio.run(run())  # returns => detected reparent; TimeoutError would fail


def test_parent_death_watchdog_stays_quiet_with_live_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stable ppid must never trip the watchdog."""
    server = mcp_server.HeadroomMCPServer(check_proxy=False)
    monkeypatch.setattr(mcp_server.os, "getppid", lambda: 500)

    async def run() -> None:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(server._await_parent_death(0.001), timeout=0.05)

    asyncio.run(run())


def test_run_stdio_reaps_process_on_parent_death(monkeypatch: pytest.MonkeyPatch) -> None:
    """On reparent, run_stdio cleans up and calls os._exit(0) even though the
    (stubbed) server.run never returns — the orphan-reaper path."""
    server = mcp_server.HeadroomMCPServer(check_proxy=False)

    @contextlib.asynccontextmanager
    async def fake_stdio_server() -> AsyncIterator[tuple[object, object]]:
        yield (object(), object())

    monkeypatch.setattr(mcp_server, "stdio_server", fake_stdio_server)

    async def never_returns(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.sleep(3600)  # emulate the wedged SDK reader

    # DummyServer (MCP SDK stub) has no `.run`; raising=False lets us add it.
    monkeypatch.setattr(server.server, "run", never_returns, raising=False)

    calls = {"n": 0}

    def fake_getppid() -> int:
        calls["n"] += 1
        return 500 if calls["n"] == 1 else 1

    monkeypatch.setattr(mcp_server.os, "getppid", fake_getppid)

    cleaned = {"done": False}

    async def fake_cleanup() -> None:
        cleaned["done"] = True

    monkeypatch.setattr(server, "cleanup", fake_cleanup)

    class _Exited(Exception):
        pass

    def fake_exit(code: int) -> None:
        raise _Exited(code)  # intercept so pytest survives

    monkeypatch.setattr(mcp_server.os, "_exit", fake_exit)

    with pytest.raises(_Exited) as excinfo:
        asyncio.run(server.run_stdio(parent_death_poll_interval=0.001))

    assert excinfo.value.args[0] == 0
    assert cleaned["done"] is True


# --- Edit and Sql MCP wiring -------------------------------------------------


def test_edit_and_sql_tools_are_registered() -> None:
    server = mcp_server.HeadroomMCPServer(check_proxy=False)
    tools = server._tool_definitions()
    names = [t.kwargs["name"] for t in tools]

    assert "Edit" in names
    assert "Sql" in names

    sql_tool = next(t for t in tools if t.kwargs["name"] == "Sql")
    schema = sql_tool.kwargs["inputSchema"]
    assert schema["properties"]["action"]["enum"] == ["query", "schema"]
    for field in ["connection", "sql", "limit"]:
        assert field in schema["properties"]
    assert len(sql_tool.kwargs["description"].split()) < 120


@pytest.fixture
def _sql_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Isolate the connections config file, so a Sql wiring test never
    touches the filesystem's real config dir."""

    monkeypatch.setenv(paths.HEADROOM_CONFIG_DIR_ENV, str(tmp_path))
    return tmp_path


@pytest.fixture
def sql_keychain() -> connections.MemoryKeychain:
    """A MemoryKeychain injected into HeadroomMCPServer in place of the real
    macOS keychain, so a Sql wiring test never touches it."""

    return connections.MemoryKeychain()


def test_handle_sql_runs_a_query_against_a_known_connection(
    _sql_config_dir: Path, sql_keychain: connections.MemoryKeychain, tmp_path: Path
) -> None:
    db_path = tmp_path / "app.sqlite"
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE users (id INTEGER, name TEXT)")
    conn.execute("INSERT INTO users VALUES (1, 'Alice')")
    conn.commit()
    conn.close()

    connections.add_connection("mydb", f"sqlite://{db_path}", sql_keychain)

    server = mcp_server.HeadroomMCPServer(check_proxy=False, keychain=sql_keychain)
    response = asyncio.run(server._handle_sql({"connection": "mydb", "sql": "SELECT * FROM users"}))

    assert "Alice" in response[0].kwargs["text"]


def test_handle_sql_unknown_connection_lists_known_names(
    _sql_config_dir: Path, sql_keychain: connections.MemoryKeychain
) -> None:
    connections.add_connection("mydb", "sqlite:///anything.sqlite", sql_keychain)

    server = mcp_server.HeadroomMCPServer(check_proxy=False, keychain=sql_keychain)
    response = asyncio.run(server._handle_sql({"connection": "nope", "sql": "SELECT 1"}))

    text = response[0].kwargs["text"]
    assert "nope" in text
    assert "mydb" in text


def test_call_tool_sql_query_reaches_the_resolver_and_reports_unknown_names(
    _sql_config_dir: Path, sql_keychain: connections.MemoryKeychain, tmp_path: Path
) -> None:
    """``call_tool`` (the MCP dispatch entry point, not just the private
    ``_handle_sql`` helper) reaches ``sql.query`` for a known connection and
    shares the same known-connections message for an unknown one."""

    db_path = tmp_path / "app.sqlite"
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE users (id INTEGER, name TEXT)")
    conn.execute("INSERT INTO users VALUES (1, 'Alice')")
    conn.commit()
    conn.close()

    connections.add_connection("mydb", f"sqlite://{db_path}", sql_keychain)

    server = mcp_server.HeadroomMCPServer(check_proxy=False, keychain=sql_keychain)

    known_response = asyncio.run(
        server._call_tool_handler(
            "Sql", {"connection": "mydb", "sql": "SELECT * FROM users", "action": "query"}
        )
    )
    assert "Alice" in known_response[0].kwargs["text"]

    unknown_response = asyncio.run(
        server._call_tool_handler("Sql", {"connection": "nope", "sql": "SELECT 1"})
    )
    unknown_text = unknown_response[0].kwargs["text"]
    assert connections.describe_unknown("nope") in unknown_text


# --- Search reaches sibling git worktrees through the MCP handler path ------


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_call_tool_search_reads_a_file_in_a_sibling_worktree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``call_tool`` (the MCP dispatch entry point) resolves a Search read
    against a file in a sibling git worktree of the launch directory, not
    just the launch directory itself, and still returns a stamp."""

    launch_dir = tmp_path / "main"
    launch_dir.mkdir()
    _git("init", "-b", "main", cwd=launch_dir)
    _git("config", "user.email", "test@example.com", cwd=launch_dir)
    _git("config", "user.name", "Test", cwd=launch_dir)
    (launch_dir / "README.md").write_text("hello")
    _git("add", "README.md", cwd=launch_dir)
    _git("commit", "-m", "initial", cwd=launch_dir)

    worktree_dir = tmp_path / "feature-worktree"
    _git("worktree", "add", "-b", "feature", str(worktree_dir), cwd=launch_dir)
    (worktree_dir / "sibling.py").write_text("print('from sibling worktree')\n")

    monkeypatch.chdir(launch_dir)

    server = mcp_server.HeadroomMCPServer(check_proxy=False)
    response = asyncio.run(
        server._call_tool_handler(
            "Search", {"action": "read", "path": str(worktree_dir / "sibling.py")}
        )
    )

    text = response[0].kwargs["text"]
    assert "print('from sibling worktree')" in text
    assert "stamp=" in text


# ---------------------------------------------------------------------------
# Run: shaped command output, via the MCP dispatch entry point
# ---------------------------------------------------------------------------


def test_call_tool_run_returns_shaped_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``call_tool`` (the MCP dispatch entry point, not just the private
    ``_handle_run`` helper) runs the command in the server's launch
    directory and returns Run's shaped totals-line-plus-body text."""

    monkeypatch.chdir(tmp_path)

    server = mcp_server.HeadroomMCPServer(check_proxy=False)
    response = asyncio.run(server._call_tool_handler("Run", {"command": "printf 'hello\\n'"}))

    text = response[0].kwargs["text"]
    assert text.startswith("exit=0 lines=1 chars=6 time=")
    assert text.endswith("hello")


# ---------------------------------------------------------------------------
# SendMessage: messaging another Claude Code session on this machine
# ---------------------------------------------------------------------------


def _write_session_entry(sessions_dir: Path, pid: int, name: str) -> None:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / f"{pid}.json").write_text(
        json.dumps(
            {
                "pid": pid,
                "sessionId": f"session-{pid}",
                "cwd": f"/work/{name}",
                "name": name,
                "status": "idle",
                "messagingSocketPath": f"/tmp/cc-socks/{pid}.sock",
            }
        ),
        encoding="utf-8",
    )


def test_send_message_tool_is_registered() -> None:
    server = mcp_server.HeadroomMCPServer(check_proxy=False)
    tools = server._tool_definitions()
    tool = next(t for t in tools if t.kwargs["name"] == "SendMessage")

    schema = tool.kwargs["inputSchema"]
    assert schema["properties"]["action"]["enum"] == ["send", "list"]
    for field in ["to", "message"]:
        assert field in schema["properties"]
    assert len(tool.kwargs["description"].split()) < 120


def test_handle_send_message_lists_peers_from_the_session_registry(tmp_path: Path) -> None:
    _write_session_entry(tmp_path, 100, "alpha")
    _write_session_entry(tmp_path, 400, "me")

    server = mcp_server.HeadroomMCPServer(
        check_proxy=False, sessions_dir=tmp_path, session_pid=400, pid_alive=lambda pid: True
    )
    response = asyncio.run(server._handle_send_message({"action": "list"}))

    text = response[0].kwargs["text"]
    assert "alpha" in text
    assert "- me" not in text


def test_call_tool_send_message_reaches_the_messaging_module(tmp_path: Path) -> None:
    """``call_tool`` (the MCP dispatch entry point) routes SendMessage to
    code_tools.messaging, and an unknown recipient comes back as a plain
    refusal naming the sessions that are reachable."""
    _write_session_entry(tmp_path, 100, "alpha")

    server = mcp_server.HeadroomMCPServer(
        check_proxy=False, sessions_dir=tmp_path, session_pid=400, pid_alive=lambda pid: True
    )
    response = asyncio.run(
        server._call_tool_handler("SendMessage", {"to": "nope", "message": "hello"})
    )

    text = response[0].kwargs["text"]
    assert text.startswith("Refused:")
    assert "nope" in text and "alpha" in text


# --- Edit batch nudge -------------------------------------------------------
# The Edit tool nudges toward `multi` after three single `replace` calls in a
# row on the SAME file. Unit tests exercise the pure EditBatchNudge class
# directly; integration tests exercise the wiring through _handle_edit /
# _handle_search on a real HeadroomMCPServer instance.


def test_edit_batch_nudge_fires_on_third_same_file_replace() -> None:
    nudge = mcp_server.EditBatchNudge()
    assert nudge.record_edit("replace", "f.py") is None
    assert nudge.count == 1
    assert nudge.record_edit("replace", "f.py") is None
    assert nudge.count == 2
    third = nudge.record_edit("replace", "f.py")
    assert third == mcp_server.EDIT_BATCH_NUDGE_MESSAGE
    assert nudge.count == 0


def test_edit_batch_nudge_streak_restarts_after_firing() -> None:
    nudge = mcp_server.EditBatchNudge()
    for _ in range(3):
        nudge.record_edit("replace", "f.py")
    fourth = nudge.record_edit("replace", "f.py")
    assert fourth is None
    assert nudge.count == 1


def test_edit_batch_nudge_different_file_in_between_resets_streak() -> None:
    nudge = mcp_server.EditBatchNudge()
    nudge.record_edit("replace", "a.py")
    nudge.record_edit("replace", "b.py")
    assert nudge.count == 1
    third_overall = nudge.record_edit("replace", "b.py")
    assert third_overall is None
    assert nudge.count == 2


@pytest.mark.parametrize("action", ["create", "multi", "delete", "rename"])
def test_edit_batch_nudge_other_actions_reset_streak(action: str) -> None:
    nudge = mcp_server.EditBatchNudge()
    nudge.record_edit("replace", "f.py")
    nudge.record_edit("replace", "f.py")
    assert nudge.count == 2
    assert nudge.record_edit(action, "f.py") is None
    assert nudge.count == 0


def test_edit_batch_nudge_search_resets_streak() -> None:
    nudge = mcp_server.EditBatchNudge()
    nudge.record_edit("replace", "f.py")
    nudge.record_edit("replace", "f.py")
    assert nudge.count == 2
    nudge.record_search()
    assert nudge.count == 0


def test_edit_batch_nudge_replace_without_path_resets_streak() -> None:
    nudge = mcp_server.EditBatchNudge()
    nudge.record_edit("replace", "f.py")
    assert nudge.record_edit("replace", None) is None
    assert nudge.count == 0


@pytest.fixture
def fresh_edit_batch_nudge() -> Iterator[None]:
    """Isolate the module-level singleton the real Edit/Search handlers share."""
    original = mcp_server._edit_batch_nudge
    mcp_server._edit_batch_nudge = mcp_server.EditBatchNudge()
    yield
    mcp_server._edit_batch_nudge = original


@pytest.fixture
def edit_batch_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fresh_edit_batch_nudge: None
) -> mcp_server.HeadroomMCPServer:
    """A real HeadroomMCPServer rooted at an isolated tmp_path, with a fresh
    edit-batch-nudge streak, for the _handle_edit / _handle_search
    integration tests below."""
    monkeypatch.chdir(tmp_path)
    return mcp_server.HeadroomMCPServer(check_proxy=False)


def test_handle_edit_third_same_file_replace_ends_with_nudge_line(
    edit_batch_server: mcp_server.HeadroomMCPServer, tmp_path: Path
) -> None:
    (tmp_path / "f.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")

    first = asyncio.run(
        edit_batch_server._handle_edit(
            {"action": "replace", "path": "f.py", "old": "a = 1", "new": "a = 10"}
        )
    )
    second = asyncio.run(
        edit_batch_server._handle_edit(
            {"action": "replace", "path": "f.py", "old": "b = 2", "new": "b = 20"}
        )
    )
    third = asyncio.run(
        edit_batch_server._handle_edit(
            {"action": "replace", "path": "f.py", "old": "c = 3", "new": "c = 30"}
        )
    )

    assert not first[0].kwargs["text"].endswith(mcp_server.EDIT_BATCH_NUDGE_MESSAGE)
    assert not second[0].kwargs["text"].endswith(mcp_server.EDIT_BATCH_NUDGE_MESSAGE)
    assert third[0].kwargs["text"].endswith(mcp_server.EDIT_BATCH_NUDGE_MESSAGE)


def test_handle_edit_fourth_replace_after_nudge_does_not_nudge(
    edit_batch_server: mcp_server.HeadroomMCPServer, tmp_path: Path
) -> None:
    (tmp_path / "f.py").write_text("a = 1\nb = 2\nc = 3\nd = 4\n", encoding="utf-8")

    for old, new in (("a = 1", "a = 10"), ("b = 2", "b = 20"), ("c = 3", "c = 30")):
        asyncio.run(
            edit_batch_server._handle_edit(
                {"action": "replace", "path": "f.py", "old": old, "new": new}
            )
        )

    fourth = asyncio.run(
        edit_batch_server._handle_edit(
            {"action": "replace", "path": "f.py", "old": "d = 4", "new": "d = 40"}
        )
    )
    assert mcp_server.EDIT_BATCH_NUDGE_MESSAGE not in fourth[0].kwargs["text"]


def test_handle_edit_different_path_in_between_resets_streak(
    edit_batch_server: mcp_server.HeadroomMCPServer, tmp_path: Path
) -> None:
    (tmp_path / "f.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "g.py").write_text("y = 1\ny2 = 2\n", encoding="utf-8")

    asyncio.run(
        edit_batch_server._handle_edit(
            {"action": "replace", "path": "f.py", "old": "x = 1", "new": "x = 10"}
        )
    )
    asyncio.run(
        edit_batch_server._handle_edit(
            {"action": "replace", "path": "g.py", "old": "y = 1", "new": "y = 10"}
        )
    )
    third_overall = asyncio.run(
        edit_batch_server._handle_edit(
            {"action": "replace", "path": "g.py", "old": "y2 = 2", "new": "y2 = 20"}
        )
    )

    assert mcp_server.EDIT_BATCH_NUDGE_MESSAGE not in third_overall[0].kwargs["text"]


def test_handle_edit_multi_call_resets_streak(
    edit_batch_server: mcp_server.HeadroomMCPServer, tmp_path: Path
) -> None:
    (tmp_path / "h.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")

    asyncio.run(
        edit_batch_server._handle_edit(
            {"action": "replace", "path": "h.py", "old": "a = 1", "new": "a = 10"}
        )
    )
    asyncio.run(
        edit_batch_server._handle_edit(
            {"action": "replace", "path": "h.py", "old": "b = 2", "new": "b = 20"}
        )
    )
    asyncio.run(
        edit_batch_server._handle_edit(
            {
                "action": "multi",
                "path": "h.py",
                "edits": [{"old": "c = 3", "new": "c = 30"}],
            }
        )
    )

    response = asyncio.run(
        edit_batch_server._handle_edit(
            {"action": "replace", "path": "h.py", "old": "a = 10", "new": "a = 100"}
        )
    )
    assert mcp_server.EDIT_BATCH_NUDGE_MESSAGE not in response[0].kwargs["text"]


def test_handle_search_call_resets_streak(
    edit_batch_server: mcp_server.HeadroomMCPServer, tmp_path: Path
) -> None:
    (tmp_path / "f.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    (tmp_path / "readme.txt").write_text("hello\n", encoding="utf-8")

    asyncio.run(
        edit_batch_server._handle_edit(
            {"action": "replace", "path": "f.py", "old": "a = 1", "new": "a = 10"}
        )
    )
    asyncio.run(
        edit_batch_server._handle_edit(
            {"action": "replace", "path": "f.py", "old": "b = 2", "new": "b = 20"}
        )
    )

    asyncio.run(edit_batch_server._handle_search({"action": "read", "path": "readme.txt"}))

    response = asyncio.run(
        edit_batch_server._handle_edit(
            {"action": "replace", "path": "f.py", "old": "a = 10", "new": "a = 100"}
        )
    )
    assert mcp_server.EDIT_BATCH_NUDGE_MESSAGE not in response[0].kwargs["text"]
