# CUI // SP-CTI
"""Migration 20260803002224 — who approved the irreversible thing, and why.

ars-appr-01 added a reversibility gate to the agent loop. A gate whose verdicts
are not recorded is unfalsifiable: nobody can answer "who let the agent force-push
that branch" after the fact, which is exactly the question an irreversible action
raises.

## Why a dedicated table rather than hook_events

``hook_events`` is generic session telemetry keyed on ``hook_type`` with a JSON
payload. Two problems for this use:

  1. Querying "every denial for tool X by actor Y last month" means parsing JSON
     on every row, and CLAUDE.md forbids SQLite-dialect JSON SQL in runtime call
     sites.
  2. ``actor`` and ``reason`` are the point. Burying the only record of a human
     judgement inside an untyped blob makes it evidence nobody can index.

So the columns that matter are columns. ``hook_events`` remains the fallback
sink when this table is absent (see ``approval_gate.record_decision``).

## No argument VALUES, by construction

``arg_keys`` stores KEY NAMES ONLY and ``input_sha256`` a digest of the flattened
input. Tool arguments can carry CUI and the gate sees every tool in the platform,
so trusting a redactor across all of them is a fail-open bet. The digest still
proves two rows refer to the same call. Same reasoning as ``runtime_invocations``
(migration 341) and ``tool_input_keys`` in ``.claude/hooks/post_tool_use.py``.

## Append-only

This table IS evidence, unlike ``runtime_invocations``. A decision has no
lifecycle: it was made once, by someone, for a stated reason. Correcting one
means appending the correction. Registered in ``APPEND_ONLY_TABLES`` in
``.claude/hooks/pre_tool_use.py`` (NIST 800-53 AU).
"""
from __future__ import annotations

from tools.db.storage import get_connection, table_exists

_TABLE = "agent_approval_log"
_TAG = "[20260803002224_agent_approval_log]"

# tier/decision vocabularies mirror TIERS and the decision literals in
# tools/agent_runtime/approval_gate.py. CLAUDE.md: no second hardcoded copy that
# can drift, so the CHECK stays deliberately loose (NOT NULL) and the Python
# constant remains the single source of the vocabulary.
_DDL = """
CREATE TABLE IF NOT EXISTS agent_approval_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    decided_at      TEXT NOT NULL,
    session_id      TEXT,
    actor           TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    tier            TEXT NOT NULL,
    rule            TEXT,
    decision        TEXT NOT NULL,
    reason          TEXT,
    mode            TEXT,
    arg_keys        TEXT,
    input_sha256    TEXT,
    detail          TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
)
"""

# PostgreSQL is the primary backend; SERIAL is its spelling of AUTOINCREMENT.
_DDL_PG = """
CREATE TABLE IF NOT EXISTS agent_approval_log (
    id              SERIAL PRIMARY KEY,
    decided_at      TEXT NOT NULL,
    session_id      TEXT,
    actor           TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    tier            TEXT NOT NULL,
    rule            TEXT,
    decision        TEXT NOT NULL,
    reason          TEXT,
    mode            TEXT,
    arg_keys        TEXT,
    input_sha256    TEXT,
    detail          TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
)
"""

_INDEXES = (
    ("idx_agent_approval_decided",
     "CREATE INDEX IF NOT EXISTS idx_agent_approval_decided "
     "ON agent_approval_log (decided_at)"),
    ("idx_agent_approval_actor",
     "CREATE INDEX IF NOT EXISTS idx_agent_approval_actor "
     "ON agent_approval_log (actor)"),
    # The dominant read is "what was denied, and what got through" — index it.
    ("idx_agent_approval_decision_tier",
     "CREATE INDEX IF NOT EXISTS idx_agent_approval_decision_tier "
     "ON agent_approval_log (decision, tier)"),
    ("idx_agent_approval_tool",
     "CREATE INDEX IF NOT EXISTS idx_agent_approval_tool "
     "ON agent_approval_log (tool_name)"),
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
