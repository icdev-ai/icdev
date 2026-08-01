# CUI // SP-CTI
"""Tests for crx-kg-01: KG blast-radius freshness signal.

Covers:
  • consistency_checker.find_docs_citing_changed_entities — the extended
    concept-overlap detector: docs citing N+ recently-changed entities.
    Canonical case: a doc cites 5 entities, 3 change, min_overlap=3 -> flagged.
  • freshness_engine 5th (blast-radius) dimension — CRITICAL correctness:
    with the default 0.0 blast weight the score is byte-identical to the
    legacy 4-dimension score; only a tuned-up weight moves it.
"""
from __future__ import annotations

import contextlib
import importlib
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock


# ── Shim conn (mirrors tests/test_dsyn_consistency.py) ─────────────────────────

def _make_raw_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for ddl in [
        """CREATE TABLE dic_documents (
            doc_id TEXT PRIMARY KEY, title TEXT, collection_id TEXT,
            classification TEXT DEFAULT 'CUI', created_at TEXT
        )""",
        """CREATE TABLE kg_nodes (
            id TEXT PRIMARY KEY, label TEXT, entity_type TEXT,
            centrality REAL, source_chunk_id TEXT, graph_id TEXT
        )""",
        """CREATE TABLE kg_graphs (
            id TEXT PRIMARY KEY, source_doc_id TEXT, created_at TEXT
        )""",
    ]:
        conn.execute(ddl)
    conn.commit()
    return conn


class _ShimConn:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        return self._conn.execute(sql.replace("%s", "?"), params)

    def commit(self):
        self._conn.commit()

    def cursor(self):
        return self._conn.cursor()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


@contextlib.contextmanager
def _patch_attr(module_path: str, attr: str, value):
    mod = importlib.import_module(module_path)
    orig = getattr(mod, attr, None)
    setattr(mod, attr, value)
    try:
        yield
    finally:
        if orig is None:
            with contextlib.suppress(AttributeError):
                delattr(mod, attr)
        else:
            setattr(mod, attr, orig)


# Five distinct entity labels (none a substring of another) cited by one doc.
_FIVE_ENTITIES = ["firewall", "encryption", "authentication", "segmentation", "telemetry"]


def _seed_doc_citing(shim, doc_id: str, entities: list[str], collection_id="col-1"):
    shim.execute(
        "INSERT INTO dic_documents (doc_id, title, collection_id, created_at)"
        " VALUES (?,?,?,?)", (doc_id, f"Doc {doc_id}", collection_id, "2026-01-01"),
    )
    gid = f"graph-{doc_id}"
    shim.execute("INSERT INTO kg_graphs (id, source_doc_id) VALUES (?,?)", (gid, doc_id))
    for i, label in enumerate(entities):
        shim.execute(
            "INSERT INTO kg_nodes (id, label, entity_type, graph_id) VALUES (?,?,?,?)",
            (f"n-{doc_id}-{i}", label, "concept", gid),
        )
    shim.commit()


# ══════════════════════════════════════════════════════════════════════════════
# find_docs_citing_changed_entities — the blast-radius detector
# ══════════════════════════════════════════════════════════════════════════════

