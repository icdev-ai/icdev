# CUI // SP-CTI
"""Migration 20260802145147 — re-key developer_scorecards from the project to the COMPONENT (idp-score-01-d1).

``developer_scorecards`` (tools/db/init_icdev_db.py:8484) was designed as
exactly the thing an IDP vendor sells — overall_score, letter_grade A-F, five
dimensions, dimension_details, three indexes — and then never wired. MEASURED
2026-08-02 against the live PostgreSQL: **0 rows**, and grep across the repo
finds zero writers and zero readers; the name appears only in
``init_icdev_db.py`` and ``pg_consolidated.sql``. This migration re-keys the
table so the scorer in idp-score-01-d2/d3 can populate it per component from
``args/component_registry.yaml``, rather than designing a second schema
alongside the dead one.

## What changes, and why each piece is necessary rather than tidy

1. ``component_id TEXT`` — the new key. Nullable, because the original
   (project_id, actor) grain remains valid; a scorecard is now keyed by
   *either* a project or a component, not both.

2. ``evaluated_at TEXT`` — the evaluation timestamp, distinct from
   ``created_at`` (row-insert time, ``DEFAULT now()``). idp-score-03 persists
   one evaluation per component *per window*; the window it graded and the
   moment the row was written are different facts, and ``created_at`` cannot be
   set to the former without lying about the latter. Backfilled from
   ``created_at`` so pre-existing rows sort correctly on the new index.

3. ``idx_sc_component_evaluated (component_id, evaluated_at)`` — the composite
   this task exists for. The dominant read is "score history for one
   component, newest first", which is exactly this index's shape. Its
   ``component_id`` prefix also serves the plain per-component lookup, so no
   separate single-column index is added.

4. ``project_id`` loses NOT NULL. This is not cleanup — it is load-bearing.
   A component scorecard has no project, so with ``project_id TEXT NOT NULL``
   every INSERT the scorer makes would raise, and the surrounding
   ``except Exception`` would swallow it and report success while persisting
   nothing. That is the exact failure mode CLAUDE.md's INSERT/schema-parity
   rule was written for.

5. ``overall_score`` and ``letter_grade`` lose NOT NULL. idp-score-01-d3's
   honesty rule is that any NOT_ASSESSED dimension caps the overall score at
   unassessed — which has to be representable. NULL is that representation.
   The ``letter_grade IN ('A','B','C','D','F')`` CHECK is deliberately left
   alone: a NULL satisfies a CHECK (three-valued logic), so "unassessed" needs
   no new sentinel grade in the vocabulary.

   Points 4 and 5 are the "re-key" half of the parent task and belong in a
   migration; d2-d5 are scoped to Python files only and cannot make them.

## Why Python rather than a flat .sql file

The three relaxations are ``ALTER COLUMN ... DROP NOT NULL``, which
PostgreSQL supports and SQLite does not. SQLite needs the 12-step table
rebuild instead, so the two engines take genuinely different paths — not the
same statement with a directive around it. And the guard has to be
``column_exists`` rather than catching the error: ``migration_runner`` only
tolerates a failing script when the message contains "already exists", which
is PostgreSQL's wording for a duplicate column but not SQLite's
("duplicate column name"), and ``executescript`` aborts the rest of the file
on the first raise — so a flat .sql would skip the index creation below.

Skipping the SQLite branch the way migration 329 does is not an option here:
``tests/conftest.py`` forces ``ICDEV_STORAGE_BACKEND=sqlite``, so the
integration tests d3 has to pass run against exactly the backend that would
have kept the NOT NULLs.
"""
from __future__ import annotations

from tools.db.storage import column_exists, get_connection, is_pg, table_exists

_TABLE = "developer_scorecards"
_TAG = "[20260802145147_scorecard_component_id]"

# The rebuilt SQLite definition: the original DDL from init_icdev_db.py with
# component_id/evaluated_at appended and the three NOT NULLs dropped. Kept
# column-for-column in the original order so the copy below is a plain
# positional SELECT.
_SQLITE_NEW_DDL = """
CREATE TABLE developer_scorecards__343 (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    actor TEXT,
    overall_score REAL,
    letter_grade TEXT CHECK(letter_grade IN ('A','B','C','D','F')),
    code_quality_score REAL,
    security_score REAL,
    compliance_score REAL,
    test_coverage_score REAL,
    velocity_score REAL,
    dimension_details TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now')),
    component_id TEXT,
    evaluated_at TEXT
)
"""

