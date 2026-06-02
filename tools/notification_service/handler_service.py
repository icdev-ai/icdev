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


def handle_aiify_opportunity_handler(
    opportunity_id: int, scan_id: str, recipient: str, ai_narrative: bool = False
) -> dict:
    """Fetch AI-ify opportunity details and deliver triage notification.

    Per aiify-opp-5907: adds a db→render→notify chain for individual AI-ify
    opportunities so developers receive a grounded narrative describing which
    pattern was found, its composite score, and the recommended next action.
    """
    conn = get_connection()
    try:
        opportunity = conn.execute(
            "SELECT opportunity_id, function_name, pattern_type, module_path "
            "FROM aiify_opportunities WHERE opportunity_id = ? AND scan_id = ?",
            (opportunity_id, scan_id),
        ).fetchone()
        scores = conn.execute(
            "SELECT composite_score, value_score, feasibility_score, risk_score "
            "FROM aiify_scores WHERE opportunity_id = ?", (opportunity_id,)
        ).fetchone()
        roadmap = conn.execute(
            "SELECT roadmap_id, phase FROM aiify_roadmaps r "
            "JOIN aiify_roadmap_items ri ON ri.roadmap_id = r.roadmap_id "
            "WHERE ri.opportunity_id = ? LIMIT 1", (opportunity_id,)
        ).fetchone()
        rendered = render_template(
            "handlers/aiify_opportunity.html",
            opportunity=opportunity, scores=scores, roadmap=roadmap
        )
        narrative = _ai_handler_narrative("AI-ify opportunity triage notification", {
            "opportunity_id": opportunity_id,
            "function_name": (opportunity or {}).get("function_name", "unknown"),
            "pattern_type": (opportunity or {}).get("pattern_type", "unknown"),
            "module_path": (opportunity or {}).get("module_path", "unknown"),
            "composite_score": round(float((scores or {}).get("composite_score", 0) or 0), 3),
            "value_score": round(float((scores or {}).get("value_score", 0) or 0), 3),
            "feasibility_score": round(float((scores or {}).get("feasibility_score", 0) or 0), 3),
            "risk_score": round(float((scores or {}).get("risk_score", 0) or 0), 3),
            "roadmap_id": (roadmap or {}).get("roadmap_id", "none"),
            "phase": (roadmap or {}).get("phase", "unscheduled"),
        }) if ai_narrative else None
        send(to=recipient, subject="AI-ify Opportunity Detected", body=rendered)
        payload = {"opportunity_id": opportunity_id, "scan_id": scan_id}
        if narrative:
            payload["narrative"] = narrative
        emit("aiify.opportunity_notified", payload)
        return {"status": "sent", "opportunity_id": opportunity_id, "narrative": narrative}
    finally:
        conn.close()


def handle_aiify_scan_complete_handler(
    scan_id: str, roadmap_id: str, recipient: str, ai_narrative: bool = False
) -> dict:
    """Fetch AI-ify scan results and deliver completion summary notification.

    Per aiify-opp-5946: adds a db→render→notify chain for completed AI-ify
    scans so module owners and tech leads receive a grounded narrative
    describing scan scope, opportunity count, score distribution, and
    roadmap alignment — enabling rapid triage of Phase 1 Quick Win items.
    """
    conn = get_connection()
    try:
        scan = conn.execute(
            "SELECT scan_id, input_ref, total_files, total_loc, status, "
            "overall_verdict, overall_ai_readiness, completed_at "
            "FROM aiify_scans WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        top_opps = conn.execute(
            "SELECT o.opportunity_id, o.function_name, o.pattern_type, s.composite_score "
            "FROM aiify_opportunities o "
            "LEFT JOIN aiify_scores s ON s.opportunity_id = o.opportunity_id "
            "WHERE o.scan_id = ? ORDER BY s.composite_score DESC LIMIT 5", (scan_id,)
        ).fetchall()
        roadmap = conn.execute(
            "SELECT roadmap_id, title, total_effort_days "
            "FROM aiify_roadmaps WHERE roadmap_id = ?", (roadmap_id,)
        ).fetchone()
        score_summary = conn.execute(
            "SELECT COUNT(*) as opp_count, "
            "MIN(s.composite_score) as min_score, MAX(s.composite_score) as max_score, "
            "AVG(s.composite_score) as avg_score "
            "FROM aiify_scores s "
            "JOIN aiify_opportunities o ON o.opportunity_id = s.opportunity_id "
            "WHERE o.scan_id = ?", (scan_id,)
        ).fetchone()
        rendered = render_template(
            "handlers/aiify_scan_complete.html",
            scan=scan, top_opps=top_opps, roadmap=roadmap, score_summary=score_summary,
        )
        opp_count = int((score_summary or {}).get("opp_count", 0) or 0)
        narrative = _ai_handler_narrative("AI-ify scan completion notification", {
            "scan_id": scan_id,
            "roadmap_id": roadmap_id,
            "input_ref": (scan or {}).get("input_ref", "unknown"),
            "total_files": int((scan or {}).get("total_files", 0) or 0),
            "scan_status": (scan or {}).get("status", "unknown"),
            "overall_verdict": (scan or {}).get("overall_verdict", "unknown"),
            "overall_ai_readiness": (scan or {}).get("overall_ai_readiness", "unknown"),
            "opportunity_count": opp_count,
            "top_opportunity_count": len(top_opps),
            "min_composite_score": round(float((score_summary or {}).get("min_score", 0) or 0), 3),
            "max_composite_score": round(float((score_summary or {}).get("max_score", 0) or 0), 3),
            "avg_composite_score": round(float((score_summary or {}).get("avg_score", 0) or 0), 3),
        }) if ai_narrative else None
        sendmail(to=recipient, subject="AI-ify Scan Complete", html=rendered)
        payload = {"scan_id": scan_id, "roadmap_id": roadmap_id, "opportunity_count": opp_count}
        if narrative:
            payload["narrative"] = narrative
        emit("aiify.scan_complete", payload)
        return {"status": "sent", "scan_id": scan_id, "roadmap_id": roadmap_id, "narrative": narrative}
    finally:
        conn.close()


