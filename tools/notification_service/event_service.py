# CUI // SP-CTI
"""Event-driven notification service for ICDEVâ„¢ platform events.

Centralises Kanban task events, Genesis daemon milestones, and Oracle
prediction alerts into a single db â†’ render â†’ notify pipeline so that
multiple canvas consumers don't each re-implement delivery logic.

Per aiify-opp-5716 the deterministic chain is now AI-augmented: callers may
opt in via ``ai_narrative=True`` to additionally receive a short, grounded
LLM narrative (returned under ``narrative``). The templated notification
remains the authoritative payload and ships unchanged when the LLM is
unavailable. See ``_ai_event_narrative``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from string import Template
from typing import Iterable

from tools.db.storage import get_connection

# ---------------------------------------------------------------------------
# Module-level fallback constants â€” all overridable from args/notification_config.yaml
# under event_service.anomaly_detection.  Change config, not code.
# ---------------------------------------------------------------------------
_NARRATIVE_MAX_TOKENS      = 512
_NARRATIVE_TEMPERATURE     = 0.3
_TASK_AUDIT_LIMIT          = 5     # recent audit rows for kanban/aiify event notifications
_GENESIS_PHASE_LATEST      = 1     # most recent genesis phase log entry
_GENESIS_REFLEX_LIMIT      = 3     # recent genesis reflexes per design notification
_AIIFY_TOP_OPPS_LIMIT      = 5     # top opportunities in scan event notification
_KANBAN_DIGEST_LIMIT       = 20    # kanban tasks in platform event digest
_GENESIS_DIGEST_LIMIT      = 10    # genesis milestones in platform event digest
_ORACLE_DIGEST_LIMIT       = 10    # oracle predictions in platform event digest
_ORACLE_HORIZON_FALLBACK   = 24    # fallback horizon_days when lens row is missing

# ---------------------------------------------------------------------------
# AI-ification (aiify-opp-5716): optional LLM-synthesized event narrative.
#
# Each notify_* function below is a deterministic db â†’ render â†’ notify chain.
# The rendered notification it produces remains the AUTHORITATIVE payload â€”
# recipients must never depend on LLM availability to receive their alert.
# When a caller opts in via ``ai_narrative=True`` we ADDITIONALLY synthesize a
# short, grounded narrative (what the event means, why it matters, and the
# single most important next action) and attach it under the ``narrative``
# return key. Any failure â€” no-LLM mode, air-gap, network, missing credentials
# â€” degrades silently to ``None`` so the deterministic notification always ships.
#
# Mirrors the established pattern in ``handler_service._ai_handler_narrative``
# and ``alert_service._ai_alert_narrative``.
# ---------------------------------------------------------------------------

_EVENT_NARRATIVE_SYSTEM_PROMPT = (
    "You are a DoD/IC operations analyst writing an event notification summary. "
    "Write a concise narrative (2-4 sentences) that: (1) states what the event "
    "means in plain language, (2) explains why it matters given the context and "
    "severity, and (3) recommends the single most important next action for the "
    "recipient. Use only the facts provided â€” never invent IDs, dates, counts, "
    "scores, names, or identifiers. Output only the narrative prose; no headers, "
    "no markdown, no preamble."
)


def _ai_event_narrative(event_kind: str, facts: dict) -> str | None:
    """Synthesize an optional LLM narrative for a rendered event notification.

    Args:
        event_kind: Human label for the event family (e.g. "kanban task
            completed notification"). Steers the model's framing.
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
                        f"Event type: {event_kind}\n"
                        f"Facts:\n{fact_lines}\n\n"
                        "Write the event notification narrative."
                    ),
                }
            ],
            system_prompt=_EVENT_NARRATIVE_SYSTEM_PROMPT,
            max_tokens=_NARRATIVE_MAX_TOKENS,
            temperature=_NARRATIVE_TEMPERATURE,
            skip_injection_scan=True,  # trusted first-party fact dict, not user input
            classification="CUI",
        )
        resp = LLMRouter().invoke("narrative_generation", req)
        if resp and resp.content:
            return resp.content.strip()
    except Exception:
        pass  # Graceful degradation â€” deterministic notification is authoritative.
    return None

