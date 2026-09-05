"""Tests for headroom.code_tools.post_edit_check -- per-language post-edit checks.

Every test uses a recording fake runner and a temp repo tree; none of them
ever shells out to a real type checker or linter.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from headroom.code_tools import post_edit_check as pec


@dataclass
class _FakeResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class _RecordingRunner:
    """A fake runner: records every call and replays canned results by check name."""

    def __init__(self, results: dict[str, _FakeResult] | None = None) -> None:
        self.results = results or {}
        self.calls: list[tuple[pec.Check, int]] = []

    def __call__(self, check: pec.Check, timeout: int) -> _FakeResult:
        self.calls.append((check, timeout))
        if check.name in self.results:
            return self.results[check.name]
        return _FakeResult(returncode=0)


def _write(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. Python detection: gated by [tool.pyrefly] / [tool.ruff], plus .venv.
# ---------------------------------------------------------------------------


def test_python_file_with_pyrefly_config_and_venv_gets_one_pyrefly_check(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[tool.pyrefly]\n")
    (tmp_path / ".venv").mkdir()
    module = _write(tmp_path / "pkg" / "mod.py", "x = 1\n")

    checks = pec.detect_checks(module, tmp_path)

    names = [c.name for c in checks]
    assert names == ["pyrefly"]
    check = checks[0]
    assert check.argv == ["uv", "run", "pyrefly", "check", str(module)]
    assert check.cwd == tmp_path
    assert check.scope == "file"


def test_python_file_without_pyrefly_config_gets_no_pyrefly_check(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", '[project]\nname = "x"\n')
    (tmp_path / ".venv").mkdir()
    module = _write(tmp_path / "pkg" / "mod.py", "x = 1\n")

    checks = pec.detect_checks(module, tmp_path)

    assert "pyrefly" not in [c.name for c in checks]


def test_python_file_with_ruff_config_gets_a_ruff_check_too(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[tool.pyrefly]\n[tool.ruff]\nline-length = 100\n")
    (tmp_path / ".venv").mkdir()
    module = _write(tmp_path / "pkg" / "mod.py", "x = 1\n")

    checks = pec.detect_checks(module, tmp_path)

    names = {c.name for c in checks}
    assert names == {"pyrefly", "ruff"}
    ruff_check = next(c for c in checks if c.name == "ruff")
    assert ruff_check.argv == ["uv", "run", "ruff", "check", str(module)]
    assert ruff_check.scope == "file"


# ---------------------------------------------------------------------------
# 2. TypeScript detection: pnpm + tsconfig.json (+ eslint config).
# ---------------------------------------------------------------------------


def test_ts_file_with_tsconfig_and_pnpm_gets_a_project_scope_tsc_check(tmp_path: Path) -> None:
    _write(tmp_path / "package.json", "{}")
    _write(tmp_path / "pnpm-lock.yaml", "")
    _write(tmp_path / "tsconfig.json", "{}")
    module = _write(tmp_path / "src" / "index.ts", "const x = 1;\n")

    checks = pec.detect_checks(module, tmp_path)

    tsc = next(c for c in checks if c.name == "tsc")
    assert tsc.argv == ["pnpm", "exec", "tsc", "--noEmit"]
    assert tsc.cwd == tmp_path
    assert tsc.scope == "project"


def test_ts_file_with_eslint_config_also_gets_an_eslint_check(tmp_path: Path) -> None:
    _write(tmp_path / "package.json", "{}")
    _write(tmp_path / "pnpm-lock.yaml", "")
    _write(tmp_path / "tsconfig.json", "{}")
    _write(tmp_path / "eslint.config.js", "module.exports = [];\n")
    module = _write(tmp_path / "src" / "index.ts", "const x = 1;\n")

    checks = pec.detect_checks(module, tmp_path)

    names = {c.name for c in checks}
    assert names == {"tsc", "eslint"}
    eslint = next(c for c in checks if c.name == "eslint")
    assert eslint.argv == ["pnpm", "exec", "eslint", str(module)]
    assert eslint.scope == "file"


# ---------------------------------------------------------------------------
# 3. Svelte detection.
# ---------------------------------------------------------------------------


def test_svelte_file_in_project_declaring_svelte_check_gets_the_command(tmp_path: Path) -> None:
    _write(
        tmp_path / "package.json",
        json.dumps({"devDependencies": {"svelte-check": "^3.0.0"}}),
    )
    _write(tmp_path / "pnpm-lock.yaml", "")
    module = _write(tmp_path / "src" / "App.svelte", "<script></script>\n")

    checks = pec.detect_checks(module, tmp_path)

    assert len(checks) == 1
    check = checks[0]
    assert check.name == "svelte-check"
    assert check.argv == ["pnpm", "exec", "sv", "check"]
    assert check.cwd == tmp_path
    assert check.scope == "project"


def test_svelte_file_without_svelte_check_declared_gets_no_check(tmp_path: Path) -> None:
    _write(tmp_path / "package.json", json.dumps({"devDependencies": {}}))
    _write(tmp_path / "pnpm-lock.yaml", "")
    module = _write(tmp_path / "src" / "App.svelte", "<script></script>\n")

    checks = pec.detect_checks(module, tmp_path)

    assert checks == []


# ---------------------------------------------------------------------------
# 4. Rust / Cargo clippy.
# ---------------------------------------------------------------------------


def test_rust_file_in_cargo_workspace_gets_project_scope_clippy(tmp_path: Path) -> None:
    _write(tmp_path / "Cargo.toml", "[workspace]\n")
    module = _write(tmp_path / "src" / "main.rs", "fn main() {}\n")

    checks = pec.detect_checks(module, tmp_path)

    assert len(checks) == 1
    check = checks[0]
    assert check.name == "cargo-clippy"
    assert check.argv == ["cargo", "clippy"]
    assert check.cwd == tmp_path
    assert check.scope == "project"


def test_rust_file_without_cargo_toml_gets_no_check(tmp_path: Path) -> None:
    module = _write(tmp_path / "src" / "main.rs", "fn main() {}\n")

    checks = pec.detect_checks(module, tmp_path)

    assert checks == []


# ---------------------------------------------------------------------------
# 5. Go: go vet + gofmt.
# ---------------------------------------------------------------------------


def test_go_file_gets_go_vet_and_gofmt(tmp_path: Path) -> None:
    _write(tmp_path / "go.mod", "module example.com/thing\n")
    module = _write(tmp_path / "pkg" / "thing.go", "package pkg\n")

    checks = pec.detect_checks(module, tmp_path)

    names = {c.name for c in checks}
    assert names == {"go-vet", "gofmt"}
    vet = next(c for c in checks if c.name == "go-vet")
    assert vet.argv == ["go", "vet", "./pkg"]
    assert vet.scope == "project"
    gofmt = next(c for c in checks if c.name == "gofmt")
    assert gofmt.argv == ["gofmt", "-l", str(module)]
    assert gofmt.scope == "file"


# ---------------------------------------------------------------------------
# 6. Project-scope output filtering.
# ---------------------------------------------------------------------------


def test_project_scope_output_is_filtered_to_the_edited_file(tmp_path: Path) -> None:
    edited = _write(tmp_path / "src" / "index.ts", "const x = 1;\n")
    other = tmp_path / "src" / "other.ts"

    check = pec.Check(
        name="tsc", argv=["pnpm", "exec", "tsc", "--noEmit"], cwd=tmp_path, scope="project"
    )
    runner = _RecordingRunner(
        {
            "tsc": _FakeResult(
                returncode=1,
                stdout=(
                    f"{edited}(3,5): error TS1: bad thing\n{other}(1,1): error TS2: unrelated\n"
                ),
            )
        }
    )

    report = pec.run_checks([check], edited, runner)

    assert not report.ok
    assert "bad thing" in report.findings
    assert "unrelated" not in report.findings
    assert str(other) not in report.findings


# ---------------------------------------------------------------------------
# 7. Missing tool -> skip, not a failure.
# ---------------------------------------------------------------------------


def test_missing_tool_is_skipped_not_failed(tmp_path: Path) -> None:
    edited = _write(tmp_path / "mod.py", "x = 1\n")
    check = pec.Check(
        name="pyrefly",
        argv=["uv", "run", "pyrefly", "check", str(edited)],
        cwd=tmp_path,
        scope="file",
    )

    def raising_runner(check: pec.Check, timeout: int) -> Any:
        raise FileNotFoundError("uv not found")

    report = pec.run_checks([check], edited, raising_runner)

    assert report.ok
    assert report.findings == ""
    assert len(report.skipped) == 1
    assert "pyrefly" in report.skipped[0]


def test_timeout_is_skipped_not_failed(tmp_path: Path) -> None:
    edited = _write(tmp_path / "mod.py", "x = 1\n")
    check = pec.Check(
        name="pyrefly",
        argv=["uv", "run", "pyrefly", "check", str(edited)],
        cwd=tmp_path,
        scope="file",
    )

    def timing_out_runner(check: pec.Check, timeout: int) -> Any:
        raise subprocess.TimeoutExpired(cmd=check.argv, timeout=timeout)

    report = pec.run_checks([check], edited, timing_out_runner)

    assert report.ok
    assert len(report.skipped) == 1
    assert "timed out" in report.skipped[0]


# ---------------------------------------------------------------------------
# 8. hook_main path resolution, both payload shapes, plus a .md immediate exit.
# ---------------------------------------------------------------------------


def test_hook_main_resolves_headroom_edit_relative_path(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", '[project]\nname = "x"\n')
    _write(tmp_path / "mod.py", "x = 1\n")
    payload = json.dumps(
        {
            "tool_input": {"path": "mod.py", "action": "replace"},
            "cwd": str(tmp_path),
        }
    )
    runner = _RecordingRunner()

    code, message = pec.hook_main(payload, runner)

    assert code == 0
    assert message == ""


def test_hook_main_resolves_builtin_edit_file_path(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", '[project]\nname = "x"\n')
    module = _write(tmp_path / "mod.py", "x = 1\n")
    payload = json.dumps(
        {
            "tool_input": {"file_path": str(module)},
            "cwd": str(tmp_path),
        }
    )
    runner = _RecordingRunner()

    code, message = pec.hook_main(payload, runner)

    assert code == 0
    assert message == ""


def test_hook_main_exits_zero_immediately_for_a_markdown_file(tmp_path: Path) -> None:
    payload = json.dumps(
        {
            "tool_input": {"file_path": str(tmp_path / "README.md")},
            "cwd": str(tmp_path),
        }
    )

    def must_not_be_called(check: pec.Check, timeout: int) -> Any:
        raise AssertionError("runner must not be called for a non-source file")

    code, message = pec.hook_main(payload, must_not_be_called)

    assert code == 0
    assert message == ""


# ---------------------------------------------------------------------------
# 9. Findings -> exit 2 with the fix-now message.
# ---------------------------------------------------------------------------


def test_hook_main_exits_two_with_findings_and_the_fix_now_message(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[tool.pyrefly]\n")
    (tmp_path / ".venv").mkdir()
    module = _write(tmp_path / "mod.py", "x = 1\n")
    payload = json.dumps(
        {
            "tool_input": {"file_path": str(module)},
            "cwd": str(tmp_path),
        }
    )
    runner = _RecordingRunner(
        {"pyrefly": _FakeResult(returncode=1, stdout=f"{module}: error: bad type\n")}
    )

    code, message = pec.hook_main(payload, runner)

    assert code == 2
    assert "bad type" in message
    assert str(module) in message
    assert "fix these now, in this file, before touching any other file" in message
    assert "pyrefly-autofix" in message


# ---------------------------------------------------------------------------
# 10. Plugin wiring: hooks.json has both PostToolUse matchers.
# ---------------------------------------------------------------------------

_PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins" / "headroom-code-agent"


def test_plugin_hooks_json_wires_post_tool_use_for_both_edit_paths() -> None:
    hooks = json.loads((_PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    post_tool_use = hooks["hooks"]["PostToolUse"]
    matchers = {entry["matcher"]: entry["hooks"][0] for entry in post_tool_use}

    assert set(matchers) == {"mcp__headroom__Edit", "Edit|Write|MultiEdit"}
    for command in matchers.values():
        assert command["command"] == "headroom code-agent check"
        assert command["timeout"] == 150


if __name__ == "__main__":
    pytest.main([__file__])
