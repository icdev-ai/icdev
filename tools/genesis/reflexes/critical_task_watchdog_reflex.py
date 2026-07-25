#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Critical Task Watchdog Reflex — polls for newly-filed critical kanban
tasks and emits dashboard alerts via watchdog_alerts table and sidecar JSON files.

GREEN tier — reads kanban_tasks, writes watchdog_state + watchdog_alerts, writes
file-based sidecar notifications to data/alerts/.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("critical_task_watchdog")

IMPLEMENTATION_STATUS = "full"

_STATE_KEY = "last_checked_at"
_ALERTS_DIR = BASE_DIR / "data" / "alerts"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _ensure_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watchdog_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watchdog_alerts (
            id SERIAL PRIMARY KEY,
            task_id TEXT NOT NULL,
            task_title TEXT NOT NULL,
            priority TEXT NOT NULL,
            dispatch_source TEXT,
            alerted_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _get_last_checked_at(conn) -> str:
    row = conn.execute(
        "SELECT value FROM watchdog_state WHERE key = %s",
        (_STATE_KEY,),
    ).fetchone()
    if row:
        return dict(row)["value"]
    # Default: 24h ago so we catch anything filed recently on first run
    default = (_utcnow() - timedelta(hours=24)).isoformat()
    return default


def _set_last_checked_at(conn, ts: str) -> None:
    now = _utcnow_iso()
    conn.execute(
        """
        INSERT INTO watchdog_state (key, value, updated_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value,
                updated_at = EXCLUDED.updated_at
        """,
        (_STATE_KEY, ts, now),
    )


def _fetch_new_critical_tasks(conn, since: str) -> List[dict]:
    rows = conn.execute(
        """
        SELECT id, title, priority, dispatch_source, created_at
        FROM kanban_tasks
        WHERE priority = 'critical'
          AND status = 'backlog'
          AND created_at > %s
        ORDER BY created_at ASC
        """,
        (since,),
    ).fetchall()
    return [dict(r) for r in rows]


def _write_sidecar_alert(task: dict, alerted_at: str) -> None:
    _ALERTS_DIR.mkdir(parents=True, exist_ok=True)
    task_id = task.get("id", uuid.uuid4().hex[:8])
    alert_file = _ALERTS_DIR / f"critical_{task_id}.json"
    payload = {
        "task_id": task_id,
        "title": task.get("title", ""),
        "priority": task.get("priority", "critical"),
        "dispatch_source": task.get("dispatch_source", ""),
        "alerted_at": alerted_at,
    }
    try:
        alert_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("watchdog: could not write sidecar alert %s: %s", alert_file, exc)


def run(config: dict, state: object) -> dict:
    """Main reflex entry point called by the Genesis daemon."""
    result: dict = {
        "reflex": "critical_task_watchdog_reflex",
        "timestamp": _utcnow_iso(),
        "new_critical_tasks": 0,
        "alerted_task_ids": [],
        "last_checked_at": "",
    }

    conn = None
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        _ensure_tables(conn)

        last_checked = _get_last_checked_at(conn)
        result["last_checked_at"] = last_checked

        new_tasks = _fetch_new_critical_tasks(conn, last_checked)
        alerted: List[str] = []
        now_iso = _utcnow_iso()

        for task in new_tasks:
            task_id = task.get("id", "")
            title = task.get("title", "")
            priority = task.get("priority", "critical")
            source = task.get("dispatch_source") or ""

            # Insert alert row
            conn.execute(
                """
                INSERT INTO watchdog_alerts
                    (task_id, task_title, priority, dispatch_source, alerted_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (task_id, title, priority, source, now_iso),
            )

            # Write file-based sidecar for dashboard polling
            _write_sidecar_alert(task, now_iso)

            alerted.append(task_id)
            logger.warning(
                "watchdog: CRITICAL task filed — id=%s title=%r source=%s",
                task_id, title, source,
            )

        # Advance the checkpoint to now
        _set_last_checked_at(conn, now_iso)
        conn.commit()

        result["new_critical_tasks"] = len(alerted)
        result["alerted_task_ids"] = alerted
        result["last_checked_at"] = now_iso

    except Exception as exc:
        result["error"] = str(exc)
        logger.exception("critical_task_watchdog_reflex failed: %s", exc)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    return result


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board/PG config as the
    # GenesisDaemon. override=True: a pip-installed ICDEV in site-packages may have
    # already loaded a different checkout's .env at import. Repo root via __file__, not cwd.
    try:
        from pathlib import Path as _EnvPath
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_EnvPath(__file__).resolve().parents[3] / ".env", override=True)
    except ImportError:
        pass
    print(json.dumps(run({}, None), indent=2))
