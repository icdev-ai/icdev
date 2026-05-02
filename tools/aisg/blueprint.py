#!/usr/bin/env python3
# CUI // SP-CTI
"""AISG Flask Blueprint.

Routes:
  GET  /ai-wizard                           — AI Launchpad Wizard page
  GET  /ai-patterns                         — AI Pattern Library page
  GET  /api/aisg/patterns                   — List all patterns (JSON)
  GET  /api/aisg/patterns/<id>              — Get a single pattern (JSON)
  POST /api/aisg/patterns/<id>/deploy       — Increment deploy count (JSON)
  POST /api/aisg/patterns/seed              — Seed built-in patterns (JSON)
  GET  /api/explain/<event_id>              — Explain an audit trail event
  GET  /api/explain/heal/<heal_id>          — Explain a self-healing event
  GET  /api/explain/reflex/<reflex_name>    — Explain latest reflex run
"""
from __future__ import annotations

import json

from flask import Blueprint, jsonify, render_template, request

from tools.db.storage import get_connection

bp = Blueprint("aisg", __name__)


def _get_audit_event(event_id: int) -> dict | None:
    """Fetch a single audit_trail row by primary key."""
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            "SELECT id, event_type, actor, action, details, created_at "
            "FROM audit_trail WHERE id = %s",
            (event_id,),
        )
        row = c.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _get_heal_event(heal_id: int) -> dict | None:
    """Fetch a self_healing_events row by primary key."""
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            "SELECT id, trigger_source, pattern_description, action_taken, "
            "outcome, confidence, created_at FROM self_healing_events WHERE id = %s",
            (heal_id,),
        )
        row = c.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _get_reflex_latest(reflex_name: str) -> dict | None:
    """Fetch the most recent genesis_audit row for a given reflex name."""
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            "SELECT id, reflex_name, outcome, artifacts_produced, error_message, "
            "started_at, finished_at FROM genesis_audit "
            "WHERE reflex_name = %s ORDER BY started_at DESC LIMIT 1",
            (reflex_name,),
        )
        row = c.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


@bp.route("/api/explain/heal/<int:heal_id>", methods=["GET"])
def explain_heal(heal_id: int):
    """Return a plain-English explanation for a self-healing event."""
    row = _get_heal_event(heal_id)
    if row is None:
        return jsonify({"error": f"Self-healing event {heal_id} not found"}), 404

    outcome = row.get("outcome", "unknown")
    action = row.get("action_taken") or "an automated remediation"
    pattern = row.get("pattern_description") or "a detected anomaly"
    trigger = row.get("trigger_source") or "the system"
    confidence = row.get("confidence")
    conf_str = f" (confidence: {confidence:.0%})" if confidence else ""

    if outcome == "success":
        summary = (
            f"ICDEV™ detected {pattern} via {trigger} and automatically applied "
            f"{action}. The remediation succeeded{conf_str}."
        )
    elif outcome == "failed":
        summary = (
            f"ICDEV™ attempted to remediate {pattern} (triggered by {trigger}) "
            f"using {action}, but the action failed{conf_str}. Manual review is recommended."
        )
    else:
        summary = (
            f"{trigger} triggered a self-healing response for {pattern}. "
            f"Action: {action}. Outcome: {outcome}{conf_str}."
        )

    return jsonify({
        "heal_id": heal_id,
        "trigger_source": trigger,
        "pattern": pattern,
        "action": action,
        "outcome": outcome,
        "recorded_at": row.get("created_at"),
        "explanation": {"summary": summary, "html": f"<p>{summary}</p>"},
    })


