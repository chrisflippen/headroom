"""Tests for headroom.code_tools.brief -- the short brief shown under the

user's prompt before the code agent acts. Calls only the public
`should_brief` and `make_brief` entry functions, plus the `headroom
code-agent brief` CLI command. A fake gatherer and a fake model runner
stand in for the real ones -- these tests never call the real `claude`
binary or open a real memory database.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from headroom.code_tools import brief as brief_module
from headroom.code_tools.brief import GatheredContext, make_brief, should_brief


def test_slash_command_is_not_briefed() -> None:
    assert should_brief("/compact") is False


def test_plain_reply_is_not_briefed() -> None:
    assert should_brief("yes.") is False


def test_short_prompt_is_not_briefed() -> None:
    nine_words = "fix the bug in the login form on page"
    assert len(nine_words.split()) == 9
    assert should_brief(nine_words) is False


def test_long_task_prompt_is_briefed() -> None:
    fourteen_words = "add a retry with backoff to the http client so flaky network calls fail"
    assert len(fourteen_words.split()) == 14
    assert should_brief(fourteen_words) is True


_TASK_PROMPT = "add a retry with backoff to the http client so flaky network calls fail"

_EMPTY_CONTEXT = GatheredContext(memories=[], glossary=[], likely_files=[])


def _fake_gather(prompt: str, cwd: str) -> GatheredContext:
    return _EMPTY_CONTEXT


def test_make_brief_returns_model_text_when_trigger_passes() -> None:
    def fake_model_runner(system: str, user: str, timeout: float) -> str:
        return "Goal: add retries.\nNot the goal: rewrite the client.\nLikely files: none obvious.\nSkills to run: none."

    result = make_brief(
        _TASK_PROMPT, cwd="/repo", gather=_fake_gather, model_runner=fake_model_runner
    )

    assert result == (
        "Goal: add retries.\nNot the goal: rewrite the client.\n"
        "Likely files: none obvious.\nSkills to run: none."
    )


def test_make_brief_returns_none_and_skips_model_runner_when_trigger_fails() -> None:
    calls: list[tuple[str, str, float]] = []

    def fake_model_runner(system: str, user: str, timeout: float) -> str:
        calls.append((system, user, timeout))
        return "should not run"

    result = make_brief("yes.", cwd="/repo", gather=_fake_gather, model_runner=fake_model_runner)

    assert result is None
    assert calls == []


def test_model_runner_receives_glossary_and_memories_in_user_text() -> None:
    context = GatheredContext(
        memories=["Christopher prefers pytest over unittest."],
        glossary=[("Brief", "The short interpretation shown under the prompt.")],
        likely_files=["headroom/code_tools/brief.py"],
    )

    def fake_gather(prompt: str, cwd: str) -> GatheredContext:
        return context

    captured: dict[str, str] = {}

    def fake_model_runner(system: str, user: str, timeout: float) -> str:
        captured["user"] = user
        return "Goal: x.\nNot the goal: y.\nLikely files: z.\nSkills to run: none."

    make_brief(_TASK_PROMPT, cwd="/repo", gather=fake_gather, model_runner=fake_model_runner)

    assert "Brief: The short interpretation shown under the prompt." in captured["user"]
    assert "Christopher prefers pytest over unittest." in captured["user"]
    assert "headroom/code_tools/brief.py" in captured["user"]


def test_make_brief_times_out_returns_none() -> None:
    def slow_model_runner(system: str, user: str, timeout: float) -> str:
        time.sleep(0.5)
        return "too slow"

    result = make_brief(
        _TASK_PROMPT,
        cwd="/repo",
        gather=_fake_gather,
        model_runner=slow_model_runner,
        budget_seconds=0.05,
    )

    assert result is None


def test_make_brief_returns_none_when_model_runner_raises() -> None:
    def broken_model_runner(system: str, user: str, timeout: float) -> str:
        raise RuntimeError("model call failed")

    result = make_brief(
        _TASK_PROMPT, cwd="/repo", gather=_fake_gather, model_runner=broken_model_runner
    )

    assert result is None


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def test_cli_brief_prints_additional_context_for_a_qualifying_prompt(
    cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from headroom.cli.main import main

    monkeypatch.setattr(brief_module, "gather", _fake_gather)
    monkeypatch.setattr(
        brief_module,
        "default_model_runner",
        lambda system, user, timeout: (
            "Goal: add retries.\nNot the goal: none.\n"
            "Likely files: none obvious.\nSkills to run: none."
        ),
    )

    stdin = json.dumps({"prompt": _TASK_PROMPT, "cwd": "/repo", "session_id": "abc"})
    result = cli_runner.invoke(main, ["code-agent", "brief"], input=stdin)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload == {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "Goal: add retries.\nNot the goal: none.\n"
            "Likely files: none obvious.\nSkills to run: none.",
        }
    }


def test_cli_brief_prints_nothing_for_a_slash_command(
    cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from headroom.cli.main import main

    def _unexpected_call(*args: object, **kwargs: object) -> str:
        raise AssertionError("model runner must not run for a slash command")

    monkeypatch.setattr(brief_module, "gather", _fake_gather)
    monkeypatch.setattr(brief_module, "default_model_runner", _unexpected_call)

    stdin = json.dumps({"prompt": "/compact", "cwd": "/repo", "session_id": "abc"})
    result = cli_runner.invoke(main, ["code-agent", "brief"], input=stdin)

    assert result.exit_code == 0
    assert result.output == ""


def test_cli_brief_recursion_guard_prints_nothing(
    cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from headroom.cli.main import main

    def _unexpected_call(*args: object, **kwargs: object) -> str:
        raise AssertionError("must not run while the recursion guard is set")

    monkeypatch.setattr(brief_module, "gather", _unexpected_call)
    monkeypatch.setattr(brief_module, "default_model_runner", _unexpected_call)
    monkeypatch.setenv(brief_module.RECURSION_GUARD_ENV, "1")

    stdin = json.dumps({"prompt": _TASK_PROMPT, "cwd": "/repo", "session_id": "abc"})
    result = cli_runner.invoke(main, ["code-agent", "brief"], input=stdin)

    assert result.exit_code == 0
    assert result.output == ""


def test_gather_names_files_by_whole_name_not_by_prose_fragments(tmp_path: Path) -> None:
    """A code-like word matches a folder or file stem; a prose word never matches
    a file just because its letters appear inside a longer path."""
    from headroom._subprocess import run as _run

    root = tmp_path / "repo"
    (root / "headroom" / "code_tools").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "headroom" / "code_tools" / "sql.py").write_text("x = 1\n", encoding="utf-8")
    (root / ".github" / "workflows" / "ci.yml").write_text("on: push\n", encoding="utf-8")
    _run(["git", "-C", str(root), "init", "-q"], capture_output=True, text=True, timeout=10)

    found = brief_module.gather(
        "Let the code_tools Sql tool work when the database is busy, then retry it", str(root)
    )

    assert found.likely_files == ["headroom/code_tools/sql.py"]


def test_gather_puts_files_named_outright_before_folder_matches(tmp_path: Path) -> None:
    from headroom._subprocess import run as _run

    root = tmp_path / "repo"
    (root / "headroom" / "code_tools").mkdir(parents=True)
    for name in ("__init__.py", "brief.py", "sql.py"):
        (root / "headroom" / "code_tools" / name).write_text("x = 1\n", encoding="utf-8")
    _run(["git", "-C", str(root), "init", "-q"], capture_output=True, text=True, timeout=10)

    found = brief_module.gather("Add a retry to the Sql tool in code_tools", str(root))

    assert found.likely_files[0] == "headroom/code_tools/sql.py"
    assert set(found.likely_files) == {
        "headroom/code_tools/sql.py",
        "headroom/code_tools/__init__.py",
        "headroom/code_tools/brief.py",
    }
