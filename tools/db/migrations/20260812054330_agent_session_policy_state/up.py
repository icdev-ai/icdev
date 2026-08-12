# CUI // SP-CTI
"""Migration 20260812054330 — the session-scoped state a stateful policy needs.

exa-policy-02 composes policies at three levels and lets a policy carry
``state_updates`` — omnigent's mechanism, e.g. ``{key: call_count, action:
increment, value: 1}``. A counter is only a control if it survives; this table is
where it survives.

## Why it has to be persisted at all

The obvious implementation is a dict in the process. It satisfies "state
persists across tool calls" and nothing else. ICDEV resumes agent sessions
across process restarts on purpose — ``AgentLoopResult.session_id`` handed back
as ``resume_session_id`` restores the full tool-use history
(``tools/agent_runtime/sessions.py``). So an in-memory-only counter means
``max_tool_calls_per_session: 50`` is bypassed by restarting the runtime and
resuming, which is not a limit, it is a speed bump. The session id is the
identity that outlives the process, so the state keyed by it must too.

## Why MUTABLE and deliberately NOT append-only

A counter has a lifecycle: it is the current value, and the current value is the
only thing a policy can act on. Appending would make the read
``SELECT ... ORDER BY updated_at DESC LIMIT 1`` per key per call, and the "which
row is current" question has exactly one answer, so there is nothing for an
auditor to reconstruct. The evidence record is elsewhere and is append-only: the
DECISION each state value produced lands in ``agent_approval_log`` through
``approval_gate.record_decision`` (migration 20260803002224), with the composed
effect and the reason. That is the row that answers "why was this refused" after
the fact. Do NOT add this table to ``APPEND_ONLY_TABLES``.

## No argument values, same rule as the audit log

``state_value`` is JSON and holds only what a policy put there — counters,
totals, small flags. Policies are the only writer and the shipped ones write
numbers. It is not a place to stash tool arguments, which can carry CUI; the
composition layer never copies ``PolicyEvent.arguments`` into state.

Composite primary key ``(session_id, state_key)``: one row per key per session,
written with an UPSERT. Portable DDL, no SERIAL/AUTOINCREMENT split.
"""
from __future__ import annotations

from tools.db.storage import get_connection, table_exists

_TABLE = "agent_session_policy_state"
_TAG = "[20260812054330_agent_session_policy_state]"

# Portable across PG and SQLite: no autoincrement column, so one DDL serves both.
_DDL = """
CREATE TABLE IF NOT EXISTS agent_session_policy_state (
    session_id      TEXT NOT NULL,
    state_key       TEXT NOT NULL,
    state_value     TEXT,
    updated_at      TEXT,
    tenant_id       TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id, state_key)
)
"""

_INDEXES = (
    # The dominant read is "the whole state for this session", served by the PK
    # prefix. This one covers the sweep that expires abandoned session state.
    ("idx_agent_session_policy_state_updated",
     "CREATE INDEX IF NOT EXISTS idx_agent_session_policy_state_updated "
     "ON agent_session_policy_state (updated_at)"),
)


def up(conn=None) -> None:
    own = conn is None
    conn = conn or get_connection()
    try:
        if table_exists(conn, _TABLE):
            print(f"{_TAG} {_TABLE} already present")
        else:
            conn.execute(_DDL)
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
