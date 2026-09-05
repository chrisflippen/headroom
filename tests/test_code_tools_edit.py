"""Tests for ``headroom.code_tools.edit`` — the Edit MCP tool."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from headroom.code_tools.edit import edit
from headroom.code_tools.search import file_stamp
from tests._mcp_stub import import_module_with_mcp_stub

mcp_server = import_module_with_mcp_stub("headroom.ccr.mcp_server")

V1_CONTENT = "print('hello')\nprint('world')"

# Known stamps: the first 12 hex characters of sha256(content.encode()).
# Precomputed once, hardcoded here — never recomputed by calling the code
# under test.
REPLACE_RESULT_STAMP = "fc0b85ca6901"  # "first\nnew line\nlast\n"
REPLACE_ALL_STAMP = "66d725133e00"  # "x = 2\nx = 2\n"
MULTI_RESULT_STAMP = "37d16a23a527"  # "ALPHA\nbeta\nGAMMA\n"
CREATE_CONTENT_STAMP = "caf026f25d71"  # "print('hi')\n"
CREATE_OVERWRITE_STAMP = "1d054714357c"  # "replacement\n"
CRLF_RESULT_STAMP = "fa950abca236"  # "first\r\nnew line\r\nlast\r\n"


@pytest.fixture(autouse=True)
def _workspace(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Point the workspace root at a throwaway dir for every test."""

    ws = tmp_path_factory.mktemp("ws")
    monkeypatch.setenv("HEADROOM_WORKSPACE_DIR", str(ws))
    return ws


# --- 1. replace: two matches refused, file untouched -------------------------


