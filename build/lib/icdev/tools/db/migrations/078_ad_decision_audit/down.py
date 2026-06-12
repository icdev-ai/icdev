#!/usr/bin/env python3
# CUI // SP-CTI
from tools.db.storage import get_connection


def down(conn=None):
    _conn = conn or get_connection()
    try:
        _conn.execute("DROP TABLE IF EXISTS ad_decision_audit")
        _conn.commit()
        print("[078_ad_decision_audit] migration down complete")
    finally:
        if conn is None:
            _conn.close()


if __name__ == "__main__":
    down()
