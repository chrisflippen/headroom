"""Tests for the code agent switch: turning it on/off, and the uninstall path.

These call only the public functions in `headroom.cli.code_agent` — never its
private helpers — and the wrap CLI's public `main` entry point for the wiring
tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest
from click.testing import CliRunner

from headroom import paths
from headroom.cli import code_agent
from headroom.code_tools import connections

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
        "permissions": {"allow": ["Bash(git *)"], "deny": ["Bash(rm *)"]},
    }
    settings_path.write_text(json.dumps(original, indent=2) + "\n")

    code_agent.ensure_agent_switch(settings_path)

    payload = json.loads(settings_path.read_text())
    assert payload["hooks"] == original["hooks"]
    assert payload["env"] == original["env"]
    # permissions.allow is the one key ensure_agent_switch touches (it grants
    # the code agent's own tools) -- the existing rule stays first, the new
    # ones are appended after it, nothing is reordered or dropped.
    assert payload["permissions"]["deny"] == ["Bash(rm *)"]
    assert payload["permissions"]["allow"] == [
        "Bash(git *)",
        "mcp__headroom__Search",
        "mcp__headroom__Edit",
        "mcp__headroom__Sql",
        "mcp__headroom__headroom_compress",
        "mcp__headroom__headroom_retrieve",
        "mcp__headroom__headroom_stats",
        "mcp__headroom__SendMessage",
    ]


# ---------------------------------------------------------------------------
# ensure_agent_switch: permissions.allow rules for the code agent's own tools
# ---------------------------------------------------------------------------

_EXPECTED_ALLOW_RULES = [
    "mcp__headroom__Search",
    "mcp__headroom__Edit",
    "mcp__headroom__Sql",
    "mcp__headroom__headroom_compress",
    "mcp__headroom__headroom_retrieve",
    "mcp__headroom__headroom_stats",
    "mcp__headroom__SendMessage",
]


def test_ensure_agent_switch_adds_permissions_key_when_absent(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"

    code_agent.ensure_agent_switch(settings_path)

    payload = json.loads(settings_path.read_text())
    assert payload["permissions"]["allow"] == _EXPECTED_ALLOW_RULES
    assert payload["_headroom_managed"]["permissions_added"] == _EXPECTED_ALLOW_RULES


def test_ensure_agent_switch_adds_the_allow_rules_only_once(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    code_agent.ensure_agent_switch(settings_path)
    first_bytes = settings_path.read_bytes()

    changed = code_agent.ensure_agent_switch(settings_path)

    assert changed is False
    assert settings_path.read_bytes() == first_bytes


def test_ensure_agent_switch_does_not_duplicate_a_pre_existing_allow_rule(
    tmp_path: Path,
) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps({"permissions": {"allow": ["mcp__headroom__Search"]}}) + "\n"
    )

    code_agent.ensure_agent_switch(settings_path)

    payload = json.loads(settings_path.read_text())
    assert payload["permissions"]["allow"] == _EXPECTED_ALLOW_RULES
    # Only the five rules that were not already there were newly added.
    assert payload["_headroom_managed"]["permissions_added"] == [
        "mcp__headroom__Edit",
        "mcp__headroom__Sql",
        "mcp__headroom__headroom_compress",
        "mcp__headroom__headroom_retrieve",
        "mcp__headroom__headroom_stats",
        "mcp__headroom__SendMessage",
    ]


def test_ensure_agent_switch_takes_over_a_user_set_agent(tmp_path: Path) -> None:
    # Ruling (Christopher, 2026-09-05): a stale agent from an uninstalled
    # plugin (his settings held "woz:code-free") must not block the switch.
    # Headroom always takes over, and remembers the prior value so it can be
    # restored later.
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"agent": "woz:code-free"}) + "\n")

    changed = code_agent.ensure_agent_switch(settings_path)

    assert changed is True
    payload = json.loads(settings_path.read_text())
    assert payload["agent"] == "headroom-code-agent:code"
    assert payload["_headroom_managed"]["agent"]["previous"] == "woz:code-free"


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

    assert code_agent.agent_switch_state(settings_path) == "on (7 allow rules)"


def test_agent_switch_state_reports_user_set_value(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"agent": "my-plugin:custom"}) + "\n")

    assert code_agent.agent_switch_state(settings_path) == "user-set:my-plugin:custom"


def test_agent_switch_state_reports_the_taken_over_value(tmp_path: Path) -> None:
    # Ruling (Christopher, 2026-09-05): once the switch has taken over a
    # user-set agent, status shows what it replaced.
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"agent": "woz:code-free"}) + "\n")
    code_agent.ensure_agent_switch(settings_path)

    assert code_agent.agent_switch_state(settings_path) == "on (was: woz:code-free; 7 allow rules)"


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


def test_remove_agent_switch_removes_exactly_the_added_allow_rules(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    code_agent.ensure_agent_switch(settings_path)

    code_agent.remove_agent_switch(settings_path)

    payload = json.loads(settings_path.read_text())
    # Every rule it added is gone, and it created the permissions key from
    # nothing, so the whole key is gone too rather than left as `{}`.
    assert "permissions" not in payload


def test_remove_agent_switch_leaves_a_pre_existing_user_allow_rule_alone(
    tmp_path: Path,
) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps({"permissions": {"allow": ["mcp__headroom__Search"]}}) + "\n"
    )
    code_agent.ensure_agent_switch(settings_path)

    code_agent.remove_agent_switch(settings_path)

    payload = json.loads(settings_path.read_text())
    assert payload["permissions"]["allow"] == ["mcp__headroom__Search"]


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


def test_remove_agent_switch_restores_a_taken_over_user_set_agent(tmp_path: Path) -> None:
    # Ruling (Christopher, 2026-09-05): removing the switch after it took
    # over a stale user-set agent restores that original value.
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"agent": "woz:code-free"}) + "\n")
    code_agent.ensure_agent_switch(settings_path)

    changed = code_agent.remove_agent_switch(settings_path)

    assert changed is True
    payload = json.loads(settings_path.read_text())
    assert payload["agent"] == "woz:code-free"
    assert "_headroom_managed" not in payload


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
# launch_plan
# ---------------------------------------------------------------------------


def test_launch_plan_prepends_default_agent_when_none_given(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    code_agent.ensure_agent_switch(settings_path)

    plan = code_agent.launch_plan([], tmp_path, settings_path)

    assert plan.args == ("--agent", "headroom-code-agent:code")
    assert plan.warning is None


def test_launch_plan_respects_explicit_agent_flag(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    code_agent.ensure_agent_switch(settings_path)

    plan = code_agent.launch_plan(["--agent", "my-plugin:custom"], tmp_path, settings_path)

    assert plan.args == ("--agent", "my-plugin:custom")
    assert plan.warning is None


def test_launch_plan_warns_on_conflicting_project_agent_override(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    code_agent.ensure_agent_switch(settings_path)
    project_dir = tmp_path / "project"
    (project_dir / ".claude").mkdir(parents=True)
    (project_dir / ".claude" / "settings.json").write_text(json.dumps({"agent": "team:custom"}))

    plan = code_agent.launch_plan([], project_dir, settings_path)

    assert plan.args == ("--agent", "headroom-code-agent:code")
    assert plan.warning == (
        "Project sets agent=team:custom (this overrides the Headroom code agent switch)."
    )


def test_launch_plan_no_warning_when_project_agrees_with_default(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    code_agent.ensure_agent_switch(settings_path)
    project_dir = tmp_path / "project"
    (project_dir / ".claude").mkdir(parents=True)
    (project_dir / ".claude" / "settings.json").write_text(
        json.dumps({"agent": "headroom-code-agent:code"})
    )

    plan = code_agent.launch_plan([], project_dir, settings_path)

    assert plan.warning is None


def test_launch_plan_always_uses_the_headroom_agent(tmp_path: Path) -> None:
    # Ruling (Christopher, 2026-09-05): the switch always takes over, so
    # launch_plan always injects DEFAULT_AGENT regardless of what is (or
    # isn't) already in the settings file -- unless the caller passed
    # --agent explicitly, covered by a separate test.
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"agent": "my-plugin:custom"}) + "\n")

    plan = code_agent.launch_plan([], tmp_path, settings_path)

    assert plan.args == ("--agent", code_agent.DEFAULT_AGENT)
    assert plan.warning is None


# ---------------------------------------------------------------------------
# install_plugin / remove_plugin / marketplace_source
# ---------------------------------------------------------------------------


def test_install_plugin_runs_expected_sequence() -> None:
    calls: list[list[str]] = []

    code_agent.install_plugin(calls.append, "/some/plugins/dir")

    assert calls == [
        ["claude", "plugin", "marketplace", "add", "/some/plugins/dir"],
        [
            "claude",
            "plugin",
            "install",
            "headroom-code-agent@headroom-code-agent-marketplace",
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
            "headroom-code-agent@headroom-code-agent-marketplace",
            "--scope",
            "user",
        ],
        ["claude", "plugin", "marketplace", "remove", "headroom-code-agent-marketplace"],
    ]


def test_marketplace_source_prefers_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADROOM_MARKETPLACE_SOURCE", "custom/source")

    assert code_agent.marketplace_source() == "custom/source"


def test_marketplace_source_defaults_to_the_installed_package_plugins_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Fix 1: the plugin ships inside the installed `headroom` package
    # (maturin includes everything under `headroom/`), so the marketplace
    # source defaults to the `plugins` dir next to the installed package --
    # never a fork or a separate local git checkout.
    monkeypatch.delenv("HEADROOM_MARKETPLACE_SOURCE", raising=False)
    import headroom

    expected = str(Path(headroom.__file__).resolve().parent / "plugins")

    assert code_agent.marketplace_source() == expected


# ---------------------------------------------------------------------------
# installed_plugins_path / plugin_installed / ensure_plugin_installed
# ---------------------------------------------------------------------------


def test_installed_plugins_path_sits_next_to_settings_file(tmp_path: Path) -> None:
    settings_path = tmp_path / ".claude" / "settings.json"

    result = code_agent.installed_plugins_path(settings_path)

    assert result == tmp_path / ".claude" / "plugins" / "installed_plugins.json"


def test_plugin_installed_false_when_registry_file_is_missing(tmp_path: Path) -> None:
    registry_path = tmp_path / "installed_plugins.json"

    assert code_agent.plugin_installed(registry_path) is False


def test_plugin_installed_false_when_plugin_key_is_absent(tmp_path: Path) -> None:
    registry_path = tmp_path / "installed_plugins.json"
    registry_path.write_text(json.dumps({"version": 2, "plugins": {"other@marketplace": [{}]}}))

    assert code_agent.plugin_installed(registry_path) is False


def test_plugin_installed_false_when_plugin_entry_is_an_empty_list(tmp_path: Path) -> None:
    registry_path = tmp_path / "installed_plugins.json"
    registry_path.write_text(
        json.dumps(
            {"version": 2, "plugins": {"headroom-code-agent@headroom-code-agent-marketplace": []}}
        )
    )

    assert code_agent.plugin_installed(registry_path) is False


def test_plugin_installed_true_when_plugin_entry_is_present(tmp_path: Path) -> None:
    registry_path = tmp_path / "installed_plugins.json"
    registry_path.write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    "headroom-code-agent@headroom-code-agent-marketplace": [
                        {"scope": "user", "version": "1.0.0"}
                    ]
                },
            }
        )
    )

    assert code_agent.plugin_installed(registry_path) is True


def test_ensure_plugin_installed_skips_install_when_probe_says_present() -> None:
    calls: list[list[str]] = []

    code_agent.ensure_plugin_installed(calls.append, "chrisflippen/headroom", lambda: True)

    assert calls == []


def test_ensure_plugin_installed_installs_when_probe_says_missing() -> None:
    calls: list[list[str]] = []

    code_agent.ensure_plugin_installed(calls.append, "/some/plugins/dir", lambda: False)

    assert calls == [
        ["claude", "plugin", "marketplace", "add", "/some/plugins/dir"],
        [
            "claude",
            "plugin",
            "install",
            "headroom-code-agent@headroom-code-agent-marketplace",
            "--scope",
            "user",
        ],
    ]


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
            "headroom-code-agent@headroom-code-agent-marketplace",
            "--scope",
            "user",
        ],
        ["claude", "plugin", "marketplace", "remove", "headroom-code-agent-marketplace"],
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
    assert calls[0][:4] == ["claude", "plugin", "marketplace", "add"]


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
# Click commands: `headroom code-agent db add / remove / list`
# ---------------------------------------------------------------------------


@pytest.fixture
def _connections_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Isolate the connections config file from the filesystem's real one."""

    monkeypatch.setenv(paths.HEADROOM_CONFIG_DIR_ENV, str(tmp_path))
    return tmp_path


