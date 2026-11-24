# CUI // SP-CTI
"""Migration 20261118104743 — the FORGE Academy CoP tracks ledger (icx-cop-01).

Two append-only tables (``tools/idp/cop_tracks.py``):

* ``cop_academy_learners``: one row per learner, their pseudonymous xAPI account name (random, kept once, never
  derived from who they are). Nothing is updated: the name a learner first got is the name every statement carries.
* ``cop_academy_events``: every event, on hash chains by ``scope``. A learner's own chain (``learner:<pseudonym>``)
  holds their mission attempts, graded on the server: ``attempted`` (not passed) or ``completed`` (passed). The
  ``bundle`` chain holds the xAPI batches for ICDEV[OS]: ``prepared`` (anyone, or an agent, built the
  ``icos-cop-academy/v1`` batch and recorded it whole, in ``bundle_json``, at its digest), then ``published`` or
  ``refused`` (a person who did not prepare it took the declared step decl-wft-cop-academy-xapi-publish, ``step_id``
  in ``wf_decision_steps``, bound to the same digest).

Each row carries the hash of its scope's previous row; ``UNIQUE(scope, prev_hash)`` stops a chain forking.
Immutable: SQLite RAISE(ABORT) triggers, or a PostgreSQL trigger function, on UPDATE and DELETE of both tables.
Every statement is IF NOT EXISTS / OR REPLACE, so a re-run converges. The PostgreSQL message is concatenated,
never a RAISE format string (the storage layer rewrites `%`).
"""
from __future__ import annotations

_TAG = "[20261118104743_cop_academy_events]"

TABLES = ("cop_academy_events", "cop_academy_learners")

DDL = (
    """CREATE TABLE IF NOT EXISTS cop_academy_learners (
    user_id         TEXT PRIMARY KEY,
    pseudonym       TEXT NOT NULL UNIQUE,
    created_at      TEXT NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS cop_academy_events (
    event_id        TEXT PRIMARY KEY,
    scope           TEXT NOT NULL,
    event           TEXT NOT NULL CHECK(event IN ('attempted','completed','prepared','published','refused')),
    actor           TEXT NOT NULL,
    actor_kind      TEXT NOT NULL CHECK(actor_kind IN ('person','agent')),
    actor_role      TEXT NOT NULL DEFAULT '',
    learner         TEXT NOT NULL DEFAULT '',
    path_id         TEXT NOT NULL DEFAULT '',
    mission_id      TEXT NOT NULL DEFAULT '',
    step_id         TEXT NOT NULL DEFAULT '',
    reason          TEXT NOT NULL DEFAULT '',
    content_digest  TEXT NOT NULL,
    bundle_json     TEXT NOT NULL DEFAULT '',
    detail_json     TEXT NOT NULL DEFAULT '{}',
    prev_hash       TEXT NOT NULL,
    row_hash        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(scope, prev_hash)
)""",
    ("CREATE INDEX IF NOT EXISTS idx_cop_academy_events_scope "
     "ON cop_academy_events (scope, created_at)"),
)

SQLITE_TRIGGERS = tuple(
    stmt
    for table in TABLES
    for stmt in (
        (f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
         f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"),
        (f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
         f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"),
    )
)

PG_TRIGGERS = (
    """CREATE OR REPLACE FUNCTION cop_academy_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION USING MESSAGE = TG_TABLE_NAME || ' is append-only';
END;
$$ LANGUAGE plpgsql""",
) + tuple(
    stmt
    for table in TABLES
    for op in ("update", "delete")
    for stmt in (
        f"DROP TRIGGER IF EXISTS {table}_no_{op} ON {table}",
        (f"CREATE TRIGGER {table}_no_{op} BEFORE {op.upper()} ON {table} "
         "FOR EACH ROW EXECUTE FUNCTION cop_academy_immutable()"),
    )
)


def _is_pg(conn) -> bool:
    from tools.db.storage import is_pg

    return is_pg(conn)


def up(conn) -> dict:
    for stmt in DDL:
        conn.execute(stmt)
    for stmt in PG_TRIGGERS if _is_pg(conn) else SQLITE_TRIGGERS:
        conn.execute(stmt)
    conn.commit()
    print(f"{_TAG} {', '.join(TABLES)} created, append-only")
    return {"status": "applied", "tables": list(TABLES)}
