"""Tests for ``headroom.code_tools.search`` — the Search MCP tool's ``read`` action."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from headroom.cache.compression_store import reset_compression_store
from headroom.code_tools.search import search
from tests._mcp_stub import import_module_with_mcp_stub

mcp_server = import_module_with_mcp_stub("headroom.ccr.mcp_server")

# Known sha256(content).hexdigest()[:12] literals for the fixture bodies below —
# the stamp format. Computed once, hardcoded here, never recomputed by calling
# the code under test.
V1_STAMP = "19a9b60d67d7"
V2_STAMP = "0ee054b6fee9"

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
    """Point the workspace dir at a throwaway dir for every test, so a bug that
    writes anything to disk shows up as a stray file instead of leaking
    between tests."""

    ws = tmp_path_factory.mktemp("ws")
    monkeypatch.setenv("HEADROOM_WORKSPACE_DIR", str(ws))
    return ws


# --- 1. Full read: header carries a stamp, then numbered content ------------


def test_read_returns_header_with_stamp_and_numbered_content(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(V1_CONTENT)

    result = search({"action": "read", "path": "a.py"}, root=tmp_path)

    assert result == f"file: a.py lines=2 stamp={V1_STAMP}\n{V1_NUMBERED}"


# --- 2. Passing back the stamp gets a marker; a wrong or missing one doesn't -


def test_read_with_matching_stamp_returns_only_marker(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(V1_CONTENT)

    first = search({"action": "read", "path": "a.py"}, root=tmp_path)
    second = search({"action": "read", "path": "a.py", "stamp": V1_STAMP}, root=tmp_path)

    assert first == f"file: a.py lines=2 stamp={V1_STAMP}\n{V1_NUMBERED}"
    assert second == (
        f'<file path="a.py" status="unchanged" lines="2" tokens="2" stamp="{V1_STAMP}"/>'
    )


def test_read_without_a_stamp_is_a_full_read_even_when_unchanged(tmp_path: Path) -> None:
    """No cache is kept, so a caller that never got a stamp — a helper agent
    that didn't do the first read, say — always gets the full text back."""
    (tmp_path / "a.py").write_text(V1_CONTENT)
    search({"action": "read", "path": "a.py"}, root=tmp_path)

    result = search({"action": "read", "path": "a.py"}, root=tmp_path)

    assert result == f"file: a.py lines=2 stamp={V1_STAMP}\n{V1_NUMBERED}"


