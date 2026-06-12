# CUI // SP-CTI
"""Migration 055 rollback — Drop sg_information_signals and sg_information_scores."""
from tools.db.storage import get_connection


def down() -> None:
    conn = get_connection()
    conn.execute("DROP TABLE IF EXISTS sg_information_scores")
    conn.execute("DROP TABLE IF EXISTS sg_information_signals")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    down()
    print("Migration 055 rolled back.")
