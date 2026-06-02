# CUI // SP-CTI
"""Handler-layer notification service for ICDEV™ — pure db→render→notify chains.

Each function follows the single-concern pattern: query the DB for context,
render the message, and send/notify. Connection lifecycle is managed with
try/finally so render and notify always execute while the connection is open.
"""

from __future__ import annotations

from tools.db.storage import get_connection
from .event_service import (
    render_template, render_to_string,
    send, sendmail, notify, emit, publish, dispatch,
)


def handle_task_status_change_notify(task_id: str, to_status: str, recipient: str) -> dict:
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
        send(to=recipient, subject="Task Status Update", body=rendered)
        emit("task.status_changed", {"task_id": task_id, "status": to_status})
        return {"status": "sent", "task_id": task_id, "recipient": recipient}
    finally:
        conn.close()


def handle_canvas_assessment_handler(canvas_id: str, recipient: str) -> dict:
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
        sendmail(to=recipient, subject="Canvas Assessment Complete", html=rendered)
        notify("compliance", rendered)
        return {"status": "sent", "canvas_id": canvas_id}
    finally:
        conn.close()


def handle_oracle_prediction_handler(prediction_id: str, recipient: str) -> dict:
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
        send(to=recipient, subject="Oracle Prediction Alert", body=rendered)
        publish("oracle.alert", {"prediction_id": prediction_id})
        return {"status": "sent", "prediction_id": prediction_id}
    finally:
        conn.close()


def handle_genesis_reflex_handler(reflex_id: str, design_id: str, recipient: str) -> dict:
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
        sendmail(to=recipient, subject="Genesis Reflex Fired", html=rendered)
        emit("genesis.reflex", {"reflex_id": reflex_id, "design_id": design_id})
        return {"status": "sent", "reflex_id": reflex_id}
    finally:
        conn.close()


def handle_stig_finding_handler(check_id: str, workload_id: str, recipient: str) -> dict:
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
        send(to=recipient, subject="STIG Finding Notification", body=rendered)
        dispatch("stig.finding", {"check_id": check_id, "workload_id": workload_id})
        return {"status": "sent", "check_id": check_id}
    finally:
        conn.close()


def handle_poam_deadline_handler(poam_id: str, project_id: str, recipient: str) -> dict:
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
        sendmail(to=(owner or {}).get("email", recipient), subject="POA&M Deadline Reminder", html=rendered)
        notify("compliance-ops", rendered)
        return {"status": "sent", "poam_id": poam_id}
    finally:
        conn.close()


def handle_zig_pillar_handler(pillar_slug: str, recipient: str) -> dict:
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
        send(to=recipient, subject="ZIG Pillar Maturity Update", body=rendered)
        publish("zig.pillar_update", {"pillar_slug": pillar_slug})
        return {"status": "sent", "pillar_slug": pillar_slug}
    finally:
        conn.close()


def handle_agent_incident_handler(agent_id: str, incident_type: str, recipient: str) -> dict:
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
        sendmail(to=recipient, subject="Agent Incident Alert", html=rendered)
        emit("agent.incident", {"agent_id": agent_id, "type": incident_type})
        return {"status": "sent", "agent_id": agent_id}
    finally:
        conn.close()
