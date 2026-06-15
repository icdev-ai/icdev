#!/usr/bin/env python3
# CUI // SP-CTI
"""Regression tests for PGP / pgp-tx-02 — runtime JSON-SQL call sites moved
off translate_sql to portable (Python-computed) form.

Covers the cross-engine registration dedup logic that previously relied on
``json_extract(metadata, '$.<key>')`` in subqueries / WHERE clauses:
  - tools/creative/creative_engine.py::_cross_register_to_innovation
  - tools/research/trend_detector.py::_cross_register_to_innovation

These now parse the ``metadata`` JSON column in Python, so they run identically
on PostgreSQL (primary) and the SQLite init-fallback without depending on the
translator. The cloud call sites are covered in test_csp_monitor.py.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# Union schema covering columns inserted by both engines' cross-registration.
_INNOVATION_SIGNALS_DDL = """
CREATE TABLE innovation_signals (
    id TEXT PRIMARY KEY,
    source TEXT,
    source_type TEXT,
    category TEXT,
    title TEXT,
    body TEXT,
    description TEXT,
    url TEXT,
    content_hash TEXT,
    composite_score REAL,
    innovation_score REAL,
    status TEXT DEFAULT 'new',
    metadata TEXT DEFAULT '{}',
    discovered_at TEXT,
    classification TEXT
)
"""

_PAIN_POINTS_DDL = """
CREATE TABLE creative_pain_points (
    id TEXT PRIMARY KEY,
    title TEXT,
    description TEXT,
    composite_score REAL,
    category TEXT,
    keywords TEXT,
    metadata TEXT
)
"""


def _registered_pain_point_ids(db_path):
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT metadata FROM innovation_signals WHERE source = 'creative_engine'"
    ).fetchall()
    conn.close()
    out = set()
    for (md,) in rows:
        pid = json.loads(md or "{}").get("creative_pain_point_id")
        if pid is not None:
            out.add(pid)
    return out


def _registered_trend_ids(db_path):
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT metadata FROM innovation_signals").fetchall()
    conn.close()
    out = set()
    for (md,) in rows:
        rid = json.loads(md or "{}").get("research_trend_id")
        if rid is not None:
            out.add(rid)
    return out


class TestCreativeCrossRegisterPortable:
    """creative_engine excludes already-registered pain points via a Python
    set instead of a ``NOT IN (SELECT json_extract(...))`` subquery."""

    @pytest.fixture
    def creative_db(self, tmp_path):
        db_path = tmp_path / "creative_test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(_PAIN_POINTS_DDL)
        conn.execute(_INNOVATION_SIGNALS_DDL)
        # Two high-scoring pain points (>0.60), one low-scoring (<0.60).
        conn.executemany(
            "INSERT INTO creative_pain_points "
            "(id, title, description, composite_score, category, keywords, metadata) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                ("pp-registered", "Already done", "d", 0.90, "feature_gap", "[]", "{}"),
                ("pp-fresh", "Brand new", "d", 0.85, "feature_gap", "[]", "{}"),
                ("pp-low", "Below threshold", "d", 0.10, "feature_gap", "[]", "{}"),
            ],
        )
        # pp-registered already cross-registered.
        conn.execute(
            "INSERT INTO innovation_signals "
            "(id, source, source_type, metadata, discovered_at) VALUES (?,?,?,?,?)",
            (
                "isig-existing",
                "creative_engine",
                "external_framework_analysis",
                json.dumps({"creative_pain_point_id": "pp-registered"}),
                "2026-06-07T00:00:00Z",
            ),
        )
        conn.commit()
        conn.close()
        return db_path

    def test_skips_registered_registers_fresh(self, creative_db):
        from tools.creative.creative_engine import _cross_register_to_innovation

        _cross_register_to_innovation(db_path=creative_db)

        registered = _registered_pain_point_ids(creative_db)
        # Fresh high-scoring point added; already-registered not duplicated;
        # below-threshold point excluded.
        assert "pp-fresh" in registered
        assert "pp-registered" in registered
        assert "pp-low" not in registered

        # Exactly one signal per pain point id (no re-registration duplicate).
        conn = sqlite3.connect(str(creative_db))
        cnt = conn.execute(
            "SELECT COUNT(*) FROM innovation_signals WHERE source = 'creative_engine'"
        ).fetchone()[0]
        conn.close()
        assert cnt == 2


class TestResearchCrossRegisterPortable:
    """research/trend_detector excludes already-registered trends via a
    prefetched Python set instead of a per-trend json_extract() WHERE."""

    def _trend(self, tid, velocity):
        return {
            "id": tid,
            "name": f"name-{tid}",
            "status": "rising",
            "velocity": velocity,
            "vertical_ids": ["v1"],
            "session_ids": ["s1"],
            "keyword_fingerprint": f"fp-{tid}",
        }

    @pytest.fixture
    def research_db(self, tmp_path):
        db_path = tmp_path / "research_test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(_INNOVATION_SIGNALS_DDL)
        conn.execute(
            "INSERT INTO innovation_signals "
            "(id, source, source_type, metadata, discovered_at) VALUES (?,?,?,?,?)",
            (
                "isig-existing",
                "research_engine",
                "external_framework_analysis",
                json.dumps({"research_trend_id": "trend-existing"}),
                "2026-06-07T00:00:00Z",
            ),
        )
        conn.commit()
        conn.close()
        return db_path

    def test_skips_registered_registers_fresh(self, research_db):
        from tools.research.trend_detector import (
            _cross_register_to_innovation,
            _get_db,
        )

        conn = _get_db(research_db)
        try:
            _cross_register_to_innovation(
                conn,
                [
                    self._trend("trend-existing", 0.90),  # already registered
                    self._trend("trend-fresh", 0.90),     # new, above threshold
                    self._trend("trend-low", 0.10),       # below threshold (0.70)
                ],
            )
            conn.commit()
        finally:
            conn.close()

        registered = _registered_trend_ids(research_db)
        assert "trend-fresh" in registered
        assert "trend-existing" in registered
        assert "trend-low" not in registered

        conn = sqlite3.connect(str(research_db))
        cnt = conn.execute("SELECT COUNT(*) FROM innovation_signals").fetchone()[0]
        conn.close()
        # original + one fresh = 2 (no duplicate for trend-existing).
        assert cnt == 2


class TestChatSourcesAggregatePortable:
    """dashboard.app._aggregate_chat_sources groups chat-upload chunks and
    extracts metadata in Python instead of json_extract()/MAX-over-JSON."""

    def _rows(self):
        return [
            {"source_id": "doc-a", "metadata": json.dumps(
                {"filename": "alpha.pdf", "context_id": "ctx-1"}),
             "created_at": "2026-06-01T10:00:00Z"},
            {"source_id": "doc-a", "metadata": json.dumps(
                {"filename": "alpha.pdf", "context_id": "ctx-1"}),
             "created_at": "2026-06-02T10:00:00Z"},
            {"source_id": "doc-b", "metadata": json.dumps(
                {"filename": "beta.txt", "context_id": "ctx-2"}),
             "created_at": "2026-06-03T10:00:00Z"},
        ]

    def test_groups_and_counts(self):
        from tools.dashboard.app import _aggregate_chat_sources

        out = _aggregate_chat_sources(self._rows())
        by_id = {s["source_id"]: s for s in out}
        assert by_id["doc-a"]["chunk_count"] == 2
        assert by_id["doc-a"]["filename"] == "alpha.pdf"
        assert by_id["doc-a"]["context_id"] == "ctx-1"
        # MAX(created_at) — most recent of the two doc-a chunks.
        assert by_id["doc-a"]["indexed_at"] == "2026-06-02T10:00:00Z"
        assert by_id["doc-b"]["chunk_count"] == 1

    def test_orders_by_indexed_at_desc(self):
        from tools.dashboard.app import _aggregate_chat_sources

        out = _aggregate_chat_sources(self._rows())
        # doc-b (2026-06-03) is most recent → first.
        assert out[0]["source_id"] == "doc-b"

    def test_context_id_filter(self):
        from tools.dashboard.app import _aggregate_chat_sources

        out = _aggregate_chat_sources(self._rows(), context_id="ctx-2")
        assert len(out) == 1
        assert out[0]["source_id"] == "doc-b"

    def test_empty_metadata_handled(self):
        from tools.dashboard.app import _aggregate_chat_sources

        out = _aggregate_chat_sources(
            [{"source_id": "doc-c", "metadata": None, "created_at": None}]
        )
        assert len(out) == 1
        assert out[0]["chunk_count"] == 1
        assert out[0]["filename"] is None
