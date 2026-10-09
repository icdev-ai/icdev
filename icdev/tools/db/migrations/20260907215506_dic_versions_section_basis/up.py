# CUI // SP-CTI
"""Migration 20260907215506 — how a version's sections were arrived at (dwr-sect-01).

``ingest_file`` now derives ``dic_sections`` from the document's own headings, so
an ingested document finally has an editing and anchoring coordinate space
instead of an empty Sections list. (Measured on the live PG board 2026-09-07:
**16 of 55 documents carried any ``dic_sections`` row at all.**)

WHY A COLUMN AND NOT A COUNT
----------------------------
A section COUNT cannot answer the question a reader actually has, because two
very different documents both produce exactly one section:

    a document with one heading          -> 1 section
    a document with NO heading found     -> 1 section, the whole-document fallback

Those are different facts and they lead to different fixes — the first is a
short document, the second is an extraction that found no structure and may need
a better extractor or a look at the source. With only ``COUNT(*)`` on
``dic_sections`` they are indistinguishable, and the fallback silently reads as
a well-structured one-section document.

``section_basis`` records which happened:

    headings        the document's own headings were used
    whole_document  no heading was found; the whole text is one section
    empty           there was no text. NO section was fabricated.

The vocabulary is DERIVED from ``section_deriver.SECTION_BASES`` rather than
respelled here, so the constant and the constraint cannot drift apart — the same
discipline migration ``20260903100336`` applies to ``TEMPLATE_TYPES``.

NULLABLE, NO DEFAULT, ON PURPOSE
--------------------------------
Every ``dic_versions`` row that exists today was written before sections were
derived at all. NULL means NOT RECORDED, and it must stay distinguishable from a
measured verdict: back-filling ``'whole_document'`` would assert that we looked
at 32 existing versions and found no headings, which nobody has done. Rows
written from here on carry a real basis.

WHY PYTHON AND NOT up.sql
-------------------------
``dic_versions`` exists on BOTH backends — on PostgreSQL from the consolidated
schema, and on SQLite from ``ingest_orchestrator``'s runtime ``CREATE TABLE IF
NOT EXISTS``. PostgreSQL has ``ADD COLUMN IF NOT EXISTS``; SQLite does not, and
re-running a bare ``ALTER TABLE ... ADD COLUMN`` there raises. Probing the live
catalogue and adding the column only when it is genuinely absent does not have
to guess which population it is facing, and is safe to re-run against either.
"""
from __future__ import annotations

TABLE = "dic_versions"
COLUMN = "section_basis"
COLUMN_TYPE = "TEXT"


def _backend(conn) -> str:
    return getattr(conn, "_backend", "sqlite")


def _existing_columns(conn, backend: str) -> set:
    if backend == "postgresql":
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (TABLE,),
        ).fetchall()
        return {dict(r)["column_name"] for r in rows}
    # pg-portability: sqlite-only path — PRAGMA has no information_schema
    # equivalent, and the PostgreSQL branch above reads information_schema.
    rows = conn.execute(f"PRAGMA table_info({TABLE})").fetchall()
    out = set()
    for r in rows:
        d = dict(r) if hasattr(r, "keys") else None
        out.add(d["name"] if d and "name" in d else r[1])
    return out


def _table_present(conn, backend: str) -> bool:
    if backend == "postgresql":
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s",
            (TABLE,),
        ).fetchone()
        return row is not None
    # pg-portability: sqlite-only path — sqlite_master is the SQLite catalogue.
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
    ).fetchone()
    return row is not None


def up(conn):
    backend = _backend(conn)

    # A database that has never run DIC ingest has no dic_versions yet;
    # ingest_orchestrator's runtime DDL creates it with this column already
    # present. Nothing to alter, and nothing is wrong.
    if not _table_present(conn, backend):
        return

    if COLUMN in _existing_columns(conn, backend):
        return

    conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} {COLUMN_TYPE}")
    conn.commit()


def down(conn):
    """Drop the column where the backend can.

    SQLite gained ``ALTER TABLE ... DROP COLUMN`` only in 3.35, and a rollback
    that silently leaves a column behind is a rollback that lied. Where the drop
    is unavailable the column stays and stays NULL, which is inert — no reader
    requires it, and the next ``up`` finds it present and does nothing.
    """
    backend = _backend(conn)
    if not _table_present(conn, backend):
        return
    if COLUMN not in _existing_columns(conn, backend):
        return
    try:
        conn.execute(f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}")
        conn.commit()
    except Exception:
        # Older SQLite. Leaving an unused nullable column is safe; failing the
        # rollback over it is not.
        pass
