#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 20260907221048 — docmod_findings persists the anchor (dwr-anchor-02).

dwr-anchor-01 made every docmod pack keep the CHUNK-LOCAL span it already
computed (``CandidateEntity.span_start`` / ``span_end``, with
``chunk_text[span_start:span_end] == raw_match``). ``scanner._insert_finding``
then dropped all three on the floor, because ``docmod_findings`` had no column
to put them in — a finding said WHICH entity in WHICH section, and nothing
about WHERE in the text, so the Word-style review workspace (dwr) could not
highlight it.

THREE COLUMNS, all NULLABLE with NO DEFAULT
-------------------------------------------
``anchor_start``  INTEGER  chunk-local offset where the matched text begins
``anchor_end``    INTEGER  chunk-local offset where it ends (exclusive)
``anchor_text``   TEXT     the text at those offsets, AS IT WAS when the
                           finding was written

NULL means NOT ANCHORED — the writer had no span (evidence_currency's anchor
entities, a superseding row written by the resolver, the link checker, the
cross-reference tracker) or the span it was handed did not describe the chunk.
A default of 0 would claim every unanchored finding sits at the start of its
chunk, which is the ``span_start = 0`` fabrication dwr-anchor-01 refused at the
pack layer; the column must not reintroduce it one layer down.

WHY A MIGRATION AND NOT A DDL EDIT
----------------------------------
``docmod_findings`` exists on the live PostgreSQL board with rows in it.
``CREATE TABLE IF NOT EXISTS`` never alters an existing table, so widening the
DDL in migration 257 (or tests/conftest.py) reaches only databases created
AFTER the change; the scanner's INSERT would then name three columns the live
table does not have, raise, and — under the scan loop's per-pack ``except`` —
report a scan that persisted nothing. The CLAUDE.md rule: every column in an
INSERT must exist in the LIVE schema. This migration is what reaches the
databases already running.

WHY up.py AND NOT up.sql
------------------------
SQLite has no ``ADD COLUMN IF NOT EXISTS``. Probing the catalogue and adding
exactly the columns that are missing is idempotent on both backends and never
guesses which population it faces: the live PG table (ALTER), a fresh SQLite
database that ran 257 first (ALTER), or a database on which this already ran
(no-op). The table is append-only (constants.APPEND_ONLY_TABLES); adding a
nullable column rewrites no row.

A database that has no ``docmod_findings`` at all is left alone: migration
257 owns the CREATE, runs before this one in version order, and a table this
migration invented would carry a shape 257 does not declare.
"""
from __future__ import annotations

TABLE = "docmod_findings"

# Declaration order. Nullable, no default — see the module docstring.
NEW_COLUMNS = (
    ("anchor_start", "INTEGER"),
    ("anchor_end", "INTEGER"),
    ("anchor_text", "TEXT"),
)


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
    """Column names the LIVE table carries, read from the catalogue."""
    backend = backend or _backend(conn)
    if backend == "postgresql":
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (TABLE,),
        ).fetchall()
        return {dict(r)["column_name"] for r in rows}
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
        # 257 owns the CREATE and precedes this migration; nothing to widen.
        return
    present = existing_columns(conn, backend)
    for name, sql_type in NEW_COLUMNS:
        if name in present:
            continue
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN {name} {sql_type}")
    conn.commit()
