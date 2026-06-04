# CUI // SP-CTI
"""Tests for the DIC Freshness Engine (dic_doc_freshness table + scoring logic).

Covers:
- Pure scoring helpers (_score_doc, _days_since)
- State thresholds (fresh / aging / stale)
- dic_doc_freshness table existence and upsert
- corpus_heatmap query against the table
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


from tools.db.storage import get_connection
from tools.document_intelligence.freshness_engine import (
    FreshnessResult,
    _days_since,
    _score_doc,
    corpus_heatmap,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(days_ago: float) -> str:
    """Return an ISO timestamp for N days ago."""
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


# ---------------------------------------------------------------------------
# Unit tests — pure functions (no DB)
# ---------------------------------------------------------------------------

def test_days_since_none_returns_sentinel():
    assert _days_since(None) == 9999.0


def test_days_since_recent():
    recent = _iso(1.0)
    assert 0.9 < _days_since(recent) < 2.0


def test_days_since_old():
    old = _iso(100)
    assert 99 < _days_since(old) < 101


def test_score_doc_fresh_new_document():
    result = _score_doc(
        doc_id="d1",
        title="New Policy",
        collection_id="col1",
        created_at=_iso(5),         # 5 days old
        latest_version_at=_iso(5),
        latest_approved_at=_iso(5),
        retention_days=90,
        drift_count_since_update=0,
        pending_section_count=0,
        tenant_id="default",
        classification="CUI",
    )
    assert result.state == "fresh"
    assert result.score < 0.35
    assert result.doc_id == "d1"


def test_score_doc_aging_intermediate():
    result = _score_doc(
        doc_id="d2",
        title="Old Policy",
        collection_id="col1",
        created_at=_iso(50),        # 50 days old (>50% of 90)
        latest_version_at=_iso(50),
        latest_approved_at=_iso(50),
        retention_days=90,
        drift_count_since_update=1,
        pending_section_count=2,
        tenant_id="default",
        classification="CUI",
    )
    assert result.state in ("aging", "stale")
    assert result.score >= 0.35


def test_score_doc_stale_old_with_drift():
    result = _score_doc(
        doc_id="d3",
        title="Stale Policy",
        collection_id="col1",
        created_at=_iso(200),       # way past retention
        latest_version_at=_iso(200),
        latest_approved_at=None,    # never approved
        retention_days=90,
        drift_count_since_update=4,
        pending_section_count=6,
        tenant_id="default",
        classification="CUI",
    )
    assert result.state == "stale"
    assert result.score >= 0.7


def test_score_doc_returns_freshness_result():
    result = _score_doc(
        doc_id="d4",
        title="Test",
        collection_id="c",
        created_at=_iso(0),
        latest_version_at=_iso(0),
        latest_approved_at=_iso(0),
        retention_days=90,
        drift_count_since_update=0,
        pending_section_count=0,
        tenant_id="t1",
        classification="SECRET",
    )
    assert isinstance(result, FreshnessResult)
    assert result.tenant_id == "t1"
    assert result.classification == "SECRET"
    assert result.source_event == "freshness_scan"


def test_score_doc_drift_capped():
    # 10 drift events — drift score should cap at 0.6
    fresh = _score_doc(
        doc_id="d5",
        title="",
        collection_id="c",
        created_at=_iso(1),
        latest_version_at=_iso(1),
        latest_approved_at=_iso(1),
        retention_days=90,
        drift_count_since_update=10,
        pending_section_count=0,
        tenant_id="default",
        classification="CUI",
    )
    stale = _score_doc(
        doc_id="d5",
        title="",
        collection_id="c",
        created_at=_iso(1),
        latest_version_at=_iso(1),
        latest_approved_at=_iso(1),
        retention_days=90,
        drift_count_since_update=100,
        pending_section_count=0,
        tenant_id="default",
        classification="CUI",
    )
    # Caps should produce the same score regardless of 10 vs 100 events
    assert fresh.score == stale.score


# ---------------------------------------------------------------------------
# DB tests — dic_doc_freshness table
# ---------------------------------------------------------------------------

def test_dic_doc_freshness_table_exists():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='dic_doc_freshness'"
        )
        row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None, "dic_doc_freshness table missing from MINIMAL_ICDEV_SCHEMA"


def test_dic_doc_freshness_upsert_and_read():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO dic_doc_freshness "
            "(doc_id, collection_id, state, reason, source_event, score, updated_at, tenant_id, classification) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (doc_id) DO UPDATE SET state=EXCLUDED.state, score=EXCLUDED.score",
            ("test_fresh_01", "col_test", "fresh", "within retention", "freshness_scan",
             0.1, "2026-01-01T00:00:00+00:00", "default", "CUI"),
        )
        conn.commit()
        cur.execute(
            "SELECT state, score FROM dic_doc_freshness WHERE doc_id = ?",
            ("test_fresh_01",),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == "fresh"
    assert abs(row[1] - 0.1) < 0.001


def test_dic_doc_freshness_upsert_updates_on_conflict():
    conn = get_connection()
    try:
        cur = conn.cursor()
        for state, score in [("aging", 0.5), ("stale", 0.9)]:
            cur.execute(
                "INSERT INTO dic_doc_freshness "
                "(doc_id, collection_id, state, score, updated_at, tenant_id, classification) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT (doc_id) DO UPDATE SET state=EXCLUDED.state, score=EXCLUDED.score",
                ("test_fresh_upsert", "col_test", state, score,
                 "2026-01-01T00:00:00+00:00", "default", "CUI"),
            )
            conn.commit()
        cur.execute(
            "SELECT state, score FROM dic_doc_freshness WHERE doc_id = ?",
            ("test_fresh_upsert",),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    assert row[0] == "stale"
    assert abs(row[1] - 0.9) < 0.001


def test_corpus_heatmap_returns_list():
    result = corpus_heatmap(tenant_id="default", limit=10)
    assert isinstance(result, list)


def test_corpus_heatmap_includes_inserted_doc():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO dic_doc_freshness "
            "(doc_id, collection_id, state, reason, source_event, score, updated_at, tenant_id, classification) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (doc_id) DO UPDATE SET state=EXCLUDED.state, score=EXCLUDED.score",
            ("test_heatmap_doc", "col_heatmap", "stale", "age 200d", "freshness_scan",
             0.95, "2026-01-01T00:00:00+00:00", "default", "CUI"),
        )
        conn.commit()
    finally:
        conn.close()

    rows = corpus_heatmap(tenant_id="default", limit=200)
    doc_ids = [r["doc_id"] for r in rows]
    assert "test_heatmap_doc" in doc_ids
