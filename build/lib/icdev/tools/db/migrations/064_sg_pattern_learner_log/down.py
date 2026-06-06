#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 064 rollback — drops sg_pattern_learner_log."""

from tools.db.storage import get_connection


def down() -> None:
    conn = get_connection()
    conn.execute("DROP TABLE IF EXISTS sg_pattern_learner_log")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    down()
    print("Migration 064 rolled back: sg_pattern_learner_log dropped.")
