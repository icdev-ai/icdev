# CUI // SP-CTI
"""FORGE IGNITE Flask blueprint — all /innovation/* routes."""

from __future__ import annotations

import json
import logging

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from .db import (
    migrate,
    create_idea, get_idea, list_ideas, update_idea, save_intake_answers,
    create_assessment, get_latest_assessment, approve_assessment,
    create_pilot, list_pilots,
    add_comment, list_comments,
    pipeline_stats, VALID_STATUSES,
)
from .intake_engine import get_rounds, generate_brief
from .scoring_engine import compute_score
from .feasibility_engine import generate_feasibility_study
from .reporting_engine import innovation_dashboard_data, compute_org_readiness

logger = logging.getLogger("icdev.innovation")
bp = Blueprint("innovation", __name__)

_initialized = False


def _ensure_init():
    global _initialized
    if not _initialized:
        try:
            migrate()
            _initialized = True
        except Exception as e:
            logger.warning("Innovation DB migration failed: %s", e)


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@bp.route("/innovation")
@bp.route("/innovation/")
def pipeline_page():
    _ensure_init()
    status_filter = request.args.get("status", "")
    phase_filter = request.args.get("phase", "")
    ideas = list_ideas(status=status_filter or None)
    if phase_filter:
        ideas = [i for i in ideas if i.get("phase_tag") == phase_filter]
    stats = pipeline_stats()
    stages = [
        {"id": "spark", "label": "Spark", "color": "#FFB800"},
        {"id": "assess", "label": "Assess", "color": "#4A90E2"},
        {"id": "score", "label": "Scored", "color": "#00D4FF"},
        {"id": "pilot", "label": "Pilot", "color": "#00FF88"},
        {"id": "measure", "label": "Measure", "color": "#9b59b6"},
        {"id": "scale", "label": "Scaled", "color": "#FF6B35"},
        {"id": "archive", "label": "Archived", "color": "#555555"},
    ]
    return render_template(
        "innovation/pipeline.html",
        ideas=ideas,
        stats=stats,
        stages=stages,
        status_filter=status_filter,
        phase_filter=phase_filter,
    )


@bp.route("/innovation/new")
def new_idea_page():
    _ensure_init()
    rounds = get_rounds()
    return render_template("innovation/intake.html", rounds=rounds)


@bp.route("/innovation/<int:idea_id>")
def idea_detail_page(idea_id: int):
    _ensure_init()
    idea = get_idea(idea_id)
    if not idea:
        return redirect(url_for("innovation.pipeline_page"))
    assessment = get_latest_assessment(idea_id)
    pilots = list_pilots(idea_id)
    comments = list_comments(idea_id)
    rounds = get_rounds()

    # Parse intake answers for display
    intake_answers = idea.get("intake_answers", {})
    if isinstance(intake_answers, str):
        try:
            intake_answers = json.loads(intake_answers)
        except Exception:
            intake_answers = {}
    if not intake_answers:
        intake_answers = {}

    # Score breakdown for display
    score_breakdown = idea.get("score_breakdown", {})
    if isinstance(score_breakdown, str):
        try:
            score_breakdown = json.loads(score_breakdown)
        except Exception:
            score_breakdown = {}

    stages = [
        {"id": "spark", "label": "Spark"}, {"id": "assess", "label": "Assess"},
        {"id": "score", "label": "Scored"}, {"id": "pilot", "label": "Pilot"},
        {"id": "measure", "label": "Measure"}, {"id": "scale", "label": "Scaled"},
        {"id": "archive", "label": "Archived"},
    ]
    return render_template(
        "innovation/idea_detail.html",
        idea=idea,
        assessment=assessment,
        pilots=pilots,
        comments=comments,
        rounds=rounds,
        intake_answers=intake_answers,
        score_breakdown=score_breakdown,
        stages=stages,
    )


@bp.route("/innovation/dashboard")
def dashboard_page():
    _ensure_init()
    data = innovation_dashboard_data()
    return render_template("innovation/dashboard.html", **data)


