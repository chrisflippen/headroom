"""Runs each edited file's own type checker and linter, automatically.

Christopher's rule: after any edit to a source file, the file's own type
checker and linter run right away, and their findings get fixed before the
model touches another file. This module is the generalized version of the
Python-only hook he already runs by hand
(`~/.claude/hooks/pyrefly-postedit.py`) -- one seam that works the same way
for every language the code agent might touch.

Three pieces:

- `detect_checks` looks at the edited file's extension and the nearest
  project's own config to decide which checks apply. It never invents a
  check the project has not configured -- no `pyproject.toml` section, no
  check; no `tsconfig.json`, no `tsc` check; and so on. The exact commands
  and config file names come from the `scaffold-first` skill's per-ecosystem
  references, not from memory.
- `run_checks` runs the checks a caller already picked, through an injected
  `runner` so tests never shell out to a real tool. A missing tool or a
  timeout is a skip, not a failure -- a broken toolchain must never look
  like a code problem.
- `hook_main` is the Claude Code `PostToolUse` hook entry point: it reads
  the hook's stdin JSON, works out which file was just edited, and turns
  `run_checks`' report into the exit code and message the hook protocol
  expects.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from headroom._subprocess import run as _run_subprocess

# ---------------------------------------------------------------------------
# Check / CheckReport -- the small data shapes everything else works with.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    """One command to run after an edit: its name, argv, working directory,
    and whether its output covers just the edited file or the whole
    project."""

    name: str
    argv: list[str]
    cwd: Path
    scope: str  # "file" or "project"


@dataclass(frozen=True)
class CheckReport:
    """What running a set of checks found.

    `findings` is empty when every check passed. `skipped` lists checks
    that could not tell us anything either way -- a missing tool, a
    timeout, or a project-scope failure that turned out to be about some
    other file -- never a check that actually failed on this file.
    """

    ok: bool
    findings: str
    skipped: list[str]


# A runner takes one `Check` and a timeout in seconds and returns something
# with `.returncode`, `.stdout`, and `.stderr` -- the same shape
# `subprocess.CompletedProcess` has. It raises `FileNotFoundError` when the
# tool isn't installed and `subprocess.TimeoutExpired` when it runs past the
# timeout; `run_checks` turns both into a skip.
Runner = Callable[[Check, int], Any]


def real_runner(check: Check, timeout: int) -> subprocess.CompletedProcess[str]:
    """Run one check's argv for real, capturing its combined output."""
    return _run_subprocess(
        check.argv, cwd=check.cwd, capture_output=True, text=True, timeout=timeout
    )


# ---------------------------------------------------------------------------
# detect_checks -- walk up to the project root, then gate each check on the
# project's own config, never on the tool being merely installed.
# ---------------------------------------------------------------------------

# Folders holding one of these are a repo root for the purposes of this walk.
# `*.csproj` / `*.sln` are matched separately since they are patterns, not
# fixed names.
_REPO_MARKERS = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "composer.json",
    "build.gradle",
    "build.gradle.kts",
    "Package.swift",
    "pubspec.yaml",
    "deno.json",
    "bun.lockb",
)


def _has_repo_marker(folder: Path) -> bool:
    for marker in _REPO_MARKERS:
        if (folder / marker).exists():
            return True
    try:
        if any(folder.glob("*.csproj")) or any(folder.glob("*.sln")):
            return True
    except OSError:
        return False
    return False


def _find_project_root(path: Path, root: Path) -> Path:
    """Walk up from `path`'s folder to `root`, looking for a repo marker.

    Stops at the first folder (inclusive of `root`) that has one. Falls
    back to `root` itself when none is found on the way, so a caller
    always gets somewhere sane to run a command from -- detection then
    simply finds no config there and skips every check.
    """
    root_resolved = root.resolve()
    current = path.resolve().parent
    while True:
        if _has_repo_marker(current):
            return current
        if current == root_resolved or current.parent == current:
            break
        current = current.parent
    return root_resolved


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


# --- Package-manager spine for JS/TS/Svelte: dispatch by lockfile. ---------


def _package_manager(project_root: Path) -> list[str]:
    if (project_root / "pnpm-lock.yaml").exists():
        return ["pnpm", "exec"]
    if (project_root / "yarn.lock").exists():
        return ["yarn"]
    if (project_root / "bun.lockb").exists():
        return ["bun", "x"]
    return ["npm", "exec", "--"]


# --- Python: pyrefly / mypy / ruff, gated by pyproject.toml sections. ------


def _has_pyrefly_config(project_root: Path, pyproject_text: str) -> bool:
    if "[tool.pyrefly]" in pyproject_text:
        return True
    if (project_root / "pyrefly.toml").exists():
        return True
    return "pyrefly" in pyproject_text


