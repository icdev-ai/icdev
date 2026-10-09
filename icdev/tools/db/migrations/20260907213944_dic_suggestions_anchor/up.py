# CUI // SP-CTI
"""Migration 20260907213944 — dic_suggestions becomes an anchored, addressable change.

dwr-anchor-03. A suggestion row said WHAT to write and, through ``section_id``,
roughly WHERE — and on the live PG board (measured 2026-09-07) 58 of 58 rows
carried an EMPTY ``section_id``, because the redline drafter was never handed a
span to point at. Accepting one ran ``UPDATE dic_sections ... WHERE section_id
= ''``, matched zero rows, and reported success. The table is extended rather
than replaced because it already carries the append-only
``dic_suggestion_decisions`` chain, ``_record_hitl_decision``, the role gates
and the decision-before-mutation ordering in ``blueprint.py``; a parallel
change table would fork all of that.

WHAT EACH COLUMN IS FOR
-----------------------
``anchor_section_id``  the section the span lives in. NOT NULL for any
                       suggestion that can be applied; NULL means nobody has
                       resolved where this change goes.
``anchor_start``       chunk-local offsets into that section's content, the
``anchor_end``         convention of ``claim_lifecycle`` (:26-30).
``anchor_text``        the VERBATIM slice: ``content[start:end]`` MUST equal it.
                       The accept path re-derives this against the live section
                       and refuses on drift (dwr-anchor-05).
``anchor_basis``       RECORDED, never inferred:
                         exact       the writer computed the span from a match
                         relocated   recovered post-hoc by ``str.find`` — an
                                     honest guess, and only when the text occurs
                                     ONCE in the section
                         unanchored  no span, or an ambiguous one. Never
                                     applied.
``origin_kind``        docmod_redline | section_draft | crowdsource | human_edit.
                       NULL means the writer did not say (rows written before
                       this migration).
``applied_text``       what was ACTUALLY written on edit-then-accept. The AI
``applied_by``         draft passed the TRUST gates; a human rewrite did not, so
                       the two are stored apart and provenance says which
                       shipped. NULL until an accept applies something.

Every column is NULLABLE with no default: NULL is NOT RECORDED, and a default
would make a row no writer ever anchored indistinguishable from one it did.

WHY A PYTHON MIGRATION AND NOT up.sql
-------------------------------------
``dic_suggestions`` is created LAZILY by ``suggestion_store._ensure_tables``
(``CREATE TABLE IF NOT EXISTS`` on first use) and by nothing in
``init_icdev_db.py``, so the populations this has to face are:

  * live PG                         the table exists, old shape -> ALTER, 8 columns
  * a SQLite db the store has used  same
  * a database the store has never  no table -> CREATE the whole shape
    touched

SQLite has no ``ADD COLUMN IF NOT EXISTS`` and a bare ALTER on a missing table
raises, so a directive-split up.sql would have to guess. Probing the catalogue
and adding exactly the missing columns does not. ``_ensure_tables`` carries the
full shape for databases whose table is created after this lands; this
migration is what reaches the ones already running.
"""
from __future__ import annotations

TABLE = "dic_suggestions"

# Declaration order. Kept in step with suggestion_store.ANCHOR_COLUMNS by test.
NEW_COLUMNS = (
    ("anchor_section_id", "TEXT"),
    ("anchor_start", "INTEGER"),
    ("anchor_end", "INTEGER"),
    ("anchor_text", "TEXT"),
    ("anchor_basis", "TEXT"),
    ("origin_kind", "TEXT"),
    ("applied_text", "TEXT"),
    ("applied_by", "TEXT"),
)

# The full shape, for a database that does not have the table at all. Mirrors
# suggestion_store._ensure_tables — plain TEXT/INTEGER types, so one string
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
    applied_by          TEXT
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