# ---------------------------------------------------------------------------
# API: Idea lifecycle
# ---------------------------------------------------------------------------

@bp.route("/api/innovation/ideas", methods=["POST"])
def api_create_idea():
    _ensure_init()
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    idea = create_idea(
        title=title,
        submitter_name=data.get("submitter_name", ""),
        submitter_email=data.get("submitter_email", ""),
        submitter_role=data.get("submitter_role", "unknown"),
        department=data.get("department", ""),
    )
    return jsonify({"ok": True, "idea": idea, "idea_id": idea["id"]}), 201


@bp.route("/api/innovation/ideas/<int:idea_id>/intake", methods=["POST"])
def api_save_intake(idea_id: int):
    _ensure_init()
    data = request.get_json(silent=True) or {}
    answers = data.get("answers", {})
    if not answers:
        return jsonify({"error": "answers required"}), 400
    idea = get_idea(idea_id)
    if not idea:
        return jsonify({"error": "idea not found"}), 404

    # Save answers
    updated = save_intake_answers(idea_id, answers)

    # Generate brief
    brief = generate_brief(
        title=idea.get("title", ""),
        submitter_role=idea.get("submitter_role", "unknown"),
        answers=answers,
    )

    # Compute score
    scores = compute_score(answers)

    # Persist score + brief + phase tag
    updated = update_idea(
        idea_id,
        opportunity_brief=brief,
        score_total=scores["total"],
        score_breakdown_json=json.dumps(scores),
        phase_tag=scores["phase_tag"],
        recommended_approach=_infer_approach_label(answers),
        status="score",
    )

    # Auto-create assessment record
    dims = scores.get("dimensions", {})
    score_vals = {k: v["score"] for k, v in dims.items()}
    create_assessment(
        idea_id=idea_id,
        scores=score_vals,
        study_text="",
        assessed_by="system",
    )

    return jsonify({
        "ok": True,
        "brief": brief,
        "scores": scores,
        "phase_tag": scores["phase_tag"],
        "phase_label": scores["phase_label"],
        "phase_emoji": scores["phase_emoji"],
        "idea_id": idea_id,
    })


@bp.route("/api/innovation/ideas/<int:idea_id>/feasibility", methods=["POST"])
def api_generate_feasibility(idea_id: int):
    _ensure_init()
    idea = get_idea(idea_id)
    if not idea:
        return jsonify({"error": "idea not found"}), 404

    score_breakdown = idea.get("score_breakdown")
    if isinstance(score_breakdown, str):
        try:
            scores = json.loads(score_breakdown)
        except Exception:
            scores = {}
    else:
        scores = score_breakdown or {}

    if not scores:
        # Recompute
        intake_answers = idea.get("intake_answers", {})
        if isinstance(intake_answers, str):
            try:
                intake_answers = json.loads(intake_answers)
            except Exception:
                intake_answers = {}
        scores = compute_score(intake_answers or {})

    study = generate_feasibility_study(idea, scores)

    # Persist to assessment
    assessment = get_latest_assessment(idea_id)
    if assessment:
        from tools.db.storage import get_canvas_connection
        with get_canvas_connection() as conn:
            conn.execute(
                "UPDATE innov_assessments SET feasibility_study_text=? WHERE id=?",
                (study, assessment["id"]),
            )
            conn.commit()
    else:
        dims = scores.get("dimensions", {})
        score_vals = {k: v["score"] for k, v in dims.items()}
        create_assessment(idea_id=idea_id, scores=score_vals, study_text=study)

    update_idea(idea_id, status="assess")
    return jsonify({"ok": True, "study": study})


