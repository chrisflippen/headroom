"""Shared pytest fixtures for Headroom tests."""

# CRITICAL: Must be set before ANY imports that could trigger sentence_transformers
# The Rust tokenizers use parallelism that deadlocks with pytest-asyncio
import os

os.environ["TOKENIZERS_PARALLELISM"] = "false"

import json
import tempfile
import warnings
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import pytest

from tests._skip_helpers import external_model_skip_reason

if TYPE_CHECKING:
    from pluggy import Result

    from headroom.config import HeadroomConfig, RequestMetrics, SmartCrusherConfig
    from headroom.providers.openai import OpenAIProvider, OpenAITokenCounter

# Capture the real `warnings.warn` before pytest imports any test module.
# Some optional dependencies (e.g. `crewai`, imported by
# tests/test_integrations/crewai/) do `warnings.warn = <narrower
# replacement>` at *module import time* and never undo it -- once collection
# imports that module, every test for the rest of the pytest process calls
# the replacement instead of the real function. That replacement's signature
# is often narrower than the real one (it dropped `skip_file_prefixes`, a
# newer `warnings.warn` keyword argument), so it breaks unrelated code
# elsewhere in the suite (observed: `htmldate`'s `_strptime.py` call raising
# `TypeError: ... got an unexpected keyword argument 'skip_file_prefixes'` in
# a wholly unrelated test) with no indication the two tests are connected.
# This module (conftest.py) is always imported before any test module is
# collected, and nothing above this line imports `crewai` (or any other
# dependency known to monkeypatch `warnings.warn`), so this reference is
# always the original, un-monkeypatched function.
_ORIGINAL_WARNINGS_WARN = warnings.warn


