#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 318: rebuild audit_trail.event_type CHECK from VALID_EVENT_TYPES.

Two copies of the same list had drifted. VALID_EVENT_TYPES held 221 entries
while the deployed constraint admitted 189, and neither included the 25
``govcon.*`` types that tools/govcon writes — so every govcon audit INSERT was
rejected by the constraint and swallowed by the caller's bare except. Measured
on live PostgreSQL before this: audit_trail held 3033 rows and zero from govcon.

The constraint is generated from the constant rather than spelled out here, so
this migration cannot itself become a third stale copy.

PostgreSQL supports DROP/ADD CONSTRAINT directly. SQLite cannot alter a CHECK,
and rebuilding audit_trail there would mean copying an append-only,
hash-chained table — so on SQLite this is a no-op: fresh databases already get
the generated constraint from init_icdev_db.py, and existing SQLite databases
are dev-local.
"""
from __future__ import annotations

from tools.audit.audit_logger import VALID_EVENT_TYPES
from tools.db.storage import get_connection

CONSTRAINT = "audit_trail_event_type_check"


def up() -> None:
    conn = get_connection()
    try:
        is_pg = getattr(conn, "_backend", "") == "postgresql"
        if not is_pg:
            print(
                "[318_audit_event_type_parity] up: SQLite — no-op "
                "(CHECK is generated at schema-init time)"
            )
            return

        values = ", ".join(f"'{t}'" for t in VALID_EVENT_TYPES)
        conn.execute(f"ALTER TABLE audit_trail DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
        conn.execute(
            f"ALTER TABLE audit_trail ADD CONSTRAINT {CONSTRAINT} "
            f"CHECK (event_type IN ({values}))"
        )
        conn.commit()
        print(
            f"[318_audit_event_type_parity] up: {CONSTRAINT} rebuilt with "
            f"{len(VALID_EVENT_TYPES)} event types"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    up()
