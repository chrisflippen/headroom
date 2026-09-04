"""Tests for ``headroom.code_tools.search`` — the Search MCP tool's ``read`` action."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from headroom.cache.compression_store import reset_compression_store
from headroom.code_tools.read_cache import ReadCache
from headroom.code_tools.search import search
from tests._mcp_stub import import_module_with_mcp_stub

mcp_server = import_module_with_mcp_stub("headroom.ccr.mcp_server")

# Known sha256(content).hexdigest()[:24] literals for the fixture bodies below.
# Computed once, hardcoded here — not recomputed by calling the code under test.
V1_HASH = "19a9b60d67d74ebb7730a9fa"
V2_HASH = "0ee054b6fee9438f50692225"

V1_CONTENT = "print('hello')\nprint('world')"
V2_CONTENT = "print('hello')\nprint('world')\nprint('changed')"

V1_NUMBERED = "     1\tprint('hello')\n     2\tprint('world')"
V2_NUMBERED = "     1\tprint('hello')\n     2\tprint('world')\n     3\tprint('changed')"


@pytest.fixture(autouse=True)
def _fresh_store() -> Iterator[None]:
    reset_compression_store()
    yield
    reset_compression_store()


@pytest.fixture(autouse=True)
def _workspace(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Point the read cache's workspace root at a throwaway dir for every test."""

    ws = tmp_path_factory.mktemp("ws")
    monkeypatch.setenv("HEADROOM_WORKSPACE_DIR", str(ws))
    return ws


# --- 1. Full read: header + numbered content --------------------------------


