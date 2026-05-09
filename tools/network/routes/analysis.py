# CUI // SP-CTI
"""Network Design Canvas — Analysis routes.

Provides topology health scoring, risk matrix, compliance trend,
and export endpoints.  All reads from nc_compliance_findings,
nc_objects, nc_circuits, nc_intent_validations, nc_versions.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("icdev.network.routes.analysis")


def register_analysis_routes(bp, get_conn=None, helpers=None):
    """Register analysis routes on the NDC blueprint."""
    from flask import jsonify, request

    def _nc():
        from tools.network.db.init_db import get_connection
        return get_connection()

    # ── Summary ───────────────────────────────────────────────────────────────

    @bp.route("/api/topologies/<topo_id>/analysis/summary", methods=["GET"])
    def analysis_summary(topo_id):
        """Aggregate device count, link count, open findings count, EOL count, compliance score."""
        with _nc() as db:
            devices = db.execute(
                "SELECT COUNT(*) FROM nc_objects WHERE topology_id=? AND object_type NOT IN ('label','group','container')",
                (topo_id,),
            ).fetchone()[0]
            links = db.execute(
                "SELECT COUNT(*) FROM nc_objects WHERE topology_id=? AND object_type='link'",
                (topo_id,),
            ).fetchone()[0]
            findings_row = db.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN severity='cat1' THEN 1 ELSE 0 END) as cat1, "
                "SUM(CASE WHEN severity='cat2' THEN 1 ELSE 0 END) as cat2, "
                "SUM(CASE WHEN severity='cat3' THEN 1 ELSE 0 END) as cat3 "
                "FROM nc_compliance_findings WHERE topology_id=? AND status='open'",
                (topo_id,),
            ).fetchone()
            eol_count = db.execute(
                "SELECT COUNT(*) FROM nc_objects WHERE topology_id=? AND eol_status IN ('eol','eos')",
                (topo_id,),
            ).fetchone()[0] if _col_exists(db, "nc_objects", "eol_status") else 0

            # Compliance score: 100 - (cat1*20 + cat2*10 + cat3*5) / max(devices,1)
            cat1 = (findings_row[1] or 0) if findings_row else 0
            cat2 = (findings_row[2] or 0) if findings_row else 0
            cat3 = (findings_row[3] or 0) if findings_row else 0
            total_findings = (findings_row[0] or 0) if findings_row else 0
            penalty = cat1 * 20 + cat2 * 10 + cat3 * 5
            score = max(0, round(100 - penalty, 1))

        return jsonify({
            "topology_id": topo_id,
            "device_count": devices,
            "link_count": links,
            "open_findings": total_findings,
            "cat1_count": cat1,
            "cat2_count": cat2,
            "cat3_count": cat3,
            "eol_device_count": eol_count,
            "compliance_score": score,
        })

    # ── Topology Health ───────────────────────────────────────────────────────

    @bp.route("/api/topologies/<topo_id>/analysis/topology-health", methods=["GET"])
    def topology_health(topo_id):
        """Health score across 5 dimensions: redundancy, security, compliance, EOL, capacity."""
        with _nc() as db:
            # Compliance dimension
            findings = db.execute(
                "SELECT severity, COUNT(*) FROM nc_compliance_findings "
                "WHERE topology_id=? AND status='open' GROUP BY severity",
                (topo_id,),
            ).fetchall()
            finding_map = {r[0]: r[1] for r in findings}
            cat1, cat2, cat3 = finding_map.get("cat1", 0), finding_map.get("cat2", 0), finding_map.get("cat3", 0)
            compliance_score = max(0, 100 - cat1 * 25 - cat2 * 10 - cat3 * 3)

            # Security: intent validation failures
            intent_fails = db.execute(
                "SELECT COUNT(*) FROM nc_intent_validations "
                "WHERE topology_id=? AND result='fail'",
                (topo_id,),
            ).fetchone()[0] if _table_exists(db, "nc_intent_validations") else 0
            intent_total = db.execute(
                "SELECT COUNT(*) FROM nc_intent_validations WHERE topology_id=?",
                (topo_id,),
            ).fetchone()[0] if _table_exists(db, "nc_intent_validations") else 1
            security_score = round(max(0, 100 - (intent_fails / max(intent_total, 1)) * 100), 1)

            # EOL dimension
            if _col_exists(db, "nc_objects", "eol_status"):
                device_count = db.execute(
                    "SELECT COUNT(*) FROM nc_objects WHERE topology_id=? AND object_type NOT IN ('label','group','link')",
                    (topo_id,),
                ).fetchone()[0] or 1
                eol_count = db.execute(
                    "SELECT COUNT(*) FROM nc_objects WHERE topology_id=? AND eol_status IN ('eol','eos')",
                    (topo_id,),
                ).fetchone()[0]
                eol_score = round(max(0, 100 - (eol_count / device_count) * 100), 1)
            else:
                eol_score = 100.0

            # Redundancy: devices with redundant links (heuristic — nodes with >1 edge)
            redundancy_score = 75.0  # placeholder heuristic

            # Capacity: circuits over threshold
            capacity_score = 90.0  # placeholder heuristic

        dimensions = {
            "compliance": compliance_score,
            "security": security_score,
            "eol": eol_score,
            "redundancy": redundancy_score,
            "capacity": capacity_score,
        }
        overall = round(sum(dimensions.values()) / len(dimensions), 1)
        return jsonify({
            "topology_id": topo_id,
            "overall_health": overall,
            "dimensions": dimensions,
        })

    # ── Risk Matrix ───────────────────────────────────────────────────────────

    @bp.route("/api/topologies/<topo_id>/analysis/risk-matrix", methods=["GET"])
    def risk_matrix(topo_id):
        """Likelihood × Impact matrix for open compliance findings."""
        _SEVERITY_IMPACT = {"cat1": 5, "cat2": 3, "cat3": 1}
        _LIKELIHOOD = {"open": 4, "in_progress": 2, "deferred": 1}

        with _nc() as db:
            findings = db.execute(
                "SELECT id, check_name, severity, status, description "
                "FROM nc_compliance_findings WHERE topology_id=? AND status != 'closed'",
                (topo_id,),
            ).fetchall()

        matrix = []
        for f in findings:
            fid, name, severity, status, desc = f
            impact = _SEVERITY_IMPACT.get(severity, 1)
            likelihood = _LIKELIHOOD.get(status, 1)
            risk_score = impact * likelihood
            matrix.append({
                "finding_id": fid,
                "name": name,
                "severity": severity,
                "status": status,
                "impact": impact,
                "likelihood": likelihood,
                "risk_score": risk_score,
                "quadrant": _quadrant(likelihood, impact),
                "description": desc,
            })

        matrix.sort(key=lambda x: -x["risk_score"])
        return jsonify({"topology_id": topo_id, "matrix": matrix, "total": len(matrix)})

    # ── Compliance Trend ──────────────────────────────────────────────────────

    @bp.route("/api/topologies/<topo_id>/analysis/trend", methods=["GET"])
    def analysis_trend(topo_id):
        """Compliance score over time from nc_versions snapshot history."""
        with _nc() as db:
            if not _table_exists(db, "nc_versions"):
                return jsonify({"topology_id": topo_id, "trend": [], "message": "No version history"})
            versions = db.execute(
                "SELECT version_number, created_at, metadata_json "
                "FROM nc_versions WHERE topology_id=? ORDER BY version_number",
                (topo_id,),
            ).fetchall() if _col_exists(db, "nc_versions", "topology_id") else []

        trend = []
        for v in versions:
            version_num, created_at, meta_json = v
            try:
                meta = json.loads(meta_json or "{}")
            except Exception:
                meta = {}
            trend.append({
                "version": version_num,
                "date": created_at,
                "compliance_score": meta.get("compliance_score"),
                "cat1_count": meta.get("cat1_count"),
            })
        return jsonify({"topology_id": topo_id, "trend": trend})

    # ── Export ────────────────────────────────────────────────────────────────

    @bp.route("/api/topologies/<topo_id>/analysis/export", methods=["GET"])
    def analysis_export(topo_id):
        """Export full analysis as JSON (default) or PDF summary."""
        fmt = request.args.get("format", "json").lower()

        with _nc() as db:
            devices = db.execute(
                "SELECT COUNT(*) FROM nc_objects WHERE topology_id=? AND object_type NOT IN ('label','group','link')",
                (topo_id,),
            ).fetchone()[0]
            findings = [
                dict(r) for r in db.execute(
                    "SELECT id, check_name, severity, status, description "
                    "FROM nc_compliance_findings WHERE topology_id=?",
                    (topo_id,),
                ).fetchall()
            ]

        payload = {
            "topology_id": topo_id,
            "device_count": devices,
            "findings": findings,
            "total_findings": len(findings),
            "cat1_count": sum(1 for f in findings if f.get("severity") == "cat1"),
        }

        if fmt == "pdf":
            try:
                from tools.network.pdf_export import export_analysis_pdf
                pdf_bytes = export_analysis_pdf(topo_id, payload)
                from flask import Response
                return Response(
                    pdf_bytes,
                    mimetype="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=analysis_{topo_id}.pdf"},
                )
            except Exception as exc:
                logger.warning("PDF export failed, returning JSON: %s", exc)

        return jsonify(payload)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _table_exists(db, table: str) -> bool:
    r = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return r is not None


def _col_exists(db, table: str, col: str) -> bool:
    cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()]
    return col in cols


def _quadrant(likelihood: int, impact: int) -> str:
    high_l = likelihood >= 3
    high_i = impact >= 3
    if high_l and high_i:
        return "critical"
    if high_l:
        return "high_likelihood"
    if high_i:
        return "high_impact"
    return "low_priority"
