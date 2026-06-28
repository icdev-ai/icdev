"""Handoff Ceremony Wizard — generate structured handoff brief when chain phase transitions."""
from __future__ import annotations
from datetime import datetime, timezone


def generate_handoff_brief(
    chain: dict,
    phase: dict,
    completed_tasks: list[dict],
    next_phase: dict | None = None,
    llm_router=None,
) -> str:
    """
    Produce a markdown handoff brief for a completing chain phase.

    chain           — wfc_process_chains row dict
    phase           — wfc_chain_phases row dict (the one completing)
    completed_tasks — kanban_tasks that are 'done' for this phase
    next_phase      — optional next phase dict for handoff context
    llm_router      — optional; falls back to template
    """
    if llm_router:
        return _llm_brief(chain, phase, completed_tasks, next_phase, llm_router)
    return _template_brief(chain, phase, completed_tasks, next_phase)


def _template_brief(chain: dict, phase: dict, tasks: list[dict], next_phase: dict | None) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    chain_name = chain.get("name") or "Chain"
    phase_name = phase.get("phase_name") or f"Phase {phase.get('phase_number', '')}"
    lines = [
        f"# Handoff Brief — {chain_name}",
        f"**Phase:** {phase_name}",
        f"**Date:** {now}",
        "",
        "## Completed Deliverables",
    ]
    if tasks:
        for t in tasks:
            lines.append(f"- {t.get('title') or 'Task'}")
    else:
        lines.append("*No tasks linked.*")
    lines += ["", "## Outstanding Items"]
    lines.append("*None identified.*")
    if next_phase:
        next_name = next_phase.get("phase_name") or f"Phase {next_phase.get('phase_number', '')}"
        lines += [
            "",
            f"## Next Phase: {next_name}",
            f"Status: {next_phase.get('phase_status') or 'pending'}",
        ]
    lines += [
        "",
        "## Handoff Checklist",
        "- [ ] All deliverables reviewed and accepted",
        "- [ ] Documentation updated",
        "- [ ] Next phase team briefed",
        "- [ ] Open items escalated to chain owner",
        "",
        "*Generated automatically by Process-Ify Handoff Wizard*",
    ]
    return "\n".join(lines)


def _llm_brief(chain: dict, phase: dict, tasks: list[dict], next_phase: dict | None, llm_router) -> str:
    from tools.llm.router import LLMRequest

    task_titles = "\n".join(f"- {t.get('title', '')}" for t in tasks[:30])
    next_ctx = f"Next phase: {next_phase.get('phase_name')}" if next_phase else "This is the final phase."
    system = (
        "You are a project coordinator. Write a concise, professional handoff brief in Markdown "
        "covering: what was completed, key decisions, open items, and handoff checklist for the next team."
    )
    prompt = (
        f"Chain: {chain.get('name')}\n"
        f"Completing phase: {phase.get('phase_name') or 'Phase ' + str(phase.get('phase_number', ''))}\n"
        f"Completed tasks:\n{task_titles}\n"
        f"{next_ctx}\n\n"
        "Write the handoff brief."
    )
    try:
        result = llm_router.invoke(
            "handoff_brief",
            LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=system,
                max_tokens=800,
            ),
        )
        return result.content if hasattr(result, "content") else str(result)
    except Exception as exc:
        fallback = _template_brief(chain, phase, tasks, next_phase)
        fallback += f"\n\n*LLM generation failed: {exc}*"
        return fallback


def save_handoff_brief(conn, phase_id: str, brief_text: str) -> None:
    """Persist handoff brief to wfc_chain_phases.handoff_brief column."""
    from tools.db.storage import sql_placeholder, get_canvas_connection
    cc = get_canvas_connection("ICDEV_WFC_ENABLED")
    cc_ph = sql_placeholder(cc)
    now = datetime.now(timezone.utc).isoformat()
    try:
        cc.execute(
            "ALTER TABLE wfc_chain_phases ADD COLUMN IF NOT EXISTS handoff_brief TEXT",
        )
    except Exception:
        pass
    cc.execute(
        f"UPDATE wfc_chain_phases SET handoff_brief = {cc_ph}, updated_at = {cc_ph} WHERE id = {cc_ph}",
        (brief_text, now, phase_id),
    )
    cc.commit()
    cc.close()
