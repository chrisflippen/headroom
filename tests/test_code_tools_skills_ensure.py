"""Tests for headroom.code_tools.skills_ensure -- keeping the code agent's

skills and Claude Code plugins current, at most once a day. Calls only the
public `ensure` entry function.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from headroom.code_tools import skills_ensure


@pytest.fixture
def lock_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A fake $HOME with a skills.sh lock file the tests can pre-populate."""
    monkeypatch.setenv("HOME", str(tmp_path))
    lock_path = tmp_path / ".agents" / ".skill-lock.json"
    lock_path.parent.mkdir(parents=True)
    return lock_path


def _write_lock(lock_path: Path, skill_names: list[str]) -> None:
    payload = {
        "version": 1,
        "skills": {name: {"source": "some/repo"} for name in skill_names},
        "dismissed": [],
        "lastSelectedAgents": [],
    }
    lock_path.write_text(json.dumps(payload), encoding="utf-8")


def test_first_run_updates_skills_and_each_plugin(lock_file: Path, tmp_path: Path) -> None:
    _write_lock(lock_file, ["code-review"])
    calls: list[list[str]] = []
    state_path = tmp_path / "state" / "skills_ensure.json"

    result = skills_ensure.ensure(
        [{"name": "code-review", "source": "mattpocock/skills@code-review"}],
        ["headroom-code-agent@headroom-marketplace", "headroom@headroom-marketplace"],
        now=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
        runner=calls.append,
        state_path=state_path,
    )

    assert result.ran is True
    assert result.skipped_reason is None
    assert result.failures == []
    assert result.commands == [
        ["npx", "skills", "update", "-g", "-y"],
        [
            "claude",
            "plugin",
            "update",
            "headroom-code-agent@headroom-marketplace",
            "--scope",
            "user",
            "-y",
        ],
        [
            "claude",
            "plugin",
            "update",
            "headroom@headroom-marketplace",
            "--scope",
            "user",
            "-y",
        ],
    ]
    # Plugin updates run concurrently, so the raw call order the runner
    # observed need not match `result.commands`' declared order -- only
    # that the same set of commands ran.
    assert sorted(calls) == sorted(result.commands)


def test_missing_skill_is_added_before_the_update(lock_file: Path, tmp_path: Path) -> None:
    _write_lock(lock_file, ["code-review"])  # domain-modeling is not in the lock
    calls: list[list[str]] = []
    state_path = tmp_path / "state" / "skills_ensure.json"

    result = skills_ensure.ensure(
        [
            {"name": "code-review", "source": "mattpocock/skills@code-review"},
            {"name": "domain-modeling", "source": "mattpocock/skills@domain-modeling"},
        ],
        [],
        now=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
        runner=calls.append,
        state_path=state_path,
    )

    assert result.commands == [
        ["npx", "skills", "add", "mattpocock/skills@domain-modeling", "-g", "-y"],
        ["npx", "skills", "update", "-g", "-y"],
    ]


def test_multiple_missing_skills_are_batched_into_one_add_command(
    lock_file: Path, tmp_path: Path
) -> None:
    _write_lock(lock_file, [])  # nothing installed yet
    calls: list[list[str]] = []
    state_path = tmp_path / "state" / "skills_ensure.json"

    result = skills_ensure.ensure(
        [
            {"name": "code-review", "source": "mattpocock/skills@code-review"},
            {"name": "domain-modeling", "source": "mattpocock/skills@domain-modeling"},
            {"name": "grill-with-docs", "source": "mattpocock/skills@grill-with-docs"},
        ],
        [],
        now=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
        runner=calls.append,
        state_path=state_path,
    )

    assert result.commands == [
        [
            "npx",
            "skills",
            "add",
            "mattpocock/skills@code-review",
            "mattpocock/skills@domain-modeling",
            "mattpocock/skills@grill-with-docs",
            "-g",
            "-y",
        ],
        ["npx", "skills", "update", "-g", "-y"],
    ]
    assert calls == result.commands


def test_plugin_updates_run_concurrently(lock_file: Path, tmp_path: Path) -> None:
    _write_lock(lock_file, ["code-review"])
    state_path = tmp_path / "state" / "skills_ensure.json"

    def slow_runner(argv: list[str]) -> None:
        if argv[:2] == ["claude", "plugin"]:
            time.sleep(0.2)

    start = time.perf_counter()
    result = skills_ensure.ensure(
        [{"name": "code-review", "source": "mattpocock/skills@code-review"}],
        ["plugin-a@marketplace", "plugin-b@marketplace", "plugin-c@marketplace"],
        now=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
        runner=slow_runner,
        state_path=state_path,
    )
    elapsed = time.perf_counter() - start

    assert result.ran is True
    assert elapsed < 0.5, "three 0.2s plugin updates must overlap, not run one after another"


