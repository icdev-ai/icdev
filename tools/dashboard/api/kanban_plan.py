# CUI // SP-CTI
"""Kanban Plan API — task decomposition and scheduling endpoints."""

from flask import Blueprint, jsonify

kanban_plan_api = Blueprint("kanban_plan_api", __name__)


@kanban_plan_api.route("/api/kanban/plans", methods=["GET"])
def list_plans():
    """List kanban plans."""
    return jsonify({"plans": [], "total": 0})


@kanban_plan_api.route("/api/kanban/scheduler-status", methods=["GET"])
def scheduler_status():
    """Return kanban scheduler heartbeat from log mtime.

    Called by the Live Activity panel to determine if the scheduler is alive.
    scheduler_last_seen_secs > 600 → scheduler likely dead (zombie tasks possible).
    """
    import pathlib
    import time as _t

    log_path = pathlib.Path(".tmp/kanban_scheduler.log")
    scheduler_last_seen_secs = None
    if log_path.exists():
        scheduler_last_seen_secs = int(_t.time() - log_path.stat().st_mtime)

    if scheduler_last_seen_secs is None:
        staleness = "unknown"
    elif scheduler_last_seen_secs > 600:
        staleness = "stale"
    elif scheduler_last_seen_secs > 180:
        staleness = "warning"
    else:
        staleness = "active"

    return jsonify({
        "scheduler_last_seen_secs": scheduler_last_seen_secs,
        "staleness": staleness,
    })


@kanban_plan_api.route("/api/kanban/live-activity", methods=["GET"])
def live_activity():
    """In-progress tasks enriched with elapsed time and scheduler heartbeat.

    Staleness signals:
      - elapsed_dispatch_secs: seconds since the task moved to in_progress.
        The scheduler does NOT heartbeat updated_at during execution — it only
        sets it at dispatch — so this field reflects how long the task has been
        running, not the last scheduler ping.
      - scheduler_last_seen_secs: seconds since kanban_scheduler.log was last
        written. If > 180s the scheduler is quiet; > 600s it's likely dead.
      - staleness: "active" | "warning" | "stale" | "unknown"
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, title, status, priority, task_type, failure_count, "
            "last_failure_at, created_at, updated_at, description, "
            "dispatch_source, executor_type "
            "FROM kanban_tasks WHERE status = 'in_progress' "
            "ORDER BY updated_at DESC"
        ).fetchall()

        now = datetime.now(timezone.utc)

        # Scheduler heartbeat — last write to the scheduler log file.
        log_path = pathlib.Path(".tmp/kanban_scheduler.log")
        scheduler_last_seen_secs = None
        if log_path.exists():
            scheduler_last_seen_secs = int(_time.time() - log_path.stat().st_mtime)

        tasks = []
        for r in rows:
            d = dict(r)

            # Elapsed since dispatch (updated_at is set at dispatch time).
            updated_raw = d.get("updated_at") or d.get("created_at")
            elapsed_dispatch_secs = None
            if updated_raw:
                try:
                    from dateutil.parser import parse as _parse_dt
                    dt = _parse_dt(str(updated_raw))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    elapsed_dispatch_secs = int((now - dt).total_seconds())
                except Exception:
                    pass

            d["elapsed_dispatch_secs"] = elapsed_dispatch_secs
            d["scheduler_last_seen_secs"] = scheduler_last_seen_secs

            if scheduler_last_seen_secs is None:
                staleness = "unknown"
            elif scheduler_last_seen_secs > 600:
                staleness = "stale"
            elif scheduler_last_seen_secs > 180:
                staleness = "warning"
            else:
                staleness = "active"

            d["staleness"] = staleness
            tasks.append(d)

        return jsonify({
            "tasks": tasks,
            "scheduler_last_seen_secs": scheduler_last_seen_secs,
        })
    finally:
        conn.close()
