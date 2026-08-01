# CUI // SP-CTI
"""SQLiteVectorStore must never reach a non-SQLite backend.

`storage.get_connection(db_path=...)` dispatches on ICDEV_STORAGE_BACKEND and
**ignores db_path** when that says postgresql. `SQLiteVectorStore._get_conn()`
used it, so on a PG deployment the class whose entire implementation is
SQLite-specific — `?` placeholders, float16 blob decode, the `sign_bits` column —
silently talked to PostgreSQL.

Observed 2026-07-26 while building a SQLite corpus for oss-meas-01: constructing
the store sent `_init_schema()`'s

    ALTER TABLE rag_chunks ADD COLUMN sign_bits BLOB

at the *production* PG table. Only a lock timeout stopped it landing. These tests
pin the backend so a class named for its storage engine cannot reach another one.
"""
from __future__ import annotations

import sqlite3

import pytest

from tools.rag.sqlite_vector_store import SQLiteVectorStore
from tools.rag.vector_store_provider import VectorChunk


@pytest.fixture
def pg_configured(monkeypatch):
    """Make the global backend say postgresql, as a real PG deployment does."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")


def _chunk(i: int) -> VectorChunk:
    return VectorChunk(
        chunk_id=f"c{i}",
        content=f"content {i}",
        content_hash=f"h{i}",
        embedding=[0.1 * (i + 1)] * 8,
        source_type="test",
    )


def test_get_conn_returns_real_sqlite_even_when_backend_says_postgres(
    pg_configured, tmp_path
):
    """The load-bearing assertion: a genuine sqlite3.Connection, not a router."""
    store = SQLiteVectorStore(db_path=tmp_path / "v.db")
    conn = store._get_conn()
    try:
        # The StorageConnection wrapper is retained (upsert uses %s placeholders
        # and needs its translation) but must be pinned to sqlite, not resolved.
        assert getattr(conn, "_backend", None) == "sqlite", (
            f"backend is {getattr(conn, '_backend', None)!r} — _get_conn resolved "
            "the backend instead of pinning it"
        )
    finally:
        conn.close()


def test_storage_get_connection_is_not_used_for_the_store_file(
    pg_configured, tmp_path, monkeypatch
):
    """Prove it by making the shared helper explode if the store calls it.

    This is what would have caught the original defect: the old implementation
    could not open its own file without going through the router.
    """
    import importlib

    # Patch the name AS BOUND IN THE STORE MODULE. `from tools.db.storage import
    # get_connection` binds a module-local reference at import time, so patching
    # tools.db.storage leaves the store's copy untouched — the first version of
    # this test did exactly that and passed against the broken implementation.
    store_mod = importlib.import_module("tools.rag.sqlite_vector_store")

    def _boom(*args, **kwargs):
        raise AssertionError(
            "SQLiteVectorStore reached storage.get_connection() for its own file "
            "— on a PG deployment that routes its DDL at the production database"
        )

    monkeypatch.setattr(store_mod, "get_connection", _boom)

    store = SQLiteVectorStore(db_path=tmp_path / "v.db")   # runs _init_schema()
    assert store.upsert([_chunk(0), _chunk(1)]) == 2
    assert store.count() == 2


def test_schema_lands_in_the_given_file_not_elsewhere(pg_configured, tmp_path):
    """_init_schema() must create rag_chunks in the store's own db_path."""
    path = tmp_path / "vectors.db"
    SQLiteVectorStore(db_path=path)

    assert path.exists(), "store did not create its own database file"
    raw = sqlite3.connect(path)
    try:
        cols = {r[1] for r in raw.execute("PRAGMA table_info(rag_chunks)")}
    finally:
        raw.close()
    assert "sign_bits" in cols, "sign_bits column missing from the store's own file"
    assert "embedding" in cols


def test_round_trip_under_pg_backend(pg_configured, tmp_path):
    """Full write/read cycle while the global backend claims postgresql."""
    store = SQLiteVectorStore(db_path=tmp_path / "v.db")
    store.upsert([_chunk(i) for i in range(3)])

    hits = store.search([0.1] * 8, top_k=3)
    assert len(hits) == 3
    assert {h.chunk_id for h in hits} == {"c0", "c1", "c2"}
    assert store.delete(["c0"]) == 1
    assert store.count() == 2


def test_parent_directory_is_created(pg_configured, tmp_path):
    nested = tmp_path / "a" / "b" / "v.db"
    store = SQLiteVectorStore(db_path=nested)
    assert store.upsert([_chunk(0)]) == 1
    assert nested.exists()