_SQLITE_COPY = """
INSERT INTO developer_scorecards__343 (
    id, project_id, actor, overall_score, letter_grade,
    code_quality_score, security_score, compliance_score,
    test_coverage_score, velocity_score, dimension_details,
    classification, created_at, component_id, evaluated_at
)
SELECT
    id, project_id, actor, overall_score, letter_grade,
    code_quality_score, security_score, compliance_score,
    test_coverage_score, velocity_score, dimension_details,
    classification, created_at, NULL, created_at
FROM developer_scorecards
"""

# Recreated after the SQLite rebuild (DROP TABLE takes its indexes with it) and
# created idempotently on PostgreSQL, where the first three already exist.
_INDEXES = (
    ("idx_sc_project",
     "CREATE INDEX IF NOT EXISTS idx_sc_project ON developer_scorecards(project_id)"),
    ("idx_sc_actor",
     "CREATE INDEX IF NOT EXISTS idx_sc_actor ON developer_scorecards(actor)"),
    ("idx_sc_created",
     "CREATE INDEX IF NOT EXISTS idx_sc_created ON developer_scorecards(created_at)"),
    ("idx_sc_component_evaluated",
     "CREATE INDEX IF NOT EXISTS idx_sc_component_evaluated "
     "ON developer_scorecards(component_id, evaluated_at)"),
)

_NEW_COLUMNS = (("component_id", "TEXT"), ("evaluated_at", "TEXT"))
_DROP_NOT_NULL = ("project_id", "overall_score", "letter_grade")


def _up_postgres(conn) -> None:
    for column, coltype in _NEW_COLUMNS:
        if column_exists(conn, _TABLE, column):
            print(f"{_TAG} {_TABLE}.{column} already present")
        else:
            conn.execute(f"ALTER TABLE {_TABLE} ADD COLUMN {column} {coltype}")
            print(f"{_TAG} added {_TABLE}.{column}")

    for column in _DROP_NOT_NULL:
        # A no-op when the column is already nullable, so this stays idempotent
        # across re-runs without needing to read attnotnull first.
        conn.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN {column} DROP NOT NULL")
    print(f"{_TAG} NOT NULL dropped on {', '.join(_DROP_NOT_NULL)}")


def _up_sqlite(conn) -> None:
    """The 12-step rebuild: SQLite cannot drop a NOT NULL in place."""
    conn.execute("DROP TABLE IF EXISTS developer_scorecards__343")
    conn.execute(_SQLITE_NEW_DDL)
    conn.execute(_SQLITE_COPY)
    conn.execute(f"DROP TABLE {_TABLE}")
    conn.execute(f"ALTER TABLE developer_scorecards__343 RENAME TO {_TABLE}")
    print(f"{_TAG} rebuilt {_TABLE} with component_id/evaluated_at, NOT NULL relaxed")


def up(conn=None) -> None:
    own = conn is None
    conn = conn or get_connection()
    try:
        if not table_exists(conn, _TABLE):
            # A database built from tests/conftest.py's MINIMAL_ICDEV_SCHEMA
            # rather than init_icdev_db.py. Creating the table here would fork a
            # second definition away from the one that owns it.
            print(f"{_TAG} {_TABLE} absent; nothing to re-key")
            return

        if column_exists(conn, _TABLE, "component_id") and not is_pg(conn):
            # The SQLite rebuild is not idempotent — a second pass would copy
            # from the already-migrated table. PostgreSQL's per-statement
            # guards below are, so it falls through.
            print(f"{_TAG} {_TABLE}.component_id already present")
        elif is_pg(conn):
            _up_postgres(conn)
        else:
            _up_sqlite(conn)

        # Existing rows predate the split, so the only evaluation time on
        # record for them is when the row was written.
        # Table name written out rather than interpolated: bandit's B608 flags
        # an f-string in a DML statement even when the substitution is a module
        # constant, and a literal is the honest way to satisfy it.
        conn.execute(
            "UPDATE developer_scorecards SET evaluated_at = created_at "
            "WHERE evaluated_at IS NULL AND created_at IS NOT NULL"
        )

        for name, sql in _INDEXES:
            try:
                conn.execute(sql)
            except Exception as exc:  # noqa: BLE001 — an index is not worth failing on
                print(f"{_TAG} index {name} skipped: {exc}")

        conn.commit()
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    up()
