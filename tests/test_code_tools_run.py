"""Tests for ``headroom.code_tools.run`` — the Run MCP tool.

Run executes one shell command through login-shell semantics (``/bin/zsh
-lc``), merges stdout+stderr, and returns compact shaped text: a totals
line, then a head/tail window of the output with repeats collapsed and
long lines clipped. When the window drops or clips anything, the full
output is parked in the CCR compression store and a retrieve marker is
appended so ``headroom_retrieve`` can still get all of it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from headroom.cache.compression_store import get_compression_store, reset_compression_store
from headroom.code_tools.run import run

_HEADER_RE = re.compile(
    r"^exit=(?P<exit>-?\d+) lines=(?P<lines>\d+) chars=(?P<chars>\d+) "
    r"time=(?P<time>\d+\.\d+)s(?P<timed_out> timed out)?$"
)


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


def _header(text: str) -> re.Match[str]:
    first_line = text.splitlines()[0]
    match = _HEADER_RE.match(first_line)
    assert match is not None, f"header line did not match expected shape: {first_line!r}"
    return match


# --- 1. exit code and totals line -------------------------------------------


def test_totals_line_reports_exit_code_lines_and_chars(tmp_path: Path) -> None:
    result = run({"command": "printf 'line1\\nline2\\nline3'"}, root=tmp_path)

    match = _header(result)
    assert match["exit"] == "0"
    assert match["lines"] == "3"
    assert match["chars"] == "17"
    assert match["timed_out"] is None
    assert result.splitlines()[1:] == ["line1", "line2", "line3"]


def test_totals_line_reports_nonzero_exit_code(tmp_path: Path) -> None:
    result = run({"command": "exit 7"}, root=tmp_path)

    match = _header(result)
    assert match["exit"] == "7"
    assert match["lines"] == "0"
    assert match["chars"] == "0"


# --- 2. head/tail windows with the omitted-lines separator -------------------


def test_head_tail_window_separates_with_omitted_count(tmp_path: Path) -> None:
    command = "for i in $(seq 1 10); do echo line$i; done"
    result = run({"command": command, "head": 2, "tail": 2}, root=tmp_path)

    match = _header(result)
    assert match["lines"] == "10"
    body = result.splitlines()[1:]
    assert body[0] == "line1"
    assert body[1] == "line2"
    assert body[2] == "… 6 lines omitted …"
    assert body[3] == "line9"
    assert body[4] == "line10"
    assert "Retrieve original: hash=" in result


def test_output_that_fits_the_window_is_not_windowed(tmp_path: Path) -> None:
    result = run({"command": "printf 'a\\nb\\nc'", "head": 40, "tail": 40}, root=tmp_path)

    assert "omitted" not in result
    assert "Retrieve original: hash=" not in result


# --- 3. repeated lines collapsed with x-count --------------------------------


def test_consecutive_repeats_collapse_with_count(tmp_path: Path) -> None:
    command = "printf 'same\\nsame\\nsame\\nother\\n'"
    result = run({"command": command}, root=tmp_path)

    match = _header(result)
    assert match["lines"] == "4"
    body = result.splitlines()[1:]
    assert body[0] == "same ×3"
    assert body[1] == "other"
    assert "Retrieve original: hash=" in result


def test_non_repeating_lines_are_not_collapsed(tmp_path: Path) -> None:
    result = run({"command": "printf 'a\\nb\\nc\\n'"}, root=tmp_path)

    assert "×" not in result


# --- 4. long line clipped ----------------------------------------------------


def test_long_line_is_clipped_at_400_chars(tmp_path: Path) -> None:
    result = run({"command": "python3 -c \"print('a' * 500)\""}, root=tmp_path)

    match = _header(result)
    assert match["lines"] == "1"
    body = result.splitlines()[1]
    assert body == "a" * 400 + "…"
    assert "Retrieve original: hash=" in result


# --- 5. full output stored when lossy; nothing stored when it fits ----------


def test_full_output_is_retrievable_when_windowed(tmp_path: Path) -> None:
    command = "for i in $(seq 1 10); do echo line$i; done"
    result = run({"command": command, "head": 2, "tail": 2}, root=tmp_path)

    hash_match = re.search(r"Retrieve original: hash=([0-9a-f]+)", result)
    assert hash_match is not None
    entry = get_compression_store().retrieve(hash_match.group(1))
    assert entry is not None
    expected_original = "\n".join(f"line{i}" for i in range(1, 11)) + "\n"
    assert entry.original_content == expected_original


def test_nothing_is_stored_when_output_fits(tmp_path: Path) -> None:
    stats_before = get_compression_store().get_stats()["entry_count"]

    run({"command": "printf 'a\\nb\\nc'"}, root=tmp_path)

    stats_after = get_compression_store().get_stats()["entry_count"]
    assert stats_after == stats_before


# --- 6. timeout reported -----------------------------------------------------


def test_timeout_is_reported_in_header(tmp_path: Path) -> None:
    result = run({"command": "sleep 3", "timeout_seconds": 1}, root=tmp_path)

    match = _header(result)
    assert match["timed_out"] == " timed out"
    assert match["exit"] == "124"


# --- 7. empty command refused ------------------------------------------------


def test_empty_command_is_refused(tmp_path: Path) -> None:
    result = run({"command": ""}, root=tmp_path)

    assert result == "error: command is required"


def test_whitespace_only_command_is_refused(tmp_path: Path) -> None:
    result = run({"command": "   "}, root=tmp_path)

    assert result == "error: command is required"


def test_missing_command_is_refused(tmp_path: Path) -> None:
    result = run({}, root=tmp_path)

    assert result == "error: command is required"


# --- 8. cwd outside roots refused --------------------------------------------


def test_cwd_outside_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    result = run({"command": "echo hi", "cwd": str(outside)}, root=root)

    assert result == f"error: path outside root: {outside}"
