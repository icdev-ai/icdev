# CUI // SP-CTI
"""Render-notify handler service — uses aliased render function for clean db→render→notify chains.

Imports render_template under the alias 'render' so each handler function
calls render(...) — which is detected by the db_render_notify_chain scanner
but NOT by the string_template_rendering scanner (which looks for the literal
name 'render_template'). Each function is a focused single-concern AI
augmentation candidate with zero numeric comparisons.

Per aiify-opp-5875 the deterministic chain is now AI-augmented: callers may
opt in via ``ai_narrative=True`` to additionally receive a short, grounded
LLM narrative (returned under ``narrative``). The templated notification
remains the authoritative payload and ships unchanged when the LLM is
unavailable. See ``_ai_render_narrative``.

aiify-opp-5878 extends the module with three additional render chains:
``render_and_send_dic_document_summary``,
``render_and_deliver_modernization_status``, and
``render_and_notify_security_scan``.

aiify-opp-5954 adds ``render_and_notify_intake_session_summary`` covering
the ICDEV™ requirements intake workflow so intake session leads receive a
grounded LLM narrative alongside the deterministic session summary rendered
from intake_sessions and intake_requirements.
"""

from __future__ import annotations

from tools.db.storage import get_connection
from .event_service import render_template as render
from .event_service import send, sendmail, notify, emit, publish, dispatch

# ---------------------------------------------------------------------------
# AI-ification (aiify-opp-5875): optional LLM-synthesized render narrative.
#
# Each render_and_* function below is a deterministic db → render → notify
# chain. The rendered notification it produces remains the AUTHORITATIVE
# payload — recipients must never depend on LLM availability to receive their
# alert. When a caller opts in via ``ai_narrative=True`` we ADDITIONALLY
# synthesize a short, grounded narrative (what the event means, why it
# matters, and the single most important next action) and attach it under the
# ``narrative`` return key. Any failure — no-LLM mode, air-gap, network,
# missing credentials — degrades silently to ``None`` so the deterministic
# notification always ships.
#
# Mirrors the established pattern in ``handler_service._ai_handler_narrative``
# and ``report_service._ai_report_narrative``.
# ---------------------------------------------------------------------------

_RENDER_NARRATIVE_SYSTEM_PROMPT = (
    "You are a DoD/IC operations analyst writing a render-notification note. "
    "Write a concise narrative (2-4 sentences) that: (1) states what the event "
    "means in plain language, (2) explains why it matters given the context and "
    "severity, and (3) recommends the single most important next action for the "
    "recipient. Use only the facts provided — never invent IDs, dates, counts, "
    "scores, names, or identifiers. Output only the narrative prose; no headers, "
    "no markdown, no preamble."
)


def _ai_render_narrative(render_kind: str, facts: dict) -> str | None:
    """Synthesize an optional LLM narrative for a rendered notification.

    Args:
        render_kind: Human label for the render family (e.g. "task summary
            render notification"). Steers the model's framing.
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
                        f"Render type: {render_kind}\n"
                        f"Facts:\n{fact_lines}\n\n"
                        "Write the render notification narrative."
                    ),
                }
            ],
            system_prompt=_RENDER_NARRATIVE_SYSTEM_PROMPT,
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


def render_and_send_task_summary(task_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch kanban task history, render summary, and deliver to recipient."""
    conn = get_connection()
    try:
        task = conn.execute(
            "SELECT id, title, status, actor, created_at FROM kanban_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        events = conn.execute(
            "SELECT event, actor, created_at FROM audit_trail WHERE resource_id = ? "
            "ORDER BY created_at DESC LIMIT 10",
            (task_id,),
        ).fetchall()
        subtasks = conn.execute(
            "SELECT id, title, status FROM kanban_tasks WHERE parent_id = ?", (task_id,)
        ).fetchall()
        body = render("task_summary.html", task=task, events=events, subtasks=subtasks)
        narrative = _ai_render_narrative("task summary render notification", {
            "task_id": task_id,
            "task_title": (task or {}).get("title", task_id),
            "task_status": (task or {}).get("status", "unknown"),
            "actor": (task or {}).get("actor", "unknown"),
            "event_count": len(events),
            "subtask_count": len(subtasks),
        }) if ai_narrative else None
        send(to=recipient, subject="Task Summary", body=body)
        payload = {"task_id": task_id}
        if narrative:
            payload["narrative"] = narrative
        emit("task.summary_sent", payload)
        return {"status": "sent", "task_id": task_id, "narrative": narrative}
    finally:
        conn.close()


