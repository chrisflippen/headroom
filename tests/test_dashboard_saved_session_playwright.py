"""Playwright validation for the saved-session strip on the Session view.

The proxy persists a display session (requests, tokens, cost) that survives
restarts and rolls over after an hour of inactivity. The Session tab used to
show only in-process counters, so every restart looked like lost data. It now
leads with the saved session and labels the process counters as such.
"""

from __future__ import annotations

import copy
import json
from urllib.parse import urlsplit

import pytest

from headroom.dashboard import get_dashboard_html
from tests.test_dashboard_cache_lifetime_playwright import _lifetime_cache_payload
from tests.test_dashboard_cache_ttl_playwright import (
    _fulfill_static_asset,
    _sample_history,
    _sample_stats,
)

playwright = pytest.importorskip("playwright.sync_api")
Page = playwright.Page
expect = playwright.expect
sync_playwright = playwright.sync_playwright


def _stats_with_saved_session() -> dict:
    stats = copy.deepcopy(_sample_stats())
    stats["display_session"] = {
        "requests": 1467,
        "tokens_saved": 1_929_769,
        "compression_savings_usd": 75.82,
        "cache_read_tokens": 149_438_590,
        "cache_savings_usd": 700.51,
        "total_input_tokens": 150_355_141,
        "total_input_cost_usd": 88.51,
        "savings_percent": 1.27,
        "started_at": "2026-09-04T17:38:55Z",
        "last_activity_at": "2026-09-04T20:18:46Z",
    }
    stats["display_session_policy"] = {"rollover_inactivity_minutes": 60}
    return stats


def _open_session_view(page: Page, stats: dict) -> None:
    history = _sample_history()
    lifetime = _lifetime_cache_payload()
    dashboard_html = get_dashboard_html()
    health = {"status": "healthy", "version": "0.37.0"}

    def handler(route) -> None:  # type: ignore[no-untyped-def]
        path = urlsplit(route.request.url).path
        if path in ("/dashboard", "/"):
            route.fulfill(status=200, content_type="text/html", body=dashboard_html)
            return
        if _fulfill_static_asset(route, path):
            return
        if "/stats-history" in path:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(history))
            return
        if "/stats-lifetime" in path:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(lifetime))
            return
        if "/health" in path:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(health))
            return
        if "/stats" in path:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(stats))
            return
        route.fulfill(status=200, content_type="application/json", body="{}")

    page.route("**/*", handler)
    page.goto("http://headroom.local/dashboard")
    page.wait_for_load_state("networkidle")
    page.get_by_role("button", name="Session", exact=True).click()


def test_session_view_leads_with_the_saved_session() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1800})
        _open_session_view(page, _stats_with_saved_session())

        strip = page.get_by_test_id("saved-session")
        expect(strip).to_be_visible()
        expect(strip.get_by_test_id("saved-session-requests")).to_have_text("1,467")
        expect(strip.get_by_test_id("saved-session-tokens")).to_have_text("1.9M")
        expect(strip.get_by_test_id("saved-session-compression-usd")).to_have_text("$75.82")
        expect(strip.get_by_test_id("saved-session-cache-usd")).to_have_text("$700.51")
        expect(strip.get_by_text("survives restarts", exact=False)).to_be_visible()
        expect(strip.get_by_text("1 hour idle", exact=False)).to_be_visible()
        # The process-only counters are now labelled as such, not as "the session".
        expect(page.get_by_text("This proxy process", exact=False)).to_be_visible()

        browser.close()


def test_strip_is_hidden_until_a_saved_session_exists() -> None:
    stats = copy.deepcopy(_sample_stats())
    stats["display_session"] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1800})
        _open_session_view(page, stats)

        expect(page.get_by_test_id("saved-session")).to_have_count(0)

        browser.close()
