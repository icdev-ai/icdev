# CUI // SP-CTI
"""Migration 20260908071432 - a retired suggestion NAMES the one that replaced it.

dwr-ev-03. ``dic_suggestions.status`` has admitted ``superseded`` since the
table was written (``suggestion_store._VALID_STATUSES``) and NOTHING has ever
written it: ``decide_suggestion`` accepts only ``accepted``/``rejected``. So a
change that a human asked to be redrafted had nowhere to go -- leaving it
``pending`` beside its replacement puts two live proposals for one span in
front of the next reviewer, and overwriting it in place destroys the draft the
reviewer's comments were about.

WHAT EACH COLUMN IS FOR
-----------------------
``superseded_by``      the ``suggestion_id`` that REPLACED this row. Required
                       by ``suggestion_store.supersede_suggestion``, which
                       refuses an empty successor and refuses a self-reference.
                       A status of ``superseded`` with no successor is a dead
                       end a reader cannot follow, and it makes a retired
                       change indistinguishable from a lost one.
``superseded_reason``  free text; today ``redraft requested by <actor>``.

Both are NULLABLE with no default: NULL is NOT RECORDED. A default would make a
row nothing ever superseded read as one that was.

A SUPERSEDE IS NOT A DISPOSITION, so nothing here touches
``dic_suggestion_decisions``. That table answers "was this reviewed?"
(cef-ui-03) and a redraft request is not a review verdict. The audit record of
WHO asked lives in ``audit_trail`` under ``dic.redraft`` (migration
20260908071433).

WHY A PYTHON MIGRATION AND NOT up.sql
-------------------------------------
The same three populations 20260907213944 faced, for the same reason:
``dic_suggestions`` is created LAZILY by ``suggestion_store._ensure_tables``
and by nothing in ``init_icdev_db.py``, SQLite has no
``ADD COLUMN IF NOT EXISTS``, and a bare ALTER against a missing table raises.
Probing the catalogue and adding exactly the missing columns does not guess.
"""
from __future__ import annotations

TABLE = "dic_suggestions"

# Declaration order. Kept in step with suggestion_store.SUPERSEDE_COLUMNS by
# test -- a second spelling of a column list is how a migration and its writer
# come to disagree about the shape of a table.
NEW_COLUMNS = (
    ("superseded_by", "TEXT"),
    ("superseded_reason", "TEXT"),
)

# The full shape, for a database that does not have the table at all. Mirrors
# suggestion_store._ensure_tables -- plain TEXT/INTEGER types, so one string
# serves both backends.
CREATE_FULL = """
CREATE TABLE IF NOT EXISTS dic_suggestions (
    suggestion_id       TEXT    PRIMARY KEY,
    section_id          TEXT,
    doc_id              TEXT,
    collection_id       TEXT,
    trigger_event_id    TEXT,
    canvas_source       TEXT    NOT NULL DEFAULT 'unknown',
    suggested_content   TEXT    NOT NULL DEFAULT '',
    current_content     TEXT,
    rationale           TEXT,
    status              TEXT    NOT NULL DEFAULT 'pending',
    created_at          TEXT    NOT NULL,
    updated_at          TEXT,
    tenant_id           TEXT,
    classification      TEXT    NOT NULL DEFAULT 'CUI',
    anchor_section_id   TEXT,
    anchor_start        INTEGER,
    anchor_end          INTEGER,
    anchor_text         TEXT,
    anchor_basis        TEXT,
    origin_kind         TEXT,
    applied_text        TEXT,
    applied_by          TEXT,
    superseded_by       TEXT,
    superseded_reason   TEXT
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
    # pg-portability: sqlite-only path - sqlite_master is the SQLite catalogue;
    # the PostgreSQL branch above reads information_schema instead.
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=%s", (TABLE,)
    ).fetchone()
    return row is not None


def existing_columns(conn, backend: str | None = None) -> set:
    """The column names the LIVE table carries - what an INSERT may name."""
    backend = backend or _backend(conn)
    if backend == "postgresql":
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (TABLE,),
        ).fetchall()
        return {dict(r)["column_name"] for r in rows}
    # pg-portability: sqlite-only path - PRAGMA has no information_schema
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

    A table this migration CREATED is left standing: its rows are the store's
    data, and dropping a table is not the inverse of adding columns.
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
