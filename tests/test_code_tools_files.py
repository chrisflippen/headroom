"""Tests for ``headroom.code_tools.files.resolve_path`` and the allowed-roots
rule it now checks against: the launch directory, every sibling git
worktree, and any directory added via ``headroom code-agent roots add``."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from headroom.code_tools import roots as code_tools_roots
from headroom.code_tools.files import PathOutsideRootError, resolve_path


@pytest.fixture(autouse=True)
def _workspace(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Point the workspace dir (and so the roots file) at a throwaway dir,
    same as the code_tools search/edit tests, so nothing here touches the
    real ``~/.headroom``."""

    ws = tmp_path_factory.mktemp("ws")
    monkeypatch.setenv("HEADROOM_WORKSPACE_DIR", str(ws))
    return ws


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


# --- 1. Regression: relative paths still resolve against the launch dir, and
#        a path outside every allowed root is still rejected. -----------------


def test_resolve_path_relative_resolves_against_launch_dir(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x")

    resolved = resolve_path("a.py", tmp_path)

    assert resolved == (tmp_path / "a.py").resolve()


def test_resolve_path_rejects_path_outside_every_root(tmp_path: Path) -> None:
    launch_dir = tmp_path / "project"
    launch_dir.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret.txt").write_text("nope")

    with pytest.raises(PathOutsideRootError):
        resolve_path(str(outside / "secret.txt"), launch_dir)


# --- 2. A path inside a sibling git worktree resolves. -----------------------


def test_resolve_path_accepts_path_inside_sibling_worktree(tmp_path: Path) -> None:
    main_checkout = tmp_path / "main"
    main_checkout.mkdir()
    _git("init", "-b", "main", cwd=main_checkout)
    _git("config", "user.email", "test@example.com", cwd=main_checkout)
    _git("config", "user.name", "Test", cwd=main_checkout)
    (main_checkout / "README.md").write_text("hello")
    _git("add", "README.md", cwd=main_checkout)
    _git("commit", "-m", "initial", cwd=main_checkout)

    worktree_dir = tmp_path / "feature-worktree"
    _git("worktree", "add", "-b", "feature", str(worktree_dir), cwd=main_checkout)
    (worktree_dir / "new_file.py").write_text("print('hi')")

    resolved = resolve_path(str(worktree_dir / "new_file.py"), main_checkout)

    assert resolved == (worktree_dir / "new_file.py").resolve()


def test_resolve_path_rejects_git_dir_inside_sibling_worktree(tmp_path: Path) -> None:
    main_checkout = tmp_path / "main"
    main_checkout.mkdir()
    _git("init", "-b", "main", cwd=main_checkout)
    _git("config", "user.email", "test@example.com", cwd=main_checkout)
    _git("config", "user.name", "Test", cwd=main_checkout)
    (main_checkout / "README.md").write_text("hello")
    _git("add", "README.md", cwd=main_checkout)
    _git("commit", "-m", "initial", cwd=main_checkout)

    worktree_dir = tmp_path / "feature-worktree"
    _git("worktree", "add", "-b", "feature", str(worktree_dir), cwd=main_checkout)

    with pytest.raises(PathOutsideRootError):
        resolve_path(str(worktree_dir / ".git" / "HEAD"), main_checkout)


# --- 3. A path under a roots-file root resolves only for the launch dir it
#        was added under. ----------------------------------------------------


def test_resolve_path_accepts_path_under_roots_file_root_for_launch_dir(tmp_path: Path) -> None:
    launch_dir = tmp_path / "project"
    launch_dir.mkdir()
    added = tmp_path / "added-root"
    added.mkdir()
    (added / "data.py").write_text("x")

    code_tools_roots.add_root(launch_dir, added)

    resolved = resolve_path(str(added / "data.py"), launch_dir)

    assert resolved == (added / "data.py").resolve()


def test_resolve_path_rejects_roots_file_root_added_under_a_different_launch_dir(
    tmp_path: Path,
) -> None:
    other_launch_dir = tmp_path / "other-project"
    other_launch_dir.mkdir()
    this_launch_dir = tmp_path / "this-project"
    this_launch_dir.mkdir()
    added = tmp_path / "added-root"
    added.mkdir()
    (added / "data.py").write_text("x")

    code_tools_roots.add_root(other_launch_dir, added)

    with pytest.raises(PathOutsideRootError):
        resolve_path(str(added / "data.py"), this_launch_dir)


# --- 4. roots.py: add/remove/list round-trip and the missing-directory guard.


def test_add_root_then_file_roots_round_trips(tmp_path: Path) -> None:
    launch_dir = tmp_path / "project"
    launch_dir.mkdir()
    target = tmp_path / "sibling"
    target.mkdir()

    stored = code_tools_roots.add_root(launch_dir, target)

    assert stored == target.resolve()
    assert code_tools_roots.file_roots(launch_dir) == [target.resolve()]


def test_add_root_rejects_a_missing_directory(tmp_path: Path) -> None:
    launch_dir = tmp_path / "project"
    launch_dir.mkdir()
    missing = tmp_path / "does-not-exist"

    with pytest.raises(ValueError):
        code_tools_roots.add_root(launch_dir, missing)


def test_remove_root_drops_it(tmp_path: Path) -> None:
    launch_dir = tmp_path / "project"
    launch_dir.mkdir()
    target = tmp_path / "sibling"
    target.mkdir()
    code_tools_roots.add_root(launch_dir, target)

    removed = code_tools_roots.remove_root(launch_dir, target)

    assert removed is True
    assert code_tools_roots.file_roots(launch_dir) == []


def test_remove_root_reports_false_when_not_present(tmp_path: Path) -> None:
    launch_dir = tmp_path / "project"
    launch_dir.mkdir()
    target = tmp_path / "sibling"
    target.mkdir()

    assert code_tools_roots.remove_root(launch_dir, target) is False