class TestBlastRadiusDetection:
    def test_three_of_five_cited_entities_change_flags_doc(self):
        """Canonical spec case: doc cites 5 entities, 3 change -> flagged."""
        shim = _ShimConn(_make_raw_conn())
        _seed_doc_citing(shim, "doc-a", _FIVE_ENTITIES)
        changed = _FIVE_ENTITIES[:3]  # 3 of the 5 entities changed
        with _patch_attr("tools.document_intelligence.consistency_checker",
                         "get_connection", MagicMock(return_value=shim)):
            from tools.document_intelligence.consistency_checker import (
                find_docs_citing_changed_entities,
            )
            flagged = find_docs_citing_changed_entities(changed, min_overlap=3)
        ids = [f["doc_id"] for f in flagged]
        assert "doc-a" in ids
        hit = next(f for f in flagged if f["doc_id"] == "doc-a")
        assert hit["overlap_count"] == 3
        assert set(hit["matched_entities"]) == set(changed)

    def test_below_threshold_not_flagged(self):
        """Only 3 overlap -> not flagged when min_overlap is 4."""
        shim = _ShimConn(_make_raw_conn())
        _seed_doc_citing(shim, "doc-a", _FIVE_ENTITIES)
        with _patch_attr("tools.document_intelligence.consistency_checker",
                         "get_connection", MagicMock(return_value=shim)):
            from tools.document_intelligence.consistency_checker import (
                find_docs_citing_changed_entities,
            )
            flagged = find_docs_citing_changed_entities(_FIVE_ENTITIES[:3], min_overlap=4)
        assert flagged == []

    def test_only_docs_meeting_threshold_returned(self):
        """Doc-a cites all 5 (3 change); doc-b cites 1 changed entity."""
        shim = _ShimConn(_make_raw_conn())
        _seed_doc_citing(shim, "doc-a", _FIVE_ENTITIES)
        _seed_doc_citing(shim, "doc-b", ["firewall", "unrelated", "misc"])
        with _patch_attr("tools.document_intelligence.consistency_checker",
                         "get_connection", MagicMock(return_value=shim)):
            from tools.document_intelligence.consistency_checker import (
                find_docs_citing_changed_entities,
            )
            flagged = find_docs_citing_changed_entities(_FIVE_ENTITIES[:3], min_overlap=3)
        ids = [f["doc_id"] for f in flagged]
        assert "doc-a" in ids
        assert "doc-b" not in ids  # only 1 overlap < 3

    def test_empty_changed_entities_returns_empty(self):
        from tools.document_intelligence.consistency_checker import (
            find_docs_citing_changed_entities,
        )
        assert find_docs_citing_changed_entities([], min_overlap=1) == []

    def test_result_sorted_by_overlap_desc(self):
        shim = _ShimConn(_make_raw_conn())
        _seed_doc_citing(shim, "doc-hi", _FIVE_ENTITIES)          # overlaps 3
        _seed_doc_citing(shim, "doc-lo", ["firewall", "encryption", "x", "y"])  # overlaps 2
        with _patch_attr("tools.document_intelligence.consistency_checker",
                         "get_connection", MagicMock(return_value=shim)):
            from tools.document_intelligence.consistency_checker import (
                find_docs_citing_changed_entities,
            )
            flagged = find_docs_citing_changed_entities(_FIVE_ENTITIES[:3], min_overlap=2)
        assert [f["doc_id"] for f in flagged] == ["doc-hi", "doc-lo"]


# ══════════════════════════════════════════════════════════════════════════════
# freshness_engine — 5th dimension behind a config toggle (default weight 0)
# ══════════════════════════════════════════════════════════════════════════════

def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


class TestBlastDimensionWeighting:
    def test_default_weight_is_zero(self):
        from tools.document_intelligence.freshness_engine import get_weights
        weights = get_weights()
        assert weights["blast_radius"] == 0.0
        # All five dimensions present.
        assert set(weights) == {"age", "approval", "drift", "pending", "blast_radius"}

    def test_blast_count_does_not_change_score_at_default_weight(self):
        """CRITICAL: with the default 0.0 blast weight, a nonzero blast_count
        must leave the score byte-identical to blast_count=0."""
        from tools.document_intelligence.freshness_engine import _score_doc
        kwargs = dict(
            doc_id="d", title="t", collection_id="c",
            created_at=_iso(40), latest_version_at=_iso(40),
            latest_approved_at=_iso(40), retention_days=90,
            drift_count_since_update=1, pending_section_count=2,
            tenant_id="default", classification="CUI",
        )
        base = _score_doc(**kwargs, blast_count=0)
        with_blast = _score_doc(**kwargs, blast_count=5)
        assert base.score == with_blast.score

    def test_legacy_call_matches_new_default(self):
        """Calling _score_doc with the legacy positional signature (no blast
        kwargs) yields the same score as passing blast_count=0."""
        from tools.document_intelligence.freshness_engine import _score_doc
        legacy = _score_doc(
            "d", "t", "c", _iso(40), _iso(40), _iso(40), 90, 1, 2,
            "default", "CUI",
        )
        explicit = _score_doc(
            "d", "t", "c", _iso(40), _iso(40), _iso(40), 90, 1, 2,
            "default", "CUI", blast_count=0,
        )
        assert legacy.score == explicit.score

    def test_tuned_weight_raises_score(self):
        """With a tuned-up blast weight, more changed cited entities => staler."""
        from tools.document_intelligence.freshness_engine import (
            _DEFAULT_WEIGHTS,
            _score_doc,
        )
        weights = dict(_DEFAULT_WEIGHTS)
        weights["blast_radius"] = 0.5
        common = dict(
            doc_id="d", title="t", collection_id="c",
            created_at=_iso(10), latest_version_at=_iso(10),
            latest_approved_at=_iso(10), retention_days=90,
            drift_count_since_update=0, pending_section_count=0,
            tenant_id="default", classification="CUI",
            weights=weights, blast_score_per_entity=0.2,
        )
        no_blast = _score_doc(**common, blast_count=0)
        heavy_blast = _score_doc(**common, blast_count=5)
        assert heavy_blast.score > no_blast.score
