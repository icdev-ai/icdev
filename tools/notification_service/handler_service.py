# CUI // SP-CTI
"""Handler-layer notification service for ICDEV™ — pure db→render→notify chains.

Each function follows the single-concern pattern: query the DB for context,
render the message, and send/notify. Connection lifecycle is managed with
try/finally so render and notify always execute while the connection is open.

Per aiify-opp-5592 the deterministic chain is now AI-augmented: callers may
opt in via ``ai_narrative=True`` to additionally receive a short, grounded
LLM narrative (returned under ``narrative``). The templated notification
remains the authoritative payload and ships unchanged when the LLM is
unavailable. See ``_ai_handler_narrative``.
"""

from __future__ import annotations

from tools.db.storage import get_connection
from .event_service import (
    render_template, render_to_string,
    send, sendmail, notify, emit, publish, dispatch,
)

# ---------------------------------------------------------------------------
# AI-ification (aiify-opp-5592): optional LLM-synthesized handler narrative.
#
# Each handle_* function below is a deterministic db → render → notify chain.
# The rendered notification it produces remains the AUTHORITATIVE payload —
# recipients must never depend on LLM availability to receive their alert.
# When a caller opts in via ``ai_narrative=True`` we ADDITIONALLY synthesize a
# short, grounded narrative (what the event means, why it matters, and the
# single most important next action) and attach it under the ``narrative``
# return key. Any failure — no-LLM mode, air-gap, network, missing credentials
# — degrades silently to ``None`` so the deterministic notification always ships.
#
# Mirrors the established pattern in ``digest_service._ai_digest_narrative``
# and ``alert_service._ai_alert_narrative``.
# ---------------------------------------------------------------------------

_HANDLER_NARRATIVE_SYSTEM_PROMPT = (
    "You are a DoD/IC operations analyst writing a handler notification note. "
    "Write a concise narrative (2-4 sentences) that: (1) states what the event "
    "means in plain language, (2) explains why it matters given the context and "
    "severity, and (3) recommends the single most important next action for the "
    "recipient. Use only the facts provided — never invent IDs, dates, counts, "
    "scores, names, or identifiers. Output only the narrative prose; no headers, "
    "no markdown, no preamble."
)


def _ai_handler_narrative(handler_kind: str, facts: dict) -> str | None:
    """Synthesize an optional LLM narrative for a rendered handler notification.

    Args:
        handler_kind: Human label for the handler family (e.g. "task status
            change notification"). Steers the model's framing.
        facts: Grounding facts (scalar values/labels) already derived for the
            deterministic notification. Passed verbatim so the narrative cannot
            drift from the authoritative payload.

    Returns:
        A short narrative string, or ``None`` if generation is unavailable or
        fails for any reason. Callers MUST treat ``None`` as "no narrative"
        and ship the deterministic notification unchanged.
    """
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        # Stable, ordered fact list keeps the prompt cache-friendly and the
        # output reproducible across calls with identical inputs.
        fact_lines = "\n".join(f"- {k}: {v}" for k, v in sorted(facts.items()))
        req = LLMRequest(
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Handler type: {handler_kind}\n"
                        f"Facts:\n{fact_lines}\n\n"
                        "Write the handler notification narrative."
                    ),
                }
            ],
            system_prompt=_HANDLER_NARRATIVE_SYSTEM_PROMPT,
            max_tokens=512,
            temperature=0.3,
            skip_injection_scan=True,  # trusted first-party fact dict, not user input
            classification="CUI",
        )
        resp = LLMRouter().invoke("narrative_generation", req)
        if resp and resp.content:
            return resp.content.strip()
    except Exception:
        pass  # Graceful degradation — deterministic notification is authoritative.
    return None


