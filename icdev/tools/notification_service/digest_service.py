# CUI // SP-CTI
"""Digest and summary delivery service — linear db→render→notify pipelines.

Each function follows a strict linear pattern: DB fetch → render → notify.
Per aiify-opp-5556 the deterministic chain is now AI-augmented: callers may
opt in via ``ai_narrative=True`` to additionally receive a short, grounded
LLM narrative (returned under ``narrative`` and attached to structured
channel payloads). The templated digest remains the authoritative payload and
ships unchanged when the LLM is unavailable. See ``_ai_digest_narrative``.
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone

import yaml

from tools.db.storage import get_connection
from .event_service import (
    render_template, render_to_string,
    send, sendmail, notify, emit, publish, dispatch, _now_iso,
)

_NOTIFICATION_CONFIG_PATH = (
    pathlib.Path(__file__).parent.parent.parent / "args" / "notification_config.yaml"
)


def _load_narrative_config() -> dict:
    """Load narrative_llm section from notification_config.yaml; return {} on failure."""
    try:
        with open(_NOTIFICATION_CONFIG_PATH, encoding="utf-8") as fh:
            return (yaml.safe_load(fh) or {}).get("narrative_llm", {})
    except Exception:
        return {}


_NARR_CFG = _load_narrative_config()
_NARRATIVE_MAX_TOKENS: int = int(_NARR_CFG.get("max_tokens", 512))
_NARRATIVE_TEMPERATURE: float = float(_NARR_CFG.get("temperature", 0.3))

# Query / slice limits used by notification consumers and tests
_KANBAN_DONE_LIMIT         = 5
_ORACLE_TOP_PREDS_LIMIT    = 5
_AIIFY_TOP_OPPS_LIMIT      = 5
_AGENT_ERRORS_LIMIT        = 5
_ZIG_GAPS_LIMIT            = 10
_POAM_DAYS_AHEAD           = 14
_AUDIT_SINCE_HOURS         = 24
_NARRATIVE_TOP_PREDS_SLICE = 3
_NARRATIVE_TOP_OPPS_SLICE  = 3

# ---------------------------------------------------------------------------
# AI-ification (aiify-opp-5556): optional LLM-synthesized digest narrative.
#
# Every send_* function below is a deterministic db → render → notify chain.
# The templated digest/report it produces remains the AUTHORITATIVE payload —
# recipients must never depend on LLM availability to receive their digest.
# When a caller opts in via ``ai_narrative=True`` we ADDITIONALLY synthesize a
# short, grounded narrative (what the numbers say in plain language, what
# changed and why it matters, and the single most important next action) and
# attach it under the ``narrative`` return key and to structured channel
# payloads. Any failure — no-LLM mode, air-gap, network, missing credentials —
# degrades silently to ``None`` so the deterministic digest always ships.
#
# Mirrors the established pattern in ``report_service._ai_report_narrative``
# and ``alert_service._ai_alert_narrative``.
# ---------------------------------------------------------------------------

_DIGEST_NARRATIVE_SYSTEM_PROMPT = (
    "You are a DoD/IC operations analyst writing the opening note for a "
    "scheduled status digest. Write a concise narrative (2-4 sentences) that: "
    "(1) summarizes the current state in plain language, (2) highlights what "
    "stands out given the counts, scores, and deltas provided, and (3) "
    "recommends the single most important next action for the recipient. Use "
    "only the facts provided — never invent counts, scores, dates, names, or "
    "identifiers. Output only the narrative prose; no headers, no markdown, "
    "no preamble."
)


# ---------------------------------------------------------------------------
# Per-user digest gating (crx-not-01)
#
# Extends this digest service (rather than forking it) to honour the per-user
# notification preferences added in tools/notifications/preferences.py: a user
# only receives a scheduled digest when they have opted in, and never during
# their quiet hours. The check is best-effort — if the preferences layer is
# unavailable the digest ships (deterministic delivery stays authoritative).
# ---------------------------------------------------------------------------


def should_deliver_digest(
    user_id: str,
    tenant_id: str | None = None,
    now=None,
    default: bool = True,
) -> bool:
    """Return True when a scheduled digest should be delivered to ``user_id``.

    Consults tools.notifications.preferences: the user must have opted into
    digest mode (``wants_digest``) and must not currently be in quiet hours.
    Returns ``default`` if the preferences layer cannot be consulted so callers
    that pass a bare recipient string keep working unchanged.
    """
    try:
        from tools.notifications import preferences as _prefs

        if not _prefs.wants_digest(user_id, tenant_id):
            return False
        if _prefs.in_quiet_hours(_prefs.get_preferences(user_id, tenant_id), now):
            return False
        return True
    except Exception:
        return default


def _ai_digest_narrative(digest_kind: str, facts: dict) -> str | None:
    """Synthesize an optional LLM narrative for a rendered digest.

    Args:
        digest_kind: Human label for the digest family (e.g. "daily kanban
            digest"). Steers the model's framing.
        facts: Grounding facts (scalar counts/scores/labels) already derived
            for the deterministic digest. Passed verbatim so the narrative
            cannot drift from the authoritative payload.

    Returns:
        A short narrative string, or ``None`` if generation is unavailable or
        fails for any reason. Callers MUST treat ``None`` as "no narrative"
        and ship the deterministic digest unchanged.
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
                        f"Digest type: {digest_kind}\n"
                        f"Facts:\n{fact_lines}\n\n"
                        "Write the digest narrative."
                    ),
                }
            ],
            system_prompt=_DIGEST_NARRATIVE_SYSTEM_PROMPT,
            max_tokens=_NARRATIVE_MAX_TOKENS,
            temperature=_NARRATIVE_TEMPERATURE,
            skip_injection_scan=True,  # trusted first-party fact dict, not user input
            classification="CUI",
        )
        resp = LLMRouter().invoke("narrative_generation", req)
        if resp and resp.content:
            return resp.content.strip()
    except Exception:
        pass  # Graceful degradation — deterministic digest is authoritative.
    return None


