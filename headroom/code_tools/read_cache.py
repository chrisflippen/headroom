"""On-disk cache of what Search has already read.

Search needs one question answered on every read: has this file changed
since the model last saw it? This cache holds the answer — one entry per
file, keyed by its absolute path — on disk, so the answer survives a new
process (a subagent, or the MCP server restarting after context
compaction). Edit will use this same cache later to know what content is
safe to patch without a fresh read first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from headroom import fsutil, paths

_CACHE_FILE_NAME = "read_cache.json"


@dataclass(frozen=True)
class ReadCacheEntry:
    """What we knew about one file the last time Search read it."""

    content_hash: str
    store_hash: str
    line_count: int
    token_estimate: int


class ReadCache:
    """Reads and writes the on-disk read cache.

    Every call re-reads the file from disk and, for a write, replaces it
    atomically. Nothing is kept in memory between calls, so two processes
    (the main session and a subagent, say) never race on a half-written
    file, and the cache is correct even if this is the first call ever
    made against it in this process.
    """

    def __init__(self, cache_path: Path | None = None) -> None:
        self._path = cache_path or (paths.code_tools_dir() / _CACHE_FILE_NAME)

    def _load(self) -> dict[str, object]:
        raw = fsutil.read_text(self._path, default="")
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # A corrupt cache file is treated as empty rather than raised —
            # a bad write must never stop the agent from reading files.
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _save(self, data: dict[str, object]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fsutil.write_text(self._path, json.dumps(data))

    def get(self, path: str) -> ReadCacheEntry | None:
        """Return the cached entry for an absolute path, or ``None``."""

        raw_entry = self._load().get(path)
        if not isinstance(raw_entry, dict):
            return None
        try:
            return ReadCacheEntry(
                content_hash=str(raw_entry["content_hash"]),
                store_hash=str(raw_entry["store_hash"]),
                line_count=int(raw_entry["line_count"]),
                token_estimate=int(raw_entry["token_estimate"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def put(
        self,
        path: str,
        *,
        content_hash: str,
        store_hash: str,
        line_count: int,
        token_estimate: int,
    ) -> None:
        """Record (or replace) the cached entry for an absolute path."""

        data = self._load()
        data[path] = {
            "content_hash": content_hash,
            "store_hash": store_hash,
            "line_count": line_count,
            "token_estimate": token_estimate,
        }
        self._save(data)

    def invalidate(self, path: str) -> None:
        """Drop the cached entry for an absolute path, if there is one."""

        data = self._load()
        if path in data:
            del data[path]
            self._save(data)
