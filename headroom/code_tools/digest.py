"""A short digest of the open task, saved before compaction and restored after.

Claude Code's compaction rewrites the whole transcript into a single summary.
Anything the summary drops -- the exact next step, a ruling Christopher gave,
a `[Retrieve more: hash=...]` marker -- is gone for good. This module builds
a small, deterministic digest of what matters (no model call) from the raw
transcript, so the `PreCompact` hook can steer the summary toward keeping it,
and the `SessionStart` hook (matcher `"compact"`) can hand it back to the
agent on the other side.

Everything here is pure with respect to the transcript: `build_digest` only
reads the file it is given. `save_digest`/`load_digest` are the only I/O,
and both take `root` explicitly rather than reaching for a global -- the CLI
layer (`headroom.cli.code_agent`) is what defaults `root` to
`headroom.paths.workspace_dir() / "code_tools"`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from headroom import fsutil
from headroom.code_tools.brief import is_machine_prompt

# Tool names whose `tool_use` input names a file that was actually changed.
# `path` is the key `mcp__headroom__Edit` uses; `file_path` is the key the
# built-in Edit/Write/MultiEdit tools use.
_TRACKED_EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "mcp__headroom__Edit"})
_PATH_KEYS = ("path", "file_path")

_ASK_USER_QUESTION_TOOL = "AskUserQuestion"

_MAX_USER_PROMPTS = 3
_MAX_FILES = 15
_MAX_RULINGS = 3
_MIN_STATUS_CHARS = 40

# No single transcript entry is allowed to crowd out the rest of the digest --
# a pasted file or a long ruling is truncated to this many characters before
# the sections are assembled.
_MAX_ENTRY_CHARS = 400

_DIGESTS_DIRNAME = "digests"
_UNSAFE_SESSION_ID_CHARS = re.compile(r"[^A-Za-z0-9_.-]")


# ---------------------------------------------------------------------------
# Building the digest.
# ---------------------------------------------------------------------------


def build_digest(transcript_path: Path, *, max_chars: int = 2500) -> str:
    """Return a plain-text digest of `transcript_path`, or `""` if it has nothing useful.

    Reads the Claude Code transcript (JSONL, one JSON object per line, each
    with a `"type"` of `"user"` or `"assistant"` and a `"message"` holding
    `"content"` as either a string or a list of blocks). Extracts, in this
    order:

    * "Open task" -- the last `_MAX_USER_PROMPTS` real user prompts, in
      chronological order. A prompt is skipped when it is a
      machine-generated notification (`is_machine_prompt`) or when the
      "user" line holds only `tool_result` blocks and no text.
    * "Files touched" -- distinct file paths named by `tool_use` blocks for
      `Edit`, `Write`, `MultiEdit`, or `mcp__headroom__Edit` (the `path` or
      `file_path` input key), newest first, capped at `_MAX_FILES`.
    * "Rulings" -- the last `_MAX_RULINGS` answers Christopher gave to an
      `AskUserQuestion` tool call (the matching `tool_result`'s content), in
      chronological order.
    * "Last status" -- the last assistant text block longer than
      `_MIN_STATUS_CHARS` characters.

    A missing/unreadable file or a transcript with none of the above yields
    `""`. The rendered text is capped at `max_chars`.
    """
    records = _load_records(transcript_path)
    if not records:
        return ""

    sections: list[tuple[str, list[str]]] = [
        ("Open task", _real_user_prompts(records)),
        ("Files touched", _touched_files(records)),
        ("Rulings", _ask_user_question_answers(records)),
        ("Last status", _last_status(records)),
    ]
    sections = [(title, items) for title, items in sections if items]
    if not sections:
        return ""

    rendered = _render(sections)
    return rendered[:max_chars].rstrip()


def precompact_context(digest: str) -> str:
    """The `PreCompact` `additionalContext`: an instruction, then `digest`.

    Tells the compaction summary to keep the open task, every ruling, the
    files touched, the exact next step, and every retrieve marker or hash
    verbatim -- the details a generic summary is most likely to drop -- and
    then hands it the digest itself.
    """
    instruction = (
        "When summarizing this conversation, preserve verbatim: the open "
        "task, every ruling Christopher made, every file touched, the exact "
        "next step, and every retrieve marker or hash (e.g. "
        "'[Retrieve more: hash=...]'). Do not paraphrase these away."
    )
    if not digest:
        return instruction
    return f"{instruction}\n\n{digest}"


# ---------------------------------------------------------------------------
# Transcript reading.
# ---------------------------------------------------------------------------


def _load_records(transcript_path: Path) -> list[dict]:
    raw = fsutil.read_text(transcript_path, default="")
    if not raw:
        return []
    records: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _message_content(record: dict) -> str | list | None:
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    return message.get("content")


# ---------------------------------------------------------------------------
# (a) Open task -- the last real user prompts.
# ---------------------------------------------------------------------------


def _real_user_prompts(records: list[dict]) -> list[str]:
    prompts: list[str] = []
    for record in records:
        if record.get("type") != "user":
            continue
        text = _user_prompt_text(record)
        if text is not None:
            prompts.append(_truncate(text))
    return prompts[-_MAX_USER_PROMPTS:]


def _user_prompt_text(record: dict) -> str | None:
    """The real prompt text in a "user" line, or `None` for anything else.

    A `"user"` line is not always something Christopher typed: it is also
    how a tool result comes back. Only a plain string `content`, or a
    `"text"` block inside a list `content`, counts as a prompt -- a line
    whose `content` is a list of nothing but `tool_result` blocks yields no
    text and is skipped.
    """
    content = _message_content(record)
    text: str | None = None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        texts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        joined = "\n".join(t for t in texts if t)
        text = joined or None

    if text is None:
        return None
    stripped = text.strip()
    if not stripped or is_machine_prompt(stripped):
        return None
    return stripped


# ---------------------------------------------------------------------------
# (b) Files touched -- newest first, deduped.
# ---------------------------------------------------------------------------


def _touched_files(records: list[dict]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for record in reversed(records):
        if record.get("type") != "assistant":
            continue
        content = _message_content(record)
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") not in _TRACKED_EDIT_TOOLS:
                continue
            path = _edit_input_path(block.get("input"))
            if path is None or path in seen:
                continue
            seen.add(path)
            paths.append(path)
            if len(paths) >= _MAX_FILES:
                return paths
    return paths


def _edit_input_path(tool_input: object) -> str | None:
    if not isinstance(tool_input, dict):
        return None
    for key in _PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


# ---------------------------------------------------------------------------
# (d) Rulings -- Christopher's answers to AskUserQuestion.
# ---------------------------------------------------------------------------


def _ask_user_question_answers(records: list[dict]) -> list[str]:
    pending_ids: set[str] = set()
    answers: list[str] = []
    for record in records:
        content = _message_content(record)
        if not isinstance(content, list):
            continue
        record_type = record.get("type")
        if record_type == "assistant":
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") != _ASK_USER_QUESTION_TOOL:
                    continue
                tc_id = block.get("id")
                if isinstance(tc_id, str) and tc_id:
                    pending_ids.add(tc_id)
        elif record_type == "user":
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tc_id = block.get("tool_use_id")
                if tc_id not in pending_ids:
                    continue
                text = _tool_result_text(block.get("content"))
                if text:
                    answers.append(_truncate(text))
    return answers[-_MAX_RULINGS:]


def _tool_result_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(t for t in texts if t).strip()
    return ""


# ---------------------------------------------------------------------------
# (c) Last status -- the last assistant text block over the length floor.
# ---------------------------------------------------------------------------


def _last_status(records: list[dict]) -> list[str]:
    for record in reversed(records):
        if record.get("type") != "assistant":
            continue
        content = _message_content(record)
        if not isinstance(content, list):
            continue
        for block in reversed(content):
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = block.get("text")
            if not isinstance(text, str):
                continue
            stripped = text.strip()
            if len(stripped) > _MIN_STATUS_CHARS:
                return [_truncate(stripped)]
    return []


# ---------------------------------------------------------------------------
# Rendering.
# ---------------------------------------------------------------------------


def _truncate(text: str) -> str:
    if len(text) <= _MAX_ENTRY_CHARS:
        return text
    return text[: _MAX_ENTRY_CHARS - 1].rstrip() + "…"


def _render(sections: list[tuple[str, list[str]]]) -> str:
    parts = []
    for title, items in sections:
        lines = [title, *(f"- {item}" for item in items)]
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Saving and loading -- the only I/O in this module.
# ---------------------------------------------------------------------------


def _safe_session_id(session_id: str) -> str:
    """A `session_id` cannot escape `root/digests/` via `/` or `..`."""
    safe = _UNSAFE_SESSION_ID_CHARS.sub("_", session_id)
    return safe or "_"


def _digest_path(session_id: str, root: Path) -> Path:
    return root / _DIGESTS_DIRNAME / f"{_safe_session_id(session_id)}.md"


def save_digest(session_id: str, text: str, root: Path) -> Path:
    """Write `text` to `root/digests/<session_id>.md`, and return that path."""
    path = _digest_path(session_id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fsutil.write_text(path, text)
    return path


def load_digest(session_id: str, root: Path) -> str | None:
    """Return the saved digest for `session_id`, or `None` if there is none."""
    path = _digest_path(session_id, root)
    text = fsutil.read_text(path, default=None)
    if not text or not text.strip():
        return None
    return text
