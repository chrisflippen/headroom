"""Shared reader for Claude Code's own plugin registry.

Claude Code tracks which plugins are installed in
`plugins/installed_plugins.json`, a file that sits next to its own
`settings.json`. Two different callers need to read this same file --
`headroom.cli.code_agent` (to skip installing the code agent plugin when
it is already there) and `headroom.code_tools.skills_ensure` (to skip
updating a configured plugin that is not installed on this machine) --
so the JSON reading lives here once instead of in both places.
"""

from __future__ import annotations

import json
from pathlib import Path


def installed_plugins_path(settings_path: Path) -> Path:
    """Where Claude Code records installed plugins, next to `settings_path`.

    Claude Code keeps `plugins/installed_plugins.json` as a sibling of
    `settings.json` under the same config directory, so this only needs
    the settings path the caller already has -- no separate env lookup.
    """
    return settings_path.parent / "plugins" / "installed_plugins.json"


def plugin_installed(installed_plugins_path: Path, plugin_key: str) -> bool:
    """True when Claude Code's own registry lists `plugin_key` as installed.

    A missing file, unreadable JSON, or a present key with an empty list
    (uninstalled but not yet pruned from the file) all count as not
    installed.
    """
    if not installed_plugins_path.exists():
        return False
    try:
        payload = json.loads(installed_plugins_path.read_text())
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    plugins = payload.get("plugins")
    if not isinstance(plugins, dict):
        return False
    entry = plugins.get(plugin_key)
    return isinstance(entry, list) and len(entry) > 0