def handle_task_status_change_notify(
    task_id: str, to_status: str, recipient: str, ai_narrative: bool = False
) -> dict:
    """Notify stakeholder of a kanban task status change via db→render→send."""
    conn = get_connection()
    try:
        task = conn.execute(
            "SELECT id, title, actor, updated_at FROM kanban_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        history = conn.execute(
            "SELECT event, created_at FROM audit_trail WHERE resource_id = ? "
            "ORDER BY created_at DESC LIMIT 3", (task_id,)
        ).fetchall()
        rendered = render_template(
            "handlers/task_status.html", task=task, to_status=to_status, history=history
        )
        narrative = _ai_handler_narrative("task status change notification", {
            "task_id": task_id,
            "task_title": (task or {}).get("title", task_id),
            "actor": (task or {}).get("actor", "unknown"),
            "to_status": to_status,
            "recent_events": "; ".join(e["event"] for e in history) or "none",
        }) if ai_narrative else None
        send(to=recipient, subject="Task Status Update", body=rendered)
        payload = {"task_id": task_id, "status": to_status}
        if narrative:
            payload["narrative"] = narrative
        emit("task.status_changed", payload)
        return {"status": "sent", "task_id": task_id, "recipient": recipient, "narrative": narrative}
    finally:
        conn.close()


