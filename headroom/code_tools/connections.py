"""Connection references: a name and a keychain pointer on disk, never a secret.

The code agent's Sql tool never sees a raw connection URL or password. A
connection is added once under a short name; from then on the name is what
gets passed around. The name, the database kind, and a keychain lookup
pointer (service + account) live in a small JSON file under the config
directory. The URL itself -- which usually carries a password -- lives only
in a keychain, reached through a small adapter so tests never touch the
real macOS keychain.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from headroom import paths
from headroom._subprocess import run

KEYCHAIN_SERVICE = "headroom-code-agent"

_POSTGRES_SCHEMES = {"postgres", "postgresql"}
_SQLITE_SCHEMES = {"sqlite"}


class Keychain(Protocol):
    """A place to store and fetch one secret string per (service, account)."""

    def set_secret(self, service: str, account: str, secret: str) -> None: ...

    def get_secret(self, service: str, account: str) -> str | None: ...

    def delete_secret(self, service: str, account: str) -> None: ...


class MemoryKeychain:
    """An in-memory keychain for tests. Never touches the real keychain."""

    def __init__(self) -> None:
        self._secrets: dict[tuple[str, str], str] = {}

    def set_secret(self, service: str, account: str, secret: str) -> None:
        self._secrets[(service, account)] = secret

    def get_secret(self, service: str, account: str) -> str | None:
        return self._secrets.get((service, account))

    def delete_secret(self, service: str, account: str) -> None:
        self._secrets.pop((service, account), None)


class MacOSKeychain:
    """A keychain adapter that shells out to the macOS ``security`` CLI."""

    def __init__(self, runner: object = run) -> None:
        self._run = runner

    def set_secret(self, service: str, account: str, secret: str) -> None:
        self._run(  # type: ignore[operator]
            [
                "security",
                "add-generic-password",
                "-U",
                "-s",
                service,
                "-a",
                account,
                "-w",
                secret,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )

    def get_secret(self, service: str, account: str) -> str | None:
        result = self._run(  # type: ignore[operator]
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        secret = result.stdout.strip()
        return secret or None

    def delete_secret(self, service: str, account: str) -> None:
        self._run(  # type: ignore[operator]
            ["security", "delete-generic-password", "-s", service, "-a", account],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )


def _kind_from_url(url: str) -> str:
    scheme = urlparse(url).scheme.lower()
    if scheme in _POSTGRES_SCHEMES:
        return "postgres"
    if scheme in _SQLITE_SCHEMES:
        return "sqlite"
    raise ValueError(f"unsupported connection URL scheme: {scheme!r}")


def _config_path() -> Path:
    return paths.connections_path()


def _load_config() -> dict[str, dict[str, dict[str, str]]]:
    path = _config_path()
    if not path.exists():
        return {"connections": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"connections": {}}
    if not isinstance(data, dict) or not isinstance(data.get("connections"), dict):
        return {"connections": {}}
    return data


def _save_config(data: dict[str, dict[str, dict[str, str]]]) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def add_connection(name: str, url: str, store: Keychain) -> None:
    """Add a named connection: kind and a keychain pointer on disk, url in the keychain."""

    kind = _kind_from_url(url)
    store.set_secret(KEYCHAIN_SERVICE, name, url)
    data = _load_config()
    data["connections"][name] = {
        "kind": kind,
        "keychain_service": KEYCHAIN_SERVICE,
        "keychain_account": name,
    }
    _save_config(data)


def resolve_connection(name: str, store: Keychain) -> str:
    """Return the connection URL for a named connection, read from the keychain."""

    data = _load_config()
    entry = data["connections"].get(name)
    if entry is None:
        raise KeyError(f"no connection named {name!r}")
    url = store.get_secret(entry["keychain_service"], entry["keychain_account"])
    if url is None:
        raise KeyError(f"no secret in the keychain for connection {name!r}")
    return url


def remove_connection(name: str, store: Keychain) -> None:
    """Remove a named connection: drop it from disk and delete its keychain secret."""

    data = _load_config()
    entry = data["connections"].pop(name, None)
    _save_config(data)
    if entry is not None:
        store.delete_secret(entry["keychain_service"], entry["keychain_account"])


def list_connections() -> list[str]:
    """Return the names of every connection reference on disk, sorted."""

    data = _load_config()
    return sorted(data["connections"].keys())


__all__ = [
    "KEYCHAIN_SERVICE",
    "Keychain",
    "MemoryKeychain",
    "MacOSKeychain",
    "add_connection",
    "resolve_connection",
    "remove_connection",
    "list_connections",
]
