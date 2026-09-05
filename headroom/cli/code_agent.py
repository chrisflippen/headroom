"""Turn Headroom's code agent on or off, and its install/uninstall path.

The code agent is Headroom's own Claude Code agent: the main model reaches
files and databases only through headroom's Search, Edit, and Sql tools,
never the built-in Read, Edit, Write, Grep, or Glob. This module holds:

* the agent switch — the "agent" key in Claude Code's user settings.json
  that makes the code agent the default for a session, plus the managed
  marker that lets `off`/`remove` tell "headroom wrote this" from "the user
  set this by hand" and only ever touch the former;
* the plugin install/uninstall commands, built as plain argv lists so a
  test can record them instead of running the real `claude` binary;
* the `headroom code-agent` command group wiring those together.

The functions here are pure where the name says pure: they take paths and
values in, return a result, and the only I/O is the settings file read/write
that is the whole point of the function. Nothing here shells out to `claude`
directly — that always goes through an injected runner.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

import headroom
from headroom import fsutil
from headroom._subprocess import run
from headroom.code_tools import brief, plugin_registry, skills_ensure
from headroom.code_tools.connections import (
    Keychain,
    MacOSKeychain,
    add_connection,
    kind_from_url,
    list_connections,
    remove_connection,
)
from headroom.code_tools.post_edit_check import hook_main, real_runner
from headroom.providers.claude.vscode import claude_user_settings_path

from .main import main

# The full name Claude Code expects for a plugin agent: "<plugin>:<agent>".
DEFAULT_AGENT = "headroom-code-agent:code"

_MARKETPLACE_NAME = "headroom-code-agent-marketplace"
_PLUGIN_NAME = "headroom-code-agent"
_MANAGED_KEY = "_headroom_managed"
_AGENT_KEY = "agent"
_PERMISSIONS_KEY = "permissions"
_ALLOW_KEY = "allow"
_PERMISSIONS_ADDED_KEY = "permissions_added"

# Ruling (Christopher, 2026-09-05): the agent switch also grants the code
# agent's own tools in `permissions.allow`, since `permissions.defaultMode =
# acceptEdits` (Christopher's setting) does not cover MCP tools -- without
# this every one of these calls prompts for permission. Each group below is
# a managed entry: `ensure_agent_switch` adds only the rules not already
# there and records exactly those under `_headroom_managed.permissions_added`,
# so `off`/`remove` take out only what headroom itself added and leave a
# rule alone if the user already had it.
CODE_AGENT_ALLOW_RULES: tuple[str, ...] = (
    # The code agent's own file, edit, and database tools on the headroom
    # MCP server -- what the agent uses instead of the built-in Read, Edit,
    # Write, Grep, and Glob.
    "mcp__headroom__Search",
    "mcp__headroom__Edit",
    "mcp__headroom__Sql",
    # Headroom's own compression tools, on the same MCP server: compress
    # content on demand, retrieve original content behind a "[Retrieve
    # more: hash=...]" marker, and read session compression stats.
    "mcp__headroom__headroom_compress",
    "mcp__headroom__headroom_retrieve",
    "mcp__headroom__headroom_stats",
    # No memory rule: for Claude Code, `memory_search`/`memory_save` are
    # injected straight into each request by the proxy (see
    # `headroom.proxy.memory_tool_adapter`), not registered as an MCP
    # server -- `~/.claude.json` only ever gets a "headroom" MCP entry
    # (`_setup_headroom_mcp` in `headroom/cli/wrap.py`), and
    # `_inject_memory_mcp_config` only ever writes Codex's `config.toml`.
    # There is no `mcp__headroom_memory__*` tool name for Claude Code to
    # prompt on, so there is nothing to add here.
)

# A runner takes one `claude ...` argv list and does something with it (run
# it for real, or record it in a test). Pure functions build the argv and
# never call `claude` themselves.
Runner = Callable[[list[str]], None]


# ---------------------------------------------------------------------------
# Settings file I/O — same atomic-write technique as headroom.fsutil, used
# everywhere else headroom rewrites a Claude Code settings file.
# ---------------------------------------------------------------------------


def _read_settings(settings_path: Path) -> dict[str, Any]:
    """Read a settings file as a dict, treating "missing" and "empty" as {}."""
    if not settings_path.exists():
        return {}
    raw = fsutil.read_text(settings_path)
    if not raw.strip():
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise click.ClickException(f"{settings_path} does not contain a JSON object.")
    return payload


def _write_settings(settings_path: Path, payload: dict[str, Any]) -> None:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    fsutil.write_text(settings_path, json.dumps(payload, indent=2) + "\n")


def _managed_agent_entry(payload: dict[str, Any]) -> dict[str, Any] | None:
    managed = payload.get(_MANAGED_KEY)
    if not isinstance(managed, dict):
        return None
    entry = managed.get(_AGENT_KEY)
    return entry if isinstance(entry, dict) else None


def _add_allow_rules(payload: dict[str, Any]) -> list[str]:
    """Add any of CODE_AGENT_ALLOW_RULES missing from permissions.allow.

    Returns only the rules this call actually added -- never a rule that
    was already there, whether the user set it or a previous run of this
    same function did. Existing entries are never reordered or dropped.
    """
    permissions = payload.get(_PERMISSIONS_KEY)
    if not isinstance(permissions, dict):
        permissions = {}
    allow = permissions.get(_ALLOW_KEY)
    if not isinstance(allow, list):
        allow = []

    added: list[str] = []
    for rule in CODE_AGENT_ALLOW_RULES:
        if rule not in allow:
            allow.append(rule)
            added.append(rule)

    permissions[_ALLOW_KEY] = allow
    payload[_PERMISSIONS_KEY] = permissions
    return added


def _remove_allow_rules(payload: dict[str, Any], rules: list[str]) -> None:
    """Remove exactly `rules` from permissions.allow, dropping empty keys.

    `rules` is what `_add_allow_rules` recorded as added -- a rule the user
    already had (so never recorded) is never touched here.
    """
    if not rules:
        return
    permissions = payload.get(_PERMISSIONS_KEY)
    if not isinstance(permissions, dict):
        return
    allow = permissions.get(_ALLOW_KEY)
    if not isinstance(allow, list):
        return

    remaining = [rule for rule in allow if rule not in rules]
    if remaining:
        permissions[_ALLOW_KEY] = remaining
    else:
        permissions.pop(_ALLOW_KEY, None)

    if permissions:
        payload[_PERMISSIONS_KEY] = permissions
    else:
        payload.pop(_PERMISSIONS_KEY, None)


# ---------------------------------------------------------------------------
# 1-2. The agent switch itself.
# ---------------------------------------------------------------------------


def ensure_agent_switch(settings_path: Path, agent: str = DEFAULT_AGENT) -> bool:
    """Set `agent` in the user settings file, marking it as headroom-managed.

    Returns True when the file changed. Ruling (Christopher, 2026-09-05):
    headroom always takes over the agent switch, even when the user's
    settings already hold a different `agent` value that headroom did not
    write (for example a stale value left behind by an uninstalled plugin).
    The prior value is remembered in the managed marker's `previous` field
    so `remove_agent_switch` can restore it later.

    Also grants CODE_AGENT_ALLOW_RULES in `permissions.allow`, so the code
    agent's own tools never prompt for permission under
    `permissions.defaultMode = acceptEdits` (which does not cover MCP
    tools). Only the rules actually added are recorded, under
    `_headroom_managed.permissions_added`.
    """
    payload = _read_settings(settings_path)
    current = payload.get(_AGENT_KEY)
    managed = _managed_agent_entry(payload)

    managed_all = payload.get(_MANAGED_KEY)
    if not isinstance(managed_all, dict):
        managed_all = {}

    if current == agent and managed is not None:
        # Already on. A switch written before the permission grant existed
        # still needs the rules, so add any that are missing and stop there.
        added_rules = _add_allow_rules(payload)
        if not added_rules:
            return False
        recorded = managed_all.get(_PERMISSIONS_ADDED_KEY)
        recorded_list = recorded if isinstance(recorded, list) else []
        managed_all[_PERMISSIONS_ADDED_KEY] = recorded_list + added_rules
        payload[_MANAGED_KEY] = managed_all
        _write_settings(settings_path, payload)
        return True

    previous = managed.get("previous") if managed is not None else current
    payload[_AGENT_KEY] = agent
    added_rules = _add_allow_rules(payload)
    managed_all[_AGENT_KEY] = {"previous": previous}
    managed_all[_PERMISSIONS_ADDED_KEY] = added_rules
    payload[_MANAGED_KEY] = managed_all

    _write_settings(settings_path, payload)
    return True


def remove_agent_switch(settings_path: Path) -> bool:
    """Remove the managed `agent` entry, restoring the value it replaced.

    A user-set `agent` value (one headroom never wrote) is left alone. Also
    removes exactly the `permissions.allow` rules `ensure_agent_switch`
    recorded as its own -- a rule the user already had before is untouched.
    """
    payload = _read_settings(settings_path)
    managed = _managed_agent_entry(payload)
    if managed is None:
        return False

    previous = managed.get("previous")
    if previous is None:
        payload.pop(_AGENT_KEY, None)
    else:
        payload[_AGENT_KEY] = previous

    managed_all = payload.get(_MANAGED_KEY)
    added_rules = managed_all.get(_PERMISSIONS_ADDED_KEY) if isinstance(managed_all, dict) else None
    _remove_allow_rules(payload, added_rules if isinstance(added_rules, list) else [])

    if isinstance(managed_all, dict):
        managed_all.pop(_AGENT_KEY, None)
        managed_all.pop(_PERMISSIONS_ADDED_KEY, None)
        if managed_all:
            payload[_MANAGED_KEY] = managed_all
        else:
            payload.pop(_MANAGED_KEY, None)

    _write_settings(settings_path, payload)
    return True


def agent_switch_state(settings_path: Path) -> str:
    """Return "off", "on (N allow rules)", or "on (was: X; N allow rules)".

    N is how many `permissions.allow` rules `ensure_agent_switch` added --
    see CODE_AGENT_ALLOW_RULES.
    """
    payload = _read_settings(settings_path)
    current = payload.get(_AGENT_KEY)
    if current is None:
        return "off"
    managed = _managed_agent_entry(payload)
    if managed is None:
        return f"user-set:{current}"

    managed_all = payload.get(_MANAGED_KEY)
    added_rules = managed_all.get(_PERMISSIONS_ADDED_KEY) if isinstance(managed_all, dict) else None
    rule_count = len(added_rules) if isinstance(added_rules, list) else 0
    plural = "" if rule_count == 1 else "s"
    rules_note = f"{rule_count} allow rule{plural}"

    previous = managed.get("previous")
    if previous is not None:
        return f"on (was: {previous}; {rules_note})"
    return f"on ({rules_note})"


# ---------------------------------------------------------------------------
# 3. Project-level override — read-only, never writes.
# ---------------------------------------------------------------------------


def project_agent_override(cwd: Path) -> str | None:
    """Return the `agent` a project's own settings set, if any.

    Checks `.claude/settings.json` before `.claude/settings.local.json`
    (settings.json takes precedence). Never writes either file.
    """
    for name in ("settings.json", "settings.local.json"):
        path = cwd / ".claude" / name
        if not path.exists():
            continue
        try:
            payload = json.loads(fsutil.read_text(path))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        value = payload.get(_AGENT_KEY)
        if isinstance(value, str) and value:
            return value
    return None


# ---------------------------------------------------------------------------
# 4. Launch args.
# ---------------------------------------------------------------------------


def agent_launch_args(agent: str) -> list[str]:
    """The `claude` CLI flags that select `agent` for the launched session."""
    return ["--agent", agent]


@dataclass(frozen=True)
class LaunchPlan:
    """What `wrap claude` should do about the code agent for one launch.

    `args` is the full `claude` argv to run (the caller's own args, with the
    `--agent` flag prepended unless the caller already passed one). `warning`
    is a message to print when a project's own settings override the agent
    switch, or None when there is nothing to say.
    """

    args: tuple[str, ...]
    warning: str | None


def launch_plan(claude_args: Sequence[str], cwd: Path, settings_path: Path) -> LaunchPlan:
    """Decide the `--agent` launch args and warning for one `wrap claude` run.

    Call this after `ensure_agent_switch(settings_path)` has already run.
    Ruling (Christopher, 2026-09-05): headroom's agent switch always takes
    over, so this always injects `DEFAULT_AGENT` unless the caller passed
    `--agent` explicitly on the command line. `settings_path` is accepted
    for interface stability (callers already pass it, and it may be used
    again if a future override needs it) but is not read here. A project's
    own `.claude/settings.json` or `settings.local.json` `agent` key is
    never rewritten — Claude Code's own settings precedence, not this
    decision, is what makes the project's value win at runtime, so this only
    warns about the conflict, never changes the args because of it.
    """
    warning = None
    project_agent = project_agent_override(cwd)
    if project_agent is not None and project_agent != DEFAULT_AGENT:
        warning = (
            f"Project sets agent={project_agent} (this overrides the Headroom code agent switch)."
        )

    args = tuple(claude_args)
    if "--agent" not in args:
        args = (*agent_launch_args(DEFAULT_AGENT), *args)

    return LaunchPlan(args=args, warning=warning)


# ---------------------------------------------------------------------------
# 5. Plugin install / uninstall — argv only, dispatched through `runner`.
# ---------------------------------------------------------------------------


def marketplace_source() -> str:
    """Where to add the code agent's own marketplace from.

    `HEADROOM_MARKETPLACE_SOURCE` wins if set (matches `headroom init`'s
    override, and lets a test or a local checkout point elsewhere).
    Otherwise this is the `plugins` directory shipped inside the installed
    `headroom` package itself — maturin ships everything under `headroom/`
    in the wheel, so the plugin travels with the binary and never depends on
    a separate git checkout or fork existing on the machine.
    """
    override = os.environ.get("HEADROOM_MARKETPLACE_SOURCE")
    if override:
        return override
    return str(Path(headroom.__file__).resolve().parent / "plugins")


def install_plugin(runner: Runner, source: str) -> None:
    """Add the code agent's own marketplace, then install the plugin from it.

    This only ever adds/uses `_MARKETPLACE_NAME` (the code agent's own
    shipped marketplace) — it never touches `headroom-marketplace`, which
    belongs to the separate hooks plugin and is managed by `headroom init`.
    """
    runner(["claude", "plugin", "marketplace", "add", source])
    runner(
        [
            "claude",
            "plugin",
            "install",
            f"{_PLUGIN_NAME}@{_MARKETPLACE_NAME}",
            "--scope",
            "user",
        ]
    )


def installed_plugins_path(settings_path: Path) -> Path:
    """Where Claude Code records installed plugins, next to `settings_path`.

    A thin wrapper over `plugin_registry.installed_plugins_path` -- kept as
    its own name here so callers and tests in this module don't need to know
    the JSON reading moved to a shared helper.
    """

    return plugin_registry.installed_plugins_path(settings_path)


def plugin_installed(
    installed_plugins_path: Path,
    plugin_key: str = f"{_PLUGIN_NAME}@{_MARKETPLACE_NAME}",
) -> bool:
    """True when Claude Code's own registry lists `plugin_key` as installed.

    A thin wrapper over `plugin_registry.plugin_installed`, which
    `headroom.code_tools.skills_ensure` also calls for its own plugins --
    the JSON reading lives there once instead of in both places.
    """

    return plugin_registry.plugin_installed(installed_plugins_path, plugin_key)


def ensure_plugin_installed(
    runner: Runner, source: str, installed_probe: Callable[[], bool]
) -> None:
    """Install the code agent plugin, unless `installed_probe` says it already is.

    `wrap claude` calls this on every launch (never a one-time `on`
    command), so it must stay cheap when the plugin is already there --
    that's what `installed_probe` is for: a caller passes in whichever
    check is worth paying for, and this function only calls `install_plugin`
    when that check says the plugin is missing.
    """

    if installed_probe():
        return
    install_plugin(runner, source)


def remove_plugin(runner: Runner) -> None:
    """Uninstall the code agent plugin, then remove only our own marketplace.

    Never touches `headroom-marketplace` — that belongs to the separate
    hooks plugin and is managed by `headroom init`.
    """
    runner(
        [
            "claude",
            "plugin",
            "uninstall",
            f"{_PLUGIN_NAME}@{_MARKETPLACE_NAME}",
            "--scope",
            "user",
        ]
    )
    runner(["claude", "plugin", "marketplace", "remove", _MARKETPLACE_NAME])


# Phrases that mean "already in the state the caller wanted" for any
# `claude` command -- never a real failure.
_TOLERATED_PHRASES = ("already installed", "already exists", "already added")

# Phrases that are only safe to tolerate for a removal command: there, "not
# found" genuinely means "already gone", which is what the caller wanted.
# For every other command (in particular `plugin install`), "not found" is
# the real, live bug this guards against -- e.g. "Plugin ... not found in
# marketplace" -- and must raise.
_TOLERATED_REMOVAL_PHRASES = ("not found", "no marketplace", "not installed")


def _is_removal_command(argv: list[str]) -> bool:
    return argv[1:4] == ["plugin", "marketplace", "remove"] or argv[1:3] == ["plugin", "uninstall"]


def _claude_runner(argv: list[str]) -> None:
    """Run a `claude ...` argv for real.

    A marketplace or plugin that is already in the requested state is not a
    failure — it is exactly what the caller wanted, so a non-zero exit whose
    message says so is tolerated rather than raised. That tolerance is
    narrow: "already installed/exists/added" is tolerated for any command,
    and "not found"/"no marketplace"/"not installed" is tolerated only for
    the removal commands (`plugin marketplace remove`, `plugin uninstall`)
    where "not found" means "already gone". Anything else — including
    `plugin install` reporting the plugin was "not found in marketplace" —
    raises with the full detail.
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise click.ClickException("'claude' not found in PATH. Install Claude Code first.")
    result = run([claude_bin, *argv[1:]], capture_output=True, text=True)
    if result.returncode == 0:
        return
    detail = "\n".join(part for part in (result.stderr.strip(), result.stdout.strip()) if part)
    lowered = detail.lower()
    tolerated = any(phrase in lowered for phrase in _TOLERATED_PHRASES)
    if not tolerated and _is_removal_command(argv):
        tolerated = any(phrase in lowered for phrase in _TOLERATED_REMOVAL_PHRASES)
    if tolerated:
        return
    raise click.ClickException(f"{' '.join(argv)} failed: {detail or result.returncode}")


