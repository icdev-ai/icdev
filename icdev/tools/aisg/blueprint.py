#!/usr/bin/env python3
# CUI // SP-CTI
"""AISG Flask Blueprint.

Routes:
  GET  /ai-wizard                            — AI Launchpad Wizard page
  POST /api/ai-wizard/submit                 — Process wizard answers (JSON)
  GET  /ai-patterns                          — AI Pattern Library page
  GET  /api/aisg/patterns                    — List all patterns (JSON)
  GET  /api/aisg/patterns/<id>               — Get a single pattern (JSON)
  POST /api/aisg/patterns/<id>/deploy        — Record pattern usage (JSON; tracks usage, does not deploy)
  POST /api/aisg/patterns/seed               — Seed built-in patterns (JSON)
  GET  /api/explain/<event_id>               — Explain an audit trail event
  GET  /api/explain/heal/<heal_id>           — Explain a self-healing event
  GET  /api/explain/reflex/<reflex_name>     — Explain latest reflex run
  GET  /dashboard/compliance-view            — ATO / compliance dashboard
  GET  /dashboard/executive-view             — Executive ROI dashboard
  GET  /dashboard/pm-view                    — PM sprint task dashboard
  GET  /ai-learning                          — AI Learning Paths
  GET  /ai-handoff                           — Knowledge Handoff (export/import)
  POST /api/ai-handoff/export                — Export knowledge package
  POST /api/ai-handoff/import                — Import knowledge package (restores fine-tuning patterns)
  GET  /ai-builder                           — Visual Agent Builder
  POST /api/ai-builder/save                  — Save canvas design
  POST /api/ai-builder/generate              — Generate goal from canvas
  GET  /ai-roi                               — AI ROI Tracker dashboard
  GET  /ai-skills                            — AI Skills Gap Tracker
"""
from __future__ import annotations

import json
import uuid

from flask import Blueprint, jsonify, render_template, request

from tools.dashboard.auth import require_role
from tools.db.storage import get_connection

bp = Blueprint("aisg", __name__)

# nav-sec-06: state-changing AISG endpoints (wizard-completion, pattern
# seed/deploy, knowledge-handoff export/import, visual-builder save/generate)
# are restricted to admin/pm. Before this fix they were gated only on "is
# authenticated", so any lowest-privilege ``developer`` could deploy patterns,
# import an arbitrary knowledge package, or persist an agent design. Reads (GET
# pages + list/get patterns + explain/autotune/iqe) stay open. Every role here
# also appears in ``VALID_DASHBOARD_ROLES`` in tools/dashboard/auth.py.
_AISG_MUTATION_ROLES = ("admin", "pm")


# ---------------------------------------------------------------------------
# Internal DB helpers
# ---------------------------------------------------------------------------

def _get_audit_event(event_id: int) -> dict | None:
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


# ---------------------------------------------------------------------------
# Explain API
# ---------------------------------------------------------------------------

@bp.route("/api/explain/heal/<int:heal_id>", methods=["GET"])
def explain_heal(heal_id: int):
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
    from tools.aisg.explain_translator import translate

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
# AI Wizard
# ---------------------------------------------------------------------------

@bp.route("/ai-wizard", methods=["GET"])
def ai_wizard_page():
    return render_template("aisg/wizard.html")


@bp.route("/api/ai-wizard/submit", methods=["POST"])
@require_role(*_AISG_MUTATION_ROLES)
def wizard_submit():
    from tools.aisg.wizard import WizardEngine
    _engine = WizardEngine()
    data = request.get_json(silent=True) or {}
    answers = {
        "use_case": data.get("use_case", "web_app"),
        "compliance_level": data.get("compliance_level", "IL2"),
        "tech_stack": data.get("tech_stack", "python"),
        "ai_maturity": data.get("ai_maturity", "none"),
        "cloud_provider": data.get("cloud_provider", "local"),
    }
    session_token = data.get("session_token") or str(uuid.uuid4())
    result = _engine.process(session_token=session_token, answers=answers)
    return jsonify({"session_token": session_token, **result})


# ---------------------------------------------------------------------------
# AI Pattern Library
# ---------------------------------------------------------------------------

