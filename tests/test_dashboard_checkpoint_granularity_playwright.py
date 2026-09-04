"""Playwright validation for the Recent Historical Checkpoints granularity control.

The proxy writes a history checkpoint per request, so the newest eight raw
points are seconds apart and round to identical values on screen. The panel
therefore lets the user pick which series it lists (hourly by default) and
remembers the choice in the browser.
"""

from __future__ import annotations

import copy
import json
import re
from urllib.parse import urlsplit

import pytest

from headroom.dashboard import get_dashboard_html
from tests.test_dashboard_cache_lifetime_playwright import _lifetime_cache_payload
from tests.test_dashboard_cache_ttl_playwright import _fulfill_static_asset, _sample_stats

playwright = pytest.importorskip("playwright.sync_api")
Page = playwright.Page
expect = playwright.expect
sync_playwright = playwright.sync_playwright

STORAGE_KEY = "headroom-checkpoint-granularity"


def _point(ts: str, tokens: int, usd: float) -> dict:
    return {"timestamp": ts, "total_tokens_saved": tokens, "compression_savings_usd": usd}


def _history_payload() -> dict:
    """Ten per-request points seconds apart, plus three hourly and two daily rollups."""
    raw = [
        _point(f"2026-09-04T19:50:{sec:02d}Z", 2_000_000 + sec * 100, 85.5 + sec / 10_000)
        for sec in range(10, 40, 3)
    ]
    hourly = [
        {**_point("2026-09-04T17:00:00Z", 859_819, 33.48), "tokens_saved": 400_817},
        {**_point("2026-09-04T18:00:00Z", 1_626_554, 64.40), "tokens_saved": 766_735},
        {**_point("2026-09-04T19:00:00Z", 2_076_387, 87.59), "tokens_saved": 449_833},
    ]
    daily = [
        {**_point("2026-09-03T00:00:00Z", 459_002, 20.01), "tokens_saved": 459_002},
        {**_point("2026-09-04T00:00:00Z", 2_076_387, 87.59), "tokens_saved": 1_617_385},
    ]
    return {
        "history": raw,
        "series": {"hourly": hourly, "daily": daily, "weekly": [], "monthly": []},
        "lifetime": {"tokens_saved": 2_076_387, "compression_savings_usd": 87.59},
    }


def _install_routes(page: Page, history: dict) -> None:
    stats = copy.deepcopy(_sample_stats())
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


def _open_history_view(page: Page, history: dict) -> None:
    _install_routes(page, history)
    page.goto("http://headroom.local/dashboard")
    page.wait_for_load_state("networkidle")
    page.get_by_role("button", name="Historical", exact=True).click()
    expect(page.get_by_text("Recent Historical Checkpoints", exact=True)).to_be_visible()


def _checkpoint_panel(page: Page):  # type: ignore[no-untyped-def]
    return page.get_by_test_id("recent-checkpoints")


def test_panel_defaults_to_hourly_rollups() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1800})
        _open_history_view(page, _history_payload())

        panel = _checkpoint_panel(page)
        expect(panel.get_by_role("button", name="Hourly", exact=True)).to_have_class(
            re.compile(r"bg-accent")
        )
        # Three hourly rollups, not the ten raw per-request points.
        expect(panel.get_by_test_id("checkpoint-row")).to_have_count(3)
        expect(panel.get_by_text("$87.59", exact=True)).to_be_visible()
        expect(panel.get_by_text("$33.48", exact=True)).to_be_visible()

        browser.close()


def test_every_request_option_lists_raw_points() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1800})
        _open_history_view(page, _history_payload())

        panel = _checkpoint_panel(page)
        panel.get_by_role("button", name="Every request", exact=True).click()
        expect(panel.get_by_test_id("checkpoint-row")).to_have_count(8)

        browser.close()


def test_choice_is_remembered_across_reload() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1800})
        _open_history_view(page, _history_payload())

        _checkpoint_panel(page).get_by_role("button", name="Daily", exact=True).click()
        expect(_checkpoint_panel(page).get_by_test_id("checkpoint-row")).to_have_count(2)
        assert page.evaluate(f"localStorage.getItem('{STORAGE_KEY}')") == "daily"

        page.reload()
        page.wait_for_load_state("networkidle")
        page.get_by_role("button", name="Historical", exact=True).click()
        expect(_checkpoint_panel(page).get_by_test_id("checkpoint-row")).to_have_count(2)

        browser.close()


def test_empty_series_shows_explanation_instead_of_raw_points() -> None:
    history = _history_payload()
    history["series"]["hourly"] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1800})
        _open_history_view(page, history)

        panel = _checkpoint_panel(page)
        expect(panel.get_by_test_id("checkpoint-row")).to_have_count(0)
        expect(panel.get_by_text("No hourly rollups yet", exact=False)).to_be_visible()

        browser.close()


def test_daily_checkpoints_show_the_bucket_date_not_a_shifted_local_time() -> None:
    """Rollup buckets start at UTC midnight; showing them in local time moves them to the day before."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            timezone_id="America/Chicago", viewport={"width": 1440, "height": 1800}
        )
        page = context.new_page()
        _open_history_view(page, _history_payload())

        panel = _checkpoint_panel(page)
        panel.get_by_role("button", name="Daily", exact=True).click()
        rows = panel.get_by_test_id("checkpoint-row")
        expect(rows).to_have_count(2)
        expect(rows.first).to_contain_text("Sep 4")
        expect(rows.first).not_to_contain_text("Sep 3")
        expect(rows.first).not_to_contain_text("PM")

        browser.close()