# A live `headroom` dev session exports HEADROOM_* into the shell (and the
# Claude wrap adds ANTHROPIC_CUSTOM_HEADERS). Click `envvar=` options pick
# those up inside CliRunner, so assertions would see the developer's proxy
# config instead of the test's. Scrub them so local runs match CI; tests
# that need a value set it explicitly via monkeypatch or CliRunner env.
@pytest.fixture(autouse=True)
def _skip_proxy_dependency_gate_unless_exercised(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Most CLI tests run without headroom-ai[proxy] extras installed."""
    if request.node.get_closest_marker("proxy_dependency_gate") is not None:
        return
    try:
        from headroom.cli import proxy
    except ModuleNotFoundError:
        # Native-wrapper jobs intentionally install only pytest and exercise the
        # installer scripts without importing Headroom's runtime dependencies.
        return
    monkeypatch.setattr(proxy, "ensure_proxy_dependencies", lambda: None)


@pytest.fixture(autouse=True)
def _scrub_developer_headroom_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("HEADROOM_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("ANTHROPIC_CUSTOM_HEADERS", raising=False)


# The scrub above deletes HEADROOM_WORKSPACE_DIR / HEADROOM_CONFIG_DIR, which
# means every helper in `headroom.paths` (workspace_dir(), config_dir(),
# proxy_log_path(), savings_events_path(), memory_db_path(), ...) falls back to
# deriving from `Path.home()` -- and so does every one of the ~56 call sites
# elsewhere in the codebase (headroom/install/paths.py, cli/wrap.py,
# install/supervisors.py, binaries.py, learn/writer.py, ...) that call
# `Path.home()` directly instead of going through `headroom.paths`. On a
# developer machine that real home is the actual `~/.headroom` the live
# dashboard reads. Observed a full local run appending hundreds of fake proxy
# start-up lines to `~/.headroom/logs/proxy.log` and fake savings rows to
# `~/.headroom/savings_events.jsonl` / `proxy_savings.json`.
#
# Redirect HOME (and its Windows equivalent) plus both canonical roots at
# fresh, already-created sub-directories of their own dedicated tmp tree
# (via `tmp_path_factory`, *not* the test's own `tmp_path`) so every one of
# those resolution paths -- the `headroom.paths` helpers *and* the raw
# `Path.home()` call sites -- lands somewhere disposable without leaking
# into a test's own `tmp_path` contents (a test asserting on
# `tmp_path.iterdir()` must see only what it wrote, not our isolation
# scaffolding). Depends on the scrub fixture (declared as a fixture arg, not
# just declaration order) so this always runs after HEADROOM_* is cleared
# and gets the last word.
@pytest.fixture(autouse=True)
def _isolate_headroom_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
    _scrub_developer_headroom_env: None,
) -> None:
    base = tmp_path_factory.mktemp("headroom-isolated-home")
    fake_home = base / "fake-home"
    fake_workspace = base / "fake-workspace"
    fake_config = base / "fake-config"
    for directory in (fake_home, fake_workspace, fake_config):
        directory.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(fake_home))
    # `Path.home()` reads `USERPROFILE` first on Windows, then `HOME`.
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("HEADROOM_WORKSPACE_DIR", str(fake_workspace))
    monkeypatch.setenv("HEADROOM_CONFIG_DIR", str(fake_config))


_REAL_SERVICE_MANAGER_COMMANDS = frozenset({"launchctl", "systemctl", "sc.exe"})


def _real_service_manager_command_name(command: object) -> str | None:
    """Return the lowercase basename of *command*'s argv[0], if any.

    ``command`` is whatever was handed to ``subprocess.run`` -- either an
    argv list/tuple (the common case) or, for the Windows ``sc.exe create``
    call, a single pre-quoted string (see `headroom/install/supervisors.py`).
    """
    head: object
    if isinstance(command, (list, tuple)) and command:
        head = command[0]
    elif isinstance(command, str) and command.strip():
        head = command.split()[0]
    else:
        return None
    return str(head).replace("\\", "/").rsplit("/", 1)[-1].lower()


def _is_real_service_manager_command(command: object) -> bool:
    return _real_service_manager_command_name(command) in _REAL_SERVICE_MANAGER_COMMANDS


def _raise_real_service_manager_blocked() -> None:
    raise AssertionError(
        "test tried to touch the real service manager. A production headroom "
        "proxy was taken offline for 8 hours (2026-09) by tests that let a "
        "`launchctl bootout gui/501/com.headroom.default` reach the real OS "
        "instead of a mock. File-path isolation (see `_isolate_headroom_home` "
        "above) does not stop this -- it only isolates *file* paths, not "
        "*process/service* control. If a test genuinely needs the real "
        "service manager or to send a real process signal, opt in explicitly "
        "with `@pytest.mark.allow_real_service_manager` or by requesting the "
        "`allow_real_service_manager` fixture."
    )


class _RealOsKillGuardProxy:
    """Stands in for the ``os`` module inside a single module's namespace.

    Delegates every attribute except ``kill`` to the real ``os`` module, so
    existing tests that do e.g.
    ``monkeypatch.setattr("headroom.install.runtime.os.getuid", ...)`` keep
    working unchanged, while ``headroom.install.runtime.os.kill(...)`` (the
    call that sends `SIGTERM` to stop a running proxy) is guarded. Scoped to
    one module's ``os`` name rather than the real ``os`` module itself, since
    `os.kill` is also used, legitimately, by unrelated code
    (`headroom/cli/wrap.py`) and unrelated tests that clean up their own
    spawned child processes -- patching the real module would break those.
    """

    def __init__(self, real_os_module: Any) -> None:
        object.__setattr__(self, "_real_os_module", real_os_module)

    def kill(self, pid: int, sig: int) -> None:
        _raise_real_service_manager_blocked()

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_real_os_module"), name)


# 2026-09: two install tests called the REAL `launchctl bootout
# gui/501/com.headroom.default` and took the live proxy down for 8 hours.
# `_isolate_headroom_home` above only redirects *file* paths -- it cannot
# stop a test from shelling out to the real launchd/systemd/Windows SCM, or
# from sending a real SIGTERM to a real pid. Guard those entry points too.
#
# `headroom.install.supervisors` and `headroom.install.runtime` both reach
# launchctl/systemctl/sc.exe through the *same* underlying `subprocess.run`
# (either directly, or via `headroom._subprocess.run`'s `subprocess.run(...)`
# call) -- so patching the real `subprocess.run` here, content-filtered to
# only the three service-manager binaries, silently protects every call
# path without needing to know which of the two ways a given call site
# reaches it. Every existing test in tests/test_install/test_supervisors.py
# and tests/test_install/test_runtime.py already stubs out
# ``subprocess.run``/``os.kill`` itself before exercising these code paths;
# that stub is installed *after* this fixture runs and simply replaces the
# guard for that one test (same shared `monkeypatch` instance, last write
# wins) -- this guard only ever fires for a call that nobody has mocked.
#
# `os.kill` cannot be guarded the same way: it is real, unrelated,
# legitimate cleanup code elsewhere (`headroom/cli/wrap.py`, several e2e/wrap
# tests) that kills real child processes those tests spawned themselves.
# Patching the real `os.kill` globally would break all of that, so it is
# guarded only inside `headroom.install.runtime`'s own namespace, where its
# one call site (the `SIGTERM` in `stop_runtime`) actually lives.
@pytest.fixture(autouse=True)
def _guard_real_service_manager(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    if "allow_real_service_manager" in request.fixturenames:
        return
    if request.node.get_closest_marker("allow_real_service_manager") is not None:
        return

    import subprocess as real_subprocess

    try:
        from headroom.install import runtime as _install_runtime
    except ModuleNotFoundError:
        # Same reasoning as `_skip_proxy_dependency_gate_unless_exercised`:
        # native-wrapper jobs install only pytest, without Headroom's
        # runtime dependencies, and never exercise this code path.
        return

    real_subprocess_run = real_subprocess.run

    def _guarded_subprocess_run(command: Any, *args: Any, **kwargs: Any) -> Any:
        if _is_real_service_manager_command(command):
            _raise_real_service_manager_blocked()
        return real_subprocess_run(command, *args, **kwargs)

    monkeypatch.setattr(real_subprocess, "run", _guarded_subprocess_run)
    monkeypatch.setattr(_install_runtime, "os", _RealOsKillGuardProxy(_install_runtime.os))


@pytest.fixture
def allow_real_service_manager() -> None:
    """Opt-in escape hatch for a test that must legitimately reach the real
    service manager or send a real process signal.

    Requesting this fixture (or marking the test
    ``@pytest.mark.allow_real_service_manager``) disables
    `_guard_real_service_manager` above for that one test. This should be
    exceedingly rare -- it is exactly the gap that took the production proxy
    down for 8 hours -- so any use of it should be reviewed carefully.
    """
    return None


_GUARDED_PROXY_PORT = 8787
_GUARDED_PROXY_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _is_guarded_proxy_address(address: tuple[Any, ...] | str | bytes) -> bool:
    """True when *address* names the developer's real, live proxy port.

    Only ever matches that one well-known port -- tests routinely bind their
    own ephemeral-port servers (httpx/respx test servers, local echo
    servers, ...) and those must keep working untouched.
    """
    if not isinstance(address, tuple) or len(address) < 2:
        return False
    host, port = address[0], address[1]
    return port == _GUARDED_PROXY_PORT and str(host) in _GUARDED_PROXY_HOSTS


def _raise_real_proxy_network_blocked() -> None:
    raise ConnectionRefusedError(
        f"test tried to open a real TCP connection to 127.0.0.1:{_GUARDED_PROXY_PORT} "
        "-- the developer's live Headroom proxy listens there. "
        "headroom/cli/wrap.py's _check_proxy() / "
        "headroom/providers/copilot/wrap.py's query_proxy_config() do exactly "
        "this over a real socket (HOME/env-var isolation cannot help here -- "
        "the port itself is real regardless of HOME), read the live proxy's "
        "own pid out of its /health payload, and hand that real pid to "
        "_kill_proxy_by_pid() -> os.kill(pid, SIGTERM). This SIGTERM'd the "
        "developer's live proxy eight times in one test run (2026-09-05: "
        "_proxy_needs_version_restart()/_detect_running_proxy_backend() ->"
        " _kill_proxy_by_pid(), headroom/cli/wrap.py:3647/3663). If a test "
        "genuinely needs to probe or restart a real local proxy, opt in "
        "explicitly with `@pytest.mark.allow_real_service_manager` or the "
        "`allow_real_service_manager` fixture, and bind that proxy on a port "
        "other than 8787."
    )


def _raise_real_process_signal_blocked(pid: int) -> None:
    raise AssertionError(
        f"test tried to signal a process it did not start: pid {pid}. "
        "headroom/cli/wrap.py::_kill_proxy_by_pid and headroom/install/"
        "runtime.py's stop path both call os.kill(pid, SIGTERM/SIGKILL) on a "
        "pid read from a real, already-running process (the live proxy's own "
        "/health payload, or a pid file) rather than one this test spawned "
        "itself -- this SIGTERM'd the developer's live proxy eight times in "
        "one test run (2026-09-05). If a test genuinely needs to signal a "
        "real, non-test-owned process, opt in explicitly with "
        "`@pytest.mark.allow_real_service_manager` or the "
        "`allow_real_service_manager` fixture."
    )


# 2026-09-05 follow-up to the incident above: even with `_guard_real_service_manager`
# in place, a suite run in a worktree that already had both guard commits still
# SIGTERM'd the real, live dev proxy on port 8787 eight times. Root cause: that
# guard only patches `os.kill` inside `headroom.install.runtime`'s own module
# namespace (see `_RealOsKillGuardProxy` above) -- it does nothing for
# `headroom/cli/wrap.py`, which imports the real `os` module directly and
# reaches a REAL pid by a REAL network round-trip: `_check_proxy()` opens a
# real socket to 127.0.0.1:8787, `query_proxy_config()` GETs its real
# `/health` payload (which includes the real proxy's own pid), and
# `_kill_proxy_by_pid()` sends that real pid a real `os.kill(pid, SIGTERM)`
# (wrap.py:3647) then `os.kill(pid, SIGKILL)` (wrap.py:3663) if that doesn't
# work. File-path isolation cannot help: the port is real no matter what HOME
# points at.
#
# Two hard rules below, both autouse, both opt-out only via the existing
# `allow_real_service_manager` marker/fixture:
#
# 1. `os.kill`/`os.killpg` may only signal a pid this test itself spawned
#    (tracked by wrapping the real `subprocess.Popen.__init__`, which every
#    one of `subprocess.run`/`.check_call`/`.check_output`/`asyncio`
#    subprocess transports goes through, and the real
#    `multiprocessing.process.BaseProcess.start`, which every
#    `multiprocessing.Process`/`ProcessPoolExecutor` worker goes through) or
#    the test process's own pid. `os.kill(pid, 0)` is exempt: POSIX signal 0
#    never actually signals anything -- it is the standard "is this pid
#    alive" liveness probe, used legitimately throughout the suite
#    (`headroom._subprocess.pid_alive`'s fallback path) against pids the
#    test did not spawn, and blocking it would break that check for no
#    safety benefit.
#    (2026-09-05, same day: a full-suite run surfaced a real false positive
#    here -- `tests/test_image_compression_isolation.py::
#    test_worker_sigsegv_fails_open_parent_survives` deliberately crashes a
#    `ProcessPoolExecutor` worker, and the executor's own internal cleanup
#    thread called `os.kill`/`terminate()` on that worker's pid, which this
#    guard did not recognize because it only tracked `subprocess.Popen`
#    pids. Fixed by also tracking `multiprocessing.process.BaseProcess.start`
#    below.)
# 2. Opening a real TCP connection to 127.0.0.1 / localhost / ::1 port 8787 --
#    the developer's live Headroom proxy -- raises `ConnectionRefusedError`
#    instead of actually connecting. No other port is touched.
@pytest.fixture(autouse=True)
def _guard_real_process_signals_and_proxy_network(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    if "allow_real_service_manager" in request.fixturenames:
        return
    if request.node.get_closest_marker("allow_real_service_manager") is not None:
        return

    import multiprocessing.process as real_mp_process
    import socket as real_socket
    import subprocess as real_subprocess

    allowed_pids = {os.getpid()}

    real_popen_init = real_subprocess.Popen.__init__

    def _tracking_popen_init(self: Any, *args: Any, **kwargs: Any) -> None:
        real_popen_init(self, *args, **kwargs)
        allowed_pids.add(self.pid)

    monkeypatch.setattr(real_subprocess.Popen, "__init__", _tracking_popen_init)

    real_process_start = real_mp_process.BaseProcess.start

    def _tracking_process_start(self: Any) -> None:
        real_process_start(self)
        if self.pid is not None:
            allowed_pids.add(self.pid)

    monkeypatch.setattr(real_mp_process.BaseProcess, "start", _tracking_process_start)

    real_os_kill = os.kill

    def _guarded_kill(pid: int, sig: int) -> None:
        if sig != 0 and pid not in allowed_pids:
            _raise_real_process_signal_blocked(pid)
        real_os_kill(pid, sig)

    monkeypatch.setattr(os, "kill", _guarded_kill)

    if hasattr(os, "killpg"):
        real_os_killpg = os.killpg

        def _guarded_killpg(pgid: int, sig: int) -> None:
            if sig != 0 and pgid not in allowed_pids:
                _raise_real_process_signal_blocked(pgid)
            real_os_killpg(pgid, sig)

        monkeypatch.setattr(os, "killpg", _guarded_killpg)

    real_connect = real_socket.socket.connect
    real_connect_ex = real_socket.socket.connect_ex

    def _guarded_connect(self: Any, address: tuple[Any, ...] | str | bytes) -> None:
        if _is_guarded_proxy_address(address):
            _raise_real_proxy_network_blocked()
        real_connect(self, address)

    def _guarded_connect_ex(self: Any, address: tuple[Any, ...] | str | bytes) -> int:
        if _is_guarded_proxy_address(address):
            _raise_real_proxy_network_blocked()
        return real_connect_ex(self, address)

    monkeypatch.setattr(real_socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(real_socket.socket, "connect_ex", _guarded_connect_ex)
    return None


# The scrub above deletes every HEADROOM_* var — which includes HEADROOM_BEACON,
# and the beacon defaults to ON. So scrubbing for hermeticity is precisely what
# switches it on, and with HEADROOM_TELEMETRY_ENDPOINT scrubbed too it falls back
# to the real production endpoint. Every test that reaches the outcome funnel
# then POSTs a session event for real: observed writing into the live corpus
# during a local run, and CI would do the same on every push.
#
# Depends on the scrub fixture so it is guaranteed to run after it rather than
# relying on declaration order. A test that wants the beacon on just sets the
# var itself — monkeypatch inside the test wins over this.
@pytest.fixture(autouse=True)
def _disable_telemetry_beacon(
    monkeypatch: pytest.MonkeyPatch, _scrub_developer_headroom_env: None
) -> None:
    monkeypatch.setenv("HEADROOM_BEACON", "off")


# The MCP install ledger defaults to ``~/.headroom/mcp_installs.json``, so any
# test that registers a server (directly or through `wrap`) writes into
# whatever workspace_dir() currently resolves to — observed adding a live
# `claude/serena` entry to the developer's REAL ledger during a local run
# before `_isolate_headroom_home` above existed. That fixture now points
# workspace_dir() at a tmp path for every test, but a test can still legally
# monkeypatch HEADROOM_WORKSPACE_DIR/HOME back off for its own purposes, so
# redirect the ledger independently rather than relying on that. Every writer
# (`record_install` / `clear_install` / `headroom_installed_matching`) resolves
# it through this module-global, so one patch covers them all. Patched here
# rather than pointing workspace_dir() at a tmp path, which would break the
# tests that assert the default workspace layout.
@pytest.fixture(autouse=True)
def _isolate_mcp_ledger(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    # Same guard as _reset_copilot_routing_flag below: the macos/windows-native-
    # wrapper CI jobs install only pytest and drive the installer shell scripts
    # via subprocess, so headroom isn't importable and there is no ledger to
    # redirect. Skip there instead of erroring at setup.
    try:
        from headroom.mcp_registry import ledger
    except ModuleNotFoundError:
        return

    ledger_file = tmp_path_factory.mktemp("mcp-ledger") / "mcp_installs.json"
    monkeypatch.setattr(ledger, "ledger_path", lambda: ledger_file)


# The Copilot "routed to Copilot" flag is a module-global ContextVar that
# build_copilot_upstream_url() sets as a side effect. Unit tests that call that
# builder directly (or otherwise run in the shared root context) would leave it
# set and mislabel a later test's request outcome as "copilot". Reset it around
# every test so build-time side effects can't leak between tests.
@pytest.fixture(autouse=True)
def _reset_copilot_routing_flag() -> Generator[None, None, None]:
    # The macos/windows-native-wrapper CI jobs run the installer tests with only
    # pytest installed (no headroom): they drive the installer shell scripts via
    # subprocess, so headroom isn't importable and there's no routing flag to
    # reset. Skip the reset there instead of erroring at setup.
    try:
        from headroom.copilot_auth import reset_request_routed_to_copilot
    except ModuleNotFoundError:
        yield
        return

    reset_request_routed_to_copilot()
    yield
    reset_request_routed_to_copilot()


# `savings_tracker._resolve_litellm_model` is an `lru_cache`d, module-global,
# process-lifetime cache keyed by model name (bounded — see #2860). Many test
# files monkeypatch `savings_tracker.litellm` to a fake with different
# `model_cost`/`cost_per_token` behavior per test, but reuse common model
# names like "gpt-4o" across them. Without a reset, whichever test resolves
# "gpt-4o" first "wins" the cache entry for the rest of the run, and later
# tests silently stop exercising their own fake — a real-not-hypothetical
# order-dependence bug once the cache is process-lifetime instead of per-call.
# Clear before AND after so a test's own within-test resolutions never leak
# in from, or leak out to, a neighboring test either.
@pytest.fixture(autouse=True)
def _reset_litellm_model_resolution_cache() -> Generator[None, None, None]:
    try:
        from headroom.proxy.savings_tracker import _resolve_litellm_model
    except ModuleNotFoundError:
        yield
        return

    _resolve_litellm_model.cache_clear()
    yield
    _resolve_litellm_model.cache_clear()


# `kompress_compressor._kompress_cache` is a process-lifetime, module-global
# dict keyed by HF model id. Any test that builds a real, un-mocked
# `ContentRouter` (``enable_kompress=True`` by default) and compresses content
# above the word floor reaches `_try_ml_compressor`, which -- when the model
# isn't cached yet -- calls `compressor.ensure_background_load()`. That starts
# a real daemon thread that loads the actual Kompress model from the local
# HuggingFace cache (no network needed once it's on disk) and writes it into
# `_kompress_cache` a few seconds later, with no join and nothing to revert
# it. The triggering test (e.g.
# tests/test_garbled_compression_fixes.py::test_harness_banner_survives_router_compression_byte_intact
# and ::test_mixed_table_render_is_not_a_quoted_json_string_blob) passes
# immediately, long before that thread finishes, so nothing in it looks
# wrong -- but whichever later test happens to run once the thread completes
# then observes a genuinely-loaded model, order-dependently, purely based on
# elapsed wall-clock time. That broke tests/test_proxy_health.py's `/readyz`
# "kompress not ready" assertions. `_reset_kompress_cache_for_test` joins any
# in-flight download thread (clearing the cache alone would not stop one
# already loading the model) before clearing all Kompress process state.
@pytest.fixture(autouse=True)
def _reset_kompress_cache_for_test() -> Generator[None, None, None]:
    try:
        from headroom.transforms.kompress_compressor import (
            _reset_kompress_cache_for_test as _reset,
        )
    except ModuleNotFoundError:
        yield
        return

    _reset()
    yield
    _reset()


# =============================================================================
# Global test hooks
# =============================================================================


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, "Result[None]", None]:
    """Wrap test execution to skip transient or offline external model failures.

    This handles model-loading failures that occur when:
    - HuggingFace Hub is slow during model downloads (sentence-transformers)
    - Required HuggingFace model files were not restored into the offline CI cache
    - External embedding APIs timeout
    - Network connectivity issues in CI
    """
    outcome = yield

    if outcome.excinfo is not None:
        exc_type, exc_value, exc_tb = outcome.excinfo
        reason = external_model_skip_reason(exc_value)
        if reason is not None:
            pytest.skip(reason)


@pytest.fixture(autouse=True)
def _null_binary_pins() -> Generator[None, None, None]:
    """Null the tools.json SHA-256 pins during tests.

    Installer tests fetch small mock archives, whose digests can't match the
    real published pins. Nulling the pins lets those download/extract mechanics
    tests run (verification then falls back to HTTPS trust); the tests that
    specifically exercise verification set their own pin explicitly. Production
    keeps the real pins (this fixture is test-only) and the tools-hash-refresh
    CI gate guarantees they stay correct.
    """
    try:
        from headroom import binaries
    except Exception:
        # Lean CI environments (e.g. the native-installer jobs) omit heavy deps
        # such as opentelemetry that importing `binaries` pulls in. There are no
        # tool pins to null there, so skip cleanly rather than erroring at setup.
        yield
        return

    saved = [
        (asset, asset.get("sha256"))
        for tool in binaries._registry().get("tools", {}).values()
        for asset in tool.get("assets", {}).values()
    ]
    for asset, _original in saved:
        asset["sha256"] = None
    yield
    for asset, original in saved:
        asset["sha256"] = original


@pytest.fixture(autouse=True)
def _reset_headroom_logger_propagation() -> Generator[None, None, None]:
    """Keep `headroom.*` log records flowing to pytest's caplog handler.

    Two sources disable propagation on the headroom logger tree and never
    restore it, which then makes later `caplog`-based assertions flaky in
    full-suite runs (caplog attaches to root, so a `propagate=False` anywhere
    on the chain silently drops the records):

    - ``headroom.proxy.helpers._setup_file_logging`` sets
      ``getLogger("headroom").propagate = False`` on proxy startup.
    - ``benchmarks.claude_session_mode_benchmark._disable_headroom_benchmark_logging``
      (exercised by ``test_claude_session_mode_benchmark``) sets
      ``propagate = False`` + ``CRITICAL`` on ``headroom``, ``headroom.proxy``,
      ``headroom.transforms``, ``headroom.cache`` (and children).

    Resetting only ``"headroom"`` is not enough — a child like
    ``"headroom.proxy"`` left non-propagating blocks the record before it
    reaches root. Reset the whole subtree before every test so capture is
    deterministic regardless of run order.
    """
    import logging as _logging

    for _name in ("headroom", *list(_logging.root.manager.loggerDict)):
        if _name == "headroom" or _name.startswith("headroom."):
            logger = _logging.getLogger(_name)
            logger.disabled = False
            # The benchmark also raises the level to CRITICAL; children
            # inherit it (effective level), so a WARNING would be filtered
            # at the logger before it can propagate to caplog. Reset to
            # NOTSET so the subtree inherits root's level deterministically.
            logger.setLevel(_logging.NOTSET)
            logger.propagate = True
    yield


@pytest.fixture(autouse=True)
def _restore_warnings_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force `warnings.warn` back to the real implementation before every test.

    Some optional dependencies (e.g. `crewai`) monkeypatch `warnings.warn`
    globally at import time with a narrower signature that drops newer
    keyword arguments (like `skip_file_prefixes`), and never undo it. Once
    any test's collection imports one of those, `warnings.warn` stays broken
    for the rest of the pytest session and breaks unrelated code (e.g.
    `htmldate`'s own `warnings.warn(..., skip_file_prefixes=...)` call inside
    `_strptime.py`). Resetting it here, before every test, means no test's
    import order can poison another test.
    """
    monkeypatch.setattr(warnings, "warn", _ORIGINAL_WARNINGS_WARN)


# =============================================================================
# Sample messages fixtures
# =============================================================================


# Sample messages fixtures
@pytest.fixture
def sample_messages() -> list[dict[str, Any]]:
    """Basic conversation messages."""
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello, how are you?"},
        {"role": "assistant", "content": "I'm doing well, thank you!"},
    ]


