"""Process Conformance Checking — expected step order vs actual kanban completion."""
from __future__ import annotations
from datetime import datetime, timezone


def check_conformance(workflow: dict, tasks: list[dict]) -> dict:
    """
    Compare expected step order (from processified workflow) vs actual task completion order.

    workflow — processified workflow dict with steps list
    tasks    — kanban_tasks rows filtered to this workflow, ideally ordered by updated_at
    """
    expected_steps = [s.get("title", "").strip().lower() for s in (workflow.get("steps") or [])]
    done_tasks = [t for t in tasks if (t.get("status") or "") == "done"]
    done_tasks_sorted = sorted(
        done_tasks,
        key=lambda t: t.get("updated_at") or t.get("created_at") or "",
    )
    executed_titles = [t.get("title", "").strip().lower() for t in done_tasks_sorted]

    deviations: list[dict] = []
    skipped: list[str] = []
    unexpected: list[str] = []

    executed_set = set(executed_titles)
    expected_set = set(expected_steps)

    for title in expected_steps:
        if title not in executed_set:
            skipped.append(title)

    for title in executed_titles:
        if title not in expected_set:
            unexpected.append(title)

    # Order violations — expected step A should precede expected step B
    # Check if actual order violates expected order for steps that appear in both
    common = [t for t in expected_steps if t in executed_set]
    actual_positions = {t: executed_titles.index(t) for t in executed_titles if t in expected_set}
    for i in range(len(common) - 1):
        a, b = common[i], common[i + 1]
        if a in actual_positions and b in actual_positions:
            if actual_positions[a] > actual_positions[b]:
                deviations.append({
                    "type": "order_violation",
                    "message": f"'{b}' was completed before '{a}' (reversed expected order)",
                })

    # Role violations — check if assignee_role on task matches expected step
    steps_by_title = {s.get("title", "").strip().lower(): s for s in (workflow.get("steps") or [])}
    wrong_role: list[dict] = []
    for task in done_tasks:
        title_key = (task.get("title") or "").strip().lower()
        expected_step = steps_by_title.get(title_key)
        if expected_step:
            exp_role = (expected_step.get("assignee_role") or "").strip()
            actual_role = (task.get("assignee_role") or "").strip()
            if exp_role and actual_role and exp_role.lower() != actual_role.lower():
                wrong_role.append({
                    "step": task.get("title"),
                    "expected_role": exp_role,
                    "actual_role": actual_role,
                })

    conformant = not deviations and not skipped and not wrong_role

    return {
        "conformant": conformant,
        "total_expected_steps": len(expected_steps),
        "total_executed": len(done_tasks),
        "skipped_steps": skipped,
        "unexpected_steps": unexpected,
        "order_deviations": deviations,
        "role_deviations": wrong_role,
        "summary": (
            "Conformant" if conformant
            else f"{len(deviations)} order violation(s), {len(skipped)} skipped, {len(wrong_role)} wrong role(s)"
        ),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def check_conformance_from_db(workflow_id: str) -> dict:
    """Load workflow + tasks from DB and run conformance check."""
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
    return check_conformance(workflow, tasks_list)