# ---------------------------------------------------------------------------
# 6. Uninstall path.
# ---------------------------------------------------------------------------


def remove_all(settings_path: Path, runner: Runner, workspace_dir: Path) -> list[str]:
    """Undo everything the code agent switch installed.

    Removes the managed agent switch, uninstalls the plugin (always
    attempted — it is a no-op if it was never installed), and deletes only
    `workspace_dir / "code_tools"`, the tool state: never the rest of the
    workspace, and never a `.claude` folder. Returns what it removed.
    """
    removed: list[str] = []

    if remove_agent_switch(settings_path):
        removed.append("agent switch")

    remove_plugin(runner)
    removed.append("plugin")

    code_tools_dir = workspace_dir / "code_tools"
    if code_tools_dir.exists():
        shutil.rmtree(code_tools_dir)
        removed.append("tool state")

    return removed


# ---------------------------------------------------------------------------
# 8. Click commands: `headroom code-agent on / off / remove / status`.
# ---------------------------------------------------------------------------


@main.group("code-agent")
def code_agent_group() -> None:
    """Turn Headroom's Claude Code agent on or off, and manage its install."""


@code_agent_group.command("on")
def code_agent_on() -> None:
    """Make the code agent the default agent, and install its plugin."""
    settings_path = claude_user_settings_path()
    changed = ensure_agent_switch(settings_path)
    install_plugin(_claude_runner, marketplace_source())
    if changed:
        click.echo(f"  Agent switch on: agent={DEFAULT_AGENT}")
    else:
        click.echo(f"  Agent switch: {agent_switch_state(settings_path)}")