def test_read_returns_header_and_numbered_content(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(V1_CONTENT)

    result = search({"action": "read", "path": "a.py"}, root=tmp_path)

    assert result == f"a.py: 2 lines\n{V1_NUMBERED}"


# --- 2. Unchanged marker on repeat read --------------------------------------


def test_read_unchanged_file_returns_only_marker(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(V1_CONTENT)

    first = search({"action": "read", "path": "a.py"}, root=tmp_path)
    second = search({"action": "read", "path": "a.py"}, root=tmp_path)

    assert first == f"a.py: 2 lines\n{V1_NUMBERED}"
    assert second == f'<file path="a.py" status="unchanged" lines="2" hash="{V1_HASH}"/>'


# --- 3. Changed file returns full content again, cache updates --------------


def test_read_changed_file_returns_full_content_again(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text(V1_CONTENT)
    search({"action": "read", "path": "a.py"}, root=tmp_path)

    target.write_text(V2_CONTENT)
    second = search({"action": "read", "path": "a.py"}, root=tmp_path)
    third = search({"action": "read", "path": "a.py"}, root=tmp_path)

    assert second == f"a.py: 3 lines\n{V2_NUMBERED}"
    assert third == f'<file path="a.py" status="unchanged" lines="3" hash="{V2_HASH}"/>'


# --- 4. fresh=true forces a full read ----------------------------------------


def test_fresh_forces_full_read_even_when_unchanged(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(V1_CONTENT)
    search({"action": "read", "path": "a.py"}, root=tmp_path)

    result = search({"action": "read", "path": "a.py", "fresh": True}, root=tmp_path)

    assert result == f"a.py: 2 lines\n{V1_NUMBERED}"


# --- 5. Cache is on disk, survives a new instance, tolerates corruption -----


def test_cache_written_to_disk_under_code_tools_dir(tmp_path: Path, _workspace: Path) -> None:
    (tmp_path / "a.py").write_text(V1_CONTENT)

    search({"action": "read", "path": "a.py"}, root=tmp_path)

    cache_file = _workspace / "code_tools" / "read_cache.json"
    assert cache_file.exists()
    data = json.loads(cache_file.read_text())
    assert data[str((tmp_path / "a.py").resolve())]["content_hash"] == V1_HASH


def test_cache_entry_written_by_one_call_is_read_by_a_fresh_call(tmp_path: Path) -> None:
    """Simulates a new process: a brand new ReadCache instance, written to
    directly (as a prior process would have left it), is picked up by
    ``search`` with no in-memory state carried over."""
    target = tmp_path / "a.py"
    target.write_text(V1_CONTENT)

    # A prior read, in a prior "process": store the content, then record the
    # cache entry a fresh ReadCache instance would produce.
    from headroom.cache.compression_store import get_compression_store

    store_hash = get_compression_store().store(
        original=V1_CONTENT,
        compressed="[File: a.py, 2 lines]",
        original_tokens=4,
        compressed_tokens=5,
        tool_name="search_read",
        ttl=3600,
    )
    ReadCache().put(
        str(target.resolve()),
        content_hash=V1_HASH,
        store_hash=store_hash,
        line_count=2,
        token_estimate=4,
    )

    result = search({"action": "read", "path": "a.py"}, root=tmp_path)

    assert result == f'<file path="a.py" status="unchanged" lines="2" hash="{store_hash}"/>'


def test_corrupt_cache_file_is_treated_as_empty(tmp_path: Path, _workspace: Path) -> None:
    (tmp_path / "a.py").write_text(V1_CONTENT)
    cache_dir = _workspace / "code_tools"
    cache_dir.mkdir(parents=True)
    (cache_dir / "read_cache.json").write_text("{not valid json")

    result = search({"action": "read", "path": "a.py"}, root=tmp_path)

    assert result == f"a.py: 2 lines\n{V1_NUMBERED}"


def test_missing_cache_file_is_treated_as_empty(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(V1_CONTENT)

    result = search({"action": "read", "path": "a.py"}, root=tmp_path)

    assert result == f"a.py: 2 lines\n{V1_NUMBERED}"


# --- 6. Path handling: relative/absolute/outside-root/missing/directory ----


def test_relative_path_resolves_against_root(tmp_path: Path) -> None:
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "a.py").write_text(V1_CONTENT)

    result = search({"action": "read", "path": "src/a.py"}, root=tmp_path)

    assert result == f"src/a.py: 2 lines\n{V1_NUMBERED}"


def test_absolute_path_within_root_is_allowed(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text(V1_CONTENT)

    result = search({"action": "read", "path": str(target)}, root=tmp_path)

    assert result == f"{target}: 2 lines\n{V1_NUMBERED}"


def test_path_outside_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text(V1_CONTENT)

    result = search({"action": "read", "path": str(outside)}, root=root)

    assert result == f"error: path outside root: {outside}"


def test_missing_file_gives_plain_error(tmp_path: Path) -> None:
    result = search({"action": "read", "path": "nope.py"}, root=tmp_path)

    assert result == "error: file not found: nope.py"


def test_directory_path_gives_plain_error(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()

    result = search({"action": "read", "path": "src"}, root=tmp_path)

    assert result == "error: path is a directory: src"


def test_missing_path_argument_gives_plain_error(tmp_path: Path) -> None:
    result = search({"action": "read"}, root=tmp_path)

    assert result == "error: path is required"


def test_unknown_action_gives_plain_error(tmp_path: Path) -> None:
    result = search({"action": "dance", "path": "a.py"}, root=tmp_path)

    assert result == "error: unknown action: 'dance'"


# --- 7. start/end line range, cache untouched --------------------------------


def test_start_end_returns_only_that_range(tmp_path: Path) -> None:
    content = "one\ntwo\nthree\nfour\nfive"
    (tmp_path / "a.py").write_text(content)

    result = search({"action": "read", "path": "a.py", "start": 2, "end": 4}, root=tmp_path)

    assert result == "a.py: lines 2-4 of 5\n     2\ttwo\n     3\tthree\n     4\tfour"


def test_start_end_does_not_populate_the_cache(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    content = "one\ntwo\nthree\nfour\nfive"
    target.write_text(content)

    search({"action": "read", "path": "a.py", "start": 2, "end": 4}, root=tmp_path)
    # No cache entry from the ranged read — the next full read is a fresh read.
    entry = ReadCache().get(str(target.resolve()))

    assert entry is None


# --- 8 & 9. Wiring: the Search tool on HeadroomMCPServer --------------------


def test_search_tool_is_registered_and_read_tool_is_gone() -> None:
    server = mcp_server.HeadroomMCPServer(check_proxy=False)
    tools = server._tool_definitions()
    names = [t.kwargs["name"] for t in tools]

    assert "Search" in names
    assert "headroom_read" not in names
    assert not hasattr(mcp_server, "READ_TOOL_NAME")
    assert not hasattr(mcp_server, "_READ_ENABLED")
    assert not hasattr(mcp_server, "HEADROOM_MCP_READ")
    search_tool = next(t for t in tools if t.kwargs["name"] == "Search")
    schema = search_tool.kwargs["inputSchema"]
    assert schema["properties"]["action"]["enum"] == ["read"]
    assert "path" in schema["properties"]
    assert "start" in schema["properties"]
    assert "end" in schema["properties"]
    assert "fresh" in schema["properties"]
    description = search_tool.kwargs["description"].lower()
    assert "read" in description
    assert "unchanged" in description


def test_server_has_no_leftover_read_state() -> None:
    server = mcp_server.HeadroomMCPServer(check_proxy=False)

    assert not hasattr(server, "_file_cache")
    assert not hasattr(server, "_handle_read")


def test_handle_search_read_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.py").write_text(V1_CONTENT)
    monkeypatch.chdir(tmp_path)

    server = mcp_server.HeadroomMCPServer(check_proxy=False)
    response = asyncio.run(server._handle_search({"action": "read", "path": "a.py"}))

    assert response[0].kwargs["text"] == f"a.py: 2 lines\n{V1_NUMBERED}"


def test_handle_search_records_stats_on_unchanged_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.py").write_text(V1_CONTENT)
    monkeypatch.chdir(tmp_path)

    server = mcp_server.HeadroomMCPServer(check_proxy=False)
    asyncio.run(server._handle_search({"action": "read", "path": "a.py"}))
    before = server._stats.compressions

    response = asyncio.run(server._handle_search({"action": "read", "path": "a.py"}))

    assert response[0].kwargs["text"] == (
        f'<file path="a.py" status="unchanged" lines="2" hash="{V1_HASH}"/>'
    )
    assert server._stats.compressions == before + 1
    assert server._stats.events[-1]["strategy"] == "search_unchanged"
