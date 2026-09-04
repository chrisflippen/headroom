"""Tests for the headroom-code-agent plugin (plugins/headroom-code-agent/).

These tests parse the shipped plugin files directly — agent frontmatter,
hooks.json, marketplace.json, and plugin.json — and assert on their shape.
No code from the plugin is imported; the plugin ships instructions and
config, not Python.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import yaml

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "headroom-code-agent"


def _frontmatter(relative_path: str) -> dict[str, Any]:
    """Parse the YAML frontmatter block at the top of a plugin markdown file."""
    text = (PLUGIN_ROOT / relative_path).read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{relative_path} has no frontmatter block"
    end = text.index("\n---", 4)
    return cast(dict[str, Any], yaml.safe_load(text[4:end]))


def _as_set(value: str) -> set[str]:
    return {item.strip() for item in value.split(",")}


def test_code_agent_bans_exactly_the_direct_file_tools() -> None:
    front = _frontmatter("agents/code.md")
    assert _as_set(front["disallowedTools"]) == {"Read", "Edit", "Write", "Grep", "Glob"}


def test_code_agent_model_is_inherit() -> None:
    front = _frontmatter("agents/code.md")
    assert front["model"] == "inherit"


def test_code_agent_name_is_code() -> None:
    front = _frontmatter("agents/code.md")
    assert front["name"] == "code"


def test_scan_helper_model_is_haiku() -> None:
    front = _frontmatter("agents/scan.md")
    assert front["model"] == "haiku"


def test_edit_helper_model_is_sonnet() -> None:
    front = _frontmatter("agents/edit.md")
    assert front["model"] == "sonnet"


def test_scan_helper_tool_list() -> None:
    front = _frontmatter("agents/scan.md")
    assert _as_set(front["tools"]) == {"mcp__headroom__Search", "mcp__headroom__Sql", "Bash"}


def test_edit_helper_tool_list() -> None:
    front = _frontmatter("agents/edit.md")
    assert _as_set(front["tools"]) == {"mcp__headroom__Search", "mcp__headroom__Edit", "Bash"}


def test_hooks_json_has_session_start_running_skills_ensure() -> None:
    hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    session_start = hooks["hooks"]["SessionStart"][0]
    assert session_start["matcher"] == "startup|resume"
    command = session_start["hooks"][0]
    assert command["command"] == "headroom code-agent skills-ensure"
    assert command["timeout"] == 60


def test_hooks_json_has_user_prompt_submit_running_brief() -> None:
    hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    prompt_submit = hooks["hooks"]["UserPromptSubmit"][0]
    command = prompt_submit["hooks"][0]
    assert command["command"] == "headroom code-agent brief"
    assert command["timeout"] == 10


def test_pyrefly_autofix_skill_ships_with_skill_md() -> None:
    assert (PLUGIN_ROOT / "skills" / "pyrefly-autofix" / "SKILL.md").is_file()


def test_scaffold_first_skill_ships_with_skill_md() -> None:
    assert (PLUGIN_ROOT / "skills" / "scaffold-first" / "SKILL.md").is_file()


def test_marketplace_lists_both_plugins() -> None:
    marketplace = json.loads(
        (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )
    names = {plugin["name"] for plugin in marketplace["plugins"]}
    assert names == {"headroom", "headroom-code-agent"}


def test_marketplace_code_agent_entry_points_to_plugin_root() -> None:
    marketplace = json.loads(
        (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )
    entry = next(p for p in marketplace["plugins"] if p["name"] == "headroom-code-agent")
    assert entry["source"] == "./plugins/headroom-code-agent"
    plugin_root = (REPO_ROOT / entry["source"]).resolve()
    assert plugin_root == PLUGIN_ROOT.resolve()


def test_plugin_json_version_matches_package_version() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_version = pyproject["project"]["version"]
    plugin_json = json.loads(
        (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert plugin_json["name"] == "headroom-code-agent"
    assert plugin_json["version"] == package_version
