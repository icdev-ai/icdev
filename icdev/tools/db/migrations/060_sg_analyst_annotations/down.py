#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 060 rollback — drops sg_analyst_annotations."""
from tools.db.storage import get_connection


def down() -> None:
    conn = get_connection()
    conn.execute("DROP TABLE IF EXISTS sg_analyst_annotations")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    down()
    print("Migration 060 rolled back: sg_analyst_annotations dropped.")
