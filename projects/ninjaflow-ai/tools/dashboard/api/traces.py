#!/usr/bin/env python3
# CUI // SP-CTI
"""Traces API Blueprint — REST endpoints for observability (Phase 46).

Provides /api/traces/*, /api/provenance/*, /api/xai/* endpoints
for the dashboard trace explorer, provenance viewer, and XAI dashboard.
"""

import json
import os
import sqlite3
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

try:
    from tools.compat.db_utils import get_db_connection
except ImportError:
    get_db_connection = None

traces_api = Blueprint("traces_api", __name__, url_prefix="/api/traces")


def _get_db() -> sqlite3.Connection:
    if get_db_connection:
        return get_db_connection(DB_PATH)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ── Trace endpoints ──────────────────────────────────────────────


@traces_api.route("/", methods=["GET"])
def list_traces():
    """List recent traces with summary stats."""
    project_id = request.args.get("project_id")
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    try:
        conn = _get_db()
        # Get distinct traces with span count and time range
        where = "WHERE project_id = ?" if project_id else ""
        params = (project_id,) if project_id else ()

        traces = conn.execute(f"""
            SELECT trace_id,
                   COUNT(*) as span_count,
                   MIN(start_time) as first_span,
                   MAX(end_time) as last_span,
                   SUM(duration_ms) as total_duration_ms,
                   project_id,
                   GROUP_CONCAT(DISTINCT name) as span_names
            FROM otel_spans {where}
            GROUP BY trace_id
            ORDER BY first_span DESC
            LIMIT ? OFFSET ?
        """, params + (limit, offset)).fetchall()

        total = conn.execute(
            f"SELECT COUNT(DISTINCT trace_id) FROM otel_spans {where}", params
        ).fetchone()[0]

        conn.close()
        return jsonify({
            "traces": [dict(t) for t in traces],
            "total": total,
            "limit": limit,
            "offset": offset,
        })
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500


@traces_api.route("/<trace_id>", methods=["GET"])
def get_trace(trace_id: str):
    """Get all spans for a trace (waterfall view)."""
    try:
        conn = _get_db()
        spans = conn.execute(
            """SELECT * FROM otel_spans
               WHERE trace_id = ?
               ORDER BY start_time ASC""",
            (trace_id,),
        ).fetchall()
        conn.close()

        if not spans:
            return jsonify({"error": "Trace not found"}), 404

        return jsonify({
            "trace_id": trace_id,
            "span_count": len(spans),
            "spans": [dict(s) for s in spans],
        })
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500


@traces_api.route("/stats", methods=["GET"])
def trace_stats():
    """Get aggregate trace statistics."""
    project_id = request.args.get("project_id")

    try:
        conn = _get_db()
        where = "WHERE project_id = ?" if project_id else ""
        params = (project_id,) if project_id else ()

        stats = {
            "total_spans": conn.execute(
                f"SELECT COUNT(*) FROM otel_spans {where}", params
            ).fetchone()[0],
            "total_traces": conn.execute(
                f"SELECT COUNT(DISTINCT trace_id) FROM otel_spans {where}", params
            ).fetchone()[0],
            "mcp_tool_calls": conn.execute(
                f"SELECT COUNT(*) FROM otel_spans {where} {'AND' if where else 'WHERE'} name = 'mcp.tool_call'",
                params,
            ).fetchone()[0],
            "error_spans": conn.execute(
                f"SELECT COUNT(*) FROM otel_spans {where} {'AND' if where else 'WHERE'} status_code = 'ERROR'",
                params,
            ).fetchone()[0],
        }

        # Avg duration
        avg = conn.execute(
            f"SELECT AVG(duration_ms) FROM otel_spans {where}", params
        ).fetchone()[0]
        stats["avg_duration_ms"] = round(avg, 2) if avg else 0

        conn.close()
        return jsonify(stats)
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500


# ── Provenance endpoints ─────────────────────────────────────────

provenance_api = Blueprint("provenance_api", __name__, url_prefix="/api/provenance")


@provenance_api.route("/entities", methods=["GET"])
def list_entities():
    """List provenance entities."""
    project_id = request.args.get("project_id")
    limit = min(int(request.args.get("limit", 50)), 200)

    try:
        conn = _get_db()
        where = "WHERE project_id = ?" if project_id else ""
        params = (project_id,) if project_id else ()

        entities = conn.execute(
            f"SELECT * FROM prov_entities {where} ORDER BY created_at DESC LIMIT ?",
            params + (limit,),
        ).fetchall()

        total = conn.execute(
            f"SELECT COUNT(*) FROM prov_entities {where}", params
        ).fetchone()[0]

        conn.close()
        return jsonify({"entities": [dict(e) for e in entities], "total": total})
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500


@provenance_api.route("/activities", methods=["GET"])
def list_activities():
    """List provenance activities."""
    project_id = request.args.get("project_id")
    limit = min(int(request.args.get("limit", 50)), 200)

    try:
        conn = _get_db()
        where = "WHERE project_id = ?" if project_id else ""
        params = (project_id,) if project_id else ()

        activities = conn.execute(
            f"SELECT * FROM prov_activities {where} ORDER BY created_at DESC LIMIT ?",
            params + (limit,),
        ).fetchall()

        total = conn.execute(
            f"SELECT COUNT(*) FROM prov_activities {where}", params
        ).fetchone()[0]

        conn.close()
        return jsonify({"activities": [dict(a) for a in activities], "total": total})
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500


