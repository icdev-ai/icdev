# CUI // SP-CTI
"""Report delivery service — canvas assessment summaries and posture digests.

Queries canvas assessment tables, renders structured reports, and delivers
them to stakeholders via email, webhook, or audit trail.
"""

from __future__ import annotations

import json
from string import Template
from typing import Iterable

from tools.db.storage import get_connection
from .event_service import (
    render_template, render_to_string, send, sendmail, notify, emit, publish, dispatch, _now_iso,
)

CANVAS_REPORT_TEMPLATES = {
    "assessment_complete": (
        "Canvas **$canvas_name** assessment complete.\n"
        "Score: $score/100 | CAT I: $cat1 | CAT II: $cat2 | CAT III: $cat3\n"
        "Assessment ID: $assessment_id | Run at: $ran_at"
    ),
    "score_regression": (
        "REGRESSION: $canvas_name dropped $delta points ($prev_score → $score).\n"
        "Top findings: $top_findings"
    ),
    "score_improvement": (
        "IMPROVEMENT: $canvas_name gained $delta points ($prev_score → $score).\n"
        "Remediated: $remediated_count findings."
    ),
    "no_data": "Canvas $canvas_name has no assessment data. Run icdev-secure to generate.",
    "gate_blocked": "Gate $gate_id blocked $canvas_name promotion. Findings: $finding_count.",
}

POSTURE_DIGEST_TEMPLATES = {
    "daily":   "Daily Posture Digest — $date\nOverall: $overall_score/100\n$canvas_table",
    "weekly":  "Weekly Posture Summary — week of $week_start\nΔ7d: $delta_7d pts\n$canvas_table",
    "monthly": "Monthly Compliance Report — $month $year\nΔ30d: $delta_30d pts\n$canvas_table",
    "on_demand": "On-Demand Posture Report ($requester)\nGenerated: $generated_at\n$canvas_table",
}

ASSESSMENT_SUMMARY_TEMPLATES = {
    "fedramp":  "FedRAMP Moderate Assessment\nControls: $controls_total | Pass: $controls_pass\n$ssp_link",
    "cmmc":     "CMMC Level-2 Assessment\nPractices: $practices_total | Pass: $practices_pass",
    "stig":     "STIG Assessment\nChecks: $checks_total | NAF: $naf_count | Open: $open_count\nScore: $score%",
    "cato":     "cATO Evidence Package\nAO Decision: $ao_decision | Exp: $expiry\n$artifact_list",
    "poam":     "POA&M Summary\nOpen: $open_count | Closed: $closed_count | Due: $due_soon_count",
}

# ---------------------------------------------------------------------------
# AI-ification (aiify-opp-5539, aiify-opp-5922): optional LLM-synthesized executive summary.
#
# The db → render → notify chains below produce deterministic, template-based
# report text that remains the AUTHORITATIVE payload — stakeholders must never
# depend on LLM availability to receive an assessment or posture report. When a
# caller opts in via ``ai_narrative=True`` we ADDITIONALLY synthesize a short,
# grounded executive summary (the posture in plain language, what changed and
# why it matters, and the single most important next action) and attach it
# under the ``narrative`` return key. Any failure — no-LLM mode, air-gap,
# network, missing credentials — degrades silently to ``None`` so the
# deterministic report always ships.
#
# Mirrors the established pattern in ``alert_service._ai_alert_narrative``.
# ---------------------------------------------------------------------------

_NARRATIVE_SYSTEM_PROMPT = (
    "You are a DoD/IC compliance analyst briefing leadership on a security "
    "posture report. Write a concise executive summary (2-4 sentences) that: "
    "(1) states the current posture in plain language, (2) explains what "
    "changed and why it matters given the scores and deltas, and (3) "
    "recommends the single most important next action. Use only the facts "
    "provided — never invent scores, dates, counts, or canvas/system names. "
    "Output only the summary prose; no headers, no markdown, no preamble."
)