@code_agent_group.command("off")
def code_agent_off() -> None:
    """Turn off the code agent switch. Leaves the plugin installed."""
    settings_path = claude_user_settings_path()
    if remove_agent_switch(settings_path):
        click.echo("  Agent switch off.")
    else:
        click.echo("  Agent switch was not set by headroom; nothing to remove.")


@code_agent_group.command("remove")
def code_agent_remove() -> None:
    """Undo the agent switch, uninstall the plugin, and clear its tool state."""
    from headroom.paths import workspace_dir

    settings_path = claude_user_settings_path()
    removed = remove_all(settings_path, _claude_runner, workspace_dir())
    if removed:
        click.echo(f"  Removed: {', '.join(removed)}")
    else:
        click.echo("  Nothing to remove.")


@code_agent_group.command("status")
def code_agent_status() -> None:
    """Show whether the code agent switch is on, and any project override."""
    settings_path = claude_user_settings_path()
    click.echo(f"  Agent switch: {agent_switch_state(settings_path)}")
    override = project_agent_override(Path.cwd())
    if override is not None:
        click.echo(f"  Project override: agent={override}")


# ---------------------------------------------------------------------------
# 9. Click commands: `headroom code-agent db add / remove / list`.
# ---------------------------------------------------------------------------


def _default_keychain() -> Keychain:
    return MacOSKeychain()


