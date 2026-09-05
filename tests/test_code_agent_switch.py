"""Tests for the code agent switch: turning it on/off, and the uninstall path.

These call only the public functions in `headroom.cli.code_agent` — never its
private helpers — and the wrap CLI's public `main` entry point for the wiring
tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from headroom.cli import code_agent

# ---------------------------------------------------------------------------
# ensure_agent_switch
# ---------------------------------------------------------------------------


def test_ensure_agent_switch_writes_agent_and_marker_on_fresh_file(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"

    changed = code_agent.ensure_agent_switch(settings_path)

    assert changed is True
    payload = json.loads(settings_path.read_text())
    assert payload["agent"] == "headroom-code-agent:code"
    assert payload["_headroom_managed"]["agent"]["previous"] is None


def test_ensure_agent_switch_second_call_is_a_no_op(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    code_agent.ensure_agent_switch(settings_path)

    changed = code_agent.ensure_agent_switch(settings_path)

    assert changed is False


def test_ensure_agent_switch_preserves_unrelated_keys(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    original = {
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"command": "echo hi"}]}]},
        "env": {"SOME_KEY": "some-value"},
        "permissions": {"allow": ["Bash(git *)"]},
    }
    settings_path.write_text(json.dumps(original, indent=2) + "\n")

    code_agent.ensure_agent_switch(settings_path)

    payload = json.loads(settings_path.read_text())
    for key, value in original.items():
        assert payload[key] == value


def test_ensure_agent_switch_does_not_overwrite_user_set_agent(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"agent": "my-plugin:custom"}) + "\n")

    changed = code_agent.ensure_agent_switch(settings_path)

    assert changed is False
    payload = json.loads(settings_path.read_text())
    assert payload == {"agent": "my-plugin:custom"}


def test_ensure_agent_switch_custom_agent_name(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"

    code_agent.ensure_agent_switch(settings_path, agent="other-plugin:other")

    payload = json.loads(settings_path.read_text())
    assert payload["agent"] == "other-plugin:other"


# ---------------------------------------------------------------------------
# agent_switch_state
# ---------------------------------------------------------------------------


def test_agent_switch_state_off_when_no_file(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"

    assert code_agent.agent_switch_state(settings_path) == "off"


def test_agent_switch_state_on_after_ensure(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    code_agent.ensure_agent_switch(settings_path)

    assert code_agent.agent_switch_state(settings_path) == "on"


def test_agent_switch_state_reports_user_set_value(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"agent": "my-plugin:custom"}) + "\n")

    assert code_agent.agent_switch_state(settings_path) == "user-set:my-plugin:custom"


# ---------------------------------------------------------------------------
# remove_agent_switch
# ---------------------------------------------------------------------------


def test_remove_agent_switch_removes_managed_entry(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    code_agent.ensure_agent_switch(settings_path)

    changed = code_agent.remove_agent_switch(settings_path)

    assert changed is True
    payload = json.loads(settings_path.read_text())
    assert "agent" not in payload
    assert "_headroom_managed" not in payload


def test_remove_agent_switch_restores_previous_value(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"agent": "earlier:agent"}) + "\n")
    # Headroom takes over an already-managed entry (e.g. re-running `on` with a
    # new agent name) — the prior managed value becomes the restore point.
    code_agent.ensure_agent_switch(settings_path, agent="earlier:agent")
    code_agent.ensure_agent_switch(settings_path, agent="new:agent")

    code_agent.remove_agent_switch(settings_path)

    payload = json.loads(settings_path.read_text())
    assert payload["agent"] == "earlier:agent"


def test_remove_agent_switch_leaves_user_set_value_alone(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"agent": "my-plugin:custom"}) + "\n")

    changed = code_agent.remove_agent_switch(settings_path)

    assert changed is False
    payload = json.loads(settings_path.read_text())
    assert payload == {"agent": "my-plugin:custom"}


def test_remove_agent_switch_no_op_on_fresh_file(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"

    assert code_agent.remove_agent_switch(settings_path) is False
    assert not settings_path.exists()


# ---------------------------------------------------------------------------
# project_agent_override
# ---------------------------------------------------------------------------


def test_project_agent_override_none_when_absent(tmp_path: Path) -> None:
    assert code_agent.project_agent_override(tmp_path) is None


def test_project_agent_override_reads_settings_json(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps({"agent": "team:agent"}) + "\n")

    assert code_agent.project_agent_override(tmp_path) == "team:agent"


def test_project_agent_override_reads_settings_local_json(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.local.json").write_text(json.dumps({"agent": "local:agent"}) + "\n")

    assert code_agent.project_agent_override(tmp_path) == "local:agent"


def test_project_agent_override_settings_json_wins_over_local(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps({"agent": "shared:agent"}) + "\n")
    (claude_dir / "settings.local.json").write_text(json.dumps({"agent": "local:agent"}) + "\n")

    assert code_agent.project_agent_override(tmp_path) == "shared:agent"


def test_project_agent_override_never_writes(tmp_path: Path) -> None:
    code_agent.project_agent_override(tmp_path)

    assert not (tmp_path / ".claude").exists()


# ---------------------------------------------------------------------------
# agent_launch_args
# ---------------------------------------------------------------------------


def test_agent_launch_args_default() -> None:
    assert code_agent.agent_launch_args("headroom-code-agent:code") == [
        "--agent",
        "headroom-code-agent:code",
    ]


def test_agent_launch_args_custom_name() -> None:
    assert code_agent.agent_launch_args("other-plugin:other") == ["--agent", "other-plugin:other"]


# ---------------------------------------------------------------------------
# install_plugin / remove_plugin / marketplace_source
# ---------------------------------------------------------------------------


def test_install_plugin_runs_expected_sequence() -> None:
    calls: list[list[str]] = []

    code_agent.install_plugin(calls.append, "chrisflippen/headroom")

    assert calls == [
        ["claude", "plugin", "marketplace", "remove", "headroom-marketplace"],
        ["claude", "plugin", "marketplace", "add", "chrisflippen/headroom"],
        [
            "claude",
            "plugin",
            "install",
            "headroom-code-agent@headroom-marketplace",
            "--scope",
            "user",
        ],
    ]


def test_remove_plugin_runs_expected_command() -> None:
    calls: list[list[str]] = []

    code_agent.remove_plugin(calls.append)

    assert calls == [
        [
            "claude",
            "plugin",
            "uninstall",
            "headroom-code-agent@headroom-marketplace",
            "--scope",
            "user",
        ],
    ]


def test_marketplace_source_prefers_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADROOM_MARKETPLACE_SOURCE", "custom/source")

    assert code_agent.marketplace_source() == "custom/source"


def test_marketplace_source_defaults_to_fork(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEADROOM_MARKETPLACE_SOURCE", raising=False)
    monkeypatch.setattr(code_agent, "_local_checkout_source", lambda: None)

    assert code_agent.marketplace_source() == "chrisflippen/headroom"


def test_marketplace_source_prefers_local_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEADROOM_MARKETPLACE_SOURCE", raising=False)
    monkeypatch.setattr(code_agent, "_local_checkout_source", lambda: "/some/repo/checkout")

    assert code_agent.marketplace_source() == "/some/repo/checkout"


# ---------------------------------------------------------------------------
# remove_all
# ---------------------------------------------------------------------------


def test_remove_all_removes_switch_plugin_and_tool_state(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    code_agent.ensure_agent_switch(settings_path)
    workspace_dir = tmp_path / "workspace"
    code_tools_dir = workspace_dir / "code_tools"
    code_tools_dir.mkdir(parents=True)
    (code_tools_dir / "skills_ensure.json").write_text("{}")
    calls: list[list[str]] = []

    removed = code_agent.remove_all(settings_path, calls.append, workspace_dir)

    assert removed == ["agent switch", "plugin", "tool state"]
    assert "agent" not in json.loads(settings_path.read_text())
    assert not code_tools_dir.exists()
    assert calls == [
        [
            "claude",
            "plugin",
            "uninstall",
            "headroom-code-agent@headroom-marketplace",
            "--scope",
            "user",
        ],
    ]


def test_remove_all_on_a_workspace_with_nothing_to_remove(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    workspace_dir = tmp_path / "workspace"
    calls: list[list[str]] = []

    removed = code_agent.remove_all(settings_path, calls.append, workspace_dir)

    assert removed == ["plugin"]
    assert not workspace_dir.exists()


def test_remove_all_never_touches_other_workspace_contents(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    workspace_dir = tmp_path / "workspace"
    other_dir = workspace_dir / "ledger"
    other_dir.mkdir(parents=True)
    (other_dir / "events.jsonl").write_text("kept")
    code_tools_dir = workspace_dir / "code_tools"
    code_tools_dir.mkdir()

    code_agent.remove_all(settings_path, lambda _argv: None, workspace_dir)

    assert not code_tools_dir.exists()
    assert (other_dir / "events.jsonl").read_text() == "kept"


# ---------------------------------------------------------------------------
# Click commands: headroom code-agent on/off/remove/status
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_code_agent_on_writes_switch_and_installs_plugin(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from headroom.cli.main import main

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    calls: list[list[str]] = []
    monkeypatch.setattr(code_agent, "_claude_runner", calls.append)

    result = runner.invoke(main, ["code-agent", "on"])

    assert result.exit_code == 0, result.output
    payload = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert payload["agent"] == "headroom-code-agent:code"
    assert calls[0][:4] == ["claude", "plugin", "marketplace", "remove"]


def test_code_agent_off_removes_switch(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from headroom.cli.main import main

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    settings_path = tmp_path / ".claude" / "settings.json"
    code_agent.ensure_agent_switch(settings_path)

    result = runner.invoke(main, ["code-agent", "off"])

    assert result.exit_code == 0, result.output
    assert "agent" not in json.loads(settings_path.read_text())


def test_code_agent_status_reports_state_and_project_override(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from headroom.cli.main import main

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    project_dir = tmp_path / "project"
    (project_dir / ".claude").mkdir(parents=True)
    (project_dir / ".claude" / "settings.json").write_text(
        json.dumps({"agent": "team:agent"}) + "\n"
    )
    monkeypatch.chdir(project_dir)

    result = runner.invoke(main, ["code-agent", "status"])

    assert result.exit_code == 0, result.output
    assert "off" in result.output
    assert "team:agent" in result.output


def test_code_agent_remove_reports_what_it_removed(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from headroom.cli.main import main

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.setenv("HEADROOM_WORKSPACE_DIR", str(tmp_path / "workspace"))
    settings_path = tmp_path / ".claude" / "settings.json"
    code_agent.ensure_agent_switch(settings_path)
    monkeypatch.setattr(code_agent, "_claude_runner", lambda _argv: None)

    result = runner.invoke(main, ["code-agent", "remove"])

    assert result.exit_code == 0, result.output
    assert "agent switch" in result.output
    assert "plugin" in result.output


# ---------------------------------------------------------------------------
# wrap claude wiring: --agent is injected by default, --no-code-agent skips it
# ---------------------------------------------------------------------------


class _Completed:
    returncode = 0


def _patch_wrap_claude_scaffolding(monkeypatch: pytest.MonkeyPatch, wrap_mod) -> dict:
    captured: dict = {}
    monkeypatch.setattr(wrap_mod.shutil, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(wrap_mod, "_register_proxy_client", lambda _port: None)
    monkeypatch.setattr(wrap_mod, "_make_cleanup", lambda _holder, _port: lambda: None)
    monkeypatch.setattr(wrap_mod.signal, "signal", lambda *_a, **_k: None)
    monkeypatch.setattr(wrap_mod, "_push_runtime_env", lambda *_a, **_k: None)
    monkeypatch.setattr(wrap_mod, "_setup_coding_compressor", lambda *_a, **_k: None)
    monkeypatch.setattr(wrap_mod, "_write_claude_wrap_base_url", lambda *_a, **_k: None)
    monkeypatch.setattr(wrap_mod, "_restore_claude_wrap_base_url", lambda *_a, **_k: None)
    monkeypatch.setattr(wrap_mod, "_write_claude_wrap_tool_search", lambda *_a, **_k: None)
    monkeypatch.setattr(wrap_mod, "_restore_claude_wrap_tool_search", lambda *_a, **_k: None)
    monkeypatch.setattr(wrap_mod, "_print_telemetry_notice", lambda: None)
    monkeypatch.setattr(wrap_mod, "_ensure_claude_wrap_selfheal_hook", lambda *_a, **_k: None)
    monkeypatch.setattr(wrap_mod, "detect_claude_code_version", lambda *_a, **_k: (2, 1, 261))

    def fake_ensure_proxy(*args: object, **kwargs: object) -> tuple[None, int]:
        port = args[0] if args else 8787
        return None, int(port) if isinstance(port, int) else 8787

    monkeypatch.setattr(wrap_mod, "_ensure_proxy", fake_ensure_proxy)

    def fake_run(cmd: list[str], *, env: dict[str, str]) -> _Completed:
        captured["child_cmd"] = cmd
        return _Completed()

    monkeypatch.setattr(wrap_mod.subprocess, "run", fake_run)
    return captured


def _clear_claude_mode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_VERTEX_BASE_URL",
        "ANTHROPIC_FOUNDRY_BASE_URL",
        "ANTHROPIC_FOUNDRY_RESOURCE",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "VERTEX_TARGET_API_URL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "ENABLE_TOOL_SEARCH",
    ):
        monkeypatch.delenv(key, raising=False)


def test_wrap_claude_injects_agent_flag_by_default(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from headroom.cli import wrap as wrap_mod
    from headroom.cli.main import main

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.chdir(tmp_path)
    _clear_claude_mode_env(monkeypatch)
    captured = _patch_wrap_claude_scaffolding(monkeypatch, wrap_mod)

    result = runner.invoke(
        main, ["wrap", "claude", "--no-mcp", "--no-tokensave", "--no-serena"], env={}
    )

    assert result.exit_code == 0, result.output
    assert captured["child_cmd"] == [
        "/usr/bin/claude",
        "--agent",
        "headroom-code-agent:code",
    ]
    payload = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert payload["agent"] == "headroom-code-agent:code"


def test_wrap_claude_no_code_agent_flag_skips_injection(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from headroom.cli import wrap as wrap_mod
    from headroom.cli.main import main

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.chdir(tmp_path)
    _clear_claude_mode_env(monkeypatch)
    captured = _patch_wrap_claude_scaffolding(monkeypatch, wrap_mod)

    result = runner.invoke(
        main,
        ["wrap", "claude", "--no-mcp", "--no-tokensave", "--no-serena", "--no-code-agent"],
        env={},
    )

    assert result.exit_code == 0, result.output
    assert captured["child_cmd"] == ["/usr/bin/claude"]
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_wrap_claude_respects_explicit_agent_flag(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from headroom.cli import wrap as wrap_mod
    from headroom.cli.main import main

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.chdir(tmp_path)
    _clear_claude_mode_env(monkeypatch)
    captured = _patch_wrap_claude_scaffolding(monkeypatch, wrap_mod)

    result = runner.invoke(
        main,
        [
            "wrap",
            "claude",
            "--no-mcp",
            "--no-tokensave",
            "--no-serena",
            "--",
            "--agent",
            "my-plugin:custom",
        ],
        env={},
    )

    assert result.exit_code == 0, result.output
    assert captured["child_cmd"] == ["/usr/bin/claude", "--agent", "my-plugin:custom"]


def test_wrap_claude_warns_on_conflicting_project_agent_override(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from headroom.cli import wrap as wrap_mod
    from headroom.cli.main import main

    # User settings and the project's .claude/settings.json must be distinct
    # files here, or writing a project override would also be read back as
    # the (already-agreeing) user switch and the conflict this test exists
    # to check for would never occur.
    home_dir = tmp_path / "home"
    project_dir = tmp_path / "project"
    home_dir.mkdir()
    project_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home_dir / ".claude"))
    monkeypatch.chdir(project_dir)
    _clear_claude_mode_env(monkeypatch)
    captured = _patch_wrap_claude_scaffolding(monkeypatch, wrap_mod)

    project_settings = project_dir / ".claude" / "settings.json"
    project_settings.parent.mkdir(parents=True, exist_ok=True)
    project_settings.write_text(json.dumps({"agent": "team:custom"}))

    result = runner.invoke(
        main, ["wrap", "claude", "--no-mcp", "--no-tokensave", "--no-serena"], env={}
    )

    assert result.exit_code == 0, result.output
    assert "team:custom" in result.output
    assert "overrides the Headroom code agent switch" in result.output
    # The project file itself is never rewritten.
    assert json.loads(project_settings.read_text()) == {"agent": "team:custom"}
    # The user settings switch is still written...
    user_settings = json.loads((home_dir / ".claude" / "settings.json").read_text())
    assert user_settings["agent"] == "headroom-code-agent:code"
    # ...and the launch args still carry the injected --agent: only Claude
    # Code's own settings precedence (not wrap) makes the project's `agent`
    # key win at runtime, so wrap's job here is only to warn, never rewrite.
    assert captured["child_cmd"] == [
        "/usr/bin/claude",
        "--agent",
        "headroom-code-agent:code",
    ]


def test_unwrap_claude_removes_agent_switch(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from headroom.cli import wrap as wrap_mod
    from headroom.cli.main import main

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    settings_path = tmp_path / ".claude" / "settings.json"
    code_agent.ensure_agent_switch(settings_path)
    monkeypatch.setattr(wrap_mod, "_stop_local_proxy_for_unwrap", lambda *_a, **_k: "not-running")
    monkeypatch.setattr(wrap_mod, "_warn_if_proxy_env_leaked", lambda *_a, **_k: None)
    monkeypatch.setattr(wrap_mod, "_remove_claude_managed_hooks", lambda *_a, **_k: False)

    result = runner.invoke(main, ["unwrap", "claude", "--no-stop-proxy", "--keep-mcp"])

    assert result.exit_code == 0, result.output
    assert "agent" not in json.loads(settings_path.read_text())


# ---------------------------------------------------------------------------
# _skills_ensure_runner
# ---------------------------------------------------------------------------


def test_skills_ensure_runner_caps_the_subprocess_timeout_at_25_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv: list[str], **kwargs: object) -> _FakeResult:
        captured.update(kwargs)
        return _FakeResult()

    monkeypatch.setattr(code_agent, "run", fake_run)

    code_agent._skills_ensure_runner(["npx", "skills", "update", "-g", "-y"])

    assert captured["timeout"] == 25
