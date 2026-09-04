# CUI // SP-CTI
"""AI Observatory Blueprint — cross-canvas AI decision traceability dashboard.

Mounted at /ai-observatory by tools/dashboard/app.py.
All 8 required components per CLAUDE.md dashboard gate are present:
  1. template: tools/dashboard/templates/ai_observatory/page.html
  2. icdev/ mirror: icdev/tools/dashboard/templates/ai_observatory/page.html
  3. route: @bp.route("/ai-observatory")
  4. backing module: tools/ai_observatory/analytics.py
  5. constants: tools/ai_observatory/constants.py
  6. DB: reads from canvas_ai_decisions (migration 121) — no new tables
  7. nav link: base.html Intelligence → Awareness → AI Observatory
  8. IQE: tools/iqe/adapters/ai_observatory.py + /api/iqe-query + widget + dispatch entry
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger


from flask import Blueprint, jsonify, render_template, request as flask_request

logger = get_logger(__name__)

bp = Blueprint("ai_observatory", __name__)


@bp.route("/ai-observatory")
def ai_observatory():
    """AI Observatory main page — cross-canvas AI decision history and analytics."""
    from tools.ai_observatory.analytics import get_decision_stats, get_confabulation_flags
    from tools.ai_observatory.constants import CANVAS_LABELS, DECISION_TYPE_LABELS

    stats = get_decision_stats(days=7)
    confab_flags = get_confabulation_flags(days=7)
    return render_template(
        "ai_observatory/page.html",
        stats=stats,
        confab_flags=confab_flags,
        canvas_labels=CANVAS_LABELS,
        decision_type_labels=DECISION_TYPE_LABELS,
    )


@bp.route("/api/ai-observatory/stats")
def ao_api_stats():
    """JSON stats for AI Observatory: counts, avg confidence, breakdowns."""
    from tools.ai_observatory.analytics import get_decision_stats
    days = int(flask_request.args.get("days", 7))
    return jsonify(get_decision_stats(days=days))


@bp.route("/api/ai-observatory/decisions")
def ao_api_decisions():
    """JSON list of AI decisions, filterable by canvas and decision_type."""
    from tools.ai_observatory.analytics import get_decisions
    canvas = flask_request.args.get("canvas") or None
    decision_type = flask_request.args.get("decision_type") or None
    limit = min(int(flask_request.args.get("limit", 100)), 500)
    return jsonify(get_decisions(canvas=canvas, decision_type=decision_type, limit=limit))


@bp.route("/api/ai-observatory/heatmap")
def ao_api_heatmap():
    """JSON confidence heatmap: canvas × decision_type → avg confidence."""
    from tools.ai_observatory.analytics import get_confidence_heatmap
    return jsonify(get_confidence_heatmap())


@bp.route("/api/ai-observatory/confabulation-flags")
def ao_api_confab():
    """JSON list of confabulation_flag decisions from the last N days."""
    from tools.ai_observatory.analytics import get_confabulation_flags
    days = int(flask_request.args.get("days", 7))
    return jsonify(get_confabulation_flags(days=days))


# rmf-ui-17: the page template has always pointed its widget at
# /ai-observatory/api/iqe-query; the route was declared at the bare
# /api/iqe-query on this prefix-less blueprint, so the widget 404'd and the
# rule collided with ohc and govlift.
@bp.route("/ai-observatory/api/iqe-query", methods=["POST"])
def ao_api_iqe_query():
    """IQE query against observatory.decisions and observatory.confabulation_flags."""
    from tools.iqe.nl_to_iqe import nl_to_iqe
    from tools.iqe.parser import IQESyntaxError, parse
    from tools.iqe.executor import execute_query
    import tools.iqe.adapters.ai_observatory  # noqa: F401

    data = flask_request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    collections = ["observatory.decisions", "observatory.confabulation_flags"]
    translation = nl_to_iqe(question, collections)
    iqe_str = translation.get("iqe", "")
    explanation = translation.get("explanation", "")

    if not data.get("execute", True):
        return jsonify({"ok": True, "iqe": iqe_str, "explanation": explanation}), 200

    try:
        ast = parse(iqe_str)
        rows = execute_query(ast, None)
        return jsonify({"ok": True, "iqe": iqe_str, "explanation": explanation,
                        "results": rows, "row_count": len(rows)}), 200
    except IQESyntaxError as exc:
        return jsonify({"error": f"IQE syntax error: {exc}", "iqe": iqe_str}), 400
    except Exception as exc:
        logger.warning("AI Observatory IQE query error: %s", exc)
        return jsonify({"error": str(exc), "iqe": iqe_str}), 500