@pytest.fixture
def sample_messages_with_tools() -> list[dict[str, Any]]:
    """Conversation with tool calls and responses."""
    return [
        {"role": "system", "content": "You are a helpful assistant with tools."},
        {"role": "user", "content": "Search for user 12345"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "search_user", "arguments": '{"user_id": "12345"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_123",
            "content": '{"id": "12345", "name": "Alice", "email": "alice@example.com"}',
        },
        {"role": "assistant", "content": "I found user Alice with ID 12345."},
    ]


@pytest.fixture
def sample_tool_output_large() -> str:
    """Large tool output for compression testing (100 items)."""
    return json.dumps(
        [
            {
                "id": i,
                "name": f"Item {i}",
                "score": i * 0.1,
                "status": "active" if i % 2 == 0 else "inactive",
            }
            for i in range(100)
        ]
    )


@pytest.fixture
def sample_tool_output_with_errors() -> str:
    """Tool output containing error items."""
    items = [{"id": i, "status": "success"} for i in range(20)]
    items[5] = {"id": 5, "status": "error", "message": "Connection refused"}
    items[15] = {"id": 15, "status": "failed", "exception": "TimeoutError"}
    return json.dumps(items)


@pytest.fixture
def sample_system_prompt_with_date() -> str:
    """System prompt containing dynamic date."""
    return "You are a helpful assistant. Current date: 2025-01-06. Help the user with their tasks."


