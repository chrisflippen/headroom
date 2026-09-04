"""Search: the code agent's file-reading tool.

The code agent reaches files only through Search — never the built-in Read.
Its first job, the ``read`` action, is a smarter Read: return a file's
content once, then return a short "unchanged" marker on every later read of
the same, unmodified file, so the model never pays tokens to see the same
bytes twice. Later slices add ``find``, ``grep``, ``symbols`` and
``importers`` as more actions on this same entry point — add a handler
function per action, not a new entry point.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from headroom.cache.compression_store import get_compression_store
from headroom.code_tools.read_cache import ReadCache
from headroom.proxy.helpers import safe_decode_for_logging

# Session-scoped: content lives as long as the coding session, matching the
# MCP server's own MCP_SESSION_TTL (headroom/ccr/mcp_server.py). Kept as its
# own constant here rather than imported, so this module never has to import
# the MCP server module (mcp_server imports this module, not the other way).
SESSION_TTL = 3600


class PathOutsideRootError(ValueError):
    """Raised when a request path resolves to somewhere outside the root."""


def resolve_path(raw_path: str, root: Path) -> Path:
    """Resolve a request path against ``root``.

    A relative path resolves against ``root``. An absolute path is allowed,
    but it must still land inside ``root``'s tree — anything else is
    refused, so Search can't be used to read files outside the project.
    """

    candidate = Path(raw_path).expanduser()
    target = candidate if candidate.is_absolute() else root / candidate
    resolved = target.resolve()
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise PathOutsideRootError(f"path outside root: {raw_path}") from None
    return resolved


def is_unchanged_marker(text: str) -> bool:
    """True when ``text`` is the unchanged-file marker the ``read`` action emits."""

    return text.startswith("<file ") and 'status="unchanged"' in text


def _numbered_lines(content: str) -> str:
    return "\n".join(f"{i:>6}\t{line}" for i, line in enumerate(content.split("\n"), 1))


def _line_count(content: str) -> int:
    return content.count("\n") + (1 if content and not content.endswith("\n") else 0)


def _read_file_text(path: Path) -> str:
    return safe_decode_for_logging(path.read_bytes())


def _read_range(content: str, raw_path: str, start: Any, end: Any) -> str:
    lines = content.split("\n")
    total = len(lines)
    try:
        start_line = int(start) if start is not None else 1
        end_line = int(end) if end is not None else total
    except (TypeError, ValueError):
        return "error: start and end must be integers"

    start_line = max(1, start_line)
    end_line = min(total, end_line)
    if start_line > end_line:
        return f"error: empty range: start={start} end={end}"

    selected = lines[start_line - 1 : end_line]
    numbered = "\n".join(f"{i:>6}\t{line}" for i, line in enumerate(selected, start_line))
    header = f"{raw_path}: lines {start_line}-{end_line} of {total}"
    return f"{header}\n{numbered}"


def _handle_read(request: dict[str, Any], root: Path) -> str:
    raw_path = request.get("path")
    if not raw_path:
        return "error: path is required"
    raw_path = str(raw_path)

    try:
        path = resolve_path(raw_path, root)
    except PathOutsideRootError as exc:
        return f"error: {exc}"

    if not path.exists():
        return f"error: file not found: {raw_path}"
    if path.is_dir():
        return f"error: path is a directory: {raw_path}"

    try:
        content = _read_file_text(path)
    except OSError as exc:
        return f"error: cannot read file: {exc}"

    start = request.get("start")
    end = request.get("end")
    if start is not None or end is not None:
        return _read_range(content, raw_path, start, end)

    fresh = bool(request.get("fresh", False))
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:24]
    line_count = _line_count(content)
    str_path = str(path)
    cache = ReadCache()

    if not fresh:
        entry = cache.get(str_path)
        if entry is not None and entry.content_hash == content_hash:
            store = get_compression_store()
            if store.exists(entry.store_hash):
                return (
                    f'<file path="{raw_path}" status="unchanged" '
                    f'lines="{entry.line_count}" hash="{entry.store_hash}"/>'
                )
            # The compression store entry expired — the marker's hash would
            # 404 on retrieval, so treat this like a fresh file and re-store.
            cache.invalidate(str_path)

    store = get_compression_store()
    token_estimate = len(content.split())
    store_hash = store.store(
        original=content,
        compressed=f"[File: {path.name}, {line_count} lines]",
        original_tokens=token_estimate,
        compressed_tokens=5,
        tool_name="search_read",
        ttl=SESSION_TTL,
    )
    cache.put(
        str_path,
        content_hash=content_hash,
        store_hash=store_hash,
        line_count=line_count,
        token_estimate=token_estimate,
    )

    header = f"{raw_path}: {line_count} lines"
    return f"{header}\n{_numbered_lines(content)}"


_ACTIONS: dict[str, Callable[[dict[str, Any], Path], str]] = {
    "read": _handle_read,
}


def search(request: dict[str, Any], root: Path) -> str:
    """Run one Search action and return the result as plain text.

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
