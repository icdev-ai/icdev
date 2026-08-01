# CUI // SP-CTI
"""Canonical task seeder — never use raw INSERT directly.

Usage:
    from tools.kanban.task_factory import create_tasks

    created = create_tasks([{
        "id": "my-task-01",
        "title": "Do the thing",
        "description": "...",
        "task_type": "build",          # optional, default 'build'
        "priority": "medium",          # optional, default 'high'
        "status": "backlog",           # optional, default 'backlog'
        "depends_on_task_id": None,    # optional
        "source_doc_id": "abc123",     # optional — DIC document source
        "source_collection_id": "c1", # optional — DIC collection source
    }])
    # returns list of IDs that were actually inserted (skips duplicates)
"""
from __future__ import annotations

from tools.logging.icdev_logger import get_logger
from datetime import datetime, timezone

logger = get_logger(__name__)


def create_tasks(task_specs: list[dict]) -> list[str]:
    """Insert tasks that don't already exist. Returns list of inserted IDs."""
    if not task_specs:
        return []

    from tools.db.storage import get_connection
    from tools.kanban.init_db import init_kanban_tables

    init_kanban_tables()
    now = datetime.now(timezone.utc).isoformat()

    created: list[str] = []
    conn = get_connection()
    try:
        for t in task_specs:
            task_id = str(t.get("id") or "").strip()
            if not task_id:
                logger.warning("task_factory: skipping task with no id: %s", t.get("title"))
                continue

            existing = conn.execute(
                "SELECT id FROM kanban_tasks WHERE id = %s", (task_id,)
            ).fetchone()
            if existing:
                logger.debug("task_factory: skip existing task %s", task_id)
                continue

            # Idempotency key check — deduplicates webhook/automation retries
            idem_key = t.get("idempotency_key") or None
            if idem_key:
                idem_exists = conn.execute(
                    "SELECT id FROM kanban_tasks WHERE idempotency_key = %s", (idem_key,)
                ).fetchone()
                if idem_exists:
                    logger.debug(
                        "task_factory: skip duplicate idempotency_key=%s (task %s)",
                        idem_key, task_id,
                    )
                    continue

            max_retries = int(t.get("max_retries") or 5)
            max_runtime_seconds = t.get("max_runtime_seconds")
            if max_runtime_seconds is not None:
                max_runtime_seconds = int(max_runtime_seconds)

            conn.execute(
                """INSERT INTO kanban_tasks
                   (id, title, description, task_type, priority, status,
                    depends_on_task_id, source_prediction_id,
                    source_doc_id, source_collection_id,
                    dispatch_source, idempotency_key, max_retries,
                    max_runtime_seconds, loop_type, adversarial_enabled,
                    acceptance_criteria,
                    created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    task_id,
                    str(t.get("title", "Untitled task"))[:255],
                    t.get("description") or "",
                    t.get("task_type", "build"),
                    t.get("priority", "high"),
                    t.get("status", "backlog"),
                    t.get("depends_on_task_id"),
                    t.get("source_prediction_id"),
                    t.get("source_doc_id"),
                    t.get("source_collection_id"),
                    t.get("dispatch_source", "dic_notebook"),
                    idem_key,
                    max_retries,
                    max_runtime_seconds,
                    t.get("loop_type", "deterministic"),
                    1 if t.get("adversarial_enabled") else 0,
                    # Persisted so the dispatcher can put it in the prompt.
                    # Without this the column stayed empty on every seeded task
                    # (0 of 2427 populated), which left review_conformance
                    # unable to judge and the agent with no machine-checkable
                    # definition of done.
                    t.get("acceptance_criteria"),
                    now,
                    now,
                ),
            )
            created.append(task_id)

        conn.commit()
    except Exception as exc:
        logger.error("task_factory: commit failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    logger.info("task_factory: inserted %d / %d tasks", len(created), len(task_specs))
    return created