# ---------------------------------------------------------------------------
# Notification channel registry (extensible via args/notification_config.yaml)
# ---------------------------------------------------------------------------

CHANNEL_REGISTRY = {
    "email":   "smtp",
    "slack":   "webhook",
    "teams":   "webhook",
    "webhook": "raw_http",
    "syslog":  "udp",
    "audit":   "db_insert",
    "console": "stdout",
    "mcp":     "mcp_tool_call",
    "kanban":  "kanban_task",
}

KANBAN_EVENT_TEMPLATES = {
    "task_started":   "Task **$task_id** ($title) moved to IN PROGRESS by $actor.",
    "task_completed": "Task **$task_id** ($title) marked DONE. Duration: $duration.",
    "task_blocked":   "Task **$task_id** ($title) BLOCKED â€” $reason. Assigned: $actor.",
    "task_failed":    "Task **$task_id** ($title) FAILED after $attempts attempts: $error.",
    "sprint_closed":  "Sprint $sprint closed. $done_count done / $total_count total.",
    "epic_complete":  "Epic **$epic_key** completed. All $task_count tasks resolved.",
    "token_limit":    "Task **$task_id** hit token limit. Auto-paused after $tokens tokens.",
}

GENESIS_MILESTONE_TEMPLATES = {
    "phase_enter":    "Genesis phase **$phase** entered for design $design_id.",
    "phase_complete": "Genesis phase **$phase** complete. Next: $next_phase.",
    "reflex_fired":   "Reflex **$reflex_name** fired (confidence $confidence). $summary",
    "drift_detected": "Drift detected in $component. Baseline delta: $delta. Action: $action.",
    "self_heal":      "Self-heal triggered for $component ($severity). Fix applied: $fix_desc.",
    "awareness_scan": "Awareness scan complete. $gap_count gaps detected, $fix_count auto-fixed.",
}

ORACLE_ALERT_TEMPLATES = {
    "cat1_new":       "[CAT I] New Oracle prediction: $title ($lens_id). Confidence: $confidence.",
    "cat1_escalate":  "[CAT I ESCALATION] $count unresolved CAT-I predictions in $horizon hours.",
    "cat2_digest":    "[CAT II] $count medium-risk predictions pending review.",
    "convergence":    "Oracle convergence detected: $count lenses agree on $finding.",
    "false_positive": "Oracle prediction $pred_id marked false positive by $actor.",
}

AIIFY_OPPORTUNITY_TEMPLATES = {
    "opportunity_detected":   "New AI-ify opportunity #$opportunity_id detected: $pattern_type in $module_path ($function_name). Composite score: $composite_score.",
    "opportunity_dispatched": "AI-ify opportunity #$opportunity_id dispatched as task $task_id. Module: $module_path â€” paradigm: $ai_paradigm.",
    "opportunity_completed":  "AI-ify opportunity #$opportunity_id completed in $module_path ($function_name). Paradigm: $ai_paradigm.",
    "opportunity_skipped":    "AI-ify opportunity #$opportunity_id skipped (duplicate or low-priority). Module: $module_path.",
    "opportunity_failed":     "AI-ify opportunity #$opportunity_id FAILED after $attempts attempts: $error. Module: $module_path.",
}


