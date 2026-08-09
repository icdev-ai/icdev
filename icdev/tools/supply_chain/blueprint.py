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
from tools.logging.icdev_logger import get_logger

from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Blueprint, Response, g, jsonify, render_template, request

logger = get_logger("icdev.supply_chain")

_TMPL_DIR = Path(__file__).resolve().parents[2] / "tools" / "dashboard" / "templates"

_SC_IQE_EXAMPLES = [
    {"label": "All vendors", "query": "show all supply chain vendors"},
    {"label": "High risk", "query": "show vendors with high or critical SCRM risk tier"},
    {"label": "Open CVEs", "query": "show open CVEs with critical or high severity"},
    {"label": "Expiring ISAs", "query": "show ISA agreements expiring within 90 days"},
    {"label": "Section 889", "query": "show vendors under review for Section 889"},
]


def find_boundary_isa_matches(partner_systems) -> dict:
    """Cross-link supply-chain ISA partner systems to Boundary Canvas ISAs.

    NAVIGATION-ONLY: the two ISA stores stay separate (bd_isa_tracker in the
    Boundary Canvas DB vs isa_agreements in the project DB). This helper does a
    conservative, case-insensitive EXACT match of each supply-chain
    ``partner_system`` against a boundary ``bd_isa_tracker.interconnection_id``
    or the parent boundary design name, so the /supply_chain ISA listing can
    render a "related" backlink to /boundary/isa-tracker.

    Args:
        partner_systems: iterable of partner-system name strings.

    Returns:
        dict mapping each matched original partner_system string -> True.
        Fails closed: on any error (missing DB/table, empty store) returns {}
        and logs a warning — never raises, so callers never 500.
    """
    matches: dict = {}
    wanted: dict = {}
    for p in (partner_systems or []):
        norm = (p or "").strip().lower()
        if norm:
            wanted[norm] = p
    if not wanted:
        return matches
    try:
        from tools.boundary_canvas.db.init_db import get_connection as _bdc_conn

        known: set = set()
        with _bdc_conn() as conn:
            rows = conn.execute(
                "SELECT t.interconnection_id, d.name AS design_name "
                "FROM bd_isa_tracker t "
                "LEFT JOIN boundary_designs d ON t.design_id = d.id"
            ).fetchall()
        for r in rows:
            rd = dict(r)
            ic = (rd.get("interconnection_id") or "").strip().lower()
            dn = (rd.get("design_name") or "").strip().lower()
            if ic:
                known.add(ic)
            if dn:
                known.add(dn)
        for norm, original in wanted.items():
            if norm in known:
                matches[original] = True
    except Exception as exc:
        logger.warning("boundary ISA cross-link lookup failed: %s", exc)
        return {}
    return matches


