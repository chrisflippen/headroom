"""Tests for headroom.code_tools.digest -- the deterministic, no-model-call

digest of the open task built from a Claude Code transcript before
compaction, and restored after. Calls only the public `build_digest`,
`precompact_context`, `save_digest`, and `load_digest` entry points; the
transcript is a fixture JSONL string written to `tmp_path`, never a real
session file.
"""

from __future__ import annotations

import json
from pathlib import Path

from headroom.code_tools.digest import (
    build_digest,
    load_digest,
    precompact_context,
    save_digest,
)


def _write_transcript(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


def _user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _assistant_text(text: str) -> dict:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def _assistant_tool_use(tool_use_id: str, name: str, tool_input: dict) -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}],
        },
    }


def _user_tool_result(tool_use_id: str, content: object) -> dict:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": content}],
        },
    }


# ---------------------------------------------------------------------------
# Empty / missing transcript.
# ---------------------------------------------------------------------------


def test_empty_transcript_returns_empty_digest(tmp_path: Path) -> None:
    path = _write_transcript(tmp_path, [])
    assert build_digest(path) == ""


def test_missing_transcript_returns_empty_digest(tmp_path: Path) -> None:
    assert build_digest(tmp_path / "does-not-exist.jsonl") == ""


def test_transcript_with_nothing_useful_returns_empty_digest(tmp_path: Path) -> None:
    # Only a short assistant text block (under the length floor) and no
    # user prompts, files, or rulings.
    path = _write_transcript(tmp_path, [_assistant_text("ok")])
    assert build_digest(path) == ""


# ---------------------------------------------------------------------------
# (a) Open task.
# ---------------------------------------------------------------------------


def test_real_prompts_kept_and_notifications_skipped(tmp_path: Path) -> None:
    records = [
        _user("add retry logic to the http client"),
        _user("<task-notification>a background task finished"),
        _user("now also log every retry attempt"),
        _user("some prefix [SYSTEM NOTIFICATION - NOT USER INPUT] some suffix"),
        _user("and cap retries at three"),
    ]
    path = _write_transcript(tmp_path, records)

    result = build_digest(path)

    assert "Open task" in result
    assert "add retry logic to the http client" in result
    assert "now also log every retry attempt" in result
    assert "and cap retries at three" in result
    assert "task-notification" not in result
    assert "SYSTEM NOTIFICATION" not in result


def test_only_last_three_real_prompts_are_kept(tmp_path: Path) -> None:
    records = [_user(f"prompt number {i}") for i in range(5)]
    path = _write_transcript(tmp_path, records)

    result = build_digest(path)

    assert "prompt number 0" not in result
    assert "prompt number 1" not in result
    assert "prompt number 2" in result
    assert "prompt number 3" in result
    assert "prompt number 4" in result


def test_tool_result_only_user_line_is_not_a_prompt(tmp_path: Path) -> None:
    records = [
        _user("do the thing"),
        _assistant_tool_use("tc1", "mcp__headroom__Search", {"query": "x"}),
        _user_tool_result("tc1", "search results here"),
    ]
    path = _write_transcript(tmp_path, records)

    result = build_digest(path)

    assert "do the thing" in result
    assert "search results here" not in result


# ---------------------------------------------------------------------------
# (b) Files touched.
# ---------------------------------------------------------------------------


def test_files_touched_deduped_newest_first(tmp_path: Path) -> None:
    records = [
        _user("do work"),
        _assistant_tool_use("tc1", "Edit", {"file_path": "a.py"}),
        _assistant_tool_use("tc2", "Write", {"file_path": "b.py"}),
        _assistant_tool_use("tc3", "mcp__headroom__Edit", {"path": "c.py"}),
        _assistant_tool_use("tc4", "Edit", {"file_path": "a.py"}),  # repeat, dropped
    ]
    path = _write_transcript(tmp_path, records)

    result = build_digest(path)

    files_section = result.split("Files touched", 1)[1]
    a_index = files_section.index("a.py")
    b_index = files_section.index("b.py")
    c_index = files_section.index("c.py")
    # Newest first, by each path's most recent mention: a.py's newest edit
    # is tc4 (last overall), c.py's is tc3, b.py's is tc2 -- so a.py leads,
    # then c.py, then b.py. tc1's repeat of a.py is dropped as a duplicate.
    assert a_index < c_index < b_index
    assert files_section.count("a.py") == 1


def test_untracked_tool_is_not_a_touched_file(tmp_path: Path) -> None:
    records = [
        _user("do work"),
        _assistant_tool_use("tc1", "mcp__headroom__Search", {"path": "should-not-appear.py"}),
    ]
    path = _write_transcript(tmp_path, records)

    result = build_digest(path)

    assert "should-not-appear.py" not in result