@pytest.fixture
def db_keychain(monkeypatch: pytest.MonkeyPatch) -> connections.MemoryKeychain:
    """A MemoryKeychain injected in place of the real macOS keychain."""

    store = connections.MemoryKeychain()
    monkeypatch.setattr(code_agent, "_default_keychain", lambda: store)
    return store


def test_code_agent_db_add_prints_only_the_name_and_kind(
    runner: CliRunner,
    _connections_config_dir: Path,
    db_keychain: connections.MemoryKeychain,
) -> None:
    from headroom.cli.main import main

    result = runner.invoke(
        main, ["code-agent", "db", "add", "warehouse", "postgres://user:hunter2@host/db"]
    )

    assert result.exit_code == 0, result.output
    assert "Added connection 'warehouse' (postgres)" in result.output
    assert "hunter2" not in result.output
    assert connections.list_connections() == ["warehouse"]
    assert db_keychain.get_secret(connections.KEYCHAIN_SERVICE, "warehouse") == (
        "postgres://user:hunter2@host/db"
    )


def test_code_agent_db_add_reports_updated_for_an_existing_name(
    runner: CliRunner,
    _connections_config_dir: Path,
    db_keychain: connections.MemoryKeychain,
) -> None:
    from headroom.cli.main import main

    connections.add_connection("warehouse", "sqlite:///first.db", db_keychain)

    result = runner.invoke(main, ["code-agent", "db", "add", "warehouse", "sqlite:///second.db"])

    assert result.exit_code == 0, result.output
    assert "Updated connection 'warehouse' (sqlite)" in result.output


