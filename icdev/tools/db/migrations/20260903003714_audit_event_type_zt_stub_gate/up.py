#!/usr/bin/env python3
# CUI // SP-CTI
"""rmf-zt-01 — admit ``zt.stub_gate`` into audit_trail.

``ICDEV_ZT_ALLOW_STUB`` decides whether an unverifiable device posture may be
honored, and it decided that silently: nothing anywhere recorded that the gate
had been consulted, let alone which way it went.
``tools/security/stub_gate.py::record_stub_decision`` now writes one row per
decision under the ``zt.stub_gate`` event type.

``audit_trail.event_type`` carries a CHECK generated from
``VALID_EVENT_TYPES``. Adding the constant without rebuilding the deployed
constraint is the drift ``tests/test_audit_event_type_parity.py`` exists to
catch — the write is rejected on its first line, the caller's best-effort
``except`` swallows the rejection, and the control looks audited while nothing
was written. That is the worst failure mode an append-only NIST AU table has,
and it is exactly the shape of the defect this task is fixing one layer up.

A Python migration rather than SQL because the constraint body must be
GENERATED from the constant; respelling the vocabulary in a .sql file would
create the next stale copy.

THIS REBUILD IS A PURE WIDENING, and that was checked rather than assumed. The
deployed CHECK on the live board admitted two types the constant did not —
``cato_evidence_collected`` and ``oscal_generated`` — and, uniquely,
``tools/compliance/{cato_monitor,cato_scheduler,oscal_generator}.py`` write
them with a RAW ``INSERT INTO audit_trail`` rather than through ``log_event``.
The CHECK is therefore the only thing letting those three writers through, and
regenerating it from a constant that omitted them would have started REFUSING
three live compliance audit writers. Both names were added to
``VALID_EVENT_TYPES`` in the same change, so this migration only ever adds.

SQLite is a no-op by design: SQLite cannot ALTER a CHECK, and rebuilding
audit_trail there would mean copying an append-only, hash-chained table. Fresh
SQLite databases get the generated constraint from ``init_icdev_db.py``, which
reads the same constant; existing ones are dev-local.
"""


def up(conn):
    from tools.audit.audit_logger import rebuild_event_type_constraint

    rebuilt = rebuild_event_type_constraint(conn)
    if not rebuilt:
        print(
            "  audit_trail.event_type CHECK left as-is (SQLite cannot ALTER a CHECK). "
            "Fresh databases generate it from VALID_EVENT_TYPES."
        )
    return {"constraint_rebuilt": rebuilt}