def _python_checks(path: Path, project_root: Path) -> list[Check]:
    pyproject = project_root / "pyproject.toml"
    if not pyproject.exists() or not (project_root / ".venv").exists():
        return []
    text = _read_text(pyproject)

    checks: list[Check] = []
    if _has_pyrefly_config(project_root, text):
        checks.append(
            Check("pyrefly", ["uv", "run", "pyrefly", "check", str(path)], project_root, "file")
        )
    if "[tool.mypy]" in text:
        checks.append(Check("mypy", ["uv", "run", "mypy", str(path)], project_root, "file"))
    has_ruff_config = "[tool.ruff]" in text or (project_root / "ruff.toml").exists()
    if has_ruff_config:
        checks.append(
            Check("ruff", ["uv", "run", "ruff", "check", str(path)], project_root, "file")
        )
    return checks


# --- JS/TS: tsc --noEmit, eslint, biome, oxlint -- each gated by its own
# config file, per the js-ts-core.md / js-servers.md / js-web-extended.md
# references. ----------------------------------------------------------

_ESLINT_CONFIG_NAMES = (
    "eslint.config.js",
    "eslint.config.mjs",
    "eslint.config.cjs",
    "eslint.config.ts",
)


def _has_eslint_config(project_root: Path) -> bool:
    return any((project_root / name).exists() for name in _ESLINT_CONFIG_NAMES)


def _has_biome_config(project_root: Path) -> bool:
    return (project_root / "biome.json").exists() or (project_root / "biome.jsonc").exists()


def _js_ts_checks(path: Path, project_root: Path) -> list[Check]:
    if not (project_root / "package.json").exists():
        return []
    pm = _package_manager(project_root)

    checks: list[Check] = []
    if (project_root / "tsconfig.json").exists():
        checks.append(Check("tsc", [*pm, "tsc", "--noEmit"], project_root, "project"))
    if _has_eslint_config(project_root):
        checks.append(Check("eslint", [*pm, "eslint", str(path)], project_root, "file"))
    if _has_biome_config(project_root):
        checks.append(Check("biome", [*pm, "biome", "check", str(path)], project_root, "file"))
    if (project_root / ".oxlintrc.json").exists():
        checks.append(Check("oxlint", [*pm, "oxlint", str(path)], project_root, "file"))
    return checks


# --- Svelte: svelte-check, gated by the project declaring it. --------------


def _svelte_checks(path: Path, project_root: Path) -> list[Check]:
    package_json = project_root / "package.json"
    if not package_json.exists():
        return []
    if "svelte-check" not in _read_text(package_json):
        return []
    pm = _package_manager(project_root)
    return [Check("svelte-check", [*pm, "sv", "check"], project_root, "project")]


# --- Rust: cargo clippy, works with a bare Cargo.toml (zero extra config). -


def _rust_checks(path: Path, project_root: Path) -> list[Check]:
    if not (project_root / "Cargo.toml").exists():
        return []
    return [Check("cargo-clippy", ["cargo", "clippy"], project_root, "project")]


# --- Go: go vet + gofmt -l, both ship with the toolchain, zero config. -----


def _go_checks(path: Path, project_root: Path) -> list[Check]:
    if not (project_root / "go.mod").exists():
        return []
    rel_dir = path.resolve().parent.relative_to(project_root.resolve()).as_posix()
    package = "./..." if rel_dir == "." else f"./{rel_dir}"
    return [
        Check("go-vet", ["go", "vet", package], project_root, "project"),
        Check("gofmt", ["gofmt", "-l", str(path)], project_root, "file"),
    ]


_SVELTE_SUFFIXES = (".svelte", ".svelte.ts", ".svelte.js")
_JS_TS_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


def detect_checks(path: Path, root: Path) -> list[Check]:
    """Which checks apply to `path`, an edited file somewhere under `root`.

    Detection is by extension, then gated by the nearest project's own
    config -- a check never runs unless the project actually set the tool
    up. Nothing is returned for a language the scaffold-first references
    are silent on.
    """
    project_root = _find_project_root(path, root)
    name = path.name

    if name.endswith(_SVELTE_SUFFIXES):
        return _svelte_checks(path, project_root)
    if path.suffix.lower() == ".py":
        return _python_checks(path, project_root)
    if path.suffix.lower() in _JS_TS_SUFFIXES:
        return _js_ts_checks(path, project_root)
    if path.suffix.lower() == ".rs":
        return _rust_checks(path, project_root)
    if path.suffix.lower() == ".go":
        return _go_checks(path, project_root)
    return []


# ---------------------------------------------------------------------------
# run_checks -- run the checks, filter project-scope output to the edited
# file, and turn a missing tool or a timeout into a skip, never a failure.
# ---------------------------------------------------------------------------

# gofmt -l always exits 0 -- it signals "needs formatting" by listing the
# file on stdout, not by a non-zero exit code. Every other check here uses
# the ordinary "non-zero means findings" convention.
_ALWAYS_ZERO_EXIT = frozenset({"gofmt"})


def _combined_output(result: Any) -> str:
    stdout = getattr(result, "stdout", "") or ""
    stderr = getattr(result, "stderr", "") or ""
    return "\n".join(part for part in (stdout, stderr) if part)


def _lines_mentioning(text: str, path: Path) -> str:
    name = path.name
    matched = [line for line in text.splitlines() if name in line]
    return "\n".join(matched)


