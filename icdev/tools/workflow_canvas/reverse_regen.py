"""Reverse Process-Ify — synthesize as-executed document from completed kanban run."""
from __future__ import annotations


def reverse_regen(workflow: dict, tasks: list[dict], llm_router=None) -> str:
    """
    Produce a human-readable as-executed document from:
      - workflow: the original processified workflow dict
      - tasks: kanban task rows that were completed for this workflow
      - llm_router: optional; falls back to template if absent
    """
    if llm_router:
        return _llm_reverse(workflow, tasks, llm_router)
    return _template_reverse(workflow, tasks)


def _template_reverse(workflow: dict, tasks: list[dict]) -> str:
    from datetime import datetime, timezone
    lines: list[str] = [
        "# As-Executed Process Report",
        f"**Workflow:** {workflow.get('workflow_name') or 'Unnamed'}",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Summary",
        f"- **Total steps defined:** {len(workflow.get('steps') or [])}",
        f"- **Tasks completed:** {sum(1 for t in tasks if (t.get('status') or '') == 'done')}",
        f"- **Total tasks:** {len(tasks)}",
        "",
        "## Executed Steps",
    ]
    done_tasks = [t for t in tasks if (t.get("status") or "") == "done"]
    for i, task in enumerate(done_tasks, 1):
        lines.append(f"### {i}. {task.get('title') or 'Step'}")
        if task.get("description"):
            lines.append(f"{task['description']}")
        if task.get("updated_at"):
            lines.append(f"*Completed: {task['updated_at']}*")
        lines.append("")
    if not done_tasks:
        lines.append("*No tasks completed yet.*")
    return "\n".join(lines)


def _llm_reverse(workflow: dict, tasks: list[dict], llm_router) -> str:
    from tools.llm.router import LLMRequest

    done_tasks = [t for t in tasks if (t.get("status") or "") == "done"]
    task_summary = "\n".join(
        f"- {t.get('title', '')} (completed {t.get('updated_at', 'unknown')})"
        for t in done_tasks[:50]
    )
    system = (
        "You are a technical writer. The user provides an original process definition and the "
        "list of actually completed tasks. Write a professional 'as-executed' process document "
        "in Markdown — what was done, who did it, when, and any notable deviations from the plan."
    )
    prompt = (
        f"Original workflow: {workflow.get('workflow_name')}\n"
        f"Steps planned: {len(workflow.get('steps') or [])}\n\n"
        f"Completed tasks:\n{task_summary}\n\n"
        "Write the as-executed document."
    )
    try:
        result = llm_router.invoke(
            "reverse_regen",
            LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=system,
                max_tokens=1500,
            ),
        )
        return result.content if hasattr(result, "content") else str(result)
    except Exception as exc:
        fallback = _template_reverse(workflow, tasks)
        fallback += f"\n\n*LLM generation failed: {exc}*"
        return fallback


def reverse_regen_from_db(workflow_id: str) -> str:
    """Load workflow + tasks from DB and produce as-executed doc."""
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
    wf_dict = wf_row if hasattr(wf_row, "keys") else {}
    template_yaml = wf_dict.get("template_yaml") or "{}"
    workflow = yaml.safe_load(template_yaml) or {}

    tasks = conn.execute(
        f"SELECT * FROM kanban_tasks WHERE description LIKE {ph}",
        (f"%{workflow_id}%",),
    ).fetchall()
    tasks_list = [dict(t) if hasattr(t, "keys") else {} for t in tasks]
    conn.close()

    try:
        from tools.llm.router import LLMRouter
        router = LLMRouter()
    except Exception:
        router = None

    return reverse_regen(workflow, tasks_list, llm_router=router)
