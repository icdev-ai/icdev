# CUI // SP-CTI
"""Migration 20261114162849 — declared decision steps and finding adjudications (icx-cnf-02).

Two append-only tables.

``wf_decision_steps`` is the ledger of every decision a person takes through a
declared template (``args/workflow_hitl_templates/*.yaml``). One row per event:
``proposed`` (an agent, a service key or a person without the stage's role asked),
``decided`` (a person holding the stage took it), ``spent`` (a machine caller
used the decided step once), ``withdrawn`` (the proposal's own effect failed).
``UNIQUE(step_id, event)`` is what stops a step being decided or spent twice.
The wf_instances row each decision opens lives in the engine's own tables; this
ledger links the two.

``cortex_finding_adjudications`` is a person's ruling on one Cortex resolution
finding: accept or reject, with a reason, by whom, through which step. One per
finding (``UNIQUE(tenant_id, finding_id)``): a finding goes open -> adjudicated
once. A disagreement whose claims change is a NEW finding id, so it opens again.

Both are immutable: SQLite RAISE(ABORT) triggers, or a PostgreSQL trigger
function, on UPDATE and DELETE. Every statement is IF NOT EXISTS / OR REPLACE,
so a re-run converges.
"""
from __future__ import annotations

_TAG = "[20261114162849_wf_declared_decisions]"

TABLES = ("wf_decision_steps", "cortex_finding_adjudications")

DDL = (
    """CREATE TABLE IF NOT EXISTS wf_decision_steps (
    event_id        TEXT PRIMARY KEY,
    step_id         TEXT NOT NULL,
    event           TEXT NOT NULL
        CHECK(event IN ('proposed','decided','spent','withdrawn')),
    template_id     TEXT NOT NULL,
    surface         TEXT NOT NULL,
    subject         TEXT NOT NULL DEFAULT '',
    actor           TEXT NOT NULL,
    actor_role      TEXT NOT NULL DEFAULT '',
    decision        TEXT NOT NULL DEFAULT '',
    affirmative     INTEGER NOT NULL DEFAULT 0,
    reason          TEXT NOT NULL DEFAULT '',
    instance_id     TEXT NOT NULL DEFAULT '',
    payload_digest  TEXT NOT NULL DEFAULT '',
    payload_json    TEXT NOT NULL DEFAULT '{}',
    created_at      TIMESTAMP NOT NULL,
    UNIQUE(step_id, event)
)""",
    ("CREATE INDEX IF NOT EXISTS idx_wf_decision_steps_event "
     "ON wf_decision_steps (event, created_at)"),
    ("CREATE INDEX IF NOT EXISTS idx_wf_decision_steps_template "
     "ON wf_decision_steps (template_id, surface)"),
    """CREATE TABLE IF NOT EXISTS cortex_finding_adjudications (
    adjudication_id TEXT PRIMARY KEY,
    finding_id      TEXT NOT NULL,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    decision        TEXT NOT NULL CHECK(decision IN ('accept','reject')),
    reason          TEXT NOT NULL,
    adjudicated_by  TEXT NOT NULL,
    step_id         TEXT NOT NULL,
    instance_id     TEXT NOT NULL DEFAULT '',
    adjudicated_at  TIMESTAMP NOT NULL,
    UNIQUE(tenant_id, finding_id)
)""",
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
    """CREATE OR REPLACE FUNCTION wf_declared_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql""",
) + tuple(
    stmt
    for table in TABLES
    for op in ("update", "delete")
    for stmt in (
        f"DROP TRIGGER IF EXISTS {table}_no_{op} ON {table}",
        (f"CREATE TRIGGER {table}_no_{op} BEFORE {op.upper()} ON {table} "
         "FOR EACH ROW EXECUTE FUNCTION wf_declared_immutable()"),
    )
)


def _is_pg(conn) -> bool:
    from tools.db.storage import is_pg

    return is_pg(conn)


def up(conn) -> dict:
    for stmt in DDL:
        conn.execute(stmt)
    triggers = PG_TRIGGERS if _is_pg(conn) else SQLITE_TRIGGERS
    for stmt in triggers:
        conn.execute(stmt)
    conn.commit()
    print(f"{_TAG} {', '.join(TABLES)} created, append-only")
    return {"status": "applied", "tables": list(TABLES)}
