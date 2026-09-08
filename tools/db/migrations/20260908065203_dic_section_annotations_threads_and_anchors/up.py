# CUI // SP-CTI
"""Migration 20260908065203 — dic_section_annotations becomes threaded and anchored.

dwr-cmt-01. The table already had ``selected_text``, ``category``, ``author``
and an open/resolved lifecycle with ``resolution_note``/``resolved_by``/
``resolved_at``. Measured on the live PG board 2026-09-08 it had 14 columns,
ZERO ROWS — it has never been written to — and, more to the point, NO
MIGRATION: it was a runtime ``CREATE TABLE IF NOT EXISTS`` inside
``blueprint.py`` (``_ensure_dic_annotations``), so a deployment where that
``_ensure`` had run was indistinguishable from one where it had not. Nothing
could say which shape a given database carried, and nothing could roll one
forward.

WHAT EACH COLUMN IS FOR
-----------------------
``parent_ann_id``  the thread. A ROOT row has NULL here and carries the anchor
                   and the thread's lifecycle; a REPLY names its root. Threads
                   are FLAT: a reply to a reply is refused by the store, so this
                   column is one level deep by construction.
``anchor_start``   chunk-local offsets into the section's ``content``, the
``anchor_end``     convention of ``claim_lifecycle`` and of ``dic_suggestions``
                   (migration 20260907213944).
``anchor_text``    the VERBATIM slice. ``content[start:end]`` MUST equal it;
                   the store checks it at write time and RE-DERIVES it on every
                   read, showing a comment whose anchor no longer matches as
                   ORPHANED rather than re-pointing it at whatever now sits at
                   those offsets.
``anchor_basis``   RECORDED, never inferred: exact | relocated | unanchored.
                   ``unanchored`` is a SECTION-LEVEL comment and is not a
                   defect; it is how a remark about the whole section is stored.
``tenant_id``      every other ``dic_*`` table has one, and this table's reads
                   were unscoped.

There is deliberately NO ``anchor_section_id``: the row already carries
``section_id`` NOT NULL, and that IS the section the offsets index. Two
spellings of one fact drift.

Every column is NULLABLE with no default. NULL is NOT RECORDED — a default
would make a row nobody anchored indistinguishable from one somebody did.

WHY A PYTHON MIGRATION AND NOT up.sql
-------------------------------------
``dic_section_annotations`` is created LAZILY (``CREATE TABLE IF NOT EXISTS`` on
first request) and by nothing in ``init_icdev_db.py``, so the populations this
has to face are:

  * live PG                          the table exists, old shape -> ALTER, 6 columns
  * a SQLite db the blueprint used   same
  * a database nothing has touched   no table -> CREATE the whole shape

SQLite has no ``ADD COLUMN IF NOT EXISTS`` and a bare ALTER on a missing table
raises, so a directive-split up.sql would have to guess. Probing the catalogue
and adding exactly the missing columns does not. This mirrors migration
20260907213944, which faced the same three populations for ``dic_suggestions``.
"""
from __future__ import annotations

TABLE = "dic_section_annotations"

# Declaration order. Kept in step with annotation_store.NEW_COLUMNS by test.
NEW_COLUMNS = (
    ("parent_ann_id", "TEXT"),
    ("anchor_start", "INTEGER"),
    ("anchor_end", "INTEGER"),
    ("anchor_text", "TEXT"),
    ("anchor_basis", "TEXT"),
    ("tenant_id", "TEXT"),
)

# The full shape, for a database that does not have the table at all. Mirrors
# annotation_store.CREATE_FULL — plain TEXT/INTEGER types, so one string serves
# both backends.
CREATE_FULL = """
CREATE TABLE IF NOT EXISTS dic_section_annotations (
    ann_id          TEXT    PRIMARY KEY,
    section_id      TEXT    NOT NULL,
    doc_id          TEXT    NOT NULL,
    selected_text   TEXT    NOT NULL DEFAULT '',
    category        TEXT    NOT NULL,
    comment         TEXT    NOT NULL,
    author          TEXT    NOT NULL DEFAULT 'reviewer',
    status          TEXT    NOT NULL DEFAULT 'open',
    resolution_note TEXT,
    resolved_by     TEXT,
    resolved_at     TEXT,
    classification  TEXT    DEFAULT 'CUI',
    created_at      TEXT    NOT NULL,
    updated_at      TEXT,
    parent_ann_id   TEXT,
    anchor_start    INTEGER,
    anchor_end      INTEGER,
    anchor_text     TEXT,
    anchor_basis    TEXT,
    tenant_id       TEXT
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

    A table this migration CREATED is left standing: its rows are review
    comments, and dropping a table is not the inverse of adding columns.
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
