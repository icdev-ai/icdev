# CUI // SP-CTI
"""MCIP DAT Blueprint — Diplomatic Activity Tracker canvas.

Mounted at /dat by tools/dashboard/app.py.
All 8 required components per CLAUDE.md dashboard gate are present:
  1. template: tools/dashboard/templates/mcip/page.html
  2. icdev/ mirror: icdev/tools/dashboard/templates/mcip/page.html
  3. route: @bp.route("/dat")
  4. backing module: tools/mcip/analytics.py
  5. constants: tools/mcip/constants.py
  6. DB: mcip_dat_events + mcip_dti_scores (migration via conftest schema)
  7. nav link: base.html Strategos → DAT
  8. IQE: tools/iqe/adapters/mcip.py + /api/iqe-query + widget + dispatch entry
"""
from __future__ import annotations


from flask import Blueprint, jsonify, render_template, request as flask_request

from tools.mcip.analytics import (
    get_current_dti,
    get_dti_history,
    get_recent_events,
    ingest_event,
    compute_dti,
    record_dti_score,
    content_hash,
)
from tools.mcip.constants import (
    SOURCE_LABELS, SOURCE_COLORS, BAND_COLORS, DAT_SOURCE_TYPES,
    DTI_UPDATE_INTERVAL_HOURS, classify_dti,
)

from tools.logging.icdev_logger import get_logger
log = get_logger("mcip.blueprint")

bp = Blueprint("mcip_dat", __name__)


@bp.route("/dat")
def dat_page():
    """Diplomatic Activity Tracker main page."""

    current = get_current_dti()
    history = get_dti_history(hours=48)
    events = get_recent_events(limit=50)

    score = current["score"] if current else 0.0
    band = classify_dti(score)

    return render_template(
        "mcip/page.html",
        current_dti=current,
        dti_score=score,
        dti_band=band,
        dti_history=history,
        recent_events=events,
        source_labels=SOURCE_LABELS,
        source_colors=SOURCE_COLORS,
        band_colors=BAND_COLORS,
        source_types=DAT_SOURCE_TYPES,
        update_interval_h=DTI_UPDATE_INTERVAL_HOURS,
    )


@bp.route("/api/dat/dti")
def api_dti_current():
    """Current DTI score with band classification."""
    row = get_current_dti()
    if row:
        score = float(row["score"])
        return jsonify({
            "score": score,
            "band": classify_dti(score),
            "cable_sub": row.get("cable_sub"),
            "unsc_sub": row.get("unsc_sub"),
            "backchannel_sub": row.get("backchannel_sub"),
            "event_count": row.get("event_count"),
            "computed_at": row.get("computed_at"),
        })
    # No persisted score yet — compute on-the-fly
    live = compute_dti()
    return jsonify(live)


@bp.route("/api/dat/dti/history")
def api_dti_history():
    """DTI trend: last N hours of scores."""
    hours = min(int(flask_request.args.get("hours", 48)), 720)
    rows = get_dti_history(hours=hours)
    return jsonify({"history": rows, "count": len(rows)})


@bp.route("/api/dat/events")
def api_dat_events():
    """Paginated DAT event feed."""
    source_type = flask_request.args.get("source_type") or None
    limit = min(int(flask_request.args.get("limit", 50)), 200)
    rows = get_recent_events(source_type=source_type, limit=limit)
    return jsonify({"events": rows, "count": len(rows)})


@bp.route("/api/dat/events", methods=["POST"])
def api_dat_ingest():
    """Ingest a new DAT event."""
    data = flask_request.get_json(silent=True) or {}
    source_type = (data.get("source_type") or "").strip()
    text = data.get("text") or data.get("content") or ""
    sender = (data.get("sender") or "unknown").strip()
    recipient = (data.get("recipient") or "unknown").strip()
    classification = (data.get("classification") or "CUI").strip()
    tension_signal = float(data.get("tension_signal", 0.5))

    if source_type not in DAT_SOURCE_TYPES:
        return jsonify({"error": f"source_type must be one of {DAT_SOURCE_TYPES}"}), 400

    chash = content_hash(text) if text else content_hash(f"{source_type}{sender}{recipient}")
    event_id = ingest_event(source_type, chash, sender, recipient, classification, tension_signal)
    return jsonify({"status": "ok", "event_id": event_id}), 201


@bp.route("/api/dat/compute", methods=["POST"])
def api_dat_compute():
    """Trigger manual DTI recompute and persist the result."""
    result = compute_dti()
    row_id = record_dti_score(result["score"], result["sub_scores"], result["event_count"])
    result["persisted_id"] = row_id
    return jsonify(result)


@bp.route("/api/dat/iqe-query", methods=["POST"])
def dat_api_iqe_query():
    """IQE query against DAT collections."""
    from tools.iqe.nl_to_iqe import nl_to_iqe
    from tools.iqe.parser import IQESyntaxError, parse
    from tools.iqe.executor import execute_query
    import tools.iqe.adapters.mcip  # noqa: F401

    data = flask_request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    collections = ["dat.events", "dat.dti_history"]
    translation = nl_to_iqe(question, collections)
    iqe_str = translation.get("iqe", "")
    explanation = translation.get("explanation", "")

    if not data.get("execute", True):
        return jsonify({"ok": True, "iqe": iqe_str, "explanation": explanation})

    try:
        ast = parse(iqe_str)
        rows = execute_query(ast, None)
        return jsonify({"ok": True, "iqe": iqe_str, "explanation": explanation,
                        "results": rows, "row_count": len(rows)})
    except (IQESyntaxError, Exception) as exc:
        return jsonify({"ok": True, "iqe": iqe_str, "explanation": explanation,
                        "results": [], "row_count": 0, "warn": str(exc)})
