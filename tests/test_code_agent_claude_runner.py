"""Tests for `_claude_runner`'s narrow failure-tolerance rules (Fix 2).

`_claude_runner` is the one place headroom's code agent CLI actually shells
out to the real `claude` binary. It used to tolerate any non-zero exit whose
message contained "not found" -- which hid the actual live bug this module
shipped with: `claude plugin install ...` failing with "Plugin ... not
found in marketplace" was silently swallowed as if it meant "already gone".

These tests call the private `_claude_runner` directly (unlike
`test_code_agent_switch.py`, which sticks to public functions) because the
tolerance rules are exactly its own internal branching -- there is no public
seam that exercises every branch without one.
"""

from __future__ import annotations

import subprocess
from typing import Any

import click
import pytest

from headroom.cli import code_agent


def _fake_run(returncode: int, stdout: str = "", stderr: str = "") -> Any:
    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr
        )

    return run


@pytest.fixture(autouse=True)
def _fake_claude_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(code_agent.shutil, "which", lambda _name: "/usr/bin/claude")


def test_claude_runner_succeeds_on_zero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(code_agent, "run", _fake_run(0))

    code_agent._claude_runner(["claude", "plugin", "install", "x@y", "--scope", "user"])


def test_claude_runner_tolerates_already_installed_on_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        code_agent, "run", _fake_run(1, stderr="Error: plugin x@y is already installed")
    )

    code_agent._claude_runner(["claude", "plugin", "install", "x@y", "--scope", "user"])


def test_claude_runner_tolerates_already_exists_on_marketplace_add(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        code_agent,
        "run",
        _fake_run(1, stderr="marketplace 'headroom-code-agent-marketplace' already exists"),
    )

    code_agent._claude_runner(["claude", "plugin", "marketplace", "add", "/some/dir"])


def test_claude_runner_tolerates_already_added_on_marketplace_add(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(code_agent, "run", _fake_run(1, stderr="that marketplace is already added"))

    code_agent._claude_runner(["claude", "plugin", "marketplace", "add", "/some/dir"])


def test_claude_runner_tolerates_not_found_on_plugin_uninstall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(code_agent, "run", _fake_run(1, stderr="Plugin x@y not found"))

    code_agent._claude_runner(["claude", "plugin", "uninstall", "x@y", "--scope", "user"])


def test_claude_runner_tolerates_no_marketplace_on_marketplace_remove(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(code_agent, "run", _fake_run(1, stderr="no marketplace named that"))

    code_agent._claude_runner(["claude", "plugin", "marketplace", "remove", "some-marketplace"])


def test_claude_runner_tolerates_not_installed_on_plugin_uninstall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(code_agent, "run", _fake_run(1, stderr="plugin x@y is not installed"))

    code_agent._claude_runner(["claude", "plugin", "uninstall", "x@y", "--scope", "user"])


def test_claude_runner_raises_on_plugin_not_found_in_marketplace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This is the actual live bug this module shipped with: "not found" on a
    # `plugin install` command means the marketplace entry is wrong (a stale
    # marketplace name, a typo, a plugin the marketplace doesn't list) --
    # never "already done". It must raise, not be silently swallowed.
    monkeypatch.setattr(
        code_agent,
        "run",
        _fake_run(1, stderr="Plugin 'headroom-code-agent' not found in marketplace"),
    )

    with pytest.raises(click.ClickException) as exc_info:
        code_agent._claude_runner(
            [
                "claude",
                "plugin",
                "install",
                "headroom-code-agent@headroom-code-agent-marketplace",
                "--scope",
                "user",
            ]
        )

    assert "not found in marketplace" in str(exc_info.value)


def test_claude_runner_raises_on_unrelated_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(code_agent, "run", _fake_run(1, stderr="permission denied"))

    with pytest.raises(click.ClickException) as exc_info:
        code_agent._claude_runner(["claude", "plugin", "marketplace", "add", "/some/dir"])

    assert "permission denied" in str(exc_info.value)


def test_claude_runner_raises_when_claude_binary_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(code_agent.shutil, "which", lambda _name: None)

    with pytest.raises(click.ClickException) as exc_info:
        code_agent._claude_runner(["claude", "plugin", "marketplace", "add", "/some/dir"])

    assert "'claude' not found in PATH" in str(exc_info.value)
