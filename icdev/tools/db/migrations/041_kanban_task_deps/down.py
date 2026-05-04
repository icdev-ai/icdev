# CUI // SP-CTI
"""Migration 041 rollback — drop kanban_task_deps junction table."""
from tools.db.storage import get_connection


def down():
    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS kanban_task_deps")
        conn.commit()
        print("Migration 041 down: kanban_task_deps dropped.")
    finally:
        conn.close()


if __name__ == "__main__":
    down()