def test_replace_two_matches_refused_file_byte_identical(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    original = "x = 1\nx = 1\n"
    target.write_bytes(original.encode())

    result = edit({"action": "replace", "path": "a.py", "old": "x = 1", "new": "x = 2"}, tmp_path)

    assert result == (
        'error: "old" found 2 times in a.py, expected exactly 1 '
        '(pass "all": true to replace every occurrence)'
    )
    assert target.read_bytes() == original.encode()


# --- 2. replace: one match writes the expected literal file ------------------


def test_replace_one_match_writes_expected_file(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("first\nold line\nlast\n")

    result = edit(
        {"action": "replace", "path": "a.py", "old": "old line", "new": "new line"}, tmp_path
    )

    assert result == f"edited a.py: replaced 1 occurrence (lines 2-2) stamp={REPLACE_RESULT_STAMP}"
    assert target.read_text() == "first\nnew line\nlast\n"


# --- 3. replace: all: true replaces both --------------------------------------


def test_replace_all_true_replaces_both(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("x = 1\nx = 1\n")

    result = edit(
        {"action": "replace", "path": "a.py", "old": "x = 1", "new": "x = 2", "all": True},
        tmp_path,
    )

    assert result == f"edited a.py: replaced 2 occurrences stamp={REPLACE_ALL_STAMP}"
    assert target.read_text() == "x = 2\nx = 2\n"


# --- 4. multi: applies edits in order; a failing edit writes nothing ---------


def test_multi_applies_two_edits_in_order(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("alpha\nbeta\ngamma\n")

    result = edit(
        {
            "action": "multi",
            "path": "a.py",
            "edits": [
                {"old": "alpha", "new": "ALPHA"},
                {"old": "gamma", "new": "GAMMA"},
            ],
        },
        tmp_path,
    )

    assert result == f"edited a.py: applied 2 edits stamp={MULTI_RESULT_STAMP}"
    assert target.read_text() == "ALPHA\nbeta\nGAMMA\n"


def test_multi_failing_second_edit_leaves_file_untouched(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    original = "alpha\nbeta\ngamma\n"
    target.write_bytes(original.encode())

    result = edit(
        {
            "action": "multi",
            "path": "a.py",
            "edits": [
                {"old": "alpha", "new": "ALPHA"},
                {"old": "not there", "new": "x"},
            ],
        },
        tmp_path,
    )

    assert result == 'error: edit 1: "old" not found (0 occurrences), nothing written'
    assert target.read_bytes() == original.encode()


# --- 5. create: makes parent folders, refuses to overwrite -------------------


def test_create_makes_parent_folders(tmp_path: Path) -> None:
    result = edit(
        {"action": "create", "path": "new/dir/b.py", "content": "print('hi')\n"}, tmp_path
    )

    assert result == f"created new/dir/b.py: 1 line stamp={CREATE_CONTENT_STAMP}"
    assert (tmp_path / "new" / "dir" / "b.py").read_text() == "print('hi')\n"


def test_create_refuses_existing_file_without_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "b.py"
    target.write_text("original\n")

    result = edit({"action": "create", "path": "b.py", "content": "replacement\n"}, tmp_path)

    assert result == ('error: file already exists: b.py (pass "overwrite": true to replace it)')
    assert target.read_text() == "original\n"


def test_create_overwrites_when_asked(tmp_path: Path) -> None:
    target = tmp_path / "b.py"
    target.write_text("original\n")

    result = edit(
        {"action": "create", "path": "b.py", "content": "replacement\n", "overwrite": True},
        tmp_path,
    )

    assert result == f"created b.py: 1 line stamp={CREATE_OVERWRITE_STAMP}"
    assert target.read_text() == "replacement\n"


# --- 6. delete: refuses a directory -------------------------------------------


def test_delete_refuses_a_directory(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()

    result = edit({"action": "delete", "path": "src"}, tmp_path)

    assert result == "error: refused: path is a directory: src"
    assert (tmp_path / "src").is_dir()


def test_delete_removes_a_file(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text(V1_CONTENT)

    result = edit({"action": "delete", "path": "a.py"}, tmp_path)

    assert result == "deleted a.py"
    assert not target.exists()


def test_delete_missing_path_gives_plain_error(tmp_path: Path) -> None:
    result = edit({"action": "delete", "path": "nope.py"}, tmp_path)

    assert result == "error: file not found: nope.py"


# --- 7. rename: refuses an existing target ------------------------------------


def test_rename_refuses_an_existing_target(tmp_path: Path) -> None:
    source = tmp_path / "a.py"
    source.write_text(V1_CONTENT)
    dest = tmp_path / "b.py"
    dest.write_text("already here\n")

    result = edit({"action": "rename", "path": "a.py", "to": "b.py"}, tmp_path)

    assert result == "error: target already exists: b.py"
    assert source.exists()
    assert dest.read_text() == "already here\n"


def test_rename_moves_a_file(tmp_path: Path) -> None:
    source = tmp_path / "a.py"
    source.write_text(V1_CONTENT)

    result = edit({"action": "rename", "path": "a.py", "to": "b.py"}, tmp_path)

    assert result == "renamed a.py -> b.py"
    assert not source.exists()
    assert (tmp_path / "b.py").read_text() == V1_CONTENT


# --- 8. stamp contract: Edit's stamp matches Search's stamp for the result ---


def test_edit_returns_the_same_stamp_search_would_compute_for_the_result(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("print('hi')\nprint('world')")

    # "print('hi')\nprint('world')" with "hi" -> "hello" becomes
    # "print('hello')\nprint('world')", the exact bytes V1_CONTENT names in
    # tests/test_code_tools_search.py — this is the same known stamp,
    # confirming edit.py and search.py compute file_stamp identically.
    result = edit({"action": "replace", "path": "a.py", "old": "hi", "new": "hello"}, tmp_path)

    assert result == "edited a.py: replaced 1 occurrence (lines 1-1) stamp=19a9b60d67d7"
    assert file_stamp(target.read_text()) == "19a9b60d67d7"


# --- 9. a .git path is refused -------------------------------------------------


def test_git_config_path_is_refused(tmp_path: Path) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]\n")

    result = edit(
        {"action": "replace", "path": ".git/config", "old": "core", "new": "nope"}, tmp_path
    )

    assert result == "error: refused: path under .git: .git/config"
    assert (git_dir / "config").read_text() == "[core]\n"


# --- 10. CRLF files stay CRLF --------------------------------------------------


def test_crlf_file_stays_crlf(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_bytes(b"first\r\nold line\r\nlast\r\n")

    result = edit(
        {"action": "replace", "path": "a.py", "old": "old line", "new": "new line"}, tmp_path
    )

    assert result == f"edited a.py: replaced 1 occurrence (lines 2-2) stamp={CRLF_RESULT_STAMP}"
    assert target.read_bytes() == b"first\r\nnew line\r\nlast\r\n"


# --- 11. MCP wiring: Edit is registered and reachable through the server ----


def test_edit_tool_is_registered() -> None:
    server = mcp_server.HeadroomMCPServer(check_proxy=False)
    tools = server._tool_definitions()
    names = [t.kwargs["name"] for t in tools]

    assert "Edit" in names
    edit_tool = next(t for t in tools if t.kwargs["name"] == "Edit")
    schema = edit_tool.kwargs["inputSchema"]
    assert schema["properties"]["action"]["enum"] == [
        "replace",
        "multi",
        "create",
        "delete",
        "rename",
    ]
    for field in ["path", "old", "new", "all", "edits", "content", "overwrite", "to"]:
        assert field in schema["properties"]
    assert len(edit_tool.kwargs["description"].split()) < 60


def test_handle_edit_create_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    server = mcp_server.HeadroomMCPServer(check_proxy=False)
    response = asyncio.run(
        server._handle_edit({"action": "create", "path": "a.py", "content": "print('hi')\n"})
    )

    assert response[0].kwargs["text"] == f"created a.py: 1 line stamp={CREATE_CONTENT_STAMP}"
    assert (tmp_path / "a.py").read_text() == "print('hi')\n"
