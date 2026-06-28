"""AI Process Coach — surface contextual guidance when a step goes in_progress."""
from __future__ import annotations


def coach_step(step: dict, workflow_context: dict, llm_router=None) -> dict:
    """
    Return coaching content for a specific workflow step.

    step             — the step dict (title, assignee_role, checklist, etc.)
    workflow_context — the full workflow dict for context
    llm_router       — optional; falls back to checklist-based tips
    """
    step_title = step.get("title") or "Step"
    checklist = step.get("checklist") or []
    role = step.get("assignee_role") or "Unassigned"

    if llm_router:
        return _llm_coach(step, workflow_context, llm_router)

    tips: list[str] = []
    title_lower = step_title.lower()
    if "review" in title_lower:
        tips.append("Review all prior step outputs before proceeding.")
        tips.append("Use a checklist to track each review criterion.")
    if "approval" in title_lower or "approve" in title_lower:
        tips.append("Ensure all required signatures/approvals are captured.")
        tips.append("Document approval rationale for audit trail.")
    if "notify" in title_lower:
        tips.append("Confirm distribution list is current before sending.")
    if "draft" in title_lower or "write" in title_lower or "document" in title_lower:
        tips.append("Reference the source document for terminology consistency.")
        tips.append("Use structured templates when available.")
    if not tips:
        tips.append("Complete checklist items in order.")
        tips.append("Flag blockers immediately to the chain owner.")

    return {
        "step_title": step_title,
        "role": role,
        "tips": tips,
        "checklist_reminders": [f"[ ] {item}" for item in checklist[:10]],
        "source": "rule_based",
    }


def _llm_coach(step: dict, workflow_context: dict, llm_router) -> dict:
    from tools.llm.router import LLMRequest

    system = (
        "You are an expert process coach. Given a workflow step and its context, "
        "provide 3-5 concise, actionable tips to help the assignee execute this step well. "
        "Return JSON: {tips: [string], common_mistakes: [string], resources: [string]}"
    )
    prompt = (
        f"Workflow: {workflow_context.get('workflow_name') or 'Unknown'}\n"
        f"Step: {step.get('title')}\n"
        f"Role: {step.get('assignee_role') or 'Unassigned'}\n"
        f"Checklist: {step.get('checklist') or []}\n\n"
        "Provide coaching tips."
    )
    try:
        import json
        import re
        result = llm_router.invoke(
            "process_coach",
            LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=system,
                max_tokens=500,
            ),
        )
        raw = result.content if hasattr(result, "content") else str(result)
        raw = re.sub(r"^```(?:json)?\n?", "", raw.strip())
        raw = re.sub(r"\n?```$", "", raw)
        data = json.loads(raw)
        data["step_title"] = step.get("title")
        data["role"] = step.get("assignee_role") or "Unassigned"
        data["source"] = "llm"
        return data
    except Exception as exc:
        fallback = coach_step(step, workflow_context, llm_router=None)
        fallback["llm_error"] = str(exc)
        return fallback


def coach_from_db(workflow_id: str, step_index: int) -> dict:
    """Load workflow from DB and coach a specific step by index."""
    import yaml
    from tools.db.storage import get_connection, sql_placeholder

    conn = get_connection()
    ph = sql_placeholder(conn)
    wf_row = conn.execute(
        f"SELECT * FROM studio_workflows WHERE workflow_id = {ph}", (workflow_id,)
    ).fetchone()
    conn.close()
    if not wf_row:
        raise ValueError(f"Workflow {workflow_id} not found")
    wf_dict = dict(wf_row) if hasattr(wf_row, "keys") else {}
    workflow = yaml.safe_load(wf_dict.get("template_yaml") or "{}") or {}
    steps = workflow.get("steps") or []
    if step_index < 0 or step_index >= len(steps):
        raise ValueError(f"Step index {step_index} out of range (0-{len(steps)-1})")

    try:
        from tools.llm.router import LLMRouter
        router = LLMRouter()
    except Exception:
        router = None

    return coach_step(steps[step_index], workflow, llm_router=router)