def test_read_with_wrong_stamp_is_a_full_read(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(V1_CONTENT)

    result = search({"action": "read", "path": "a.py", "stamp": "notarealstamp"}, root=tmp_path)

    assert result == f"file: a.py lines=2 stamp={V1_STAMP}\n{V1_NUMBERED}"


def test_read_with_malformed_stamp_type_is_a_full_read(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(V1_CONTENT)

    result = search({"action": "read", "path": "a.py", "stamp": 12345}, root=tmp_path)

    assert result == f"file: a.py lines=2 stamp={V1_STAMP}\n{V1_NUMBERED}"


# --- 3. A changed file: the old stamp no longer matches, new stamp comes back


def test_read_changed_file_gets_full_content_with_the_new_stamp(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text(V1_CONTENT)
    search({"action": "read", "path": "a.py"}, root=tmp_path)

    target.write_text(V2_CONTENT)
    result = search({"action": "read", "path": "a.py", "stamp": V1_STAMP}, root=tmp_path)

    assert result == f"file: a.py lines=3 stamp={V2_STAMP}\n{V2_NUMBERED}"


def test_read_with_new_stamp_after_a_change_returns_the_marker(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text(V1_CONTENT)
    search({"action": "read", "path": "a.py"}, root=tmp_path)
    target.write_text(V2_CONTENT)
    search({"action": "read", "path": "a.py"}, root=tmp_path)

    result = search({"action": "read", "path": "a.py", "stamp": V2_STAMP}, root=tmp_path)

    assert result == (
        f'<file path="a.py" status="unchanged" lines="3" tokens="3" stamp="{V2_STAMP}"/>'
    )


# --- 4. No cache: nothing is consulted or written to disk -------------------


def test_read_never_writes_anything_to_the_workspace_dir(tmp_path: Path, _workspace: Path) -> None:
    (tmp_path / "a.py").write_text(V1_CONTENT)

    search({"action": "read", "path": "a.py"}, root=tmp_path)
    search({"action": "read", "path": "a.py", "stamp": V1_STAMP}, root=tmp_path)

    assert not _workspace.exists() or not any(_workspace.iterdir())


def test_read_cache_module_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        import headroom.code_tools.read_cache  # noqa: F401


# --- 6. Path handling: relative/absolute/outside-root/missing/directory ----


def test_relative_path_resolves_against_root(tmp_path: Path) -> None:
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "a.py").write_text(V1_CONTENT)

    result = search({"action": "read", "path": "src/a.py"}, root=tmp_path)

    assert result == f"file: src/a.py lines=2 stamp={V1_STAMP}\n{V1_NUMBERED}"


def test_absolute_path_within_root_is_allowed_and_shown_relative(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text(V1_CONTENT)

    result = search({"action": "read", "path": str(target)}, root=tmp_path)

    assert result == f"file: a.py lines=2 stamp={V1_STAMP}\n{V1_NUMBERED}"


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


def test_git_config_path_is_refused(tmp_path: Path) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]\n")

    result = search({"action": "read", "path": ".git/config"}, root=tmp_path)

    assert result == "error: refused: path under .git: .git/config"


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


# --- 7. start/end line range, header carries a stamp too ---------------------

RANGE_STAMP = "42064da518ab"


def test_start_end_returns_only_that_range_with_a_stamp(tmp_path: Path) -> None:
    content = "one\ntwo\nthree\nfour\nfive"
    (tmp_path / "a.py").write_text(content)

    result = search({"action": "read", "path": "a.py", "start": 2, "end": 4}, root=tmp_path)

    assert result == (
        f"a.py: lines 2-4 of 5 stamp={RANGE_STAMP}\n     2\ttwo\n     3\tthree\n     4\tfour"
    )


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
    assert schema["properties"]["action"]["enum"] == [
        "read",
        "find",
        "grep",
        "symbols",
        "importers",
    ]
    assert "path" in schema["properties"]
    assert "pattern" in schema["properties"]
    assert "glob" in schema["properties"]
    assert "context" in schema["properties"]
    assert "limit" in schema["properties"]
    assert "start" in schema["properties"]
    assert "end" in schema["properties"]
    assert "fresh" not in schema["properties"]
    assert "stamp" in schema["properties"]
    stamp_description = schema["properties"]["stamp"]["description"].lower()
    assert "stamp" in stamp_description
    assert "marker" in stamp_description
    description = search_tool.kwargs["description"].lower()
    assert "read" in description
    assert "unchanged" in description
    assert "marker" in description
    assert "already have" in description or "already has" in description
    assert "find" in description
    assert "grep" in description
    assert "symbols" in description
    assert "importers" in description
    assert len(description.split()) < 60


def test_server_has_no_leftover_read_state() -> None:
    server = mcp_server.HeadroomMCPServer(check_proxy=False)

    assert not hasattr(server, "_file_cache")
    assert not hasattr(server, "_handle_read")


def test_handle_search_read_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.py").write_text(V1_CONTENT)
    monkeypatch.chdir(tmp_path)

    server = mcp_server.HeadroomMCPServer(check_proxy=False)
    response = asyncio.run(server._handle_search({"action": "read", "path": "a.py"}))

    assert response[0].kwargs["text"] == f"file: a.py lines=2 stamp={V1_STAMP}\n{V1_NUMBERED}"


def test_handle_search_records_stats_on_unchanged_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.py").write_text(V1_CONTENT)
    monkeypatch.chdir(tmp_path)

    server = mcp_server.HeadroomMCPServer(check_proxy=False)
    asyncio.run(server._handle_search({"action": "read", "path": "a.py"}))
    before = server._stats.compressions

    response = asyncio.run(
        server._handle_search({"action": "read", "path": "a.py", "stamp": V1_STAMP})
    )

    assert response[0].kwargs["text"] == (
        f'<file path="a.py" status="unchanged" lines="2" tokens="2" stamp="{V1_STAMP}"/>'
    )
    assert server._stats.compressions == before + 1
    assert server._stats.events[-1]["strategy"] == "search_unchanged"


# --- 10. find: list files by glob, honouring .gitignore, with a cap --------


def test_find_lists_files_matching_pattern(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "b.py").write_text("x")
    (tmp_path / "notes.txt").write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.py").write_text("x")

    result = search({"action": "find", "pattern": "**/*.py"}, root=tmp_path)

    assert result == "a.py\nb.py\nsub/c.py"


def test_find_honours_gitignore_in_git_repo(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    (tmp_path / "kept.py").write_text("x")
    (tmp_path / "ignored.py").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)

    result = search({"action": "find", "pattern": "**/*.py"}, root=tmp_path)

    assert result == "kept.py"


def test_find_caps_results_with_footer(tmp_path: Path) -> None:
    for name in ["a.py", "b.py", "c.py", "d.py", "e.py"]:
        (tmp_path / name).write_text("x")

    result = search({"action": "find", "pattern": "**/*.py", "limit": 3}, root=tmp_path)

    assert result == "a.py\nb.py\nc.py\n… 2 more"


def test_find_no_matches_gives_plain_message(tmp_path: Path) -> None:
    result = search({"action": "find", "pattern": "**/*.py"}, root=tmp_path)

    assert result == "no files found"


# --- 11. grep: regex search grouped by file, with rg or a Python fallback --


def test_grep_refuses_empty_pattern(tmp_path: Path) -> None:
    result = search({"action": "grep", "pattern": ""}, root=tmp_path)

    assert result == "error: pattern is required"


def test_grep_groups_matches_by_file(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("one\nfoo\nthree\n")
    (tmp_path / "b.py").write_text("foo\nfive\n")

    result = search({"action": "grep", "pattern": "foo"}, root=tmp_path)

    assert result == "a.py:\n  2: foo\nb.py:\n  1: foo"


def test_grep_no_matches_gives_plain_message(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("one\ntwo\n")

    result = search({"action": "grep", "pattern": "nope"}, root=tmp_path)

    assert result == "no matches"


def test_grep_falls_back_without_rg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.py").write_text("one\nfoo\nthree\n")
    (tmp_path / "b.py").write_text("foo\nfive\n")
    monkeypatch.setattr(
        "headroom.code_tools.search.shutil.which", lambda name: None if name == "rg" else name
    )

    result = search({"action": "grep", "pattern": "foo"}, root=tmp_path)

    assert result == "a.py:\n  2: foo\nb.py:\n  1: foo"


def test_grep_caps_matches_with_footer(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("foo\nfoo\nfoo\nfoo\n")

    result = search({"action": "grep", "pattern": "foo", "limit": 2}, root=tmp_path)

    assert result == "a.py:\n  1: foo\n  2: foo\n… 2 more"


# --- 12. symbols: outline of classes, functions, and types -------------------

PY_SYMBOLS_SOURCE = (
    "class Greeter:\n"
    "    def greet(self):\n"
    '        return "hi"\n'
    "\n"
    "\n"
    "def helper():\n"
    "    pass\n"
    "\n"
    "\n"
    "def other():\n"
    "    pass\n"
)

TS_SYMBOLS_SOURCE = (
    "type Config = {\n"
    "  host: string\n"
    "}\n"
    "\n"
    "class Service {\n"
    "  run() {\n"
    "    return true\n"
    "  }\n"
    "}\n"
    "\n"
    "function start() {\n"
    "  return 1\n"
    "}\n"
    "\n"
    'type Mode = "a" | "b"\n'
)


def test_symbols_outlines_a_python_file(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(PY_SYMBOLS_SOURCE)

    result = search({"action": "symbols", "path": "a.py"}, root=tmp_path)

    assert result == (
        "a.py:\n   1  class Greeter\n   2    def greet\n   6  def helper\n  10  def other"
    )


def test_symbols_outlines_a_typescript_file(tmp_path: Path) -> None:
    (tmp_path / "a.ts").write_text(TS_SYMBOLS_SOURCE)

    result = search({"action": "symbols", "path": "a.ts"}, root=tmp_path)

    assert result == (
        "a.ts:\n   1  type Config\n   5  class Service\n   6    def run\n  11  def start\n"
        "  15  type Mode"
    )


def test_symbols_falls_back_to_regex_without_tree_sitter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.py").write_text("class Greeter:\n    def greet(self):\n        pass\n")

    def _boom(language: str) -> None:
        raise ImportError("no tree-sitter")

    monkeypatch.setattr("headroom.code_tools.search.parser_for", _boom)

    result = search({"action": "symbols", "path": "a.py"}, root=tmp_path)

    assert result == (
        "a.py:\n   1  class Greeter\n   2  def greet\n"
        "(tree-sitter unavailable, used a regex outline)"
    )


# --- 13. importers: files that import a given module -------------------------


def test_importers_finds_importing_files_but_not_unrelated(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "helper.py").write_text("def do_thing():\n    pass\n")
    (tmp_path / "b.py").write_text("from src.helper import do_thing\n")
    (tmp_path / "c.py").write_text("import src.helper as h\n")
    (tmp_path / "d.py").write_text("from other import thing\n")

    result = search({"action": "importers", "path": "src/helper.py"}, root=tmp_path)

    assert result == (
        "b.py:\n  1: from src.helper import do_thing\nc.py:\n  1: import src.helper as h"
    )


def test_importers_searches_the_worktree_containing_the_target(tmp_path: Path) -> None:
    import subprocess

    main_checkout = tmp_path / "main"
    main_checkout.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=main_checkout, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=main_checkout, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=main_checkout, check=True)
    (main_checkout / "README.md").write_text("hello")
    subprocess.run(["git", "add", "README.md"], cwd=main_checkout, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=main_checkout, check=True)

    worktree_dir = tmp_path / "feature-worktree"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature", str(worktree_dir)],
        cwd=main_checkout,
        check=True,
    )
    (worktree_dir / "src").mkdir()
    (worktree_dir / "src" / "helper.py").write_text("def do_thing():\n    pass\n")
    (worktree_dir / "b.py").write_text("from src.helper import do_thing\n")

    result = search(
        {"action": "importers", "path": str(worktree_dir / "src" / "helper.py")},
        root=main_checkout,
    )

    assert result == "b.py:\n  1: from src.helper import do_thing"