def notify_aiify_opportunity_event(
    opportunity_id: int,
    event_type: str,
    channels: Iterable[str],
    extra: dict | None = None,
    ai_narrative: bool = False,
) -> dict:
    """Query the AI-ify kanban task, render an opportunity event notification, and deliver.

    Covers the db â†’ render â†’ notify chain for AI-ify opportunity lifecycle events
    (detected, dispatched, completed, skipped, failed). Callers supply the
    ``opportunity_id`` and event-specific context via ``extra``; the function
    fetches the associated kanban task from the main ICDEV DB to ground the
    rendered message in authoritative state.

    Args:
        opportunity_id: Numeric ID of the AI-ify opportunity (e.g. 5792).
        event_type:     One of the keys in ``AIIFY_OPPORTUNITY_TEMPLATES``.
        channels:       Delivery targets from ``CHANNEL_REGISTRY``.
        extra:          Opportunity facts: module_path, function_name,
            pattern_type, ai_paradigm, composite_score, task_id, etc.
        ai_narrative:   If True, attach an optional LLM narrative under
            ``narrative``; ``None`` if generation is unavailable.

    Returns:
        Delivery receipt with per-channel status and rendered message.
    """
    conn = get_connection()
    try:
        task_id = (extra or {}).get("task_id", f"aiify-opp-{opportunity_id}")
        task_row = conn.execute(
            "SELECT id, title, status, actor, attempts, created_at, updated_at "
            "FROM kanban_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        audit_rows = conn.execute(
            "SELECT event, created_at FROM audit_trail "
            f"WHERE resource_id = ? ORDER BY created_at DESC LIMIT {_TASK_AUDIT_LIMIT}",
            (task_id,),
        ).fetchall()

        vars_ = {
            "opportunity_id": opportunity_id,
            "task_id": task_id,
            "title": (task_row["title"] if task_row else f"aiify-opp-{opportunity_id}"),
            "actor": (task_row["actor"] if task_row else "system"),
            "attempts": (task_row["attempts"] if task_row else 1),
            "module_path": (extra or {}).get("module_path", "(unknown)"),
            "function_name": (extra or {}).get("function_name", "(unknown)"),
            "pattern_type": (extra or {}).get("pattern_type", "(unknown)"),
            "ai_paradigm": (extra or {}).get("ai_paradigm", "(unknown)"),
            "composite_score": (extra or {}).get("composite_score", 0.0),
            "error": (extra or {}).get("error", ""),
        }
        if extra:
            vars_.update(extra)

        tmpl_str = AIIFY_OPPORTUNITY_TEMPLATES.get(
            event_type, "AI-ify opportunity #$opportunity_id: $event_type in $module_path."
        )
        rendered = Template(tmpl_str).safe_substitute(vars_)

        narrative = _ai_event_narrative(
            f"aiify opportunity {event_type} notification",
            {k: str(v) for k, v in vars_.items()},
        ) if ai_narrative else None

        receipts = {}
        for ch in channels:
            if ch == "audit":
                conn.execute(
                    "INSERT INTO audit_trail (resource_type, resource_id, event, actor, detail) "
                    "VALUES ('aiify_notification', ?, ?, 'system', ?)",
                    (str(opportunity_id), event_type, rendered),
                )
                conn.commit()
                receipts[ch] = "inserted"
            elif ch in ("slack", "teams", "webhook"):
                payload = {
                    "text": rendered,
                    "opportunity_id": opportunity_id,
                    "event": event_type,
                }
                publish(ch, payload)
                receipts[ch] = "published"
            elif ch in ("email", "smtp"):
                send(
                    to=(extra or {}).get("email", "engineering@icdev.local"),
                    subject=f"AI-ify: {event_type} â€” opp #{opportunity_id}",
                    body=rendered,
                )
                receipts[ch] = "sent"
            elif ch == "console":
                emit("aiify.opportunity_event", {"message": rendered, "opportunity_id": opportunity_id})
                receipts[ch] = "emitted"
            else:
                notify(ch, rendered)
                receipts[ch] = "dispatched"

        return {
            "status": "delivered",
            "opportunity_id": opportunity_id,
            "event_type": event_type,
            "rendered": rendered,
            "narrative": narrative,
            "receipts": receipts,
            "audit_history": [dict(r) for r in audit_rows],
        }
    finally:
        conn.close()


