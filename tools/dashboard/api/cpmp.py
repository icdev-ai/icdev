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
from datetime import datetime, timezone
from tools.db.storage import get_connection
from pathlib import Path

from flask import Blueprint, g, jsonify, make_response, request

from tools.dashboard.auth import require_role
from tools.dashboard.config import DEFAULT_CLASSIFICATION
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.dashboard.api.cpmp")

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

cpmp_api = Blueprint("cpmp_api", __name__, url_prefix="/api/cpmp")


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    # RLS-aware (prop-fix-12): in a request context _attach_flask_security_context()
    # already wired g.security_context into the connection, so tenant_id +
    # classification predicates inject automatically (migrations 245/246/247 added
    # the columns to every cpmp_* table). The historical set_security_context(None)
    # bypass here cited subquery/JOIN injection bugs that _find_outer_where /
    # _depth0_skeleton have since fixed.
    try:
        from flask import has_request_context
        if not has_request_context():
            conn.set_security_context(None)  # rls-bypass: CLI / background tasks run without a user session; no tenant context available.
    except Exception:
        pass
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _uuid():
    return str(uuid.uuid4())


def _audit(conn, action, details="", actor="cpmp_api"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (event_type, actor, action, details, session_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("hook_event_logged", actor, action, details, "cpmp"),
        )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_audit: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


def _classification():
    return DEFAULT_CLASSIFICATION


def _cor_access_log(conn, user_id, contract_id, action):
    try:
        conn.execute(
            "INSERT INTO cpmp_cor_access_log (user_id, contract_id, action) VALUES (%s, %s, %s)",
            (user_id, contract_id, action),
        )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_cor_access_log: best-effort INSERT into cpmp_cor_access_log failed (non-blocking): %s", exc)


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
            "SELECT classification, compartments FROM cpmp_contracts WHERE id = %s",
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
@require_role("admin", "pm", "co", "contract_mgr")
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
@require_role("admin", "pm", "co", "contract_mgr")
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
@require_role("admin", "pm", "co", "contract_mgr")
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
@require_role("admin", "pm", "co", "contract_mgr")
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
@require_role("admin", "pm", "co", "contract_mgr")
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
@require_role("admin", "pm", "co", "contract_mgr")
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
@require_role("admin", "pm", "co", "contract_mgr")
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
@require_role("admin", "pm", "co", "contract_mgr")
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
@require_role("admin", "pm", "co", "contract_mgr")
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
@require_role("admin", "pm", "co", "contract_mgr")
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
# AI PMO Advisor Routes
# =====================================================================


@cpmp_api.route("/contracts/<contract_id>/ai-advisor/recommendations", methods=["GET"])
def ai_recommendations(contract_id):
    """GET /api/cpmp/contracts/<id>/ai-advisor/recommendations"""
    try:
        from tools.govcon.pmo_ai_advisor import get_recommendations
        return jsonify(get_recommendations(contract_id))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/ai-advisor/award-fee", methods=["GET"])
def ai_award_fee(contract_id):
    """GET /api/cpmp/contracts/<id>/ai-advisor/award-fee"""
    try:
        from tools.govcon.pmo_ai_advisor import predict_award_fee
        return jsonify(predict_award_fee(contract_id))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/ai-advisor/auto-detect", methods=["POST"])
@require_role("admin", "pm", "co", "contract_mgr")
def ai_auto_detect(contract_id):
    """POST /api/cpmp/contracts/<id>/ai-advisor/auto-detect"""
    try:
        from tools.govcon.pmo_ai_advisor import auto_detect_issues
        return jsonify(auto_detect_issues(contract_id))
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


@cpmp_api.route("/portfolio/<contract_id>", methods=["GET"])
def get_portfolio_contract_detail(contract_id):
    """GET /api/cpmp/portfolio/<id> — Portfolio detail view for a single contract.

    Nests obligation_summary (obligated_value, funded_value, burn_rate_pct) and the
    base/option period breakdown alongside the base contract fields.
    """
    try:
        from tools.govcon.contract_manager import get_contract as _get_contract
        from tools.govcon.contract_periods_manager import get_obligation_summary

        result = _get_contract(contract_id)
        if result.get("status") == "error":
            return jsonify(result), 404
        # Bell-LaPadula: no-read-up check on the contract
        denied = _mac_deny_read(result)
        if denied:
            return denied

        contract = result.get("contract", {})
        obligation = get_obligation_summary(contract_id)
        if obligation.get("status") == "ok":
            result["obligation_summary"] = {
                "obligated_value": obligation.get("total_obligated"),
                "funded_value": contract.get("funded_value", 0),
                "billed_value": obligation.get("total_billed"),
                "remaining_obligation": obligation.get("remaining_obligation"),
                "burn_rate_pct": obligation.get("burn_rate_pct"),
                "periods": obligation.get("by_period", []),
            }
        else:
            result["obligation_summary"] = {
                "obligated_value": contract.get("obligated_value", 0),
                "funded_value": contract.get("funded_value", 0),
                "billed_value": 0,
                "remaining_obligation": 0,
                "burn_rate_pct": 0,
                "periods": [],
            }
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/from-opportunity/<opp_id>", methods=["POST"])
@require_role("admin", "pm", "co", "contract_mgr")
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
@require_role("admin", "pm", "co", "contract_mgr")
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


@cpmp_api.route("/contracts/<contract_id>/monte-carlo", methods=["POST"])
@require_role("admin", "pm", "co", "contract_mgr")
def evm_monte_carlo(contract_id):
    """POST /api/cpmp/contracts/<id>/monte-carlo — Monte Carlo EAC forecast (UI endpoint)."""
    try:
        from tools.govcon.evm_engine import forecast_monte_carlo

        body = request.get_json(silent=True) or {}
        iterations = int(body.get("iterations", 10000))
        result = forecast_monte_carlo(contract_id, iterations)
        if result.get("status") == "error":
            return jsonify({"error": result.get("message", "Forecast failed")}), 400
        pct = result.get("percentiles", {})
        return jsonify({
            "status": "ok",
            "p50": pct.get("P50"),
            "p80": pct.get("P80"),
            "p95": pct.get("P95"),
            "mean": result.get("distribution", {}).get("mean"),
            "deterministic_eac": result.get("deterministic_eac"),
            "iterations": result.get("iterations"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
@require_role("admin", "pm", "co", "contract_mgr")
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
@require_role("admin", "pm", "co", "contract_mgr")
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
@require_role("admin", "pm", "co", "contract_mgr")
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
@require_role("admin", "pm", "co", "contract_mgr")
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
@require_role("admin", "pm", "co", "contract_mgr")
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
@require_role("admin", "pm", "co", "contract_mgr")
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
@require_role("admin", "pm", "co", "contract_mgr")
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
@require_role("admin", "pm", "co", "contract_mgr")
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
@require_role("admin", "pm", "co", "contract_mgr")
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
@require_role("admin", "pm", "co", "contract_mgr")
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
@require_role("admin", "pm", "co", "contract_mgr")
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
@require_role("admin", "pm", "co", "contract_mgr")
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
        "SELECT * FROM cpmp_contracts WHERE cor_email = %s ORDER BY created_at DESC",
        (cor_email,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _cor_contract_authorized(contract_id) -> bool:
    """Authorize COR-portal access to a contract.

    A `cor` user may only see contracts they are personally assigned to (by
    cor_email). Oversight roles (admin/pm/co/isso) provide program-wide
    visibility and may view any contract's COR data.
    """
    user = g.current_user or {}
    if user.get("role", "") in ("admin", "pm", "co", "isso"):
        return True
    cor_email = user.get("email", "")
    return any(c.get("id") == contract_id for c in _get_cor_contracts(cor_email))


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


# =====================================================================
# Phase D — Contract Periods + Obligation Tracking (D-CPMP-10)
# =====================================================================


@cpmp_api.route("/contracts/<contract_id>/periods", methods=["GET"])
def list_contract_periods(contract_id):
    """GET /api/cpmp/contracts/<id>/periods — List base+option periods."""
    try:
        from tools.govcon.contract_periods_manager import list_periods

        result = list_periods(contract_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/periods", methods=["POST"])
@require_role("admin", "co", "contract_mgr")
def create_contract_period(contract_id):
    """POST /api/cpmp/contracts/<id>/periods — Create a period of performance."""
    try:
        from tools.govcon.contract_periods_manager import create_period

        data = request.get_json(silent=True) or {}
        period_type = data.get("period_type")
        if not period_type:
            return jsonify({"status": "error", "message": "period_type required"}), 400
        result = create_period(
            contract_id,
            period_type,
            pop_start=data.get("pop_start"),
            pop_end=data.get("pop_end"),
            obligated_value=float(data.get("obligated_value", 0)),
            funded_value=float(data.get("funded_value", 0)),
            ceiling_value=float(data.get("ceiling_value", 0)),
            notes=data.get("notes"),
            created_by=getattr(g, "current_user", {}).get("username") if hasattr(g, "current_user") else None,
        )
        return jsonify(result), 201 if result.get("status") == "ok" else 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/periods/<period_id>/exercise", methods=["PUT"])
@require_role("admin", "co", "contract_mgr")
def exercise_period_option(period_id):
    """PUT /api/cpmp/periods/<id>/exercise — Exercise an option period."""
    try:
        from tools.govcon.contract_periods_manager import exercise_option

        data = request.get_json(silent=True) or {}
        obligated_value = float(data.get("obligated_value", 0))
        exercised_by = getattr(g, "current_user", {}).get("username") if hasattr(g, "current_user") else None
        result = exercise_option(period_id, obligated_value, exercised_by)
        if result.get("status") == "error":
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/obligation-summary", methods=["GET"])
def contract_obligation_summary(contract_id):
    """GET /api/cpmp/contracts/<id>/obligation-summary — Burn-rate vs obligation."""
    try:
        from tools.govcon.contract_periods_manager import get_obligation_summary

        result = get_obligation_summary(contract_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


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
        if not _cor_contract_authorized(contract_id):
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
        if not _cor_contract_authorized(contract_id):
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
        if not _cor_contract_authorized(contract_id):
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
        if not _cor_contract_authorized(contract_id):
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


# =====================================================================
# Option Period Tracker
# =====================================================================


@cpmp_api.route("/contracts/<contract_id>/options", methods=["GET"])
def list_contract_options(contract_id):
    """GET /api/cpmp/contracts/<id>/options — List option periods."""
    try:
        from tools.govcon.option_period_tracker import list_option_periods
        return jsonify(list_option_periods(contract_id))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/options", methods=["POST"])
@require_role("admin", "pm", "co", "contract_mgr")
def create_contract_option(contract_id):
    """POST /api/cpmp/contracts/<id>/options — Create option period."""
    try:
        from tools.govcon.option_period_tracker import create_option_period
        data = request.get_json(force=True) or {}
        result = create_option_period(contract_id, data)
        if result.get("status") == "error":
            return jsonify(result), 400
        return jsonify(result), 201
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/options/<option_id>", methods=["PUT"])
@require_role("admin", "pm", "co", "contract_mgr")
def update_contract_option(option_id):
    """PUT /api/cpmp/options/<id> — Update option period fields."""
    try:
        from tools.govcon.option_period_tracker import update_option_period
        data = request.get_json(force=True) or {}
        result = update_option_period(option_id, data)
        if result.get("status") == "error":
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/options/<option_id>/exercise", methods=["PUT"])
@require_role("admin", "pm", "co")
def exercise_contract_option(option_id):
    """PUT /api/cpmp/options/<id>/exercise — Mark option as exercised."""
    try:
        from tools.govcon.option_period_tracker import exercise_option
        actor = "system"
        if hasattr(g, "current_user") and g.current_user:
            actor = (g.current_user.get("username") or "system") if isinstance(g.current_user, dict) else "system"
        result = exercise_option(option_id, exercised_by=actor)
        if result.get("status") == "error":
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/options/<option_id>/recommend", methods=["GET"])
def option_ai_recommendation(option_id):
    """GET /api/cpmp/options/<id>/recommend — AI go/no-go recommendation."""
    try:
        from tools.govcon.option_period_tracker import ai_exercise_recommendation
        return jsonify(ai_exercise_recommendation(option_id))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/options/countdown", methods=["GET"])
def options_portfolio_countdown():
    """GET /api/cpmp/options/countdown — Portfolio-wide option period countdown."""
    try:
        from tools.govcon.option_period_tracker import get_portfolio_countdown
        return jsonify(get_portfolio_countdown())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Portfolio Deliverable Command Center
# =====================================================================


@cpmp_api.route("/portfolio/health-matrix", methods=["GET"])
def portfolio_health_matrix():
    """GET /api/cpmp/portfolio/health-matrix — Per-contract health dimension breakdown for the matrix grid."""
    try:
        from tools.govcon.portfolio_manager import compute_contract_health

        conn = _get_db()
        rows = conn.execute(
            "SELECT id, contract_number, title, agency, status FROM cpmp_contracts "
            "WHERE status NOT IN ('closed', 'terminated') ORDER BY contract_number ASC"
        ).fetchall()
        conn.close()

        matrix = []
        for r in rows:
            cid = r["id"]
            try:
                health = compute_contract_health(cid)
                dims = health.get("dimensions", {})
                matrix.append({
                    "contract_id": cid,
                    "contract_number": r["contract_number"],
                    "title": r["title"],
                    "agency": r["agency"],
                    "status": r["status"],
                    "overall": health.get("health", "unknown"),
                    "health_score": health.get("health_score"),
                    "evm": _dim_tier(dims.get("evm")),
                    "deliverables": _dim_tier(dims.get("deliverables")),
                    "cpars": _dim_tier(dims.get("cpars")),
                    "funding": _dim_tier(dims.get("funding")),
                    "negative_events": _dim_tier(dims.get("negative_events")),
                })
            except Exception:
                matrix.append({
                    "contract_id": cid,
                    "contract_number": r["contract_number"],
                    "title": r["title"],
                    "agency": r["agency"],
                    "status": r["status"],
                    "overall": "unknown",
                    "health_score": None,
                    "evm": "unknown", "deliverables": "unknown",
                    "cpars": "unknown", "funding": "unknown", "negative_events": "unknown",
                })

        return jsonify({"status": "ok", "contracts": matrix, "total": len(matrix)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


def _dim_tier(score):
    """Map a 0-1 dimension score to green/yellow/red/unknown."""
    if score is None:
        return "unknown"
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "unknown"
    if s >= 0.75:
        return "green"
    if s >= 0.50:
        return "yellow"
    return "red"


@cpmp_api.route("/deliverables/portfolio", methods=["GET"])
def portfolio_deliverables():
    """GET /api/cpmp/deliverables/portfolio — All deliverables across all contracts.

    Query params:
        contract_id  filter to single contract
        status       filter by deliverable status
        window       7 | 14 | 30 | overdue | all  (default: all)
        sort         due_date | contract | status  (default: due_date)
    """
    try:

        conn = _get_db()
        contract_id = request.args.get("contract_id")
        status_filter = request.args.get("status")
        window = request.args.get("window", "all")
        sort = request.args.get("sort", "due_date")

        where_clauses = ["1=1"]
        params = []

        if contract_id:
            where_clauses.append("d.contract_id = ?")
            params.append(contract_id)

        if status_filter:
            where_clauses.append("d.status = ?")
            params.append(status_filter)

        # Date window filtering is done in Python after fetch (avoids SQLite vs PG date syntax)

        order_col = {
            "contract": "c.contract_number",
            "status": "d.status",
        }.get(sort, "d.due_date")

        where_sql = " AND ".join(where_clauses)
        rows = conn.execute(
            f"""
            SELECT
                d.id, d.contract_id, d.cdrl_number, d.title, d.deliverable_type,
                d.due_date, d.submitted_date, d.accepted_date, d.status,
                d.days_overdue, d.generated_by_tool, d.reviewer, d.notes,
                c.contract_number, c.title AS contract_title, c.agency
            FROM cpmp_deliverables d
            JOIN cpmp_contracts c ON c.id = d.contract_id
            WHERE {where_sql}
            ORDER BY {order_col} ASC
            """,
            params,
        ).fetchall()
        conn.close()

        deliverables = []
        for r in rows:
            d = dict(r)
            due = d.get("due_date") or ""
            if due and d.get("status") not in ("accepted",):
                try:
                    from datetime import date as _d2
                    delta = (_d2.fromisoformat(due) - _d2.today()).days
                    d["days_until_due"] = delta
                    d["is_overdue"] = delta < 0
                    if delta < 0:
                        d["urgency"] = "overdue"
                    elif delta <= 7:
                        d["urgency"] = "critical"
                    elif delta <= 14:
                        d["urgency"] = "warning"
                    else:
                        d["urgency"] = "ok"
                except Exception:
                    d["days_until_due"] = None
                    d["is_overdue"] = False
                    d["urgency"] = "ok"
            else:
                d["days_until_due"] = None
                d["is_overdue"] = False
                d["urgency"] = "ok"
            deliverables.append(d)

        # Apply window filter in Python (avoids SQLite vs PostgreSQL date syntax differences)
        if window == "overdue":
            deliverables = [d for d in deliverables if d["is_overdue"]]
        elif window in ("7", "14", "30"):
            limit = int(window)
            deliverables = [d for d in deliverables
                            if d.get("days_until_due") is not None and 0 <= d["days_until_due"] <= limit]

        # Sort in Python since we filtered post-query
        if sort == "contract":
            deliverables.sort(key=lambda d: d.get("contract_number") or "")
        elif sort == "status":
            deliverables.sort(key=lambda d: d.get("status") or "")
        else:
            deliverables.sort(key=lambda d: d.get("due_date") or "")

        # Summary stats
        overdue = sum(1 for d in deliverables if d["is_overdue"])
        due_7 = sum(1 for d in deliverables if d.get("urgency") == "critical")
        due_14 = sum(1 for d in deliverables if d.get("urgency") in ("critical", "warning"))
        generated = sum(1 for d in deliverables if d.get("generated_by_tool"))

        return jsonify({
            "status": "ok",
            "total": len(deliverables),
            "summary": {
                "overdue": overdue,
                "due_in_7_days": due_7,
                "due_in_14_days": due_14,
                "generated_pct": round(generated / len(deliverables) * 100, 1) if deliverables else 0,
            },
            "deliverables": deliverables,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/deliverables/auto-generate-portfolio", methods=["POST"])
@require_role("admin", "pm", "co", "contract_mgr")
def auto_generate_portfolio_deliverables():
    """POST /api/cpmp/deliverables/auto-generate-portfolio — Auto-generate CDRLs due within N days.

    Body: { "days_ahead": 14 }
    """
    try:
        from tools.govcon.cdrl_generator import generate_all_due

        body = request.get_json(silent=True) or {}
        days_ahead = int(body.get("days_ahead", 14))

        conn = _get_db()
        contracts = conn.execute(
            "SELECT id, contract_number FROM cpmp_contracts WHERE status = 'active'"
        ).fetchall()
        conn.close()

        results = []
        total_generated = 0
        for c in contracts:
            try:
                res = generate_all_due(c["id"], days_ahead)
                generated = res.get("generated", 0)
                total_generated += generated
                results.append({
                    "contract_id": c["id"],
                    "contract_number": c["contract_number"],
                    "generated": generated,
                    "skipped": res.get("skipped", 0),
                    "errors": res.get("errors", []),
                })
            except Exception as ex:
                results.append({
                    "contract_id": c["id"],
                    "contract_number": c["contract_number"],
                    "generated": 0,
                    "error": str(ex),
                })

        return jsonify({
            "status": "ok",
            "contracts_processed": len(contracts),
            "total_generated": total_generated,
            "days_ahead": days_ahead,
            "results": results,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ── PMO Weekly Brief Archive ──────────────────────────────────────────────────


@cpmp_api.route("/reports/list", methods=["GET"])
def cpmp_list_pmo_reports():
    """Return sorted list of PMO weekly brief HTML files from data/reports/."""
    import re
    from datetime import datetime

    reports_dir = BASE_DIR / "data" / "reports"
    reports = []
    if reports_dir.exists():
        for f in sorted(reports_dir.glob("pmo_weekly_*.html"), reverse=True):
            m = re.search(r"pmo_weekly_(\d{4}-\d{2}-\d{2})\.html", f.name)
            if m:
                date_str = m.group(1)
                try:
                    label = datetime.strptime(date_str, "%Y-%m-%d").strftime("%b %d, %Y")
                except ValueError:
                    label = date_str
                reports.append({"filename": f.name, "date": date_str, "label": label})
    return jsonify({"reports": reports})


@cpmp_api.route("/reports/content/<path:filename>", methods=["GET"])
def cpmp_pmo_report_content(filename):
    """Serve a single PMO weekly brief HTML for iframe embedding."""
    import re

    if not re.fullmatch(r"pmo_weekly_\d{4}-\d{2}-\d{2}\.html", filename):
        return jsonify({"status": "error", "message": "Invalid filename"}), 400
    report_path = BASE_DIR / "data" / "reports" / filename
    if not report_path.exists():
        return jsonify({"status": "error", "message": "Report not found"}), 404
    content = report_path.read_text(encoding="utf-8")
    resp = make_response(content)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    return resp


@cpmp_api.route("/iqe-query", methods=["POST"])
@require_role("admin", "pm", "co", "contract_mgr")
def cpmp_iqe_query():
    """IQE NL-to-SQL for CPMP (Contract Portfolio Management) canvas."""
    from tools.iqe.nl_to_iqe import nl_to_iqe
    from tools.iqe.parser import IQESyntaxError, parse
    from tools.iqe.executor import execute_query
    import tools.iqe.adapters.cpmp  # noqa: F401

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    collections = ["cpmp.contracts", "cpmp.deliverables", "cpmp.clins", "cpmp.cpars", "cpmp.evm"]
    translation = nl_to_iqe(question, collections)
    iqe_str = translation.get("iqe", "")
    explanation = translation.get("explanation", "")

    if not data.get("execute", True):
        return jsonify({"ok": True, "iqe": iqe_str, "explanation": explanation}), 200

    try:
        ast = parse(iqe_str)
        rows = execute_query(ast, None)
        return jsonify({"ok": True, "iqe": iqe_str, "explanation": explanation,
                        "results": rows, "row_count": len(rows)}), 200
    except IQESyntaxError as exc:
        return jsonify({"error": f"IQE syntax error: {exc}", "iqe": iqe_str}), 400
    except Exception as exc:
        return jsonify({"error": str(exc), "iqe": iqe_str}), 500


# ---------------------------------------------------------------------------
# Initiative Budget Allocation Routes
# Tier 1 (execution-ready) / Tier 2 (backup) prioritization with obligation tracking.
# ---------------------------------------------------------------------------


@cpmp_api.route("/budget-allocations", methods=["GET"])
def list_budget_allocations():
    """List initiative budget allocations with optional tier/fy/status filters."""
    from tools.budget.initiative_allocator import (
        list_allocations, AllocationTier, AllocationStatus,
    )
    tier = request.args.get("tier")
    fy = request.args.get("fiscal_year", type=int)
    status = request.args.get("status")
    kwargs = {}
    if tier in ("tier_1", "tier_2"):
        kwargs["tier"] = AllocationTier(tier)
    if fy is not None:
        kwargs["fiscal_year"] = fy
    if status in ("active", "depleted", "deferred", "cancelled"):
        kwargs["status"] = AllocationStatus(status)
    return jsonify({"ok": True, "allocations": list_allocations(**kwargs)})


@cpmp_api.route("/budget-allocations", methods=["POST"])
@require_role("admin", "pm")
def create_budget_allocation():
    """Create a new budget allocation for an initiative."""
    from tools.budget.initiative_allocator import create_allocation, AllocationTier
    data = request.get_json(silent=True) or {}
    try:
        tier = AllocationTier(data.get("tier", "tier_1"))
        alloc = create_allocation(
            initiative_code=data.get("initiative_code", ""),
            title=data.get("title", ""),
            tier=tier,
            fiscal_year=int(data.get("fiscal_year", 0)),
            allocated_usd=float(data.get("allocated_usd", 0.0)),
            agency=data.get("agency", ""),
            contract_id=data.get("contract_id"),
            owner=data.get("owner", ""),
            justification=data.get("justification", ""),
        )
        return jsonify({"ok": True, "allocation": alloc}), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@cpmp_api.route("/budget-allocations/<allocation_id>", methods=["GET"])
def get_budget_allocation(allocation_id):
    """Fetch a single budget allocation by ID."""
    from tools.budget.initiative_allocator import get_allocation
    try:
        return jsonify({"ok": True, "allocation": get_allocation(allocation_id)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 404


@cpmp_api.route("/budget-allocations/<allocation_id>", methods=["PUT"])
@require_role("admin", "pm")
def update_budget_allocation(allocation_id):
    """Update mutable metadata (title, owner, allocated_usd, contract_id, etc)."""
    from tools.budget.initiative_allocator import update_allocation
    data = request.get_json(silent=True) or {}
    try:
        alloc = update_allocation(
            allocation_id=allocation_id,
            title=data.get("title"),
            agency=data.get("agency"),
            owner=data.get("owner"),
            justification=data.get("justification"),
            allocated_usd=data.get("allocated_usd"),
            contract_id=data.get("contract_id"),
        )
        return jsonify({"ok": True, "allocation": alloc})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@cpmp_api.route("/budget-allocations/<allocation_id>", methods=["DELETE"])
@require_role("admin", "pm")
def cancel_budget_allocation(allocation_id):
    """Soft-cancel an allocation (sets status=cancelled)."""
    from tools.budget.initiative_allocator import delete_allocation
    try:
        return jsonify({"ok": True, "allocation": delete_allocation(allocation_id)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@cpmp_api.route("/budget-allocations/<allocation_id>/obligations", methods=["POST"])
@require_role("admin", "pm")
def record_budget_obligation(allocation_id):
    """Record an obligation against an allocation. Blocks over-allocation."""
    from tools.budget.initiative_allocator import record_obligation
    data = request.get_json(silent=True) or {}
    try:
        amount = float(data.get("amount_usd", 0.0))
        result = record_obligation(
            allocation_id=allocation_id,
            amount_usd=amount,
            description=data.get("description", ""),
            reference_id=data.get("reference_id"),
            recorded_by=data.get("recorded_by"),
        )
        return jsonify({"ok": True, "allocation": result})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@cpmp_api.route("/budget-allocations/<allocation_id>/obligations", methods=["GET"])
def list_budget_obligations(allocation_id):
    """List all obligations for an allocation (append-only audit trail)."""
    from tools.budget.initiative_allocator import list_obligations
    return jsonify({"ok": True, "obligations": list_obligations(allocation_id)})


@cpmp_api.route("/budget-allocations/<allocation_id>/transition-tier", methods=["POST"])
@require_role("admin", "pm")
def transition_budget_tier(allocation_id):
    """Move an initiative between Tier 1 and Tier 2 with audit trail."""
    from tools.budget.initiative_allocator import transition_tier, AllocationTier
    data = request.get_json(silent=True) or {}
    try:
        target = AllocationTier(data.get("tier", "tier_1"))
        result = transition_tier(
            allocation_id=allocation_id,
            new_tier=target,
            reason=data.get("reason", ""),
            actor=data.get("actor"),
        )
        return jsonify({"ok": True, "allocation": result})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@cpmp_api.route("/budget-allocations/<allocation_id>/history", methods=["GET"])
def get_budget_allocation_history(allocation_id):
    """Return audit history (creation, tier transitions, obligations)."""
    from tools.budget.initiative_allocator import get_initiative_history
    return jsonify({"ok": True, "history": get_initiative_history(allocation_id)})


@cpmp_api.route("/budget-allocations/tier-summary", methods=["GET"])
def get_budget_tier_summary():
    """Aggregate allocated/obligated/available per tier for a fiscal year."""
    from tools.budget.initiative_allocator import get_tier_summary
    fy = request.args.get("fiscal_year", type=int)
    return jsonify({"ok": True, "summary": get_tier_summary(fiscal_year=fy)})


@cpmp_api.route("/budget-allocations/portfolio-status", methods=["GET"])
def get_budget_portfolio_status():
    """Portfolio-level budget status (warnings + overspend detection)."""
    from tools.budget.initiative_allocator import get_portfolio_budget_status
    fy = request.args.get("fiscal_year", type=int)
    warn = request.args.get("warning_threshold", default=0.90, type=float)
    crit = request.args.get("critical_threshold", default=0.98, type=float)
    return jsonify({
        "ok": True,
        "status": get_portfolio_budget_status(
            fiscal_year=fy, warning_threshold=warn, critical_threshold=crit,
        ),
    })


# =====================================================================
# Personnel Registry
# =====================================================================


@cpmp_api.route("/contracts/<contract_id>/personnel", methods=["POST"])
@require_role("admin", "pm", "isso")
def upsert_personnel(contract_id):
    """POST /api/cpmp/contracts/<id>/personnel — Create or update a personnel record."""
    try:
        from tools.govcon.personnel_manager import upsert_person

        data = request.get_json(silent=True) or {}
        result = upsert_person(contract_id, data)
        if result.get("status") == "error":
            return jsonify(result), 400
        return jsonify(result), 201
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/personnel", methods=["GET"])
def list_personnel(contract_id):
    """GET /api/cpmp/contracts/<id>/personnel — List personnel with optional filters."""
    try:
        from tools.govcon.personnel_manager import list_personnel as _list

        clearance_level = request.args.get("clearance_level")
        lcat = request.args.get("lcat")
        result = _list(contract_id, clearance_level=clearance_level, lcat=lcat)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/personnel/<person_id>", methods=["PUT"])
@require_role("admin", "pm", "isso")
def update_personnel(person_id):
    """PUT /api/cpmp/personnel/<pid> — Update personnel status or backup assignment."""
    try:
        from tools.govcon.personnel_manager import update_person

        data = request.get_json(silent=True) or {}
        result = update_person(person_id, data)
        if result.get("status") == "error":
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/personnel/expiring", methods=["GET"])
def list_expiring_personnel(contract_id):
    """GET /api/cpmp/contracts/<id>/personnel/expiring — Personnel with credentials expiring within ?days=90."""
    try:
        from tools.govcon.personnel_manager import get_expiring_personnel

        days = request.args.get("days", default=90, type=int)
        result = get_expiring_personnel(contract_id, days=days)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/personnel/key-persons", methods=["GET"])
def list_key_persons(contract_id):
    """GET /api/cpmp/contracts/<id>/personnel/key-persons — Key personnel list."""
    try:
        from tools.govcon.personnel_manager import get_key_persons

        result = get_key_persons(contract_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/personnel/alerts", methods=["GET"])
def list_personnel_alerts(contract_id):
    """GET /api/cpmp/contracts/<id>/personnel/alerts — Open credential alerts for the contract."""
    try:
        from tools.govcon.personnel_manager import get_personnel_alerts

        result = get_personnel_alerts(contract_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/personnel/alerts/<alert_id>", methods=["PUT"])
@require_role("admin", "pm", "isso")
def update_personnel_alert(alert_id):
    """PUT /api/cpmp/personnel/alerts/<aid> — Acknowledge or resolve a credential alert."""
    try:
        from tools.govcon.personnel_manager import update_alert

        data = request.get_json(silent=True) or {}
        result = update_alert(alert_id, data)
        if result.get("status") == "error":
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# INT Coverage Map and Collection Requirements (pma-igap-04)
# =====================================================================

_COVERAGE_STATUSES = ("gap", "partial", "covered")
_REQ_STATUSES = ("open", "tasked", "satisfied")
_REQ_PRIORITIES = ("critical", "high", "medium", "low")

_REQ_TEMPLATES = [
    "Collect {discipline} intelligence covering {area}",
    "Establish persistent collection posture for {discipline} in {area}",
    "Identify key {discipline} collection assets available for {area}",
    "Assess collection gaps and develop supplemental {discipline} requirements for {area}",
    "Coordinate multi-source {discipline} collection to address coverage shortfall in {area}",
]


def _generate_req_texts(discipline, coverage_area, count):
    """Return deterministic template-based requirement texts (LLM fallback)."""
    area = coverage_area or "designated area"
    disc = discipline or "INT"
    return [
        _REQ_TEMPLATES[i % len(_REQ_TEMPLATES)].format(discipline=disc, area=area)
        for i in range(count)
    ]


def _row_to_dict(row):
    return dict(row) if not isinstance(row, dict) else row


@cpmp_api.route("/contracts/<contract_id>/coverage", methods=["POST"])
@require_role("admin", "pm", "isso")
def upsert_int_coverage(contract_id):
    """POST /api/cpmp/contracts/<id>/coverage — Upsert INT coverage record."""
    try:
        data = request.get_json(silent=True) or {}
        status = data.get("status", "gap")
        if status not in _COVERAGE_STATUSES:
            return jsonify({"status": "error", "message": f"status must be one of {_COVERAGE_STATUSES}"}), 400

        now = datetime.now(timezone.utc).isoformat()
        conn = _get_db()
        try:
            cid = _uuid()
            conn.execute(
                """INSERT INTO cpmp_int_coverage
                   (id, contract_id, discipline, coverage_area, status, confidence,
                    source_type, notes, last_assessed, persistent_since, metadata,
                    created_at, updated_at, classification)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT(id) DO NOTHING""",
                (
                    cid, contract_id,
                    data.get("discipline", ""),
                    data.get("coverage_area", ""),
                    status,
                    float(data.get("confidence", 0.0)),
                    data.get("source_type", ""),
                    data.get("notes"),
                    data.get("last_assessed"),
                    data.get("persistent_since"),
                    _mac_json.dumps(data.get("metadata", {})),
                    now, now, "CUI",
                ),
            )
            conn.commit()
            _audit(conn, "upsert_coverage", f"contract={contract_id} cov={cid}")
            record = _row_to_dict(
                conn.execute(
                    "SELECT * FROM cpmp_int_coverage WHERE id = %s", (cid,)
                ).fetchone()
            )
            return jsonify({"status": "ok", "coverage": record}), 201
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/coverage", methods=["GET"])
def list_int_coverage(contract_id):
    """GET /api/cpmp/contracts/<id>/coverage — List coverage records with optional status filter."""
    try:
        status_filter = request.args.get("status")
        conn = _get_db()
        try:
            if status_filter and status_filter in _COVERAGE_STATUSES:
                rows = conn.execute(
                    "SELECT * FROM cpmp_int_coverage WHERE contract_id = %s AND status = %s ORDER BY discipline, coverage_area",
                    (contract_id, status_filter),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM cpmp_int_coverage WHERE contract_id = %s ORDER BY discipline, coverage_area",
                    (contract_id,),
                ).fetchall()
            return jsonify({"status": "ok", "coverage": [_row_to_dict(r) for r in rows], "count": len(rows)})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/coverage/gaps", methods=["GET"])
def list_coverage_gaps(contract_id):
    """GET /api/cpmp/contracts/<id>/coverage/gaps — Gap and partial records only."""
    try:
        conn = _get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM cpmp_int_coverage WHERE contract_id = %s AND status IN ('gap', 'partial') ORDER BY discipline, coverage_area",
                (contract_id,),
            ).fetchall()
            return jsonify({"status": "ok", "gaps": [_row_to_dict(r) for r in rows], "count": len(rows)})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/coverage/persistent", methods=["GET"])
def list_persistent_gaps(contract_id):
    """GET /api/cpmp/contracts/<id>/coverage/persistent — Persistent gaps (?days=14)."""
    try:
        days = request.args.get("days", default=14, type=int)
        conn = _get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM cpmp_int_coverage "
                "WHERE contract_id = %s AND status IN ('gap', 'partial') "
                "AND persistent_since IS NOT NULL "
                "AND persistent_since <= datetime('now', %s || ' days') "
                "ORDER BY persistent_since",
                (contract_id, f"-{days}"),
            ).fetchall()
            return jsonify({"status": "ok", "persistent_gaps": [_row_to_dict(r) for r in rows], "days": days, "count": len(rows)})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/coverage/<coverage_id>/generate-reqs", methods=["POST"])
@require_role("admin", "pm", "isso")
def generate_collection_requirements(contract_id, coverage_id):
    """POST /api/cpmp/contracts/<id>/coverage/<cid>/generate-reqs — AI-generate collection requirements."""
    try:
        data = request.get_json(silent=True) or {}
        count = max(1, min(int(data.get("count", 3)), 10))
        priority = data.get("priority", "medium")
        if priority not in _REQ_PRIORITIES:
            priority = "medium"

        conn = _get_db()
        try:
            cov_row = conn.execute(
                "SELECT id, discipline, coverage_area, status FROM cpmp_int_coverage "
                "WHERE id = %s AND contract_id = %s",
                (coverage_id, contract_id),
            ).fetchone()
            if not cov_row:
                return jsonify({"status": "error", "message": "Coverage record not found"}), 404

            cov = _row_to_dict(cov_row)
            discipline = cov.get("discipline") or "INT"
            coverage_area = cov.get("coverage_area") or ""

            req_texts = _generate_req_texts(discipline, coverage_area, count)
            now = datetime.now(timezone.utc).isoformat()
            created = []
            for text in req_texts:
                rid = _uuid()
                conn.execute(
                    "INSERT INTO cpmp_collection_requirements "
                    "(id, coverage_id, contract_id, requirement_text, discipline, priority, "
                    "status, ai_generated, created_at, updated_at, classification) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 'open', 1, %s, %s, 'CUI')",
                    (rid, coverage_id, contract_id, text, discipline, priority, now, now),
                )
                created.append({
                    "id": rid,
                    "coverage_id": coverage_id,
                    "contract_id": contract_id,
                    "requirement_text": text,
                    "discipline": discipline,
                    "priority": priority,
                    "status": "open",
                    "ai_generated": True,
                    "created_at": now,
                })
            conn.commit()
            _audit(conn, "generate_reqs", f"coverage={coverage_id} count={len(created)}")
            return jsonify({"status": "ok", "requirements": created, "count": len(created)}), 201
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/coverage/<coverage_id>/requirements", methods=["GET"])
def list_coverage_requirements(coverage_id):
    """GET /api/cpmp/coverage/<cid>/requirements — List requirements for a coverage record."""
    try:
        conn = _get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM cpmp_collection_requirements WHERE coverage_id = %s ORDER BY priority, created_at",
                (coverage_id,),
            ).fetchall()
            return jsonify({"status": "ok", "requirements": [_row_to_dict(r) for r in rows], "count": len(rows)})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/requirements/<requirement_id>", methods=["PUT"])
@require_role("admin", "pm", "isso")
def update_collection_requirement(requirement_id):
    """PUT /api/cpmp/requirements/<rid> — Update status: open→tasked→satisfied."""
    try:
        data = request.get_json(silent=True) or {}
        new_status = data.get("status")
        if new_status and new_status not in _REQ_STATUSES:
            return jsonify({"status": "error", "message": f"status must be one of {_REQ_STATUSES}"}), 400

        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT * FROM cpmp_collection_requirements WHERE id = %s",
                (requirement_id,),
            ).fetchone()
            if not row:
                return jsonify({"status": "error", "message": "Requirement not found"}), 404

            now = datetime.now(timezone.utc).isoformat()
            updates = {"updated_at": now}
            if new_status:
                updates["status"] = new_status
            if "tasked_to" in data:
                updates["tasked_to"] = data["tasked_to"]
            if "notes" in data:
                updates["notes"] = data["notes"]
            if new_status == "tasked" and not dict(row).get("tasked_at"):
                updates["tasked_at"] = now
            if new_status == "satisfied" and not dict(row).get("satisfied_at"):
                updates["satisfied_at"] = now

            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [requirement_id]
            conn.execute(
                f"UPDATE cpmp_collection_requirements SET {set_clause} WHERE id = %s",
                values,
            )
            conn.commit()
            _audit(conn, "update_requirement", f"req={requirement_id} status={new_status}")
            updated = _row_to_dict(
                conn.execute(
                    "SELECT * FROM cpmp_collection_requirements WHERE id = %s",
                    (requirement_id,),
                ).fetchone()
            )
            return jsonify({"status": "ok", "requirement": updated})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/requirements", methods=["GET"])
def list_contract_requirements(contract_id):
    """GET /api/cpmp/contracts/<id>/requirements — All requirements, filterable by discipline/status."""
    try:
        discipline = request.args.get("discipline")
        status_filter = request.args.get("status")
        conn = _get_db()
        try:
            where = ["contract_id = ?"]
            params = [contract_id]
            if discipline:
                where.append("discipline = ?")
                params.append(discipline)
            if status_filter and status_filter in _REQ_STATUSES:
                where.append("status = ?")
                params.append(status_filter)
            sql = (
                "SELECT * FROM cpmp_collection_requirements WHERE "
                + " AND ".join(where)
                + " ORDER BY discipline, status, created_at"
            )
            rows = conn.execute(sql, params).fetchall()
            return jsonify({"status": "ok", "requirements": [_row_to_dict(r) for r in rows], "count": len(rows)})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Meeting Coordination (pma-coord-02)
# =====================================================================


@cpmp_api.route("/contracts/<contract_id>/meetings", methods=["POST"])
@require_role("admin", "pm", "co", "contract_mgr")
def create_meeting(contract_id):
    """POST /api/cpmp/contracts/<id>/meetings — Create a meeting log."""
    try:
        from tools.pma.meeting_coordinator import create_meeting as _create

        data = request.get_json(silent=True) or {}
        result = _create(contract_id, data)
        return jsonify(result), 201
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/meetings", methods=["GET"])
def list_meetings(contract_id):
    """GET /api/cpmp/contracts/<id>/meetings — List meeting logs."""
    try:
        from tools.pma.meeting_coordinator import list_meetings as _list

        return jsonify({"status": "ok", "meetings": _list(contract_id)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/meetings/<meeting_id>/extract-actions", methods=["POST"])
@require_role("admin", "pm", "co", "contract_mgr")
def extract_meeting_actions(meeting_id):
    """POST /api/cpmp/meetings/<id>/extract-actions — AI-extract action items from notes."""
    try:
        from tools.pma.meeting_coordinator import extract_action_items

        data = request.get_json(silent=True) or {}
        notes = data.get("notes", "")
        items = extract_action_items(meeting_id, notes)
        return jsonify({"status": "ok", "action_items": items, "count": len(items)}), 201
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/contracts/<contract_id>/meetings/overdue", methods=["GET"])
def list_overdue_action_items(contract_id):
    """GET /api/cpmp/contracts/<id>/meetings/overdue — Open items past due date."""
    try:
        from tools.pma.meeting_coordinator import get_overdue_action_items

        items = get_overdue_action_items(contract_id)
        return jsonify({"status": "ok", "overdue_items": items, "count": len(items)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/meetings/<meeting_id>/action-items", methods=["GET"])
def list_meeting_action_items(meeting_id):
    """GET /api/cpmp/meetings/<id>/action-items — List action items for a meeting."""
    try:
        from tools.pma.meeting_coordinator import list_action_items

        return jsonify({"status": "ok", "action_items": list_action_items(meeting_id)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@cpmp_api.route("/action-items/<item_id>", methods=["PUT"])
@require_role("admin", "pm", "co", "contract_mgr")
def update_action_item(item_id):
    """PUT /api/cpmp/action-items/<id> — HITL approval gate + status update."""
    try:
        from tools.pma.meeting_coordinator import update_action_item as _update

        data = request.get_json(silent=True) or {}
        result = _update(item_id, data)
        if result.get("status") == "error":
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
