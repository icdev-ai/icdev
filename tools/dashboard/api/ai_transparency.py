#!/usr/bin/env python3
# CUI // SP-CTI
"""AI Transparency API Blueprint — REST endpoints for Phase 48 dashboard."""

import json
import os
import sqlite3
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

ai_transparency_api = Blueprint("ai_transparency_api", __name__, url_prefix="/api/ai-transparency")


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _safe_count(conn, table, project_id=None):
    try:
        if project_id:
            row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table} WHERE project_id = ?", (project_id,)).fetchone()
        else:
            row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
        return row["cnt"] if row else 0
    except Exception:
        return 0


@ai_transparency_api.route("/stats", methods=["GET"])
def get_stats():
    """Summary statistics for AI transparency dashboard."""
    project_id = request.args.get("project_id")
    try:
        conn = _get_db()
        stats = {
            "inventory_count": _safe_count(conn, "ai_use_case_inventory", project_id),
            "model_card_count": _safe_count(conn, "model_cards", project_id),
            "system_card_count": _safe_count(conn, "system_cards", project_id),
            "confabulation_count": _safe_count(conn, "confabulation_checks", project_id),
            "transparency_score": None,
            "fairness_score": None,
        }

        # Get latest fairness score
        try:
            where = "WHERE project_id = ?" if project_id else ""
            params = (project_id,) if project_id else ()
            row = conn.execute(
                f"SELECT overall_score FROM fairness_assessments {where} ORDER BY created_at DESC LIMIT 1",
                params,
            ).fetchone()
            if row:
                stats["fairness_score"] = round(row["overall_score"], 1)
        except Exception:
            pass

        conn.close()
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@ai_transparency_api.route("/frameworks", methods=["GET"])
def get_frameworks():
    """Framework assessment results."""
    project_id = request.args.get("project_id")
    frameworks = []
    try:
        conn = _get_db()
        for table, name in [
            ("omb_m25_21_assessments", "OMB M-25-21"),
            ("omb_m26_04_assessments", "OMB M-26-04"),
            ("nist_ai_600_1_assessments", "NIST AI 600-1"),
            ("gao_ai_assessments", "GAO-21-519SP"),
        ]:
            try:
                where = "WHERE project_id = ?" if project_id else ""
                params = (project_id,) if project_id else ()
                total = conn.execute(
                    f"SELECT COUNT(DISTINCT requirement_id) as cnt FROM {table} {where}", params
                ).fetchone()["cnt"]
                satisfied = conn.execute(
                    f"SELECT COUNT(DISTINCT requirement_id) as cnt FROM {table} {where} {'AND' if project_id else 'WHERE'} status IN ('satisfied', 'partially_satisfied')",
                    params,
                ).fetchone()["cnt"]
                coverage = round(satisfied / total * 100, 1) if total > 0 else 0
                frameworks.append({"name": name, "coverage": coverage, "total": total})
            except Exception:
                frameworks.append({"name": name, "coverage": 0, "total": 0})
        conn.close()
        return jsonify({"frameworks": frameworks})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@ai_transparency_api.route("/inventory", methods=["GET"])
def get_inventory():
    """AI use case inventory listing."""
    project_id = request.args.get("project_id")
    try:
        conn = _get_db()
        where = "WHERE project_id = ?" if project_id else ""
        params = (project_id,) if project_id else ()
        rows = conn.execute(
            f"SELECT * FROM ai_use_case_inventory {where} ORDER BY name", params
        ).fetchall()
        conn.close()
        return jsonify({"items": [dict(r) for r in rows], "total": len(rows)})
    except Exception as e:
        return jsonify({"items": [], "total": 0, "error": str(e)})


@ai_transparency_api.route("/model-cards", methods=["GET"])
def get_model_cards():
    """Model cards listing."""
    project_id = request.args.get("project_id")
    try:
        conn = _get_db()
        where = "WHERE project_id = ?" if project_id else ""
        params = (project_id,) if project_id else ()
        rows = conn.execute(
            f"SELECT id, project_id, model_name, version, created_at FROM model_cards {where} ORDER BY created_at DESC",
            params,
        ).fetchall()
        conn.close()
        return jsonify({"cards": [dict(r) for r in rows], "total": len(rows)})
    except Exception as e:
        return jsonify({"cards": [], "total": 0, "error": str(e)})


@ai_transparency_api.route("/gaps", methods=["GET"])
def get_gaps():
    """Get transparency gaps from latest audit."""
    project_id = request.args.get("project_id")
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from ai_transparency_audit import run_transparency_audit
        result = run_transparency_audit(project_id or "default", db_path=DB_PATH)
        return jsonify({"gaps": result.get("gaps", []), "gap_count": result.get("gap_count", 0)})
    except Exception as e:
        return jsonify({"gaps": [], "gap_count": 0, "error": str(e)})


@ai_transparency_api.route("/audit", methods=["POST"])
def run_audit():
    """Run full transparency audit."""
    data = request.get_json(silent=True) or {}
    project_id = data.get("project_id", "default")
    project_dir = data.get("project_dir")
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from ai_transparency_audit import run_transparency_audit
        result = run_transparency_audit(project_id, project_dir, db_path=DB_PATH)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@ai_transparency_api.route("/model-card", methods=["POST"])
def generate_model_card():
    """Generate a model card."""
    data = request.get_json(silent=True) or {}
    project_id = data.get("project_id", "default")
    model_name = data.get("model_name")
    if not model_name:
        return jsonify({"error": "model_name required"}), 400
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from model_card_generator import generate_model_card as gen
        result = gen(project_id, model_name, db_path=DB_PATH)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@ai_transparency_api.route("/system-card", methods=["POST"])
def generate_system_card():
    """Generate a system card."""
    data = request.get_json(silent=True) or {}
    project_id = data.get("project_id", "default")
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from system_card_generator import generate_system_card as gen
        result = gen(project_id, db_path=DB_PATH)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
