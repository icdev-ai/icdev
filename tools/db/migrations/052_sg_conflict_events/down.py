#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 052 rollback — drops sg_conflict_events.

WARNING: cascades will also invalidate data added by migrations 053 and 055
(GDELT and STIX columns).  Only roll back when all dependent migrations have
already been reverted.
"""
from tools.db.storage import get_connection


def down() -> None:
    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS sg_conflict_events")
        conn.commit()
        print("Migration 052 rolled back: sg_conflict_events dropped.")
    finally:
        conn.close()


if __name__ == "__main__":
    down()
