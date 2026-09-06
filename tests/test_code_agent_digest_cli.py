"""Tests for the `headroom code-agent digest-save` / `digest-inject` CLI

commands -- the `PreCompact` and `SessionStart` (matcher `"compact"`) hooks
that save a digest of the open task before compaction and hand it back
after. Both commands must never fail a hook: bad/missing stdin always
exits 0 with no output.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from headroom.cli.main import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    workspace_dir = tmp_path / "workspace"
    monkeypatch.setenv("HEADROOM_WORKSPACE_DIR", str(workspace_dir))
    return workspace_dir


# ---------------------------------------------------------------------------
# digest-save
# ---------------------------------------------------------------------------


def test_digest_save_writes_digest_and_prints_precompact_context(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_dir = _workspace(monkeypatch, tmp_path)
    transcript_path = tmp_path / "transcript.jsonl"
    transcript_path.write_text(
        json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": "add retry logic to the http client please",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    stdin = json.dumps(
        {
            "session_id": "abc-123",
            "transcript_path": str(transcript_path),
            "cwd": str(tmp_path),
            "trigger": "auto",
        }
    )
    result = runner.invoke(main, ["code-agent", "digest-save"], input=stdin)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["hookSpecificOutput"]["hookEventName"] == "PreCompact"
    assert (
        "add retry logic to the http client please"
        in payload["hookSpecificOutput"]["additionalContext"]
    )

    saved = workspace_dir / "code_tools" / "digests" / "abc-123.md"
    assert saved.exists()
    assert "add retry logic to the http client please" in saved.read_text()


def test_digest_save_prints_nothing_when_transcript_has_nothing_useful(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _workspace(monkeypatch, tmp_path)
    transcript_path = tmp_path / "transcript.jsonl"
    transcript_path.write_text("", encoding="utf-8")

    stdin = json.dumps(
        {"session_id": "abc-123", "transcript_path": str(transcript_path), "trigger": "manual"}
    )
    result = runner.invoke(main, ["code-agent", "digest-save"], input=stdin)

    assert result.exit_code == 0
    assert result.output == ""


def test_digest_save_exits_zero_on_invalid_json(runner: CliRunner) -> None:
    result = runner.invoke(main, ["code-agent", "digest-save"], input="not json")

    assert result.exit_code == 0
    assert result.output == ""


def test_digest_save_exits_zero_on_missing_fields(runner: CliRunner) -> None:
    result = runner.invoke(main, ["code-agent", "digest-save"], input=json.dumps({}))

    assert result.exit_code == 0
    assert result.output == ""


def test_digest_save_exits_zero_when_transcript_missing(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _workspace(monkeypatch, tmp_path)

    stdin = json.dumps(
        {
            "session_id": "abc-123",
            "transcript_path": str(tmp_path / "does-not-exist.jsonl"),
        }
    )
    result = runner.invoke(main, ["code-agent", "digest-save"], input=stdin)

    assert result.exit_code == 0
    assert result.output == ""


# ---------------------------------------------------------------------------
# digest-inject
# ---------------------------------------------------------------------------


def test_digest_inject_prints_sessionstart_context_when_digest_exists(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from headroom.code_tools.digest import save_digest

    workspace_dir = _workspace(monkeypatch, tmp_path)
    save_digest("abc-123", "Open task\n- do the thing", workspace_dir / "code_tools")

    stdin = json.dumps({"session_id": "abc-123", "source": "compact", "cwd": str(tmp_path)})
    result = runner.invoke(main, ["code-agent", "digest-inject"], input=stdin)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("Context restored after compaction:")
    assert "do the thing" in context


def test_digest_inject_prints_nothing_when_no_digest_saved(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _workspace(monkeypatch, tmp_path)

    stdin = json.dumps({"session_id": "no-such-session", "source": "compact"})
    result = runner.invoke(main, ["code-agent", "digest-inject"], input=stdin)

    assert result.exit_code == 0
    assert result.output == ""


def test_digest_inject_exits_zero_on_invalid_json(runner: CliRunner) -> None:
    result = runner.invoke(main, ["code-agent", "digest-inject"], input="not json")

    assert result.exit_code == 0
    assert result.output == ""


def test_digest_inject_exits_zero_on_missing_session_id(runner: CliRunner) -> None:
    result = runner.invoke(
        main, ["code-agent", "digest-inject"], input=json.dumps({"source": "compact"})
    )

    assert result.exit_code == 0
    assert result.output == ""


# ---------------------------------------------------------------------------
# hooks.json -- the two new entries this slice adds.
# ---------------------------------------------------------------------------

_HOOKS_JSON_PATH = (
    Path(__file__).resolve().parent.parent
    / "headroom"
    / "plugins"
    / "headroom-code-agent"
    / "hooks"
    / "hooks.json"
)


def test_hooks_json_parses() -> None:
    payload = json.loads(_HOOKS_JSON_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)


def test_hooks_json_has_precompact_running_digest_save() -> None:
    payload = json.loads(_HOOKS_JSON_PATH.read_text(encoding="utf-8"))
    precompact = payload["hooks"]["PreCompact"]
    commands = [hook["command"] for entry in precompact for hook in entry["hooks"]]
    assert "headroom code-agent digest-save" in commands


def test_hooks_json_has_compact_session_start_running_digest_inject() -> None:
    payload = json.loads(_HOOKS_JSON_PATH.read_text(encoding="utf-8"))
    session_start = payload["hooks"]["SessionStart"]
    compact_entries = [entry for entry in session_start if entry.get("matcher") == "compact"]
    assert compact_entries
    commands = [hook["command"] for entry in compact_entries for hook in entry["hooks"]]
    assert "headroom code-agent digest-inject" in commands


def test_hooks_json_still_has_startup_resume_session_start() -> None:
    """The pre-existing skills-ensure SessionStart entry must survive this slice."""
    payload = json.loads(_HOOKS_JSON_PATH.read_text(encoding="utf-8"))
    session_start = payload["hooks"]["SessionStart"]
    startup_entries = [entry for entry in session_start if entry.get("matcher") == "startup|resume"]
    assert startup_entries
    commands = [hook["command"] for entry in startup_entries for hook in entry["hooks"]]
    assert "headroom code-agent skills-ensure" in commands
