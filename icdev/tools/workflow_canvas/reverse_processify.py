"""Reverse Process-Ify — reconstruct an as-executed document from a completed kanban run."""
from __future__ import annotations


def reverse_processify(workflow_id: str) -> str:
    """
    Given all kanban tasks for a workflow are done, synthesize an as-executed document
    capturing the actual execution path, decisions made, and form data submitted.
    Returns the reconstructed document text.
    """
    import yaml
    from tools.db.storage import get_connection, sql_placeholder
    from tools.llm.router import LLMRouter
    from tools.llm.provider import LLMRequest

    conn = get_connection()
    ph = sql_placeholder(conn)

    # Load workflow definition
    wf_row = conn.execute(
        f"SELECT * FROM studio_workflows WHERE workflow_id={ph}", (workflow_id,)
    ).fetchone()
    if not wf_row:
        conn.close()
        raise ValueError(f"Workflow {workflow_id} not found")
    wf_row = dict(wf_row)

    try:
        wf_data = yaml.safe_load(wf_row.get("template_yaml") or "{}") or {}
    except Exception:
        wf_data = {}

    # Load all kanban tasks for this workflow
    prefix = f"processify-{workflow_id[:8]}-%"
    tasks = conn.execute(
        f"SELECT * FROM kanban_tasks WHERE id LIKE {ph} ORDER BY updated_at",
        (prefix,),
    ).fetchall()
    tasks = [dict(t) for t in tasks]
    conn.close()

    if not tasks:
        return "(No kanban tasks found — cannot reconstruct executed document)"

    steps = wf_data.get("steps", [])
    wf_name = wf_data.get("workflow_name") or wf_row.get("name") or "Workflow"
    industry = wf_data.get("industry", "General")

    # Build execution narrative
    execution_lines = []
    for i, task in enumerate(tasks):
        status = task.get("status", "unknown")
        title = task.get("title", f"Task {i+1}")
        updated = task.get("updated_at", "")
        description = task.get("description", "")
        execution_lines.append(
            f"{i+1}. [{status.upper()}] {title}\n"
            f"   Completed: {updated}\n"
            + (f"   Notes: {description[:200]}\n" if description else "")
        )

    execution_text = "\n".join(execution_lines)

    prompt = (
        f"You are reconstructing an as-executed Standard Operating Procedure from a completed process run.\n\n"
        f"Original workflow: {wf_name}\n"
        f"Industry: {industry}\n\n"
        f"Execution log ({len(tasks)} tasks completed):\n"
        f"{execution_text}\n\n"
        "Write an as-executed document in professional SOP format:\n"
        "- Title: 'AS-EXECUTED: {workflow_name}'\n"
        "- Include execution date, all completed steps, who was responsible\n"
        "- Note any tasks that were skipped or marked differently\n"
        "- Add an 'Execution Summary' section\n"
        "- Use clear section headers\n"
        "- Keep it factual and traceable\n\n"
        "Return plain Markdown text only."
    )

    doc_text = f"# AS-EXECUTED: {wf_name}\n\n"
    doc_text += "**Execution Date:** Auto-reconstructed from kanban run\n\n"
    doc_text += f"## Execution Log\n\n{execution_text}"

    try:
        router = LLMRouter()
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="You are a professional process documenter. Return only Markdown.",
            max_tokens=2000,
        )
        result = router.invoke("reverse_processify", req)
        doc_text = (result.content if hasattr(result, "content") else str(result)).strip()
    except Exception:
        pass

    return doc_text