def test_code_agent_db_add_rejects_an_unsupported_scheme(
    runner: CliRunner,
    _connections_config_dir: Path,
    db_keychain: connections.MemoryKeychain,
) -> None:
    from headroom.cli.main import main

    result = runner.invoke(main, ["code-agent", "db", "add", "warehouse", "mysql://host/db"])

    assert result.exit_code != 0
    assert connections.list_connections() == []


def test_code_agent_db_remove_removes_a_known_connection(
    runner: CliRunner,
    _connections_config_dir: Path,
    db_keychain: connections.MemoryKeychain,
) -> None:
    from headroom.cli.main import main

    connections.add_connection("warehouse", "sqlite:///first.db", db_keychain)

    result = runner.invoke(main, ["code-agent", "db", "remove", "warehouse"])

    assert result.exit_code == 0, result.output
    assert "Removed connection 'warehouse'" in result.output
    assert connections.list_connections() == []


def test_code_agent_db_remove_reports_an_unknown_name(
    runner: CliRunner,
    _connections_config_dir: Path,
    db_keychain: connections.MemoryKeychain,
) -> None:
    from headroom.cli.main import main

    result = runner.invoke(main, ["code-agent", "db", "remove", "nope"])

    assert result.exit_code == 0, result.output
    assert "No connection reference named 'nope'" in result.output


