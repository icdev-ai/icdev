# CUI // SP-CTI
"""Migration 20261118070149 — the golden path ledger (icx-coe-04).

One append-only, hash-chained table. ``coe_golden_path_events`` holds every event in a
golden path's life (``tools/idp/golden_paths.py``): ``seeded`` (the labelled bootstrap
identity recorded a declared path), ``proposed`` (anyone, or an agent, drafted a version),
``drift_checked`` (someone ran the version's drift check; its result is in ``detail_json``),
then ``published`` or ``refused`` and later ``retired`` or ``kept`` (a person who did not
author the version took the declared step decl-wft-coe-golden-path-publish or -retire,
``step_id`` in ``wf_decision_steps``). Nothing is updated in place.

Each row carries the hash of the path's previous row. ``UNIQUE(path_id, prev_hash)`` is
what stops two events being appended after the same one: a path's history cannot fork.

Immutable: SQLite RAISE(ABORT) triggers, or a PostgreSQL trigger function, on UPDATE
and DELETE. Every statement is IF NOT EXISTS / OR REPLACE, so a re-run converges. The
PostgreSQL message is concatenated, never a RAISE format string (the storage layer
rewrites `%`).
"""
from __future__ import annotations

_TAG = "[20261118070149_coe_golden_path_events]"

TABLE = "coe_golden_path_events"

DDL = (
    """CREATE TABLE IF NOT EXISTS coe_golden_path_events (
    event_id        TEXT PRIMARY KEY,
    path_id         TEXT NOT NULL,
    version         TEXT NOT NULL,
    event           TEXT NOT NULL CHECK(event IN ('seeded','proposed','drift_checked','published','refused','retired','kept')),
    actor           TEXT NOT NULL,
    actor_kind      TEXT NOT NULL CHECK(actor_kind IN ('person','agent','bootstrap')),
    actor_role      TEXT NOT NULL DEFAULT '',
    step_id         TEXT NOT NULL DEFAULT '',
    reason          TEXT NOT NULL DEFAULT '',
    entry_digest    TEXT NOT NULL,
    entry_json      TEXT NOT NULL,
    detail_json     TEXT NOT NULL DEFAULT '{}',
    prev_hash       TEXT NOT NULL,
    row_hash        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(path_id, prev_hash)
)""",
    ("CREATE INDEX IF NOT EXISTS idx_coe_golden_path_events_path "
     "ON coe_golden_path_events (path_id, created_at)"),
)

SQLITE_TRIGGERS = (
    (f"CREATE TRIGGER IF NOT EXISTS {TABLE}_no_update BEFORE UPDATE ON {TABLE} "
     f"BEGIN SELECT RAISE(ABORT, '{TABLE} is append-only'); END"),
    (f"CREATE TRIGGER IF NOT EXISTS {TABLE}_no_delete BEFORE DELETE ON {TABLE} "
     f"BEGIN SELECT RAISE(ABORT, '{TABLE} is append-only'); END"),
)

PG_TRIGGERS = (
    """CREATE OR REPLACE FUNCTION coe_golden_path_events_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION USING MESSAGE = TG_TABLE_NAME || ' is append-only';
END;
$$ LANGUAGE plpgsql""",
) + tuple(
    stmt
    for op in ("update", "delete")
    for stmt in (
        f"DROP TRIGGER IF EXISTS {TABLE}_no_{op} ON {TABLE}",
        (f"CREATE TRIGGER {TABLE}_no_{op} BEFORE {op.upper()} ON {TABLE} "
         "FOR EACH ROW EXECUTE FUNCTION coe_golden_path_events_immutable()"),
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
    print(f"{_TAG} {TABLE} created, append-only")
    return {"status": "applied", "tables": [TABLE]}
