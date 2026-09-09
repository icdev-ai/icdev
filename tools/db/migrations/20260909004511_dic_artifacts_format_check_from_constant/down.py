#!/usr/bin/env python3
# CUI // SP-CTI
"""Drop the named CHECK on PostgreSQL, leaving the column unconstrained.

Deliberately does NOT restore the four-name list. Rows written while the
migration was up may name ``docx_tracked``; re-adding a constraint that
refuses them would fail the ALTER on exactly the deployments that used the
feature, and dropping those rows to make the constraint fit would delete the
record of an artifact that exists on disk.

SQLite is a no-op: reversing the table rebuild would mean a second rebuild to
narrow a constraint, with the same row problem and no upside.
"""

CONSTRAINT = "dic_artifacts_format_check"


def down(conn):
    if getattr(conn, "_backend", "") != "postgresql":
        print("  dic_artifacts.format CHECK left as-is (SQLite; see module doc)")
        return {"dropped": False}
    conn.execute(
        f"ALTER TABLE dic_artifacts DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    conn.commit()
    return {"dropped": True}
