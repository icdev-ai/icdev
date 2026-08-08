#!/usr/bin/env python3
# CUI // SP-CTI
"""sbx-prc-02 — admit the two SBOM revision event types into audit_trail.

`sbom_revised` and `sbom_corrected` were added to
`tools/audit/audit_logger.py::VALID_EVENT_TYPES`, and `audit_trail.event_type`
carries a CHECK generated from that constant. Adding the constant without
rebuilding the deployed constraint is exactly the 221-vs-189 drift
`tests/test_audit_event_type_parity.py` exists to catch: the write is rejected,
every caller's best-effort `except` swallows the rejection, and the correction
looks recorded while nothing was written. That is the worst failure mode an
append-only NIST AU table has.

A Python migration rather than SQL because the constraint body must be
*generated* from the constant. Respelling 278 event types in a .sql file would
create the next stale copy — the thing the parity test refuses.

The list is not enumerated here. `rebuild_event_type_constraint` drops and
re-adds `audit_trail_event_type_check` from `event_type_check_sql()`, so this
migration stays correct however the vocabulary grows.

SQLite is a no-op by design: SQLite cannot ALTER a CHECK, and rebuilding
audit_trail there would mean copying an append-only, hash-chained table. Fresh
SQLite databases get the generated constraint from `init_icdev_db.py`, which
reads the same constant; existing ones are dev-local. `rebuild_event_type_constraint`
returns False there and says so.
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
