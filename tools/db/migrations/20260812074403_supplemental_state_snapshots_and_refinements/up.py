#!/usr/bin/env python3
# CUI // SP-CTI
"""exa-refine-05 — the two append-only tables a refinement cycle writes.

``tools/agent_runtime/refinement_cycle.py`` snapshots the supplemental harness
state (prompt versions, auto-skills, learned goals) so a refinement cycle can be
rolled back as a unit. The *file* half of a snapshot is delegated to
``tools/agent_runtime/checkpoints.py``, which stores under ``.tmp/checkpoints``;
these tables are the *row* half — the serialised provider state, the link to the
file checkpoint, and the applied refinements.

Both are append-only (NIST AU). A cycle being rolled back is recorded as a new
``('cycle', 'rolled_back')`` row in ``supplemental_refinements``, never as a
status flip on the snapshot — ``refinement_cycle.cycle_status`` derives the
verdict at read time. They are registered in ``APPEND_ONLY_TABLES`` in
``.claude/hooks/pre_tool_use.py``.

``audit_entry_id`` points at the chained ``audit_trail`` row (exa-audit-03) that
records the same event; ``refinement_cycle.verify_cycle`` feeds those ids back
through ``provenance_verifier.verify_audit_integrity``. It is nullable on
purpose — an audit write that fails must not lose the snapshot, and a NULL here
reports as ``unaudited``, which is louder than a snapshot that never happened.

A Python migration rather than SQL because it also has to rebuild
``audit_trail``'s ``event_type`` CHECK from ``VALID_EVENT_TYPES``, which now
admits ``supplemental_state``. Adding the constant without rebuilding the
deployed constraint is the drift ``tests/test_audit_event_type_parity.py``
exists to catch: the INSERT is rejected, the caller's best-effort ``except``
swallows it, and every self-modification looks audited while nothing was
written.
"""

_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS supplemental_state_snapshots (
    id              TEXT PRIMARY KEY,
    cycle_id        TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'open',
    label           TEXT DEFAULT '',
    actor           TEXT DEFAULT 'system',
    checkpoint_id   TEXT,
    state_json      TEXT NOT NULL,
    state_hash      TEXT NOT NULL,
    audit_entry_id  INTEGER,
    classification  TEXT DEFAULT 'CUI',
    tenant_id       TEXT,
    created_at      TEXT NOT NULL
)
"""

_REFINEMENTS_DDL = """
CREATE TABLE IF NOT EXISTS supplemental_refinements (
    id              TEXT PRIMARY KEY,
    cycle_id        TEXT NOT NULL,
    provider        TEXT NOT NULL,
    action          TEXT NOT NULL,
    target          TEXT DEFAULT '',
    actor           TEXT DEFAULT 'system',
    details         TEXT DEFAULT '{}',
    audit_entry_id  INTEGER,
    classification  TEXT DEFAULT 'CUI',
    tenant_id       TEXT,
    created_at      TEXT NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_sss_cycle ON supplemental_state_snapshots(cycle_id)",
    "CREATE INDEX IF NOT EXISTS idx_sss_created ON supplemental_state_snapshots(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_sr_cycle ON supplemental_refinements(cycle_id)",
    "CREATE INDEX IF NOT EXISTS idx_sr_created ON supplemental_refinements(created_at)",
)


def up(conn):
    conn.execute(_SNAPSHOTS_DDL)
    conn.execute(_REFINEMENTS_DDL)
    for statement in _INDEXES:
        conn.execute(statement)

    from tools.audit.audit_logger import rebuild_event_type_constraint

    rebuilt = rebuild_event_type_constraint(conn)
    if not rebuilt:
        print(
            "  audit_trail.event_type CHECK left as-is (SQLite cannot ALTER a CHECK). "
            "Fresh databases generate it from VALID_EVENT_TYPES."
        )
    return {"tables_created": 2, "constraint_rebuilt": rebuilt}