@bp.route("/api/explain/reflex/<reflex_name>", methods=["GET"])
def explain_reflex(reflex_name: str):
    """Return a plain-English explanation for the latest run of a Genesis reflex."""
    row = _get_reflex_latest(reflex_name)
    if row is None:
        return jsonify({
            "reflex_name": reflex_name,
            "explanation": {
                "summary": f"No execution history found for reflex '{reflex_name}'.",
                "html": f"<p>No execution history found for reflex <strong>{reflex_name}</strong>.</p>",
            },
        })

    outcome = row.get("outcome", "unknown")
    artifacts = row.get("artifacts_produced") or 0
    started = row.get("started_at", "")[:16] if row.get("started_at") else "unknown time"
    error = row.get("error_message")

    if outcome == "success":
        art_str = f" and produced {artifacts} artifact(s)" if artifacts else ""
        summary = (
            f"The <strong>{reflex_name}</strong> reflex ran successfully at {started}{art_str}. "
            f"This autonomous reflex monitors conditions and acts when thresholds are met."
        )
    elif outcome in ("error", "failed"):
        err_str = f" Error: {error}" if error else ""
        summary = (
            f"The <strong>{reflex_name}</strong> reflex encountered an error during its run at {started}.{err_str} "
            f"Check the Genesis log for details."
        )
    else:
        summary = (
            f"The <strong>{reflex_name}</strong> reflex last ran at {started} with outcome: {outcome}."
        )

    return jsonify({
        "reflex_name": reflex_name,
        "outcome": outcome,
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "artifacts_produced": artifacts,
        "explanation": {"summary": summary, "html": f"<p>{summary}</p>"},
    })


@bp.route("/api/explain/<int:event_id>", methods=["GET"])
def explain_event(event_id: int):
    """Return a plain-English explanation for an audit trail event.

    Fetches the event from audit_trail by *event_id*, parses the JSON
    details field as event_data, then delegates to
    explain_translator.translate() for deterministic rule-based rendering.
    """
    from tools.aisg.explain_translator import translate  # local to avoid circular

    row = _get_audit_event(event_id)
    if row is None:
        return jsonify({"error": f"Event {event_id} not found"}), 404

    event_type: str = row.get("event_type", "")
    raw_details = row.get("details") or "{}"
    try:
        event_data: dict = json.loads(raw_details) if isinstance(raw_details, str) else raw_details
    except (json.JSONDecodeError, TypeError):
        event_data = {}

    explanation = translate(event_type, event_data)
    return jsonify({
        "event_id": event_id,
        "event_type": event_type,
        "actor": row.get("actor"),
        "action": row.get("action"),
        "recorded_at": row.get("created_at"),
        "explanation": explanation,
    })


# ---------------------------------------------------------------------------
# Dashboard pages
# ---------------------------------------------------------------------------

@bp.route("/ai-wizard", methods=["GET"])
def ai_wizard_page():
    """AI Launchpad Wizard — guided onboarding for non-AI teams."""
    return render_template("aisg/wizard.html")


@bp.route("/ai-patterns", methods=["GET"])
def ai_patterns_page():
    """AI Pattern Library dashboard page."""
    from tools.aisg.pattern_registry import list_patterns  # noqa: PLC0415
    try:
        patterns = list_patterns()
    except Exception:
        patterns = []
    return render_template("aisg/patterns.html", patterns=patterns)


# ---------------------------------------------------------------------------
# Pattern API
# ---------------------------------------------------------------------------

@bp.route("/api/aisg/patterns", methods=["GET"])
def api_list_patterns():
    """List patterns; optional ?category= and ?complexity= query params."""
    from tools.aisg.pattern_registry import list_patterns  # noqa: PLC0415
    category = request.args.get("category")
    complexity = request.args.get("complexity")
    try:
        patterns = list_patterns(category=category, complexity=complexity)
        return jsonify({"patterns": patterns, "total": len(patterns)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/aisg/patterns/seed", methods=["POST"])
def api_seed_patterns():
    """Seed built-in patterns. Idempotent."""
    from tools.aisg.pattern_registry import _seed_builtins  # noqa: PLC0415
    try:
        inserted = _seed_builtins()
        return jsonify({"inserted": inserted})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/aisg/patterns/<pattern_id>", methods=["GET"])
def api_get_pattern(pattern_id: str):
    """Return a single pattern by ID."""
    from tools.aisg.pattern_registry import get_pattern  # noqa: PLC0415
    pat = get_pattern(pattern_id)
    if pat is None:
        return jsonify({"error": f"Pattern {pattern_id} not found"}), 404
    return jsonify(pat)


@bp.route("/api/aisg/patterns/<pattern_id>/deploy", methods=["POST"])
def api_deploy_pattern(pattern_id: str):
    """Increment deploy_count and return updated pattern."""
    from tools.aisg.pattern_registry import deploy_pattern  # noqa: PLC0415
    result = deploy_pattern(pattern_id)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)
