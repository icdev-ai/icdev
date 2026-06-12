#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 036 rollback: drop topology_nodes."""
from __future__ import annotations

from tools.db.storage import get_connection


def down() -> None:
    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS topology_nodes")
        conn.commit()
        print("[036_topology_nodes] down: topology_nodes dropped")
    finally:
        conn.close()


if __name__ == "__main__":
    down()
