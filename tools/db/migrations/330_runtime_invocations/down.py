# CUI // SP-CTI
"""Migration 330 rollback — drop runtime_invocations.

Safe to drop: the table is telemetry, not evidence. Nothing reads it except the
observability surfaces added alongside it, and no other table references it.
"""
from __future__ import annotations

from tools.db.storage import get_connection


def down(conn=None) -> dict:
    own = conn is None
    conn = conn or get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS runtime_invocations")
        conn.commit()
    finally:
        if own:
            conn.close()
    return {"status": "rolled_back", "dropped": ["runtime_invocations"]}


if __name__ == "__main__":
    print(down())
