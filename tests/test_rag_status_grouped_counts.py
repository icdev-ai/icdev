#!/usr/bin/env python3
# CUI // SP-CTI
"""get_status counts by source type and tier in one grouped query each.

The per-value loop issued one COUNT per registered source (64) plus one per
tier, which made /knowledge-search ~4s on the live dashboard and timed out the
E2E page load on a loaded host (qa-fail-181763bb17fe5938). The PG store also
ignored the tier filter, so every tier reported the total.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

from tools.rag import ingestion_manager
from tools.rag.sqlite_vector_store import SQLiteVectorStore


def _seed(db_path):
    store = SQLiteVectorStore(db_path=db_path)  # creates the schema
    conn = sqlite3.connect(db_path)
    rows = [("a", "dic_document", "hot"), ("b", "dic_document", "warm"), ("c", "compliance_reference", "hot")]
    conn.executemany(
        "INSERT INTO rag_chunks (id, content, content_hash, source_type, tier) VALUES (?, 'x', ?, ?, ?)",
        [(i, i, s, t) for i, s, t in rows],
    )
    conn.commit()
    conn.close()
    return store


class _CountingStore:
    """Wraps a real store and counts count() calls."""

    provider_name = "sqlite"

    def __init__(self, inner):
        self._inner = inner
        self.count_calls = 0

    def check_availability(self):
        return True

    def count(self, filters=None):
        self.count_calls += 1
        return self._inner.count(filters)

    def count_by(self, field):
        return self._inner.count_by(field)


def test_sqlite_count_by_groups(tmp_path):
    db = tmp_path / "rag.db"
    store = _seed(db)
    assert store.count_by("source_type") == {"dic_document": 2, "compliance_reference": 1}
    assert store.count_by("tier") == {"hot": 2, "warm": 1}
    assert store.count_by("id; DROP TABLE rag_chunks") is None


def test_get_status_uses_grouped_counts(tmp_path):
    db = tmp_path / "rag.db"
    store = _CountingStore(_seed(db))
    with patch.object(ingestion_manager.VectorStoreFactory, "create", return_value=store):
        status = ingestion_manager.get_status()
    assert status["by_source_type"] == {"dic_document": 2, "compliance_reference": 1}
    assert status["by_tier"] == {"hot": 2, "warm": 1}
    assert status["total_chunks"] == 3
    # Only the unfiltered total — no per-source / per-tier round trips.
    assert store.count_calls == 1


def test_pg_count_honours_tier_filter():
    from tools.rag import pg_vector_store

    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = (7,)
    store = pg_vector_store.PgVectorStore.__new__(pg_vector_store.PgVectorStore)
    with patch.object(pg_vector_store, "get_connection", return_value=conn):
        assert store.count(filters={"tier": "hot"}) == 7
    sql, params = conn.execute.call_args.args
    assert "tier = %s" in sql
    assert params == ["hot"]
