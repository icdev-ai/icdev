#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 031 rollback: drop network_twin_snapshots."""
from __future__ import annotations

from tools.db.storage import get_connection


def down() -> None:
    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS network_twin_snapshots")
        conn.commit()
        print("[031_network_twin_snapshots] down: network_twin_snapshots dropped")
    finally:
        conn.close()


if __name__ == "__main__":
    down()