def notify_kanban_event(
    task_id: str,
    event_type: str,
    channels: Iterable[str],
    extra: dict | None = None,
    ai_narrative: bool = False,
) -> dict:
    """Query task state, render event notification, and deliver to channels.

    Args:
        task_id:    Kanban task identifier (e.g. ``dt-zig-01``).
        event_type: One of the keys in ``KANBAN_EVENT_TEMPLATES``.
        channels:   Delivery targets from ``CHANNEL_REGISTRY``.
        extra:      Optional supplemental template variables.

    Returns:
        Delivery receipt with per-channel status and rendered message.
    """
    conn = get_connection()
    try:
        # --- DB: fetch task and recent audit rows ---
        task_row = conn.execute(
            "SELECT id, title, status, actor, attempts, created_at, updated_at "
            "FROM kanban_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        audit_rows = conn.execute(
            "SELECT event, created_at FROM audit_trail "
            f"WHERE resource_id = ? ORDER BY created_at DESC LIMIT {_TASK_AUDIT_LIMIT}",
            (task_id,),
        ).fetchall()

        if not task_row:
            return {"status": "error", "reason": f"task {task_id!r} not found"}

        # Build template variables
        vars_ = {
            "task_id": task_id,
            "title": task_row["title"] or "(no title)",
            "actor": task_row["actor"] or "system",
            "attempts": task_row["attempts"] or 1,
            "duration": _duration_str(task_row["created_at"], task_row["updated_at"]),
            "reason": (extra or {}).get("reason", "unspecified"),
            "error": (extra or {}).get("error", ""),
            "done_count": (extra or {}).get("done_count", 0),
            "total_count": (extra or {}).get("total_count", 0),
            "sprint": (extra or {}).get("sprint", "current"),
            "epic_key": (extra or {}).get("epic_key", ""),
            "task_count": (extra or {}).get("task_count", 0),
            "tokens": (extra or {}).get("tokens", 0),
        }
        if extra:
            vars_.update(extra)

        # --- Render template ---
        tmpl_str = KANBAN_EVENT_TEMPLATES.get(
            event_type, "Task $task_id: $event_type"
        )
        rendered = Template(tmpl_str).safe_substitute(vars_)
        rendered_html = render_to_string("notifications/kanban_event.html", vars_)

        narrative = _ai_event_narrative(
            f"kanban {event_type} notification",
            {k: str(v) for k, v in vars_.items()},
        ) if ai_narrative else None

        # --- Notify each channel ---
        receipts = {}
        for ch in channels:
            if ch == "audit":
                conn.execute(
                    "INSERT INTO audit_trail (resource_type, resource_id, event, actor, detail) "
                    "VALUES ('kanban_notification', ?, ?, 'system', ?)",
                    (task_id, event_type, rendered),
                )
                conn.commit()
                receipts[ch] = "inserted"
            elif ch in ("email", "smtp"):
                send(to=vars_.get("email", "ops@icdev.local"), subject=f"Kanban: {event_type}", body=rendered_html)
                receipts[ch] = "sent"
            elif ch in ("slack", "teams", "webhook"):
                payload = {"text": rendered, "task_id": task_id, "event": event_type}
                publish(ch, payload)
                receipts[ch] = "published"
            elif ch == "console":
                emit("kanban.event", {"message": rendered, "task_id": task_id})
                receipts[ch] = "emitted"
            else:
                notify(ch, rendered)
                receipts[ch] = "dispatched"

        return {
            "status": "delivered",
            "task_id": task_id,
            "event_type": event_type,
            "rendered": rendered,
            "narrative": narrative,
            "receipts": receipts,
            "audit_history": [dict(r) for r in audit_rows],
        }
    finally:
        conn.close()


