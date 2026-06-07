# CUI // SP-CTI
"""Tests for tools/cross_register.py — push approved foundry concepts into creative_gaps.

Hermetic: a throwaway file-backed SQLite DB holds the creative_gaps table.
``get_connection`` is monkey-patched to the temp DB so the function never
 touches the repo database.
"""
from __future__ import annotations

import sqlite3

import pytest

from tools.cross_register import push_to_creative, _generate_id, _priority_from_score

# Minimal creative_gaps DDL — mirrors the orphan-table schema.
_GAP_DDL = """
CREATE TABLE creative_gaps (
    id          TEXT PRIMARY KEY,
    gap_type    TEXT DEFAULT '',
    description TEXT DEFAULT '',
    priority    TEXT DEFAULT 'medium',
    status      TEXT DEFAULT 'open',
    created_at  TEXT
);
"""


def _new_conn(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


@pytest.fixture
def db(tmp_path, monkeypatch):
    """File-backed SQLite DB with creative_gaps, wired into push_to_creative."""
    path = str(tmp_path / "cross_register_test.db")
    boot = _new_conn(path)
    boot.executescript(_GAP_DDL)
    boot.commit()
    boot.close()

    import importlib

    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _new_conn(path))
    yield path


def _make_concept(**overrides) -> dict:
    defaults = {
        "id": 42,
        "run_id": "r1",
        "name": "AI Document Mapper",
        "slug": "ai_doc_mapper",
        "problem_statement": "Users struggle to map fields across document formats",
        "proposed_capability": "An AI-powered field mapper with visual UI",
        "target_users": "Data engineers",
        "composite_score": 0.75,
        "status": "approved",
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def test_generate_id_is_stable():
    c = _make_concept()
    assert _generate_id(c) == _generate_id(c)


def test_generate_id_differs_on_slug():
    a = _make_concept(slug="alpha")
    b = _make_concept(slug="beta")
    assert _generate_id(a) != _generate_id(b)


def test_priority_from_score_high():
    assert _priority_from_score(0.85) == "high"
    assert _priority_from_score(0.8) == "high"


def test_priority_from_score_medium():
    assert _priority_from_score(0.5) == "medium"
    assert _priority_from_score(0.79) == "medium"


def test_priority_from_score_low():
    assert _priority_from_score(0.49) == "low"
    assert _priority_from_score(0.0) == "low"


def test_priority_from_score_none():
    assert _priority_from_score(None) == "medium"


# ---------------------------------------------------------------------------
# push_to_creative — success path
# ---------------------------------------------------------------------------
def test_push_approved_concept(db):
    concept = _make_concept()
    result = push_to_creative(concept)
    assert result["success"] is True
    assert result["gap_id"].startswith("cgap-")

    conn = _new_conn(db)
    try:
        row = conn.execute(
            "SELECT * FROM creative_gaps WHERE id = ?", (result["gap_id"],)
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["gap_type"] == "AI Document Mapper"
    assert "Problem: Users struggle" in row["description"]
    assert "Capability: An AI-powered" in row["description"]
    assert "Target users: Data engineers" in row["description"]
    assert row["priority"] == "medium"  # 0.75
    assert row["status"] == "open"
    assert row["created_at"] is not None


def test_push_approved_concept_with_external_conn(db):
    """When conn is passed, the function must NOT close it."""
    concept = _make_concept()
    conn = _new_conn(db)
    try:
        result = push_to_creative(concept, conn=conn)
        assert result["success"] is True
        # If the function closed conn, this would raise ProgrammingError
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM creative_gaps"
        ).fetchone()
        assert row["cnt"] == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# push_to_creative — guard rails
# ---------------------------------------------------------------------------
def test_push_rejected_concept(db):
    concept = _make_concept(status="rejected")
    result = push_to_creative(concept)
    assert result["success"] is False
    assert "only 'approved' may be pushed" in result["message"]


def test_push_proposed_concept(db):
    concept = _make_concept(status="proposed")
    result = push_to_creative(concept)
    assert result["success"] is False


def test_push_non_dict(db):
    result = push_to_creative("not-a-dict")
    assert result["success"] is False
    assert "concept must be a dict" in result["message"]


# ---------------------------------------------------------------------------
# push_to_creative — duplicate / missing table
# ---------------------------------------------------------------------------
def test_push_duplicate_id_is_rejected(db):
    concept = _make_concept()
    first = push_to_creative(concept)
    assert first["success"] is True

    second = push_to_creative(concept)
    assert second["success"] is False
    assert "Duplicate gap id" in second["message"]


def test_push_degrades_on_missing_table(tmp_path, monkeypatch):
    """If creative_gaps does not exist, the function returns an error dict
    instead of raising an unhandled exception."""
    bare = str(tmp_path / "bare.db")
    c = _new_conn(bare)
    c.commit()
    c.close()

    import importlib

    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _new_conn(bare))

    concept = _make_concept()
    result = push_to_creative(concept)
    assert result["success"] is False
    assert "Insert failed" in result["message"]
