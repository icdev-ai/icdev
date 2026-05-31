#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: Contract Performance Management Portal (Phase 60).

Post-award contract lifecycle — EVM, CDRL, CPARS, subcontractors, COR portal.
Bridges tools/govcon/ CPMP tools into the Flask dashboard.

Integration points:
    contract_manager.py      → Contracts, CLINs, WBS, Deliverables CRUD
    portfolio_manager.py     → Portfolio summary, health scoring, transition bridge
    evm_engine.py            → ANSI/EIA-748 calculations, Monte Carlo
    cpars_predictor.py       → Deterministic weighted CPARS scoring
    subcontractor_tracker.py → FAR 52.219-9, ISR/SSR
    negative_event_tracker.py → NDAA event-based tracking
    cdrl_generator.py        → CDRL auto-generation via ICDEV™ tools
    sam_contract_sync.py     → SAM.gov Contract Awards API
"""

import json as _mac_json
import os
import sys
import uuid
from tools.db.storage import get_connection
from pathlib import Path

from flask import Blueprint, g, jsonify, make_response, request

from tools.dashboard.auth import require_role
from tools.dashboard.config import DEFAULT_CLASSIFICATION

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

cpmp_api = Blueprint("cpmp_api", __name__, url_prefix="/api/cpmp")


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    # Clear RLS context — cpmp tables use classification=CUI universally;
    # complex JOIN queries break when RLS injects c.classification into subqueries.
    conn.set_security_context(None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _uuid():
    return str(uuid.uuid4())


def _audit(conn, action, details="", actor="cpmp_api"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (event_type, actor, action, details, session_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ("hook_event_logged", actor, action, details, "cpmp"),
        )
    except Exception:
        pass


def _classification():
    return DEFAULT_CLASSIFICATION


def _cor_access_log(conn, user_id, contract_id, action):
    try:
        conn.execute(
            "INSERT INTO cpmp_cor_access_log (user_id, contract_id, action) VALUES (?, ?, ?)",
            (user_id, contract_id, action),
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Bell-LaPadula MAC helpers (prop-sec-02)
# ---------------------------------------------------------------------------

def _mac_ctx():
    """Return current security context from Flask g (None = system/unauthenticated)."""
    try:
        return getattr(g, "security_context", None)
    except RuntimeError:
        return None


def _mac_compartments(raw):
    """Parse compartments from JSON string or collection to a plain set."""
    if isinstance(raw, (list, tuple, set, frozenset)):
        return set(raw)
    try:
        return set(_mac_json.loads(raw or "[]"))
    except Exception:
        return set()


def _mac_filter(rows):
    """Filter row list — keep only those the current user can read per Bell-LaPadula.

    Applies no-read-up (clearance >= classification) and strict-subset
    compartment check (resource compartments ⊆ user compartments).
    """
    from tools.security.classification_enforcer import can_read, can_access_compartment
    ctx = _mac_ctx()
    if ctx is None:
        return [dict(r) if not isinstance(r, dict) else r for r in rows]
    result = []
    for row in rows:
        d = dict(row) if not isinstance(row, dict) else row
        cls = d.get("classification", "CUI") or "CUI"
        comps = _mac_compartments(d.get("compartments", "[]"))
        if can_read(cls, ctx) and can_access_compartment(comps, ctx):
            result.append(d)
    return result


def _mac_deny_read(row):
    """Return 403 JSON response if current user cannot read this row. Returns None on pass."""
    from tools.security.classification_enforcer import can_read, can_access_compartment
    ctx = _mac_ctx()
    if ctx is None:
        return None
    d = dict(row) if not isinstance(row, dict) else row
    cls = d.get("classification", "CUI") or "CUI"
    comps = _mac_compartments(d.get("compartments", "[]"))
    if not can_read(cls, ctx) or not can_access_compartment(comps, ctx):
        return make_response(
            jsonify({"error": "Insufficient clearance", "code": "MAC_DENIED"}), 403
        )
    return None


def _mac_deny_write(classification, compartments_raw="[]"):
    """Return 403 JSON response if current user cannot write at this classification.

    Implements no-write-down: user clearance must be >= target classification so
    a lower-cleared user cannot create or update a higher-classified contract.
    """
    from tools.security.classification_enforcer import can_read, can_access_compartment
    ctx = _mac_ctx()
    if ctx is None:
        return None
    cls = (classification or "CUI").upper()
    comps = _mac_compartments(compartments_raw)
    if not can_read(cls, ctx) or not can_access_compartment(comps, ctx):
        return make_response(
            jsonify({"error": "MAC write policy denied", "code": "MAC_WRITE_DENIED"}), 403
        )
    return None


def _mac_check_parent_contract(contract_id, conn):
    """Check MAC read access on the parent contract. Returns 403 response or None."""
    try:
        row = conn.execute(
            "SELECT classification, compartments FROM cpmp_contracts WHERE id = ?",
            (contract_id,),
        ).fetchone()
        if not row:
            return None  # Let child route return 404
        return _mac_deny_read(row)
    except Exception:
        return None


# =====================================================================
# Phase A — Contracts CRUD
# =====================================================================


@cpmp_api.route("/contracts", methods=["GET"])
def list_contracts():
    """GET /api/cpmp/contracts — List contracts with optional filters."""
    try:
        from tools.govcon.contract_manager import list_contracts as _list

        status = request.args.get("status")
        request.args.get("agency")
        limit = int(request.args.get("limit", 50))
        result = _list(status=status, limit=limit)
        # Bell-LaPadula: filter contracts list to only rows caller can read
        if isinstance(result, dict) and "contracts" in result:
            result["contracts"] = _mac_filter(result["contracts"])
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts", methods=["POST"])
def create_contract():
    """POST /api/cpmp/contracts — Create a new contract."""
    try:
        from tools.govcon.contract_manager import create_contract as _create

        data = request.get_json(silent=True) or {}
        # Bell-LaPadula: no-write-down — caller must hold clearance >= target classification
        denied = _mac_deny_write(data.get("classification", "CUI"), data.get("compartments", "[]"))
        if denied:
            return denied
        result = _create(data)
        return jsonify(result), 201 if result.get("status") == "ok" else 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>", methods=["GET"])
def get_contract(contract_id):
    """GET /api/cpmp/contracts/<id> — Get contract details."""
    try:
        from tools.govcon.contract_manager import get_contract as _get

        result = _get(contract_id)
        if result.get("status") == "error":
            return jsonify(result), 404
        # Bell-LaPadula: no-read-up check on individual contract
        denied = _mac_deny_read(result)
        if denied:
            return denied
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>", methods=["PUT"])
def update_contract(contract_id):
    """PUT /api/cpmp/contracts/<id> — Update contract fields."""
    try:
        from tools.govcon.contract_manager import get_contract as _get, update_contract as _update

        data = request.get_json(silent=True) or {}
        # Bell-LaPadula: check read access on existing record before updating
        existing = _get(contract_id)
        if existing.get("status") != "error":
            denied = _mac_deny_read(existing)
            if denied:
                return denied
            # Also check write at the target classification
            new_cls = data.get("classification", existing.get("classification", "CUI"))
            denied = _mac_deny_write(new_cls, data.get("compartments", existing.get("compartments", "[]")))
            if denied:
                return denied
        result = _update(contract_id, data)
        if result.get("status") == "error":
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/status", methods=["PUT"])
def transition_contract(contract_id):
    """PUT /api/cpmp/contracts/<id>/status — Transition contract status."""
    try:
        from tools.govcon.contract_manager import transition_contract as _transition

        data = request.get_json(silent=True) or {}
        new_status = data.get("status")
        changed_by = data.get("changed_by")
        reason = data.get("reason")
        if not new_status:
            return jsonify({"status": "error", "message": "status required"}), 400
        result = _transition(contract_id, new_status, changed_by, reason)
        if result.get("status") == "error":
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Phase A — CLINs
# =====================================================================


@cpmp_api.route("/contracts/<contract_id>/clins", methods=["GET"])
def list_clins(contract_id):
    """GET /api/cpmp/contracts/<id>/clins — List CLINs for a contract."""
    try:
        from tools.govcon.contract_manager import list_clins as _list

        # Bell-LaPadula: inherit parent contract classification
        conn = _get_db()
        try:
            denied = _mac_check_parent_contract(contract_id, conn)
        finally:
            conn.close()
        if denied:
            return denied
        result = _list(contract_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/clins", methods=["POST"])
def create_clin(contract_id):
    """POST /api/cpmp/contracts/<id>/clins — Create a CLIN."""
    try:
        from tools.govcon.contract_manager import create_clin as _create

        data = request.get_json(silent=True) or {}
        result = _create(contract_id, data)
        return jsonify(result), 201 if result.get("status") == "ok" else 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/clins/<clin_id>", methods=["PUT"])
def update_clin(clin_id):
    """PUT /api/cpmp/clins/<id> — Update a CLIN."""
    try:
        from tools.govcon.contract_manager import update_clin as _update

        data = request.get_json(silent=True) or {}
        result = _update(clin_id, data)
        if result.get("status") == "error":
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Phase A — WBS
# =====================================================================


@cpmp_api.route("/contracts/<contract_id>/wbs", methods=["GET"])
def list_wbs(contract_id):
    """GET /api/cpmp/contracts/<id>/wbs — List WBS elements (flat or tree)."""
    try:
        from tools.govcon.contract_manager import list_wbs as _list, build_wbs_tree as _tree

        # Bell-LaPadula: inherit parent contract classification
        conn = _get_db()
        try:
            denied = _mac_check_parent_contract(contract_id, conn)
        finally:
            conn.close()
        if denied:
            return denied
        mode = request.args.get("mode", "")
        tree_flag = request.args.get("tree", "").lower() == "true"
        if mode == "tree" or tree_flag:
            result = _tree(contract_id)
        else:
            result = _list(contract_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/wbs", methods=["POST"])
def create_wbs(contract_id):
    """POST /api/cpmp/contracts/<id>/wbs — Create a WBS element."""
    try:
        from tools.govcon.contract_manager import create_wbs as _create

        data = request.get_json(silent=True) or {}
        result = _create(contract_id, data)
        return jsonify(result), 201 if result.get("status") == "ok" else 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/wbs/<wbs_id>", methods=["PUT"])
def update_wbs(wbs_id):
    """PUT /api/cpmp/wbs/<id> — Update a WBS element."""
    try:
        from tools.govcon.contract_manager import update_wbs as _update

        data = request.get_json(silent=True) or {}
        result = _update(wbs_id, data)
        if result.get("status") == "error":
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Phase A — Deliverables
# =====================================================================


@cpmp_api.route("/contracts/<contract_id>/deliverables", methods=["GET"])
def list_deliverables(contract_id):
    """GET /api/cpmp/contracts/<id>/deliverables — List deliverables."""
    try:
        from tools.govcon.contract_manager import list_deliverables as _list

        status = request.args.get("status")
        result = _list(contract_id, status=status)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/deliverables", methods=["POST"])
def create_deliverable(contract_id):
    """POST /api/cpmp/contracts/<id>/deliverables — Create a deliverable."""
    try:
        from tools.govcon.contract_manager import create_deliverable as _create

        data = request.get_json(silent=True) or {}
        result = _create(contract_id, data)
        return jsonify(result), 201 if result.get("status") == "ok" else 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/deliverables/<deliverable_id>", methods=["GET"])
def get_deliverable(deliverable_id):
    """GET /api/cpmp/deliverables/<id> — Get deliverable with generations/history."""
    try:
        from tools.govcon.contract_manager import get_deliverable as _get

        result = _get(deliverable_id)
        if result.get("status") == "error":
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/deliverables/<deliverable_id>", methods=["PUT"])
def update_deliverable(deliverable_id):
    """PUT /api/cpmp/deliverables/<id> — Update deliverable fields."""
    try:
        from tools.govcon.contract_manager import update_deliverable as _update

        data = request.get_json(silent=True) or {}
        result = _update(deliverable_id, data)
        if result.get("status") == "error":
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/deliverables/<deliverable_id>/status", methods=["PUT"])
def transition_deliverable(deliverable_id):
    """PUT /api/cpmp/deliverables/<id>/status — Transition deliverable status."""
    try:
        from tools.govcon.contract_manager import transition_deliverable as _transition

        data = request.get_json(silent=True) or {}
        new_status = data.get("status")
        changed_by = data.get("changed_by")
        reason = data.get("reason")
        if not new_status:
            return jsonify({"status": "error", "message": "status required"}), 400
        result = _transition(deliverable_id, new_status, changed_by, reason)
        if result.get("status") == "error":
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Phase A — Portfolio + Transition
# =====================================================================


@cpmp_api.route("/portfolio", methods=["GET"])
def get_portfolio():
    """GET /api/cpmp/portfolio — Portfolio dashboard summary."""
    try:
        from tools.govcon.portfolio_manager import get_portfolio_summary

        result = get_portfolio_summary()
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/from-opportunity/<opp_id>", methods=["POST"])
def transition_from_opportunity(opp_id):
    """POST /api/cpmp/from-opportunity/<opp_id> — Create contract from won proposal."""
    try:
        from tools.govcon.portfolio_manager import transition_from_opportunity

        data = request.get_json(silent=True) or {}
        result = transition_from_opportunity(opp_id, created_by=data.get("created_by"))
        if result.get("status") == "error":
            return jsonify(result), 400
        return jsonify(result), 201
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Phase B — EVM
# =====================================================================


@cpmp_api.route("/contracts/<contract_id>/evm", methods=["GET"])
def get_evm(contract_id):
    """GET /api/cpmp/contracts/<id>/evm — Aggregated contract-level EVM."""
    try:
        from tools.govcon.evm_engine import aggregate_contract_evm

        result = aggregate_contract_evm(contract_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/evm", methods=["POST"])
def record_evm_period(contract_id):
    """POST /api/cpmp/contracts/<id>/evm — Record an EVM period snapshot."""
    try:
        from tools.govcon.evm_engine import record_period

        data = request.get_json(silent=True) or {}
        wbs_id = data.get("wbs_id")
        period_date = data.get("period_date")
        pv = data.get("pv", 0)
        ev = data.get("ev", 0)
        ac = data.get("ac", 0)
        source = data.get("source", "manual")
        if not wbs_id or not period_date:
            return jsonify({"status": "error", "message": "wbs_id and period_date required"}), 400
        result = record_period(contract_id, wbs_id, period_date, pv, ev, ac, source)
        if result.get("status") == "error":
            return jsonify(result), 400
        return jsonify(result), 201
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/evm/forecast", methods=["GET"])
def evm_forecast(contract_id):
    """GET /api/cpmp/contracts/<id>/evm/forecast — Monte Carlo EAC forecast."""
    try:
        from tools.govcon.evm_engine import forecast_monte_carlo

        iterations = int(request.args.get("iterations", 10000))
        result = forecast_monte_carlo(contract_id, iterations)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/evm/scurve", methods=["GET"])
def evm_scurve(contract_id):
    """GET /api/cpmp/contracts/<id>/evm/scurve — S-curve chart data."""
    try:
        from tools.govcon.evm_engine import generate_scurve_data

        result = generate_scurve_data(contract_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/evm/ipmdar", methods=["GET"])
def evm_ipmdar(contract_id):
    """GET /api/cpmp/contracts/<id>/evm/ipmdar — IPMDAR-compatible data."""
    try:
        from tools.govcon.evm_engine import generate_ipmdar_data

        result = generate_ipmdar_data(contract_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/evm/periods", methods=["GET"])
def evm_periods(contract_id):
    """GET /api/cpmp/contracts/<id>/evm/periods — List EVM period records."""
    try:
        from tools.govcon.evm_engine import get_evm_periods

        wbs_id = request.args.get("wbs_id")
        result = get_evm_periods(contract_id, wbs_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Phase B — Subcontractors
# =====================================================================


@cpmp_api.route("/contracts/<contract_id>/subcontractors", methods=["GET"])
def list_subcontractors(contract_id):
    """GET /api/cpmp/contracts/<id>/subcontractors — List subcontractors."""
    try:
        from tools.govcon.subcontractor_tracker import list_subcontractors as _list

        business_size = request.args.get("business_size")
        result = _list(contract_id, business_size=business_size)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/subcontractors", methods=["POST"])
def create_subcontractor(contract_id):
    """POST /api/cpmp/contracts/<id>/subcontractors — Add a subcontractor."""
    try:
        from tools.govcon.subcontractor_tracker import create_subcontractor as _create

        data = request.get_json(silent=True) or {}
        result = _create(contract_id, data)
        return jsonify(result), 201 if result.get("status") == "ok" else 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/subcontractors/<sub_id>", methods=["PUT"])
def update_subcontractor(sub_id):
    """PUT /api/cpmp/subcontractors/<id> — Update subcontractor."""
    try:
        from tools.govcon.subcontractor_tracker import update_subcontractor as _update

        data = request.get_json(silent=True) or {}
        result = _update(sub_id, data)
        if result.get("status") == "error":
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/subcontractors/noncompliance", methods=["GET"])
def subcontractor_noncompliance(contract_id):
    """GET /api/cpmp/contracts/<id>/subcontractors/noncompliance — Detect noncompliance."""
    try:
        from tools.govcon.subcontractor_tracker import detect_noncompliance

        result = detect_noncompliance(contract_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/sb-compliance", methods=["GET"])
def sb_compliance(contract_id):
    """GET /api/cpmp/contracts/<id>/sb-compliance — Small business compliance."""
    try:
        from tools.govcon.subcontractor_tracker import compute_sb_compliance

        result = compute_sb_compliance(contract_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Phase B — Small Business Plans (ISR/SSR)
# =====================================================================


@cpmp_api.route("/contracts/<contract_id>/small-business", methods=["GET"])
def list_sb_reports(contract_id):
    """GET /api/cpmp/contracts/<id>/small-business — List ISR/SSR reports."""
    try:
        from tools.govcon.subcontractor_tracker import list_sb_reports as _list

        result = _list(contract_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/small-business", methods=["POST"])
def create_sb_report(contract_id):
    """POST /api/cpmp/contracts/<id>/small-business — Create ISR/SSR report."""
    try:
        from tools.govcon.subcontractor_tracker import create_sb_report as _create

        data = request.get_json(silent=True) or {}
        period = data.get("period")
        report_type = data.get("type", "isr")
        if not period:
            return jsonify({"status": "error", "message": "period required"}), 400
        result = _create(contract_id, period, report_type)
        return jsonify(result), 201 if result.get("status") == "ok" else 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Phase B — CPARS
# =====================================================================


@cpmp_api.route("/contracts/<contract_id>/cpars", methods=["GET"])
def list_cpars(contract_id):
    """GET /api/cpmp/contracts/<id>/cpars — List CPARS assessments."""
    try:
        from tools.govcon.cpars_predictor import list_assessments as _list

        result = _list(contract_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/cpars", methods=["POST"])
def create_cpars(contract_id):
    """POST /api/cpmp/contracts/<id>/cpars — Create CPARS assessment."""
    try:
        from tools.govcon.cpars_predictor import create_assessment as _create

        data = request.get_json(silent=True) or {}
        period_start = data.get("period_start")
        period_end = data.get("period_end")
        if not period_start or not period_end:
            return jsonify({"status": "error", "message": "period_start and period_end required"}), 400
        result = _create(contract_id, period_start, period_end, data)
        return jsonify(result), 201 if result.get("status") == "ok" else 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/cpars/<assessment_id>", methods=["PUT"])
def update_cpars(assessment_id):
    """PUT /api/cpmp/cpars/<id> — Update CPARS assessment."""
    try:
        from tools.govcon.cpars_predictor import update_assessment as _update

        data = request.get_json(silent=True) or {}
        result = _update(assessment_id, data)
        if result.get("status") == "error":
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/cpars/predict", methods=["GET"])
def predict_cpars(contract_id):
    """GET /api/cpmp/contracts/<id>/cpars/predict — Predictive CPARS score."""
    try:
        from tools.govcon.cpars_predictor import predict_cpars as _predict

        result = _predict(contract_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/cpars/trend", methods=["GET"])
def cpars_trend(contract_id):
    """GET /api/cpmp/contracts/<id>/cpars/trend — CPARS score trend."""
    try:
        from tools.govcon.cpars_predictor import get_cpars_trend as _trend

        result = _trend(contract_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Phase B — Negative Events
# =====================================================================


@cpmp_api.route("/contracts/<contract_id>/negative-events", methods=["GET"])
def list_negative_events(contract_id):
    """GET /api/cpmp/contracts/<id>/negative-events — List negative events."""
    try:
        from tools.govcon.negative_event_tracker import list_events as _list

        severity = request.args.get("severity")
        status = request.args.get("status")
        result = _list(contract_id, severity=severity, status=status)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/negative-events", methods=["POST"])
def record_negative_event(contract_id):
    """POST /api/cpmp/contracts/<id>/negative-events — Record a negative event."""
    try:
        from tools.govcon.negative_event_tracker import record_event as _record

        data = request.get_json(silent=True) or {}
        event_type = data.get("event_type")
        if not event_type:
            return jsonify({"status": "error", "message": "event_type required"}), 400
        severity = data.get("severity", "medium")
        description = data.get("description", "")
        result = _record(
            contract_id,
            event_type,
            severity,
            description,
            corrective_action=data.get("corrective_action"),
            deliverable_id=data.get("deliverable_id"),
            subcontractor_id=data.get("subcontractor_id"),
            event_date=data.get("event_date"),
            corrective_action_taken=data.get("corrective_action_taken", False),
        )
        return jsonify(result), 201 if result.get("status") == "ok" else 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/negative-events/<event_id>", methods=["PUT"])
def update_negative_event(event_id):
    """PUT /api/cpmp/negative-events/<id> — Update corrective action status."""
    try:
        from tools.govcon.negative_event_tracker import update_corrective_action as _update

        data = request.get_json(silent=True) or {}
        ca_status = data.get("corrective_action_status")
        if not ca_status:
            return jsonify({"status": "error", "message": "corrective_action_status required"}), 400
        result = _update(event_id, ca_status)
        if result.get("status") == "error":
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/negative-events/auto-detect", methods=["POST"])
def auto_detect_events(contract_id):
    """POST /api/cpmp/contracts/<id>/negative-events/auto-detect — Run auto-detection."""
    try:
        from tools.govcon.negative_event_tracker import auto_detect_all as _detect

        result = _detect(contract_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/negative-events/ndaa-thresholds", methods=["GET"])
def ndaa_thresholds(contract_id):
    """GET /api/cpmp/contracts/<id>/negative-events/ndaa-thresholds — Check NDAA thresholds."""
    try:
        from tools.govcon.negative_event_tracker import check_ndaa_thresholds

        result = check_ndaa_thresholds(contract_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Phase B — Health
# =====================================================================


@cpmp_api.route("/contracts/<contract_id>/health", methods=["GET"])
def contract_health(contract_id):
    """GET /api/cpmp/contracts/<id>/health — Compute contract health score."""
    try:
        from tools.govcon.portfolio_manager import compute_contract_health

        result = compute_contract_health(contract_id)
        # Add health_color alias so test consumers can find the color field
        if result.get("status") == "ok":
            result["health_color"] = result.get("health")
            result["color"] = result.get("health")
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Phase C — CDRL Generation
# =====================================================================


@cpmp_api.route("/contracts/<contract_id>/generate-cdrl/<deliverable_id>", methods=["POST"])
def generate_cdrl(contract_id, deliverable_id):
    """POST /api/cpmp/contracts/<id>/generate-cdrl/<did> — Generate CDRL."""
    try:
        from tools.govcon.cdrl_generator import generate_cdrl as _generate

        data = request.get_json(silent=True) or {}
        project_id = data.get("project_id")
        result = _generate(deliverable_id, project_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/generate-due", methods=["POST"])
def generate_due_cdrls(contract_id):
    """POST /api/cpmp/contracts/<id>/generate-due — Generate all due CDRLs."""
    try:
        from tools.govcon.cdrl_generator import generate_all_due as _generate

        data = request.get_json(silent=True) or {}
        days_ahead = data.get("days_ahead")
        result = _generate(contract_id, days_ahead)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/cdrl-generations", methods=["GET"])
def list_cdrl_generations():
    """GET /api/cpmp/cdrl-generations — List CDRL generation records."""
    try:
        from tools.govcon.cdrl_generator import list_generations as _list

        contract_id = request.args.get("contract_id")
        deliverable_id = request.args.get("deliverable_id")
        status = request.args.get("status")
        result = _list(contract_id, deliverable_id, status)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Phase C — SAM.gov Contract Awards
# =====================================================================


@cpmp_api.route("/sam/sync-awards", methods=["POST"])
def sync_sam_awards():
    """POST /api/cpmp/sam/sync-awards — Sync awards from SAM.gov."""
    try:
        from tools.govcon.sam_contract_sync import sync_awards

        data = request.get_json(silent=True) or {}
        lookback_days = data.get("lookback_days")
        result = sync_awards(lookback_days)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/sam/awards", methods=["GET"])
def list_sam_awards():
    """GET /api/cpmp/sam/awards — List cached SAM.gov awards."""
    try:
        from tools.govcon.sam_contract_sync import list_awards

        linked_only = request.args.get("linked_only", "").lower() == "true"
        limit = int(request.args.get("limit", 50))
        result = list_awards(linked_only, limit)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/sam/awards/search", methods=["GET"])
def search_sam_awards():
    """GET /api/cpmp/sam/awards/search?q=keyword — Search awards."""
    try:
        from tools.govcon.sam_contract_sync import search_awards

        query = request.args.get("q", "")
        if not query:
            return jsonify({"status": "error", "message": "q parameter required"}), 400
        result = search_awards(query)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/sam/link/<sam_award_id>", methods=["POST"])
def link_sam_award(sam_award_id):
    """POST /api/cpmp/sam/link/<sam_award_id> — Link SAM award to contract."""
    try:
        from tools.govcon.sam_contract_sync import link_award_to_contract

        data = request.get_json(silent=True) or {}
        contract_id = data.get("contract_id")
        if not contract_id:
            return jsonify({"status": "error", "message": "contract_id required"}), 400
        result = link_award_to_contract(sam_award_id, contract_id)
        if result.get("status") == "error":
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Phase C — COR Portal (Read-Only)
# =====================================================================


def _get_cor_contracts(cor_email):
    """Get contracts where the COR email matches."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM cpmp_contracts WHERE cor_email = ? ORDER BY created_at DESC",
        (cor_email,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# Hidden fields for COR view (from config)
COR_HIDDEN_FIELDS = {
    "subcontractor_pricing",
    "subcontractor_rate",
    "subcontractor_cost",
    "internal_cost_details",
    "internal_notes",
    "corrective_action_details",
    "billed_value",
    "ac_cumulative",
}


def _sanitize_for_cor(data):
    """Remove internal-only fields from data for COR view."""
    if isinstance(data, dict):
        return {k: _sanitize_for_cor(v) for k, v in data.items() if k not in COR_HIDDEN_FIELDS}
    if isinstance(data, list):
        return [_sanitize_for_cor(item) for item in data]
    return data


@cpmp_api.route("/cor/contracts", methods=["GET"])
@require_role("admin", "pm", "isso", "co", "cor")
def cor_list_contracts():
    """GET /api/cpmp/cor/contracts — COR: list assigned contracts."""
    try:
        cor_email = g.current_user.get("email", "")
        if not cor_email:
            return jsonify({"status": "error", "message": "Authenticated user has no email"}), 400
        conn = _get_db()
        _audit(conn, "cor.view_contracts", f"COR {cor_email} listed assigned contracts", actor=cor_email)
        conn.commit()
        conn.close()
        contracts = _get_cor_contracts(cor_email)
        return jsonify({"status": "ok", "total": len(contracts), "contracts": _sanitize_for_cor(contracts)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/cor/contracts/<contract_id>", methods=["GET"])
@require_role("admin", "pm", "isso", "co", "cor")
def cor_get_contract(contract_id):
    """GET /api/cpmp/cor/contracts/<id> — COR: view contract detail."""
    try:
        cor_email = g.current_user.get("email", "")
        if not cor_email:
            return jsonify({"status": "error", "message": "Authenticated user has no email"}), 400
        # Verify COR is assigned to this contract
        cor_contracts = _get_cor_contracts(cor_email)
        if not any(c.get("id") == contract_id for c in cor_contracts):
            return jsonify({"status": "error", "message": "Access denied: not assigned COR for this contract"}), 403
        from tools.govcon.contract_manager import get_contract as _get

        result = _get(contract_id)
        if result.get("status") == "error":
            return jsonify(result), 404
        conn = _get_db()
        _cor_access_log(conn, cor_email, contract_id, "view_contract")
        conn.commit()
        conn.close()
        return jsonify(_sanitize_for_cor(result))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/cor/contracts/<contract_id>/deliverables", methods=["GET"])
@require_role("admin", "pm", "isso", "co", "cor")
def cor_list_deliverables(contract_id):
    """GET /api/cpmp/cor/contracts/<id>/deliverables — COR: view deliverables."""
    try:
        cor_email = g.current_user.get("email", "")
        if not cor_email:
            return jsonify({"status": "error", "message": "Authenticated user has no email"}), 400
        cor_contracts = _get_cor_contracts(cor_email)
        if not any(c.get("id") == contract_id for c in cor_contracts):
            return jsonify({"status": "error", "message": "Access denied: not assigned COR for this contract"}), 403
        from tools.govcon.contract_manager import list_deliverables as _list

        result = _list(contract_id)
        conn = _get_db()
        _cor_access_log(conn, cor_email, contract_id, "view_deliverables")
        conn.commit()
        conn.close()
        return jsonify(_sanitize_for_cor(result))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/cor/contracts/<contract_id>/evm", methods=["GET"])
@require_role("admin", "pm", "isso", "co", "cor")
def cor_get_evm(contract_id):
    """GET /api/cpmp/cor/contracts/<id>/evm — COR: view EVM data."""
    try:
        cor_email = g.current_user.get("email", "")
        if not cor_email:
            return jsonify({"status": "error", "message": "Authenticated user has no email"}), 400
        cor_contracts = _get_cor_contracts(cor_email)
        if not any(c.get("id") == contract_id for c in cor_contracts):
            return jsonify({"status": "error", "message": "Access denied: not assigned COR for this contract"}), 403
        from tools.govcon.evm_engine import aggregate_contract_evm

        result = aggregate_contract_evm(contract_id)
        conn = _get_db()
        _cor_access_log(conn, cor_email, contract_id, "view_evm")
        conn.commit()
        conn.close()
        return jsonify(_sanitize_for_cor(result))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/cor/contracts/<contract_id>/cpars", methods=["GET"])
@require_role("admin", "pm", "isso", "co", "cor")
def cor_get_cpars(contract_id):
    """GET /api/cpmp/cor/contracts/<id>/cpars — COR: view CPARS ratings."""
    try:
        cor_email = g.current_user.get("email", "")
        if not cor_email:
            return jsonify({"status": "error", "message": "Authenticated user has no email"}), 400
        cor_contracts = _get_cor_contracts(cor_email)
        if not any(c.get("id") == contract_id for c in cor_contracts):
            return jsonify({"status": "error", "message": "Access denied: not assigned COR for this contract"}), 403
        from tools.govcon.cpars_predictor import list_assessments as _list

        result = _list(contract_id)
        conn = _get_db()
        _cor_access_log(conn, cor_email, contract_id, "view_cpars")
        conn.commit()
        conn.close()
        return jsonify(_sanitize_for_cor(result))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Phase D — Integrated Master Schedule (IMS, prop-pm-01)
# =====================================================================


@cpmp_api.route("/contracts/<contract_id>/milestones", methods=["GET"])
def list_milestones(contract_id):
    """GET /api/cpmp/contracts/<id>/milestones — List milestones with WBS + EVM joins."""
    try:
        conn = _get_db()
        mac_err = _mac_check_parent_contract(contract_id, conn)
        conn.close()
        if mac_err:
            return mac_err
        from tools.govcon.milestone_manager import list_milestones as _list

        status = request.args.get("status")
        result = _list(contract_id, status=status)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/milestones", methods=["POST"])
@require_role("pm", "admin")
def create_milestone(contract_id):
    """POST /api/cpmp/contracts/<id>/milestones — Create milestone (pm/admin only)."""
    try:
        conn = _get_db()
        mac_err = _mac_check_parent_contract(contract_id, conn)
        conn.close()
        if mac_err:
            return mac_err
        from tools.govcon.milestone_manager import create_milestone as _create

        data = request.get_json(silent=True) or {}
        data["contract_id"] = contract_id
        result = _create(data)
        return jsonify(result), 201 if result.get("status") == "ok" else 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/milestones/<milestone_id>", methods=["GET"])
def get_milestone(milestone_id):
    """GET /api/cpmp/milestones/<id> — Get a single milestone."""
    try:
        from tools.govcon.milestone_manager import get_milestone as _get

        result = _get(milestone_id)
        if result.get("status") == "error":
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/milestones/<milestone_id>", methods=["PUT"])
@require_role("pm", "admin")
def update_milestone(milestone_id):
    """PUT /api/cpmp/milestones/<id> — Update milestone (pm/admin only)."""
    try:
        from tools.govcon.milestone_manager import update_milestone as _update

        data = request.get_json(silent=True) or {}
        result = _update(milestone_id, data)
        if result.get("status") == "error":
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/milestones/<milestone_id>", methods=["DELETE"])
@require_role("pm", "admin")
def delete_milestone(milestone_id):
    """DELETE /api/cpmp/milestones/<id> — Delete milestone + its deps (pm/admin only)."""
    try:
        from tools.govcon.milestone_manager import delete_milestone as _delete

        result = _delete(milestone_id)
        if result.get("status") == "error":
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/milestone-deps", methods=["GET"])
def list_milestone_deps(contract_id):
    """GET /api/cpmp/contracts/<id>/milestone-deps — List milestone dependencies."""
    try:
        conn = _get_db()
        mac_err = _mac_check_parent_contract(contract_id, conn)
        conn.close()
        if mac_err:
            return mac_err
        from tools.govcon.milestone_manager import list_deps as _list

        result = _list(contract_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/milestone-deps", methods=["POST"])
@require_role("pm", "admin")
def create_milestone_dep(contract_id):
    """POST /api/cpmp/contracts/<id>/milestone-deps — Create dependency (pm/admin only)."""
    try:
        conn = _get_db()
        mac_err = _mac_check_parent_contract(contract_id, conn)
        conn.close()
        if mac_err:
            return mac_err
        from tools.govcon.milestone_manager import create_dep as _create

        data = request.get_json(silent=True) or {}
        data["contract_id"] = contract_id
        result = _create(data)
        return jsonify(result), 201 if result.get("status") == "ok" else 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/milestone-deps/<dep_id>", methods=["DELETE"])
@require_role("pm", "admin")
def delete_milestone_dep(dep_id):
    """DELETE /api/cpmp/milestone-deps/<id> — Remove a dependency (pm/admin only)."""
    try:
        from tools.govcon.milestone_manager import delete_dep as _delete

        result = _delete(dep_id)
        if result.get("status") == "error":
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ---------------------------------------------------------------------------
# Contract Modifications — request/approval workflow (prop-ctr-01)
# ---------------------------------------------------------------------------

@cpmp_api.route("/contracts/<contract_id>/mods", methods=["GET"])
@require_role("admin", "pm", "co", "contract_mgr", "cor")
def list_contract_mods(contract_id):
    """GET /api/cpmp/contracts/<id>/mods — List all modifications for a contract."""
    try:
        from tools.govcon.contract_mods_manager import list_mods as _list_mods
        mods = _list_mods(contract_id, db_path=str(DB_PATH))
        return jsonify({"mods": mods, "count": len(mods)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@cpmp_api.route("/contracts/<contract_id>/mods", methods=["POST"])
@require_role("admin", "co", "contract_mgr")
def create_contract_mod(contract_id):
    """POST /api/cpmp/contracts/<id>/mods — Request a new contract modification."""
    data = request.get_json(force=True) or {}
    denied = _mac_deny_write(data.get("classification", "CUI"), data.get("compartments", "[]"))
    if denied:
        return denied
    try:
        from tools.govcon.contract_mods_manager import create_mod as _create_mod
        actor = "system"
        if hasattr(g, "current_user") and g.current_user:
            actor = (g.current_user.get("username") or "system") if isinstance(g.current_user, dict) else "system"
        mod = _create_mod(
            contract_id=contract_id,
            type_=data.get("type", "admin"),
            description=data.get("description", ""),
            value_delta=float(data.get("value_delta", 0.0)),
            requested_by=actor,
            effective_date=data.get("effective_date"),
            classification=data.get("classification", "CUI"),
            tenant_id=data.get("tenant_id"),
            metadata=data.get("metadata", "{}"),
            db_path=str(DB_PATH),
        )
        return jsonify({"mod": mod}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@cpmp_api.route("/mods/<mod_id>", methods=["GET"])
@require_role("admin", "pm", "co", "contract_mgr", "cor")
def get_contract_mod(mod_id):
    """GET /api/cpmp/mods/<id> — Get a single modification record."""
    try:
        from tools.govcon.contract_mods_manager import get_mod as _get_mod
        mod = _get_mod(mod_id, db_path=str(DB_PATH))
        if not mod:
            return jsonify({"error": "Modification not found"}), 404
        denied = _mac_deny_read(mod)
        if denied:
            return denied
        return jsonify({"mod": mod})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@cpmp_api.route("/mods/<mod_id>/status", methods=["PUT"])
@require_role("admin", "co", "contract_mgr")
def transition_contract_mod(mod_id):
    """PUT /api/cpmp/mods/<id>/status — Advance modification through approval workflow."""
    data = request.get_json(force=True) or {}
    new_status = data.get("status")
    if not new_status:
        return jsonify({"error": "status is required"}), 400
    try:
        from tools.govcon.contract_mods_manager import transition_mod as _transition_mod
        actor = "system"
        if hasattr(g, "current_user") and g.current_user:
            actor = (g.current_user.get("username") or "system") if isinstance(g.current_user, dict) else "system"
        mod = _transition_mod(
            mod_id=mod_id,
            new_status=new_status,
            actor=actor,
            reason=data.get("reason"),
            db_path=str(DB_PATH),
        )
        return jsonify({"mod": mod})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
