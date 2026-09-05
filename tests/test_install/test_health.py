from __future__ import annotations

import urllib.error
from types import TracebackType

import pytest

from headroom.install.health import probe_alive, probe_json, probe_ready


class _Response:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def test_probe_json_returns_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda url, timeout=2.0: _Response(b'{"ready": true}'),
    )

    assert probe_json("http://example.test") == {"ready": True}


def test_probe_json_returns_none_for_invalid_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=2.0: _Response(b"[]"))
    assert probe_json("http://example.test") is None

    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=2.0: _Response(b"{"))
    assert probe_json("http://example.test") is None

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda url, timeout=2.0: (_ for _ in ()).throw(urllib.error.URLError("boom")),
    )
    assert probe_json("http://example.test") is None


def test_probe_ready_accepts_ready_and_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "headroom.install.health.probe_json", lambda url, timeout=2.0: {"ready": True}
    )
    assert probe_ready("http://example.test")

    monkeypatch.setattr(
        "headroom.install.health.probe_json", lambda url, timeout=2.0: {"status": "healthy"}
    )
    assert probe_ready("http://example.test")

    monkeypatch.setattr("headroom.install.health.probe_json", lambda url, timeout=2.0: None)
    assert not probe_ready("http://example.test")


def test_probe_alive_true_when_health_endpoint_answers_regardless_of_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live proxy whose upstream is unreachable still answers /health with
    status "unhealthy" (server.py folds the cached upstream check into both
    /health and /readyz). probe_alive must say True anyway -- it only cares
    that a headroom proxy, not the upstream, is answering on the port."""

    seen_urls: list[str] = []

    def fake_probe_json(url: str, timeout: float = 2.0) -> dict[str, object] | None:
        seen_urls.append(url)
        return {"service": "headroom-proxy", "status": "unhealthy", "ready": False}

    monkeypatch.setattr("headroom.install.health.probe_json", fake_probe_json)

    assert probe_alive("http://127.0.0.1:8787/readyz")
    # The /readyz URL must be translated to /health before probing.
    assert seen_urls == ["http://127.0.0.1:8787/health"]


def test_probe_alive_false_on_connection_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("headroom.install.health.probe_json", lambda url, timeout=2.0: None)

    assert not probe_alive("http://127.0.0.1:8787/readyz")


def test_probe_alive_false_on_non_headroom_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 200 from something that isn't headroom (a stray dev server, etc.)
    must not be mistaken for a live proxy."""

    monkeypatch.setattr(
        "headroom.install.health.probe_json",
        lambda url, timeout=2.0: {"status": "ok", "service": "some-other-app"},
    )

    assert not probe_alive("http://127.0.0.1:8787/readyz")