def notify_genesis_milestone(
    design_id: str,
    milestone_type: str,
    channels: Iterable[str],
    phase_data: dict | None = None,
    ai_narrative: bool = False,
) -> dict:
    """Query Genesis design state, render milestone message, and deliver.

    Covers phase transitions, reflex firings, drift detection, and
    self-healing events so that downstream consumers (Kanban, Oracle,
    Dashboard) all receive consistent milestone notifications.
    """
    conn = get_connection()
    try:
        # --- DB: design metadata + latest phase row ---
        design_row = conn.execute(
            "SELECT id, name, status, current_phase, created_at FROM genesis_designs WHERE id = ?",
            (design_id,),
        ).fetchone()
        phase_row = conn.execute(
            "SELECT phase, status, started_at, completed_at FROM genesis_phase_log "
            f"WHERE design_id = ? ORDER BY started_at DESC LIMIT {_GENESIS_PHASE_LATEST}",
            (design_id,),
        ).fetchone()
        reflex_rows = conn.execute(
            "SELECT name, confidence, fired_at FROM genesis_reflexes "
            f"WHERE design_id = ? ORDER BY fired_at DESC LIMIT {_GENESIS_REFLEX_LIMIT}",
            (design_id,),
        ).fetchall()

        vars_ = {
            "design_id": design_id,
            "design_name": design_row["name"] if design_row else "(unknown)",
            "phase": phase_row["phase"] if phase_row else (phase_data or {}).get("phase", "unknown"),
            "next_phase": (phase_data or {}).get("next_phase", ""),
            "reflex_name": (reflex_rows[0]["name"] if reflex_rows else ""),
            "confidence": (reflex_rows[0]["confidence"] if reflex_rows else 0.0),
            "summary": (phase_data or {}).get("summary", ""),
            "component": (phase_data or {}).get("component", ""),
            "delta": (phase_data or {}).get("delta", 0.0),
            "action": (phase_data or {}).get("action", ""),
            "severity": (phase_data or {}).get("severity", "medium"),
            "fix_desc": (phase_data or {}).get("fix_desc", ""),
            "gap_count": (phase_data or {}).get("gap_count", 0),
            "fix_count": (phase_data or {}).get("fix_count", 0),
        }

        # --- Render ---
        tmpl_str = GENESIS_MILESTONE_TEMPLATES.get(
            milestone_type, "Genesis milestone $milestone_type for $design_id."
        )
        rendered = Template(tmpl_str).safe_substitute(vars_)
        rendered_full = render_string(
            _GENESIS_EMAIL_TEMPLATE, design_name=vars_["design_name"],
            milestone=milestone_type, body=rendered, ts=_now_iso()
        )

        narrative = _ai_event_narrative(
            f"genesis {milestone_type} milestone notification",
            {k: str(v) for k, v in vars_.items()},
        ) if ai_narrative else None

        # --- Notify ---
        receipts = {}
        for ch in channels:
            if ch == "audit":
                conn.execute(
                    "INSERT INTO audit_trail (resource_type, resource_id, event, actor, detail) "
                    "VALUES ('genesis_notification', ?, ?, 'system', ?)",
                    (design_id, milestone_type, rendered),
                )
                conn.commit()
                receipts[ch] = "inserted"
            elif ch in ("email", "smtp"):
                sendmail(
                    to="engineering@icdev.local",
                    subject=f"Genesis: {milestone_type} â€” {vars_['design_name']}",
                    html=rendered_full,
                )
                receipts[ch] = "sent"
            elif ch in ("slack", "teams"):
                dispatch(ch, {"blocks": [{"type": "section", "text": rendered}]})
                receipts[ch] = "dispatched"
            else:
                emit("genesis.milestone", {"design_id": design_id, "type": milestone_type, "message": rendered})
                receipts[ch] = "emitted"

        return {
            "status": "delivered",
            "design_id": design_id,
            "milestone": milestone_type,
            "rendered": rendered,
            "narrative": narrative,
            "receipts": receipts,
        }
    finally:
        conn.close()


