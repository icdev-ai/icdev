# CUI // SP-CTI
"""Migration 041 — kanban_task_deps junction table for multi-parent task dependencies.

Adds:
  kanban_task_deps(task_id, depends_on_id, created_at)

The existing depends_on_task_id scalar column on kanban_tasks is kept for
backwards compatibility during the transition period. New code writes to both;
the junction table is authoritative for blocking logic.
"""
from tools.db.storage import get_connection


def up(conn=None):
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kanban_task_deps (
                task_id         TEXT NOT NULL,
                depends_on_id   TEXT NOT NULL,
                created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                PRIMARY KEY (task_id, depends_on_id),
                FOREIGN KEY (task_id)       REFERENCES kanban_tasks(id) ON DELETE CASCADE,
                FOREIGN KEY (depends_on_id) REFERENCES kanban_tasks(id) ON DELETE CASCADE
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ktd_depends_on ON kanban_task_deps(depends_on_id)"
        )

        # Backfill existing scalar depends_on_task_id rows into the junction table.
        conn.execute("""
            INSERT INTO kanban_task_deps (task_id, depends_on_id, created_at)
            SELECT id, depends_on_task_id, NOW()
            FROM kanban_tasks
            WHERE depends_on_task_id IS NOT NULL
              AND id != depends_on_task_id
            ON CONFLICT (task_id, depends_on_id) DO NOTHING
        """)
        conn.commit()
        print("Migration 041 up: kanban_task_deps created and backfilled.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