@code_agent_group.group("db")
def code_agent_db_group() -> None:
    """Manage the Sql tool's connection references: a name, never a URL."""


@code_agent_db_group.command("add")
@click.argument("name")
@click.argument("url")
def code_agent_db_add(name: str, url: str) -> None:
    """Add or replace connection reference NAME, pointing at URL.

    The URL goes straight to the keychain; only the name and database kind
    are ever printed or written to the connections config file.
    """
    try:
        kind = kind_from_url(url)
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    verb = "Updated" if name in list_connections() else "Added"
    add_connection(name, url, _default_keychain())
    click.echo(f"  {verb} connection {name!r} ({kind}).")


@code_agent_db_group.command("remove")
@click.argument("name")
def code_agent_db_remove(name: str) -> None:
    """Remove connection reference NAME."""
    if name not in list_connections():
        click.echo(f"  No connection reference named {name!r}.")
        return
    remove_connection(name, _default_keychain())
    click.echo(f"  Removed connection {name!r}.")


@code_agent_db_group.command("list")
def code_agent_db_list() -> None:
    """List configured connection reference names."""
    names = list_connections()
    if not names:
        click.echo("  No connections are configured.")
        return
    for name in names:
        click.echo(f"  {name}")


def _skills_ensure_runner(argv: list[str]) -> None:
    """Run one skills/plugin update command for real, with a timeout.

    Only ever called through `skills_ensure.ensure`'s `runner` parameter,
    which catches whatever this raises and keeps going with the remaining
    commands -- a broken network or a missing binary must never stop a
    session from starting.
    """
    result = run(argv, capture_output=True, text=True, timeout=25)
    if result.returncode != 0:
        detail = "\n".join(part for part in (result.stderr.strip(), result.stdout.strip()) if part)
        raise RuntimeError(detail or f"exit code {result.returncode}")