def test_code_agent_db_list_reports_no_connections(
    runner: CliRunner, _connections_config_dir: Path
) -> None:
    from headroom.cli.main import main

    result = runner.invoke(main, ["code-agent", "db", "list"])

    assert result.exit_code == 0, result.output
    assert "No connections are configured." in result.output


def test_code_agent_db_list_reports_configured_names(
    runner: CliRunner,
    _connections_config_dir: Path,
    db_keychain: connections.MemoryKeychain,
) -> None:
    from headroom.cli.main import main

    connections.add_connection("warehouse", "sqlite:///first.db", db_keychain)
    connections.add_connection("reporting", "sqlite:///second.db", db_keychain)

    result = runner.invoke(main, ["code-agent", "db", "list"])

    assert result.exit_code == 0, result.output
    assert "reporting" in result.output
    assert "warehouse" in result.output


# ---------------------------------------------------------------------------
# wrap claude wiring: --agent is injected by default, --no-code-agent skips it
# ---------------------------------------------------------------------------


class _Completed:
    returncode = 0


def _patch_wrap_claude_scaffolding(
    monkeypatch: pytest.MonkeyPatch, wrap_mod: ModuleType
) -> dict[str, object]:
    captured: dict[str, object] = {}
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

    # The plugin is already installed by default, so the ordinary wiring
    # tests never see an install attempt. Tests that care about the install
    # path override `plugin_installed` themselves and read this list.
    plugin_runner_calls: list[list[str]] = []
    monkeypatch.setattr(code_agent, "_claude_runner", plugin_runner_calls.append)
    monkeypatch.setattr(code_agent, "plugin_installed", lambda *_a, **_k: True)
    captured["plugin_runner_calls"] = plugin_runner_calls
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


