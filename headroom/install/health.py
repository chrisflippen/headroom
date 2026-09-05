"""Health helpers for persistent deployments."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


def probe_json(url: str, timeout: float = 2.0) -> dict[str, Any] | None:
    """Return a JSON payload from the URL when reachable."""

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def probe_ready(url: str, timeout: float = 2.0) -> bool:
    """Return True when the ready endpoint reports readiness."""

    payload = probe_json(url, timeout=timeout)
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("ready", False) or payload.get("status") == "healthy")


def probe_alive(health_url: str, timeout: float = 2.0) -> bool:
    """Return True when a headroom proxy is bound to the port at all.

    ``health_url`` is the manifest's ``/readyz`` URL; this probes the paired
    ``/health`` endpoint instead. Both endpoints fold in a cached upstream
    reachability check (see the proxy's server module), so their "ready" /
    "status" fields can legitimately say "unhealthy" while a live, correctly
    bound proxy is answering the port -- the upstream, not the proxy, is
    what's down. ``/health`` always answers with HTTP 200 regardless of that
    check, so it is the right endpoint to ask "is a proxy here at all?".

    Callers deciding whether to start a second proxy need exactly that
    question answered, not "can it currently reach the upstream" -- starting
    a second process when one is already alive just makes it fail to bind
    the port and exit immediately, which is the bug this probe exists to
    avoid. probe_alive therefore ignores "ready"/"status" entirely and only
    confirms the responder identifies itself as a headroom proxy.
    """

    liveness_url = health_url.replace("/readyz", "/health")
    payload = probe_json(liveness_url, timeout=timeout)
    if not isinstance(payload, dict):
        return False
    return payload.get("service") == "headroom-proxy"
