# CUI // SP-CTI
"""Migration 20261118143737 — the FOCUS cost bundle ledger (osx-cop-05).

One append-only, hash-chained table. ``finops_focus_events`` holds every event in the
FOCUS cost bundles FinOps hands ICDEV[OS]'s Cloud CoP (``tools/finops/focus_bundle.py``):
``prepared`` (anyone, or an agent, read a FOCUS export, built its ``icos-focus-cost/v1``
bundle and recorded it at its digest; the bundle is in ``bundle_json``), then ``published``
or ``refused`` (a person who did not prepare it took the declared step
decl-wft-finops-focus-publish, ``step_id`` in ``wf_decision_steps``, bound to the same
digest). Only a published bundle leaves for ICDEV[OS]; nothing is updated in place.

The chain is per billing month (``period``, YYYY-MM). Each row carries the hash of the
month's previous row; ``UNIQUE(period, prev_hash)`` is what stops two events being
appended after the same one: a month's history cannot fork.

Immutable: SQLite RAISE(ABORT) triggers, or a PostgreSQL trigger function, on UPDATE
and DELETE. Every statement is IF NOT EXISTS / OR REPLACE, so a re-run converges. The
PostgreSQL message is concatenated, never a RAISE format string (the storage layer
rewrites `%`).
"""
from __future__ import annotations

_TAG = "[20261118143737_finops_focus_events]"

TABLE = "finops_focus_events"

DDL = (
    """CREATE TABLE IF NOT EXISTS finops_focus_events (
    event_id        TEXT PRIMARY KEY,
    period          TEXT NOT NULL,
    event           TEXT NOT NULL CHECK(event IN ('prepared','published','refused')),
    actor           TEXT NOT NULL,
    actor_kind      TEXT NOT NULL CHECK(actor_kind IN ('person','agent','bootstrap')),
    actor_role      TEXT NOT NULL DEFAULT '',
    step_id         TEXT NOT NULL DEFAULT '',
    reason          TEXT NOT NULL DEFAULT '',
    content_digest  TEXT NOT NULL,
    bundle_json     TEXT NOT NULL DEFAULT '',
    detail_json     TEXT NOT NULL DEFAULT '{}',
    prev_hash       TEXT NOT NULL,
    row_hash        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(period, prev_hash)
)""",
    ("CREATE INDEX IF NOT EXISTS idx_finops_focus_events_period "
     "ON finops_focus_events (period, created_at)"),
)

SQLITE_TRIGGERS = (
    (f"CREATE TRIGGER IF NOT EXISTS {TABLE}_no_update BEFORE UPDATE ON {TABLE} "
     f"BEGIN SELECT RAISE(ABORT, '{TABLE} is append-only'); END"),
    (f"CREATE TRIGGER IF NOT EXISTS {TABLE}_no_delete BEFORE DELETE ON {TABLE} "
     f"BEGIN SELECT RAISE(ABORT, '{TABLE} is append-only'); END"),
)

PG_TRIGGERS = (
    """CREATE OR REPLACE FUNCTION finops_focus_events_immutable()
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
         "FOR EACH ROW EXECUTE FUNCTION finops_focus_events_immutable()"),
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
