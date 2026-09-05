"""Shared file-path and file-content helpers for the code agent's tools.

Search and Edit both resolve request paths against a launch directory,
refuse a path that escapes every allowed root, compute the stamp that lets
a caller prove it already holds a file's exact bytes, and read a file's
text the same lossy-safe way. Both tools import these from here rather
than reaching into each other's private names.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from headroom.code_tools.roots import allowed_roots
from headroom.proxy.helpers import safe_decode_for_logging

_STAMP_LENGTH = 12


def file_stamp(content: str) -> str:
    """Return the stamp for a file's content: the first 12 hex characters of
    the sha256 hash of its bytes. Two reads of the same bytes get the same
    stamp; any change to the bytes changes it. Shared by Search and Edit so
    a write reports the same stamp a read of the result would."""

    return hashlib.sha256(content.encode()).hexdigest()[:_STAMP_LENGTH]


class PathOutsideRootError(ValueError):
    """Raised when a request path resolves to somewhere outside the root."""


def resolve_path(raw_path: str, root: Path) -> Path:
    """Resolve a request path against ``root``, the launch directory.

    A relative path always resolves against ``root``. The resolved path is
    accepted when it lands inside ``root`` itself, inside a sibling git
    worktree of ``root``, or inside a directory added with
    ``headroom code-agent roots add`` for ``root`` — see
    ``headroom.code_tools.roots.allowed_roots``, recomputed fresh on every
    call. Anything else is refused, so Search and Edit can't reach files
    outside every allowed root. A path under ``.git`` is refused too,
    whether it was written as relative, absolute, or through a symlink —
    Search and Edit share this check so neither can read or write a repo's
    own git data.
    """

    candidate = Path(raw_path).expanduser()
    target = candidate if candidate.is_absolute() else root / candidate
    resolved = target.resolve()
    for allowed in allowed_roots(root):
        allowed_resolved = allowed.resolve()
        try:
            rel = resolved.relative_to(allowed_resolved)
        except ValueError:
            continue
        if rel.parts[:1] == (".git",):
            raise PathOutsideRootError(f"refused: path under .git: {raw_path}")
        return resolved
    raise PathOutsideRootError(f"path outside root: {raw_path}")


def root_containing(resolved: Path, root: Path) -> Path:
    """Return whichever allowed root contains ``resolved``: ``root`` itself
    when possible, else the first sibling git worktree or added root that
    does. Callers pass in a path already checked by ``resolve_path``, so one
    of ``allowed_roots(root)`` is guaranteed to contain it; falls back to
    ``root`` itself if none does, so a caller that skipped that check still
    gets a usable root rather than an exception."""

    resolved = resolved.resolve()
    for allowed in allowed_roots(root):
        allowed_resolved = allowed.resolve()
        try:
            resolved.relative_to(allowed_resolved)
        except ValueError:
            continue
        return allowed_resolved
    return root.resolve()


def display_path(resolved: Path, root: Path) -> str:
    """The path to show back to a caller for ``resolved``: relative to
    whichever allowed root contains it (``root`` itself, a sibling git
    worktree, or an added root), so a file reached through a widened root
    still gets a short, readable path instead of a raw absolute one."""

    resolved = resolved.resolve()
    containing = root_containing(resolved, root)
    try:
        return resolved.relative_to(containing).as_posix()
    except ValueError:
        return resolved.as_posix()


def line_count(content: str) -> int:
    return content.count("\n") + (1 if content and not content.endswith("\n") else 0)


def read_file_text(path: Path) -> str:
    return safe_decode_for_logging(path.read_bytes())


__all__ = [
    "PathOutsideRootError",
    "display_path",
    "file_stamp",
    "line_count",
    "read_file_text",
    "resolve_path",
    "root_containing",
]