def _ai_report_narrative(report_kind: str, facts: dict) -> str | None:
    """Synthesize an optional LLM executive summary for a rendered report.

    Args:
        report_kind: Human label for the report family (e.g. "canvas
            assessment report"). Steers the model's framing.
        facts: Grounding facts already assembled for template rendering
            (the ``vars_`` dict). Passed verbatim so the summary cannot
            drift from the deterministic payload.

    Returns:
        A short summary string, or ``None`` if generation is unavailable
        or fails for any reason. Callers MUST treat ``None`` as "no
        narrative" and ship the deterministic report unchanged.
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
                        f"Report type: {report_kind}\n"
                        f"Facts:\n{fact_lines}\n\n"
                        "Write the executive summary."
                    ),
                }
            ],
            system_prompt=_NARRATIVE_SYSTEM_PROMPT,
            max_tokens=512,
            temperature=0.3,
            skip_injection_scan=True,  # trusted first-party fact dict, not user input
            classification="CUI",
        )
        resp = LLMRouter().invoke("narrative_generation", req)
        if resp and resp.content:
            return resp.content.strip()
    except Exception:
        pass  # Graceful degradation — deterministic report is authoritative.
    return None


def deliver_canvas_report(
    canvas_name: str,
    assessment_id: str,
    report_type: str,
    recipients: Iterable[str],
    channels: Iterable[str],
    ai_narrative: bool = False,
) -> dict:
    """Query canvas assessment, render report, and deliver to stakeholders.

    Fetches the latest assessment row for ``canvas_name``, builds a full
    report including CAT findings and trend delta, renders it as both
    plain-text and HTML, and delivers via all requested channels.

    When ``ai_narrative`` is True, additionally synthesizes a short LLM
    executive summary (returned under ``narrative`` and attached to Slack/
    Teams/webhook payloads). The deterministic templated report remains the
    authoritative payload; the summary is best-effort and is ``None`` when
    the LLM is unavailable (air-gap, no credentials, network failure).
    """
    conn = get_connection()
    try:
        # --- DB: fetch assessment + previous assessment for delta ---
        assessment_row = conn.execute(
            "SELECT id, design_id, score, cat1_findings, cat2_findings, cat3_findings, "
            "findings_json, assessment_type, created_at "
            "FROM canvas_assessments WHERE id = ?",
            (assessment_id,),
        ).fetchone()
        prev_row = conn.execute(
            "SELECT score FROM canvas_assessments "
            "WHERE design_id = ? AND id != ? ORDER BY created_at DESC LIMIT 1",
            ((assessment_row or {}).get("design_id", ""), assessment_id),
        ).fetchone()
        gate_rows = conn.execute(
            "SELECT gate_id, status, finding_count FROM canvas_gate_results "
            "WHERE assessment_id = ? ORDER BY finding_count DESC LIMIT 5",
            (assessment_id,),
        ).fetchall()

        if not assessment_row:
            rendered = Template(CANVAS_REPORT_TEMPLATES["no_data"]).safe_substitute(
                {"canvas_name": canvas_name}
            )
            return {"status": "no_data", "rendered": rendered}

        score = float(assessment_row["score"] or 0)
        prev_score = float((prev_row or {}).get("score", score))
        delta = round(score - prev_score, 1)

        vars_ = {
            "canvas_name": canvas_name,
            "score": round(score, 1),
            "prev_score": round(prev_score, 1),
            "delta": abs(delta),
            "assessment_id": assessment_id,
            "cat1": assessment_row["cat1_findings"] or 0,
            "cat2": assessment_row["cat2_findings"] or 0,
            "cat3": assessment_row["cat3_findings"] or 0,
            "ran_at": assessment_row["created_at"] or _now_iso(),
            "top_findings": _summarise_findings(assessment_row["findings_json"]),
            "remediated_count": (assessment_row["cat2_findings"] or 0) + (assessment_row["cat3_findings"] or 0),
            "finding_count": sum(r["finding_count"] or 0 for r in gate_rows),
            "gate_id": (gate_rows[0]["gate_id"] if gate_rows else ""),
        }

        delta_sign = "neg" if str(delta).startswith("-") else ("pos" if delta else "zero")
        tmpl_key = {"neg": "score_regression", "pos": "score_improvement"}.get(delta_sign, "assessment_complete")

        # --- Render ---
        rendered = Template(CANVAS_REPORT_TEMPLATES[tmpl_key]).safe_substitute(vars_)
        rendered_html = render_template(
            "reports/canvas_assessment.html",
            canvas_name=canvas_name,
            assessment=assessment_row,
            prev_score=prev_score,
            delta=delta,
            gates=gate_rows,
            report_type=report_type,
        )
        rendered_text = render_to_string("reports/canvas_assessment_plain.txt", vars_)

        # --- AI (optional): synthesize executive summary; None if unavailable ---
        narrative = _ai_report_narrative(
            "canvas assessment report", vars_
        ) if ai_narrative else None

        # --- Deliver ---
        receipts = {}
        for recipient in recipients:
            sendmail(
                to=recipient,
                subject=f"[ICDEV] {canvas_name} Assessment Report — Score: {score}",
                html=rendered_html,
            )
            receipts[f"email:{recipient}"] = "sent"

        for ch in channels:
            if ch == "audit":
                conn.execute(
                    "INSERT INTO audit_trail (resource_type, resource_id, event, actor, detail) "
                    "VALUES ('canvas_report', ?, 'report_delivered', 'system', ?)",
                    (assessment_id, rendered),
                )
                conn.commit()
                receipts["audit"] = "inserted"
            elif ch in ("slack", "teams"):
                payload = {"text": rendered, "canvas": canvas_name, "score": score}
                if narrative:
                    payload["narrative"] = narrative
                publish(ch, payload)
                receipts[ch] = "published"
            elif ch == "webhook":
                payload = {"canvas_name": canvas_name, "score": score, "delta": delta, "html": rendered_html}
                if narrative:
                    payload["narrative"] = narrative
                dispatch("webhook", payload)
                receipts[ch] = "dispatched"
            else:
                notify(ch, rendered_text)
                receipts[ch] = "notified"

        return {
            "status": "delivered",
            "canvas_name": canvas_name,
            "score": score,
            "delta": delta,
            "rendered": rendered,
            "narrative": narrative,
            "receipts": receipts,
        }
    finally:
        conn.close()


def deliver_assessment_summary(
    framework: str,
    project_id: str,
    recipients: Iterable[str],
    channels: Iterable[str],
    ai_narrative: bool = False,
) -> dict:
    """Query framework-specific compliance data, render summary, and deliver.

    Supports: fedramp, cmmc, stig, cato, poam.

    When ``ai_narrative`` is True, attaches a best-effort LLM executive
    summary (``None`` if the LLM is unavailable); the deterministic templated
    summary remains authoritative.
    """
    conn = get_connection()
    try:
        # --- DB: fetch framework data ---
        if framework == "stig":
            stig_row = conn.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN status IN ('not_a_finding','not_applicable') THEN 1 ELSE 0 END) as naf, "
                "SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) as open_cnt "
                "FROM govlift_stig_checks WHERE workload_id = ?",
                (project_id,),
            ).fetchone()
            total = int((stig_row or {}).get("total", 0))
            naf = int((stig_row or {}).get("naf", 0))
            open_cnt = int((stig_row or {}).get("open_cnt", 0))
            score = round(naf / total * 100, 1) if total else 0
            vars_ = {"checks_total": total, "naf_count": naf, "open_count": open_cnt, "score": score}

        elif framework == "poam":
            poam_row = conn.execute(
                "SELECT "
                "SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) as open_cnt, "
                "SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) as closed_cnt, "
                "SUM(CASE WHEN status='open' AND due_date <= DATE('now','+30 days') THEN 1 ELSE 0 END) as due_soon "
                "FROM poam_items WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            vars_ = {
                "open_count": int((poam_row or {}).get("open_cnt", 0)),
                "closed_count": int((poam_row or {}).get("closed_cnt", 0)),
                "due_soon_count": int((poam_row or {}).get("due_soon", 0)),
            }

        else:
            control_rows = conn.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN status='implemented' THEN 1 ELSE 0 END) as passed "
                "FROM compliance_controls WHERE project_id = ? AND framework = ?",
                (project_id, framework),
            ).fetchone()
            vars_ = {
                "controls_total": int((control_rows or {}).get("total", 0)),
                "controls_pass": int((control_rows or {}).get("passed", 0)),
                "practices_total": int((control_rows or {}).get("total", 0)),
                "practices_pass": int((control_rows or {}).get("passed", 0)),
                "ssp_link": f"/projects/{project_id}/ssp",
                "ao_decision": "Authorized",
                "expiry": "2027-01-01",
                "artifact_list": "",
            }

        # --- Render ---
        tmpl_str = ASSESSMENT_SUMMARY_TEMPLATES.get(framework, "Assessment: $framework ($project_id)")
        rendered = Template(tmpl_str).safe_substitute(vars_)
        rendered_html = render_template(
            f"reports/{framework}_summary.html",
            framework=framework,
            project_id=project_id,
            **vars_,
        )

        # --- AI (optional): synthesize executive summary; None if unavailable ---
        narrative = _ai_report_narrative(
            f"{framework} compliance assessment summary",
            {**vars_, "framework": framework, "project_id": project_id},
        ) if ai_narrative else None

        # --- Deliver ---
        receipts = {}
        for recipient in recipients:
            send(to=recipient, subject=f"[ICDEV] {framework.upper()} Assessment — {project_id}", body=rendered)
            receipts[f"email:{recipient}"] = "sent"
        for ch in channels:
            if ch == "audit":
                conn.execute(
                    "INSERT INTO audit_trail (resource_type, resource_id, event, actor, detail) "
                    "VALUES ('compliance_report', ?, ?, 'system', ?)",
                    (project_id, f"{framework}_summary", rendered),
                )
                conn.commit()
                receipts["audit"] = "inserted"
            else:
                payload = {"framework": framework, "project_id": project_id, "summary": rendered}
                if narrative:
                    payload["narrative"] = narrative
                publish(ch, payload)
                receipts[ch] = "published"

        return {"status": "delivered", "framework": framework, "project_id": project_id, "rendered": rendered, "narrative": narrative, "receipts": receipts}
    finally:
        conn.close()


def deliver_posture_digest(
    digest_type: str,
    period: dict,
    recipients: Iterable[str],
    channels: Iterable[str],
    ai_narrative: bool = False,
) -> dict:
    """Compile overall compliance posture, render digest, and deliver.

    Aggregates canvas scores, computes period delta, renders a multi-canvas
    digest table, and delivers to all recipients and channels.

    When ``ai_narrative`` is True, attaches a best-effort LLM executive
    summary of the posture trend (``None`` if the LLM is unavailable); the
    deterministic templated digest remains authoritative.
    """
    conn = get_connection()
    try:
        # --- DB: aggregate current scores across all canvases ---
        canvas_rows = conn.execute(
            "SELECT canvas_name, AVG(score) as avg_score, MAX(created_at) as latest "
            "FROM canvas_assessments "
            "GROUP BY canvas_name ORDER BY avg_score DESC"
        ).fetchall()
        overall_row = conn.execute(
            "SELECT AVG(score) as overall FROM canvas_assessments "
            "WHERE created_at >= DATE('now', '-1 day')"
        ).fetchone()
        prev_overall_row = conn.execute(
            "SELECT AVG(score) as overall FROM canvas_assessments "
            "WHERE created_at >= DATE('now', '-8 days') AND created_at < DATE('now', '-1 day')"
        ).fetchone()

        overall_score = round(float((overall_row or {}).get("overall", 0) or 0), 1)
        prev_score = round(float((prev_overall_row or {}).get("overall", overall_score) or overall_score), 1)
        delta_7d = round(overall_score - prev_score, 1)

        canvas_table = "\n".join(
            f"  {r['canvas_name']:20} {round(r['avg_score'] or 0, 1):5.1f}/100"
            for r in canvas_rows
        )

        vars_ = {
            "date": period.get("date", _now_iso()[:10]),
            "week_start": period.get("week_start", ""),
            "month": period.get("month", ""),
            "year": period.get("year", ""),
            "overall_score": overall_score,
            "delta_7d": delta_7d,
            "delta_30d": period.get("delta_30d", 0),
            "canvas_table": canvas_table,
            "requester": period.get("requester", "system"),
            "generated_at": _now_iso(),
        }

        # --- Render ---
        tmpl_str = POSTURE_DIGEST_TEMPLATES.get(digest_type, POSTURE_DIGEST_TEMPLATES["on_demand"])
        rendered = Template(tmpl_str).safe_substitute(vars_)
        rendered_html = render_template(
            "reports/posture_digest.html",
            digest_type=digest_type,
            canvases=canvas_rows,
            overall=overall_score,
            delta=delta_7d,
            period=period,
        )

        # --- AI (optional): synthesize executive summary; None if unavailable ---
        narrative = _ai_report_narrative(
            f"{digest_type} compliance posture digest",
            {k: v for k, v in vars_.items() if k != "canvas_table"},
        ) if ai_narrative else None

        # --- Deliver ---
        receipts = {}
        for recipient in recipients:
            sendmail(
                to=recipient,
                subject=f"[ICDEV] {digest_type.title()} Posture Digest — {overall_score}/100",
                html=rendered_html,
            )
            receipts[f"email:{recipient}"] = "sent"
        for ch in channels:
            if ch == "audit":
                conn.execute(
                    "INSERT INTO audit_trail (resource_type, resource_id, event, actor, detail) "
                    "VALUES ('posture_digest', 'global', ?, 'system', ?)",
                    (digest_type, rendered),
                )
                conn.commit()
                receipts["audit"] = "inserted"
            elif ch in ("slack", "teams", "webhook"):
                payload = {"type": digest_type, "overall": overall_score, "delta": delta_7d}
                if narrative:
                    payload["narrative"] = narrative
                emit("posture.digest", payload)
                receipts[ch] = "emitted"
            else:
                notify(ch, rendered)
                receipts[ch] = "notified"

        return {
            "status": "delivered",
            "digest_type": digest_type,
            "overall_score": overall_score,
            "delta_7d": delta_7d,
            "canvas_count": len(canvas_rows),
            "rendered": rendered,
            "narrative": narrative,
            "receipts": receipts,
        }
    finally:
        conn.close()


def deliver_aiify_roadmap_report(
    roadmap_id: str,
    recipients: Iterable[str],
    channels: Iterable[str],
    ai_narrative: bool = False,
) -> dict:
    """Query AI-ify roadmap data, render report, and deliver to stakeholders.

    Fetches the roadmap, its top-scoring opportunities (by composite score),
    and the parent scan's overall AI readiness score; renders a structured
    report; and delivers via all requested channels.

    When ``ai_narrative`` is True, additionally synthesizes a short LLM
    executive summary of the AI readiness posture and recommended next steps
    (returned under ``narrative``). The deterministic templated report remains
    the authoritative payload; the summary is best-effort and is ``None`` when
    the LLM is unavailable (air-gap, no credentials, network failure).
    """
    conn = get_connection()
    try:
        # --- DB: fetch roadmap + top opportunities + scan readiness ---
        roadmap_row = conn.execute(
            "SELECT roadmap_id, scan_id, phases_json, created_at "
            "FROM aiify_roadmaps WHERE roadmap_id = ?",
            (roadmap_id,),
        ).fetchone()

        if not roadmap_row:
            rendered = f"AI-ify roadmap {roadmap_id} not found. Run icdev-secure to generate."
            return {"status": "no_data", "rendered": rendered}

        scan_id = roadmap_row["scan_id"]
        top_opps = conn.execute(
            "SELECT o.function_name, o.pattern_type, o.ai_paradigm, "
            "s.composite_score, s.value_score, s.feasibility_score "
            "FROM aiify_opportunities o "
            "JOIN aiify_scores s ON s.opportunity_id = o.opportunity_id "
            "WHERE o.roadmap_id = ? ORDER BY s.composite_score DESC LIMIT 10",
            (roadmap_id,),
        ).fetchall()
        scan_row = conn.execute(
            "SELECT overall_ai_readiness, status, input_ref FROM aiify_scans "
            "WHERE scan_id = ?",
            (scan_id,),
        ).fetchone()

        opp_count = len(top_opps)
        readiness = round(float((scan_row or {}).get("overall_ai_readiness", 0) or 0), 1)
        top_pattern = top_opps[0]["pattern_type"] if top_opps else "none"
        avg_composite = round(
            sum(float(o["composite_score"] or 0) for o in top_opps) / max(opp_count, 1), 3
        )

        vars_ = {
            "roadmap_id": roadmap_id,
            "readiness": readiness,
            "opportunity_count": opp_count,
            "top_pattern": top_pattern,
            "avg_composite_score": avg_composite,
            "scan_status": (scan_row or {}).get("status", "unknown"),
            "input_ref": (scan_row or {}).get("input_ref", ""),
            "created_at": roadmap_row["created_at"] or _now_iso(),
        }

        rendered = (
            f"AI-ify Roadmap Report: {roadmap_id}\n"
            f"AI Readiness: {readiness}/100 | Opportunities: {opp_count}\n"
            f"Top pattern: {top_pattern} | Avg composite score: {avg_composite}\n"
            f"Scan status: {vars_['scan_status']} | Input: {vars_['input_ref']}"
        )
        rendered_html = render_template(
            "reports/aiify_roadmap.html",
            roadmap=roadmap_row,
            opportunities=top_opps,
            scan=scan_row,
            readiness=readiness,
        )

        # --- AI (optional): synthesize executive summary; None if unavailable ---
        narrative = _ai_report_narrative(
            "AI-ify roadmap readiness report", vars_
        ) if ai_narrative else None

        # --- Deliver ---
        receipts = {}
        for recipient in recipients:
            sendmail(
                to=recipient,
                subject=f"[ICDEV] AI-ify Roadmap Report — {roadmap_id} ({readiness}/100 readiness)",
                html=rendered_html,
            )
            receipts[f"email:{recipient}"] = "sent"

        for ch in channels:
            if ch == "audit":
                conn.execute(
                    "INSERT INTO audit_trail (resource_type, resource_id, event, actor, detail) "
                    "VALUES ('aiify_report', ?, 'report_delivered', 'system', ?)",
                    (roadmap_id, rendered),
                )
                conn.commit()
                receipts["audit"] = "inserted"
            elif ch in ("slack", "teams", "webhook"):
                payload = {
                    "roadmap_id": roadmap_id,
                    "readiness": readiness,
                    "opportunities": opp_count,
                }
                if narrative:
                    payload["narrative"] = narrative
                publish(ch, payload)
                receipts[ch] = "published"
            else:
                notify(ch, rendered)
                receipts[ch] = "notified"

        return {
            "status": "delivered",
            "roadmap_id": roadmap_id,
            "readiness": readiness,
            "opportunity_count": opp_count,
            "rendered": rendered,
            "narrative": narrative,
            "receipts": receipts,
        }
    finally:
        conn.close()


def deliver_aiify_scan_report(
    scan_id: int,
    recipients: Iterable[str],
    channels: Iterable[str],
    ai_narrative: bool = False,
) -> dict:
    """Query AI-ify scan findings, render report, and deliver to stakeholders.

    Fetches the scan record, aggregates opportunity stats by pattern type and
    module, and renders a structured scan findings report; delivers via all
    requested channels.

    When ``ai_narrative`` is True, additionally synthesizes a short LLM
    executive summary of the scan findings and recommended next steps
    (returned under ``narrative``). The deterministic templated report remains
    the authoritative payload; the summary is best-effort and is ``None`` when
    the LLM is unavailable (air-gap, no credentials, network failure).
    """
    conn = get_connection()
    try:
        # --- DB: fetch scan metadata ---
        scan_row = conn.execute(
            "SELECT scan_id, overall_ai_readiness, status, input_ref, created_at "
            "FROM aiify_scans WHERE scan_id = ?",
            (scan_id,),
        ).fetchone()

        if not scan_row:
            rendered = f"AI-ify scan {scan_id} not found. Run icdev-secure to generate."
            return {"status": "no_data", "rendered": rendered}

        # --- DB: opportunity pattern breakdown ---
        pattern_rows = conn.execute(
            "SELECT o.pattern_type, COUNT(*) as opp_count, "
            "AVG(s.composite_score) as avg_score "
            "FROM aiify_opportunities o "
            "JOIN aiify_scores s ON s.opportunity_id = o.opportunity_id "
            "WHERE o.scan_id = ? GROUP BY o.pattern_type ORDER BY opp_count DESC",
            (scan_id,),
        ).fetchall()

        # --- DB: top modules by opportunity density ---
        module_rows = conn.execute(
            "SELECT o.module_path, COUNT(*) as opp_count "
            "FROM aiify_opportunities o "
            "WHERE o.scan_id = ? GROUP BY o.module_path "
            "ORDER BY opp_count DESC LIMIT 10",
            (scan_id,),
        ).fetchall()

        # --- DB: highest-value opportunities ---
        top_opps = conn.execute(
            "SELECT o.function_name, o.pattern_type, o.module_path, s.composite_score "
            "FROM aiify_opportunities o "
            "JOIN aiify_scores s ON s.opportunity_id = o.opportunity_id "
            "WHERE o.scan_id = ? ORDER BY s.composite_score DESC LIMIT 5",
            (scan_id,),
        ).fetchall()

        readiness = round(float(scan_row["overall_ai_readiness"] or 0), 1)
        total_opps = sum(int(p["opp_count"]) for p in pattern_rows)
        avg_composite = round(
            sum(float(p["avg_score"] or 0) for p in pattern_rows) / max(len(pattern_rows), 1), 3
        )

        vars_ = {
            "scan_id": scan_id,
            "readiness": readiness,
            "total_opportunities": total_opps,
            "pattern_count": len(pattern_rows),
            "top_pattern": pattern_rows[0]["pattern_type"] if pattern_rows else "none",
            "avg_composite_score": avg_composite,
            "scan_status": scan_row["status"] or "unknown",
            "input_ref": scan_row["input_ref"] or "",
            "created_at": scan_row["created_at"] or _now_iso(),
        }

        rendered = (
            f"AI-ify Scan Report: scan-{scan_id}\n"
            f"AI Readiness: {readiness}/100 | Opportunities: {total_opps}\n"
            f"Patterns: {len(pattern_rows)} | Top: {vars_['top_pattern']} | Avg score: {avg_composite}\n"
            f"Input: {vars_['input_ref']} | Status: {vars_['scan_status']}"
        )
        rendered_html = render_template(
            "reports/aiify_scan.html",
            scan=scan_row,
            patterns=pattern_rows,
            modules=module_rows,
            top_opps=top_opps,
            readiness=readiness,
        )

        # --- AI (optional): synthesize executive summary; None if unavailable ---
        narrative = _ai_report_narrative(
            "AI-ify scan findings report", vars_
        ) if ai_narrative else None

        # --- Deliver ---
        receipts = {}
        for recipient in recipients:
            sendmail(
                to=recipient,
                subject=f"[ICDEV] AI-ify Scan Report — scan-{scan_id} ({readiness}/100 readiness)",
                html=rendered_html,
            )
            receipts[f"email:{recipient}"] = "sent"

        for ch in channels:
            if ch == "audit":
                conn.execute(
                    "INSERT INTO audit_trail (resource_type, resource_id, event, actor, detail) "
                    "VALUES ('aiify_scan_report', ?, 'report_delivered', 'system', ?)",
                    (str(scan_id), rendered),
                )
                conn.commit()
                receipts["audit"] = "inserted"
            elif ch in ("slack", "teams", "webhook"):
                payload = {
                    "scan_id": scan_id,
                    "readiness": readiness,
                    "total_opportunities": total_opps,
                }
                if narrative:
                    payload["narrative"] = narrative
                publish(ch, payload)
                receipts[ch] = "published"
            else:
                notify(ch, rendered)
                receipts[ch] = "notified"

        return {
            "status": "delivered",
            "scan_id": scan_id,
            "readiness": readiness,
            "total_opportunities": total_opps,
            "rendered": rendered,
            "narrative": narrative,
            "receipts": receipts,
        }
    finally:
        conn.close()


def deliver_aiify_opportunity_report(
    opportunity_id: int,
    recipients: Iterable[str],
    channels: Iterable[str],
    ai_narrative: bool = False,
) -> dict:
    """Query a single AI-ify opportunity, render a report, and deliver to stakeholders.

    Fetches the opportunity record, its score breakdown, and the parent scan's
    readiness baseline; renders a structured single-opportunity report; and
    delivers via all requested channels.

    When ``ai_narrative`` is True, additionally synthesizes a short LLM
    executive summary of the opportunity's value and recommended next step
    (returned under ``narrative``). The deterministic templated report remains
    the authoritative payload; the summary is best-effort and is ``None`` when
    the LLM is unavailable (air-gap, no credentials, network failure).
    """
    conn = get_connection()
    try:
        # --- DB: fetch opportunity + score ---
        opp_row = conn.execute(
            "SELECT o.opportunity_id, o.scan_id, o.roadmap_id, o.function_name, "
            "o.module_path, o.pattern_type, o.ai_paradigm, o.phase, o.status, o.created_at "
            "FROM aiify_opportunities o WHERE o.opportunity_id = ?",
            (opportunity_id,),
        ).fetchone()

        if not opp_row:
            rendered = f"AI-ify opportunity {opportunity_id} not found. Run icdev-secure to generate."
            return {"status": "no_data", "rendered": rendered}

        score_row = conn.execute(
            "SELECT composite_score, value_score, feasibility_score, risk_score "
            "FROM aiify_scores WHERE opportunity_id = ?",
            (opportunity_id,),
        ).fetchone()

        scan_row = conn.execute(
            "SELECT overall_ai_readiness, status FROM aiify_scans WHERE scan_id = ?",
            (opp_row["scan_id"],),
        ).fetchone()

        composite = round(float((score_row or {}).get("composite_score", 0) or 0), 4)
        value = round(float((score_row or {}).get("value_score", 0) or 0), 4)
        feasibility = round(float((score_row or {}).get("feasibility_score", 0) or 0), 4)
        risk = round(float((score_row or {}).get("risk_score", 0) or 0), 4)
        readiness = round(float((scan_row or {}).get("overall_ai_readiness", 0) or 0), 1)

        vars_ = {
            "opportunity_id": opportunity_id,
            "scan_id": opp_row["scan_id"],
            "roadmap_id": opp_row["roadmap_id"] or "",
            "function_name": opp_row["function_name"] or "<unknown>",
            "module_path": opp_row["module_path"] or "",
            "pattern_type": opp_row["pattern_type"] or "",
            "ai_paradigm": opp_row["ai_paradigm"] or "",
            "phase": opp_row["phase"] or "",
            "status": opp_row["status"] or "open",
            "composite_score": composite,
            "value_score": value,
            "feasibility_score": feasibility,
            "risk_score": risk,
            "overall_readiness": readiness,
            "created_at": opp_row["created_at"] or _now_iso(),
        }

        rendered = (
            f"AI-ify Opportunity Report: opp-{opportunity_id}\n"
            f"Function: {vars_['function_name']} | Module: {vars_['module_path']}\n"
            f"Pattern: {vars_['pattern_type']} | Paradigm: {vars_['ai_paradigm']}\n"
            f"Phase: {vars_['phase']} | Status: {vars_['status']}\n"
            f"Composite: {composite} | Value: {value} | Feasibility: {feasibility} | Risk: {risk}\n"
            f"Scan readiness: {readiness}/100 | Roadmap: {vars_['roadmap_id']}"
        )
        rendered_html = render_template(
            "reports/aiify_opportunity.html",
            opportunity=opp_row,
            scores=score_row,
            scan=scan_row,
            **vars_,
        )

        # --- AI (optional): synthesize executive summary; None if unavailable ---
        narrative = _ai_report_narrative(
            "AI-ify opportunity readiness report", vars_
        ) if ai_narrative else None

        # --- Deliver ---
        receipts = {}
        for recipient in recipients:
            sendmail(
                to=recipient,
                subject=(
                    f"[ICDEV] AI-ify Opportunity Report — opp-{opportunity_id} "
                    f"({vars_['pattern_type']}, score: {composite})"
                ),
                html=rendered_html,
            )
            receipts[f"email:{recipient}"] = "sent"

        for ch in channels:
            if ch == "audit":
                conn.execute(
                    "INSERT INTO audit_trail (resource_type, resource_id, event, actor, detail) "
                    "VALUES ('aiify_opportunity_report', ?, 'report_delivered', 'system', ?)",
                    (str(opportunity_id), rendered),
                )
                conn.commit()
                receipts["audit"] = "inserted"
            elif ch in ("slack", "teams", "webhook"):
                payload = {
                    "opportunity_id": opportunity_id,
                    "pattern_type": vars_["pattern_type"],
                    "composite_score": composite,
                    "status": vars_["status"],
                }
                if narrative:
                    payload["narrative"] = narrative
                publish(ch, payload)
                receipts[ch] = "published"
            else:
                notify(ch, rendered)
                receipts[ch] = "notified"

        return {
            "status": "delivered",
            "opportunity_id": opportunity_id,
            "pattern_type": vars_["pattern_type"],
            "composite_score": composite,
            "rendered": rendered,
            "narrative": narrative,
            "receipts": receipts,
        }
    finally:
        conn.close()


def _summarise_findings(findings_json: str | None) -> str:
    if not findings_json:
        return "none"
    try:
        findings = json.loads(findings_json)
        top = [f.get("title", "finding") for f in (findings[:3] if isinstance(findings, list) else [])]
        return "; ".join(top) or "none"
    except Exception:
        return "parse error"
