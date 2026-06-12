# CUI // SP-CTI
from tools.db.storage import get_connection


def down(conn=None):
    _conn = conn or get_connection()
    try:
        _conn.execute("DROP TABLE IF EXISTS osint_privacy_audit")
        _conn.commit()
        print("[159_osint_privacy_audit] migration down complete")
    finally:
        if conn is None:
            _conn.close()


if __name__ == "__main__":
    down()
