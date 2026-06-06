#!/usr/bin/env python3
# CUI // SP-CTI
"""AI Transparency API Blueprint — REST endpoints for Phase 48 dashboard."""

import os
import sqlite3
import sys
from tools.db.storage import get_connection
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

ai_transparency_api = Blueprint("ai_transparency_api", __name__, url_prefix="/api/ai-transparency")


def _get_db() -> sqlite3.Connection:
    conn = get_connection(db_path=str(DB_PATH))
    return conn


def _resolve_project_id(explicit: str = None) -> str:
    """Resolve project ID: explicit > query param > first project in DB > 'icdev-platform'."""
    pid = explicit or request.args.get("project_id")
    if pid:
        return pid
    try:
        conn = _get_db()
        row = conn.execute("SELECT id FROM projects ORDER BY created_at ASC LIMIT 1").fetchone()
        conn.close()
        if row:
            return row["id"]
    except Exception:
        pass
    return "icdev-platform"


def _safe_count(conn, table, project_id=None):
    try:
        if project_id:
            row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table} WHERE project_id = ?", (project_id,)).fetchone()  # nosec B608 -- table/column names are internal constants, not user input
        else:
            row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()  # nosec B608 -- table/column names are internal constants, not user input
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
            "telemetry_calls": 0,
            "telemetry_tokens": 0,
            "agentic_design_count": 0,
        }

        # Telemetry counts
        try:
            row = conn.execute("SELECT COUNT(*) as c, SUM(input_tokens+output_tokens) as t FROM ai_telemetry").fetchone()
            if row:
                stats["telemetry_calls"] = row["c"] or 0
                stats["telemetry_tokens"] = row["t"] or 0
        except Exception:
            pass

        # Agentic design count + designs list
        try:
            sys.path.insert(0, str(BASE_DIR))
            from tools.agentic_ai_canvas.db.init_db import get_connection as aac_conn
            ac = aac_conn()
            row = ac.execute("SELECT COUNT(*) as c FROM aadc_designs").fetchone()
            if row:
                stats["agentic_design_count"] = row["c"] or 0
            drows = ac.execute(
                "SELECT id, name, domain, classification, created_at FROM aadc_designs ORDER BY created_at DESC"
            ).fetchall()
            stats["designs"] = [dict(r) for r in drows]
            ac.close()
        except Exception:
            stats["designs"] = []

        # Get latest fairness score
        try:
            where = "WHERE project_id = ?" if project_id else ""
            params = (project_id,) if project_id else ()
            row = conn.execute(
                f"SELECT overall_score FROM fairness_assessments {where} ORDER BY created_at DESC LIMIT 1",  # nosec B608 -- table/column names are internal constants, not user input
                params,
            ).fetchone()
            if row:
                stats["fairness_score"] = round(row["overall_score"], 1)
        except Exception:
            pass

        # Compute transparency score from framework coverage averages
        try:
            assessment_tables = [
                "omb_m25_21_assessments",
                "omb_m26_04_assessments",
                "nist_ai_600_1_assessments",
                "gao_ai_assessments",
            ]
            pid = _resolve_project_id(project_id)
            coverages = []
            for tbl in assessment_tables:
                try:
                    total = conn.execute(
                        f"SELECT COUNT(DISTINCT requirement_id) as cnt FROM {tbl} WHERE project_id = ?",
                        (pid,),  # nosec B608 -- table/column names are internal constants, not user input
                    ).fetchone()
                    satisfied = conn.execute(
                        f"SELECT COUNT(DISTINCT requirement_id) as cnt FROM {tbl} WHERE project_id = ? AND status IN ('satisfied', 'partially_satisfied')",  # nosec B608 -- table/column names are internal constants, not user input
                        (pid,),
                    ).fetchone()
                    if total and total["cnt"] > 0:
                        coverages.append(round(satisfied["cnt"] / total["cnt"] * 100, 1))
                except Exception:
                    pass
            if coverages:
                framework_avg = round(sum(coverages) / len(coverages), 1)
                artifact_score = (
                    100.0
                    if all(
                        [
                            stats["inventory_count"] > 0,
                            stats["model_card_count"] > 0,
                            stats["system_card_count"] > 0,
                            stats["confabulation_count"] > 0,
                        ]
                    )
                    else 50.0
                )
                fairness = stats["fairness_score"] or 0
                stats["transparency_score"] = round(0.4 * framework_avg + 0.4 * artifact_score + 0.2 * fairness, 1)
        except Exception:
            pass

        # Telemetry breakdown (inline — avoids routing conflicts with legacy alias)
        try:
            rows = conn.execute(
                "SELECT provider, model_id, COUNT(*) as calls, "
                "SUM(input_tokens + output_tokens) as tokens "
                "FROM ai_telemetry GROUP BY provider, model_id ORDER BY calls DESC LIMIT 20"
            ).fetchall()
            funcs = conn.execute(
                "SELECT function, COUNT(*) as c FROM ai_telemetry GROUP BY function ORDER BY c DESC LIMIT 8"
            ).fetchall()
            stats["telemetry_breakdown"] = [dict(r) for r in rows]
            stats["telemetry_functions"] = [dict(r) for r in funcs]
        except Exception:
            stats["telemetry_breakdown"] = []
            stats["telemetry_functions"] = []

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
                    f"SELECT COUNT(DISTINCT requirement_id) as cnt FROM {table} {where}",
                    params,  # nosec B608 -- table/column names are internal constants, not user input
                ).fetchone()["cnt"]
                satisfied = conn.execute(
                    f"SELECT COUNT(DISTINCT requirement_id) as cnt FROM {table} {where} {'AND' if project_id else 'WHERE'} status IN ('satisfied', 'partially_satisfied')",  # nosec B608 -- table/column names are internal constants, not user input
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
            f"SELECT * FROM ai_use_case_inventory {where} ORDER BY name",
            params,  # nosec B608 -- table/column names are internal constants, not user input
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
            f"SELECT id, project_id, model_name, version, created_at FROM model_cards {where} ORDER BY created_at DESC",  # nosec B608 -- table/column names are internal constants, not user input
            params,
        ).fetchall()
        conn.close()
        return jsonify({"cards": [dict(r) for r in rows], "total": len(rows)})
    except Exception as e:
        return jsonify({"cards": [], "total": 0, "error": str(e)})


@ai_transparency_api.route("/gaps", methods=["GET"])
def get_gaps():
    """Get transparency gaps from latest audit."""
    project_id = _resolve_project_id()
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from ai_transparency_audit import run_transparency_audit

        result = run_transparency_audit(project_id, db_path=DB_PATH)
        return jsonify({"gaps": result.get("gaps", []), "gap_count": result.get("gap_count", 0)})
    except Exception as e:
        return jsonify({"gaps": [], "gap_count": 0, "error": str(e)})


@ai_transparency_api.route("/audit", methods=["POST"])
def run_audit():
    """Run full transparency audit."""
    data = request.get_json(silent=True) or {}
    project_id = _resolve_project_id(data.get("project_id"))
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
    project_id = _resolve_project_id(data.get("project_id"))
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
    project_id = _resolve_project_id(data.get("project_id"))
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from system_card_generator import generate_system_card as gen

        result = gen(project_id, db_path=DB_PATH)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
