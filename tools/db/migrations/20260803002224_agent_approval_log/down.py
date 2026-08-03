# CUI // SP-CTI
"""Migration 342 rollback — drop agent_approval_log.

Dropping this destroys the only record of who authorised which irreversible
action. Rolling back is a schema operation, not a routine one: take a dump first
if the table has rows you may be asked about later.
"""
from __future__ import annotations

from tools.db.storage import get_connection


def down(conn=None) -> dict:
    own = conn is None
    conn = conn or get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS agent_approval_log")
        conn.commit()
    finally:
        if own:
            conn.close()
    return {"status": "rolled_back", "dropped": ["agent_approval_log"]}


if __name__ == "__main__":
    print(down())
