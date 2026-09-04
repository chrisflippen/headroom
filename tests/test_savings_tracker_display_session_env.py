"""The display-session idle rollover window is configurable from the environment."""

from __future__ import annotations

import pytest

from headroom.proxy.savings_tracker import (
    DEFAULT_DISPLAY_SESSION_INACTIVITY_MINUTES,
    DISPLAY_SESSION_INACTIVITY_ENV,
    SavingsTracker,
)


def _policy_minutes(tracker: SavingsTracker) -> int:
    minutes = tracker.history_response()["display_session_policy"]["rollover_inactivity_minutes"]
    assert isinstance(minutes, int)
    return minutes


def test_default_window_when_env_unset(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(DISPLAY_SESSION_INACTIVITY_ENV, raising=False)
    tracker = SavingsTracker(path=str(tmp_path / "savings.json"))
    assert _policy_minutes(tracker) == DEFAULT_DISPLAY_SESSION_INACTIVITY_MINUTES


def test_env_sets_window(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(DISPLAY_SESSION_INACTIVITY_ENV, "1440")
    tracker = SavingsTracker(path=str(tmp_path / "savings.json"))
    assert _policy_minutes(tracker) == 1440


@pytest.mark.parametrize("raw", ["", "abc", "0", "-5"])
def test_bad_env_falls_back_to_default(tmp_path, monkeypatch, raw: str) -> None:
    monkeypatch.setenv(DISPLAY_SESSION_INACTIVITY_ENV, raw)
    tracker = SavingsTracker(path=str(tmp_path / "savings.json"))
    assert _policy_minutes(tracker) == DEFAULT_DISPLAY_SESSION_INACTIVITY_MINUTES


def test_explicit_argument_beats_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(DISPLAY_SESSION_INACTIVITY_ENV, "1440")
    tracker = SavingsTracker(
        path=str(tmp_path / "savings.json"), display_session_inactivity_minutes=90
    )
    assert _policy_minutes(tracker) == 90
