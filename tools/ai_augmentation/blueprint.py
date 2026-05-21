# CUI // SP-CTI
"""AI Augmentation Canvas (AAC) — Flask Blueprint.

Routes:
  GET  /ai-augmentation/              index (scan form + history)
  POST /ai-augmentation/api/scan      run scan → {scan_id, ...}
  GET  /ai-augmentation/api/scan/<id> get scan results
  POST /ai-augmentation/api/iqe-query IQE natural-language query
"""
from __future__ import annotations

import importlib
import json
import logging

from flask import Blueprint, jsonify, render_template, request

from tools.ai_augmentation.db.init_db import get_connection, init_db
from tools.ai_augmentation.engine import run_scan

logger = logging.getLogger(__name__)

aac_bp = Blueprint(
    "aac",
    __name__,
    url_prefix="/ai-augmentation",
    template_folder="../../tools/dashboard/templates",
)

_INIT_DONE = False


def _ensure_init() -> None:
    global _INIT_DONE
    if _INIT_DONE:
        return
    try:
        init_db()
    except Exception as exc:
        logger.warning("aac: DB init error: %s", exc)
    _INIT_DONE = True


@aac_bp.before_request
def _init():
    _ensure_init()


def _conn():
    return get_connection()


def _parse_phases(raw):
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return []
    return raw or []


@aac_bp.route("/")
def index():
    conn = _conn()
    try:
        scans = [dict(r) for r in conn.execute(
            "SELECT scan_id, input_type, input_ref, total_files, total_loc, "
            "status, created_at FROM aac_scans ORDER BY created_at DESC LIMIT 10"
        ).fetchall()]

        opportunities: list[dict] = []
        roadmap: dict | None = None

        if scans:
            latest_id = scans[0]["scan_id"]
            opportunities = [dict(r) for r in conn.execute(
                "SELECT o.opportunity_id, o.module_path, o.function_name, o.language, "
                "o.pattern_type, o.ai_paradigm, o.il_recommended_model, "
                "s.composite_score, s.value_score "
                "FROM aac_opportunities o "
                "LEFT JOIN aac_scores s ON s.opportunity_id = o.opportunity_id "
                "WHERE o.scan_id = ? ORDER BY s.composite_score DESC",
                (latest_id,)
            ).fetchall()]

            rm = conn.execute(
                "SELECT roadmap_id, title, phases, total_effort_days "
                "FROM aac_roadmaps WHERE scan_id = ? ORDER BY created_at DESC LIMIT 1",
                (latest_id,)
            ).fetchone()
            if rm:
                roadmap = dict(rm)
                roadmap["phases"] = _parse_phases(roadmap.get("phases"))
    finally:
        conn.close()

    return render_template(
        "ai_augmentation/page.html",
        scans=scans,
        opportunities=opportunities,
        roadmap=roadmap,
        iqe_canvas="aac",
        iqe_api_route="/ai-augmentation/api/iqe-query",
        iqe_title="AI Augmentation IQE",
        iqe_examples=[
            {"label": "Top opportunities", "query": "show top opportunities by composite score"},
            {"label": "Completed scans",   "query": "list all completed scans"},
            {"label": "Agentic patterns",  "query": "show agentic_trigger opportunities"},
        ],
    )


@aac_bp.route("/api/scan", methods=["POST"])
def api_scan():
    data = request.get_json(force=True, silent=True) or {}
    input_type = data.get("input_type", "local_path")
    input_ref  = (data.get("input_ref") or "").strip()
    il_level   = data.get("il_level", "il4")

    if not input_ref:
        return jsonify({"error": "input_ref is required"}), 400

    try:
        result = run_scan(input_type, input_ref, {"il_level": il_level})
        return jsonify(result), 201
    except Exception as exc:
        logger.error("aac: scan error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@aac_bp.route("/api/scan/<int:scan_id>")
def api_get_scan(scan_id: int):
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM aac_scans WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404

        opps = [dict(r) for r in conn.execute(
            "SELECT o.opportunity_id, o.module_path, o.function_name, o.language, "
            "o.pattern_type, o.ai_paradigm, o.il_recommended_model, "
            "s.composite_score, s.value_score "
            "FROM aac_opportunities o "
            "LEFT JOIN aac_scores s ON s.opportunity_id = o.opportunity_id "
            "WHERE o.scan_id = ? ORDER BY s.composite_score DESC",
            (scan_id,)
        ).fetchall()]

        rm = conn.execute(
            "SELECT roadmap_id, title, phases, total_effort_days "
            "FROM aac_roadmaps WHERE scan_id = ? ORDER BY created_at DESC LIMIT 1",
            (scan_id,)
        ).fetchone()
        roadmap = None
        if rm:
            roadmap = dict(rm)
            roadmap["phases"] = _parse_phases(roadmap.get("phases"))

        return jsonify({"scan": dict(row), "opportunities": opps, "roadmap": roadmap})
    finally:
        conn.close()


@aac_bp.route("/api/iqe-query", methods=["POST"])
def api_iqe_query():
    data = request.get_json(force=True, silent=True) or {}
    question = (data.get("question") or "").strip()
    execute   = data.get("execute", True)

    if not question:
        return jsonify({"error": "question is required"}), 400

    try:
        importlib.import_module("tools.iqe.adapters.ai_augmentation")
    except Exception:
        pass

    iqe_str = ""
    try:
        from tools.iqe.nl_to_iqe import nl_to_iqe
        from tools.iqe.parser import parse as _parse
        from tools.iqe.executor import execute_query

        collections = [
            "ai_augmentation.opportunities",
            "ai_augmentation.scans",
            "ai_augmentation.roadmaps",
        ]
        iqe_str = nl_to_iqe(question, collections=collections)
        ast = _parse(iqe_str)
        results = execute_query(ast) if execute else []
        return jsonify({"iqe": iqe_str, "results": results, "row_count": len(results)})
    except Exception as exc:
        return jsonify({"error": str(exc), "iqe": iqe_str}), 500
