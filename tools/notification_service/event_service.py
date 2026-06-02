# CUI // SP-CTI
"""Event-driven notification service for ICDEV™ platform events.

Centralises Kanban task events, Genesis daemon milestones, and Oracle
prediction alerts into a single db → render → notify pipeline so that
multiple canvas consumers don't each re-implement delivery logic.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from string import Template
from typing import Iterable

from tools.db.storage import get_connection

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
    "task_blocked":   "Task **$task_id** ($title) BLOCKED — $reason. Assigned: $actor.",
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


def notify_kanban_event(
    task_id: str,
    event_type: str,
    channels: Iterable[str],
    extra: dict | None = None,
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
            "WHERE resource_id = ? ORDER BY created_at DESC LIMIT 5",
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
            "WHERE design_id = ? ORDER BY started_at DESC LIMIT 1",
            (design_id,),
        ).fetchone()
        reflex_rows = conn.execute(
            "SELECT name, confidence, fired_at FROM genesis_reflexes "
            "WHERE design_id = ? ORDER BY fired_at DESC LIMIT 3",
            (design_id,),
        ).fetchall()

        vars_ = {
            "design_id": design_id,
            "design_name": (design_row or {}).get("name", "(unknown)"),
            "phase": (phase_row or {}).get("phase", (phase_data or {}).get("phase", "unknown")),
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
                    subject=f"Genesis: {milestone_type} — {vars_['design_name']}",
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
            "lens_name": (lens_row or {}).get("name", lens_id),
            "horizon": (lens_row or {}).get("horizon_days", 24),
            "count": len(pred_rows),
            "cat1_count": (cat1_count or {}).get("cnt", 0),
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
            "prediction_count": len(pred_rows),
            "receipts": receipts,
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


# Stubs — replaced at runtime by injected service implementations
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
