"""Shared pytest fixtures for Headroom tests."""

# CRITICAL: Must be set before ANY imports that could trigger sentence_transformers
# The Rust tokenizers use parallelism that deadlocks with pytest-asyncio
import os

os.environ["TOKENIZERS_PARALLELISM"] = "false"

import json
import tempfile
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
# fresh, already-created sub-directories of `tmp_path` so every one of those
# resolution paths -- the `headroom.paths` helpers *and* the raw
# `Path.home()` call sites -- lands somewhere disposable. Depends on the
# scrub fixture (declared as a fixture arg, not just declaration order) so
# this always runs after HEADROOM_* is cleared and gets the last word.
@pytest.fixture(autouse=True)
def _isolate_headroom_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _scrub_developer_headroom_env: None
) -> None:
    fake_home = tmp_path / "fake-home"
    fake_workspace = tmp_path / "fake-workspace"
    fake_config = tmp_path / "fake-config"
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
