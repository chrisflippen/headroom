"""Proof for the 2026-09-05 follow-up incident: a suite run in this worktree
SIGTERM'd the developer's live, production-adjacent Headroom proxy on port
8787 eight times between 16:50 and 17:08, via clean SIGTERM shutdowns that
never touched `launchctl`/`systemctl`/`sc.exe` at all -- so the pre-existing
`_guard_real_service_manager` (see `tests/test_install/test_real_service_manager_guard.py`)
never saw them.

Root cause: `headroom/cli/wrap.py` imports the real `os` and `socket` modules
directly (not through `headroom.install.runtime`), and
`_stop_local_proxy_for_unwrap` reaches a *real* pid over a *real* network
round-trip that no amount of HOME/env-var isolation can stop:

    _check_proxy(port)          -- real `socket.socket(...).connect(("127.0.0.1", 8787))`
    -> _query_proxy_config(port) / query_proxy_config(port)
                                 -- real `urllib.request.urlopen("http://127.0.0.1:8787/health")`,
                                    which returns the live proxy's own self-reported pid
    -> _kill_proxy_by_pid(pid, port)
                                 -- real `os.kill(pid, signal.SIGTERM)`  (wrap.py:3647)
                                    real `os.kill(pid, signal.SIGKILL)` (wrap.py:3663) on timeout

This is `_guard_real_process_signals_and_proxy_network` in `tests/conftest.py`:
a second, independent, autouse guard (same opt-out mechanism) that (1) patches
the real, global `os.kill`/`os.killpg` so a signal may only reach a pid the
current test itself spawned (or its own pid), and (2) patches the real,
global `socket.socket.connect`/`.connect_ex` so nothing can open a real TCP
connection to 127.0.0.1/localhost/::1 port 8787.
"""

from __future__ import annotations

import multiprocessing
import os
import signal
import socket
import subprocess
import time

import pytest

# Captured before any fixture runs, so these are reliable references to the
# real, unpatched functions.
_REAL_OS_KILL = os.kill
_REAL_SOCKET_CONNECT = socket.socket.connect


def test_guard_blocks_signalling_a_pid_the_test_did_not_spawn() -> None:
    """This is exactly the shape of the leak: `_kill_proxy_by_pid` sends
    `SIGTERM` to a pid it read from somewhere else (the live proxy's own
    `/health` payload), not one it spawned itself. Deliberately use a pid
    number far outside any real process's range (macOS/Linux both cap real
    pids well under this) rather than a nearby "real" pid -- the guard must
    raise *before* ever reaching the real `os.kill` syscall, so even a buggy
    guard implementation can only fail with `ProcessLookupError` here, never
    signal an actual running process."""
    unused_pid = 2**31 - 2
    with pytest.raises(AssertionError, match="did not start"):
        os.kill(unused_pid, signal.SIGTERM)


def test_guard_exempts_signal_zero_liveness_probes() -> None:
    """Signal 0 never signals anything -- it is the standard "is this pid
    alive" probe (used by `headroom._subprocess.pid_alive`'s psutil-less
    fallback) and must keep working against pids the test did not spawn, or
    every liveness check in the suite would start raising."""
    os.kill(os.getpid(), 0)  # must not raise


def test_guard_allows_signalling_a_pid_the_test_itself_spawned() -> None:
    """A test that spawns its own child via `subprocess.Popen` (or anything
    built on it -- `.run`/`.check_call`/`.check_output`) must still be able
    to clean that child up with a real `os.kill`."""
    proc = subprocess.Popen(["sleep", "5"])
    try:
        os.kill(proc.pid, signal.SIGTERM)  # must not raise
    finally:
        proc.wait(timeout=5)


def test_guard_allows_signalling_own_pid() -> None:
    os.kill(os.getpid(), 0)  # must not raise


def _sleep_forever() -> None:
    time.sleep(5)


def test_guard_allows_signalling_a_multiprocessing_worker_the_test_spawned() -> None:
    """2026-09-05 same-day follow-up: a full-suite run showed
    `concurrent.futures.process.ProcessPoolExecutor`'s own internal cleanup
    thread getting blocked by this guard when terminating a worker it had
    just spawned (`tests/test_image_compression_isolation.py::
    test_worker_sigsegv_fails_open_parent_survives`), because the guard only
    tracked `subprocess.Popen`-spawned pids. `multiprocessing.Process` (and
    therefore `ProcessPoolExecutor`) goes through
    `multiprocessing.process.BaseProcess.start`, which must be tracked too."""
    proc = multiprocessing.Process(target=_sleep_forever)
    proc.start()
    try:
        assert proc.pid is not None
        os.kill(proc.pid, signal.SIGTERM)  # must not raise
    finally:
        proc.join(timeout=5)


def test_guard_blocks_real_connection_to_the_live_proxy_port() -> None:
    """This is exactly `headroom/cli/wrap.py::_check_proxy`'s real socket
    connect to the developer's live proxy."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(ConnectionRefusedError, match="8787"):
            sock.connect(("127.0.0.1", 8787))
    finally:
        sock.close()


def test_guard_blocks_real_connection_via_connect_ex_too() -> None:
    """`_check_proxy` (and other callers) may use `connect_ex` instead of
    `connect` to avoid a raised exception on refusal -- the guard must cover
    both entry points, or a caller using `connect_ex` sails straight through."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(ConnectionRefusedError, match="8787"):
            sock.connect_ex(("127.0.0.1", 8787))
    finally:
        sock.close()


def test_guard_blocks_localhost_and_the_ipv6_loopback_too() -> None:
    """`127.0.0.1`, `localhost`, and `::1` are all how a caller might spell
    "the local machine" -- the guard must not depend on one specific
    spelling."""
    for host in ("localhost", "::1"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(ConnectionRefusedError, match="8787"):
                sock.connect((host, 8787))
        finally:
            sock.close()


def test_guard_does_not_block_other_ports() -> None:
    """Only port 8787 is guarded -- tests routinely bind their own ephemeral
    -port servers and must be able to connect to them exactly as before."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            client.connect(("127.0.0.1", port))  # must not raise
        finally:
            client.close()
    finally:
        server.close()


@pytest.mark.allow_real_service_manager
def test_marker_opts_out_of_both_new_guards() -> None:
    """`@pytest.mark.allow_real_service_manager` disables
    `_guard_real_process_signals_and_proxy_network` too, not just
    `_guard_real_service_manager` -- both hard rules share the one opt-out
    mechanism, proven here without actually needing to reach a real service."""
    assert os.kill is _REAL_OS_KILL
    assert socket.socket.connect is _REAL_SOCKET_CONNECT


def test_fixture_opts_out_of_both_new_guards(allow_real_service_manager: None) -> None:
    """Same proof, via the fixture-based opt-in instead of the marker."""
    assert os.kill is _REAL_OS_KILL
    assert socket.socket.connect is _REAL_SOCKET_CONNECT
