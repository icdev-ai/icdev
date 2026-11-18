# CUI // SP-CTI
"""Migration 20261117231010 — the engineering standards register's lifecycle ledger (icx-coe-01).

One append-only, hash-chained table. ``coe_standard_events`` holds every event in
an engineering standard's life (``tools/idp/standards.py``): ``seeded`` (a
labelled bootstrap identity recorded a standard ICDEV already enforces, never a
person's signature), ``proposed`` (a draft, by a person or an agent), ``reviewed``
or ``returned`` (a steward's step), ``adopted`` or ``refused`` (a steward signs,
never the author), and ``deprecated``. A standard's state is the replay of its
rows; nothing is updated in place.

Each row carries the entry it is about (``entry_json``, digested), the declared
step that decided it (``step_id``, in ``wf_decision_steps``), and the hash of the
standard's previous row. ``UNIQUE(standard_id, prev_hash)`` is what stops two
events being appended after the same one: the lifecycle cannot fork.

Immutable: SQLite RAISE(ABORT) triggers, or a PostgreSQL trigger function, on
UPDATE and DELETE. Every statement is IF NOT EXISTS / OR REPLACE, so a re-run
converges. The PostgreSQL message is concatenated, never a RAISE format string
(the storage layer rewrites `%`).
"""
from __future__ import annotations

_TAG = "[20261117231010_coe_standard_events]"

TABLE = "coe_standard_events"

DDL = (
    """CREATE TABLE IF NOT EXISTS coe_standard_events (
    event_id        TEXT PRIMARY KEY,
    standard_id     TEXT NOT NULL,
    version         TEXT NOT NULL,
    event           TEXT NOT NULL
        CHECK(event IN ('seeded','proposed','reviewed','returned','adopted','refused','deprecated')),
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
    UNIQUE(standard_id, prev_hash)
)""",
    ("CREATE INDEX IF NOT EXISTS idx_coe_standard_events_standard "
     "ON coe_standard_events (standard_id, created_at)"),
)

SQLITE_TRIGGERS = (
    (f"CREATE TRIGGER IF NOT EXISTS {TABLE}_no_update BEFORE UPDATE ON {TABLE} "
     f"BEGIN SELECT RAISE(ABORT, '{TABLE} is append-only'); END"),
    (f"CREATE TRIGGER IF NOT EXISTS {TABLE}_no_delete BEFORE DELETE ON {TABLE} "
     f"BEGIN SELECT RAISE(ABORT, '{TABLE} is append-only'); END"),
)

PG_TRIGGERS = (
    """CREATE OR REPLACE FUNCTION coe_standard_events_immutable()
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
         "FOR EACH ROW EXECUTE FUNCTION coe_standard_events_immutable()"),
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
