"""SLA Gate Enforcement for Process-Ify workflows."""
from __future__ import annotations
from datetime import datetime, timezone


def check_sla_status(workflow: dict, tasks: list[dict]) -> dict:
    """
    For each step with sla_hours defined, check if in_progress/review tasks are overdue.

    workflow — processified workflow dict
    tasks    — kanban_tasks rows for this workflow
    """
    steps_by_title = {
        (s.get("title") or "").strip().lower(): s
        for s in (workflow.get("steps") or [])
    }
    now_utc = datetime.now(timezone.utc)
    overdue: list[dict] = []
    at_risk: list[dict] = []
    compliant: list[dict] = []

    for task in tasks:
        status = task.get("status") or ""
        if status not in ("in_progress", "review"):
            continue
        title_key = (task.get("title") or "").strip().lower()
        step = steps_by_title.get(title_key)
        if not step:
            continue
        sla_hours = step.get("sla_hours")
        if not sla_hours:
            continue
        try:
            sla_hours = float(sla_hours)
        except (TypeError, ValueError):
            continue

        started_at_raw = task.get("updated_at") or task.get("created_at") or ""
        try:
            if isinstance(started_at_raw, str):
                started_at = datetime.fromisoformat(started_at_raw.replace("Z", "+00:00"))
            else:
                started_at = started_at_raw
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        elapsed_hours = (now_utc - started_at).total_seconds() / 3600
        remaining_hours = sla_hours - elapsed_hours
        pct_consumed = round(elapsed_hours / sla_hours * 100)

        record = {
            "task_id": task.get("id"),
            "title": task.get("title"),
            "sla_hours": sla_hours,
            "elapsed_hours": round(elapsed_hours, 1),
            "remaining_hours": round(remaining_hours, 1),
            "pct_consumed": pct_consumed,
            "status": status,
        }
        if remaining_hours < 0:
            record["sla_status"] = "overdue"
            overdue.append(record)
        elif pct_consumed >= 80:
            record["sla_status"] = "at_risk"
            at_risk.append(record)
        else:
            record["sla_status"] = "ok"
            compliant.append(record)

    return {
        "overdue_count": len(overdue),
        "at_risk_count": len(at_risk),
        "compliant_count": len(compliant),
        "overdue": overdue,
        "at_risk": at_risk,
        "compliant": compliant,
        "checked_at": now_utc.isoformat(),
        "summary": (
            f"{len(overdue)} overdue, {len(at_risk)} at risk, {len(compliant)} on track"
        ),
    }


def check_sla_from_db(workflow_id: str) -> dict:
    import yaml
    from tools.db.storage import get_connection, sql_placeholder

    conn = get_connection()
    ph = sql_placeholder(conn)
    wf_row = conn.execute(
        f"SELECT * FROM studio_workflows WHERE workflow_id = {ph}", (workflow_id,)
    ).fetchone()
    if not wf_row:
        conn.close()
        raise ValueError(f"Workflow {workflow_id} not found")
    wf_dict = dict(wf_row) if hasattr(wf_row, "keys") else {}
    workflow = yaml.safe_load(wf_dict.get("template_yaml") or "{}") or {}
    tasks = conn.execute(
        f"SELECT * FROM kanban_tasks WHERE description LIKE {ph}",
        (f"%{workflow_id}%",),
    ).fetchall()
    tasks_list = [dict(t) if hasattr(t, "keys") else {} for t in tasks]
    conn.close()
    return check_sla_status(workflow, tasks_list)