def test_files_touched_capped_at_fifteen(tmp_path: Path) -> None:
    records = [_user("do work")] + [
        _assistant_tool_use(f"tc{i}", "Edit", {"file_path": f"file{i}.py"}) for i in range(20)
    ]
    path = _write_transcript(tmp_path, records)

    result = build_digest(path)

    files_section = result.split("Files touched", 1)[1].split("\n\n", 1)[0]
    assert files_section.count("- file") == 15


# ---------------------------------------------------------------------------
# (d) Rulings -- AskUserQuestion answers.
# ---------------------------------------------------------------------------


def test_ask_user_question_answers_are_captured(tmp_path: Path) -> None:
    records = [
        _user("should we use postgres or sqlite"),
        _assistant_tool_use(
            "aq1", "AskUserQuestion", {"questions": [{"question": "postgres or sqlite?"}]}
        ),
        _user_tool_result("aq1", "sqlite, keep it simple"),
    ]
    path = _write_transcript(tmp_path, records)

    result = build_digest(path)

    assert "Rulings" in result
    assert "sqlite, keep it simple" in result


def test_only_last_three_rulings_are_kept(tmp_path: Path) -> None:
    records = [_user("kick things off")]
    for i in range(5):
        records.append(_assistant_tool_use(f"aq{i}", "AskUserQuestion", {}))
        records.append(_user_tool_result(f"aq{i}", f"ruling number {i}"))
    path = _write_transcript(tmp_path, records)

    result = build_digest(path)

    assert "ruling number 0" not in result
    assert "ruling number 1" not in result
    assert "ruling number 2" in result
    assert "ruling number 3" in result
    assert "ruling number 4" in result


def test_tool_result_for_unrelated_tool_is_not_a_ruling(tmp_path: Path) -> None:
    records = [
        _user("do work"),
        _assistant_tool_use("tc1", "mcp__headroom__Search", {"query": "x"}),
        _user_tool_result("tc1", "just a search result, not a ruling"),
    ]
    path = _write_transcript(tmp_path, records)

    result = build_digest(path)

    assert "just a search result, not a ruling" not in result


# ---------------------------------------------------------------------------
# (c) Last status.
# ---------------------------------------------------------------------------


def test_last_long_assistant_text_block_is_the_status(tmp_path: Path) -> None:
    records = [
        _user("do the big refactor"),
        _assistant_text("ok"),  # too short, ignored
        _assistant_text(
            "I refactored the http client to retry on 5xx with backoff and added tests."
        ),
    ]
    path = _write_transcript(tmp_path, records)

    result = build_digest(path)

    assert "Last status" in result
    assert "I refactored the http client" in result


def test_short_assistant_text_is_not_a_status(tmp_path: Path) -> None:
    records = [
        _user("do a real task that is long enough to be a real prompt"),
        _assistant_text("ok"),
    ]
    path = _write_transcript(tmp_path, records)

    result = build_digest(path)

    assert "Last status" not in result


# ---------------------------------------------------------------------------
# max_chars.
# ---------------------------------------------------------------------------


def test_max_chars_is_respected(tmp_path: Path) -> None:
    records = [_user("x " * 5000)]
    path = _write_transcript(tmp_path, records)

    result = build_digest(path, max_chars=200)

    assert len(result) <= 200


# ---------------------------------------------------------------------------
# precompact_context.
# ---------------------------------------------------------------------------


def test_precompact_context_includes_digest_and_instruction() -> None:
    result = precompact_context("Open task\n- do the thing")

    assert "do the thing" in result
    assert "verbatim" in result.lower()
    assert "retrieve" in result.lower()


def test_precompact_context_with_empty_digest_still_has_instruction() -> None:
    result = precompact_context("")

    assert result != ""
    assert "verbatim" in result.lower()


# ---------------------------------------------------------------------------
# save_digest / load_digest round trip.
# ---------------------------------------------------------------------------


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "workspace" / "code_tools"

    path = save_digest("session-123", "Open task\n- do the thing", root)

    assert path == root / "digests" / "session-123.md"
    assert load_digest("session-123", root) == "Open task\n- do the thing"


def test_load_digest_returns_none_when_missing(tmp_path: Path) -> None:
    root = tmp_path / "workspace" / "code_tools"

    assert load_digest("no-such-session", root) is None


def test_save_digest_sanitizes_session_id(tmp_path: Path) -> None:
    """A session_id with path separators can never escape root/digests/.

    The rendered filename may still contain literal dots (from the `..`
    tokens), but with every `/` replaced there is no separator left for
    them to act as a parent-directory reference -- the file lands inside
    `root/digests/` as a single path component, not outside it.
    """
    root = tmp_path / "workspace" / "code_tools"

    path = save_digest("../../etc/passwd", "hi", root)

    assert path.parent == root / "digests"
    assert "/" not in path.name
    assert path.resolve().parent == (root / "digests").resolve()
