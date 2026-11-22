# CUI // SP-CTI
"""Migration 20261118231119 — the AI-assisted vulnerability discovery ledger (icx-ent-01).

One append-only, hash-chained table. ``vr_events`` holds every event of ``tools/vulnresearch``, one hash chain per
stream (``target:<id>``, ``run:<id>``, ``patch:<finding id>``, ``bundle:<target id>``): a target registered, then
a person authorises or refuses it (decl-wft-vr-target-authorise) and may revoke it (a stop); a discovery run's
whole result at its digest; a measurement on the labelled benchmark; a patch proposed, then a person accepts or
rejects it (decl-wft-vr-patch-review); a findings bundle prepared, then a person who did not prepare it publishes
or refuses it (decl-wft-vr-findings-publish). ``step_id`` names the declared step in ``wf_decision_steps``.

``UNIQUE(stream, prev_hash)`` stops a stream forking. Immutable: SQLite RAISE(ABORT) triggers, or a PostgreSQL
trigger function, on UPDATE and DELETE. The CHECK lists are derived from tools/vulnresearch/constants.py. Every
statement is IF NOT EXISTS / OR REPLACE, so a re-run converges. The PostgreSQL message is concatenated, never a
RAISE format string (the storage layer rewrites `%`).
"""
from __future__ import annotations

from tools.vulnresearch.constants import ACTOR_KINDS, EVENTS

_TAG = "[20261118231119_vr_events]"

TABLE = "vr_events"


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


DDL = (
    f"""CREATE TABLE IF NOT EXISTS vr_events (
    event_id        TEXT PRIMARY KEY,
    stream          TEXT NOT NULL,
    event           TEXT NOT NULL CHECK(event IN ({_in(EVENTS)})),
    subject         TEXT NOT NULL,
    actor           TEXT NOT NULL,
    actor_kind      TEXT NOT NULL CHECK(actor_kind IN ({_in(ACTOR_KINDS)})),
    actor_role      TEXT NOT NULL DEFAULT '',
    step_id         TEXT NOT NULL DEFAULT '',
    reason          TEXT NOT NULL DEFAULT '',
    content_digest  TEXT NOT NULL DEFAULT '',
    payload_json    TEXT NOT NULL DEFAULT '{{}}',
    prev_hash       TEXT NOT NULL,
    row_hash        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(stream, prev_hash)
)""",
    "CREATE INDEX IF NOT EXISTS idx_vr_events_stream ON vr_events (stream, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_vr_events_subject ON vr_events (subject, created_at)",
)

SQLITE_TRIGGERS = (
    (f"CREATE TRIGGER IF NOT EXISTS {TABLE}_no_update BEFORE UPDATE ON {TABLE} "
     f"BEGIN SELECT RAISE(ABORT, '{TABLE} is append-only'); END"),
    (f"CREATE TRIGGER IF NOT EXISTS {TABLE}_no_delete BEFORE DELETE ON {TABLE} "
     f"BEGIN SELECT RAISE(ABORT, '{TABLE} is append-only'); END"),
)

PG_TRIGGERS = (
    """CREATE OR REPLACE FUNCTION vr_events_immutable()
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
         "FOR EACH ROW EXECUTE FUNCTION vr_events_immutable()"),
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
