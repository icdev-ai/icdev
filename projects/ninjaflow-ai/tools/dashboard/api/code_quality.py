#!/usr/bin/env python3
# CUI // SP-CTI
"""Code Quality API Blueprint — REST endpoints for code intelligence dashboard (Phase 52, D331-D337).

Provides /api/code-quality/* endpoints: scan, metrics summary, function detail,
trend data, runtime feedback, and health scores.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

try:
    from tools.compat.db_utils import get_db_connection
except ImportError:
    get_db_connection = None

code_quality_api = Blueprint("code_quality_api", __name__, url_prefix="/api/code-quality")


def _get_db() -> sqlite3.Connection:
    if get_db_connection:
        return get_db_connection(DB_PATH)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ── Metrics Summary ────────────────────────────────────────────

@code_quality_api.route("/summary", methods=["GET"])
def metrics_summary():
    """Summary statistics from latest scan."""
    try:
        conn = _get_db()
        # Get latest scan_id
        latest = conn.execute(
            "SELECT scan_id, MAX(created_at) as ts FROM code_quality_metrics GROUP BY scan_id ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if not latest:
            conn.close()
            return jsonify({"error": "No scan data found", "has_data": False})

        scan_id = latest["scan_id"]
        rows = conn.execute(
            "SELECT * FROM code_quality_metrics WHERE scan_id = ?", (scan_id,)
        ).fetchall()
        conn.close()

        metrics = [dict(r) for r in rows]
        fn_metrics = [m for m in metrics if m.get("function_name")]
        total_loc = sum(m.get("loc", 0) for m in metrics)
        total_smells = sum(m.get("smell_count", 0) for m in metrics)
        avg_cc = round(sum(m.get("cyclomatic_complexity", 0) for m in fn_metrics) / max(len(fn_metrics), 1), 2)
        avg_maint = round(sum(m.get("maintainability_score", 0) for m in fn_metrics) / max(len(fn_metrics), 1), 4)
        high_cc = len([m for m in fn_metrics if m.get("cyclomatic_complexity", 0) > 15])

        return jsonify({
            "has_data": True,
            "scan_id": scan_id,
            "total_files": len(set(m.get("file_path") for m in metrics)),
            "total_functions": len(fn_metrics),
            "total_loc": total_loc,
            "total_smells": total_smells,
            "avg_complexity": avg_cc,
            "avg_maintainability": avg_maint,
            "high_complexity_count": high_cc,
        })
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500


# ── Top Complex Functions ──────────────────────────────────────

@code_quality_api.route("/top-complex", methods=["GET"])
def top_complex():
    """Top N most complex functions."""
    limit = min(int(request.args.get("limit", 20)), 100)
    try:
        conn = _get_db()
        rows = conn.execute(
            """SELECT function_name, file_path, cyclomatic_complexity, cognitive_complexity,
                      nesting_depth, parameter_count, smell_count, maintainability_score, loc
               FROM code_quality_metrics
               WHERE function_name IS NOT NULL
               ORDER BY cyclomatic_complexity DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        conn.close()
        return jsonify({"functions": [dict(r) for r in rows]})
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500


# ── Smell Breakdown ────────────────────────────────────────────

@code_quality_api.route("/smells", methods=["GET"])
def smell_breakdown():
    """Smell type breakdown across all scanned functions."""
    try:
        conn = _get_db()
        rows = conn.execute(
            """SELECT smells_json FROM code_quality_metrics
               WHERE smell_count > 0 AND smells_json != '[]'
               ORDER BY created_at DESC LIMIT 500"""
        ).fetchall()
        conn.close()

        counts = {}
        for r in rows:
            try:
                smells = json.loads(r["smells_json"])
                for s in smells:
                    counts[s] = counts.get(s, 0) + 1
            except (json.JSONDecodeError, TypeError):
                pass

        return jsonify({"smells": [{"name": k, "count": v} for k, v in sorted(counts.items(), key=lambda x: -x[1])]})
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500


# ── Trend Data ─────────────────────────────────────────────────

@code_quality_api.route("/trend", methods=["GET"])
def trend_data():
    """Maintainability trend over scans."""
    project_id = request.args.get("project_id", "icdev")
    try:
        conn = _get_db()
        rows = conn.execute(
            """SELECT scan_id, MIN(created_at) as scan_date,
                      AVG(cyclomatic_complexity) as avg_complexity,
                      AVG(maintainability_score) as avg_maintainability,
                      SUM(smell_count) as total_smells,
                      COUNT(DISTINCT file_path) as files_scanned
               FROM code_quality_metrics
               WHERE project_id = ? OR ?1 IS NULL
               GROUP BY scan_id
               ORDER BY scan_date ASC
               LIMIT 30""",
            (project_id,),
        ).fetchall()
        conn.close()
        return jsonify({"trend": [dict(r) for r in rows]})
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500


# ── Runtime Feedback Stats ─────────────────────────────────────

@code_quality_api.route("/feedback", methods=["GET"])
def feedback_stats():
    """Runtime feedback summary — test pass rates by source function."""
    limit = min(int(request.args.get("limit", 20)), 100)
    try:
        conn = _get_db()
        rows = conn.execute(
            """SELECT source_function,
                      COUNT(*) as test_total,
                      SUM(CASE WHEN test_passed = 1 THEN 1 ELSE 0 END) as test_passed,
                      AVG(test_duration_ms) as avg_duration
               FROM runtime_feedback
               WHERE source_function IS NOT NULL
               GROUP BY source_function
               ORDER BY test_total DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        conn.close()

        results = []
        for r in rows:
            total = r["test_total"]
            passed = r["test_passed"]
            results.append({
                "source_function": r["source_function"],
                "test_total": total,
                "test_passed": passed,
                "pass_rate": round(passed / max(total, 1), 4),
                "avg_duration_ms": round(r["avg_duration"] or 0, 2),
            })
        return jsonify({"feedback": results})
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500


# ── Trigger Scan ───────────────────────────────────────────────

@code_quality_api.route("/scan", methods=["POST"])
def trigger_scan():
    """Trigger a code quality scan on the tools/ directory."""
    try:
        sys.path.insert(0, str(BASE_DIR))
        from tools.analysis.code_analyzer import CodeAnalyzer
        analyzer = CodeAnalyzer(
            project_dir=str(BASE_DIR / "tools"),
            project_id="icdev",
            db_path=DB_PATH,
        )
        result = analyzer.scan_directory()
        # Store metrics
        try:
            stored = analyzer.store_metrics(
                result.get("metrics", []),
                result.get("scan_id", ""),
                db_path=DB_PATH,
            )
            result["stored_rows"] = stored
        except Exception:
            result["stored_rows"] = 0
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
