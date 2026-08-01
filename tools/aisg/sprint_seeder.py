# CUI // SP-CTI
"""AISG Sprint Seeder — seeds a 30-day sprint Kanban from wizard maturity level.

Call seed_for_maturity() after wizard.process() to auto-populate the task board
with the generated sprint plan as ready-to-execute Kanban tasks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tools.aisg.wizard import _build_sprint_tasks


def seed_for_maturity(
    maturity: str,
    compliance_level: str = "IL2",
    use_case: str = "web_app",
    tech_stack: str = "python",
    cloud_provider: str = "local",
    db_conn: Any = None,
) -> list[str]:
    """Insert wizard-generated sprint tasks into kanban_tasks.

    Args:
        maturity: AI maturity level — "none", "pilot", or "scaling".
        compliance_level: Compliance level (IL2, IL4, IL5, FedRAMP, CMMC).
        use_case: Wizard use-case answer (default "web_app").
        tech_stack: Primary tech stack (default "python").
        cloud_provider: Cloud target (default "local").
        db_conn: Optional injected DB connection (sqlite3 for tests,
                 get_connection() for prod). If None, uses get_connection().

    Returns:
        List of task IDs inserted into kanban_tasks.
    """
    tasks = _build_sprint_tasks(
        use_case=use_case,
        compliance_level=compliance_level,
        tech_stack=tech_stack,
        ai_maturity=maturity,
        cloud_provider=cloud_provider,
    )

    owns_conn = db_conn is None
    if owns_conn:
        from tools.db.storage import get_connection
        conn = get_connection()
    else:
        conn = db_conn

    # Resolve the backend-correct placeholder for THIS connection. A raw sqlite3
    # connection (tests) has no ``_backend`` attribute, so sql_placeholder returns
    # ``?``; a StorageConnection returns ``%s`` on PG (and its execute() translate
    # layer rewrites ``INSERT OR IGNORE`` for PostgreSQL). Hard-coding ``%s`` here
    # broke injected raw-sqlite3 connections, which never see the translate layer.
    from tools.db.storage import sql_placeholder
    ph = sql_placeholder(conn)

    now = datetime.now(timezone.utc).isoformat()
    inserted: list[str] = []

    try:
        for task in tasks:
            task_id = task["id"]
            conn.execute(
                f"""INSERT OR IGNORE INTO kanban_tasks
                   (id, title, task_type, priority, status, created_at, updated_at,
                    scheduled_at, dispatch_source)
                   VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})""",
                (
                    task_id,
                    task["title"],
                    task.get("type", "chore"),
                    task.get("priority", "medium"),
                    "backlog",
                    now,
                    now,
                    now,
                    "aisg_wizard",
                ),
            )
            inserted.append(task_id)

        if hasattr(conn, "commit"):
            conn.commit()
    finally:
        if owns_conn and hasattr(conn, "close"):
            conn.close()

    return inserted