def send_daily_kanban_digest(recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch today's kanban activity, render digest, and send to recipient."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM kanban_tasks "
        "GROUP BY status ORDER BY cnt DESC"
    ).fetchall()
    done = conn.execute(
        "SELECT id, title FROM kanban_tasks WHERE status='done' "
        "AND updated_at >= DATE('now','-1 day') LIMIT 5"
    ).fetchall()
    conn.close()
    body = render_template("digests/kanban_daily.html", rows=rows, done=done, date=_now_iso()[:10])
    narrative = _ai_digest_narrative("daily kanban digest", {
        "date": _now_iso()[:10],
        "status_breakdown": "; ".join(f"{r['status']}: {r['cnt']}" for r in rows) or "none",
        "status_group_count": len(rows),
        "recently_completed": "; ".join(d["title"] for d in done) or "none",
    }) if ai_narrative else None
    send(to=recipient, subject=f"Daily Kanban Digest — {_now_iso()[:10]}", body=body)
    payload = {"type": "kanban_daily", "recipient": recipient}
    if narrative:
        payload["narrative"] = narrative
    emit("digest.sent", payload)
    return {"status": "sent", "recipient": recipient, "task_count": len(rows), "narrative": narrative}


def send_oracle_weekly_digest(recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch week's Oracle predictions, render summary, and deliver."""
    conn = get_connection()
    predictions = conn.execute(
        "SELECT severity, COUNT(*) as cnt, AVG(confidence) as avg_conf "
        "FROM oracle_predictions WHERE created_at >= DATE('now','-7 days') "
        "GROUP BY severity ORDER BY cnt DESC"
    ).fetchall()
    top_preds = conn.execute(
        "SELECT title, severity, confidence FROM oracle_predictions "
        "WHERE created_at >= DATE('now','-7 days') "
        "ORDER BY confidence DESC LIMIT 10"
    ).fetchall()
    lens_stats = conn.execute(
        "SELECT lens_id, COUNT(*) as cnt FROM oracle_predictions "
        "WHERE created_at >= DATE('now','-7 days') GROUP BY lens_id"
    ).fetchall()
    conn.close()
    rendered = render_template(
        "digests/oracle_weekly.html",
        predictions=predictions, top_preds=top_preds, lens_stats=lens_stats,
    )
    narrative = _ai_digest_narrative("oracle weekly prediction digest", {
        "prediction_groups": len(predictions),
        "severity_breakdown": "; ".join(
            f"{p['severity']}: {p['cnt']} (avg conf {round(float(p['avg_conf'] or 0), 2)})"
            for p in predictions
        ) or "none",
        "top_predictions": "; ".join(
            f"{p['title']} ({p['severity']}, conf {round(float(p['confidence'] or 0), 2)})"
            for p in top_preds[:5]
        ) or "none",
        "active_lens_count": len(lens_stats),
    }) if ai_narrative else None
    sendmail(to=recipient, subject=f"Oracle Weekly Digest — {_now_iso()[:10]}", html=rendered)
    notify("audit", f"oracle_weekly_digest sent to {recipient}")
    return {"status": "sent", "recipient": recipient, "prediction_groups": len(predictions), "narrative": narrative}


def send_compliance_posture_report(recipient: str, canvas_filter: list | None = None, ai_narrative: bool = False) -> dict:
    """Fetch canvas compliance scores, render posture report, and deliver."""
    conn = get_connection()
    canvases = conn.execute(
        "SELECT canvas_name, AVG(score) as score, MAX(created_at) as latest "
        "FROM canvas_assessments GROUP BY canvas_name ORDER BY score"
    ).fetchall()
    overall = conn.execute(
        "SELECT AVG(score) as overall FROM canvas_assessments "
        "WHERE created_at >= DATE('now','-1 day')"
    ).fetchone()
    open_findings = conn.execute(
        "SELECT severity, COUNT(*) as cnt FROM stig_findings "
        "WHERE status='open' GROUP BY severity"
    ).fetchall()
    conn.close()
    if canvas_filter:
        canvases = [c for c in canvases if c["canvas_name"] in canvas_filter]
    overall_score = round(float((overall or {}).get("overall", 0) or 0), 1)
    report_html = render_template(
        "reports/compliance_posture.html",
        canvases=canvases, overall=overall_score, findings=open_findings,
    )
    narrative = _ai_digest_narrative("compliance posture report", {
        "overall_score": overall_score,
        "canvas_count": len(canvases),
        "lowest_canvases": "; ".join(
            f"{c['canvas_name']}: {round(float(c['score'] or 0), 1)}" for c in canvases[:3]
        ) or "none",
        "open_findings_by_severity": "; ".join(
            f"{f['severity']}: {f['cnt']}" for f in open_findings
        ) or "none",
    }) if ai_narrative else None
    sendmail(to=recipient, subject=f"Compliance Posture Report — {overall_score}/100", html=report_html)
    payload = {"overall": overall_score, "recipient": recipient}
    if narrative:
        payload["narrative"] = narrative
    publish("compliance.report", payload)
    return {"status": "sent", "recipient": recipient, "overall_score": overall_score, "narrative": narrative}


def send_agent_health_alert(recipient: str, ai_narrative: bool = False) -> dict:
    """Query agent health, render alert if degraded, and notify ops."""
    conn = get_connection()
    agents = conn.execute(
        "SELECT id, name, status, last_heartbeat FROM agents ORDER BY status"
    ).fetchall()
    inactive = conn.execute(
        "SELECT COUNT(*) as cnt FROM agents WHERE status != 'active'"
    ).fetchone()
    recent_errors = conn.execute(
        "SELECT agent_id, error_msg, created_at FROM agent_errors "
        "WHERE created_at >= DATE('now','-1 hour') ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    conn.close()
    inactive_count = int((inactive or {}).get("cnt", 0))
    rendered = render_to_string(
        "alerts/agent_health.html",
        {"agents": agents, "inactive_count": inactive_count, "errors": recent_errors},
    )
    narrative = _ai_digest_narrative("agent health alert", {
        "total_agents": len(agents),
        "inactive_count": inactive_count,
        "recent_error_count": len(recent_errors),
        "recent_errors": "; ".join(
            f"{e['agent_id']}: {e['error_msg']}" for e in recent_errors[:3]
        ) or "none",
    }) if ai_narrative else None
    send(to=recipient, subject=f"Agent Health Alert — {inactive_count} inactive", body=rendered)
    payload = {"inactive_count": inactive_count, "recipient": recipient}
    if narrative:
        payload["narrative"] = narrative
    emit("agent.health_alert", payload)
    return {"status": "sent", "recipient": recipient, "inactive_agents": inactive_count, "narrative": narrative}


def send_genesis_phase_summary(design_id: str, recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch genesis phase history, render summary, and deliver to recipient."""
    conn = get_connection()
    phases = conn.execute(
        "SELECT phase, status, started_at, completed_at FROM genesis_phase_log "
        "WHERE design_id=%s ORDER BY started_at", (design_id,)
    ).fetchall()
    design = conn.execute(
        "SELECT name, status, current_phase FROM genesis_designs WHERE id=%s", (design_id,)
    ).fetchone()
    reflexes = conn.execute(
        "SELECT name, confidence, fired_at FROM genesis_reflexes "
        "WHERE design_id=%s ORDER BY fired_at DESC LIMIT 5", (design_id,)
    ).fetchall()
    conn.close()
    rendered = render_template(
        "reports/genesis_phase_summary.html",
        design=design, phases=phases, reflexes=reflexes,
    )
    narrative = _ai_digest_narrative("genesis design phase summary", {
        "design_id": design_id,
        "design_name": (design or {}).get("name", design_id),
        "design_status": (design or {}).get("status", "unknown"),
        "current_phase": (design or {}).get("current_phase", "unknown"),
        "phase_count": len(phases),
        "recent_reflexes": "; ".join(
            f"{r['name']} (conf {round(float(r['confidence'] or 0), 2)})" for r in reflexes
        ) or "none",
    }) if ai_narrative else None
    sendmail(to=recipient, subject=f"Genesis Summary — {(design or {}).get('name', design_id)}", html=rendered)
    payload = {"design_id": design_id, "phase_count": len(phases)}
    if narrative:
        payload["narrative"] = narrative
    dispatch("genesis.summary", payload)
    return {"status": "sent", "design_id": design_id, "recipient": recipient, "phases": len(phases), "narrative": narrative}


def send_zig_maturity_report(recipient: str, ai_narrative: bool = False) -> dict:
    """Query ZIG pillar scores, render maturity report, and deliver."""
    conn = get_connection()
    pillars = conn.execute(
        "SELECT pillar_slug, score, maturity_level, complete_activities, activity_count "
        "FROM zig_maturity_scores m1 WHERE assessment_run_at = ("
        "  SELECT MAX(assessment_run_at) FROM zig_maturity_scores m2 "
        "  WHERE m2.pillar_slug = m1.pillar_slug) ORDER BY score"
    ).fetchall()
    gaps = conn.execute(
        "SELECT pillar_slug, title, phase FROM zig_capabilities "
        "WHERE implementation_status='not_started' ORDER BY pillar_slug LIMIT 10"
    ).fetchall()
    conn.close()
    overall = round(sum(float(p["score"]) for p in pillars) / len(pillars) * 100, 1) if pillars else 0
    rendered = render_template(
        "reports/zig_maturity.html", pillars=pillars, gaps=gaps, overall=overall,
    )
    narrative = _ai_digest_narrative("ZIG zero-trust maturity report", {
        "overall_pct": overall,
        "pillar_count": len(pillars),
        "lowest_pillars": "; ".join(
            f"{p['pillar_slug']}: {round(float(p['score'] or 0) * 100, 1)}% ({p['maturity_level']})"
            for p in pillars[:3]
        ) or "none",
        "not_started_capabilities": "; ".join(
            f"{g['pillar_slug']}/{g['title']}" for g in gaps[:5]
        ) or "none",
    }) if ai_narrative else None
    sendmail(to=recipient, subject=f"ZIG Maturity Report — {overall}% overall", html=rendered)
    notify("security-ops", f"ZIG maturity report sent: {overall}%")
    return {"status": "sent", "recipient": recipient, "overall_pct": overall, "pillars": len(pillars), "narrative": narrative}


def send_poam_due_soon_digest(recipient: str, days_ahead: int = 30, ai_narrative: bool = False) -> dict:
    """Fetch upcoming POA&M deadlines, render digest, and deliver."""
    conn = get_connection()
    due_limit = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).date().isoformat()
    due_items = conn.execute(
        "SELECT id, title, severity, due_date, owner, status, "
        "CAST(julianday(due_date) - julianday('now') AS INTEGER) as days_left "
        "FROM poam_items WHERE status='open' "
        "AND due_date <= %s ORDER BY due_date", (due_limit,)
    ).fetchall()
    overdue = conn.execute(
        "SELECT COUNT(*) as cnt FROM poam_items WHERE status='open' AND due_date < DATE('now')"
    ).fetchone()
    conn.close()
    overdue_count = int((overdue or {}).get("cnt", 0))
    rendered = render_template(
        "alerts/poam_due_soon.html",
        due_items=due_items, overdue_count=overdue_count, days_ahead=days_ahead,
    )
    narrative = _ai_digest_narrative("POA&M due-soon digest", {
        "days_ahead": days_ahead,
        "due_count": len(due_items),
        "overdue_count": overdue_count,
        "soonest_due": "; ".join(
            f"{d['title']} ({d['severity']}, {d['days_left']}d, owner {d['owner']})"
            for d in due_items[:5]
        ) or "none",
    }) if ai_narrative else None
    sendmail(to=recipient, subject=f"POA&M Digest — {len(due_items)} due in {days_ahead}d", html=rendered)
    payload = {"due_count": len(due_items), "overdue": overdue_count}
    if narrative:
        payload["narrative"] = narrative
    emit("poam.digest", payload)
    return {"status": "sent", "recipient": recipient, "due_count": len(due_items), "overdue": overdue_count, "narrative": narrative}


def send_audit_trail_summary(recipient: str, since_hours: int = 24, ai_narrative: bool = False) -> dict:
    """Fetch recent audit trail events, render activity summary, and deliver."""
    conn = get_connection()
    since = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
    events = conn.execute(
        "SELECT resource_type, event, actor, COUNT(*) as cnt "
        "FROM audit_trail WHERE created_at >= %s "
        "GROUP BY resource_type, event, actor ORDER BY cnt DESC LIMIT 20", (since,)
    ).fetchall()
    actors = conn.execute(
        "SELECT actor, COUNT(*) as cnt FROM audit_trail "
        "WHERE created_at >= %s GROUP BY actor ORDER BY cnt DESC", (since,)
    ).fetchall()
    conn.close()
    total_events = sum(int(e["cnt"]) for e in events)
    rendered = render_template(
        "reports/audit_summary.html",
        events=events, actors=actors, total=total_events, hours=since_hours,
    )
    narrative = _ai_digest_narrative("audit trail activity summary", {
        "window_hours": since_hours,
        "total_events": total_events,
        "top_event_types": "; ".join(
            f"{e['resource_type']}/{e['event']} by {e['actor']}: {e['cnt']}" for e in events[:5]
        ) or "none",
        "top_actors": "; ".join(f"{a['actor']}: {a['cnt']}" for a in actors[:5]) or "none",
    }) if ai_narrative else None
    send(to=recipient, subject=f"Audit Summary ({since_hours}h) — {total_events} events", body=rendered)
    payload = {"total_events": total_events, "since_hours": since_hours}
    if narrative:
        payload["narrative"] = narrative
    publish("audit.summary", payload)
    return {"status": "sent", "recipient": recipient, "event_count": total_events, "narrative": narrative}


def send_aiify_weekly_digest(recipient: str, ai_narrative: bool = False) -> dict:
    """Fetch weekly AI-ify scan activity, render opportunity digest, and deliver."""
    conn = get_connection()
    scans = conn.execute(
        "SELECT scan_id, status, overall_ai_readiness, created_at FROM aiify_scans "
        "WHERE created_at >= DATE('now','-7 days') ORDER BY created_at DESC"
    ).fetchall()
    top_opps = conn.execute(
        "SELECT o.function_name, o.pattern_type, s.composite_score "
        "FROM aiify_scores s JOIN aiify_opportunities o ON o.opportunity_id = s.opportunity_id "
        "WHERE o.scan_id IN ("
        "  SELECT scan_id FROM aiify_scans WHERE created_at >= DATE('now','-7 days')"
        ") ORDER BY s.composite_score DESC LIMIT 10"
    ).fetchall()
    pattern_summary = conn.execute(
        "SELECT pattern_type, COUNT(*) as cnt FROM aiify_opportunities "
        "WHERE scan_id IN ("
        "  SELECT scan_id FROM aiify_scans WHERE created_at >= DATE('now','-7 days')"
        ") GROUP BY pattern_type ORDER BY cnt DESC"
    ).fetchall()
    conn.close()
    avg_readiness = round(
        sum(float(s["overall_ai_readiness"] or 0) for s in scans) / len(scans) * 100, 1
    ) if scans else 0.0
    rendered = render_template(
        "digests/aiify_weekly.html",
        scans=scans, top_opps=top_opps, pattern_summary=pattern_summary,
        avg_readiness=avg_readiness,
    )
    narrative = _ai_digest_narrative("AI-ify weekly opportunity digest", {
        "scan_count": len(scans),
        "avg_readiness_pct": avg_readiness,
        "top_patterns": "; ".join(
            f"{p['pattern_type']}: {p['cnt']}" for p in pattern_summary[:5]
        ) or "none",
        "top_opportunities": "; ".join(
            f"{o['function_name']} ({o['pattern_type']}, score {round(float(o['composite_score'] or 0), 2)})"
            for o in top_opps[:5]
        ) or "none",
    }) if ai_narrative else None
    sendmail(
        to=recipient,
        subject=f"AI-ify Weekly Digest — {len(scans)} scans, {avg_readiness}% avg readiness",
        html=rendered,
    )
    payload = {"scan_count": len(scans), "avg_readiness_pct": avg_readiness, "recipient": recipient}
    if narrative:
        payload["narrative"] = narrative
    emit("aiify.weekly_digest_sent", payload)
    return {
        "status": "sent",
        "recipient": recipient,
        "scan_count": len(scans),
        "avg_readiness_pct": avg_readiness,
        "narrative": narrative,
    }