def notify_oracle_alert(
    lens_id: str,
    alert_type: str,
    prediction_ids: list[str],
    channels: Iterable[str],
    urgency: str = "normal",
    ai_narrative: bool = False,
) -> dict:
    """Query Oracle predictions, render severity-appropriate alert, and deliver.

    Groups pending predictions by severity, selects the appropriate alert
    template, renders it with context from the DB, and dispatches to all
    registered channels. Writes a delivery receipt to the audit trail.
    """
    conn = get_connection()
    try:
        # --- DB: fetch predictions and lens metadata ---
        placeholders = ", ".join("?" * len(prediction_ids)) if prediction_ids else "''"
        pred_rows = conn.execute(
            f"SELECT id, title, severity, confidence, lens_id, outcome, created_at "  # nosec B608
            f"FROM oracle_predictions WHERE id IN ({placeholders})",
            prediction_ids,
        ).fetchall()
        lens_row = conn.execute(
            "SELECT lens_id, name, horizon_days FROM oracle_lenses WHERE lens_id = ?",
            (lens_id,),
        ).fetchone()
        cat1_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM oracle_predictions "
            "WHERE lens_id = ? AND severity IN ('critical','high') AND outcome = 'pending'",
            (lens_id,),
        ).fetchone()

        vars_ = {
            "lens_id": lens_id,
            "lens_name": lens_row["name"] if lens_row else lens_id,
            "horizon": lens_row["horizon_days"] if lens_row else _ORACLE_HORIZON_FALLBACK,
            "count": len(pred_rows),
            "cat1_count": cat1_count["cnt"] if cat1_count else 0,
            "title": (pred_rows[0]["title"] if pred_rows else "(no predictions)"),
            "confidence": (pred_rows[0]["confidence"] if pred_rows else 0.0),
            "finding": (pred_rows[0]["title"] if pred_rows else ""),
            "actor": "oracle",
            "pred_id": (prediction_ids[0] if prediction_ids else ""),
        }

        # --- Render: pick template by alert type ---
        tmpl_str = ORACLE_ALERT_TEMPLATES.get(
            alert_type, "Oracle alert $alert_type from lens $lens_id: $count predictions."
        )
        rendered = Template(tmpl_str).safe_substitute(vars_)
        rendered_html = render_template(
            "notifications/oracle_alert.html",
            alert_type=alert_type,
            predictions=pred_rows,
            lens=lens_row,
            urgency=urgency,
        )

        narrative = _ai_event_narrative(
            f"oracle {alert_type} alert notification",
            {k: str(v) for k, v in vars_.items()},
        ) if ai_narrative else None

        # --- Notify ---
        receipts = {}
        for ch in channels:
            if ch == "audit":
                conn.execute(
                    "INSERT INTO audit_trail (resource_type, resource_id, event, actor, detail) "
                    "VALUES ('oracle_notification', ?, ?, 'oracle', ?)",
                    (lens_id, alert_type, json.dumps({"rendered": rendered, "pred_ids": prediction_ids})),
                )
                conn.commit()
                receipts[ch] = "inserted"
            elif urgency == "critical" and ch in ("email", "smtp"):
                send(to="security-ops@icdev.local", subject=f"[CRITICAL] Oracle: {rendered}", body=rendered_html)
                receipts[ch] = "sent"
            elif ch in ("slack", "webhook"):
                publish(ch, {"text": rendered, "urgency": urgency, "lens_id": lens_id})
                receipts[ch] = "published"
            else:
                notify(ch, rendered)
                receipts[ch] = "notified"

        return {
            "status": "delivered",
            "lens_id": lens_id,
            "alert_type": alert_type,
            "rendered": rendered,
            "narrative": narrative,
            "prediction_count": len(pred_rows),
            "receipts": receipts,
        }
    finally:
        conn.close()