@bp.route("/ai-patterns", methods=["GET"])
def ai_patterns_page():
    from tools.aisg.pattern_registry import list_patterns
    try:
        patterns = list_patterns()
    except Exception:
        patterns = []
    return render_template("aisg/patterns.html", patterns=patterns)


@bp.route("/api/aisg/patterns", methods=["GET"])
def api_list_patterns():
    from tools.aisg.pattern_registry import list_patterns
    category = request.args.get("category")
    complexity = request.args.get("complexity")
    try:
        patterns = list_patterns(category=category, complexity=complexity)
        return jsonify({"patterns": patterns, "total": len(patterns)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/aisg/patterns/seed", methods=["POST"])
@require_role(*_AISG_MUTATION_ROLES)
def api_seed_patterns():
    from tools.aisg.pattern_registry import _seed_builtins
    try:
        inserted = _seed_builtins()
        return jsonify({"inserted": inserted})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/aisg/patterns/<pattern_id>", methods=["GET"])
def api_get_pattern(pattern_id: str):
    from tools.aisg.pattern_registry import get_pattern
    pat = get_pattern(pattern_id)
    if pat is None:
        return jsonify({"error": f"Pattern {pattern_id} not found"}), 404
    return jsonify(pat)


@bp.route("/api/aisg/patterns/<pattern_id>/deploy", methods=["POST"])
@require_role(*_AISG_MUTATION_ROLES)
def api_record_pattern_usage(pattern_id: str):
    # NOTE: this records that a pattern was used as a starting blueprint — it
    # does not provision infrastructure or generate tasks. The URL retains the
    # legacy ``/deploy`` path for compatibility, but the response reports
    # ``usage_count`` / ``action=usage_recorded`` so it cannot be mistaken for a
    # real deployment.
    from tools.aisg.pattern_registry import record_pattern_usage
    result = record_pattern_usage(pattern_id)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


# ---------------------------------------------------------------------------
# Role-based dashboard views
# ---------------------------------------------------------------------------

@bp.route("/dashboard/compliance-view", methods=["GET"])
def compliance_view_page():
    from tools.aisg.compliance_view import get_compliance_data
    data = get_compliance_data()
    return render_template("aisg/compliance_view.html", **data)


@bp.route("/dashboard/executive-view", methods=["GET"])
def executive_view_page():
    from tools.aisg.executive_view import get_executive_data
    data = get_executive_data()
    return render_template("aisg/executive_view.html", **data)


@bp.route("/dashboard/pm-view", methods=["GET"])
def pm_view_page():
    from tools.aisg.pm_view import get_pm_data
    data = get_pm_data()
    return render_template("aisg/pm_view.html", **data)


# ---------------------------------------------------------------------------
# AI Learning Paths
# ---------------------------------------------------------------------------

@bp.route("/ai-learning", methods=["GET"])
def ai_learning_page():
    from tools.aisg.learning_paths import get_tracks
    tracks = get_tracks()
    return render_template("aisg/learning.html", tracks=tracks)


# ---------------------------------------------------------------------------
# Knowledge Handoff
# ---------------------------------------------------------------------------

@bp.route("/ai-handoff", methods=["GET"])
def ai_handoff_page():
    return render_template("aisg/handoff.html")


@bp.route("/api/ai-handoff/export", methods=["POST"])
@require_role(*_AISG_MUTATION_ROLES)
def api_handoff_export():
    from tools.aisg.knowledge_handoff import export_package
    import tempfile
    import os
    data = request.get_json(silent=True) or {}
    user_email = data.get("user_email", "unknown@example.com")
    output_path = os.path.join(tempfile.gettempdir(), f"handoff_{user_email.split('@')[0]}.zip")
    try:
        result = export_package(user_email=user_email, output_path=output_path)
        return jsonify({"status": "ok", **result})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/ai-handoff/import", methods=["POST"])
@require_role(*_AISG_MUTATION_ROLES)
def api_handoff_import():
    from tools.aisg.knowledge_handoff import import_package
    data = request.get_json(silent=True) or {}
    zip_path = data.get("zip_path", "")
    new_user = data.get("new_user_email", "")
    if not zip_path or not new_user:
        return jsonify({"error": "zip_path and new_user_email are required"}), 400
    try:
        result = import_package(zip_path=zip_path, new_user_email=new_user)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except (ValueError, KeyError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    # Keep ``imported_for`` for the existing UI, but the real counts come from
    # import_package (imported_patterns / skipped_audit_entries / …).
    return jsonify({"imported_for": new_user, "source": zip_path, **result})


# ---------------------------------------------------------------------------
# Visual Agent Builder
# ---------------------------------------------------------------------------

@bp.route("/ai-builder", methods=["GET"])
def ai_builder_page():
    return render_template("aisg/builder.html")


@bp.route("/api/ai-builder/save", methods=["POST"])
@require_role(*_AISG_MUTATION_ROLES)
def api_builder_save():
    from tools.aisg.visual_agent_builder import save_design
    data = request.get_json(silent=True) or {}
    name = data.get("name", "Untitled Design")
    canvas_json = data.get("canvas_json", "{}")
    trust_tier = data.get("trust_tier", "advisor")
    created_by = data.get("created_by", "user")
    try:
        design_id = save_design(name=name, canvas_json=canvas_json, trust_tier=trust_tier, created_by=created_by)
        return jsonify({"status": "saved", "design_id": design_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/ai-builder/generate", methods=["POST"])
@require_role(*_AISG_MUTATION_ROLES)
def api_builder_generate():
    from tools.aisg.visual_agent_builder import generate_goal
    data = request.get_json(silent=True) or {}
    design_id = data.get("design_id")
    if not design_id:
        return jsonify({"error": "design_id is required"}), 400
    try:
        goal_md = generate_goal(design_id)
        return jsonify({"status": "ok", "goal_markdown": goal_md})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# AI ROI Tracker
# ---------------------------------------------------------------------------

@bp.route("/ai-roi", methods=["GET"])
def ai_roi_page():
    from tools.aisg.roi_tracker import get_roi_summary
    summary = get_roi_summary()
    return render_template("aisg/roi.html", **summary)


# ---------------------------------------------------------------------------
# AI Skills Gap Tracker
# ---------------------------------------------------------------------------

@bp.route("/ai-skills", methods=["GET"])
def ai_skills_page():
    from tools.aisg.skills_tracker import get_skills_data
    data = get_skills_data()
    return render_template("aisg/skills.html", **data)


# ---------------------------------------------------------------------------
# AutoTune — business-metric evaluation
# ---------------------------------------------------------------------------

@bp.route("/api/autotune/evaluate", methods=["GET"])
def api_autotune_evaluate():
    from tools.aisg.autotune import evaluate_business_metric
    business_goal = request.args.get("business_goal", "accuracy")
    model_version = request.args.get("model_version", "latest")
    _test_pct_raw = request.args.get("_test_pct")
    _test_pct = float(_test_pct_raw) if _test_pct_raw is not None else None
    try:
        result = evaluate_business_metric(
            business_goal=business_goal,
            model_version=model_version,
            _test_pct=_test_pct,
        )
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# IQE — natural-language query over AISG collections
# ---------------------------------------------------------------------------

@bp.route("/api/aisg/iqe-query", methods=["POST"])
def api_iqe_query():
    """Translate a plain-English question to IQE and execute it over the AISG
    collections (aisg.roadmaps / aisg.skills / aisg.roi / aisg.patterns)."""
    import importlib

    data = request.get_json(force=True, silent=True) or {}
    question = (data.get("question") or "").strip()
    execute = data.get("execute", True)
    if not question:
        return jsonify({"error": "question is required"}), 400

    collections = ["aisg.roadmaps", "aisg.skills", "aisg.roi", "aisg.patterns"]
    iqe_str = ""
    try:
        importlib.import_module("tools.iqe.adapters.aisg")  # register collections
        from tools.iqe.executor import execute_query
        from tools.iqe.nl_to_iqe import nl_to_iqe
        from tools.iqe.parser import parse as _parse

        result = nl_to_iqe(question, collections)
        iqe_str = result.get("iqe", "")
        explanation = result.get("explanation", "")
        rows = execute_query(_parse(iqe_str), conn=None) if execute else []
        return jsonify({
            "iqe": iqe_str,
            "explanation": explanation,
            "results": rows,
            "row_count": len(rows),
        })
    except Exception as exc:
        return jsonify({"error": str(exc), "iqe": iqe_str}), 500