def create_supply_chain_blueprint() -> Blueprint:
    bp = Blueprint(
        "supply_chain",
        __name__,
        template_folder=str(_TMPL_DIR),
    )

    def _db():
        from tools.db.storage import get_connection
        conn = get_connection()
        conn.set_security_context(None)  # rls-bypass: supply chain reads cross-tenant catalog data; session context not applicable
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
                "WHERE status IN ('active','expiring') AND expiry_date <= %s", (cutoff,)
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
            # NAVIGATION cross-link: flag rows that have a matching Boundary
            # Canvas ISA so the UI can render a backlink. Fails closed — the
            # cross-DB lookup never propagates an error to this response.
            try:
                _bd = find_boundary_isa_matches([r.get("partner_system") for r in rows])
            except Exception as exc:  # defensive; find_* already fails closed
                logger.warning("sc_isa boundary enrichment failed: %s", exc)
                _bd = {}
            for r in rows:
                r["boundary_match"] = bool(_bd.get(r.get("partner_system")))
            return jsonify(rows)
        except Exception as exc:
            logger.warning("sc_isa_agreements error: %s", exc)
            return jsonify([])

    # ── SBOM records ──────────────────────────────────────────────────────────

    @bp.route("/api/supply_chain/sbom")
    def sc_sbom():
        """Catalog listing: one row per SBOM, each with its retrieval URL.

        Metadata only — no artifact bytes cross this route, so the listing
        stays visible to anyone who can reach the page while the retrieval
        routes below apply the access decision. Each row is annotated with
        whether *this* caller would be served, so a withheld artifact reads as
        withheld rather than as a broken link.
        """
        from tools.compliance import sbom_distribution as dist

        try:
            conn = _db()
            records = dist.list_records(conn, limit=100)
            user = getattr(g, "current_user", None)
            for r in records:
                # A path on the generating host is not a delivery mechanism,
                # and publishing it leaks the host's layout for nothing.
                path = r.pop("file_path", None)
                decision = dist.evaluate_access(r, user)
                r["access"] = decision.reason
                r["accessible"] = decision.allowed
                r["conformance"] = (
                    dist.conformance({**r, "file_path": path}) if decision.allowed
                    else {"available": False, "score": None, "total": None,
                          "reason": "withheld"}
                )
            return jsonify(records)
        except Exception as exc:
            logger.warning("sc_sbom error: %s", exc)
            return jsonify([])

    # ── SBOM retrieval — Distribution and Delivery (sbx-gov-02) ───────────────
    #
    # Three addresses onto the same artifact:
    #   /api/supply_chain/sbom/versions/<project_id>  — the version index
    #   /api/supply_chain/sbom/record/<id>            — row-keyed permalink
    #   /api/supply_chain/sbom/<project_id>/<version> — the version-specific URL
    #
    # The last one is what the 2026 element names. The two static-prefixed
    # rules are ranked above it by Werkzeug, so they win for a project id that
    # happens to be "versions" or "record"; such a project could not be
    # addressed by the generic rule, which is a naming collision nobody has and
    # a 404 rather than a wrong artifact if they ever do.

    def _serve(record):
        """Apply the access decision and return the artifact's exact bytes.

        The role check lives in ``sbom_distribution.evaluate_access`` rather
        than in a ``@require_role`` decorator so that one function answers
        every leg of the decision — role, classification and tenant — and a
        denied caller gets a machine-readable ``reason`` plus an audit row
        instead of a bare 403. ``SBOM_RETRIEVAL_ROLES`` is the single place the
        role list is written down.
        """
        from tools.compliance import sbom_distribution as dist

        user = getattr(g, "current_user", None)
        decision = dist.evaluate_access(record, user)
        if not decision.allowed:
            dist.log_distribution(record, user, decision, ip_address=request.remote_addr)
            return jsonify({
                "error": decision.detail,
                "reason": decision.reason,
                # The standard expects a recipient to be able to ask about a
                # withholding rather than guess at it.
                "contact": "Contact the SBOM author's ISSO to request release.",
            }), decision.status

        try:
            payload = dist.read_artifact_bytes(record)
        except dist.ArtifactUnavailable as exc:
            logger.warning("sbom artifact unavailable for record %s: %s", record.get("id"), exc)
            return jsonify({"error": str(exc), "reason": "artifact_missing"}), 404

        digest = dist.artifact_digest(payload)
        dist.log_distribution(record, user, decision, digest=digest,
                              ip_address=request.remote_addr)
        markings = dist.document_markings(payload)
        response = Response(payload, mimetype=dist.media_type(record))
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{dist.filename_for(record)}"'
        )
        # Repeated in headers for consumers that pipe the artifact without
        # parsing it. The authoritative copy stays inside the document.
        response.headers["X-ICDEV-Classification"] = (
            markings["classification"] or record.get("classification") or "CUI"
        )
        if markings["distribution"]:
            response.headers["X-ICDEV-Distribution"] = markings["distribution"]
        response.headers["X-ICDEV-SBOM-SHA256"] = digest
        response.headers["X-ICDEV-SBOM-Version"] = str(
            record.get("sbom_version") or record.get("version") or ""
        )
        if record.get("serial_number"):
            response.headers["X-ICDEV-SBOM-Serial"] = str(record["serial_number"])
        return response

    @bp.route("/api/supply_chain/sbom/versions/<path:project_id>")
    def sc_sbom_versions(project_id):
        from tools.compliance import sbom_distribution as dist

        try:
            conn = _db()
            records = dist.list_records(conn, project_id=project_id, limit=200)
        except Exception as exc:
            logger.warning("sc_sbom_versions error: %s", exc)
            return jsonify({"error": "lookup failed", "project_id": project_id}), 500
        user = getattr(g, "current_user", None)
        for r in records:
            r.pop("file_path", None)
            decision = dist.evaluate_access(r, user)
            r["access"] = decision.reason
            r["accessible"] = decision.allowed
        return jsonify({"project_id": project_id, "versions": records,
                        "count": len(records)})

    @bp.route("/api/supply_chain/sbom/record/<int:record_id>")
    def sc_sbom_by_record(record_id):
        from tools.compliance import sbom_distribution as dist

        try:
            conn = _db()
            record = dist.resolve_record(conn, record_id=record_id)
        except Exception as exc:
            logger.warning("sc_sbom_by_record error: %s", exc)
            return jsonify({"error": "lookup failed"}), 500
        if not record:
            return jsonify({"error": "no such SBOM record", "reason": "not_found"}), 404
        return _serve(record)

    @bp.route("/api/supply_chain/sbom/<project_id>/<version>")
    def sc_sbom_version(project_id, version):
        from tools.compliance import sbom_distribution as dist

        try:
            conn = _db()
            record = dist.resolve_record(conn, project_id=project_id, version=version)
        except Exception as exc:
            logger.warning("sc_sbom_version error: %s", exc)
            return jsonify({"error": "lookup failed"}), 500
        if not record:
            return jsonify({
                "error": f"no SBOM recorded for project '{project_id}' version '{version}'",
                "reason": "not_found",
            }), 404
        return _serve(record)

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
