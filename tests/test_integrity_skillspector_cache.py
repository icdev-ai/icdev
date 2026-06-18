# CUI // SP-CTI
"""Tests for tools/integrity/skillspector_cache.py.

Covers cache hit / miss / invalidate, hash stability, disk persistence, and
graceful handling of missing/corrupt cache files. All tests use an isolated
monkey-patched cache path so they never touch the real .tmp/skillspector_cache.json.
"""
from __future__ import annotations

import json
import time

import pytest

from tools.integrity import skillspector_cache


@pytest.fixture
def isolated_cache(monkeypatch, tmp_path):
    """Patch skillspector_cache to use a temp cache file and reset module state."""
    cache_path = tmp_path / "skillspector_cache.json"
    monkeypatch.setattr(skillspector_cache, "CACHE_PATH", cache_path)
    monkeypatch.setattr(skillspector_cache, "_store", {})
    monkeypatch.setattr(skillspector_cache, "_loaded", False)
    return cache_path


@pytest.fixture
def skill_dir(tmp_path):
    """Create a temporary skill directory with a SKILL.md file."""
    d = tmp_path / "icdev-test"
    d.mkdir()
    (d / "SKILL.md").write_text("# Test Skill\n", encoding="utf-8")
    return d


# --------------------------------------------------------------------------- #
# Hash stability
# --------------------------------------------------------------------------- #
def test_skill_dir_hash_is_stable_for_same_content(isolated_cache, skill_dir):
    """Two consecutive hashes of unchanged SKILL.md must match."""
    h1 = skillspector_cache.skill_dir_hash(skill_dir)
    h2 = skillspector_cache.skill_dir_hash(skill_dir)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex length


def test_skill_dir_hash_changes_when_content_changes(isolated_cache, skill_dir):
    """Editing SKILL.md content must produce a different cache key."""
    h1 = skillspector_cache.skill_dir_hash(skill_dir)
    (skill_dir / "SKILL.md").write_text("# Modified Skill\n", encoding="utf-8")
    h2 = skillspector_cache.skill_dir_hash(skill_dir)
    assert h1 != h2


def test_skill_dir_hash_changes_when_mtime_changes(isolated_cache, skill_dir):
    """Touching SKILL.md (same content, new mtime) must produce a different key."""
    h1 = skillspector_cache.skill_dir_hash(skill_dir)
    # Sleep briefly then touch to guarantee a different st_mtime_ns.
    time.sleep(0.05)
    (skill_dir / "SKILL.md").write_text("# Test Skill\n", encoding="utf-8")
    h2 = skillspector_cache.skill_dir_hash(skill_dir)
    assert h1 != h2


def test_skill_dir_hash_without_skill_md_uses_path(skill_dir):
    """When SKILL.md is missing, the key derives from the resolved directory path."""
    (skill_dir / "SKILL.md").unlink()
    h1 = skillspector_cache.skill_dir_hash(skill_dir)
    h2 = skillspector_cache.skill_dir_hash(skill_dir)
    assert h1 == h2
    assert len(h1) == 64


# --------------------------------------------------------------------------- #
# Cache hit / miss / invalidate
# --------------------------------------------------------------------------- #
def test_get_cached_returns_none_on_miss(isolated_cache, skill_dir):
    """A skill directory never cached must return None."""
    assert skillspector_cache.get_cached(skill_dir) is None


def test_set_cached_then_get_cached_returns_data_on_hit(isolated_cache, skill_dir):
    """After storing a result, get_cached must return it."""
    data = {"allowed": True, "risk_score": 0.0, "reason": "clean"}
    skillspector_cache.set_cached(skill_dir, data)
    cached = skillspector_cache.get_cached(skill_dir)
    assert cached == data


def test_get_cached_returns_none_after_skill_md_content_change(isolated_cache, skill_dir):
    """Changing SKILL.md invalidates the prior cache entry."""
    data = {"allowed": True, "risk_score": 0.0}
    skillspector_cache.set_cached(skill_dir, data)
    assert skillspector_cache.get_cached(skill_dir) == data

    (skill_dir / "SKILL.md").write_text("# Changed\n", encoding="utf-8")
    assert skillspector_cache.get_cached(skill_dir) is None


def test_get_cached_returns_none_after_skill_md_mtime_change(isolated_cache, skill_dir):
    """Touching SKILL.md (same content, new mtime) invalidates the entry."""
    data = {"allowed": True, "risk_score": 0.0}
    skillspector_cache.set_cached(skill_dir, data)
    assert skillspector_cache.get_cached(skill_dir) == data

    time.sleep(0.05)
    (skill_dir / "SKILL.md").write_text("# Test Skill\n", encoding="utf-8")
    assert skillspector_cache.get_cached(skill_dir) is None


def test_set_cached_overwrites_existing_entry(isolated_cache, skill_dir):
    """Storing a new result for the same key replaces the old one."""
    skillspector_cache.set_cached(skill_dir, {"risk_score": 10.0})
    skillspector_cache.set_cached(skill_dir, {"risk_score": 42.0})
    assert skillspector_cache.get_cached(skill_dir) == {"risk_score": 42.0}


# --------------------------------------------------------------------------- #
# Disk persistence
# --------------------------------------------------------------------------- #
def test_save_writes_valid_json(isolated_cache, skill_dir):
    """save() must write readable JSON containing the cached entry."""
    data = {"allowed": False, "risk_score": 75.0}
    skillspector_cache.set_cached(skill_dir, data)
    assert isolated_cache.exists()
    raw = json.loads(isolated_cache.read_text(encoding="utf-8"))
    key = skillspector_cache.skill_dir_hash(skill_dir)
    assert key in raw
    assert raw[key]["result"] == data


def test_load_restores_cache_from_disk(isolated_cache, skill_dir):
    """A saved cache must be reloadable into a fresh in-memory store."""
    data = {"allowed": True, "risk_score": 5.0}
    skillspector_cache.set_cached(skill_dir, data)

    # Reset module state as if a new process just imported the module.
    isolated_cache.parent.mkdir(parents=True, exist_ok=True)
    skillspector_cache._store = {}
    skillspector_cache._loaded = False

    assert skillspector_cache.get_cached(skill_dir) == data


def test_load_handles_missing_cache_file(isolated_cache, skill_dir):
    """When the cache file is absent, load() leaves an empty store."""
    assert not isolated_cache.exists()
    skillspector_cache.load()
    assert skillspector_cache._store == {}
    assert skillspector_cache._loaded is True


def test_load_handles_corrupt_cache_file(isolated_cache, skill_dir):
    """Corrupt JSON in the cache file must not raise and must reset the store."""
    isolated_cache.write_text("not-json{", encoding="utf-8")
    skillspector_cache.load()
    assert skillspector_cache._store == {}
    assert skillspector_cache._loaded is True