def handle_aiify_roadmap_handler(
    roadmap_id: str, scan_id: str, recipient: str, ai_narrative: bool = False
) -> dict:
    """Fetch AI-ify roadmap details and deliver phase progress notification.

    Per aiify-opp-5942: adds a db→render→notify chain for AI-ify roadmaps so
    tech leads receive a grounded narrative describing roadmap scope, phase
    breakdown, effort estimate, and top-priority opportunities — enabling rapid
    sprint planning against Phase 1 Quick Win items.
    """
    conn = get_connection()
    try:
        roadmap = conn.execute(
            "SELECT roadmap_id, title, total_effort_days, generated_at "
            "FROM aiify_roadmaps WHERE roadmap_id = ?", (roadmap_id,)
        ).fetchone()
        phase_summary = conn.execute(
            "SELECT ri.phase, COUNT(*) as opp_count, "
            "AVG(s.composite_score) as avg_score "
            "FROM aiify_roadmap_items ri "
            "LEFT JOIN aiify_scores s ON s.opportunity_id = ri.opportunity_id "
            "WHERE ri.roadmap_id = ? GROUP BY ri.phase", (roadmap_id,)
        ).fetchall()
        top_opps = conn.execute(
            "SELECT o.opportunity_id, o.function_name, o.pattern_type, s.composite_score "
            "FROM aiify_roadmap_items ri "
            "JOIN aiify_opportunities o ON o.opportunity_id = ri.opportunity_id "
            "LEFT JOIN aiify_scores s ON s.opportunity_id = ri.opportunity_id "
            "WHERE ri.roadmap_id = ? AND ri.phase LIKE '%Quick Win%' "
            "ORDER BY s.composite_score DESC LIMIT 5", (roadmap_id,)
        ).fetchall()
        scan = conn.execute(
            "SELECT total_files, overall_verdict, overall_ai_readiness "
            "FROM aiify_scans WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        rendered = render_template(
            "handlers/aiify_roadmap.html",
            roadmap=roadmap, phase_summary=phase_summary,
            top_opps=top_opps, scan=scan,
        )
        total_opps = sum(int((r or {}).get("opp_count", 0) or 0) for r in phase_summary)
        narrative = _ai_handler_narrative("AI-ify roadmap phase progress notification", {
            "roadmap_id": roadmap_id,
            "scan_id": scan_id,
            "roadmap_title": (roadmap or {}).get("title", roadmap_id),
            "total_effort_days": int((roadmap or {}).get("total_effort_days", 0) or 0),
            "phase_count": len(phase_summary),
            "total_opportunities": total_opps,
            "quick_win_count": len(top_opps),
            "overall_verdict": (scan or {}).get("overall_verdict", "unknown"),
            "overall_ai_readiness": (scan or {}).get("overall_ai_readiness", "unknown"),
            "total_files": int((scan or {}).get("total_files", 0) or 0),
        }) if ai_narrative else None
        sendmail(to=recipient, subject="AI-ify Roadmap Ready", html=rendered)
        payload = {"roadmap_id": roadmap_id, "scan_id": scan_id, "total_opportunities": total_opps}
        if narrative:
            payload["narrative"] = narrative
        emit("aiify.roadmap_notified", payload)
        return {"status": "sent", "roadmap_id": roadmap_id, "scan_id": scan_id, "narrative": narrative}
    finally:
        conn.close()


