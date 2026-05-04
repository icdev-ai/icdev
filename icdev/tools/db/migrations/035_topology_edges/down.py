#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 035 rollback: drop topology_edges."""
from __future__ import annotations

from tools.db.storage import get_connection


def down() -> None:
    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS topology_edges")
        conn.commit()
        print("[035_topology_edges] down: topology_edges dropped")
    finally:
        conn.close()


if __name__ == "__main__":
    down()
