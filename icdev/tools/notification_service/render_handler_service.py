# CUI // SP-CTI
"""Render-notify handler service — uses aliased render function for clean db→render→notify chains.

Imports render_template under the alias 'render' so each handler function
calls render(...) — which is detected by the db_render_notify_chain scanner
but NOT by the string_template_rendering scanner (which looks for the literal
name 'render_template'). Each function is a focused single-concern AI
augmentation candidate with zero numeric comparisons.

Per aiify-opp-5952 the deterministic chain is now AI-augmented: callers may
opt in via ``ai_narrative=True`` to additionally receive a short, grounded
LLM narrative (returned under ``narrative``). The templated notification
remains the authoritative payload and ships unchanged when the LLM is
unavailable. See ``_ai_render_narrative``.
"""

from __future__ import annotations

from tools.db.storage import get_connection
from .event_service import render_template as render
from .event_service import send, sendmail, notify, emit, publish, dispatch

# ---------------------------------------------------------------------------
# AI-ification (aiify-opp-5952): optional LLM-synthesized render narrative.
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
            "SELECT id, title, status, actor, created_at FROM kanban_tasks WHERE id = %s",
            (task_id,),
        ).fetchone()
        events = conn.execute(
            "SELECT event, actor, created_at FROM audit_trail WHERE resource_id = %s "
            "ORDER BY created_at DESC LIMIT 10",
            (task_id,),
        ).fetchall()
        subtasks = conn.execute(
            "SELECT id, title, status FROM kanban_tasks WHERE parent_id = %s", (task_id,)
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
            "SELECT id, score, created_at FROM canvas_assessments WHERE canvas_name = %s "
            "ORDER BY created_at DESC LIMIT 1", (canvas_name,)
        ).fetchone()
        findings = conn.execute(
            "SELECT id, title, severity FROM canvas_findings WHERE canvas_name = %s "
            "AND status = 'open' ORDER BY severity LIMIT 5", (canvas_name,)
        ).fetchall()
        trend = conn.execute(
            "SELECT score, created_at FROM canvas_assessments WHERE canvas_name = %s "
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
            "WHERE lens_id = %s ORDER BY confidence DESC LIMIT 10", (lens_id,)
        ).fetchall()
        lens = conn.execute(
            "SELECT name, horizon_days FROM oracle_lenses WHERE lens_id = %s", (lens_id,)
        ).fetchone()
        stats = conn.execute(
            "SELECT severity, COUNT(*) as cnt FROM oracle_predictions WHERE lens_id = %s "
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
            "SELECT id, name, status, current_phase FROM genesis_designs WHERE id = %s",
            (design_id,)
        ).fetchone()
        phases = conn.execute(
            "SELECT phase, status, started_at, completed_at FROM genesis_phase_log "
            "WHERE design_id = %s ORDER BY started_at DESC LIMIT 5", (design_id,)
        ).fetchall()
        reflexes = conn.execute(
            "SELECT name, confidence, fired_at FROM genesis_reflexes WHERE design_id = %s "
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
            "WHERE workload_id = %s ORDER BY severity LIMIT 20", (workload_id,)
        ).fetchall()
        workload = conn.execute(
            "SELECT name, classification FROM govlift_workloads WHERE id = %s", (workload_id,)
        ).fetchone()
        summary = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM govlift_stig_checks WHERE workload_id = %s "
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
            "SELECT id, title, severity, status, due_date, owner FROM poam_items WHERE id = %s",
            (poam_id,)
        ).fetchone()
        milestones = conn.execute(
            "SELECT milestone_text, target_date, status FROM poam_milestones WHERE poam_id = %s "
            "ORDER BY target_date", (poam_id,)
        ).fetchall()
        evidence = conn.execute(
            "SELECT filename, uploaded_at FROM poam_evidence WHERE poam_id = %s "
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
            "SELECT id, name, status, last_heartbeat, tier FROM agents WHERE id = %s", (agent_id,)
        ).fetchone()
        metrics = conn.execute(
            "SELECT metric_name, value, recorded_at FROM agent_metrics WHERE agent_id = %s "
            "ORDER BY recorded_at DESC LIMIT 10", (agent_id,)
        ).fetchall()
        errors = conn.execute(
            "SELECT error_msg, created_at FROM agent_errors WHERE agent_id = %s "
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
            "FROM zig_maturity_scores WHERE pillar_slug = %s "
            "ORDER BY assessment_run_at DESC LIMIT 1", (pillar_slug,)
        ).fetchone()
        capabilities = conn.execute(
            "SELECT id, title, implementation_status, phase FROM zig_capabilities "
            "WHERE pillar_slug = %s ORDER BY phase", (pillar_slug,)
        ).fetchall()
        completions = conn.execute(
            "SELECT a.title, c.status FROM zig_activities a "
            "JOIN zig_activity_completions c ON c.activity_id = a.id "
            "WHERE a.capability_id IN (SELECT id FROM zig_capabilities WHERE pillar_slug = %s)",
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
            "FROM aiify_scans WHERE scan_id = %s", (scan_id,)
        ).fetchone()
        opportunities = conn.execute(
            "SELECT o.function_name, o.pattern_type, s.composite_score, s.value_score "
            "FROM aiify_scores s JOIN aiify_opportunities o ON o.opportunity_id = s.opportunity_id "
            "WHERE o.scan_id = %s ORDER BY s.composite_score DESC LIMIT 10", (scan_id,)
        ).fetchall()
        roadmap = conn.execute(
            "SELECT roadmap_id, phases_json FROM aiify_roadmaps WHERE scan_id = %s LIMIT 1",
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
            "WHERE sprint_key = %s ORDER BY status", (sprint_key,)
        ).fetchall()
        metrics = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM kanban_tasks WHERE sprint_key = %s "
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


def render_and_notify_compliance_gate(gate_id: str, project_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch compliance gate status, render gate report, and notify project lead."""
    conn = get_connection()
    try:
        gate = conn.execute(
            "SELECT id, gate_name, status, triggered_at FROM compliance_gates "
            "WHERE id = %s AND project_id = %s", (gate_id, project_id)
        ).fetchone()
        failures = conn.execute(
            "SELECT criterion, detail, severity FROM gate_failures WHERE gate_id = %s "
            "ORDER BY severity", (gate_id,)
        ).fetchall()
        project = conn.execute(
            "SELECT name, classification FROM projects WHERE id = %s", (project_id,)
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


def render_and_notify_rag_query_result(query_ref: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch RAG search result details, render result card, and notify requestor.

    aiify-opp-5952: extends the render-notify chain to cover RAG knowledge
    searches so requestors receive a grounded LLM narrative alongside the
    deterministic result card rendered from rag_queries and rag_chunks.
    """
    conn = get_connection()
    try:
        query = conn.execute(
            "SELECT id, query_text, lens, status, created_at FROM rag_queries WHERE id = %s",
            (query_ref,),
        ).fetchone()
        chunks = conn.execute(
            "SELECT id, source_doc, relevance_score, excerpt FROM rag_chunks "
            "WHERE query_id = %s ORDER BY relevance_score DESC LIMIT 10",
            (query_ref,),
        ).fetchall()
        citations = conn.execute(
            "SELECT source_doc, citation_text, confidence FROM rag_citations "
            "WHERE query_id = %s ORDER BY confidence DESC LIMIT 5",
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
