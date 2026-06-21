# CUI // SP-CTI
"""Migration 158 — Rollback: drop conflict_predictions table."""
from tools.db.storage import get_connection


def down(conn=None) -> None:
    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS conflict_predictions")
        conn.commit()
        print("Migration 158 rolled back.")
    finally:
        conn.close()


if __name__ == "__main__":
    down()