@provenance_api.route("/relations", methods=["GET"])
def list_relations():
    """List provenance relations."""
    project_id = request.args.get("project_id")
    limit = min(int(request.args.get("limit", 50)), 200)

    try:
        conn = _get_db()
        where = "WHERE project_id = ?" if project_id else ""
        params = (project_id,) if project_id else ()

        relations = conn.execute(
            f"SELECT * FROM prov_relations {where} ORDER BY created_at DESC LIMIT ?",
            params + (limit,),
        ).fetchall()

        conn.close()
        return jsonify({"relations": [dict(r) for r in relations]})
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500


@provenance_api.route("/lineage/<entity_id>", methods=["GET"])
def get_lineage(entity_id: str):
    """Get lineage for an entity."""
    direction = request.args.get("direction", "backward")
    max_depth = min(int(request.args.get("max_depth", 50)), 100)

    try:
        from tools.observability.provenance.prov_recorder import ProvRecorder
        recorder = ProvRecorder(db_path=DB_PATH)
        lineage = recorder.get_lineage(entity_id, direction=direction, max_depth=max_depth)
        return jsonify({"entity_id": entity_id, "direction": direction, "lineage": lineage})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@provenance_api.route("/export", methods=["GET"])
def export_prov():
    """Export provenance as PROV-JSON."""
    project_id = request.args.get("project_id")

    try:
        from tools.observability.provenance.prov_recorder import ProvRecorder
        recorder = ProvRecorder(db_path=DB_PATH, project_id=project_id)
        prov_json = recorder.export_prov_json(project_id=project_id)
        return jsonify(prov_json)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── XAI endpoints ────────────────────────────────────────────────

xai_api = Blueprint("xai_api", __name__, url_prefix="/api/xai")


@xai_api.route("/assess", methods=["POST"])
def run_assessment():
    """Run XAI compliance assessment for a project."""
    data = request.get_json(force=True, silent=True) or {}
    project_id = data.get("project_id")

    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    try:
        from tools.compliance.xai_assessor import XAIAssessor
        assessor = XAIAssessor(db_path=DB_PATH)
        project = {"id": project_id}
        results = assessor.get_automated_checks(project)
        return jsonify({
            "project_id": project_id,
            "framework": "xai",
            "checks": results,
            "satisfied": sum(1 for s in results.values() if s == "satisfied"),
            "total": len(results),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@xai_api.route("/shap/<trace_id>", methods=["GET"])
def get_shap(trace_id: str):
    """Get SHAP attributions for a trace."""
    try:
        conn = _get_db()
        rows = conn.execute(
            "SELECT * FROM shap_attributions WHERE trace_id = ? ORDER BY shapley_value DESC",
            (trace_id,),
        ).fetchall()
        conn.close()

        if not rows:
            return jsonify({"error": "No SHAP data for trace"}), 404

        return jsonify({
            "trace_id": trace_id,
            "attributions": [dict(r) for r in rows],
        })
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500


@xai_api.route("/shap/analyze", methods=["POST"])
def analyze_shap():
    """Run SHAP analysis on a trace."""
    data = request.get_json(force=True, silent=True) or {}
    trace_id = data.get("trace_id")
    iterations = min(int(data.get("iterations", 1000)), 5000)

    if not trace_id:
        return jsonify({"error": "trace_id required"}), 400

    try:
        from tools.observability.shap.agent_shap import AgentSHAP
        shap = AgentSHAP(db_path=DB_PATH)
        result = shap.analyze_trace(trace_id, iterations=iterations)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@xai_api.route("/summary", methods=["GET"])
def xai_summary():
    """Get XAI observability summary statistics."""
    project_id = request.args.get("project_id")

    try:
        conn = _get_db()
        where = "WHERE project_id = ?" if project_id else ""
        params = (project_id,) if project_id else ()

        summary = {}

        # Trace stats
        summary["total_spans"] = conn.execute(
            f"SELECT COUNT(*) FROM otel_spans {where}", params
        ).fetchone()[0]
        summary["total_traces"] = conn.execute(
            f"SELECT COUNT(DISTINCT trace_id) FROM otel_spans {where}", params
        ).fetchone()[0]

        # Provenance stats
        summary["prov_entities"] = conn.execute(
            f"SELECT COUNT(*) FROM prov_entities {where}", params
        ).fetchone()[0]
        summary["prov_activities"] = conn.execute(
            f"SELECT COUNT(*) FROM prov_activities {where}", params
        ).fetchone()[0]
        summary["prov_relations"] = conn.execute(
            f"SELECT COUNT(*) FROM prov_relations {where}", params
        ).fetchone()[0]

        # SHAP stats
        summary["shap_analyses"] = conn.execute(
            f"SELECT COUNT(DISTINCT trace_id) FROM shap_attributions {where}", params
        ).fetchone()[0]

        conn.close()
        return jsonify(summary)
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500
