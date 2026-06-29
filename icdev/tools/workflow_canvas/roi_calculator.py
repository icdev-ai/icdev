"""ROI Calculator for Process-Ify chains."""
from __future__ import annotations
from datetime import datetime, timezone


def calculate_chain_roi(chain_row: dict, phases_rows: list[dict], tasks: list[dict]) -> dict:
    """
    Compute ROI metrics for a completed or in-progress chain.

    chain_row   — row from wfc_process_chains
    phases_rows — rows from wfc_chain_phases (ordered)
    tasks       — kanban_tasks rows linked to this chain
    """
    phase_count = len(phases_rows)
    total_tasks = len(tasks)
    done_tasks = [t for t in tasks if (t.get("status") or "") == "done"]
    in_progress_tasks = [t for t in tasks if (t.get("status") or "") in ("in_progress", "review")]
    completion_pct = round(len(done_tasks) / max(total_tasks, 1) * 100)

    # Unique roles across phases
    roles: set[str] = set()
    for p in phases_rows:
        r = p.get("assignee_role") or ""
        if r:
            roles.add(r)
    for t in tasks:
        for field in ("assignee_role", "reviewer_role"):
            v = (t.get(field) or "").strip()
            if v:
                roles.add(v)

    # Automation coverage — tasks whose title contains automation keywords
    auto_keywords = {"auto", "automat", "trigger", "schedule", "workflow", "api", "notify"}
    auto_count = sum(
        1 for t in tasks
        if any(kw in (t.get("title") or "").lower() for kw in auto_keywords)
    )
    automation_pct = round(auto_count / max(total_tasks, 1) * 100)

    # Cycle time estimation: sla_hours field if present in phase metadata
    est_hours_total = 0.0
    for p in phases_rows:
        meta = p.get("meta") or {}
        if isinstance(meta, str):
            import json
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        est_hours_total += float(meta.get("sla_hours", 0) or 0)

    # Actual cycle time from chain created_at → now (or last done task updated_at)
    actual_hours: float | None = None
    chain_created = chain_row.get("created_at")
    if chain_created:
        try:
            if isinstance(chain_created, str):
                from datetime import datetime as dt
                ca = dt.fromisoformat(chain_created.replace("Z", "+00:00"))
            else:
                ca = chain_created
            if ca.tzinfo is None:
                from datetime import timezone as tz
                ca = ca.replace(tzinfo=tz.utc)
            now_utc = datetime.now(timezone.utc)
            actual_hours = (now_utc - ca).total_seconds() / 3600
        except Exception:
            pass

    return {
        "chain_id": chain_row.get("id"),
        "chain_name": chain_row.get("name") or "Chain",
        "phase_count": phase_count,
        "total_tasks": total_tasks,
        "done_tasks": len(done_tasks),
        "in_progress_tasks": len(in_progress_tasks),
        "completion_pct": completion_pct,
        "unique_roles": sorted(roles),
        "role_count": len(roles),
        "automation_pct": automation_pct,
        "estimated_hours_total": round(est_hours_total, 1),
        "actual_hours_elapsed": round(actual_hours, 1) if actual_hours is not None else None,
        "cycle_time_delta_hours": (
            round(actual_hours - est_hours_total, 1)
            if actual_hours is not None and est_hours_total
            else None
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def chain_roi_from_db(chain_id: str) -> dict:
    """Load chain + phases + tasks from DB and compute ROI."""
    from tools.db.storage import get_connection, sql_placeholder, get_canvas_connection

    cc = get_canvas_connection("ICDEV_WFC_ENABLED")
    cc_ph = sql_placeholder(cc)
    chain_row = cc.execute(
        f"SELECT * FROM wfc_process_chains WHERE id = {cc_ph}", (chain_id,)
    ).fetchone()
    if not chain_row:
        cc.close()
        raise ValueError(f"Chain {chain_id} not found")
    phases_rows = cc.execute(
        f"SELECT * FROM wfc_chain_phases WHERE chain_id = {cc_ph} ORDER BY phase_number",
        (chain_id,),
    ).fetchall()
    cc.close()

    conn = get_connection()
    ph = sql_placeholder(conn)
    tasks = conn.execute(
        f"SELECT * FROM kanban_tasks WHERE id LIKE {ph}", (f"wfc-{chain_id[:8]}%",)
    ).fetchall()
    # Also try matching by title prefix or description containing chain_id
    if not tasks:
        tasks = conn.execute(
            f"SELECT * FROM kanban_tasks WHERE description LIKE {ph}",
            (f"%{chain_id}%",),
        ).fetchall()
    conn.close()

    chain_dict = dict(chain_row) if hasattr(chain_row, "keys") else dict(zip(chain_row.cursor.description, chain_row))
    phases_list = [dict(p) if hasattr(p, "keys") else {} for p in phases_rows]
    tasks_list = [dict(t) if hasattr(t, "keys") else {} for t in tasks]
    return calculate_chain_roi(chain_dict, phases_list, tasks_list)
