# CUI // SP-CTI
"""Migration 20260908003513 — persist the verification the generator computes (dwr-sect-02).

``doc_generator`` builds, for every drafted section, a ``citation_report`` (which
evidence chain retrieved it, whether its ``[source: ...]`` tags resolved against
that chain's own ids, what the currency screen found) and a ``verified`` flag,
returns both in the API payload — and persisted NEITHER. The section INSERT wrote
heading, content, citations_json, status and origin. Confirmed absent from every
shape of ``dic_sections`` on PG and SQLite alike (2026-09-07).

A review surface that shows a green "verified" badge must read a COLUMN. Reading
a payload that ceased to exist when the request ended is how a page ends up
asserting something nothing re-derives — ``generate.html`` was already doing
the next-worst thing and deriving ``verified`` from ``status == 'approved'``.

THE COLUMNS
-----------
``verified``         INTEGER. 1 the verifier ran and every cited claim held;
                     0 it ran and a claim did not (or the currency guard
                     tripped afterwards). NULL: THE CHECK DID NOT RUN — no
                     verifier importable, no evidence to verify against, or
                     the verifier raised before producing a verdict. "Not
                     verified" and "verified false" are DIFFERENT findings and
                     the workspace renders them differently; a 0 written for a
                     check that never ran would collapse them.
``citation_report``  TEXT, JSON — the report ``_citation_report`` builds,
                     verbatim, including ``verification.ran`` / ``.error`` so
                     the row can say WHY ``verified`` is NULL.
``abstained``        INTEGER 0/1. A decision this pipeline always makes, so it
                     is never NULL for a row the generator wrote.
``confidence``       REAL, the verifier-derived score in [0, 1]. NULL exactly
                     when ``verified`` is NULL: the in-memory 1.0 the gate
                     starts from is a threshold default, not a measurement, and
                     persisting it would be the rem-hyg-13 perfect score.

NULLABLE, NO DEFAULT, ON PURPOSE. Every ``dic_sections`` row that exists today
was written before any of this was recorded; NULL means NOT RECORDED. A default
of 0 would assert "verified: false" for sections nobody checked — the very
conflation the card exists to remove. Human-authored sections (blueprint's
upload path) keep NULL too: no verifier ever looked at them.

WHY PYTHON AND NOT up.sql
-------------------------
``dic_sections`` exists on BOTH backends — on PostgreSQL from the consolidated
schema, on SQLite from ``ingest_orchestrator``'s runtime ``CREATE TABLE IF NOT
EXISTS`` (which now carries these columns, so a fresh database needs nothing).
SQLite has no ``ADD COLUMN IF NOT EXISTS``; probing the live catalogue and adding
exactly the missing columns does not have to guess which population it faces
and is safe to re-run against either. Same discipline as 20260907215506.
"""
from __future__ import annotations

TABLE = "dic_sections"

# Declaration order. Kept in step with doc_generator.VERIFICATION_COLUMNS by test.
NEW_COLUMNS = (
    ("verified", "INTEGER"),
    ("citation_report", "TEXT"),
    ("abstained", "INTEGER"),
    ("confidence", "REAL"),
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
    """The column names the LIVE table carries — what an INSERT may name."""
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

    # A database that has never run DIC ingest or generation has no
    # dic_sections yet; ingest_orchestrator's runtime DDL creates it with these
    # columns already present. Nothing to alter, and nothing is wrong.
    if not _table_present(conn, backend):
        return

    present = existing_columns(conn, backend)
    for name, sql_type in NEW_COLUMNS:
        if name in present:
            continue
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN {name} {sql_type}")
    conn.commit()


def down(conn):
    """Drop exactly the columns this migration added, where they are present.

    SQLite gained ``ALTER TABLE ... DROP COLUMN`` only in 3.35. Where the drop is
    unavailable the column stays and stays NULL, which is inert — no reader
    requires it, and the next ``up`` finds it present and does nothing.
    """
    backend = _backend(conn)
    if not _table_present(conn, backend):
        return
    present = existing_columns(conn, backend)
    for name, _ in reversed(NEW_COLUMNS):
        if name not in present:
            continue
        try:
            conn.execute(f"ALTER TABLE {TABLE} DROP COLUMN {name}")
        except Exception:
            # Older SQLite. A rollback that fails over an unused nullable
            # column is worse than one that leaves it behind, NULL.
            pass
    conn.commit()
