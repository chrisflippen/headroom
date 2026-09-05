"""Proves the suite never touches the developer's real ``~/.headroom``.

Regression coverage for a live incident: unpatched tests wrote hundreds of
fake proxy start-up lines into ``~/.headroom/logs/proxy.log`` and fake savings
rows into ``~/.headroom/savings_events.jsonl`` / ``proxy_savings.json`` --
files the live dashboard reads. Those files come from ``headroom.paths``
(``workspace_dir()``, ``config_dir()``, ``proxy_log_path()``, ...) which honor
``HEADROOM_WORKSPACE_DIR`` / ``HEADROOM_CONFIG_DIR``, plus ~56 direct
``Path.home()`` call sites elsewhere in the codebase. The autouse
``_isolate_headroom_home`` fixture in ``tests/conftest.py`` is the fix; this
file asserts every one of those resolution paths actually lands under
``tmp_path`` rather than the real home directory.
"""

from __future__ import annotations

import os
from pathlib import Path

from headroom import paths


def test_home_env_points_under_tmp_path(tmp_path: Path) -> None:
    home = Path(os.environ["HOME"]).resolve()
    assert home.is_relative_to(tmp_path.resolve())


def test_path_home_resolves_under_tmp_path(tmp_path: Path) -> None:
    assert Path.home().resolve().is_relative_to(tmp_path.resolve())


def test_workspace_dir_resolves_under_tmp_path(tmp_path: Path) -> None:
    assert paths.workspace_dir().resolve().is_relative_to(tmp_path.resolve())


def test_config_dir_resolves_under_tmp_path(tmp_path: Path) -> None:
    assert paths.config_dir().resolve().is_relative_to(tmp_path.resolve())


def test_proxy_log_path_resolves_under_tmp_path(tmp_path: Path) -> None:
    assert paths.proxy_log_path().resolve().is_relative_to(tmp_path.resolve())


def test_savings_events_path_resolves_under_tmp_path(tmp_path: Path) -> None:
    assert paths.savings_events_path().resolve().is_relative_to(tmp_path.resolve())


def test_savings_path_resolves_under_tmp_path(tmp_path: Path) -> None:
    assert paths.savings_path().resolve().is_relative_to(tmp_path.resolve())


def test_memory_db_path_resolves_under_tmp_path(tmp_path: Path) -> None:
    assert paths.memory_db_path().resolve().is_relative_to(tmp_path.resolve())


def test_workspace_and_config_dirs_already_exist(tmp_path: Path) -> None:
    """The fixture creates the directories so writers that assume they exist work."""
    assert paths.workspace_dir().is_dir()
    assert paths.config_dir().is_dir()


def test_home_dir_is_not_the_real_developer_home() -> None:
    real_home_candidates = {"/Users/christopherflippen", "/root"}
    assert str(Path.home()) not in real_home_candidates
    assert not str(Path.home()).endswith("/christopherflippen")
