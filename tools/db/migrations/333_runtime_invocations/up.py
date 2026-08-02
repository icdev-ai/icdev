# CUI // SP-CTI
"""Migration 333 — one place that records what the runtime actually ran.

MEASURED 2026-08-02 against the live PostgreSQL board:

    surface    audit events emitted   declared event types   runtime table
    agents     56 (3 of 16 types)     16                     agent_executions: 5 rows
    personas   0                      0                      ace_sessions: 0 rows
    roles      0                      0                      (no table)
    MCP        0                      0                      (no table)

MCP is the starkest: 512 tools registered, used by every session that talks to
ICDEV, and not one recorded invocation anywhere. Personas and roles have no
event types even DECLARED, so there was nothing to emit. Agents fare best and
still emit 3 of their 16 declared types — the other 13 are dead declarations.

## Why a new table rather than audit_trail

``audit_trail`` is append-only NIST AU evidence with a hash chain and a CHECK
constraint enumerating every valid event_type. Two problems with putting
invocations there:

  1. Volume. A single agent session makes hundreds of MCP tool calls. That is
     telemetry, not evidence, and it would bury 15k rows of genuine audit
     history under machine chatter.
  2. Every new surface would need the CHECK constraint altered.

So this is a purpose-built telemetry table with a ``surface`` discriminator
rather than four tables — the four surfaces answer the same question ("what
ran, how long did it take, did it work") and a shared shape means one query
serves all of them.

## No argument VALUES, by construction

``arg_keys`` stores KEY NAMES ONLY. MCP tool arguments can carry CUI, and 512
tools have 512 different argument shapes — trusting a redactor to be correct on
all of them is a fail-open bet. Storing only the keys cannot leak a value. This
mirrors what ``.claude/hooks/post_tool_use.py`` already does with
``tool_input_keys``.

This is telemetry, NOT audit evidence, and it is deliberately absent from
``APPEND_ONLY_TABLES``: a row is opened ``running`` and UPDATEd closed with its
duration and status, so it has a real lifecycle. ``audit_trail`` remains the
immutable NIST AU record; this table is operational data about it.
"""
from __future__ import annotations

from tools.db.storage import get_connection, table_exists

_TABLE = "runtime_invocations"
_TAG = "[333_runtime_invocations]"

# Kept in sync with SURFACES in tools/observability/invocation_recorder.py.
# CLAUDE.md: derive CHECK constraints from the Python constant, never hardcode a
# second copy that can drift.
_DDL = """
CREATE TABLE IF NOT EXISTS runtime_invocations (
    id              TEXT PRIMARY KEY,
    surface         TEXT NOT NULL,
    name            TEXT NOT NULL,
    session_id      TEXT,
    project_id      TEXT,
    parent_id       TEXT,
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    duration_ms     INTEGER,
    status          TEXT NOT NULL DEFAULT 'running',
    error_class     TEXT,
    error_message   TEXT,
    arg_keys        TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
)
"""

_INDEXES = (
    ("idx_runtime_inv_surface_started",
     "CREATE INDEX IF NOT EXISTS idx_runtime_inv_surface_started "
     "ON runtime_invocations (surface, started_at)"),
    ("idx_runtime_inv_name",
     "CREATE INDEX IF NOT EXISTS idx_runtime_inv_name "
     "ON runtime_invocations (name)"),
    ("idx_runtime_inv_session",
     "CREATE INDEX IF NOT EXISTS idx_runtime_inv_session "
     "ON runtime_invocations (session_id)"),
    # The dominant read is "what failed recently", so index it.
    ("idx_runtime_inv_status_started",
     "CREATE INDEX IF NOT EXISTS idx_runtime_inv_status_started "
     "ON runtime_invocations (status, started_at)"),
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