@pytest.fixture
def sample_anthropic_messages() -> list[dict[str, Any]]:
    """Anthropic-style messages with content blocks."""
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Analyze this image"},
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": "..."},
                },
            ],
        }
    ]


# Mock client fixtures
@pytest.fixture
def mock_openai_response() -> Mock:
    """Mock OpenAI API response."""
    mock = Mock()
    mock.id = "chatcmpl-123"
    mock.model = "gpt-4o"
    mock.usage = Mock()
    mock.usage.prompt_tokens = 100
    mock.usage.completion_tokens = 50
    mock.usage.total_tokens = 150
    mock.choices = [Mock()]
    mock.choices[0].message = Mock()
    mock.choices[0].message.content = "This is a response."
    mock.choices[0].message.role = "assistant"
    mock.choices[0].finish_reason = "stop"
    return mock


@pytest.fixture
def mock_openai_client(mock_openai_response: Mock) -> Mock:
    """Mock OpenAI client."""
    client = Mock()
    client.chat = Mock()
    client.chat.completions = Mock()
    client.chat.completions.create = Mock(return_value=mock_openai_response)
    return client


# Storage fixtures
@pytest.fixture
def temp_sqlite_db() -> Generator[str, None, None]:
    """Temporary SQLite database path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        yield f.name
    Path(f.name).unlink(missing_ok=True)


@pytest.fixture
def temp_jsonl_file() -> Generator[str, None, None]:
    """Temporary JSONL file path."""
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        yield f.name
    Path(f.name).unlink(missing_ok=True)


# Provider fixtures
@pytest.fixture
def openai_provider() -> "OpenAIProvider":
    """OpenAI provider instance."""
    from headroom.providers.openai import OpenAIProvider

    return OpenAIProvider()


@pytest.fixture
def openai_tokenizer() -> "OpenAITokenCounter":
    """OpenAI token counter for gpt-4o."""
    from headroom.providers.openai import OpenAITokenCounter

    return OpenAITokenCounter("gpt-4o")


# Config fixtures
@pytest.fixture
def default_config() -> "HeadroomConfig":
    """Default HeadroomConfig."""
    from headroom.config import HeadroomConfig

    return HeadroomConfig()


@pytest.fixture
def smart_crusher_config() -> "SmartCrusherConfig":
    """SmartCrusher config for testing."""
    from headroom.config import SmartCrusherConfig

    return SmartCrusherConfig(
        enabled=True,
        min_items_to_analyze=3,
        min_tokens_to_crush=0,  # Always crush for tests
        max_items_after_crush=10,
    )


# Helper for creating RequestMetrics
@pytest.fixture
def sample_request_metrics() -> "RequestMetrics":
    """Sample RequestMetrics for storage tests."""
    from headroom.config import RequestMetrics

    return RequestMetrics(
        request_id="test-123",
        timestamp=datetime(2025, 1, 6, 12, 0, 0),
        model="gpt-4o",
        stream=False,
        mode="audit",
        tokens_input_before=1000,
        tokens_input_after=800,
        tokens_output=200,
        block_breakdown={"system": 100, "user": 200, "assistant": 500},
        waste_signals={"json_bloat": 50},
        stable_prefix_hash="abc123",
        cache_alignment_score=85.0,
        cached_tokens=100,
        transforms_applied=["CacheAligner", "SmartCrusher"],
        tool_units_dropped=1,
        turns_dropped=0,
        messages_hash="def456",
    )