@code_agent_group.command("skills-ensure")
def code_agent_skills_ensure() -> None:
    """Update the code agent's skills and Claude Code plugins, at most once a day.

    Never fails the session: every failed update command is one short line
    on stderr, and this command always exits 0.
    """
    from datetime import datetime, timezone

    from headroom.paths import workspace_dir

    skills, plugins = skills_ensure.load_configured_skills_and_plugins()
    state_path = workspace_dir() / "code_tools" / "skills_ensure.json"
    result = skills_ensure.ensure(
        skills,
        plugins,
        now=datetime.now(timezone.utc),
        runner=_skills_ensure_runner,
        state_path=state_path,
    )
    for failure in result.failures:
        click.echo(f"  skills-ensure: {failure}", err=True)
    if result.failures and result.skipped_plugins:
        count = len(result.skipped_plugins)
        plural = "" if count == 1 else "s"
        click.echo(
            f"  skills-ensure: {count} configured plugin{plural} not installed, skipped",
            err=True,
        )


@code_agent_group.command("brief")
def code_agent_brief() -> None:
    """Print a brief for the user's prompt, as a UserPromptSubmit hook.

    Reads the hook's stdin JSON (`prompt`, `cwd`, and a few other fields
    Claude Code always sends). When a brief applies, prints the
    `additionalContext` JSON Claude Code expects on stdout. Prints nothing
    and exits 0 in every other case -- missing/invalid stdin, a prompt
    that does not need a brief, a timeout, or any other error -- since a
    broken hook must never block a prompt. Also exits immediately, before
    reading stdin, when `RECURSION_GUARD_ENV` is set: that only happens
    inside the nested `claude -p` call the brief itself makes, and stops
    that nested call's own hooks from calling this command again.
    """
    if os.environ.get(brief.RECURSION_GUARD_ENV):
        return

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return
    if not isinstance(payload, dict):
        return

    prompt = payload.get("prompt")
    cwd = payload.get("cwd")
    if not isinstance(prompt, str) or not isinstance(cwd, str):
        return

    try:
        result = brief.make_brief(
            prompt, cwd=cwd, gather=brief.gather, model_runner=brief.default_model_runner
        )
    except Exception:
        return
    if result is None:
        return

    click.echo(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": result,
                }
            }
        )
    )


@code_agent_group.command("check")
def code_agent_check() -> None:
    """Run the edited file's own type checker and linter, as a PostToolUse hook.

    Reads the hook's stdin JSON, works out which checks the file's project
    has actually configured, and runs them. Exits 2 with the findings on
    stderr when any check reports a problem; otherwise exits 0 with nothing
    printed.
    """
    stdin_json = sys.stdin.read()
    code, message = hook_main(stdin_json, real_runner)
    if message:
        click.echo(message, err=True)
    sys.exit(code)
