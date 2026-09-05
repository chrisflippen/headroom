"""Proof that no test can reach the real launchd/systemd/SCM or send a real
process signal via `headroom.install.supervisors` / `headroom.install.runtime`.

Regression test for the 2026-09 incident: two install tests let a real
`launchctl bootout gui/501/com.headroom.default` reach the OS and took the
live proxy down for 8 hours. See `_guard_real_service_manager` in
`tests/conftest.py`.
"""

from __future__ import annotations

import subprocess

import pytest

from headroom.install import runtime

# Captured at module-import time, before any fixture (including the guard)
# has ever run, so this is a reliable reference to the *real* function.
_REAL_SUBPROCESS_RUN = subprocess.run
_REAL_OS_KILL = runtime.os.kill


def test_guard_replaces_subprocess_run_by_default() -> None:
    """Sanity check the fixture actually installed something."""
    assert subprocess.run is not _REAL_SUBPROCESS_RUN


@pytest.mark.parametrize(
    "command",
    [
        ["launchctl", "bootout", "gui/501/com.headroom.default"],
        ["systemctl", "stop", "headroom"],
        ["SC.EXE", "stop", "headroom"],
        'sc.exe create headroom binPath= "cmd.exe /c \\"run.cmd\\"" start= auto',
    ],
    ids=["launchctl", "systemctl", "sc.exe-list-mixed-case", "sc.exe-string"],
)
def test_guard_blocks_real_service_manager_commands(command: object) -> None:
    with pytest.raises(AssertionError, match="real service manager"):
        subprocess.run(command)  # type: ignore[call-overload]


def test_guard_lets_unrelated_subprocess_calls_through() -> None:
    """Only the three service-manager binaries are blocked; everything else
    -- e.g. the docker/nvidia-smi probes in headroom/cli/install.py -- must
    keep working exactly as before."""
    result = subprocess.run(["true"], check=False)
    assert result.returncode == 0


def test_guard_blocks_runtime_os_kill() -> None:
    with pytest.raises(AssertionError, match="real service manager"):
        runtime.os.kill(1, 15)


def test_guard_replaces_the_real_os_kill_too() -> None:
    """As of the 2026-09-05 follow-up incident, the real global `os.kill` is
    *also* patched -- by `_guard_real_process_signals_and_proxy_network` in
    `tests/conftest.py`, a second, independent guard from the one this file
    otherwise exercises. `headroom/cli/wrap.py` imports the real `os` module
    directly (not through `headroom.install.runtime`), so scoping the guard
    to `runtime`'s own `os` name alone left a real path to `os.kill` on a
    real pid: `_stop_local_proxy_for_unwrap` -> `_check_proxy` (a real socket
    connect) -> `query_proxy_config` (a real GET to /health) -> the live
    proxy's own self-reported pid -> `_kill_proxy_by_pid` ->
    `os.kill(pid, SIGTERM/SIGKILL)` (wrap.py:3647/3663). That reached the
    developer's live proxy on port 8787 eight times in one suite run. Signal
    0 (a pure liveness probe, never destructive) is exempted so
    `headroom._subprocess.pid_alive`'s fallback keeps working against pids a
    test did not spawn."""
    import os

    assert os.kill is not _REAL_OS_KILL


def test_guard_proxy_still_delegates_unrelated_os_attributes() -> None:
    """Existing tests do
    ``monkeypatch.setattr("headroom.install.runtime.os.getuid", ...)`` and
    also just read plain attributes like ``os.name`` through this module's
    ``os`` name -- the guard must not break that passthrough."""
    import os as real_os

    assert runtime.os.name == real_os.name
    assert runtime.os.getpid() == real_os.getpid()


@pytest.mark.allow_real_service_manager
def test_marker_opts_out_of_the_guard() -> None:
    """`@pytest.mark.allow_real_service_manager` disables the guard entirely
    for this one test -- proven by `subprocess.run` being the real function,
    not the wrapper -- without this test actually needing to invoke a real
    service manager to prove it."""
    assert subprocess.run is _REAL_SUBPROCESS_RUN
    assert runtime.os.kill is _REAL_OS_KILL


def test_fixture_opts_out_of_the_guard(allow_real_service_manager: None) -> None:
    """Same proof, via the fixture-based opt-in instead of the marker."""
    assert subprocess.run is _REAL_SUBPROCESS_RUN
    assert runtime.os.kill is _REAL_OS_KILL