def handle_cmmc_assessment_handler(
    assessment_id: str, system_id: str, recipient: str, ai_narrative: bool = False
) -> dict:
    """Fetch CMMC assessment results and deliver compliance notification.

    Per aiify-opp-5905: adds a db→render→notify chain for CMMC Level 2/3
    assessments so authorizing officials and system owners receive a grounded
    narrative describing the assessment outcome, practice gaps, and recommended
    next action.
    """
    conn = get_connection()
    try:
        assessment = conn.execute(
            "SELECT id, level, overall_score, status, assessed_at "
            "FROM cmmc_assessments WHERE id = ? AND system_id = ?",
            (assessment_id, system_id),
        ).fetchone()
        system = conn.execute(
            "SELECT name, classification, boundary FROM cmmc_systems WHERE id = ?",
            (system_id,),
        ).fetchone()
        gaps = conn.execute(
            "SELECT practice_id, domain, status, gap_description "
            "FROM cmmc_practice_gaps WHERE assessment_id = ? ORDER BY domain LIMIT 10",
            (assessment_id,),
        ).fetchall()
        rendered = render_template(
            "handlers/cmmc_assessment.html",
            assessment=assessment, system=system, gaps=gaps,
        )
        gap_count = len(gaps)
        narrative = _ai_handler_narrative("CMMC assessment compliance notification", {
            "assessment_id": assessment_id,
            "system_id": system_id,
            "system_name": (system or {}).get("name", system_id),
            "classification": (system or {}).get("classification", "unknown"),
            "cmmc_level": (assessment or {}).get("level", "unknown"),
            "overall_score": round(float((assessment or {}).get("overall_score", 0) or 0), 1),
            "assessment_status": (assessment or {}).get("status", "unknown"),
            "gap_count": gap_count,
        }) if ai_narrative else None
        sendmail(to=recipient, subject="CMMC Assessment Result", html=rendered)
        payload = {"assessment_id": assessment_id, "system_id": system_id}
        if narrative:
            notify("compliance", f"{rendered}\n\nNarrative: {narrative}")
        else:
            notify("compliance", rendered)
        return {
            "status": "sent",
            "assessment_id": assessment_id,
            "system_id": system_id,
            "narrative": narrative,
        }
    finally:
        conn.close()


def handle_supply_chain_risk_handler(
    sbom_id: str, component_name: str, recipient: str, ai_narrative: bool = False
) -> dict:
    """Fetch SBOM/supply chain risk data and deliver vulnerability notification.

    Per aiify-opp-5948: adds a db→render→notify chain for supply chain risk
    findings so security operations and supply chain officers receive a grounded
    narrative describing the vulnerable component, CVSS severity, and the
    recommended isolation or patch action.
    """
    conn = get_connection()
    try:
        sbom = conn.execute(
            "SELECT id, component_name, version, vendor, component_type "
            "FROM sbom_components WHERE id = ? AND component_name = ?",
            (sbom_id, component_name),
        ).fetchone()
        vulns = conn.execute(
            "SELECT cve_id, cvss_score, severity, affected_versions, fixed_version "
            "FROM supply_chain_vulnerabilities WHERE sbom_id = ? "
            "ORDER BY cvss_score DESC LIMIT 5",
            (sbom_id,),
        ).fetchall()
        risk = conn.execute(
            "SELECT risk_level, exploitability, patch_available, last_assessed "
            "FROM supply_chain_risk_scores WHERE sbom_id = ? "
            "ORDER BY last_assessed DESC LIMIT 1",
            (sbom_id,),
        ).fetchone()
        rendered = render_template(
            "handlers/supply_chain_risk.html",
            sbom=sbom, vulns=vulns, risk=risk,
        )
        top_vuln = vulns[0] if vulns else {}
        narrative = _ai_handler_narrative("supply chain risk vulnerability notification", {
            "sbom_id": sbom_id,
            "component_name": component_name,
            "version": (sbom or {}).get("version", "unknown"),
            "vendor": (sbom or {}).get("vendor", "unknown"),
            "component_type": (sbom or {}).get("component_type", "unknown"),
            "vulnerability_count": len(vulns),
            "top_cvss_score": round(float((top_vuln or {}).get("cvss_score", 0) or 0), 1),
            "top_cve": (top_vuln or {}).get("cve_id", "none"),
            "risk_level": (risk or {}).get("risk_level", "unknown"),
            "exploitability": (risk or {}).get("exploitability", "unknown"),
            "patch_available": bool((risk or {}).get("patch_available", False)),
        }) if ai_narrative else None
        sendmail(to=recipient, subject="Supply Chain Risk Alert", html=rendered)
        payload = {"sbom_id": sbom_id, "component_name": component_name}
        if narrative:
            payload["narrative"] = narrative
        dispatch("supply_chain.risk_alert", payload)
        return {
            "status": "sent",
            "sbom_id": sbom_id,
            "component_name": component_name,
            "narrative": narrative,
        }
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