@bp.route("/api/innovation/ideas/<int:idea_id>/status", methods=["POST"])
def api_update_status(idea_id: int):
    _ensure_init()
    data = request.get_json(silent=True) or {}
    new_status = data.get("status", "")
    if new_status not in VALID_STATUSES:
        return jsonify({"error": f"invalid status; must be one of {sorted(VALID_STATUSES)}"}), 400
    idea = get_idea(idea_id)
    if not idea:
        return jsonify({"error": "idea not found"}), 404
    update_idea(idea_id, status=new_status)
    return jsonify({"ok": True, "status": new_status})


@bp.route("/api/innovation/ideas/<int:idea_id>/approve-assessment", methods=["POST"])
def api_approve_assessment(idea_id: int):
    _ensure_init()
    data = request.get_json(silent=True) or {}
    notes = data.get("notes", "")
    assessment = get_latest_assessment(idea_id)
    if not assessment:
        return jsonify({"error": "no assessment found"}), 404
    approve_assessment(assessment["id"], reviewer_notes=notes)
    update_idea(idea_id, status="score")
    return jsonify({"ok": True})


@bp.route("/api/innovation/ideas/<int:idea_id>/pilot", methods=["POST"])
def api_create_pilot(idea_id: int):
    _ensure_init()
    data = request.get_json(silent=True) or {}
    pilot_name = (data.get("pilot_name") or "").strip()
    if not pilot_name:
        return jsonify({"error": "pilot_name required"}), 400
    pilot = create_pilot(
        idea_id=idea_id,
        pilot_name=pilot_name,
        hypothesis=data.get("hypothesis", ""),
        success_criteria=data.get("success_criteria", ""),
    )
    update_idea(idea_id, status="pilot")

    # Attempt Kanban epic creation
    kanban_link = _try_create_kanban_epic(idea_id, pilot_name, data)
    if kanban_link:
        from tools.db.storage import get_canvas_connection
        with get_canvas_connection() as conn:
            conn.execute(
                "UPDATE innov_pilots SET kanban_epic_id=? WHERE id=?",
                (kanban_link, pilot["id"]),
            )
            conn.commit()
        pilot["kanban_epic_id"] = kanban_link

    return jsonify({"ok": True, "pilot": pilot})


@bp.route("/api/innovation/ideas/<int:idea_id>/comments", methods=["POST"])
def api_add_comment(idea_id: int):
    _ensure_init()
    data = request.get_json(silent=True) or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"error": "body required"}), 400
    comment = add_comment(idea_id, data.get("author_name", "Anonymous"), body)
    return jsonify({"ok": True, "comment": comment}), 201


@bp.route("/api/innovation/ideas", methods=["GET"])
def api_list_ideas():
    _ensure_init()
    status = request.args.get("status")
    ideas = list_ideas(status=status)
    return jsonify({"ideas": ideas})


@bp.route("/api/innovation/dashboard", methods=["GET"])
def api_dashboard():
    _ensure_init()
    return jsonify(innovation_dashboard_data())


@bp.route("/api/innovation/org-readiness", methods=["GET"])
def api_org_readiness():
    _ensure_init()
    return jsonify(compute_org_readiness())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _infer_approach_label(answers: dict) -> str:
    from .intake_engine import _infer_approach
    return _infer_approach(answers)


def _try_create_kanban_epic(idea_id: int, pilot_name: str, data: dict) -> str:
    """Attempt to create a Kanban task group for the pilot. Returns epic ID or ''."""
    try:
        from tools.kanban.task_manager import create_task
        task = create_task(
            title=f"[IGNITE Pilot] {pilot_name}",
            description=(
                f"FORGE IGNITE pilot for idea #{idea_id}: {pilot_name}\n\n"
                f"Hypothesis: {data.get('hypothesis', 'TBD')}\n"
                f"Success criteria: {data.get('success_criteria', 'TBD')}\n\n"
                "Time-box: 2–4 weeks. Define one measurable success metric before starting."
            ),
            status="scheduled",
            priority="medium",
            tags=["ignite", "pilot", f"idea-{idea_id}"],
        )
        return str(task.get("id", ""))
    except Exception:
        return ""


# Alias for app.py _APP_DEFS registration
innovation_bp = bp