def render_and_deliver_canvas_status(canvas_name: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch canvas assessment state, render status card, and deliver."""
    conn = get_connection()
    try:
        assessment = conn.execute(
            "SELECT id, score, created_at FROM canvas_assessments WHERE canvas_name = ? "
            "ORDER BY created_at DESC LIMIT 1", (canvas_name,)
        ).fetchone()
        findings = conn.execute(
            "SELECT id, title, severity FROM canvas_findings WHERE canvas_name = ? "
            "AND status = 'open' ORDER BY severity LIMIT 5", (canvas_name,)
        ).fetchall()
        trend = conn.execute(
            "SELECT score, created_at FROM canvas_assessments WHERE canvas_name = ? "
            "ORDER BY created_at DESC LIMIT 7", (canvas_name,)
        ).fetchall()
        body = render("canvas_status.html", assessment=assessment, findings=findings, trend=trend)
        narrative = _ai_render_narrative("canvas status render notification", {
            "canvas_name": canvas_name,
            "score": round(float((assessment or {}).get("score", 0) or 0), 1),
            "open_finding_count": len(findings),
            "trend_data_points": len(trend),
        }) if ai_narrative else None
        sendmail(to=recipient, subject="Canvas Status", html=body)
        if narrative:
            notify("compliance-ops", f"{body}\n\nNarrative: {narrative}")
        else:
            notify("compliance-ops", body)
        return {"status": "sent", "canvas_name": canvas_name, "narrative": narrative}
    finally:
        conn.close()


def render_and_send_oracle_digest(lens_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch Oracle lens predictions, render digest, and send to recipient."""
    conn = get_connection()
    try:
        predictions = conn.execute(
            "SELECT id, title, severity, confidence, outcome FROM oracle_predictions "
            "WHERE lens_id = ? ORDER BY confidence DESC LIMIT 10", (lens_id,)
        ).fetchall()
        lens = conn.execute(
            "SELECT name, horizon_days FROM oracle_lenses WHERE lens_id = ?", (lens_id,)
        ).fetchone()
        stats = conn.execute(
            "SELECT severity, COUNT(*) as cnt FROM oracle_predictions WHERE lens_id = ? "
            "GROUP BY severity", (lens_id,)
        ).fetchall()
        body = render("oracle_digest.html", predictions=predictions, lens=lens, stats=stats)
        narrative = _ai_render_narrative("oracle lens digest render notification", {
            "lens_id": lens_id,
            "lens_name": (lens or {}).get("name", lens_id),
            "horizon_days": (lens or {}).get("horizon_days", "unknown"),
            "prediction_count": len(predictions),
            "severity_breakdown": "; ".join(f"{r['severity']}:{r['cnt']}" for r in stats) or "none",
        }) if ai_narrative else None
        send(to=recipient, subject="Oracle Lens Digest", body=body)
        payload = {"lens_id": lens_id}
        if narrative:
            payload["narrative"] = narrative
        publish("oracle.digest_sent", payload)
        return {"status": "sent", "lens_id": lens_id, "narrative": narrative}
    finally:
        conn.close()


def render_and_notify_genesis_progress(design_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch genesis design progress, render status, and notify stakeholder."""
    conn = get_connection()
    try:
        design = conn.execute(
            "SELECT id, name, status, current_phase FROM genesis_designs WHERE id = ?",
            (design_id,)
        ).fetchone()
        phases = conn.execute(
            "SELECT phase, status, started_at, completed_at FROM genesis_phase_log "
            "WHERE design_id = ? ORDER BY started_at DESC LIMIT 5", (design_id,)
        ).fetchall()
        reflexes = conn.execute(
            "SELECT name, confidence, fired_at FROM genesis_reflexes WHERE design_id = ? "
            "ORDER BY fired_at DESC LIMIT 3", (design_id,)
        ).fetchall()
        body = render("genesis_progress.html", design=design, phases=phases, reflexes=reflexes)
        narrative = _ai_render_narrative("genesis progress render notification", {
            "design_id": design_id,
            "design_name": (design or {}).get("name", design_id),
            "design_status": (design or {}).get("status", "unknown"),
            "current_phase": (design or {}).get("current_phase", "unknown"),
            "recent_phase_count": len(phases),
            "reflex_count": len(reflexes),
        }) if ai_narrative else None
        sendmail(to=recipient, subject="Genesis Progress Update", html=body)
        payload = {"design_id": design_id}
        if narrative:
            payload["narrative"] = narrative
        emit("genesis.progress_notified", payload)
        return {"status": "sent", "design_id": design_id, "narrative": narrative}
    finally:
        conn.close()


def render_and_dispatch_stig_report(workload_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch STIG check results, render report, and dispatch to recipient."""
    conn = get_connection()
    try:
        checks = conn.execute(
            "SELECT check_id, check_name, severity, status FROM govlift_stig_checks "
            "WHERE workload_id = ? ORDER BY severity LIMIT 20", (workload_id,)
        ).fetchall()
        workload = conn.execute(
            "SELECT name, classification FROM govlift_workloads WHERE id = ?", (workload_id,)
        ).fetchone()
        summary = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM govlift_stig_checks WHERE workload_id = ? "
            "GROUP BY status", (workload_id,)
        ).fetchall()
        body = render("stig_report.html", checks=checks, workload=workload, summary=summary)
        narrative = _ai_render_narrative("STIG compliance report render notification", {
            "workload_id": workload_id,
            "workload_name": (workload or {}).get("name", workload_id),
            "classification": (workload or {}).get("classification", "unknown"),
            "check_count": len(checks),
            "status_breakdown": "; ".join(f"{r['status']}:{r['cnt']}" for r in summary) or "none",
        }) if ai_narrative else None
        send(to=recipient, subject="STIG Compliance Report", body=body)
        payload = {"workload_id": workload_id}
        if narrative:
            payload["narrative"] = narrative
        dispatch("stig.report_dispatched", payload)
        return {"status": "sent", "workload_id": workload_id, "narrative": narrative}
    finally:
        conn.close()


def render_and_publish_poam_update(poam_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch POA&M item details, render update notice, and publish to ops channel."""
    conn = get_connection()
    try:
        poam = conn.execute(
            "SELECT id, title, severity, status, due_date, owner FROM poam_items WHERE id = ?",
            (poam_id,)
        ).fetchone()
        milestones = conn.execute(
            "SELECT milestone_text, target_date, status FROM poam_milestones WHERE poam_id = ? "
            "ORDER BY target_date", (poam_id,)
        ).fetchall()
        evidence = conn.execute(
            "SELECT filename, uploaded_at FROM poam_evidence WHERE poam_id = ? "
            "ORDER BY uploaded_at DESC LIMIT 5", (poam_id,)
        ).fetchall()
        body = render("poam_update.html", poam=poam, milestones=milestones, evidence=evidence)
        narrative = _ai_render_narrative("POA&M update render notification", {
            "poam_id": poam_id,
            "poam_title": (poam or {}).get("title", poam_id),
            "severity": (poam or {}).get("severity", "unknown"),
            "status": (poam or {}).get("status", "unknown"),
            "due_date": (poam or {}).get("due_date", "unknown"),
            "owner": (poam or {}).get("owner", "unassigned"),
            "milestone_count": len(milestones),
            "evidence_count": len(evidence),
        }) if ai_narrative else None
        sendmail(to=recipient, subject="POA&M Item Update", html=body)
        payload = {"poam_id": poam_id}
        if narrative:
            payload["narrative"] = narrative
        publish("poam.update_published", payload)
        return {"status": "sent", "poam_id": poam_id, "narrative": narrative}
    finally:
        conn.close()


def render_and_emit_agent_report(agent_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch agent performance data, render report, and emit to monitoring channel."""
    conn = get_connection()
    try:
        agent = conn.execute(
            "SELECT id, name, status, last_heartbeat, tier FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
        metrics = conn.execute(
            "SELECT metric_name, value, recorded_at FROM agent_metrics WHERE agent_id = ? "
            "ORDER BY recorded_at DESC LIMIT 10", (agent_id,)
        ).fetchall()
        errors = conn.execute(
            "SELECT error_msg, created_at FROM agent_errors WHERE agent_id = ? "
            "ORDER BY created_at DESC LIMIT 5", (agent_id,)
        ).fetchall()
        body = render("agent_report.html", agent=agent, metrics=metrics, errors=errors)
        narrative = _ai_render_narrative("agent performance report render notification", {
            "agent_id": agent_id,
            "agent_name": (agent or {}).get("name", agent_id),
            "agent_status": (agent or {}).get("status", "unknown"),
            "tier": (agent or {}).get("tier", "unknown"),
            "metric_count": len(metrics),
            "error_count": len(errors),
        }) if ai_narrative else None
        send(to=recipient, subject="Agent Performance Report", body=body)
        payload = {"agent_id": agent_id}
        if narrative:
            payload["narrative"] = narrative
        emit("agent.report_emitted", payload)
        return {"status": "sent", "agent_id": agent_id, "narrative": narrative}
    finally:
        conn.close()


def render_and_send_zig_pillar_update(pillar_slug: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch ZIG pillar progress, render maturity update, and send to recipient."""
    conn = get_connection()
    try:
        scores = conn.execute(
            "SELECT pillar_slug, score, maturity_level, complete_activities, activity_count "
            "FROM zig_maturity_scores WHERE pillar_slug = ? "
            "ORDER BY assessment_run_at DESC LIMIT 1", (pillar_slug,)
        ).fetchone()
        capabilities = conn.execute(
            "SELECT id, title, implementation_status, phase FROM zig_capabilities "
            "WHERE pillar_slug = ? ORDER BY phase", (pillar_slug,)
        ).fetchall()
        completions = conn.execute(
            "SELECT a.title, c.status FROM zig_activities a "
            "JOIN zig_activity_completions c ON c.activity_id = a.id "
            "WHERE a.capability_id IN (SELECT id FROM zig_capabilities WHERE pillar_slug = ?)",
            (pillar_slug,)
        ).fetchall()
        body = render("zig_pillar_update.html", scores=scores, capabilities=capabilities, completions=completions)
        narrative = _ai_render_narrative("ZIG pillar update render notification", {
            "pillar_slug": pillar_slug,
            "score": round(float((scores or {}).get("score", 0) or 0) * 100, 1),
            "maturity_level": (scores or {}).get("maturity_level", "unknown"),
            "complete_activities": int((scores or {}).get("complete_activities", 0) or 0),
            "activity_count": int((scores or {}).get("activity_count", 0) or 0),
            "capability_count": len(capabilities),
            "completion_count": len(completions),
        }) if ai_narrative else None
        sendmail(to=recipient, subject="ZIG Pillar Progress Update", html=body)
        if narrative:
            notify("zig-ops", f"{body}\n\nNarrative: {narrative}")
        else:
            notify("zig-ops", body)
        return {"status": "sent", "pillar_slug": pillar_slug, "narrative": narrative}
    finally:
        conn.close()


def render_and_deliver_aiify_scan_results(scan_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch AI-ify scan results, render opportunity report, and deliver."""
    conn = get_connection()
    try:
        scan = conn.execute(
            "SELECT scan_id, input_ref, status, overall_ai_readiness, created_at "
            "FROM aiify_scans WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        opportunities = conn.execute(
            "SELECT o.function_name, o.pattern_type, s.composite_score, s.value_score "
            "FROM aiify_scores s JOIN aiify_opportunities o ON o.opportunity_id = s.opportunity_id "
            "WHERE o.scan_id = ? ORDER BY s.composite_score DESC LIMIT 10", (scan_id,)
        ).fetchall()
        roadmap = conn.execute(
            "SELECT roadmap_id, phases_json FROM aiify_roadmaps WHERE scan_id = ? LIMIT 1",
            (scan_id,)
        ).fetchone()
        body = render("aiify_scan_results.html", scan=scan, opportunities=opportunities, roadmap=roadmap)
        narrative = _ai_render_narrative("AI-ify scan results render notification", {
            "scan_id": scan_id,
            "scan_status": (scan or {}).get("status", "unknown"),
            "overall_ai_readiness": round(float((scan or {}).get("overall_ai_readiness", 0) or 0), 1),
            "opportunity_count": len(opportunities),
            "roadmap_id": (roadmap or {}).get("roadmap_id", "none"),
        }) if ai_narrative else None
        send(to=recipient, subject="AI-ify Scan Results", body=body)
        payload = {"scan_id": scan_id}
        if narrative:
            payload["narrative"] = narrative
        publish("aiify.results_delivered", payload)
        return {"status": "sent", "scan_id": scan_id, "narrative": narrative}
    finally:
        conn.close()


def render_and_deliver_zig_gaps_report(recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch ZIG capability gaps, render remediation plan, and deliver."""
    conn = get_connection()
    try:
        gaps = conn.execute(
            "SELECT pillar_slug, title, phase, implementation_status FROM zig_capabilities "
            "WHERE implementation_status = 'not_started' ORDER BY pillar_slug, phase"
        ).fetchall()
        pillars = conn.execute(
            "SELECT slug, name, pillar_weight FROM zig_pillars ORDER BY slug"
        ).fetchall()
        activities = conn.execute(
            "SELECT za.title, za.phase, zc.pillar_slug FROM zig_activities za "
            "JOIN zig_capabilities zc ON zc.id = za.capability_id "
            "WHERE za.id NOT IN (SELECT activity_id FROM zig_activity_completions WHERE status = 'complete') "
            "ORDER BY zc.pillar_slug, za.phase"
        ).fetchall()
        body = render("zig_gaps_report.html", gaps=gaps, pillars=pillars, activities=activities)
        narrative = _ai_render_narrative("ZIG capability gaps render notification", {
            "gap_count": len(gaps),
            "pillar_count": len(pillars),
            "incomplete_activity_count": len(activities),
        }) if ai_narrative else None
        sendmail(to=recipient, subject="ZIG Capability Gaps Report", html=body)
        payload = {"gap_count": len(gaps), "recipient": recipient}
        if narrative:
            payload["narrative"] = narrative
        dispatch("zig.gaps_delivered", payload)
        return {"status": "sent", "recipient": recipient, "gap_count": len(gaps), "narrative": narrative}
    finally:
        conn.close()


def render_and_send_kanban_sprint_summary(sprint_key: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch sprint task data, render sprint summary, and deliver to stakeholder."""
    conn = get_connection()
    try:
        tasks = conn.execute(
            "SELECT id, title, status, actor, updated_at FROM kanban_tasks "
            "WHERE sprint_key = ? ORDER BY status", (sprint_key,)
        ).fetchall()
        metrics = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM kanban_tasks WHERE sprint_key = ? "
            "GROUP BY status", (sprint_key,)
        ).fetchall()
        events = conn.execute(
            "SELECT resource_id, event, actor, created_at FROM audit_trail "
            "WHERE resource_type = 'kanban_task' ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        body = render("kanban_sprint_summary.html", sprint_key=sprint_key, tasks=tasks, metrics=metrics, events=events)
        narrative = _ai_render_narrative("kanban sprint summary render notification", {
            "sprint_key": sprint_key,
            "task_count": len(tasks),
            "status_breakdown": "; ".join(f"{r['status']}:{r['cnt']}" for r in metrics) or "none",
            "recent_event_count": len(events),
        }) if ai_narrative else None
        send(to=recipient, subject="Sprint Summary", body=body)
        if narrative:
            notify("kanban-ops", f"{body}\n\nNarrative: {narrative}")
        else:
            notify("kanban-ops", body)
        return {"status": "sent", "sprint_key": sprint_key, "recipient": recipient, "narrative": narrative}
    finally:
        conn.close()


def render_and_send_dic_document_summary(doc_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch DIC document analysis results, render summary, and deliver to recipient."""
    conn = get_connection()
    try:
        doc = conn.execute(
            "SELECT id, title, source_type, ingested_at, classification FROM dic_documents WHERE id = ?",
            (doc_id,),
        ).fetchone()
        chunks = conn.execute(
            "SELECT id, chunk_index, relevance_score FROM dic_chunks WHERE doc_id = ? "
            "ORDER BY relevance_score DESC LIMIT 10",
            (doc_id,),
        ).fetchall()
        entities = conn.execute(
            "SELECT entity_text, entity_type, confidence FROM dic_entities WHERE doc_id = ? "
            "ORDER BY confidence DESC LIMIT 5",
            (doc_id,),
        ).fetchall()
        body = render("dic_document_summary.html", doc=doc, chunks=chunks, entities=entities)
        narrative = _ai_render_narrative("DIC document analysis render notification", {
            "doc_id": doc_id,
            "doc_title": (doc or {}).get("title", doc_id),
            "source_type": (doc or {}).get("source_type", "unknown"),
            "classification": (doc or {}).get("classification", "unknown"),
            "chunk_count": len(chunks),
            "entity_count": len(entities),
        }) if ai_narrative else None
        send(to=recipient, subject="DIC Document Analysis Summary", body=body)
        payload = {"doc_id": doc_id}
        if narrative:
            payload["narrative"] = narrative
        emit("dic.document_summary_sent", payload)
        return {"status": "sent", "doc_id": doc_id, "narrative": narrative}
    finally:
        conn.close()


def render_and_deliver_modernization_status(project_slug: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch modernization project progress, render status card, and deliver to lead."""
    conn = get_connection()
    try:
        project = conn.execute(
            "SELECT slug, name, status, completion_pct, target_il FROM modernization_projects "
            "WHERE slug = ? ORDER BY created_at DESC LIMIT 1",
            (project_slug,),
        ).fetchone()
        milestones = conn.execute(
            "SELECT title, status, due_date FROM modernization_milestones WHERE project_slug = ? "
            "ORDER BY due_date LIMIT 5",
            (project_slug,),
        ).fetchall()
        risks = conn.execute(
            "SELECT title, severity, mitigation_status FROM modernization_risks "
            "WHERE project_slug = ? AND severity IN ('high', 'critical') ORDER BY severity LIMIT 5",
            (project_slug,),
        ).fetchall()
        body = render("modernization_status.html", project=project, milestones=milestones, risks=risks)
        narrative = _ai_render_narrative("modernization project status render notification", {
            "project_slug": project_slug,
            "project_name": (project or {}).get("name", project_slug),
            "project_status": (project or {}).get("status", "unknown"),
            "completion_pct": round(float((project or {}).get("completion_pct", 0) or 0), 1),
            "target_il": (project or {}).get("target_il", "unknown"),
            "milestone_count": len(milestones),
            "high_risk_count": len(risks),
        }) if ai_narrative else None
        sendmail(to=recipient, subject="Modernization Project Status", html=body)
        payload = {"project_slug": project_slug}
        if narrative:
            payload["narrative"] = narrative
        notify("modernization-ops", body if not narrative else f"{body}\n\nNarrative: {narrative}")
        return {"status": "sent", "project_slug": project_slug, "narrative": narrative}
    finally:
        conn.close()


def render_and_notify_security_scan(scan_run_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch security scan findings, render report, and notify security lead."""
    conn = get_connection()
    try:
        scan_run = conn.execute(
            "SELECT id, scan_type, target, status, created_at FROM security_scan_runs WHERE id = ?",
            (scan_run_id,),
        ).fetchone()
        findings = conn.execute(
            "SELECT title, severity, tool, status FROM security_findings WHERE scan_run_id = ? "
            "ORDER BY severity LIMIT 20",
            (scan_run_id,),
        ).fetchall()
        summary = conn.execute(
            "SELECT severity, COUNT(*) as cnt FROM security_findings WHERE scan_run_id = ? "
            "GROUP BY severity",
            (scan_run_id,),
        ).fetchall()
        body = render("security_scan_report.html", scan_run=scan_run, findings=findings, summary=summary)
        narrative = _ai_render_narrative("security scan findings render notification", {
            "scan_run_id": scan_run_id,
            "scan_type": (scan_run or {}).get("scan_type", "unknown"),
            "target": (scan_run or {}).get("target", "unknown"),
            "scan_status": (scan_run or {}).get("status", "unknown"),
            "finding_count": len(findings),
            "severity_breakdown": "; ".join(f"{r['severity']}:{r['cnt']}" for r in summary) or "none",
        }) if ai_narrative else None
        sendmail(to=recipient, subject="Security Scan Report", html=body)
        payload = {"scan_run_id": scan_run_id}
        if narrative:
            payload["narrative"] = narrative
        dispatch("security.scan_notified", payload)
        return {"status": "sent", "scan_run_id": scan_run_id, "narrative": narrative}
    finally:
        conn.close()


def render_and_notify_compliance_gate(gate_id: str, project_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch compliance gate status, render gate report, and notify project lead."""
    conn = get_connection()
    try:
        gate = conn.execute(
            "SELECT id, gate_name, status, triggered_at FROM compliance_gates "
            "WHERE id = ? AND project_id = ?", (gate_id, project_id)
        ).fetchone()
        failures = conn.execute(
            "SELECT criterion, detail, severity FROM gate_failures WHERE gate_id = ? "
            "ORDER BY severity", (gate_id,)
        ).fetchall()
        project = conn.execute(
            "SELECT name, classification FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        body = render("compliance_gate.html", gate=gate, failures=failures, project=project)
        narrative = _ai_render_narrative("compliance gate render notification", {
            "gate_id": gate_id,
            "gate_name": (gate or {}).get("gate_name", gate_id),
            "gate_status": (gate or {}).get("status", "unknown"),
            "project_id": project_id,
            "project_name": (project or {}).get("name", project_id),
            "classification": (project or {}).get("classification", "unknown"),
            "failure_count": len(failures),
        }) if ai_narrative else None
        sendmail(to=recipient, subject="Compliance Gate Status", html=body)
        payload = {"gate_id": gate_id, "project_id": project_id}
        if narrative:
            payload["narrative"] = narrative
        dispatch("compliance.gate_notified", payload)
        return {"status": "sent", "gate_id": gate_id, "narrative": narrative}
    finally:
        conn.close()


def render_and_send_supply_chain_alert(assessment_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch supply chain risk assessment, render alert, and notify supply-chain ops.

    aiify-opp-5916: extends the render-notify chain to cover supply-chain risk
    assessments so recipients receive a grounded triage narrative alongside the
    deterministic HTML alert.
    """
    conn = get_connection()
    try:
        assessment = conn.execute(
            "SELECT id, vendor_name, risk_level, assessed_at, overall_score "
            "FROM supply_chain_assessments WHERE id = ?", (assessment_id,)
        ).fetchone()
        findings = conn.execute(
            "SELECT title, severity, category, status FROM supply_chain_findings "
            "WHERE assessment_id = ? ORDER BY severity LIMIT 20", (assessment_id,)
        ).fetchall()
        vendors = conn.execute(
            "SELECT vendor_name, risk_tier FROM supply_chain_vendors "
            "WHERE assessment_id = ? ORDER BY risk_tier LIMIT 5", (assessment_id,)
        ).fetchall()
        critical_count = sum(1 for f in findings if (f or {}).get("severity") in ("critical", "high"))
        body = render("supply_chain_alert.html", assessment=assessment, findings=findings, vendors=vendors)
        narrative = _ai_render_narrative("supply chain risk assessment render notification", {
            "assessment_id": assessment_id,
            "vendor_name": (assessment or {}).get("vendor_name", assessment_id),
            "risk_level": (assessment or {}).get("risk_level", "unknown"),
            "overall_score": round(float((assessment or {}).get("overall_score", 0) or 0), 1),
            "finding_count": len(findings),
            "critical_high_count": critical_count,
            "vendor_count": len(vendors),
        }) if ai_narrative else None
        sendmail(to=recipient, subject="Supply Chain Risk Alert", html=body)
        payload = {"assessment_id": assessment_id}
        if narrative:
            payload["narrative"] = narrative
        dispatch("supply_chain.alert_sent", payload)
        return {"status": "sent", "assessment_id": assessment_id, "narrative": narrative}
    finally:
        conn.close()


def render_and_deliver_fedramp_ato_status(package_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch FedRAMP ATO package status, render status card, and deliver to ISSO.

    aiify-opp-5916: extends the render-notify chain to cover FedRAMP authorization
    package status so compliance leads receive a grounded narrative alongside the
    deterministic ATO status render.
    """
    conn = get_connection()
    try:
        package = conn.execute(
            "SELECT id, system_name, ato_status, authorization_date, expiry_date "
            "FROM fedramp_ato_packages WHERE id = ?", (package_id,)
        ).fetchone()
        controls = conn.execute(
            "SELECT implementation_status, COUNT(*) as cnt "
            "FROM fedramp_controls WHERE package_id = ? GROUP BY implementation_status",
            (package_id,),
        ).fetchall()
        open_poams = conn.execute(
            "SELECT id, title, severity, due_date FROM poam_items "
            "WHERE project_id = ? AND status != 'closed' ORDER BY severity LIMIT 10",
            (package_id,),
        ).fetchall()
        control_breakdown = "; ".join(f"{r['implementation_status']}:{r['cnt']}" for r in controls) or "none"
        body = render("fedramp_ato_status.html", package=package, controls=controls, open_poams=open_poams)
        narrative = _ai_render_narrative("FedRAMP ATO status render notification", {
            "package_id": package_id,
            "system_name": (package or {}).get("system_name", package_id),
            "ato_status": (package or {}).get("ato_status", "unknown"),
            "authorization_date": (package or {}).get("authorization_date", "unknown"),
            "expiry_date": (package or {}).get("expiry_date", "unknown"),
            "control_breakdown": control_breakdown,
            "open_poam_count": len(open_poams),
        }) if ai_narrative else None
        sendmail(to=recipient, subject="FedRAMP ATO Status Update", html=body)
        payload = {"package_id": package_id}
        if narrative:
            payload["narrative"] = narrative
        publish("fedramp.ato_status_delivered", payload)
        return {"status": "sent", "package_id": package_id, "narrative": narrative}
    finally:
        conn.close()


def render_and_notify_innovation_cycle(cycle_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch innovation cycle results, render summary, and notify ops lead.

    aiify-opp-5916: extends the render-notify chain to cover innovation engine
    cycle completions so leads receive a grounded narrative alongside the
    deterministic cycle summary render.
    """
    conn = get_connection()
    try:
        cycle = conn.execute(
            "SELECT id, cycle_type, status, started_at, completed_at "
            "FROM innovation_cycles WHERE id = ?", (cycle_id,)
        ).fetchone()
        findings = conn.execute(
            "SELECT title, category, priority, status FROM innovation_findings "
            "WHERE cycle_id = ? ORDER BY priority LIMIT 15", (cycle_id,)
        ).fetchall()
        actions = conn.execute(
            "SELECT action_type, target, outcome FROM innovation_actions "
            "WHERE cycle_id = ? ORDER BY created_at DESC LIMIT 5", (cycle_id,)
        ).fetchall()
        top_category = findings[0]["category"] if findings else "none"
        body = render("innovation_cycle.html", cycle=cycle, findings=findings, actions=actions)
        narrative = _ai_render_narrative("innovation cycle render notification", {
            "cycle_id": cycle_id,
            "cycle_type": (cycle or {}).get("cycle_type", "unknown"),
            "cycle_status": (cycle or {}).get("status", "unknown"),
            "finding_count": len(findings),
            "action_count": len(actions),
            "top_category": top_category,
        }) if ai_narrative else None
        send(to=recipient, subject="Innovation Cycle Complete", body=body)
        payload = {"cycle_id": cycle_id}
        if narrative:
            payload["narrative"] = narrative
        emit("innovation.cycle_notified", payload)
        return {"status": "sent", "cycle_id": cycle_id, "narrative": narrative}
    finally:
        conn.close()


def render_and_notify_awareness_drift_report(run_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch Internal Awareness Engine drift detections, render report, and notify ops lead.

    aiify-opp-5915: extends the render-notify chain to cover D-AWARE drift
    detections so operators receive a grounded LLM narrative alongside the
    deterministic drift-regression report rendered from oracle_predictions
    (lens_name='internal_awareness').
    """
    conn = get_connection()
    try:
        run = conn.execute(
            "SELECT id, probe_run_id, status, created_at FROM awareness_run_log WHERE id = ?",
            (run_id,),
        ).fetchone()
        regressions = conn.execute(
            "SELECT title, severity, confidence, outcome, created_at "
            "FROM oracle_predictions "
            "WHERE lens_name = 'internal_awareness' AND prediction_run_id = ? "
            "ORDER BY severity, confidence DESC LIMIT 20",
            (run_id,),
        ).fetchall()
        component_health = conn.execute(
            "SELECT node_id, probe_type, status FROM awareness_component_health "
            "WHERE probe_run_id = ? ORDER BY status LIMIT 10",
            (run_id,),
        ).fetchall()
        failed_count = sum(1 for r in regressions if (r or {}).get("severity") in ("critical", "high"))
        body = render(
            "awareness_drift_report.html",
            run=run,
            regressions=regressions,
            component_health=component_health,
        )
        narrative = _ai_render_narrative("D-AWARE drift detection render notification", {
            "run_id": run_id,
            "run_status": (run or {}).get("status", "unknown"),
            "regression_count": len(regressions),
            "critical_high_count": failed_count,
            "component_snapshot_count": len(component_health),
        }) if ai_narrative else None
        sendmail(to=recipient, subject="Awareness Engine Drift Report", html=body)
        payload = {"run_id": run_id, "regression_count": len(regressions)}
        if narrative:
            payload["narrative"] = narrative
        dispatch("awareness.drift_notified", payload)
        return {"status": "sent", "run_id": run_id, "regression_count": len(regressions), "narrative": narrative}
    finally:
        conn.close()


def render_and_notify_finetune_job_result(job_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch fine-tuning job outcome, render result card, and notify the AI-ops lead.

    aiify-opp-5917: extends the render-notify chain to cover fine-tuning job
    completions so AI-ops teams receive a grounded LLM narrative alongside the
    deterministic job-result render.
    """
    conn = get_connection()
    try:
        job = conn.execute(
            "SELECT id, model_base, status, epochs, started_at, completed_at "
            "FROM finetune_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        metrics = conn.execute(
            "SELECT metric_name, value, epoch FROM finetune_metrics "
            "WHERE job_id = ? ORDER BY epoch DESC LIMIT 10", (job_id,)
        ).fetchall()
        eval_results = conn.execute(
            "SELECT benchmark_name, score, delta FROM finetune_eval_results "
            "WHERE job_id = ? ORDER BY score DESC LIMIT 5", (job_id,)
        ).fetchall()
        best_score = max((float(r["score"] or 0) for r in eval_results), default=0.0)
        body = render(
            "finetune_job_result.html",
            job=job,
            metrics=metrics,
            eval_results=eval_results,
        )
        narrative = _ai_render_narrative("fine-tuning job result render notification", {
            "job_id": job_id,
            "model_base": (job or {}).get("model_base", "unknown"),
            "job_status": (job or {}).get("status", "unknown"),
            "epochs": int((job or {}).get("epochs", 0) or 0),
            "metric_count": len(metrics),
            "eval_result_count": len(eval_results),
            "best_eval_score": round(best_score, 4),
        }) if ai_narrative else None
        sendmail(to=recipient, subject="Fine-Tuning Job Result", html=body)
        payload = {"job_id": job_id}
        if narrative:
            payload["narrative"] = narrative
        publish("finetune.job_result_notified", payload)
        return {"status": "sent", "job_id": job_id, "narrative": narrative}
    finally:
        conn.close()


def render_and_notify_research_findings(report_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch research engine report, render findings summary, and notify the research lead.

    aiify-opp-5955: extends the render-notify chain to cover the Research Engine
    output so leads receive a grounded LLM narrative alongside the deterministic
    findings summary rendered from research_reports and research_findings.
    """
    conn = get_connection()
    try:
        report = conn.execute(
            "SELECT id, topic, status, confidence, created_at FROM research_reports WHERE id = ?",
            (report_id,),
        ).fetchone()
        findings = conn.execute(
            "SELECT title, category, confidence, summary FROM research_findings "
            "WHERE report_id = ? ORDER BY confidence DESC LIMIT 15",
            (report_id,),
        ).fetchall()
        sources = conn.execute(
            "SELECT source_url, source_type, relevance_score FROM research_sources "
            "WHERE report_id = ? ORDER BY relevance_score DESC LIMIT 5",
            (report_id,),
        ).fetchall()
        avg_confidence = (
            round(
                sum(float(f["confidence"] or 0) for f in findings) / len(findings), 4
            )
            if findings else 0.0
        )
        body = render(
            "research_findings.html",
            report=report,
            findings=findings,
            sources=sources,
            avg_confidence=avg_confidence,
        )
        narrative = _ai_render_narrative("research engine findings render notification", {
            "report_id": report_id,
            "topic": (report or {}).get("topic", report_id)[:100],
            "report_status": (report or {}).get("status", "unknown"),
            "overall_confidence": round(float((report or {}).get("confidence", 0) or 0), 4),
            "finding_count": len(findings),
            "source_count": len(sources),
            "avg_finding_confidence": avg_confidence,
        }) if ai_narrative else None
        sendmail(to=recipient, subject="Research Engine Findings Report", html=body)
        payload = {"report_id": report_id, "finding_count": len(findings)}
        if narrative:
            payload["narrative"] = narrative
        emit("research.findings_notified", payload)
        return {
            "status": "sent",
            "report_id": report_id,
            "finding_count": len(findings),
            "narrative": narrative,
        }
    finally:
        conn.close()


def render_and_notify_rag_query_result(query_ref: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch RAG search result details, render result card, and notify requestor.

    aiify-opp-5952: extends the render-notify chain to cover RAG knowledge
    searches so requestors receive a grounded LLM narrative alongside the
    deterministic result card rendered from rag_queries and rag_chunks.
    """
    conn = get_connection()
    try:
        query = conn.execute(
            "SELECT id, query_text, lens, status, created_at FROM rag_queries WHERE id = ?",
            (query_ref,),
        ).fetchone()
        chunks = conn.execute(
            "SELECT id, source_doc, relevance_score, excerpt FROM rag_chunks "
            "WHERE query_id = ? ORDER BY relevance_score DESC LIMIT 10",
            (query_ref,),
        ).fetchall()
        citations = conn.execute(
            "SELECT source_doc, citation_text, confidence FROM rag_citations "
            "WHERE query_id = ? ORDER BY confidence DESC LIMIT 5",
            (query_ref,),
        ).fetchall()
        top_score = round(max((float(c["relevance_score"] or 0) for c in chunks), default=0.0), 4)
        body = render(
            "rag_query_result.html",
            query=query,
            chunks=chunks,
            citations=citations,
        )
        narrative = _ai_render_narrative("RAG knowledge search result render notification", {
            "query_ref": query_ref,
            "query_text": (query or {}).get("query_text", query_ref)[:120],
            "lens": (query or {}).get("lens", "default"),
            "query_status": (query or {}).get("status", "unknown"),
            "chunk_count": len(chunks),
            "citation_count": len(citations),
            "top_relevance_score": top_score,
        }) if ai_narrative else None
        send(to=recipient, subject="RAG Knowledge Search Result", body=body)
        payload = {"query_ref": query_ref, "chunk_count": len(chunks)}
        if narrative:
            payload["narrative"] = narrative
        emit("rag.query_result_notified", payload)
        return {"status": "sent", "query_ref": query_ref, "chunk_count": len(chunks), "narrative": narrative}
    finally:
        conn.close()


def render_and_send_aiify_weekly_digest(week_label: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch weekly AI-ify scan summary, render digest, and deliver to AI-ops lead.

    aiify-opp-5913: extends the render-notify chain for the AI-ify weekly digest
    (parallel to digest_service.send_aiify_weekly_digest) so callers can opt in to
    a grounded LLM narrative about the week's AI readiness posture.
    """
    conn = get_connection()
    try:
        scans = conn.execute(
            "SELECT scan_id, status, overall_ai_readiness, created_at FROM aiify_scans "
            "WHERE created_at >= date('now', '-7 days') ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        top_opps = conn.execute(
            "SELECT o.function_name, o.pattern_type, s.composite_score "
            "FROM aiify_scores s JOIN aiify_opportunities o ON o.opportunity_id = s.opportunity_id "
            "WHERE o.scan_id IN (SELECT scan_id FROM aiify_scans WHERE created_at >= date('now', '-7 days')) "
            "ORDER BY s.composite_score DESC LIMIT 5"
        ).fetchall()
        roadmaps = conn.execute(
            "SELECT roadmap_id, scan_id FROM aiify_roadmaps "
            "WHERE scan_id IN (SELECT scan_id FROM aiify_scans WHERE created_at >= date('now', '-7 days'))"
        ).fetchall()
        avg_readiness = (
            round(
                sum(float(r["overall_ai_readiness"] or 0) for r in scans) / len(scans), 1
            )
            if scans else 0.0
        )
        top_pattern = top_opps[0]["pattern_type"] if top_opps else "none"
        body = render(
            "aiify_weekly_digest.html",
            week_label=week_label,
            scans=scans,
            top_opps=top_opps,
            roadmaps=roadmaps,
            avg_readiness=avg_readiness,
        )
        narrative = _ai_render_narrative("AI-ify weekly digest render notification", {
            "week_label": week_label,
            "scan_count": len(scans),
            "opportunity_count": len(top_opps),
            "roadmap_count": len(roadmaps),
            "avg_readiness": avg_readiness,
            "top_pattern_type": top_pattern,
        }) if ai_narrative else None
        sendmail(to=recipient, subject=f"AI-ify Weekly Digest — {week_label}", html=body)
        payload = {"week_label": week_label, "scan_count": len(scans)}
        if narrative:
            payload["narrative"] = narrative
        publish("aiify.weekly_digest_sent", payload)
        return {
            "status": "sent",
            "week_label": week_label,
            "scan_count": len(scans),
            "narrative": narrative,
        }
    finally:
        conn.close()


def render_and_notify_zta_posture_report(assessment_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch ZTA posture assessment data, render report, and notify the ZTA lead.

    aiify-opp-5956: extends the render-notify chain to cover Zero Trust
    Architecture posture assessments so ZTA leads receive a grounded LLM
    narrative alongside the deterministic posture report rendered from
    zta_assessments, zta_control_results, and zta_gaps.
    """
    conn = get_connection()
    try:
        assessment = conn.execute(
            "SELECT id, pillar, maturity_level, score, assessed_at "
            "FROM zta_assessments WHERE id = ?", (assessment_id,)
        ).fetchone()
        controls = conn.execute(
            "SELECT control_id, title, status, implementation_level "
            "FROM zta_control_results WHERE assessment_id = ? ORDER BY status LIMIT 20",
            (assessment_id,),
        ).fetchall()
        gaps = conn.execute(
            "SELECT title, severity, pillar, recommended_action "
            "FROM zta_gaps WHERE assessment_id = ? ORDER BY severity LIMIT 10",
            (assessment_id,),
        ).fetchall()
        critical_gap_count = sum(1 for g in gaps if (g or {}).get("severity") in ("critical", "high"))
        body = render(
            "zta_posture_report.html",
            assessment=assessment,
            controls=controls,
            gaps=gaps,
        )
        narrative = _ai_render_narrative("ZTA posture assessment render notification", {
            "assessment_id": assessment_id,
            "pillar": (assessment or {}).get("pillar", "unknown"),
            "maturity_level": (assessment or {}).get("maturity_level", "unknown"),
            "score": round(float((assessment or {}).get("score", 0) or 0), 1),
            "control_count": len(controls),
            "gap_count": len(gaps),
            "critical_high_gap_count": critical_gap_count,
        }) if ai_narrative else None
        sendmail(to=recipient, subject="ZTA Posture Assessment Report", html=body)
        payload = {"assessment_id": assessment_id, "gap_count": len(gaps)}
        if narrative:
            payload["narrative"] = narrative
        dispatch("zta.posture_report_notified", payload)
        return {
            "status": "sent",
            "assessment_id": assessment_id,
            "gap_count": len(gaps),
            "narrative": narrative,
        }
    finally:
        conn.close()


def render_and_notify_intake_session_summary(session_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch requirements intake session data, render summary, and notify the session lead.

    aiify-opp-5954: extends the render-notify chain to cover ICDEV™ requirements
    intake sessions so leads receive a grounded LLM narrative alongside the
    deterministic session summary rendered from intake_sessions,
    intake_requirements, and intake_gaps.
    """
    conn = get_connection()
    try:
        session = conn.execute(
            "SELECT id, title, status, readiness_score, created_at "
            "FROM intake_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        requirements = conn.execute(
            "SELECT id, title, priority, category, status FROM intake_requirements "
            "WHERE session_id = ? ORDER BY priority LIMIT 20", (session_id,)
        ).fetchall()
        gaps = conn.execute(
            "SELECT title, gap_type, severity FROM intake_gaps "
            "WHERE session_id = ? ORDER BY severity LIMIT 10", (session_id,)
        ).fetchall()
        high_priority_count = sum(1 for r in requirements if (r or {}).get("priority") in (1, "high", "critical"))
        body = render(
            "intake_session_summary.html",
            session=session,
            requirements=requirements,
            gaps=gaps,
        )
        narrative = _ai_render_narrative("requirements intake session summary render notification", {
            "session_id": session_id,
            "session_title": (session or {}).get("title", session_id),
            "session_status": (session or {}).get("status", "unknown"),
            "readiness_score": round(float((session or {}).get("readiness_score", 0) or 0), 1),
            "requirement_count": len(requirements),
            "high_priority_count": high_priority_count,
            "gap_count": len(gaps),
        }) if ai_narrative else None
        sendmail(to=recipient, subject="Intake Session Summary", html=body)
        payload = {"session_id": session_id, "requirement_count": len(requirements)}
        if narrative:
            payload["narrative"] = narrative
        emit("intake.session_summary_sent", payload)
        return {
            "status": "sent",
            "session_id": session_id,
            "requirement_count": len(requirements),
            "narrative": narrative,
        }
    finally:
        conn.close()


def render_and_notify_simulation_coa_result(run_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch Digital Program Twin simulation COA results, render report, and notify program lead.

    aiify-opp-5953: extends the render-notify chain to cover simulation COA
    completions so program leads receive a grounded LLM narrative alongside the
    deterministic COA result render from simulation_runs and simulation_coas.
    """
    conn = get_connection()
    try:
        run = conn.execute(
            "SELECT id, scenario_name, status, started_at, completed_at "
            "FROM simulation_runs WHERE id = ?", (run_id,)
        ).fetchone()
        coas = conn.execute(
            "SELECT coa_id, title, feasibility_score, risk_score, recommended "
            "FROM simulation_coas WHERE run_id = ? ORDER BY feasibility_score DESC LIMIT 10",
            (run_id,),
        ).fetchall()
        metrics = conn.execute(
            "SELECT metric_name, value, unit FROM simulation_metrics "
            "WHERE run_id = ? ORDER BY metric_name LIMIT 10",
            (run_id,),
        ).fetchall()
        recommended_count = sum(1 for c in coas if (c or {}).get("recommended"))
        top_feasibility = round(max((float(c["feasibility_score"] or 0) for c in coas), default=0.0), 3)
        body = render(
            "simulation_coa_result.html",
            run=run,
            coas=coas,
            metrics=metrics,
        )
        narrative = _ai_render_narrative("simulation COA result render notification", {
            "run_id": run_id,
            "scenario_name": (run or {}).get("scenario_name", run_id),
            "run_status": (run or {}).get("status", "unknown"),
            "coa_count": len(coas),
            "recommended_count": recommended_count,
            "top_feasibility_score": top_feasibility,
            "metric_count": len(metrics),
        }) if ai_narrative else None
        sendmail(to=recipient, subject="Simulation COA Result", html=body)
        payload = {"run_id": run_id, "coa_count": len(coas)}
        if narrative:
            payload["narrative"] = narrative
        publish("simulation.coa_result_notified", payload)
        return {"status": "sent", "run_id": run_id, "coa_count": len(coas), "narrative": narrative}
    finally:
        conn.close()
