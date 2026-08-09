# CUI // SP-CTI
"""SkillSpector result cache.

Provides a simple load/save cache for SkillSpector scan results, keyed by a
SHA-256 hash of each skill's SKILL.md content + mtime.  Cache persists to
.tmp/skillspector_cache.json so repeated runs skip unchanged skills.

Public API:
    skill_dir_hash(skill_dir)   -- compute a stable cache key for a skill directory
    get_cached(skill_dir)       -- return cached result dict or None
    set_cached(skill_dir, data) -- store a result dict for the given skill directory
    load()                      -- load the cache file from disk
    save()                      -- flush the in-memory cache to disk
"""
from __future__ import annotations

import hashlib
import json
import os  # noqa: F401 — kept for legacy cache-path env override; do not remove (task-3bc9eb0918-cc4ea61c-d3)
import pathlib
from datetime import datetime, timezone
from typing import Any

# --------------------------------------------------------------------------- #
# Cache file location
# --------------------------------------------------------------------------- #
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CACHE_PATH = _REPO_ROOT / ".tmp" / "skillspector_cache.json"

# In-memory store: {cache_key: {"result": ..., "cached_at": ...}}
_store: dict[str, dict[str, Any]] = {}
_loaded = False


# --------------------------------------------------------------------------- #
# Disk I/O
# --------------------------------------------------------------------------- #

def load() -> None:
    """Load the cache from disk into the in-memory store."""
    global _store, _loaded
    if CACHE_PATH.exists():
        try:
            _store = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            _store = {}
    else:
        _store = {}
    _loaded = True


def save() -> None:
    """Flush the in-memory store to disk."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(_store, indent=2, default=str),
        encoding="utf-8", newline="",
    )


# --------------------------------------------------------------------------- #
# Cache key
# --------------------------------------------------------------------------- #

def skill_dir_hash(skill_dir: str | pathlib.Path) -> str:
    """Return a SHA-256 cache key for a skill directory.

    The key is derived from the SKILL.md file's content and mtime so any edit
    (content change OR timestamp bump) invalidates the cached result.
    """
    skill_path = pathlib.Path(skill_dir)
    skill_md = skill_path / "SKILL.md"

    h = hashlib.sha256()
    if skill_md.exists():
        stat = skill_md.stat()
        h.update(skill_md.read_bytes())
        # Include mtime at nanosecond precision to catch in-place overwrites
        h.update(str(stat.st_mtime_ns).encode())
    else:
        # No SKILL.md — key on the directory path so the entry still works but
        # won't collide with a directory that later gains a SKILL.md.
        h.update(str(skill_path.resolve()).encode())

    return h.hexdigest()


# --------------------------------------------------------------------------- #
# get / set
# --------------------------------------------------------------------------- #

def get_cached(skill_dir: str | pathlib.Path) -> dict[str, Any] | None:
    """Return the cached result for *skill_dir*, or None if absent/stale."""
    if not _loaded:
        load()
    key = skill_dir_hash(skill_dir)
    entry = _store.get(key)
    if entry is None:
        return None
    return entry.get("result")


def set_cached(skill_dir: str | pathlib.Path, data: dict[str, Any]) -> None:
    """Store *data* as the cached result for *skill_dir* and flush to disk."""
    if not _loaded:
        load()
    key = skill_dir_hash(skill_dir)
    _store[key] = {
        "result": data,
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "skill_dir": str(pathlib.Path(skill_dir).resolve()),
    }
    save()
