# CUI // SP-CTI
"""Migration 042 rollback — drop reflex_observations table."""
from tools.db.storage import get_connection


def down():
    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS reflex_observations")
        conn.commit()
        print("Migration 042 down: reflex_observations dropped.")
    finally:
        conn.close()


if __name__ == "__main__":
    down()
