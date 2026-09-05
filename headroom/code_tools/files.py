"""Shared file-path and file-content helpers for the code agent's tools.

Search and Edit both resolve request paths against a root, refuse a path
that escapes it, compute the stamp that lets a caller prove it already
holds a file's exact bytes, and read a file's text the same lossy-safe way.
Both tools import these from here rather than reaching into each other's
private names.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

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
    """Resolve a request path against ``root``.

    A relative path resolves against ``root``. An absolute path is allowed,
    but it must still land inside ``root``'s tree — anything else is
    refused, so Search can't be used to read files outside the project.
    A path under ``.git`` is refused too, whether it was written as
    relative, absolute, or through a symlink — Search and Edit share this
    check so neither can read or write the repo's own git data.
    """

    candidate = Path(raw_path).expanduser()
    target = candidate if candidate.is_absolute() else root / candidate
    resolved = target.resolve()
    root_resolved = root.resolve()
    try:
        rel = resolved.relative_to(root_resolved)
    except ValueError:
        raise PathOutsideRootError(f"path outside root: {raw_path}") from None
    if rel.parts[:1] == (".git",):
        raise PathOutsideRootError(f"refused: path under .git: {raw_path}")
    return resolved


def line_count(content: str) -> int:
    return content.count("\n") + (1 if content and not content.endswith("\n") else 0)


def read_file_text(path: Path) -> str:
    return safe_decode_for_logging(path.read_bytes())


__all__ = [
    "PathOutsideRootError",
    "file_stamp",
    "line_count",
    "read_file_text",
    "resolve_path",
]
