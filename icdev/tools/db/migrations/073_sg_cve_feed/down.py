#!/usr/bin/env python3
# CUI // SP-CTI
from tools.db.storage import get_connection


def down(conn=None):
    _conn = conn or get_connection()
    try:
        _conn.execute("DROP TABLE IF EXISTS sg_cve_feed")
        _conn.commit()
        print("[073_sg_cve_feed] migration down complete")
    finally:
        if conn is None:
            _conn.close()


if __name__ == "__main__":
    down()
