#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 030 rollback: drop ad_trap_events."""
from __future__ import annotations

from tools.db.storage import get_connection


def down() -> None:
    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS ad_trap_events")
        conn.commit()
        print("[030_ad_trap_events] down: ad_trap_events dropped")
    finally:
        conn.close()


if __name__ == "__main__":
    down()
