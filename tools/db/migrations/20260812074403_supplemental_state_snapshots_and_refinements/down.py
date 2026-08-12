#!/usr/bin/env python3
# CUI // SP-CTI
"""Rollback for 20260812074403_supplemental_state_snapshots_and_refinements.

The two tables are **not** dropped. They are append-only records of
self-modification, linked by ``audit_entry_id`` to chained ``audit_trail`` rows
that survive this rollback; dropping them would leave those audit rows pointing
at nothing and destroy the only description of what a refinement cycle changed.
Rolling back the code does not un-happen the cycles it ran.

The CHECK is rebuilt from whatever ``VALID_EVENT_TYPES`` currently holds, for
the same reason the sbom_revision rollback does: narrowing it to a hardcoded
"before" list would become a stale copy of the vocabulary and would start
rejecting rows the code still writes. To genuinely un-admit
``supplemental_state``, remove it from the constant and run this — the
constraint follows the constant, in both directions.
"""


def down(conn):
    from tools.audit.audit_logger import rebuild_event_type_constraint

    return {
        "tables_dropped": 0,
        "note": "supplemental_* tables retained: append-only self-modification evidence",
        "constraint_rebuilt": rebuild_event_type_constraint(conn),
    }
