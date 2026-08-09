# CUI // SP-CTI
"""Migration 20260809201320 — ``agent_findings``: what a detection rule saw.

agov-det-05. The DET epic adds a declarative rule engine over the agent activity
ICDEV already writes (``hook_events``, ``agent_executions``, ``ai_telemetry``,
``audit_trail``, ``ace_audit_log``). This table is where a rule match lands.

## Why a dedicated table rather than hook_events

Same reasoning as ``agent_approval_log`` (migration 20260803002224). A finding's
whole value is being queryable along the axes a reviewer asks about — "every
critical finding for rule X in session Y last week" — and burying that in an
untyped JSON payload makes it evidence nobody can index. CLAUDE.md also forbids
SQLite-dialect JSON SQL in runtime call sites, so a JSON-blob design would force
every read through a Python parse of the whole table.

## Append-only

A finding is an OBSERVATION: at a point in time, this rule matched these events.
That has no lifecycle. Re-evaluating the same session later is a new observation
and appends a new row; a rule that is later judged a false positive does not get
its finding edited, because a reviewer may already have acted on the row as it
was. Registered in ``APPEND_ONLY_TABLES`` in ``.claude/hooks/pre_tool_use.py``
(NIST 800-53 AU).

Note what that means for triage: this table deliberately carries no `status` or
`acknowledged` column. Mutable triage state, if it is ever wanted, belongs in a
separate mutable table keyed on ``finding_id`` — exactly the split the INBOX
epic makes between ``approval_items`` (mutable) and ``agent_approval_log``
(append-only). Conflating them is how a table ends up unable to do its job
without violating its own invariant.

## enforced AND decision, not one or the other

``decision`` is what happened to the tool call: ``observed`` (recorded, allowed)
or ``denied`` (blocked, and the caller got the rule's deny_message).
``enforced`` is what the RULE was configured to do at match time. They are not
redundant — an offline sweep (``--scan --session``) evaluates a rule that is
enforcing, but blocks nothing because the call is long over, so it writes
``enforced=true, decision=observed``. Collapsing the two would make it
impossible to tell "this rule is in monitor mode" from "this rule enforces but
this particular match was historical".

## event_ids is ordered, and that is the point

A sequence rule (agov-det-04) fires on a chain, and the reviewer's first
question is which events made the chain. Stored as a JSON array in match order,
read back with ``json.loads`` in Python — never ``json_each``, which is
SQLite-only dialect (CLAUDE.md runtime-SQL guardrail).

## tenant_id is present even though the DET card did not list it

``ManagedConnection._inject_rls`` appends a ``tenant_id = ?`` predicate to every
statement whenever a SecurityContext carrying a tenant is attached. A core table
reached through ``get_connection()`` without that column raises UndefinedColumn
on PostgreSQL for every query the moment someone sets a context — the failure
mode recorded for the studio run tables. Cheaper to have the column.
"""
from __future__ import annotations

from tools.db.storage import get_connection, table_exists

_TABLE = "agent_findings"
_TAG = "[20260809201320_agov_agent_findings]"

# severity / decision vocabularies live in tools/agent_detect/findings.py
# (SEVERITIES, DECISIONS). No CHECK constraint here on purpose: CLAUDE.md's
# "derive from Python constants, never hardcode" rule cuts both ways — a CHECK
# spelled out here is a second copy that drifts the first time the vocabulary
# grows, and the loser is a silent INSERT failure inside a best-effort except.
_DDL = """
CREATE TABLE IF NOT EXISTS agent_findings (
    finding_id      TEXT PRIMARY KEY,
    rule_id         TEXT NOT NULL,
    rule_version    TEXT NOT NULL,
    severity        TEXT NOT NULL,
    title           TEXT NOT NULL,
    session_id      TEXT,
    actor           TEXT,
    project_id      TEXT,
    event_ids       TEXT NOT NULL DEFAULT '[]',
    tags            TEXT NOT NULL DEFAULT '[]',
    enforced        INTEGER NOT NULL DEFAULT 0,
    decision        TEXT NOT NULL DEFAULT 'observed',
    tenant_id       TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
)
"""

# PostgreSQL is the primary backend. BOOLEAN is its spelling of the flag; the
# psycopg adapter round-trips a Python bool through it, and through SQLite's
# INTEGER affinity above, without the call site branching.
_DDL_PG = """
CREATE TABLE IF NOT EXISTS agent_findings (
    finding_id      TEXT PRIMARY KEY,
    rule_id         TEXT NOT NULL,
    rule_version    TEXT NOT NULL,
    severity        TEXT NOT NULL,
    title           TEXT NOT NULL,
    session_id      TEXT,
    actor           TEXT,
    project_id      TEXT,
    event_ids       TEXT NOT NULL DEFAULT '[]',
    tags            TEXT NOT NULL DEFAULT '[]',
    enforced        BOOLEAN NOT NULL DEFAULT FALSE,
    decision        TEXT NOT NULL DEFAULT 'observed',
    tenant_id       TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
)
"""

_INDEXES = (
    # "what fired in this session" — the forensic read, and what the CASE epic
    # joins on.
    ("idx_agent_findings_session",
     "CREATE INDEX IF NOT EXISTS idx_agent_findings_session "
     "ON agent_findings (session_id, created_at)"),
    # "how noisy is this rule" — the read that decides whether a rule is ready
    # to be switched to enforce in agov-det-06.
    ("idx_agent_findings_rule",
     "CREATE INDEX IF NOT EXISTS idx_agent_findings_rule "
     "ON agent_findings (rule_id, created_at)"),
    # "what actually got blocked" vs "what was merely noted".
    ("idx_agent_findings_decision_severity",
     "CREATE INDEX IF NOT EXISTS idx_agent_findings_decision_severity "
     "ON agent_findings (decision, severity)"),
)


def _is_pg(conn) -> bool:
    return "sqlite" not in type(conn).__module__.lower()


def up(conn=None) -> None:
    own = conn is None
    conn = conn or get_connection()
    try:
        if table_exists(conn, _TABLE):
            print(f"{_TAG} {_TABLE} already present")
        else:
            conn.execute(_DDL_PG if _is_pg(conn) else _DDL)
            print(f"{_TAG} created {_TABLE}")

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
