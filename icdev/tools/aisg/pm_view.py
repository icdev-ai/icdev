# CUI // SP-CTI
"""PM view data — sprint tasks, velocity, and learning milestones from live tables.

Sources ONLY real platform tables (no canned/demo data):
  * sprint tasks + velocity  -> ``kanban_tasks`` (shared icdev db)
  * build pass rate          -> ``kanban_executions``
  * learning milestones      -> ``aisg_track_progress`` x ``aisg_learning_tracks``

Every widget degrades to an honest empty state (and records the failure in
``data_source_error``) when its table is missing or a query fails — it NEVER
substitutes fabricated values presented as real project status.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from tools.db.storage import get_connection

from tools.logging.icdev_logger import get_logger
logger = get_logger("icdev.aisg.pm_view")

_DONE = ("done", "completed")
_TODO = ("todo", "pending", "scheduled", "backlog")
_PASS = ("success", "completed", "passed", "pass", "ok")
_FAIL = ("failed", "failure", "error", "errored")

_TASK_COLS = "id, title, status, priority, task_type, created_at, completed_at"


def _parse_dt(value):
    """Best-effort parse of a DB timestamp into an aware UTC datetime, or None."""
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    for candidate in (s, s.split(".")[0], s[:10]):
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return None


def _load_sprint_tasks(conn) -> list[dict]:
    """Recent tasks — AISG-generated first, else the most recent board tasks."""
    try:
        rows = conn.execute(
            f"SELECT {_TASK_COLS} FROM kanban_tasks "
            "WHERE dispatch_source = 'aisg_wizard' "
            "ORDER BY created_at DESC LIMIT 25"
        ).fetchall()
        tasks = [dict(r) for r in rows]
        if not tasks:
            rows = conn.execute(
                f"SELECT {_TASK_COLS} FROM kanban_tasks "
                "ORDER BY created_at DESC LIMIT 25"
            ).fetchall()
            tasks = [dict(r) for r in rows]
        return tasks
    except Exception as exc:
        logger.warning("pm_view: sprint tasks query failed: %s", exc)
        return []


def _load_velocity(conn, weeks: int = 6) -> list[dict]:
    """Weekly planned (created) vs completed counts derived from kanban_tasks."""
    try:
        rows = conn.execute(
            "SELECT created_at, completed_at, status FROM kanban_tasks "
            "ORDER BY created_at DESC LIMIT 2000"
        ).fetchall()
    except Exception as exc:
        logger.warning("pm_view: velocity query failed: %s", exc)
        return []
    if not rows:
        return []

    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday())
    buckets: "OrderedDict[datetime.date, dict]" = OrderedDict()
    for i in range(weeks):
        start = monday - timedelta(weeks=(weeks - 1 - i))
        buckets[start] = {"sprint": start.strftime("%b %d"), "planned": 0, "completed": 0}

    def _bucket_for(d):
        return buckets.get(d - timedelta(days=d.weekday()))

    for r in rows:
        created = _parse_dt(r["created_at"])
        if created:
            b = _bucket_for(created.date())
            if b is not None:
                b["planned"] += 1
        completed = _parse_dt(r["completed_at"])
        status = (r["status"] or "").lower()
        if completed and status in _DONE:
            b = _bucket_for(completed.date())
            if b is not None:
                b["completed"] += 1

    values = list(buckets.values())
    if not any(v["planned"] or v["completed"] for v in values):
        return []
    return values


def _load_build_pass_rate(conn):
    """Percent of finished kanban executions that succeeded, or None if none."""
    try:
        rows = conn.execute(
            "SELECT status FROM kanban_executions "
            "ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
    except Exception as exc:
        logger.warning("pm_view: build pass rate query failed: %s", exc)
        return None
    finished = [
        (r["status"] or "").lower()
        for r in rows
        if (r["status"] or "").lower() in _PASS + _FAIL
    ]
    if not finished:
        return None
    passed = sum(1 for s in finished if s in _PASS)
    return round(passed / len(finished) * 100)


def _load_learning_milestones(conn, limit: int = 8) -> list[dict]:
    """Real learner progress from aisg_track_progress joined to learning tracks."""
    try:
        rows = conn.execute(
            "SELECT tp.user_email, tp.tasks_completed, tp.completed_at, "
            "tp.activated_at, tp.created_at, lt.name AS track_name, "
            "lt.level AS track_level "
            "FROM aisg_track_progress tp "
            "LEFT JOIN aisg_learning_tracks lt ON tp.track_id = lt.id "
            "ORDER BY COALESCE(tp.completed_at, tp.activated_at, tp.created_at) DESC "
            "LIMIT %s",
            (limit,),
        ).fetchall()
    except Exception as exc:
        logger.warning("pm_view: learning milestones query failed: %s", exc)
        return []

    out: list[dict] = []
    for r in rows:
        d = dict(r)
        track = d.get("track_name") or d.get("track_level") or "Learning Track"
        completed = d.get("completed_at")
        if completed:
            milestone = f"Completed {track}"
            date = completed
        else:
            n = d.get("tasks_completed") or 0
            milestone = f"{n} task(s) completed in {track}"
            date = d.get("activated_at") or d.get("created_at")
        out.append({
            "user": d.get("user_email") or "—",
            "track": track,
            "milestone": milestone,
            "date": str(date)[:10] if date else "",
        })
    return out


def get_pm_data() -> dict:
    """Return sprint tasks, velocity metrics, and learning milestones (live data only)."""
    result = {
        "sprint_tasks": [],
        "sprint_name": "Live Board",
        "sprint_start": "",
        "sprint_end": "",
        "done": 0,
        "in_progress": 0,
        "todo": 0,
        "total": 0,
        "velocity_data": [],
        "test_coverage": None,
        "build_pass_rate": None,
        "learning_milestones": [],
        "data_source_error": None,
    }

    try:
        conn = get_connection()
    except Exception as exc:
        logger.error("pm_view: database unavailable: %s", exc)
        result["data_source_error"] = str(exc)
        return result

    try:
        sprint_tasks = _load_sprint_tasks(conn)
        result["sprint_tasks"] = sprint_tasks
        result["done"] = sum(1 for t in sprint_tasks if (t.get("status") or "").lower() in _DONE)
        result["in_progress"] = sum(1 for t in sprint_tasks if (t.get("status") or "").lower() == "in_progress")
        result["todo"] = sum(1 for t in sprint_tasks if (t.get("status") or "").lower() in _TODO)
        result["total"] = len(sprint_tasks)
        result["velocity_data"] = _load_velocity(conn)
        result["build_pass_rate"] = _load_build_pass_rate(conn)
        result["learning_milestones"] = _load_learning_milestones(conn)
    except Exception as exc:
        logger.exception("pm_view: unexpected failure building PM data")
        result["data_source_error"] = str(exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return result
