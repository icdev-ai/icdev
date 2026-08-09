#!/usr/bin/env python3
# CUI // SP-CTI
"""AI Transparency API Blueprint — REST endpoints for Phase 48 dashboard."""

import os
import sqlite3
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request


BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, table_exists  # noqa: E402
from tools.dashboard.auth import require_role  # noqa: E402

# Compliance-posture mutations (audits, card generation) are restricted to
# security/compliance roles, mirroring GOVCON_WRITE_ROLES in api/govcon.py.
COMPLIANCE_WRITE_ROLES = ("admin", "isso", "ciso")

ai_transparency_api = Blueprint("ai_transparency_api", __name__, url_prefix="/api/ai-transparency")


class _PGCompatConn:
    """Silently pre-translate ? → %s for PG so translate_sql never warns."""
    def __init__(self, conn):
        self._conn = conn
        self._pg = getattr(conn, "_backend", "sqlite") == "postgresql"
    def _fix(self, sql):
        return sql.replace("?", "%s") if self._pg and "?" in sql else sql
    def execute(self, sql, params=()):
        return self._conn.execute(self._fix(sql), params)
    def executemany(self, sql, seq):
        return self._conn.executemany(self._fix(sql), seq)
    def commit(self): return self._conn.commit()
    def rollback(self): return self._conn.rollback()
    def close(self): return self._conn.close()
    def __getattr__(self, name): return getattr(self._conn, name)


def _get_db() -> sqlite3.Connection:
    conn = get_connection(db_path=str(DB_PATH))
    return _PGCompatConn(conn)


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
    """COUNT rows in ``table``.

    A missing table returns 0 (feature not yet migrated). A genuine query
    failure is NOT swallowed to 0 — masking it would render a failed
    compliance read as "0 inventory / 0 model cards", a fabricated-healthy
    posture. The error propagates so get_stats returns HTTP 500.

    Uses ``?`` (not a raw ``%s``) so ``_PGCompatConn`` rewrites it to ``%s``
    on PostgreSQL and SQLite keeps its qmark paramstyle.
    """
    if not table_exists(conn, table):
        return 0
    if project_id:
        row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table} WHERE project_id = ?", (project_id,)).fetchone()  # nosec B608 -- table/column names are internal constants, not user input
    else:
        row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()  # nosec B608 -- table/column names are internal constants, not user input
    return row["cnt"] if row else 0


@ai_transparency_api.route("/telemetry", methods=["GET"])
def get_telemetry():
    """AI telemetry breakdown by provider/model."""
    try:
        conn = _get_db()
        rows = conn.execute(
            "SELECT provider, model_id, COUNT(*) as calls, "
            "SUM(input_tokens + output_tokens) as tokens "
            "FROM ai_telemetry GROUP BY provider, model_id ORDER BY calls DESC LIMIT 20"
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) as c, SUM(input_tokens + output_tokens) as t FROM ai_telemetry"
        ).fetchone()
        funcs = conn.execute(
            "SELECT function, COUNT(*) as c FROM ai_telemetry GROUP BY function ORDER BY c DESC LIMIT 8"
        ).fetchall()
        conn.close()
        return jsonify({
            "breakdown": [dict(r) for r in rows],
            "total_calls": total["c"] if total else 0,
            "total_tokens": total["t"] if total else 0,
            "top_functions": [dict(r) for r in funcs],
        })
    except Exception as e:
        # Fail loud: a telemetry read error must not render as "0 calls".
        return jsonify({"error": str(e)}), 500


@ai_transparency_api.route("/designs", methods=["GET"])
def get_designs():
    """Agentic AI canvas designs."""
    try:
        sys.path.insert(0, str(BASE_DIR))
        from tools.agentic_ai_canvas.db.init_db import get_connection as aac_conn
        ac = aac_conn()
        rows = ac.execute(
            "SELECT id, name, domain, classification, created_at FROM aadc_designs ORDER BY created_at DESC"
        ).fetchall()
        ac.close()
        return jsonify({"designs": [dict(r) for r in rows], "total": len(rows)})
    except Exception as e:
        # Fail loud: a design-catalog read error must not render as empty.
        return jsonify({"error": str(e)}), 500


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
            # nav-comp-06: the transparency score is an explicit heuristic, not a
            # calibrated model. Label it as such in the payload so consumers never
            # read it as an authoritative posture measurement.
            "transparency_score_method": "heuristic",
            "transparency_score_note": (
                "Heuristic composite = 0.4 * framework coverage + 0.4 * artifact "
                "presence + 0.2 * fairness. Not a calibrated risk model; absent "
                "artifacts score 0 (no floor)."
            ),
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

        # Agentic design count
        try:
            sys.path.insert(0, str(BASE_DIR))
            from tools.agentic_ai_canvas.db.init_db import get_connection as aac_conn
            ac = aac_conn()
            row = ac.execute("SELECT COUNT(*) as c FROM aadc_designs").fetchone()
            if row:
                stats["agentic_design_count"] = row["c"] or 0
            ac.close()
        except Exception:
            pass

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
                        f"SELECT COUNT(DISTINCT requirement_id) as cnt FROM {tbl} WHERE project_id = %s",
                        (pid,),  # nosec B608 -- table/column names are internal constants, not user input
                    ).fetchone()
                    satisfied = conn.execute(
                        f"SELECT COUNT(DISTINCT requirement_id) as cnt FROM {tbl} WHERE project_id = %s AND status IN ('satisfied', 'partially_satisfied')",  # nosec B608 -- table/column names are internal constants, not user input
                        (pid,),
                    ).fetchone()
                    if total and total["cnt"] > 0:
                        coverages.append(round(satisfied["cnt"] / total["cnt"] * 100, 1))
                except Exception:
                    pass
            if coverages:
                framework_avg = round(sum(coverages) / len(coverages), 1)
                # Heuristic: transparency = 0.4 framework + 0.4 artifact + 0.2 fairness.
                # nav-comp-06: artifact presence is scored proportionally — each of
                # the four artifact types contributes an equal share and absent
                # artifacts score 0. The old code applied an artificial 50/100 floor
                # (any incomplete set still scored 50), which manufactured posture
                # for systems with no transparency artifacts at all.
                artifact_present = [
                    stats["inventory_count"] > 0,
                    stats["model_card_count"] > 0,
                    stats["system_card_count"] > 0,
                    stats["confabulation_count"] > 0,
                ]
                artifact_score = round(
                    sum(1 for present in artifact_present if present)
                    / len(artifact_present) * 100,
                    1,
                )
                fairness = stats["fairness_score"] or 0
                stats["transparency_score"] = round(0.4 * framework_avg + 0.4 * artifact_score + 0.2 * fairness, 1)
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
            # Missing table = framework not yet assessed (honest 0/0). A genuine
            # query error must NOT be masked as a real 0% coverage — let it
            # propagate to the outer handler's HTTP 500.
            if not table_exists(conn, table):
                frameworks.append({"name": name, "coverage": 0, "total": 0})
                continue
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
        # Fail loud: an inventory read error must not render as empty.
        return jsonify({"error": str(e)}), 500


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
        # Fail loud: a model-card read error must not render as empty.
        return jsonify({"error": str(e)}), 500


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
        # Fail loud: an audit failure must not render as "0 transparency gaps".
        return jsonify({"error": str(e)}), 500


@ai_transparency_api.route("/audit", methods=["POST"])
@require_role(*COMPLIANCE_WRITE_ROLES)
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
@require_role(*COMPLIANCE_WRITE_ROLES)
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
@require_role(*COMPLIANCE_WRITE_ROLES)
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
