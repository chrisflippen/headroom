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
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from headroom import fsutil
from headroom._subprocess import run
from headroom.code_tools import brief, skills_ensure
from headroom.providers.claude.vscode import claude_user_settings_path

from .main import main

# The full name Claude Code expects for a plugin agent: "<plugin>:<agent>".
DEFAULT_AGENT = "headroom-code-agent:code"

_MARKETPLACE_NAME = "headroom-marketplace"
_PLUGIN_NAME = "headroom-code-agent"
_FORK_SOURCE = "chrisflippen/headroom"
_MANAGED_KEY = "_headroom_managed"
_AGENT_KEY = "agent"

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


# ---------------------------------------------------------------------------
# 1-2. The agent switch itself.
# ---------------------------------------------------------------------------


def ensure_agent_switch(settings_path: Path, agent: str = DEFAULT_AGENT) -> bool:
    """Set `agent` in the user settings file, marking it as headroom-managed.

    Returns True when the file changed. If the user already has a different
    `agent` value that headroom did not write, that value is left alone and
    this returns False — use `agent_switch_state` to see the conflict.
    """
    payload = _read_settings(settings_path)
    current = payload.get(_AGENT_KEY)
    managed = _managed_agent_entry(payload)

    if current is not None and managed is None:
        return False
    if current == agent and managed is not None:
        return False

    previous = managed.get("previous") if managed is not None else current
    payload[_AGENT_KEY] = agent
    managed_all = payload.get(_MANAGED_KEY)
    if not isinstance(managed_all, dict):
        managed_all = {}
    managed_all[_AGENT_KEY] = {"previous": previous}
    payload[_MANAGED_KEY] = managed_all

    _write_settings(settings_path, payload)
    return True


def remove_agent_switch(settings_path: Path) -> bool:
    """Remove the managed `agent` entry, restoring the value it replaced.

    A user-set `agent` value (one headroom never wrote) is left alone.
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
    if isinstance(managed_all, dict):
        managed_all.pop(_AGENT_KEY, None)
        if managed_all:
            payload[_MANAGED_KEY] = managed_all
        else:
            payload.pop(_MANAGED_KEY, None)

    _write_settings(settings_path, payload)
    return True


def agent_switch_state(settings_path: Path) -> str:
    """Return "off", "on", or "user-set:<name>" for the current agent switch."""
    payload = _read_settings(settings_path)
    current = payload.get(_AGENT_KEY)
    if current is None:
        return "off"
    if _managed_agent_entry(payload) is not None:
        return "on"
    return f"user-set:{current}"


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


# ---------------------------------------------------------------------------
# 5. Plugin install / uninstall — argv only, dispatched through `runner`.
# ---------------------------------------------------------------------------


def _local_checkout_source() -> str | None:
    """The repo root, if this code is running from a local git checkout."""
    repo_root = Path(__file__).resolve().parents[2]
    if (repo_root / ".claude-plugin" / "marketplace.json").exists():
        return str(repo_root)
    return None


def marketplace_source() -> str:
    """Where to add the headroom marketplace from.

    `HEADROOM_MARKETPLACE_SOURCE` wins if set (matches `headroom init`'s
    override). A local checkout of the repo wins next. Otherwise this is the
    fork, `chrisflippen/headroom`.
    """
    override = os.environ.get("HEADROOM_MARKETPLACE_SOURCE")
    if override:
        return override
    local = _local_checkout_source()
    if local is not None:
        return local
    return _FORK_SOURCE


def install_plugin(runner: Runner, source: str) -> None:
    """Replace a stale marketplace entry, then install the code agent plugin.

    Three `claude` calls in order: drop whatever `headroom-marketplace`
    currently points at, add it back pointing at `source`, then install the
    plugin for the current user.
    """
    runner(["claude", "plugin", "marketplace", "remove", _MARKETPLACE_NAME])
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


def remove_plugin(runner: Runner) -> None:
    """Uninstall the code agent plugin. Leaves the marketplace entry in place."""
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


def _claude_runner(argv: list[str]) -> None:
    """Run a `claude ...` argv for real.

    A marketplace or plugin that is already in the requested state (already
    removed, already installed, already uninstalled) is not a failure — it
    is exactly what the caller wanted, so a non-zero exit whose message says
    so is tolerated rather than raised.
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise click.ClickException("'claude' not found in PATH. Install Claude Code first.")
    result = run([claude_bin, *argv[1:]], capture_output=True, text=True)
    if result.returncode == 0:
        return
    detail = "\n".join(part for part in (result.stderr.strip(), result.stdout.strip()) if part)
    if "already" in detail.lower() or "not found" in detail.lower() or "exists" in detail.lower():
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
