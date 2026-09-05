"""Keeps the code agent's skills and Claude Code plugins current.

Christopher installs skills with the skills.sh CLI (`npx skills ...`) and
Claude Code plugins with `claude plugin ...`. This module runs those update
commands at session start, at most once a day, so the code agent is always
working from the latest version of its skills and plugins without Christopher
having to remember to run the updates by hand.

`ensure` is the one entry function. It never raises for a failed update
command -- a broken network or a missing `claude` binary should not stop a
session from starting. Every command goes through the caller's `runner`
instead of shelling out directly, so a test can record what would have run
without touching the real `npx` or `claude` binaries.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# A runner takes one argv list and does something with it (run it for real,
# or record it in a test). It raises on failure; `ensure` catches that and
# keeps going with the remaining commands.
Runner = Callable[[list[str]], None]

# Skills Christopher wants the code agent to have, and where skills.sh should
# install each one from if it is not already in the lock file. The source
# string matches skills.sh's `npx skills add <owner/repo>@<skill> -g -y`
# syntax; these are the exact sources recorded for each skill in
# ~/.agents/.skill-lock.json.
DEFAULT_SKILLS: tuple[dict[str, str], ...] = (
    {"name": "code-review", "source": "mattpocock/skills@code-review"},
    {"name": "codebase-design", "source": "mattpocock/skills@codebase-design"},
    {"name": "domain-modeling", "source": "mattpocock/skills@domain-modeling"},
    {"name": "grill-with-docs", "source": "mattpocock/skills@grill-with-docs"},
    {
        "name": "improve-codebase-architecture",
        "source": "mattpocock/skills@improve-codebase-architecture",
    },
)

# Claude Code plugins to keep current, as "<plugin>@<marketplace>". The code
# agent plugin's marketplace is "headroom-code-agent-marketplace" (Fix 1: it
# ships its own marketplace manifest inside the installed package); the hooks
# plugin's marketplace, "headroom-marketplace", is unrelated and managed by
# `headroom init`.
DEFAULT_PLUGINS: tuple[str, ...] = (
    "headroom-code-agent@headroom-code-agent-marketplace",
    "headroom@headroom-marketplace",
)

# Two dotted, flat keys in headroom's settings.json (see headroom.settings_store
# for the file this lives in) that let Christopher override the lists above.
SETTINGS_SKILLS_KEY = "code_agent.skills"
SETTINGS_PLUGINS_KEY = "code_agent.plugins"


@dataclass(frozen=True)
class EnsureResult:
    """What one call to `ensure` did.

    `ran` is False when the throttle skipped the run entirely -- in that
    case `commands` and `failures` are both empty and `skipped_reason` says
    why. When `ran` is True, `commands` lists every argv attempted, in
    order, and `failures` holds a short message per command that raised.
    """

    ran: bool
    commands: list[list[str]]
    failures: list[str]
    skipped_reason: str | None = None


def _read_last_run(state_path: Path) -> datetime | None:
    """The last run time recorded in the state file, or None.

    A missing file, an unreadable file, or a file that is not the shape
    this module writes (corrupt JSON, wrong type, bad timestamp) is treated
    as "never ran" rather than raised -- the throttle then just runs.
    """
    try:
        raw = state_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    last_run = payload.get("last_run")
    if not isinstance(last_run, str):
        return None
    try:
        return datetime.fromisoformat(last_run)
    except ValueError:
        return None


def _write_last_run(state_path: Path, now: datetime) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"last_run": now.isoformat()}
    state_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _skill_lock_path() -> Path:
    return Path.home() / ".agents" / ".skill-lock.json"


def _installed_skill_names() -> set[str]:
    """Skill names already in skills.sh's lock file.

    A missing or corrupt lock file yields an empty set -- every configured
    skill is then treated as missing and gets installed, which is the safe
    default (skills.sh's own `add` is a no-op for an already-installed skill).
    """
    try:
        raw = _skill_lock_path().read_text(encoding="utf-8")
    except OSError:
        return set()
    try:
        payload = json.loads(raw)
    except ValueError:
        return set()
    if not isinstance(payload, dict):
        return set()
    skills = payload.get("skills")
    if not isinstance(skills, dict):
        return set()
    return {name for name in skills if isinstance(name, str)}


def _run_one(
    runner: Runner, argv: list[str], commands: list[list[str]], failures: list[str]
) -> None:
    commands.append(argv)
    try:
        runner(argv)
    except Exception as exc:  # a bad update must never block a session
        failures.append(f"{' '.join(argv)}: {exc}")


def _run_plugins_concurrently(
    runner: Runner, plugins: Sequence[str], commands: list[list[str]], failures: list[str]
) -> None:
    """Run every plugin's update command on its own thread.

    One slow or hanging `claude plugin update` must never delay the rest.
    `commands` and `failures` end up in `plugins`' declared order regardless
    of which thread finishes first -- each thread only records its own
    outcome, and the results are appended to the shared lists afterward, in
    order, from the main thread.
    """
    argvs = [["claude", "plugin", "update", plugin, "--scope", "user", "-y"] for plugin in plugins]
    outcomes: list[str | None] = [None] * len(argvs)

    def worker(index: int, argv: list[str]) -> None:
        try:
            runner(argv)
        except Exception as exc:  # a bad update must never block a session
            outcomes[index] = f"{' '.join(argv)}: {exc}"

    threads = [
        threading.Thread(target=worker, args=(index, argv)) for index, argv in enumerate(argvs)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    for argv, failure in zip(argvs, outcomes):
        commands.append(argv)
        if failure is not None:
            failures.append(failure)


def ensure(
    skills: Sequence[dict[str, str]],
    plugins: Sequence[str],
    *,
    now: datetime,
    runner: Runner,
    state_path: Path,
    interval_hours: int = 24,
) -> EnsureResult:
    """Update skills and plugins through `runner`, at most once a day.

    Every skill in `skills` that skills.sh's lock file does not already list
    gets installed with one batched `npx skills add <source1> <source2> ...
    -g -y` call. Then `npx skills update -g -y` runs once, followed by one
    `claude plugin update <plugin> --scope user -y` per entry in `plugins`,
    all run concurrently since they are independent of each other. A
    command that raises has its failure recorded and the rest still run.
    The state file is written whenever the run was not skipped, even if
    every command failed.
    """
    last_run = _read_last_run(state_path)
    if last_run is not None:
        elapsed_hours = (now - last_run).total_seconds() / 3600
        if elapsed_hours < interval_hours:
            hours_ago = round(elapsed_hours)
            return EnsureResult(
                ran=False,
                commands=[],
                failures=[],
                skipped_reason=f"ran {hours_ago} hours ago",
            )

    commands: list[list[str]] = []
    failures: list[str] = []

    installed = _installed_skill_names()
    missing_sources = [skill["source"] for skill in skills if skill["name"] not in installed]
    if missing_sources:
        argv = ["npx", "skills", "add", *missing_sources, "-g", "-y"]
        _run_one(runner, argv, commands, failures)

    _run_one(runner, ["npx", "skills", "update", "-g", "-y"], commands, failures)

    _run_plugins_concurrently(runner, plugins, commands, failures)

    _write_last_run(state_path, now)

    return EnsureResult(ran=True, commands=commands, failures=failures, skipped_reason=None)


def load_configured_skills_and_plugins() -> tuple[list[dict[str, str]], list[str]]:
    """The skills and plugins to keep current: settings.json, else the defaults.

    Plugins go through the typed `HEADROOM_*` registry in
    `headroom.settings_store` (a `csv-list` field), which validates the
    value and applies the usual default/file/env precedence; the
    comma-joined string it returns is split back into a list here. Skills
    stay a raw settings.json read -- each entry is a structured name+source
    pair, not a simple comma-separated list, so there's no `csv-list` field
    shape for it to fit. A missing file, corrupt JSON, or a value that is
    not shaped as expected falls back to `DEFAULT_SKILLS` / `DEFAULT_PLUGINS`
    -- this must never raise, since it runs at session start.
    """
    from headroom import paths, settings_store

    try:
        raw = paths.settings_path().read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    skills_value = payload.get(SETTINGS_SKILLS_KEY)
    skills = _valid_skills(skills_value) if skills_value is not None else None
    if skills is None:
        skills = [dict(skill) for skill in DEFAULT_SKILLS]

    plugins_csv = settings_store.effective_values().get(SETTINGS_PLUGINS_KEY)
    if isinstance(plugins_csv, str) and plugins_csv:
        plugins = [token.strip() for token in plugins_csv.split(",") if token.strip()]
    else:
        plugins = list(DEFAULT_PLUGINS)

    return skills, plugins


def _valid_skills(value: object) -> list[dict[str, str]] | None:
    if not isinstance(value, list):
        return None
    skills: list[dict[str, str]] = []
    for entry in value:
        if not isinstance(entry, dict):
            return None
        name = entry.get("name")
        source = entry.get("source")
        if not isinstance(name, str) or not isinstance(source, str):
            return None
        skills.append({"name": name, "source": source})
    return skills