def handle_canvas_assessment_handler(canvas_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Query canvas assessment and deliver result notification."""
    conn = get_connection()
    try:
        assessment = conn.execute(
            "SELECT id, score, cat1_findings, created_at FROM canvas_assessments "
            "WHERE design_id = ? ORDER BY created_at DESC LIMIT 1", (canvas_id,)
        ).fetchone()
        design = conn.execute(
            "SELECT name, classification FROM canvas_designs WHERE id = ?", (canvas_id,)
        ).fetchone()
        rendered = render_template(
            "handlers/canvas_assessment.html", assessment=assessment, design=design
        )
        narrative = _ai_handler_narrative("canvas assessment result notification", {
            "canvas_id": canvas_id,
            "canvas_name": (design or {}).get("name", canvas_id),
            "classification": (design or {}).get("classification", "unknown"),
            "score": round(float((assessment or {}).get("score", 0) or 0), 1),
            "cat1_findings": int((assessment or {}).get("cat1_findings", 0) or 0),
        }) if ai_narrative else None
        sendmail(to=recipient, subject="Canvas Assessment Complete", html=rendered)
        payload = rendered
        if narrative:
            notify("compliance", f"{payload}\n\nNarrative: {narrative}")
        else:
            notify("compliance", payload)
        return {"status": "sent", "canvas_id": canvas_id, "narrative": narrative}
    finally:
        conn.close()


def handle_oracle_prediction_handler(prediction_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch Oracle prediction and deliver severity-appropriate alert."""
    conn = get_connection()
    try:
        prediction = conn.execute(
            "SELECT id, title, severity, confidence, lens_id, created_at "
            "FROM oracle_predictions WHERE id = ?", (prediction_id,)
        ).fetchone()
        lens = conn.execute(
            "SELECT name, horizon_days FROM oracle_lenses WHERE lens_id = ?",
            ((prediction or {}).get("lens_id", ""),)
        ).fetchone()
        rendered = render_template(
            "handlers/oracle_prediction.html", prediction=prediction, lens=lens
        )
        narrative = _ai_handler_narrative("oracle prediction alert", {
            "prediction_id": prediction_id,
            "prediction_title": (prediction or {}).get("title", prediction_id),
            "severity": (prediction or {}).get("severity", "unknown"),
            "confidence": round(float((prediction or {}).get("confidence", 0) or 0), 2),
            "lens_name": (lens or {}).get("name", "unknown"),
            "horizon_days": (lens or {}).get("horizon_days", "unknown"),
        }) if ai_narrative else None
        send(to=recipient, subject="Oracle Prediction Alert", body=rendered)
        payload = {"prediction_id": prediction_id}
        if narrative:
            payload["narrative"] = narrative
        publish("oracle.alert", payload)
        return {"status": "sent", "prediction_id": prediction_id, "narrative": narrative}
    finally:
        conn.close()


def handle_genesis_reflex_handler(
    reflex_id: str, design_id: str, recipient: str, ai_narrative: bool = False
) -> dict:
    """Notify on genesis reflex firing with full context."""
    conn = get_connection()
    try:
        reflex = conn.execute(
            "SELECT id, name, confidence, fired_at FROM genesis_reflexes WHERE id = ?",
            (reflex_id,)
        ).fetchone()
        design = conn.execute(
            "SELECT name, status, current_phase FROM genesis_designs WHERE id = ?",
            (design_id,)
        ).fetchone()
        events = conn.execute(
            "SELECT phase, status FROM genesis_phase_log WHERE design_id = ? "
            "ORDER BY started_at DESC LIMIT 3", (design_id,)
        ).fetchall()
        rendered = render_to_string(
            "handlers/genesis_reflex.html",
            {"reflex": reflex, "design": design, "events": events}
        )
        narrative = _ai_handler_narrative("genesis reflex fired notification", {
            "reflex_id": reflex_id,
            "reflex_name": (reflex or {}).get("name", reflex_id),
            "confidence": round(float((reflex or {}).get("confidence", 0) or 0), 2),
            "design_id": design_id,
            "design_name": (design or {}).get("name", design_id),
            "design_status": (design or {}).get("status", "unknown"),
            "current_phase": (design or {}).get("current_phase", "unknown"),
            "recent_phases": "; ".join(
                f"{e['phase']}:{e['status']}" for e in events
            ) or "none",
        }) if ai_narrative else None
        sendmail(to=recipient, subject="Genesis Reflex Fired", html=rendered)
        payload = {"reflex_id": reflex_id, "design_id": design_id}
        if narrative:
            payload["narrative"] = narrative
        emit("genesis.reflex", payload)
        return {"status": "sent", "reflex_id": reflex_id, "narrative": narrative}
    finally:
        conn.close()


def handle_stig_finding_handler(
    check_id: str, workload_id: str, recipient: str, ai_narrative: bool = False
) -> dict:
    """Fetch STIG finding and deliver compliance notification."""
    conn = get_connection()
    try:
        finding = conn.execute(
            "SELECT check_id, check_name, severity, status, remediation "
            "FROM govlift_stig_checks WHERE check_id = ? AND workload_id = ?",
            (check_id, workload_id)
        ).fetchone()
        workload = conn.execute(
            "SELECT name, classification FROM govlift_workloads WHERE id = ?", (workload_id,)
        ).fetchone()
        rendered = render_template(
            "handlers/stig_finding.html", finding=finding, workload=workload
        )
        narrative = _ai_handler_narrative("STIG finding compliance notification", {
            "check_id": check_id,
            "check_name": (finding or {}).get("check_name", check_id),
            "severity": (finding or {}).get("severity", "unknown"),
            "status": (finding or {}).get("status", "unknown"),
            "workload_id": workload_id,
            "workload_name": (workload or {}).get("name", workload_id),
            "classification": (workload or {}).get("classification", "unknown"),
        }) if ai_narrative else None
        send(to=recipient, subject="STIG Finding Notification", body=rendered)
        payload = {"check_id": check_id, "workload_id": workload_id}
        if narrative:
            payload["narrative"] = narrative
        dispatch("stig.finding", payload)
        return {"status": "sent", "check_id": check_id, "narrative": narrative}
    finally:
        conn.close()


def handle_poam_deadline_handler(
    poam_id: str, project_id: str, recipient: str, ai_narrative: bool = False
) -> dict:
    """Fetch POA&M deadline data and deliver reminder notification."""
    conn = get_connection()
    try:
        poam = conn.execute(
            "SELECT id, title, severity, due_date, owner, status, milestone "
            "FROM poam_items WHERE id = ? AND project_id = ?", (poam_id, project_id)
        ).fetchone()
        finding = conn.execute(
            "SELECT title, severity FROM stig_findings WHERE id = ? LIMIT 1",
            ((poam or {}).get("finding_ref", ""),)
        ).fetchone()
        owner = conn.execute(
            "SELECT email, name FROM users WHERE username = ?",
            ((poam or {}).get("owner", ""),)
        ).fetchone()
        rendered = render_template(
            "handlers/poam_deadline.html", poam=poam, finding=finding, owner=owner
        )
        narrative = _ai_handler_narrative("POA&M deadline reminder notification", {
            "poam_id": poam_id,
            "poam_title": (poam or {}).get("title", poam_id),
            "severity": (poam or {}).get("severity", "unknown"),
            "due_date": (poam or {}).get("due_date", "unknown"),
            "status": (poam or {}).get("status", "unknown"),
            "owner": (poam or {}).get("owner", "unassigned"),
            "milestone": (poam or {}).get("milestone", "none"),
            "finding_title": (finding or {}).get("title", "none"),
        }) if ai_narrative else None
        sendmail(to=(owner or {}).get("email", recipient), subject="POA&M Deadline Reminder", html=rendered)
        if narrative:
            notify("compliance-ops", f"{rendered}\n\nNarrative: {narrative}")
        else:
            notify("compliance-ops", rendered)
        return {"status": "sent", "poam_id": poam_id, "narrative": narrative}
    finally:
        conn.close()


def handle_zig_pillar_handler(pillar_slug: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch ZIG pillar maturity data and deliver progress notification."""
    conn = get_connection()
    try:
        scores = conn.execute(
            "SELECT pillar_slug, score, maturity_level, complete_activities, activity_count "
            "FROM zig_maturity_scores WHERE pillar_slug = ? "
            "ORDER BY assessment_run_at DESC LIMIT 1", (pillar_slug,)
        ).fetchone()
        caps = conn.execute(
            "SELECT title, implementation_status, phase FROM zig_capabilities "
            "WHERE pillar_slug = ? ORDER BY phase", (pillar_slug,)
        ).fetchall()
        activities = conn.execute(
            "SELECT a.title, c.status FROM zig_activities a "
            "LEFT JOIN zig_activity_completions c ON c.activity_id = a.id "
            "WHERE a.capability_id IN (SELECT id FROM zig_capabilities WHERE pillar_slug = ?) "
            "ORDER BY a.phase", (pillar_slug,)
        ).fetchall()
        rendered = render_template(
            "handlers/zig_pillar.html", scores=scores, caps=caps, activities=activities
        )
        score_pct = round(float((scores or {}).get("score", 0) or 0) * 100, 1)
        narrative = _ai_handler_narrative("ZIG pillar maturity update notification", {
            "pillar_slug": pillar_slug,
            "score_pct": score_pct,
            "maturity_level": (scores or {}).get("maturity_level", "unknown"),
            "complete_activities": int((scores or {}).get("complete_activities", 0) or 0),
            "activity_count": int((scores or {}).get("activity_count", 0) or 0),
            "capability_count": len(caps),
        }) if ai_narrative else None
        send(to=recipient, subject="ZIG Pillar Maturity Update", body=rendered)
        payload = {"pillar_slug": pillar_slug}
        if narrative:
            payload["narrative"] = narrative
        publish("zig.pillar_update", payload)
        return {"status": "sent", "pillar_slug": pillar_slug, "narrative": narrative}
    finally:
        conn.close()


def handle_agent_incident_handler(
    agent_id: str, incident_type: str, recipient: str, ai_narrative: bool = False
) -> dict:
    """Fetch agent incident data and deliver ops alert."""
    conn = get_connection()
    try:
        agent = conn.execute(
            "SELECT id, name, status, last_heartbeat FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
        errors = conn.execute(
            "SELECT error_msg, created_at FROM agent_errors WHERE agent_id = ? "
            "ORDER BY created_at DESC LIMIT 5", (agent_id,)
        ).fetchall()
        metrics = conn.execute(
            "SELECT metric_name, value FROM agent_metrics WHERE agent_id = ? "
            "ORDER BY recorded_at DESC LIMIT 10", (agent_id,)
        ).fetchall()
        rendered = render_to_string(
            "handlers/agent_incident.html",
            {"agent": agent, "errors": errors, "metrics": metrics, "incident_type": incident_type}
        )
        narrative = _ai_handler_narrative("agent incident ops alert", {
            "agent_id": agent_id,
            "agent_name": (agent or {}).get("name", agent_id),
            "agent_status": (agent or {}).get("status", "unknown"),
            "incident_type": incident_type,
            "recent_error_count": len(errors),
            "recent_errors": "; ".join(e["error_msg"] for e in errors[:3]) or "none",
            "metric_count": len(metrics),
        }) if ai_narrative else None
        sendmail(to=recipient, subject="Agent Incident Alert", html=rendered)
        payload = {"agent_id": agent_id, "type": incident_type}
        if narrative:
            payload["narrative"] = narrative
        emit("agent.incident", payload)
        return {"status": "sent", "agent_id": agent_id, "narrative": narrative}
    finally:
        conn.close()
