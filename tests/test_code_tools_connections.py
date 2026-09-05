"""Tests for headroom.code_tools.connections -- names on disk, secrets in a keychain."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from headroom import paths
from headroom.code_tools import connections


@pytest.fixture
def config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv(paths.HEADROOM_CONFIG_DIR_ENV, str(tmp_path))
    return tmp_path


def test_add_resolve_remove_round_trip_and_no_url_on_disk(config_dir: Path) -> None:
    store = connections.MemoryKeychain()

    connections.add_connection("mydb", "postgresql://alice:secret@localhost:5432/app", store)

    assert connections.list_connections() == ["mydb"]
    assert (
        connections.resolve_connection("mydb", store)
        == "postgresql://alice:secret@localhost:5432/app"
    )

    config_path = paths.connections_path()
    assert config_path.exists()
    on_disk = config_path.read_text(encoding="utf-8")
    assert "secret" not in on_disk
    assert "postgresql://" not in on_disk

    connections.remove_connection("mydb", store)

    assert connections.list_connections() == []
    with pytest.raises(KeyError):
        connections.resolve_connection("mydb", store)


def test_describe_unknown_lists_known_connections_and_the_add_command(
    config_dir: Path,
) -> None:
    store = connections.MemoryKeychain()
    connections.add_connection("mydb", "postgresql://alice:secret@localhost:5432/app", store)

    message = connections.describe_unknown("nope")

    assert message == (
        "no connection reference named 'nope'. Known connections: mydb. "
        "Add one with `headroom code-agent db add`."
    )


def test_describe_unknown_with_no_connections_configured(config_dir: Path) -> None:
    message = connections.describe_unknown("nope")

    assert message == (
        "no connection reference named 'nope'. No connections are configured. "
        "Add one with `headroom code-agent db add`."
    )


def test_macos_keychain_builds_expected_argv_without_touching_real_keychain() -> None:
    calls: list[list[str]] = []

    class FakeRunner:
        def __call__(self, command: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append(command)
            return SimpleNamespace(returncode=0, stdout="sekret\n")

    keychain = connections.MacOSKeychain(runner=FakeRunner())

    keychain.set_secret("headroom-code-agent", "mydb", "postgresql://u:p@h/db")
    assert keychain.get_secret("headroom-code-agent", "mydb") == "sekret"
    keychain.delete_secret("headroom-code-agent", "mydb")

    assert calls == [
        [
            "security",
            "add-generic-password",
            "-U",
            "-s",
            "headroom-code-agent",
            "-a",
            "mydb",
            "-w",
            "postgresql://u:p@h/db",
        ],
        [
            "security",
            "find-generic-password",
            "-s",
            "headroom-code-agent",
            "-a",
            "mydb",
            "-w",
        ],
        [
            "security",
            "delete-generic-password",
            "-s",
            "headroom-code-agent",
            "-a",
            "mydb",
        ],
    ]