def test_plugin_command_order_is_preserved_despite_concurrent_completion(
    lock_file: Path, tmp_path: Path
) -> None:
    _write_lock(lock_file, ["code-review"])
    state_path = tmp_path / "state" / "skills_ensure.json"

    def variable_speed_runner(argv: list[str]) -> None:
        if argv[:2] == ["claude", "plugin"] and argv[3] == "first@marketplace":
            time.sleep(0.1)  # finishes last even though it was issued first

    result = skills_ensure.ensure(
        [{"name": "code-review", "source": "mattpocock/skills@code-review"}],
        ["first@marketplace", "second@marketplace", "third@marketplace"],
        now=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
        runner=variable_speed_runner,
        state_path=state_path,
    )

    plugins_in_order = [cmd[3] for cmd in result.commands if cmd[:2] == ["claude", "plugin"]]
    assert plugins_in_order == ["first@marketplace", "second@marketplace", "third@marketplace"]


def test_second_run_inside_interval_does_nothing(lock_file: Path, tmp_path: Path) -> None:
    _write_lock(lock_file, ["code-review"])
    calls: list[list[str]] = []
    state_path = tmp_path / "state" / "skills_ensure.json"
    skills = [{"name": "code-review", "source": "mattpocock/skills@code-review"}]
    first_run = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    skills_ensure.ensure(skills, [], now=first_run, runner=calls.append, state_path=state_path)
    calls.clear()

    result = skills_ensure.ensure(
        skills,
        [],
        now=first_run + timedelta(hours=2),
        runner=calls.append,
        state_path=state_path,
    )

    assert result.ran is False
    assert result.commands == []
    assert result.failures == []
    assert result.skipped_reason == "ran 2 hours ago"
    assert calls == []


def test_runner_failure_is_recorded_but_other_commands_still_run(
    lock_file: Path, tmp_path: Path
) -> None:
    _write_lock(lock_file, ["code-review"])
    calls: list[list[str]] = []

    def failing_runner(argv: list[str]) -> None:
        calls.append(argv)
        if argv[:2] == ["npx", "skills"]:
            raise RuntimeError("network unreachable")

    state_path = tmp_path / "state" / "skills_ensure.json"

    result = skills_ensure.ensure(
        [{"name": "code-review", "source": "mattpocock/skills@code-review"}],
        ["headroom@headroom-marketplace"],
        now=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
        runner=failing_runner,
        state_path=state_path,
    )

    assert result.failures == [
        "npx skills update -g -y: network unreachable",
    ]
    assert calls == [
        ["npx", "skills", "update", "-g", "-y"],
        ["claude", "plugin", "update", "headroom@headroom-marketplace", "--scope", "user", "-y"],
    ]
    assert state_path.exists()


def test_corrupt_state_file_is_treated_as_never_ran(lock_file: Path, tmp_path: Path) -> None:
    _write_lock(lock_file, ["code-review"])
    calls: list[list[str]] = []
    state_path = tmp_path / "state" / "skills_ensure.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{not valid json", encoding="utf-8")

    result = skills_ensure.ensure(
        [{"name": "code-review", "source": "mattpocock/skills@code-review"}],
        [],
        now=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
        runner=calls.append,
        state_path=state_path,
    )

    assert result.ran is True
    assert result.skipped_reason is None
    assert calls == [["npx", "skills", "update", "-g", "-y"]]


def test_load_configured_skills_and_plugins_falls_back_to_defaults_when_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HEADROOM_WORKSPACE_DIR", str(tmp_path))

    skills, plugins = skills_ensure.load_configured_skills_and_plugins()

    assert skills == [dict(skill) for skill in skills_ensure.DEFAULT_SKILLS]
    assert plugins == list(skills_ensure.DEFAULT_PLUGINS)


def test_load_configured_skills_and_plugins_reads_settings_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HEADROOM_WORKSPACE_DIR", str(tmp_path))
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "code_agent.skills": [{"name": "tdd", "source": "some/repo@tdd"}],
                "code_agent.plugins": ["only-plugin@only-marketplace"],
            }
        ),
        encoding="utf-8",
    )

    skills, plugins = skills_ensure.load_configured_skills_and_plugins()

    assert skills == [{"name": "tdd", "source": "some/repo@tdd"}]
    assert plugins == ["only-plugin@only-marketplace"]


# ---------------------------------------------------------------------------
# Click command: headroom code-agent skills-ensure
# ---------------------------------------------------------------------------


def test_skills_ensure_command_exits_zero_even_when_the_runner_fails(
    lock_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from click.testing import CliRunner

    from headroom.cli import code_agent
    from headroom.cli.main import main

    _write_lock(lock_file, [])
    monkeypatch.setenv("HEADROOM_WORKSPACE_DIR", str(tmp_path / "workspace"))

    def failing_runner(argv: list[str]) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(code_agent, "_skills_ensure_runner", failing_runner)

    result = CliRunner().invoke(main, ["code-agent", "skills-ensure"])

    assert result.exit_code == 0, result.output