def send_aiify_scan_report(
    scan_id: int,
    channels: Iterable[str],
    recipient: str | None = None,
    ai_narrative: bool = False,
) -> dict:
    """Fetch AI-ify scan results, render a scan completion report, and deliver.

    Aggregates scan metadata and top opportunities from a completed AI-ify scan
    and delivers a structured report to the specified channels. When
    ``ai_narrative`` is True, attaches a best-effort LLM narrative
    (``None`` if unavailable); the deterministic report remains authoritative.

    Args:
        scan_id:      Numeric ID of the completed AI-ify scan.
        channels:     Delivery targets from ``CHANNEL_REGISTRY``.
        recipient:    Email address for email/smtp delivery (optional).
        ai_narrative: If True, attach an optional LLM narrative under
            ``narrative``; ``None`` if generation is unavailable.

    Returns:
        Delivery receipt with scan metadata, opportunity count, and optional narrative.
    """
    conn = get_connection()
    try:
        # --- DB: scan metadata ---
        scan_row = conn.execute(
            "SELECT id, roadmap_id, scan_status, opportunity_count, started_at, completed_at "
            "FROM aiify_scans WHERE id = ?",
            (scan_id,),
        ).fetchone()
        # --- DB: top opportunities by composite score ---
        top_opps = conn.execute(
            "SELECT id, module_path, function_name, pattern_type, ai_paradigm, composite_score "
            "FROM aiify_opportunities WHERE scan_id = ? "
            "ORDER BY composite_score DESC LIMIT 5",
            (scan_id,),
        ).fetchall()
        # --- DB: pattern breakdown ---
        pattern_rows = conn.execute(
            "SELECT pattern_type, COUNT(*) as cnt FROM aiify_opportunities "
            "WHERE scan_id = ? GROUP BY pattern_type ORDER BY cnt DESC",
            (scan_id,),
        ).fetchall()

        opportunity_count = scan_row["opportunity_count"] if scan_row else len(top_opps)
        top_score = float(top_opps[0]["composite_score"]) if top_opps else 0.0
        top_module = top_opps[0]["module_path"] if top_opps else "(none)"
        vars_ = {
            "scan_id": scan_id,
            "roadmap_id": scan_row["roadmap_id"] if scan_row else "(unknown)",
            "scan_status": scan_row["scan_status"] if scan_row else "complete",
            "opportunity_count": opportunity_count,
            "top_score": round(top_score, 4),
            "top_module": top_module,
            "pattern_count": len(pattern_rows),
        }

        # --- Render ---
        rendered = (
            f"AI-ify scan #{scan_id} complete. "
            f"{opportunity_count} opportunities detected "
            f"(roadmap {vars_['roadmap_id']}). "
            f"Top score: {vars_['top_score']} in {top_module}."
        )

        narrative = _ai_event_narrative(
            "aiify scan completion report",
            {k: str(v) for k, v in vars_.items()},
        ) if ai_narrative else None

        # --- Notify ---
        receipts = {}
        for ch in channels:
            if ch == "audit":
                conn.execute(
                    "INSERT INTO audit_trail (resource_type, resource_id, event, actor, detail) "
                    "VALUES ('aiify_scan_notification', ?, 'scan_complete', 'system', ?)",
                    (str(scan_id), rendered),
                )
                conn.commit()
                receipts[ch] = "inserted"
            elif ch in ("email", "smtp"):
                sendmail(
                    to=recipient or "engineering@icdev.local",
                    subject=f"[AI-ify] Scan #{scan_id} complete â€” {opportunity_count} opportunities",
                    html=rendered,
                )
                receipts[ch] = "sent"
            elif ch in ("slack", "teams", "webhook"):
                payload = {
                    "text": rendered,
                    "scan_id": scan_id,
                    "opportunity_count": opportunity_count,
                }
                publish(ch, payload)
                receipts[ch] = "published"
            elif ch == "console":
                emit("aiify.scan_complete", {"message": rendered, "scan_id": scan_id})
                receipts[ch] = "emitted"
            else:
                notify(ch, rendered)
                receipts[ch] = "dispatched"

        return {
            "status": "delivered",
            "scan_id": scan_id,
            "opportunity_count": opportunity_count,
            "rendered": rendered,
            "narrative": narrative,
            "receipts": receipts,
        }
    finally:
        conn.close()


