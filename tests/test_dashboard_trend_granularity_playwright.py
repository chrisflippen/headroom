"""Playwright validation for the Historical Savings Trend granularity control.

A fresh install has one daily rollup for a long time, so a chart that
defaults to daily sits empty while hourly rollups already exist. The chart
offers Hourly, starts on the finest series that can draw a line, and
remembers an explicit choice in the browser.
"""

from __future__ import annotations

import copy
import re

import pytest

from tests.test_dashboard_checkpoint_granularity_playwright import (
    _history_payload,
    _install_routes,
)

playwright = pytest.importorskip("playwright.sync_api")
Page = playwright.Page
expect = playwright.expect
sync_playwright = playwright.sync_playwright

STORAGE_KEY = "headroom-trend-granularity"
EMPTY_TEXT = "Historical trend data will appear"


def _fresh_install_payload() -> dict:
    """Nine hourly rollups but only one daily rollup, like day one of an install."""
    history = copy.deepcopy(_history_payload())
    history["series"]["daily"] = history["series"]["daily"][-1:]
    return history


def _open_history_view(page: Page, history: dict) -> None:
    _install_routes(page, history)
    page.goto("http://headroom.local/dashboard")
    page.wait_for_load_state("networkidle")
    page.get_by_role("button", name="Historical", exact=True).click()
    expect(page.get_by_text("Historical Savings Trend", exact=True)).to_be_visible()


def _trend_panel(page: Page):  # type: ignore[no-untyped-def]
    return page.get_by_test_id("savings-trend")


def test_trend_offers_hourly_and_starts_on_finest_series_with_a_line() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1800})
        _open_history_view(page, _fresh_install_payload())

        panel = _trend_panel(page)
        expect(panel.get_by_role("button", name="Hourly", exact=True)).to_have_class(
            re.compile(r"bg-accent")
        )
        expect(panel.get_by_text("Showing 3 hourly points", exact=True)).to_be_visible()
        expect(panel.get_by_text(EMPTY_TEXT, exact=False)).to_have_count(0)

        browser.close()


def test_trend_falls_back_to_daily_when_hourly_cannot_draw_a_line() -> None:
    history = _fresh_install_payload()
    history["series"]["hourly"] = history["series"]["hourly"][-1:]
    history["series"]["daily"] = copy.deepcopy(_history_payload())["series"]["daily"]

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1800})
        _open_history_view(page, history)

        panel = _trend_panel(page)
        expect(panel.get_by_role("button", name="Daily", exact=True)).to_have_class(
            re.compile(r"bg-accent")
        )
        expect(panel.get_by_text("Showing 2 daily points", exact=True)).to_be_visible()

        browser.close()


def test_explicit_trend_choice_is_remembered_across_reload() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1800})
        _open_history_view(page, _fresh_install_payload())

        _trend_panel(page).get_by_role("button", name="Daily", exact=True).click()
        expect(_trend_panel(page).get_by_text(EMPTY_TEXT, exact=False)).to_be_visible()
        assert page.evaluate(f"localStorage.getItem('{STORAGE_KEY}')") == "daily"

        page.reload()
        page.wait_for_load_state("networkidle")
        page.get_by_role("button", name="Historical", exact=True).click()
        expect(_trend_panel(page).get_by_role("button", name="Daily", exact=True)).to_have_class(
            re.compile(r"bg-accent")
        )

        browser.close()


def test_per_model_breakdown_follows_hourly_buckets() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1800})
        _open_history_view(page, _fresh_install_payload())

        expect(page.get_by_text("Hourly buckets", exact=True)).to_be_visible()

        browser.close()


def test_summary_average_follows_the_selected_series_and_skips_the_open_bucket() -> None:
    """Hourly selected: average is the mean of completed hourly buckets, not lifetime / month count."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1800})
        _open_history_view(page, _fresh_install_payload())

        summary = page.get_by_test_id("history-summary")
        expect(summary.get_by_text("Average per hour", exact=True)).to_be_visible()
        # completed hourly buckets: 400,817 and 766,735 -> mean 583,776
        expect(summary.get_by_test_id("history-summary-average")).to_have_text("583.8k tokens")

        browser.close()