def run_checks(checks: list[Check], path: Path, runner: Runner, timeout: int = 120) -> CheckReport:
    """Run `checks` for the edit at `path` through `runner`.

    A check that raises `FileNotFoundError` (tool not installed) or times
    out is recorded in `skipped`, not treated as a failure. A project-scope
    check whose non-zero output does not mention the edited file is also a
    skip -- it found something, just not here. Everything else that reports
    a problem goes into `findings`, one block per check, ending with a
    summary line count.
    """
    finding_blocks: list[str] = []
    skipped: list[str] = []

    for check in checks:
        try:
            result = runner(check, timeout)
        except FileNotFoundError as exc:
            skipped.append(f"{check.name}: tool not found ({exc})")
            continue
        except subprocess.TimeoutExpired:
            skipped.append(f"{check.name}: timed out after {timeout}s")
            continue

        combined = _combined_output(result)
        returncode = getattr(result, "returncode", 0)
        has_findings = returncode != 0 or (check.name in _ALWAYS_ZERO_EXIT and combined.strip())
        if not has_findings:
            continue

        if check.scope == "project":
            body = _lines_mentioning(combined, path)
            if not body:
                skipped.append(f"{check.name}: exit {returncode}, no findings for this file")
                continue
        else:
            body = combined

        finding_blocks.append(f"{check.name}:\n{body}")

    if not finding_blocks:
        return CheckReport(ok=True, findings="", skipped=skipped)

    total_lines = sum(block.count("\n") for block in finding_blocks)
    noun = "line" if total_lines == 1 else "lines"
    findings = "\n\n".join(finding_blocks) + f"\n\n{total_lines} finding {noun}."
    return CheckReport(ok=False, findings=findings, skipped=skipped)


# ---------------------------------------------------------------------------
# hook_main -- the Claude Code PostToolUse entry point.
# ---------------------------------------------------------------------------

_SOURCE_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".svelte",
    ".rs",
    ".go",
}

_SKIP_DIR_NAMES = {".venv", "node_modules", "target", ".git", "vendor", "vendored"}

_FIX_NOW_MESSAGE = (
    "fix these now, in this file, before touching any other file; for "
    "Python follow the pyrefly-autofix skill (real fixes, never "
    "suppressions); for other languages follow the tool's own message."
)


def _is_source_file(path: Path) -> bool:
    name = path.name
    if name.endswith(_SVELTE_SUFFIXES):
        return True
    return path.suffix.lower() in _SOURCE_EXTENSIONS


def _under_skipped_dir(path: Path) -> bool:
    return bool(set(path.parts) & _SKIP_DIR_NAMES)


def _edited_path(payload: dict[str, Any]) -> Path | None:
    """Pull the edited file's path out of a PostToolUse payload.

    The built-in Edit/Write/MultiEdit tools put an absolute path in
    `tool_input.file_path`. Headroom's own `mcp__headroom__Edit` puts a
    path in `tool_input.path` that is relative to the payload's `cwd` --
    except for a `rename`, where the file to check is the new path in
    `tool_input.to`, since `path` no longer exists after the rename, and a
    `delete`, where there is no file left to check at all.
    """
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None

    file_path = tool_input.get("file_path")
    if isinstance(file_path, str) and file_path:
        return Path(file_path)

    action = tool_input.get("action")
    if action == "delete":
        return None

    raw_path = tool_input.get("to") if action == "rename" else tool_input.get("path")
    if isinstance(raw_path, str) and raw_path:
        candidate = Path(raw_path)
        if candidate.is_absolute():
            return candidate
        cwd = payload.get("cwd")
        base = Path(cwd) if isinstance(cwd, str) and cwd else Path.cwd()
        return base / candidate

    return None


def hook_main(stdin_json: str, runner: Runner) -> tuple[int, str]:
    """Read a PostToolUse payload from `stdin_json` and run its checks.

    Returns `(0, "")` for anything that does not apply -- bad JSON, no
    resolvable path, a non-source file, a path under a vendored or
    dependency folder -- and for a clean run. Returns `(2, message)` when
    a check reports a finding, `message` being the findings plus the
    fix-now rule.
    """
    try:
        payload = json.loads(stdin_json or "{}")
    except ValueError:
        return 0, ""
    if not isinstance(payload, dict):
        return 0, ""

    path = _edited_path(payload)
    if path is None:
        return 0, ""
    if not _is_source_file(path) or _under_skipped_dir(path):
        return 0, ""

    cwd = payload.get("cwd")
    root = Path(cwd) if isinstance(cwd, str) and cwd else path.parent

    checks = detect_checks(path, root)
    if not checks:
        return 0, ""

    report = run_checks(checks, path, runner)
    if report.ok:
        return 0, ""

    return 2, f"{report.findings}\n\n{_FIX_NOW_MESSAGE}"


__all__ = [
    "Check",
    "CheckReport",
    "Runner",
    "detect_checks",
    "run_checks",
    "hook_main",
    "real_runner",
]
