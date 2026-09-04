"""The proxy raises its own open-file limit at startup.

launchd starts user agents with a 256 soft file-descriptor cap. A proxy that
holds hundreds of keep-alive upstream connections for twenty parallel Claude
sessions hits that cap and starts failing connects, so the server lifts the
soft limit toward the hard limit before it begins serving.
"""

from __future__ import annotations

import resource

from headroom.proxy.server import OPEN_FILE_TARGET, raise_open_file_limit


def test_raises_soft_limit_toward_target(monkeypatch) -> None:
    calls: list[tuple[int, tuple[int, int]]] = []
    monkeypatch.setattr(resource, "getrlimit", lambda _kind: (256, 1_000_000))
    monkeypatch.setattr(resource, "setrlimit", lambda kind, val: calls.append((kind, val)))

    assert raise_open_file_limit() == OPEN_FILE_TARGET
    assert calls == [(resource.RLIMIT_NOFILE, (OPEN_FILE_TARGET, 1_000_000))]


def test_caps_at_hard_limit_when_hard_is_lower(monkeypatch) -> None:
    calls: list[tuple[int, tuple[int, int]]] = []
    monkeypatch.setattr(resource, "getrlimit", lambda _kind: (256, 4096))
    monkeypatch.setattr(resource, "setrlimit", lambda kind, val: calls.append((kind, val)))

    assert raise_open_file_limit() == 4096
    assert calls == [(resource.RLIMIT_NOFILE, (4096, 4096))]


def test_leaves_a_higher_soft_limit_alone(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr(resource, "getrlimit", lambda _kind: (200_000, 200_000))
    monkeypatch.setattr(resource, "setrlimit", lambda kind, val: calls.append((kind, val)))

    assert raise_open_file_limit() == 200_000
    assert calls == []


def test_setrlimit_failure_is_not_fatal(monkeypatch) -> None:
    monkeypatch.setattr(resource, "getrlimit", lambda _kind: (256, 1_000_000))

    def boom(_kind, _val):
        raise ValueError("not permitted")

    monkeypatch.setattr(resource, "setrlimit", boom)
    assert raise_open_file_limit() == 256
