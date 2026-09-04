#!/usr/bin/env python3
# CUI // SP-CTI
"""nmce-purge-01 — make the Migration Design Canvas audit trail actually record.

`tools/migration_canvas/blueprint.py::_audit` writes to two places and BOTH have
been failing silently since they were written. The canvas has produced zero audit
rows in production — 0 in `audit_trail`, and `mc_audit` does not exist at all —
while every call site looked like it was auditing.

1. `mc_audit` was declared only in `tools/migration_canvas/db/init_db.py` SCHEMA,
   which has never been applied to PostgreSQL. Two things in that DDL are
   SQLite-only and had to change for it to be applicable at all: `user` is a
   reserved word (unquoted it is a syntax error, not a wrong-column error), and
   `INTEGER PRIMARY KEY AUTOINCREMENT` is not PG. Created here quoted, with a
   sequence-backed id.

2. `event_type = 'migration_canvas'` was never admitted to
   `audit_logger.VALID_EVENT_TYPES`, so the generated CHECK on
   `audit_trail.event_type` rejected the bridge INSERT. Adding the constant
   without rebuilding the deployed constraint is the same drift
   `tests/test_audit_event_type_parity.py` exists to catch, so the constraint is
   rebuilt here from the constant rather than respelled.

Append-only (NIST AU): `mc_audit` rows are inserted, never updated or deleted.

A Python migration because the constraint body must be *generated* from the
constant, and because the DDL differs by backend. SQLite is a no-op for both
halves: it keeps its own migration_canvas.db built from the same init_db SCHEMA,
and it cannot ALTER a CHECK — rebuilding audit_trail there would mean copying an
append-only hash-chained table.
"""

_PG_DDL = """
CREATE TABLE IF NOT EXISTS mc_audit (
    id              SERIAL PRIMARY KEY,
    design_id       TEXT,
    "user"          TEXT DEFAULT '',
    action          TEXT NOT NULL,
    detail          TEXT,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
)
"""


def up(conn):
    from tools.audit.audit_logger import rebuild_event_type_constraint
    from tools.db.storage import is_pg

    created = False
    if is_pg(conn):
        conn.execute(_PG_DDL)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mc_audit_design ON mc_audit(design_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mc_audit_created ON mc_audit(created_at)")
        conn.commit()
        created = True
    else:
        print(
            "  mc_audit left to tools/migration_canvas/db/init_db.py — on SQLite the "
            "canvas keeps its own migration_canvas.db, not this database."
        )

    rebuilt = rebuild_event_type_constraint(conn)
    if not rebuilt:
        print(
            "  audit_trail.event_type CHECK left as-is (SQLite cannot ALTER a CHECK). "
            "Fresh databases generate it from VALID_EVENT_TYPES."
        )

    return {"mc_audit_created": created, "constraint_rebuilt": rebuilt}
