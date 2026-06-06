#!/usr/bin/env python3
# CUI // SP-CTI
from tools.db.storage import get_connection


def down(conn=None):
    _conn = conn or get_connection()
    try:
        _conn.execute("DROP TABLE IF EXISTS sg_osint_results")
        _conn.commit()
        print("[077_sg_osint_results] migration down complete")
    finally:
        if conn is None:
            _conn.close()


if __name__ == "__main__":
    down()
