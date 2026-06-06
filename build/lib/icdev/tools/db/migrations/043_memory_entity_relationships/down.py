# CUI // SP-CTI
"""Migration 043 rollback — drop memory_entity_relationships table."""
from tools.db.storage import get_connection


def down():
    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS memory_entity_relationships")
        conn.commit()
        print("Migration 043 down: memory_entity_relationships dropped.")
    finally:
        conn.close()


if __name__ == "__main__":
    down()
