#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 034 rollback: drop security_nodes."""
from __future__ import annotations

from tools.db.storage import get_connection


def down() -> None:
    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS security_nodes")
        conn.commit()
        print("[034_security_nodes] down: security_nodes dropped")
    finally:
        conn.close()


if __name__ == "__main__":
    down()