def test_wrap_claude_installs_plugin_when_probe_says_missing(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from headroom.cli import wrap as wrap_mod
    from headroom.cli.main import main

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.chdir(tmp_path)
    _clear_claude_mode_env(monkeypatch)
    captured = _patch_wrap_claude_scaffolding(monkeypatch, wrap_mod)
    monkeypatch.setattr(code_agent, "plugin_installed", lambda *_a, **_k: False)
    monkeypatch.setattr(code_agent, "marketplace_source", lambda: "/some/plugins/dir")

    result = runner.invoke(
        main, ["wrap", "claude", "--no-mcp", "--no-tokensave", "--no-serena"], env={}
    )

    assert result.exit_code == 0, result.output
    assert captured["plugin_runner_calls"] == [
        ["claude", "plugin", "marketplace", "add", "/some/plugins/dir"],
        [
            "claude",
            "plugin",
            "install",
            "headroom-code-agent@headroom-code-agent-marketplace",
            "--scope",
            "user",
        ],
    ]


def test_wrap_claude_skips_install_when_probe_says_present(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from headroom.cli import wrap as wrap_mod
    from headroom.cli.main import main

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.chdir(tmp_path)
    _clear_claude_mode_env(monkeypatch)
    captured = _patch_wrap_claude_scaffolding(monkeypatch, wrap_mod)
    monkeypatch.setattr(code_agent, "plugin_installed", lambda *_a, **_k: True)

    result = runner.invoke(
        main, ["wrap", "claude", "--no-mcp", "--no-tokensave", "--no-serena"], env={}
    )

    assert result.exit_code == 0, result.output
    assert captured["plugin_runner_calls"] == []


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


def test_ensure_adds_missing_allow_rules_when_the_switch_is_already_on(tmp_path: Path) -> None:
    """A switch written before the permission grant existed still gets the rules."""
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "agent": code_agent.DEFAULT_AGENT,
                "_headroom_managed": {"agent": {"previous": "woz:code-free"}},
                "permissions": {"allow": ["Bash(git *)"]},
            }
        ),
        encoding="utf-8",
    )

    assert code_agent.ensure_agent_switch(settings) is True
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["permissions"]["allow"][0] == "Bash(git *)"
    assert set(code_agent.CODE_AGENT_ALLOW_RULES) <= set(data["permissions"]["allow"])
    assert data["_headroom_managed"]["permissions_added"] == list(code_agent.CODE_AGENT_ALLOW_RULES)
    assert data["_headroom_managed"]["agent"] == {"previous": "woz:code-free"}
    assert code_agent.ensure_agent_switch(settings) is False
