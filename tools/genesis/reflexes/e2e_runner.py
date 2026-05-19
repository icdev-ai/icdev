#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis E2E Runner Reflex — daily Playwright smoke suite via Kanban scheduler.

Creates a Kanban task (task_type=test, title contains 'playwright') once per
day.  The Kanban scheduler picks it up, dispatches to Claude CLI, which runs
`npx playwright test tests/e2e/ --project=chromium --reporter=line`.

GREEN tier — only creates a DB record, reads nothing sensitive.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

logger = logging.getLogger(__name__)

_TASK_TITLE = "[AUTO-RUN] Playwright E2E Suite — full smoke"
_TASK_DESC = (
    "Run the full Playwright E2E smoke suite targeting all menus, use cases, "
    "canvases, and chat right panels.\n\n"
    "Command:\n"
    "```\n"
    "npx playwright test tests/e2e/ --project=chromium --reporter=line\n"
    "```\n\n"
    "After the run:\n"
    "1. Report total pass / fail count as a brief assistant message.\n"
    "2. If any tests failed, list the failing test titles.\n"
    "3. Screenshots are in `.tmp/test_runs/screenshots/`.\n"
    "4. HTML report: `npx playwright show-report`\n"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _pending_run_exists(conn) -> bool:
    """Return True if a runner task is already backlog or in_progress."""
    row = conn.execute(
        """
        SELECT id FROM kanban_tasks
        WHERE title LIKE '%Playwright E2E Suite%'
          AND status IN ('backlog', 'in_progress')
        LIMIT 1
        """,
    ).fetchone()
    return row is not None


def _last_completed_at(conn) -> datetime | None:
    """Return when the last E2E runner task was completed, or None."""
    row = conn.execute(
        """
        SELECT completed_at FROM kanban_tasks
        WHERE title LIKE '%Playwright E2E Suite%'
          AND status = 'done'
        ORDER BY completed_at DESC
        LIMIT 1
        """,
    ).fetchone()
    if row:
        val = dict(row).get("completed_at")
        if val:
            if isinstance(val, datetime):
                return val
            try:
                return datetime.fromisoformat(str(val))
            except ValueError:
                pass
    return None


def _create_run_task(conn) -> str:
    """Insert the nightly runner task and return its ID."""
    import uuid
    task_id = f"task-e2e-{uuid.uuid4().hex[:8]}"
    now = _utcnow()
    conn.execute(
        """
        INSERT INTO kanban_tasks
            (id, title, description, task_type, priority, status,
             scheduled_at, created_at, updated_at, dispatch_source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            _TASK_TITLE,
            _TASK_DESC,
            "test",
            "high",
            "backlog",
            now.isoformat(),
            now.isoformat(),
            now.isoformat(),
            "e2e_runner_reflex",
        ),
    )
    return task_id


def run(config: dict, state: object) -> dict:
    """Main reflex entry point called by the Genesis daemon."""
    interval_hours = float(config.get("interval_hours", 24))
    results: dict = {
        "reflex": "e2e_runner",
        "timestamp": _utcnow().isoformat(),
        "action": "skip",
        "task_id": None,
    }

    conn = None
    try:
        conn = get_connection()

        # Skip if a run is already pending / in flight
        if _pending_run_exists(conn):
            results["reason"] = "run already pending or in_progress"
            return results

        # Skip if last run completed less than interval_hours ago
        last = _last_completed_at(conn)
        if last:
            elapsed_hours = (_utcnow() - last).total_seconds() / 3600
            if elapsed_hours < interval_hours:
                results["reason"] = f"last run {elapsed_hours:.1f}h ago (interval={interval_hours}h)"
                return results

        # Create the task
        task_id = _create_run_task(conn)
        conn.commit()
        results["action"] = "created"
        results["task_id"] = task_id
        logger.info("e2e_runner: created Kanban task %s", task_id)

    except Exception as exc:
        results["error"] = str(exc)
        logger.exception("e2e_runner reflex failed: %s", exc)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    return results


if __name__ == "__main__":
    import json
    result = run({"interval_hours": 24}, None)
    print(json.dumps(result, indent=2))
