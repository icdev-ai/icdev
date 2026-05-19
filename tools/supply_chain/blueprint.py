# CUI // SP-CTI
"""ICDEV™ Supply Chain Intelligence — Flask Blueprint.

Mounted at /supply_chain and /api/supply_chain inside the ICDEV dashboard.
Provides vendor registry, SCRM assessments, CVE triage queue, ISA agreements,
and SBOM records, backed by the supply chain DB tables in icdev.db.

Usage in app.py:
    from tools.supply_chain.blueprint import create_supply_chain_blueprint
    bp = create_supply_chain_blueprint()
    if bp:
        app.register_blueprint(bp)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

logger = logging.getLogger("icdev.supply_chain")

_TMPL_DIR = Path(__file__).resolve().parents[2] / "tools" / "dashboard" / "templates"

_SC_IQE_EXAMPLES = [
    {"label": "All vendors", "query": "show all supply chain vendors"},
    {"label": "High risk", "query": "show vendors with high or critical SCRM risk tier"},
    {"label": "Open CVEs", "query": "show open CVEs with critical or high severity"},
    {"label": "Expiring ISAs", "query": "show ISA agreements expiring within 90 days"},
    {"label": "Section 889", "query": "show vendors under review for Section 889"},
]


def create_supply_chain_blueprint() -> Blueprint:
    bp = Blueprint(
        "supply_chain",
        __name__,
        template_folder=str(_TMPL_DIR),
    )

    def _db():
        from tools.db.storage import get_connection
        conn = get_connection()
        conn.set_security_context(None)
        return conn

    def _rows(cur) -> list[dict]:
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    # ── Page ──────────────────────────────────────────────────────────────────

    @bp.route("/supply_chain")
    def supply_chain_page():
        try:
            from tools.compliance.classification_manager import get_banner
            banner = get_banner()
        except Exception:
            banner = "CUI // SP-CTI"
        return render_template(
            "supply_chain.html",
            classification_banner=banner,
            iqe_canvas="supply_chain",
            iqe_api_route="/api/supply_chain/iqe-query",
            iqe_title="Supply Chain IQE",
            iqe_examples=_SC_IQE_EXAMPLES,
        )

    # ── Stats ─────────────────────────────────────────────────────────────────

    @bp.route("/api/supply_chain/stats")
    def sc_stats():
        try:
            conn = _db()
            vendor_count = conn.execute("SELECT COUNT(*) FROM supply_chain_vendors").fetchone()[0]
            high_risk = conn.execute(
                "SELECT COUNT(*) FROM supply_chain_vendors "
                "WHERE scrm_risk_tier IN ('high','critical')"
            ).fetchone()[0]
            open_cves = conn.execute(
                "SELECT COUNT(*) FROM cve_triage "
                "WHERE triage_decision IN ('remediate','mitigate') AND remediated_at IS NULL"
            ).fetchone()[0]
            cutoff = (datetime.now(timezone.utc) + timedelta(days=90)).strftime("%Y-%m-%d")
            expiring_isas = conn.execute(
                "SELECT COUNT(*) FROM isa_agreements "
                "WHERE status IN ('active','expiring') AND expiry_date <= ?", (cutoff,)
            ).fetchone()[0]
            critical_cves = conn.execute(
                "SELECT COUNT(*) FROM cve_triage WHERE severity='critical' AND remediated_at IS NULL"
            ).fetchone()[0]
            return jsonify({
                "vendor_count": vendor_count,
                "high_risk_vendors": high_risk,
                "open_cves": open_cves,
                "critical_cves": critical_cves,
                "expiring_isas": expiring_isas,
            })
        except Exception as exc:
            logger.warning("sc_stats error: %s", exc)
            return jsonify({"vendor_count": 0, "high_risk_vendors": 0,
                            "open_cves": 0, "critical_cves": 0, "expiring_isas": 0})

    # ── Vendors ───────────────────────────────────────────────────────────────

    @bp.route("/api/supply_chain/vendors")
    def sc_vendors():
        try:
            conn = _db()
            rows = _rows(conn.execute(
                "SELECT v.id, v.vendor_name, v.vendor_type, v.country_of_origin, "
                "v.scrm_risk_tier, v.section_889_status, v.dod_approved, v.isa_required, "
                "v.last_assessed, v.classification, "
                "(SELECT COUNT(*) FROM scrm_assessments a WHERE a.vendor_id = v.id) AS assessment_count "
                "FROM supply_chain_vendors v "
                "ORDER BY CASE v.scrm_risk_tier "
                "  WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
                "  WHEN 'moderate' THEN 2 ELSE 3 END, v.vendor_name"
            ))
            return jsonify(rows)
        except Exception as exc:
            logger.warning("sc_vendors error: %s", exc)
            return jsonify([])

    # ── SCRM risks ────────────────────────────────────────────────────────────

    @bp.route("/api/supply_chain/scrm-risks")
    def sc_scrm_risks():
        try:
            conn = _db()
            rows = _rows(conn.execute(
                "SELECT a.id, a.vendor_id, v.vendor_name, a.package_name, "
                "a.assessment_type, a.risk_category, a.risk_score, "
                "a.likelihood, a.impact, a.residual_risk, a.assessed_by, a.assessed_at "
                "FROM scrm_assessments a "
                "LEFT JOIN supply_chain_vendors v ON v.id = a.vendor_id "
                "ORDER BY CASE a.residual_risk "
                "  WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
                "  WHEN 'moderate' THEN 2 ELSE 3 END, a.assessed_at DESC "
                "LIMIT 200"
            ))
            return jsonify(rows)
        except Exception as exc:
            logger.warning("sc_scrm_risks error: %s", exc)
            return jsonify([])

    # ── CVE queue ─────────────────────────────────────────────────────────────

    @bp.route("/api/supply_chain/cve-queue")
    def sc_cve_queue():
        try:
            conn = _db()
            rows = _rows(conn.execute(
                "SELECT id, cve_id, package_name, package_version, severity, cvss_score, "
                "exploitability, triage_decision, triage_rationale, sla_deadline, "
                "triaged_by, triaged_at, remediated_at "
                "FROM cve_triage "
                "ORDER BY CASE severity "
                "  WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
                "  WHEN 'medium' THEN 2 ELSE 3 END, sla_deadline ASC NULLS LAST "
                "LIMIT 500"
            ))
            return jsonify(rows)
        except Exception as exc:
            logger.warning("sc_cve_queue error: %s", exc)
            return jsonify([])

    # ── ISA agreements ────────────────────────────────────────────────────────

    @bp.route("/api/supply_chain/isa-agreements")
    def sc_isa_agreements():
        try:
            conn = _db()
            rows = _rows(conn.execute(
                "SELECT id, agreement_type, partner_system, partner_org, status, "
                "signed_date, expiry_date, next_review_date, poc_name, poc_email, "
                "review_cadence_days, classification "
                "FROM isa_agreements "
                "ORDER BY CASE status "
                "  WHEN 'expiring' THEN 0 WHEN 'expired' THEN 1 "
                "  WHEN 'review' THEN 2 WHEN 'active' THEN 3 "
                "  WHEN 'signed' THEN 4 ELSE 5 END, expiry_date ASC NULLS LAST "
                "LIMIT 200"
            ))
            return jsonify(rows)
        except Exception as exc:
            logger.warning("sc_isa_agreements error: %s", exc)
            return jsonify([])

    # ── SBOM records ──────────────────────────────────────────────────────────

    @bp.route("/api/supply_chain/sbom")
    def sc_sbom():
        try:
            conn = _db()
            rows = _rows(conn.execute(
                "SELECT s.id, s.project_id, p.name AS project_name, "
                "s.version, s.format, s.component_count, s.vulnerability_count, "
                "s.file_path, s.generated_at "
                "FROM sbom_records s "
                "LEFT JOIN projects p ON p.id = s.project_id "
                "ORDER BY s.generated_at DESC "
                "LIMIT 100"
            ))
            return jsonify(rows)
        except Exception as exc:
            logger.warning("sc_sbom error: %s", exc)
            return jsonify([])

    # ── IQE query ─────────────────────────────────────────────────────────────

    @bp.route("/api/supply_chain/iqe-query", methods=["POST"])
    def sc_iqe_query():
        from tools.iqe.nl_to_iqe import nl_to_iqe
        from tools.iqe.parser import parse as _parse, IQESyntaxError as _IQESyntaxError
        from tools.iqe.executor import execute_query
        import importlib

        data = request.get_json(silent=True) or {}
        question = (data.get("question") or "").strip()
        if not question:
            return jsonify({"error": "question required"}), 400

        iqe_str = ""
        try:
            importlib.import_module("tools.iqe.adapters.supply_chain")
            from tools.supply_chain.constants import IQE_COLLECTIONS
            result = nl_to_iqe(question, IQE_COLLECTIONS)
            iqe_str = result.get("iqe", "")
            explanation = result.get("explanation", "")
            rows = []
            try:
                ast = _parse(iqe_str)
                rows = execute_query(ast, conn=None)
            except _IQESyntaxError:
                pass
            return jsonify({"ok": True, "iqe": iqe_str,
                            "explanation": explanation, "results": rows, "row_count": len(rows)})
        except Exception as exc:
            logger.warning("sc_iqe_query error: %s", exc)
            return jsonify({"error": str(exc), "iqe": iqe_str}), 500

    return bp
