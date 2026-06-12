# CUI // SP-CTI
"""Tests for tools/document_intelligence/canvas_adapter.py (dsyn-adapt-02).

All DB calls are patched — no real database required.
Uses an in-memory SQLite shim with %s→? translation.
"""
from __future__ import annotations

import sqlite3
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── SQLite shim (same pattern as other DIC tests) ────────────────────────────

class _FakeConn:
    def __init__(self, db: sqlite3.Connection):
        self._db = db
        self._db.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        return self._db.execute(sql.replace("%s", "?"), params)

    def commit(self):
        self._db.commit()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.commit()


@contextmanager
def _fake_get_connection(db_path: str):
    raw = sqlite3.connect(db_path)
    raw.row_factory = sqlite3.Row
    c = _FakeConn(raw)
    try:
        yield c
    finally:
        c.commit()
        raw.close()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS dic_collections (
    collection_id TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    tenant_id     TEXT DEFAULT 'default',
    tags          TEXT DEFAULT '',
    created_at    TEXT
);
CREATE TABLE IF NOT EXISTS dic_processed_canvas_events (
    event_id     TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL,
    processor    TEXT NOT NULL DEFAULT 'dic_integration'
);
"""


def _make_db(tmp_path: Path) -> str:
    path = str(tmp_path / "adapter_test.db")
    db = sqlite3.connect(path)
    db.executescript(_SCHEMA)
    db.commit()
    db.close()
    return path


def _insert_collection(db_path: str, cid: str, name: str, tags: str,
                        tenant_id: str = "default") -> None:
    db = sqlite3.connect(db_path)
    db.execute(
        "INSERT OR REPLACE INTO dic_collections (collection_id, name, tags, tenant_id) "
        "VALUES (?,?,?,?)",
        (cid, name, tags, tenant_id),
    )
    db.commit()
    db.close()


@pytest.fixture()
def db(tmp_path):
    return _make_db(tmp_path)


@pytest.fixture(autouse=True)
def patch_conn(db):
    """Redirect all get_connection calls in canvas_adapter to our SQLite shim."""
    import tools.document_intelligence.canvas_adapter as mod
    real_gc = mod.get_connection

    @contextmanager
    def _patched():
        raw = sqlite3.connect(db)
        raw.row_factory = sqlite3.Row
        c = _FakeConn(raw)
        try:
            yield c
        finally:
            c.commit()
            raw.close()

    with patch.object(mod, "get_connection", _patched):
        # Bust the config cache so each test gets a fresh load
        mod._cache = None
        mod._cache_mtime = 0.0
        yield


# ── Config loading ────────────────────────────────────────────────────────────

def test_config_loads():
    from tools.document_intelligence.canvas_adapter import _load_config
    cfg = _load_config()
    assert isinstance(cfg, dict)
    assert len(cfg) > 0


def test_config_has_fallback():
    from tools.document_intelligence.canvas_adapter import _load_config
    cfg = _load_config()
    assert "fallback" in cfg


# ── _match_config_entry ───────────────────────────────────────────────────────

def test_exact_match():
    from tools.document_intelligence.canvas_adapter import _match_config_entry
    entries = {
        "ndc.topology.drift_detected": {"priority": "high"},
        "fallback": {"priority": "low"},
    }
    key, cfg = _match_config_entry("ndc.topology.drift_detected", entries)
    assert key == "ndc.topology.drift_detected"
    assert cfg["priority"] == "high"


def test_prefix_match_two_parts():
    from tools.document_intelligence.canvas_adapter import _match_config_entry
    entries = {
        "ndc.topology": {"priority": "medium"},
        "fallback": {"priority": "low"},
    }
    key, cfg = _match_config_entry("ndc.topology.new_event", entries)
    assert key == "ndc.topology"


def test_prefix_match_one_part():
    from tools.document_intelligence.canvas_adapter import _match_config_entry
    entries = {
        "ndc": {"priority": "medium"},
        "fallback": {"priority": "low"},
    }
    key, cfg = _match_config_entry("ndc.something.else", entries)
    assert key == "ndc"


def test_fallback_for_unknown():
    from tools.document_intelligence.canvas_adapter import _match_config_entry
    entries = {"fallback": {"priority": "low", "target_collection_tags": ["general"]}}
    key, cfg = _match_config_entry("unknown.event.type", entries)
    assert key == "fallback"
    assert cfg["priority"] == "low"


def test_longest_prefix_wins():
    """When both 'ndc' and 'ndc.topology' match, the longer one wins."""
    from tools.document_intelligence.canvas_adapter import _match_config_entry
    entries = {
        "ndc": {"priority": "low"},
        "ndc.topology": {"priority": "high"},
        "fallback": {"priority": "low"},
    }
    key, cfg = _match_config_entry("ndc.topology.drift_detected", entries)
    assert key == "ndc.topology"
    assert cfg["priority"] == "high"


# ── resolve_affected_collections ─────────────────────────────────────────────

def test_exact_match_returns_tagged_collection(db):
    _insert_collection(db, "col-net-arch", "Network Architecture", "network_architecture,infrastructure")
    from tools.document_intelligence.canvas_adapter import resolve_affected_collections
    results = resolve_affected_collections({"event_type": "ndc.topology.drift_detected"})
    ids = [r["collection_id"] for r in results]
    assert "col-net-arch" in ids


def test_matching_collection_has_correct_priority(db):
    _insert_collection(db, "col-sec", "Security Baseline", "security_baseline,network_architecture")
    from tools.document_intelligence.canvas_adapter import resolve_affected_collections
    results = resolve_affected_collections({"event_type": "ndc.topology.drift_detected"})
    assert any(r["priority"] == "high" for r in results)


def test_unrelated_collection_not_returned(db):
    _insert_collection(db, "col-hr", "HR Policies", "hr,onboarding")
    from tools.document_intelligence.canvas_adapter import resolve_affected_collections
    results = resolve_affected_collections({"event_type": "ndc.topology.drift_detected"})
    ids = [r["collection_id"] for r in results]
    assert "col-hr" not in ids


def test_fallback_returns_general_tagged_collection(db):
    _insert_collection(db, "col-gen", "General Docs", "general")
    from tools.document_intelligence.canvas_adapter import resolve_affected_collections
    results = resolve_affected_collections({"event_type": "completely.unknown.event"})
    ids = [r["collection_id"] for r in results]
    assert "col-gen" in ids


def test_empty_result_for_no_matching_collections(db):
    # No collections tagged with network_architecture
    from tools.document_intelligence.canvas_adapter import resolve_affected_collections
    results = resolve_affected_collections({"event_type": "ndc.topology.drift_detected"})
    assert results == []


def test_all_tag_matches_all_collections(db):
    _insert_collection(db, "col-a", "Alpha", "policy")
    _insert_collection(db, "col-b", "Beta", "runbook")
    from tools.document_intelligence.canvas_adapter import resolve_affected_collections
    # dic.review_overdue uses target_all = True (tag "all")
    results = resolve_affected_collections({"event_type": "dic.review_overdue"})
    ids = {r["collection_id"] for r in results}
    assert "col-a" in ids
    assert "col-b" in ids


def test_tenant_filter_applied(db):
    _insert_collection(db, "col-t1", "Tenant1 Docs", "network_architecture", tenant_id="tenant-1")
    _insert_collection(db, "col-t2", "Tenant2 Docs", "network_architecture", tenant_id="tenant-2")
    from tools.document_intelligence.canvas_adapter import resolve_affected_collections
    results = resolve_affected_collections({
        "event_type": "ndc.topology.drift_detected",
        "tenant_id": "tenant-1",
    })
    ids = [r["collection_id"] for r in results]
    assert "col-t1" in ids
    assert "col-t2" not in ids


def test_result_shape(db):
    _insert_collection(db, "col-shape", "Shape Test", "network_architecture")
    from tools.document_intelligence.canvas_adapter import resolve_affected_collections
    results = resolve_affected_collections({"event_type": "ndc.topology.drift_detected"})
    assert results
    r = results[0]
    for key in ("collection_id", "collection_name", "matched_tags", "doc_type",
                "doc_types", "priority", "rationale", "patch_mode", "config_key"):
        assert key in r, f"Missing key '{key}' in result"


def test_never_raises_on_bad_event():
    from tools.document_intelligence.canvas_adapter import resolve_affected_collections
    # Missing event_type key — should return empty, not raise
    result = resolve_affected_collections({})
    assert result == []


def test_never_raises_on_empty_string_event():
    from tools.document_intelligence.canvas_adapter import resolve_affected_collections
    result = resolve_affected_collections({"event_type": ""})
    assert result == []


# ── mark_event_processed / is_event_processed ─────────────────────────────────

def test_mark_and_check_processed(db):
    from tools.document_intelligence.canvas_adapter import mark_event_processed, is_event_processed
    eid = f"ev-{uuid.uuid4().hex[:8]}"
    assert not is_event_processed(eid)
    mark_event_processed(eid)
    assert is_event_processed(eid)


def test_mark_processed_is_idempotent(db):
    from tools.document_intelligence.canvas_adapter import mark_event_processed, is_event_processed
    eid = f"ev-{uuid.uuid4().hex[:8]}"
    mark_event_processed(eid)
    mark_event_processed(eid)  # second call must not raise
    assert is_event_processed(eid)


def test_different_events_tracked_independently(db):
    from tools.document_intelligence.canvas_adapter import mark_event_processed, is_event_processed
    eid1 = f"ev-{uuid.uuid4().hex[:8]}"
    eid2 = f"ev-{uuid.uuid4().hex[:8]}"
    mark_event_processed(eid1)
    assert is_event_processed(eid1)
    assert not is_event_processed(eid2)
