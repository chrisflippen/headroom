"""Extra roots the code agent's Search and Edit tools may reach.

Search and Edit resolve every path against the launch directory (today's
single "root"). That is too narrow for a session launched in one git
worktree that needs to touch a sibling worktree or checkout, so a request
path is now accepted when it resolves inside any *allowed root* for the
launch directory:

1. the launch directory itself;
2. every git worktree of the same repository, found fresh via
   ``git worktree list --porcelain`` run from the launch directory (a
   launch directory that is not in a git repo just contributes nothing
   here -- failures are ignored silently);
3. any directory added with ``headroom code-agent roots add``, recorded in
   the roots file below under the launch directory's own absolute path, so
   different projects keep separate lists.

Nothing here is cached across calls: ``allowed_roots`` re-reads the roots
file and re-runs ``git worktree list`` every time, so a root added mid
session takes effect on the very next Search or Edit call.
"""

from __future__ import annotations

import json
from pathlib import Path

from headroom import fsutil, paths
from headroom._subprocess import run as _run_subprocess

_ROOTS_FILE = "roots.json"


def roots_file_path() -> Path:
    """Return the path to the code agent's added-roots config file."""

    return paths.code_tools_dir() / _ROOTS_FILE


def _load() -> dict[str, list[str]]:
    text = fsutil.read_text(roots_file_path(), default=None)
    if text is None:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _save(data: dict[str, list[str]]) -> None:
    path = roots_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fsutil.write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def git_worktrees(launch_dir: Path) -> list[Path]:
    """Return every git worktree of the repo containing ``launch_dir``.

    Runs ``git worktree list --porcelain`` from ``launch_dir``. Returns an
    empty list, without raising, when ``launch_dir`` isn't inside a git
    repo or the command otherwise fails -- a non-repo launch directory just
    contributes nothing here.
    """

    try:
        proc = _run_subprocess(
            ["git", "worktree", "list", "--porcelain"],
            cwd=launch_dir,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except OSError:
        return []
    if proc.returncode != 0:
        return []

    found: list[Path] = []
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            found.append(Path(line[len("worktree ") :]))
    return found


def file_roots(launch_dir: Path) -> list[Path]:
    """Return the roots-file entries recorded for ``launch_dir``.

    Keyed by ``launch_dir``'s own absolute path, so different projects keep
    separate lists (the same directory added under one launch directory
    does not become reachable from another).
    """

    data = _load()
    entries = data.get(str(launch_dir.resolve()), [])
    if not isinstance(entries, list):
        return []
    return [Path(entry) for entry in entries if isinstance(entry, str)]


def add_root(launch_dir: Path, target: Path) -> Path:
    """Add ``target`` as an extra root for ``launch_dir``.

    Refuses a ``target`` that is not an existing directory. Returns the
    resolved absolute path that was stored (adding the same directory
    twice is a no-op, not a duplicate entry).
    """

    if not target.is_dir():
        raise ValueError(f"not an existing directory: {target}")

    resolved = str(target.resolve())
    key = str(launch_dir.resolve())
    data = _load()
    entries = data.setdefault(key, [])
    if resolved not in entries:
        entries.append(resolved)
    _save(data)
    return Path(resolved)


def remove_root(launch_dir: Path, target: Path) -> bool:
    """Remove ``target`` from ``launch_dir``'s added roots.

    Returns whether it was there to remove.
    """

    resolved = str(target.resolve())
    key = str(launch_dir.resolve())
    data = _load()
    entries = data.get(key, [])
    if resolved not in entries:
        return False
    entries.remove(resolved)
    if entries:
        data[key] = entries
    else:
        data.pop(key, None)
    _save(data)
    return True


def allowed_roots(launch_dir: Path) -> list[Path]:
    """Return every root a Search/Edit path may resolve inside, for
    ``launch_dir``: the launch directory itself, every sibling git
    worktree, then every added root -- in that order, deduplicated by
    resolved path. Recomputed fresh on every call; see the module
    docstring."""

    seen: set[Path] = set()
    result: list[Path] = []
    for candidate in (launch_dir, *git_worktrees(launch_dir), *file_roots(launch_dir)):
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(candidate)
    return result


__all__ = [
    "roots_file_path",
    "git_worktrees",
    "file_roots",
    "add_root",
    "remove_root",
    "allowed_roots",
]
