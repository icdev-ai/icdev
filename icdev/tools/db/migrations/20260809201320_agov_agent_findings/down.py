# CUI // SP-CTI
"""Migration 20260809201320 rollback — drop agent_findings.

Dropping this destroys every detection observation ever recorded, including any
a reviewer has already cited. The table is append-only precisely because those
rows are evidence; rolling back is a schema operation, not a routine one. Take a
dump first if it has rows you may be asked about later.
"""
from __future__ import annotations

from tools.db.storage import get_connection


def down(conn=None) -> dict:
    own = conn is None
    conn = conn or get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS agent_findings")
        conn.commit()
    finally:
        if own:
            conn.close()
    return {"status": "rolled_back", "dropped": ["agent_findings"]}


if __name__ == "__main__":
    print(down())
