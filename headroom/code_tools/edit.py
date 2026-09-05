"""Edit: the code agent's file-writing tool.

The code agent changes files only through Edit — never the built-in Edit or
Write. One entry point, five actions: ``replace`` and ``multi`` patch text
in an existing file, ``create`` makes a new one, ``delete`` removes one, and
``rename`` moves one. Every action that writes new bytes to a file ends its
result line with a stamp for the new content, so the caller can pass that
stamp back to Search later instead of reading the file again.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from headroom import fsutil
from headroom.code_tools.search import PathOutsideRootError, file_stamp, resolve_path
from headroom.proxy.helpers import safe_decode_for_logging


def _read_file_text(path: Path) -> str:
    return safe_decode_for_logging(path.read_bytes())


def _line_count(content: str) -> int:
    return content.count("\n") + (1 if content and not content.endswith("\n") else 0)


def _resolve(raw_path: str, root: Path) -> Path | str:
    """Resolve ``raw_path`` against ``root``, refusing anything outside the
    root or under ``.git``. Returns the resolved path, or a plain error
    string a handler can return straight to its caller."""

    try:
        path = resolve_path(raw_path, root)
    except PathOutsideRootError as exc:
        return f"error: {exc}"

    try:
        rel = path.relative_to(root.resolve())
    except ValueError:
        rel = None
    if rel is not None and rel.parts[:1] == (".git",):
        return f"error: refused: path under .git: {raw_path}"

    return path


def _find_occurrences(content: str, old: str) -> list[int]:
    """Return the start offset of every non-overlapping occurrence of
    ``old`` in ``content``, in the order ``str.replace`` would find them."""

    offsets = []
    start = 0
    step = max(len(old), 1)
    while True:
        idx = content.find(old, start)
        if idx == -1:
            break
        offsets.append(idx)
        start = idx + step
    return offsets


def _line_span(content: str, offset: int, length: int) -> tuple[int, int]:
    start_line = content.count("\n", 0, offset) + 1
    end_line = content.count("\n", 0, offset + length) + 1
    return start_line, end_line


def _handle_replace(request: dict[str, Any], root: Path) -> str:
    raw_path = request.get("path")
    if not raw_path:
        return "error: path is required"
    raw_path = str(raw_path)

    old = request.get("old")
    new = request.get("new")
    if old is None or new is None:
        return "error: old and new are required"
    old = str(old)
    new = str(new)
    replace_all = bool(request.get("all", False))

    resolved = _resolve(raw_path, root)
    if isinstance(resolved, str):
        return resolved
    path = resolved

    if not path.exists():
        return f"error: file not found: {raw_path}"
    if path.is_dir():
        return f"error: path is a directory: {raw_path}"

    try:
        content = _read_file_text(path)
    except OSError as exc:
        return f"error: cannot read file: {exc}"

    offsets = _find_occurrences(content, old)
    count = len(offsets)
    if count == 0:
        return f'error: "old" not found in {raw_path} (0 occurrences)'
    if count > 1 and not replace_all:
        return (
            f'error: "old" found {count} times in {raw_path}, expected exactly 1 '
            f'(pass "all": true to replace every occurrence)'
        )

    if replace_all:
        new_content = content.replace(old, new)
        plural = "" if count == 1 else "s"
        summary = f"replaced {count} occurrence{plural}"
    else:
        offset = offsets[0]
        new_content = content[:offset] + new + content[offset + len(old) :]
        start_line, end_line = _line_span(content, offset, len(old))
        summary = f"replaced 1 occurrence (lines {start_line}-{end_line})"

    fsutil.write_text(path, new_content)
    stamp = file_stamp(new_content)
    return f"edited {raw_path}: {summary} stamp={stamp}"


def _handle_multi(request: dict[str, Any], root: Path) -> str:
    raw_path = request.get("path")
    if not raw_path:
        return "error: path is required"
    raw_path = str(raw_path)

    edits = request.get("edits")
    if not isinstance(edits, list) or not edits:
        return "error: edits is required and must be a non-empty list"

    resolved = _resolve(raw_path, root)
    if isinstance(resolved, str):
        return resolved
    path = resolved

    if not path.exists():
        return f"error: file not found: {raw_path}"
    if path.is_dir():
        return f"error: path is a directory: {raw_path}"

    try:
        content = _read_file_text(path)
    except OSError as exc:
        return f"error: cannot read file: {exc}"

    running = content
    for index, one_edit in enumerate(edits):
        if not isinstance(one_edit, dict):
            return f"error: edit {index} is not an object, nothing written"
        old = one_edit.get("old")
        new = one_edit.get("new")
        if old is None or new is None:
            return f"error: edit {index} is missing old or new, nothing written"
        old = str(old)
        new = str(new)

        offsets = _find_occurrences(running, old)
        count = len(offsets)
        if count == 0:
            return f'error: edit {index}: "old" not found (0 occurrences), nothing written'
        if count > 1:
            return (
                f'error: edit {index}: "old" found {count} times, expected exactly 1, '
                "nothing written"
            )
        offset = offsets[0]
        running = running[:offset] + new + running[offset + len(old) :]

    fsutil.write_text(path, running)
    stamp = file_stamp(running)
    return f"edited {raw_path}: applied {len(edits)} edits stamp={stamp}"


def _handle_create(request: dict[str, Any], root: Path) -> str:
    raw_path = request.get("path")
    if not raw_path:
        return "error: path is required"
    raw_path = str(raw_path)

    content = request.get("content")
    if content is None:
        return "error: content is required"
    content = str(content)
    overwrite = bool(request.get("overwrite", False))

    resolved = _resolve(raw_path, root)
    if isinstance(resolved, str):
        return resolved
    path = resolved

    if path.exists():
        if path.is_dir():
            return f"error: path is a directory: {raw_path}"
        if not overwrite:
            return f'error: file already exists: {raw_path} (pass "overwrite": true to replace it)'

    path.parent.mkdir(parents=True, exist_ok=True)
    fsutil.write_text(path, content)
    stamp = file_stamp(content)
    line_count = _line_count(content)
    plural = "" if line_count == 1 else "s"
    return f"created {raw_path}: {line_count} line{plural} stamp={stamp}"


def _handle_delete(request: dict[str, Any], root: Path) -> str:
    raw_path = request.get("path")
    if not raw_path:
        return "error: path is required"
    raw_path = str(raw_path)

    resolved = _resolve(raw_path, root)
    if isinstance(resolved, str):
        return resolved
    path = resolved

    if not path.exists():
        return f"error: file not found: {raw_path}"
    if path.is_dir():
        return f"error: refused: path is a directory: {raw_path}"

    path.unlink()
    return f"deleted {raw_path}"


def _handle_rename(request: dict[str, Any], root: Path) -> str:
    raw_path = request.get("path")
    raw_to = request.get("to")
    if not raw_path or not raw_to:
        return "error: path and to are required"
    raw_path = str(raw_path)
    raw_to = str(raw_to)

    resolved = _resolve(raw_path, root)
    if isinstance(resolved, str):
        return resolved
    path = resolved

    resolved_to = _resolve(raw_to, root)
    if isinstance(resolved_to, str):
        return resolved_to
    target = resolved_to

    if not path.exists():
        return f"error: file not found: {raw_path}"
    if path.is_dir():
        return f"error: refused: path is a directory: {raw_path}"
    if target.exists():
        return f"error: target already exists: {raw_to}"

    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(path, target)

    return f"renamed {raw_path} -> {raw_to}"


_ACTIONS: dict[str, Callable[[dict[str, Any], Path], str]] = {
    "replace": _handle_replace,
    "multi": _handle_multi,
    "create": _handle_create,
    "delete": _handle_delete,
    "rename": _handle_rename,
}


def edit(request: dict[str, Any], root: Path) -> str:
    """Run one Edit action and return the result as plain text.

    ``request`` is the tool call's arguments. ``root`` is the directory a
    relative ``path`` resolves against. Every failure — a bad action, a
    missing file, a path outside root — comes back as one plain text line,
    never a traceback, so a bad request can't crash the agent's turn.
    """

    action = request.get("action")
    handler = _ACTIONS.get(str(action))
    if handler is None:
        return f"error: unknown action: {action!r}"
    return handler(request, root)
