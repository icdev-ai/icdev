# CUI // SP-CTI
"""Migration 20260908003311 — dic_documents keeps the uploaded ORIGINAL (dwr-fid-01).

``POST /document-intelligence/api/ingest`` saved the upload to a temp file and
deleted it in the ingest thread's ``finally`` while ``dic_documents.filepath``
kept pointing at the deleted path. Measured on the live PG board 2026-09-07:
55 documents, 29 with a recorded ``filepath`` that no longer exists and 15 with
none at all. Without the original there is no page geometry and no fidelity.

The upload is now copied, content-addressed, BEFORE the temp file goes
(``tools/document_intelligence/originals.py``), and these columns say where:

``original_path``         the retained file: ``<root>/<sha[:2]>/<sha><suffix>``
``original_sha256``       its content hash, so a reader can prove the retained
                          file IS the upload and detect a tampered store
``original_retained_at``  when — the timestamp a pruning policy would key on

Every column is NULLABLE with no default: NULL is NOT RETAINED (generated
in-canvas, an upload before this landed, retention switched off), and a default
would make those indistinguishable from a retained one.

WHY A PYTHON MIGRATION AND NOT up.sql
-------------------------------------
``dic_documents`` is created by ``ingest_orchestrator._SCHEMA`` (``CREATE
TABLE IF NOT EXISTS`` on first use of the canvas) and by nothing in
``init_icdev_db.py``, so the populations this has to face are:

  * live PG                          the table exists, old shape -> ALTER, 3 columns
  * a SQLite db the canvas has used  same
  * a database the canvas has never  no table -> CREATE the whole shape
    touched

SQLite has no ``ADD COLUMN IF NOT EXISTS`` and a bare ALTER on a missing table
raises, so a directive-split up.sql would have to guess. Probing the catalogue
and adding exactly the missing columns does not. The ingest DDL carries the
full shape for databases whose table is created after this lands; this
migration is what reaches the ones already running.
"""
from __future__ import annotations

TABLE = "dic_documents"

# Declaration order. Kept in step with originals.ORIGINAL_COLUMNS by test.
NEW_COLUMNS = (
    ("original_path", "TEXT"),
    ("original_sha256", "TEXT"),
    ("original_retained_at", "TEXT"),
)

# The full shape, for a database that does not have the table at all. Mirrors
# ingest_orchestrator._SCHEMA — plain TEXT/INTEGER types, so one string serves
# both backends. The post-creation columns migrations 2xx added on PG
# (owner_id, summary, template_type, writeguard_mode, source_wg_result_id,
# source_idr_session_id, origin, status) are NOT declared here: their own
# migrations create them, and a shape that restated them would drift.
CREATE_FULL = """
CREATE TABLE IF NOT EXISTS dic_documents (
    doc_id               TEXT PRIMARY KEY,
    collection_id        TEXT NOT NULL,
    source_id            TEXT,
    filename             TEXT,
    filepath             TEXT,
    content_type         TEXT,
    provider             TEXT,
    title                TEXT,
    byte_size            INTEGER,
    content_sha256       TEXT,
    page_count           INTEGER DEFAULT 1,
    created_at           TEXT NOT NULL,
    tenant_id            TEXT,
    classification       TEXT,
    original_path        TEXT,
    original_sha256      TEXT,
    original_retained_at TEXT
)
"""


def _backend(conn) -> str:
    return getattr(conn, "_backend", "sqlite")


def _table_present(conn, backend: str) -> bool:
    if backend == "postgresql":
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s",
            (TABLE,),
        ).fetchone()
        return row is not None
    # pg-portability: sqlite-only path — sqlite_master is the SQLite catalogue;
    # the PostgreSQL branch above reads information_schema instead.
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=%s", (TABLE,)
    ).fetchone()
    return row is not None


def existing_columns(conn, backend: str | None = None) -> set:
    """The column names the LIVE table carries — what an INSERT may name."""
    backend = backend or _backend(conn)
    if backend == "postgresql":
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (TABLE,),
        ).fetchall()
        out = set()
        for r in rows:
            d = dict(r) if hasattr(r, "keys") else None
            out.add(d["column_name"] if d and "column_name" in d else r[0])
        return out
    # pg-portability: sqlite-only path — PRAGMA has no information_schema
    # equivalent and vice versa.
    rows = conn.execute(f"PRAGMA table_info({TABLE})").fetchall()
    out = set()
    for r in rows:
        d = dict(r) if hasattr(r, "keys") else None
        out.add(d["name"] if d and "name" in d else r[1])
    return out


def up(conn):
    backend = _backend(conn)

    if not _table_present(conn, backend):
        conn.execute(CREATE_FULL)
        conn.commit()
        return

    present = existing_columns(conn, backend)
    for name, sql_type in NEW_COLUMNS:
        if name in present:
            continue
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN {name} {sql_type}")
    conn.commit()


def down(conn):
    """Drop exactly the columns this migration added, if they are present.

    A table this migration CREATED is left standing: its rows are the canvas's
    documents, and dropping a table is not the inverse of adding columns. The
    retained FILES are never touched by a rollback -- they are the originals.
    """
    backend = _backend(conn)
    if not _table_present(conn, backend):
        return
    present = existing_columns(conn, backend)
    for name, _ in reversed(NEW_COLUMNS):
        if name not in present:
            continue
        conn.execute(f"ALTER TABLE {TABLE} DROP COLUMN {name}")
    conn.commit()
