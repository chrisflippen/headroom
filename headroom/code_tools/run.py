"""Run: the code agent's shaped command-output tool.

Runs one shell command through the same login-shell semantics the built-in
Bash tool uses (``/bin/zsh -lc``), merging stdout and stderr into one
stream. The full output almost never needs to reach the model: this module
returns a compact totals line plus a head/tail window of the output, with
consecutive repeats collapsed and long lines clipped. When that window
drops or clips anything, the full text is parked in the CCR compression
store and a retrieve marker is appended, so ``headroom_retrieve`` can still
get all of it -- mirroring ``search(request, root)`` in
``headroom.code_tools.search`` and ``edit(...)`` in
``headroom.code_tools.edit``.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from headroom._subprocess import run as _run_subprocess
from headroom.code_tools.files import PathOutsideRootError, resolve_path

_DEFAULT_TIMEOUT_SECONDS = 120
_MAX_TIMEOUT_SECONDS = 600
_DEFAULT_HEAD = 40
_DEFAULT_TAIL = 40
_LINE_CLIP_CHARS = 400
_TIMEOUT_EXIT_CODE = 124
_SHELL = "/bin/zsh"


def _clip_line(line: str) -> tuple[str, bool]:
    """Return ``line`` clipped to ``_LINE_CLIP_CHARS``, and whether it was."""

    if len(line) <= _LINE_CLIP_CHARS:
        return line, False
    return line[:_LINE_CLIP_CHARS] + "…", True


def _collapse_repeats(lines: list[str]) -> tuple[list[str], bool]:
    """Collapse consecutive identical raw lines into ``"<line> ×<count>"``,
    clipping the representative line. Returns the display lines in order,
    and whether anything was lossy along the way -- a run collapsed to a
    count, or a line clipped."""

    collapsed: list[str] = []
    lossy = False
    index = 0
    total = len(lines)
    while index < total:
        end = index + 1
        while end < total and lines[end] == lines[index]:
            end += 1
        count = end - index
        clipped_line, was_clipped = _clip_line(lines[index])
        if was_clipped:
            lossy = True
        if count > 1:
            collapsed.append(f"{clipped_line} ×{count}")
            lossy = True
        else:
            collapsed.append(clipped_line)
        index = end
    return collapsed, lossy


def _window(display_lines: list[str], head: int, tail: int) -> tuple[list[str], bool]:
    """Keep the first ``head`` and last ``tail`` of ``display_lines``, with a
    "… N lines omitted …" separator in between when there is a middle to
    drop. Returns the windowed lines, and whether anything was omitted."""

    total = len(display_lines)
    if total <= head + tail:
        return display_lines, False
    omitted = total - head - tail
    windowed = [
        *display_lines[:head],
        f"… {omitted} lines omitted …",
        *display_lines[total - tail :],
    ]
    return windowed, True


def _store_full_output(original: str, compressed: str) -> str:
    """Store the full output in the CCR compression store and return the
    retrieve marker line, or a one-line explanation if the store could not
    be reached. Reuses the same singleton accessor the rest of the MCP
    server uses -- see ``_get_local_store`` in ``headroom.ccr.mcp_server``
    -- rather than opening a second store."""

    try:
        from headroom.cache.compression_store import get_compression_store

        store = get_compression_store()
        hash_key = store.store(original, compressed, tool_name="Run")
    except Exception as exc:  # noqa: BLE001 - storage failure must not break Run
        return f"[retrieve store unavailable: {exc}]"
    return f"[full output stored — retrieve if needed. Retrieve original: hash={hash_key}]"


def run(request: dict[str, Any], root: Path) -> str:
    """Run one shell command and return the shaped result as plain text.

    ``request`` is the tool call's arguments: ``command`` (required),
    ``cwd`` (optional, resolved against ``root``), ``timeout_seconds``
    (default 120, max 600), ``head`` (default 40), and ``tail`` (default
    40). Every failure -- an empty command, a cwd outside every allowed
    root -- comes back as one plain text line, never a traceback, so a bad
    request can't crash the agent's turn.
    """

    raw_command = request.get("command")
    command = str(raw_command).strip() if raw_command else ""
    if not command:
        return "error: command is required"

    raw_cwd = request.get("cwd")
    if raw_cwd:
        try:
            cwd = resolve_path(str(raw_cwd), root)
        except PathOutsideRootError as exc:
            return f"error: {exc}"
    else:
        cwd = root

    timeout_seconds = request.get("timeout_seconds")
    timeout_seconds = _DEFAULT_TIMEOUT_SECONDS if timeout_seconds is None else int(timeout_seconds)
    timeout_seconds = min(timeout_seconds, _MAX_TIMEOUT_SECONDS)

    head = request.get("head")
    head = _DEFAULT_HEAD if head is None else int(head)
    tail = request.get("tail")
    tail = _DEFAULT_TAIL if tail is None else int(tail)

    started = time.monotonic()
    timed_out = False
    try:
        completed = _run_subprocess(
            [_SHELL, "-lc", command],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_seconds,
        )
        exit_code = completed.returncode
        output_text = completed.stdout or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = _TIMEOUT_EXIT_CODE
        captured = exc.stdout or ""
        output_text = (
            captured if isinstance(captured, str) else captured.decode("utf-8", errors="replace")
        )
    except OSError as exc:
        return f"error: {exc}"
    elapsed = time.monotonic() - started

    total_chars = len(output_text)
    raw_lines = output_text.splitlines()
    total_lines = len(raw_lines)

    display_lines, collapsed_anything = _collapse_repeats(raw_lines)
    windowed_lines, omitted_anything = _window(display_lines, head, tail)

    header = f"exit={exit_code} lines={total_lines} chars={total_chars} time={elapsed:.2f}s"
    if timed_out:
        header += " timed out"

    body = [header, *windowed_lines]

    if collapsed_anything or omitted_anything:
        body.append(_store_full_output(output_text, "\n".join(windowed_lines)))

    return "\n".join(body)