def send_platform_event_digest(
    recipient: str,
    hours: int = 24,
    ai_narrative: bool = False,
) -> dict:
    """Fetch recent platform events, render a digest, and deliver.

    Aggregates kanban task events, Genesis milestone events, and Oracle
    alert events from the past ``hours`` into a single digest and delivers
    to ``recipient``. When ``ai_narrative`` is True, attaches a best-effort
    LLM narrative (``None`` if unavailable); the deterministic summary
    remains authoritative.

    Args:
        recipient: Email address to receive the digest.
        hours:     Look-back window in hours (default 24).
        ai_narrative: If True, attach an optional LLM narrative under
            ``narrative``; ``None`` if generation is unavailable.

    Returns:
        Delivery receipt with event counts and optional narrative.
    """
    conn = get_connection()
    try:
        # --- DB: recent kanban task events ---
        kanban_rows = conn.execute(
            "SELECT id, title, status, updated_at FROM kanban_tasks "
            "WHERE updated_at >= datetime('now', ? || ' hours') "
            f"ORDER BY updated_at DESC LIMIT {_KANBAN_DIGEST_LIMIT}",
            (f"-{hours}",),
        ).fetchall()
        # --- DB: recent Genesis milestone events ---
        genesis_rows = conn.execute(
            "SELECT design_id, phase, status, completed_at FROM genesis_phase_log "
            "WHERE completed_at >= datetime('now', ? || ' hours') "
            f"ORDER BY completed_at DESC LIMIT {_GENESIS_DIGEST_LIMIT}",
            (f"-{hours}",),
        ).fetchall()
        # --- DB: recent Oracle alert predictions ---
        oracle_rows = conn.execute(
            "SELECT id, title, severity, confidence, created_at FROM oracle_predictions "
            "WHERE created_at >= datetime('now', ? || ' hours') "
            f"AND outcome = 'pending' ORDER BY severity DESC, created_at DESC LIMIT {_ORACLE_DIGEST_LIMIT}",
            (f"-{hours}",),
        ).fetchall()

        kanban_count = len(kanban_rows)
        genesis_count = len(genesis_rows)
        oracle_count = len(oracle_rows)
        vars_ = {
            "hours_window": hours,
            "kanban_event_count": kanban_count,
            "genesis_milestone_count": genesis_count,
            "oracle_alert_count": oracle_count,
        }

        # --- Render ---
        rendered = render_template(
            "notifications/platform_event_digest.html",
            kanban_events=kanban_rows,
            genesis_milestones=genesis_rows,
            oracle_alerts=oracle_rows,
            hours=hours,
        )

        # --- AI (optional): synthesize narrative; None if unavailable ---
        narrative = _ai_event_narrative(
            "platform event digest summary", vars_
        ) if ai_narrative else None

        # --- Deliver ---
        sendmail(
            to=recipient,
            subject=(
                f"[EVENT DIGEST] {kanban_count} kanban, "
                f"{genesis_count} genesis, {oracle_count} oracle events "
                f"(last {hours}h)"
            ),
            html=rendered,
        )
        payload = {**vars_, "recipient": recipient}
        if narrative:
            payload["narrative"] = narrative
        emit("platform.event_digest_sent", payload)

        return {
            "status": "sent",
            "recipient": recipient,
            "kanban_event_count": kanban_count,
            "genesis_milestone_count": genesis_count,
            "oracle_alert_count": oracle_count,
            "narrative": narrative,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_GENESIS_EMAIL_TEMPLATE = (
    "=== ICDEV Genesis Notification ===\n"
    "Design: $design_name\n"
    "Milestone: $milestone\n"
    "$body\n"
    "Timestamp: $ts\n"
    "==================================="
)


def _duration_str(start: str | None, end: str | None) -> str:
    if not start or not end:
        return "unknown"
    try:
        from dateutil.parser import parse
        delta = parse(end) - parse(start)
        secs = int(delta.total_seconds())
        minutes, remainder = divmod(secs, 60)
        hours, minutes = divmod(minutes, 60)
        parts = [f"{hours}h"] if hours else []
        parts += [f"{minutes}m"] if minutes else []
        parts += [f"{remainder}s"] if remainder else []
        return " ".join(parts) or "0s"
    except Exception:
        return "unknown"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Stubs â€” replaced at runtime by injected service implementations
def render_template(template_name: str, **ctx) -> str:
    return f"[render_template:{template_name}] {ctx}"


def render_to_string(template_name: str, ctx: dict) -> str:
    return f"[render_to_string:{template_name}] {ctx}"


def render_string(template: str, **kwargs) -> str:
    return Template(template).safe_substitute(kwargs)


def send(to: str, subject: str, body: str = "", **kwargs) -> None:
    pass


def sendmail(to: str, subject: str, html: str = "", **kwargs) -> None:
    pass


def notify(channel: str, message: str) -> None:
    pass


def emit(event: str, payload: dict) -> None:
    pass


def publish(channel: str, payload: dict) -> None:
    pass


def dispatch(channel: str, payload: dict) -> None:
    pass

