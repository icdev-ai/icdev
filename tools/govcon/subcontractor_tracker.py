# CUI // SP-CTI
# ICDEV™ GovProposal — Subcontractor Tracker (Phase 60, D-CPMP-1)
# FAR 52.219-9 small business subcontracting compliance, ISR/SSR generation.

"""
Subcontractor Tracker — FAR 52.219-9 compliance, flow-down verification,
cybersecurity checks, and ISR/SSR report generation.

Manages:
    - Subcontractor CRUD (create, update, list with filters)
    - Small business compliance calculation (SB, SDB, WOSB, HUBZone, SDVOSB)
    - Flow-down clause verification
    - Cybersecurity/CMMC compliance checking
    - ISR/SSR report generation and listing
    - Noncompliance detection (flow-down, cyber, CMMC, ISR/SSR currency)

Tables used:
    - cpmp_subcontractors (CRUD)
    - cpmp_small_business_plan (create ISR/SSR)
    - cpmp_status_history (write — status change audit)
    - audit_trail (write — NIST AU-2)

Usage:
    python tools/govcon/subcontractor_tracker.py --create --contract-id <id> --data '{}' --json
    python tools/govcon/subcontractor_tracker.py --update --sub-id <id> --data '{}' --json
    python tools/govcon/subcontractor_tracker.py --list --contract-id <id> [--business-size small] --json
    python tools/govcon/subcontractor_tracker.py --sb-compliance --contract-id <id> --json
    python tools/govcon/subcontractor_tracker.py --check-flowdown --contract-id <id> --json
    python tools/govcon/subcontractor_tracker.py --check-cyber --contract-id <id> --json
    python tools/govcon/subcontractor_tracker.py --create-report --contract-id <id> --period 2025-Q1 --type isr --json
    python tools/govcon/subcontractor_tracker.py --list-reports --contract-id <id> --json
    python tools/govcon/subcontractor_tracker.py --detect-noncompliance --contract-id <id> --json
"""

import argparse
import json
import os
import uuid
from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path

import yaml
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.subcontractor_tracker")

_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))
_CONFIG_PATH = _ROOT / "args" / "govcon_config.yaml"


# ── Config ───────────────────────────────────────────────────────────


def _load_config():
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f).get("cpmp", {})
    return {}


_CFG = _load_config()

# Small business categories that count toward SB goals per FAR 52.219-9
SB_CATEGORIES = ("small", "sdb", "wosb", "hubzone", "sdvosb", "8a")

# Cybersecurity compliance check threshold (subcontract value)
# From negative_events.auto_detect.flowdown_failure trigger
_CYBER_THRESHOLD = 100000.0

# ISR/SSR currency window (days) — reports older than this are flagged
_ISR_SSR_MAX_AGE_DAYS = 180

# FAR 19.702(a)(1) — a small business subcontracting plan, and therefore the
# FAR 52.219-9(d)(10) ISR/SSR filing obligation the plan carries, attaches only
# to a contract expected to exceed this value. Below it there is no plan, so
# there is no report to be late with.
#
# The construction carve-out (FAR 19.702(a)(2), $1,500,000 for construction of a
# public facility) is deliberately NOT applied. 'Public facility' is not
# derivable from naics_code, and inferring it from NAICS sector 23 would raise
# the bar for every construction contract — converting a visible false positive
# into a silent false negative, which is the strictly worse trade.
_SB_PLAN_THRESHOLD = 750000.0


# ── Helpers ──────────────────────────────────────────────────────────


def _get_db():
    conn = get_connection()
    # Govcon tools are service-layer operations — clear any Flask RLS context
    # so that complex queries don't fail with RLS column injection errors.
    conn.set_security_context(None)  # rls-bypass: govcon service-layer; complex queries fail with RLS column injection
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uuid():
    return str(uuid.uuid4())


# Fields stored as INTEGER 0/1 that callers naturally send as JSON booleans.
COMPLIANCE_FLAGS = (
    "flow_down_complete",
    "flowdown_verified",
    "cybersecurity_compliant",
    "isr_ssr_current",
)

# The only strings that mean "compliant". Everything else — including an
# unrecognised word — means not compliant. See _compliance_flag.
_TRUE_STRINGS = frozenset({"1", "true", "t", "yes", "y", "on"})


def _compliance_flag(value):
    """Coerce a caller-supplied compliance flag to the INTEGER 0/1 the column holds.

    These are INTEGER columns and PostgreSQL — unlike SQLite, where ``bool`` is
    an ``int`` subclass and everything just works — refuses a boolean for an
    integer column outright ("column is of type integer but expression is of
    type boolean"). ``create_subcontractor`` has coerced since #1520;
    ``update_subcontractor`` passed ``data[field]`` through raw, so
    ``PUT /api/cpmp/subcontractors/<id>`` with ``{"flow_down_complete": true}``
    — a JSON boolean, i.e. exactly what the remediation this module exists to
    track looks like — raised on the primary backend and returned 500. The gap
    survived because the whole test suite runs on SQLite (tests/conftest.py
    forces it), so no test could ever see it, and no test exercises the PUT at
    all. Both write paths share this helper so they cannot drift apart again.

    Coercion is FAIL-CLOSED: anything not recognised as affirmative reads as 0
    (non-compliant). ``bool("0")`` is ``True`` in Python, so the obvious
    ``int(bool(value))`` turns a form-encoded "0" into "flow-down complete" and
    silently retires a FAR 52.219-9 gap nobody closed. For a compliance flag the
    safe direction of a parsing mistake is to keep reporting the gap.
    """
    if isinstance(value, bool):
        return int(value)
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value != 0)
    if isinstance(value, str):
        return int(value.strip().casefold() in _TRUE_STRINGS)
    return 0


def _audit(conn, action, details="", actor="subcontractor_tracker"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (event_type, actor, action, details, session_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("hook_event_logged", actor, action, details, "cpmp"),
        )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_audit: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


def _record_status_change(conn, entity_type, entity_id, old_status, new_status, changed_by=None, reason=None):
    """Record status change in cpmp_status_history (append-only, NIST AU-2)."""
    conn.execute(
        "INSERT INTO cpmp_status_history (entity_type, entity_id, old_status, new_status, changed_by, reason) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (entity_type, entity_id, old_status, new_status, changed_by, reason),
    )


# ── Subcontractor CRUD ──────────────────────────────────────────────


def create_subcontractor(contract_id, data):
    """Add a subcontractor to cpmp_subcontractors.

    Args:
        contract_id: Parent contract UUID.
        data: Dict with subcontractor fields (company_name required).

    Returns:
        Dict with status and sub_id.
    """
    conn = _get_db()
    if not conn.execute("SELECT id FROM cpmp_contracts WHERE id = %s", (contract_id,)).fetchone():
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    # company_name is required. Defaulting it to "Unknown" produced rows that no
    # one could act on and that check_flowdown reported forever, because
    # flow_down_complete also defaults to 0 (non-compliant). Rejecting here is
    # also what surfaces a caller sending the wrong key names — every other
    # field has a legitimate default, so a mismatched body is otherwise silent.
    company_name = data.get("company_name")
    if not isinstance(company_name, str) or not company_name.strip():
        conn.close()
        return {"status": "error", "message": "company_name is required and must be a non-empty string"}
    company_name = company_name.strip()

    sub_id = _uuid()
    conn.execute(
        "INSERT INTO cpmp_subcontractors "
        "(id, contract_id, company_name, cage_code, uei, business_size, "
        "subcontract_value, performance_rating, "
        "flow_down_complete, flowdown_verified, cybersecurity_compliant, cmmc_level, isr_ssr_current, "
        "status, notes, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            sub_id,
            contract_id,
            company_name,
            data.get("cage_code"),
            data.get("uei"),
            data.get("business_size", "large"),
            data.get("subcontract_value", 0.0),
            data.get("performance_rating"),
            # Coerce boolean flags to int — these are INTEGER columns and PG
            # (unlike SQLite) rejects a boolean expression for an integer column.
            _compliance_flag(data.get("flow_down_complete", 0)),
            _compliance_flag(data.get("flowdown_verified", 0)),
            _compliance_flag(data.get("cybersecurity_compliant", 0)),
            data.get("cmmc_level"),
            _compliance_flag(data.get("isr_ssr_current", 0)),
            data.get("status", "active"),
            data.get("notes"),
            _now(),
            _now(),
        ),
    )
    _record_status_change(
        conn, "subcontractor", sub_id, None, data.get("status", "active"), "system", "Subcontractor created"
    )
    _audit(
        conn,
        "create_subcontractor",
        f"Created subcontractor {company_name} on contract {contract_id}",
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "sub_id": sub_id}


def update_subcontractor(sub_id, data):
    """Update subcontractor fields. Record status changes in cpmp_status_history.

    Args:
        sub_id: Subcontractor UUID.
        data: Dict with fields to update.

    Returns:
        Dict with status and updated_fields list.
    """
    conn = _get_db()
    row = conn.execute("SELECT id, status FROM cpmp_subcontractors WHERE id = %s", (sub_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Subcontractor {sub_id} not found"}

    old_status = row["status"]

    # company_name is required on create; blanking it on update recreates the
    # same phantom — a row with no name, permanently non-compliant because
    # flow_down_complete defaults to 0, that check_flowdown reports forever and
    # nobody can issue a cure notice to. Validate only when the caller supplies
    # the key, so ordinary partial updates are unaffected.
    if "company_name" in data:
        company_name = data["company_name"]
        if not isinstance(company_name, str) or not company_name.strip():
            conn.close()
            return {
                "status": "error",
                "message": "company_name must be a non-empty string",
            }
        data = {**data, "company_name": company_name.strip()}

    updatable = [
        "company_name",
        "cage_code",
        "uei",
        "business_size",
        "subcontract_value",
        "performance_rating",
        "flow_down_complete",
        "flowdown_verified",
        "cybersecurity_compliant",
        "cmmc_level",
        "isr_ssr_current",
        "status",
        "notes",
    ]
    sets = []
    params = []
    for field in updatable:
        if field in data:
            sets.append(f"{field} = %s")
            # The 0/1 compliance flags go through the same coercion create uses.
            # Without it a JSON `true` reached psycopg2 as a Python bool and PG
            # rejected it for an INTEGER column, so recording the very
            # remediation this module tracks — "flow-down is now complete" —
            # failed with a 500 on the primary backend.
            params.append(
                _compliance_flag(data[field]) if field in COMPLIANCE_FLAGS else data[field]
            )

    if not sets:
        conn.close()
        return {"status": "error", "message": "No updatable fields provided"}

    sets.append("updated_at = %s")
    params.append(_now())
    params.append(sub_id)

    conn.execute(f"UPDATE cpmp_subcontractors SET {', '.join(sets)} WHERE id = %s", params)  # nosec B608 -- table/column names are internal constants, not user input

    # Record status change if status was modified
    if "status" in data and data["status"] != old_status:
        _record_status_change(
            conn, "subcontractor", sub_id, old_status, data["status"], data.get("changed_by"), data.get("reason")
        )

    _audit(conn, "update_subcontractor", f"Updated subcontractor {sub_id}: {list(data.keys())}")
    conn.commit()
    conn.close()
    return {"status": "ok", "sub_id": sub_id, "updated_fields": list(data.keys())}


def list_subcontractors(contract_id, business_size=None):
    """List subcontractors for a contract with optional business_size filter.

    Args:
        contract_id: Parent contract UUID.
        business_size: Optional filter — 'small' returns all SB categories,
                       or a specific category like 'sdb', 'wosb', etc.

    Returns:
        Dict with status, total count, and subcontractors list.
    """
    conn = _get_db()
    query = "SELECT * FROM cpmp_subcontractors WHERE contract_id = ?"
    params = [contract_id]

    if business_size:
        if business_size == "small":
            # "small" means all small-business categories per FAR 52.219-9
            placeholders = ", ".join("?" for _ in SB_CATEGORIES)
            query += f" AND business_size IN ({placeholders})"
            params.extend(SB_CATEGORIES)
        else:
            query += " AND business_size = ?"
            params.append(business_size)

    query += " ORDER BY company_name ASC"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return {"status": "ok", "total": len(rows), "subcontractors": [dict(r) for r in rows]}


# ── Small Business Compliance ────────────────────────────────────────


def compute_sb_compliance(contract_id):
    """Calculate small business subcontracting compliance per FAR 52.219-9.

    Groups active subcontractors by business_size, calculates actual percentages
    for each SB category, and compares against goals from the latest
    cpmp_small_business_plan record.

    Args:
        contract_id: Parent contract UUID.

    Returns:
        Dict with per-category compliance status and overall compliance flag.
    """
    conn = _get_db()

    # Get all active subcontractors for this contract
    rows = conn.execute(
        "SELECT business_size, subcontract_value FROM cpmp_subcontractors WHERE contract_id = %s AND status = 'active'",
        (contract_id,),
    ).fetchall()

    if not rows:
        conn.close()
        return {
            "status": "ok",
            "contract_id": contract_id,
            "total_subcontract_dollars": 0.0,
            "categories": {},
            "overall_compliant": False,
            "message": "No active subcontractors found",
        }

    # Calculate totals by category
    total_dollars = sum(r["subcontract_value"] or 0.0 for r in rows)
    category_dollars = {}
    for row in rows:
        size = row["business_size"] or "large"
        category_dollars[size] = category_dollars.get(size, 0.0) + (row["subcontract_value"] or 0.0)

    # SB aggregate = sum of all small business categories
    sb_dollars = sum(category_dollars.get(cat, 0.0) for cat in SB_CATEGORIES)

    # Calculate actual percentages
    def _pct(dollars):
        return round((dollars / total_dollars * 100) if total_dollars > 0 else 0.0, 2)

    actuals = {
        "sb": {"dollars": sb_dollars, "pct": _pct(sb_dollars)},
        "sdb": {"dollars": category_dollars.get("sdb", 0.0), "pct": _pct(category_dollars.get("sdb", 0.0))},
        "wosb": {"dollars": category_dollars.get("wosb", 0.0), "pct": _pct(category_dollars.get("wosb", 0.0))},
        "hubzone": {"dollars": category_dollars.get("hubzone", 0.0), "pct": _pct(category_dollars.get("hubzone", 0.0))},
        "sdvosb": {"dollars": category_dollars.get("sdvosb", 0.0), "pct": _pct(category_dollars.get("sdvosb", 0.0))},
    }

    # Get goals from latest SB plan
    plan_row = conn.execute(
        "SELECT * FROM cpmp_small_business_plan WHERE contract_id = %s ORDER BY created_at DESC LIMIT 1",
        (contract_id,),
    ).fetchone()
    conn.close()

    goals = {}
    if plan_row:
        goals = {
            "sb": plan_row["sb_goal_pct"] or 0.0,
            "sdb": plan_row["sdb_goal_pct"] or 0.0,
            "wosb": plan_row["wosb_goal_pct"] or 0.0,
            "hubzone": plan_row["hubzone_goal_pct"] or 0.0,
            "sdvosb": plan_row["sdvosb_goal_pct"] or 0.0,
        }

    # Compare actuals vs goals
    categories = {}
    overall_compliant = True
    for cat in ("sb", "sdb", "wosb", "hubzone", "sdvosb"):
        goal_pct = goals.get(cat, 0.0)
        actual_pct = actuals[cat]["pct"]
        met = actual_pct >= goal_pct if goal_pct > 0 else True
        if not met:
            overall_compliant = False
        categories[cat] = {
            "goal_pct": goal_pct,
            "actual_pct": actual_pct,
            "actual_dollars": actuals[cat]["dollars"],
            "met": met,
            "gap_pct": round(goal_pct - actual_pct, 2) if not met else 0.0,
        }

    return {
        "status": "ok",
        "contract_id": contract_id,
        "total_subcontract_dollars": total_dollars,
        "categories": categories,
        "overall_compliant": overall_compliant,
        "has_goals": bool(plan_row),
    }


# ── Flow-Down Verification ──────────────────────────────────────────


def check_flowdown(contract_id):
    """Check which subcontractors have incomplete flow-down clauses.

    FAR 52.219-9 requires flow-down of applicable clauses to subcontractors.

    Args:
        contract_id: Parent contract UUID.

    Returns:
        Dict with non-compliant subcontractor list.
    """
    conn = _get_db()
    rows = conn.execute(
        "SELECT id, company_name, cage_code, uei, business_size, subcontract_value, "
        "flow_down_complete, status "
        "FROM cpmp_subcontractors "
        "WHERE contract_id = %s AND status = 'active' AND flow_down_complete = 0 "
        "ORDER BY subcontract_value DESC",
        (contract_id,),
    ).fetchall()
    conn.close()

    non_compliant = [dict(r) for r in rows]
    return {
        "status": "ok",
        "contract_id": contract_id,
        "non_compliant_count": len(non_compliant),
        "non_compliant": non_compliant,
        "compliant": len(non_compliant) == 0,
    }


# ── Cybersecurity Compliance ────────────────────────────────────────


def check_cybersecurity(contract_id):
    """Check cybersecurity compliance for subcontractors with value > threshold.

    Subcontractors above the threshold (default $100,000) must be cybersecurity
    compliant. Also checks CMMC level where applicable.

    Args:
        contract_id: Parent contract UUID.

    Returns:
        Dict with non-compliant subcontractor list.
    """
    _CFG.get("negative_events", {}).get("auto_detect", {}).get("flowdown_failure", {})
    # Extract numeric threshold from trigger string if available, else use default
    cyber_threshold = _CYBER_THRESHOLD

    conn = _get_db()
    rows = conn.execute(
        "SELECT id, company_name, cage_code, uei, business_size, subcontract_value, "
        "cybersecurity_compliant, cmmc_level, status "
        "FROM cpmp_subcontractors "
        "WHERE contract_id = %s AND status = 'active' "
        "AND subcontract_value > %s AND cybersecurity_compliant = 0 "
        "ORDER BY subcontract_value DESC",
        (contract_id, cyber_threshold),
    ).fetchall()
    conn.close()

    non_compliant = [dict(r) for r in rows]
    return {
        "status": "ok",
        "contract_id": contract_id,
        "threshold": cyber_threshold,
        "non_compliant_count": len(non_compliant),
        "non_compliant": non_compliant,
        "compliant": len(non_compliant) == 0,
    }


# ── ISR/SSR Report Generation ───────────────────────────────────────


def create_sb_report(contract_id, reporting_period, report_type="isr"):
    """Create an ISR (Individual Subcontracting Report) or SSR (Summary Subcontracting Report).

    Auto-populates actual percentages and dollar amounts from current subcontractor data.

    Args:
        contract_id: Parent contract UUID.
        reporting_period: Period string (e.g. '2025-Q1', '2025-H1').
        report_type: 'isr' or 'ssr'.

    Returns:
        Dict with status and report_id.
    """
    if report_type not in ("isr", "ssr"):
        return {"status": "error", "message": f"Invalid report_type: {report_type}. Must be 'isr' or 'ssr'."}

    conn = _get_db()
    if not conn.execute("SELECT id FROM cpmp_contracts WHERE id = %s", (contract_id,)).fetchone():
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    # Compute current actuals from subcontractor data
    rows = conn.execute(
        "SELECT business_size, subcontract_value FROM cpmp_subcontractors WHERE contract_id = %s AND status = 'active'",
        (contract_id,),
    ).fetchall()

    total_dollars = sum(r["subcontract_value"] or 0.0 for r in rows)
    category_dollars = {}
    for row in rows:
        size = row["business_size"] or "large"
        category_dollars[size] = category_dollars.get(size, 0.0) + (row["subcontract_value"] or 0.0)

    sb_dollars = sum(category_dollars.get(cat, 0.0) for cat in SB_CATEGORIES)

    def _pct(dollars):
        return round((dollars / total_dollars * 100) if total_dollars > 0 else 0.0, 2)

    # Pull goals from latest existing plan (if any) to carry forward
    plan_row = conn.execute(
        "SELECT sb_goal_pct, sdb_goal_pct, wosb_goal_pct, hubzone_goal_pct, sdvosb_goal_pct "
        "FROM cpmp_small_business_plan WHERE contract_id = %s ORDER BY created_at DESC LIMIT 1",
        (contract_id,),
    ).fetchone()

    sb_goal = plan_row["sb_goal_pct"] if plan_row else 0.0
    sdb_goal = plan_row["sdb_goal_pct"] if plan_row else 0.0
    wosb_goal = plan_row["wosb_goal_pct"] if plan_row else 0.0
    hubzone_goal = plan_row["hubzone_goal_pct"] if plan_row else 0.0
    sdvosb_goal = plan_row["sdvosb_goal_pct"] if plan_row else 0.0

    sdb_dollars = category_dollars.get("sdb", 0.0)
    wosb_dollars = category_dollars.get("wosb", 0.0)
    hubzone_dollars = category_dollars.get("hubzone", 0.0)
    sdvosb_dollars = category_dollars.get("sdvosb", 0.0)

    # Determine compliance (all categories meeting goals)
    compliant = 1
    for goal, actual in [
        (sb_goal, _pct(sb_dollars)),
        (sdb_goal, _pct(sdb_dollars)),
        (wosb_goal, _pct(wosb_dollars)),
        (hubzone_goal, _pct(hubzone_dollars)),
        (sdvosb_goal, _pct(sdvosb_dollars)),
    ]:
        if goal > 0 and actual < goal:
            compliant = 0
            break

    report_id = _uuid()
    conn.execute(
        "INSERT INTO cpmp_small_business_plan "
        "(id, contract_id, reporting_period, report_type, total_subcontract_dollars, "
        "sb_goal_pct, sb_actual_pct, sb_actual_dollars, "
        "sdb_goal_pct, sdb_actual_pct, sdb_actual_dollars, "
        "wosb_goal_pct, wosb_actual_pct, wosb_actual_dollars, "
        "hubzone_goal_pct, hubzone_actual_pct, hubzone_actual_dollars, "
        "sdvosb_goal_pct, sdvosb_actual_pct, sdvosb_actual_dollars, "
        "compliant, status, notes, metadata, created_at, updated_at, classification) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            report_id,
            contract_id,
            reporting_period,
            report_type,
            total_dollars,
            sb_goal,
            _pct(sb_dollars),
            sb_dollars,
            sdb_goal,
            _pct(sdb_dollars),
            sdb_dollars,
            wosb_goal,
            _pct(wosb_dollars),
            wosb_dollars,
            hubzone_goal,
            _pct(hubzone_dollars),
            hubzone_dollars,
            sdvosb_goal,
            _pct(sdvosb_dollars),
            sdvosb_dollars,
            compliant,
            "draft",
            None,
            "{}",
            _now(),
            _now(),
            "CUI // SP-CTI",
        ),
    )

    _audit(
        conn,
        "create_sb_report",
        f"Created {report_type.upper()} report for contract {contract_id}, period {reporting_period}",
    )
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "report_id": report_id,
        "report_type": report_type,
        "reporting_period": reporting_period,
        "total_subcontract_dollars": total_dollars,
        "compliant": compliant == 1,
    }


def list_sb_reports(contract_id):
    """List ISR/SSR reports for a contract.

    Args:
        contract_id: Parent contract UUID.

    Returns:
        Dict with status, total count, and reports list.
    """
    conn = _get_db()
    # Handle both schema versions: reporting_period or period_start
    try:
        rows = conn.execute(
            "SELECT * FROM cpmp_small_business_plan WHERE contract_id = %s "
            "ORDER BY reporting_period DESC, created_at DESC",
            (contract_id,),
        ).fetchall()
    except Exception:
        rows = conn.execute(
            "SELECT * FROM cpmp_small_business_plan WHERE contract_id = %s ORDER BY period_start DESC, created_at DESC",
            (contract_id,),
        ).fetchall()
    conn.close()
    return {"status": "ok", "total": len(rows), "reports": [dict(r) for r in rows]}


# ── Noncompliance Detection ─────────────────────────────────────────


def _isr_ssr_applicability(conn, contract_id):
    """Whether FAR 52.219-9(d) ISR/SSR reporting attaches to this contract at all.

    Returns a dict with ``state`` ('required' | 'not_required' |
    'undetermined'), a one-clause ``reason``, and the ``total_value`` the
    decision was made on.

    Checks 1-3 in detect_noncompliance are each scoped to the rows they can be
    true of: flow-down and cybersecurity walk the contract's ACTIVE
    subcontractors, and CMMC is additionally gated on subcontract_value. Check 4
    had no gate of any kind — it asserted a HIGH FAR 52.219-9(d) violation from
    the mere ABSENCE of a cpmp_small_business_plan row, on every contract,
    forever. That asymmetry with its three siblings is the tell.

    Measured on the live board 2026-08-13: cpmp_small_business_plan held 0 rows
    platform-wide, so ``if not latest_report`` was true for every contract and
    the board carried 7 identical HIGH '[SUBCON] <contract>: ISR/SSR' cards —
    two of them for contracts with ZERO subcontractors, and all of them for
    contracts whose total_value is 0. Every one was false, and each dispatched
    an autonomous session to 'File the outstanding ISR/SSR in eSRS', which is
    not an action this platform can take. An applicability check with no
    applicability gate does not report a 100% violation rate; it reports
    nothing, 100% of the time.

    'undetermined' is deliberately NOT folded into either other state.
    create_contract() defaults total_value to 0.0 exactly as it defaults
    contract_number to '' — so a 0 means 'nobody filled this in' far more often
    than it means 'this contract is worth nothing'. Reading 0 as
    below-threshold would silence a genuine obligation on an unpopulated
    contract: the same defect with the sign flipped, and no longer visible.
    The caller raises it as its own MEDIUM finding instead, which names an
    action that IS available — populate the contract value.
    """
    row = conn.execute(
        "SELECT total_value FROM cpmp_contracts WHERE id = %s", (contract_id,)
    ).fetchone()
    if row is None:
        return {"state": "undetermined", "reason": "contract not found", "total_value": None}

    value = dict(row).get("total_value")
    if value is None or value <= 0:
        return {
            "state": "undetermined",
            "reason": "contract total_value is not populated",
            "total_value": value,
        }
    if value < _SB_PLAN_THRESHOLD:
        return {
            "state": "not_required",
            "reason": (
                f"contract value ${value:,.2f} is below the FAR 19.702(a)(1) "
                f"subcontracting plan threshold of ${_SB_PLAN_THRESHOLD:,.2f}"
            ),
            "total_value": value,
        }
    return {
        "state": "required",
        "reason": (
            f"contract value ${value:,.2f} meets the FAR 19.702(a)(1) "
            f"subcontracting plan threshold of ${_SB_PLAN_THRESHOLD:,.2f}"
        ),
        "total_value": value,
    }


def detect_noncompliance(contract_id):
    """Detect all types of noncompliance for a contract's subcontractors.

    Checks:
        1. Flow-down: Active subs with flow_down_complete = 0.
        2. Cybersecurity: Active subs above threshold with cybersecurity_compliant = 0.
        3. CMMC: Active subs with no CMMC level when contract requires it.
        4. ISR/SSR currency: No report within the last reporting window.

    Args:
        contract_id: Parent contract UUID.

    Returns:
        Dict with categorized noncompliance findings.
    """
    findings = []

    # 1. Flow-down noncompliance
    flowdown = check_flowdown(contract_id)
    for sub in flowdown.get("non_compliant", []):
        findings.append(
            {
                "category": "flowdown",
                "severity": "high" if (sub.get("subcontract_value") or 0) > _CYBER_THRESHOLD else "medium",
                "sub_id": sub["id"],
                "company_name": sub["company_name"],
                "description": f"Flow-down clauses incomplete for {sub['company_name']}",
                "subcontract_value": sub.get("subcontract_value", 0.0),
            }
        )

    # 2. Cybersecurity noncompliance
    cyber = check_cybersecurity(contract_id)
    for sub in cyber.get("non_compliant", []):
        findings.append(
            {
                "category": "cybersecurity",
                "severity": "critical",
                "sub_id": sub["id"],
                "company_name": sub["company_name"],
                "description": f"Cybersecurity non-compliant: {sub['company_name']} "
                f"(value: ${sub.get('subcontract_value', 0):,.2f})",
                "subcontract_value": sub.get("subcontract_value", 0.0),
            }
        )

    # 3. CMMC noncompliance — subs without CMMC level
    conn = _get_db()
    cmmc_rows = conn.execute(
        "SELECT id, company_name, subcontract_value, cmmc_level "
        "FROM cpmp_subcontractors "
        "WHERE contract_id = %s AND status = 'active' AND cmmc_level IS NULL "
        "AND subcontract_value > %s "
        "ORDER BY subcontract_value DESC",
        (contract_id, _CYBER_THRESHOLD),
    ).fetchall()
    for row in cmmc_rows:
        findings.append(
            {
                "category": "cmmc",
                "severity": "high",
                "sub_id": row["id"],
                "company_name": row["company_name"],
                "description": f"CMMC level not established for {row['company_name']}",
                "subcontract_value": row["subcontract_value"] or 0.0,
            }
        )

    # 4. ISR/SSR currency — but only where FAR 52.219-9(d) actually attaches.
    # See _isr_ssr_applicability: an absent report is only evidence of a missed
    # filing on a contract that owed a filing in the first place.
    applicability = _isr_ssr_applicability(conn, contract_id)

    latest_report = None
    if applicability["state"] == "required":
        # Note: DB may have reporting_period+report_type or period_start+period_end depending on init version
        try:
            latest_report = conn.execute(
                "SELECT created_at, reporting_period, report_type FROM cpmp_small_business_plan "
                "WHERE contract_id = %s ORDER BY created_at DESC LIMIT 1",
                (contract_id,),
            ).fetchone()
        except Exception:
            # Fallback for older schema with period_start/period_end
            latest_report = conn.execute(
                "SELECT created_at, period_start AS reporting_period FROM cpmp_small_business_plan "
                "WHERE contract_id = %s ORDER BY created_at DESC LIMIT 1",
                (contract_id,),
            ).fetchone()
    conn.close()

    if applicability["state"] == "undetermined":
        findings.append(
            {
                # A DIFFERENT category from 'isr_ssr': this is a data-quality gap
                # in our own record, not a reporting violation by the prime, and
                # the two must not be counted together.
                "category": "isr_ssr_applicability",
                # MEDIUM on purpose. cpmp_monitor files kanban cards for
                # high/critical only, so an unpopulated contract value stays
                # visible on the CPMP dashboard without dispatching a session to
                # file a report that may not be owed.
                "severity": "medium",
                "sub_id": None,
                "company_name": None,
                "description": (
                    f"ISR/SSR applicability cannot be determined: {applicability['reason']}. "
                    "Populate the contract value to enable the FAR 52.219-9(d) currency check."
                ),
                "subcontract_value": None,
            }
        )
    elif applicability["state"] != "required":
        pass  # Below the FAR 19.702(a)(1) threshold: no plan, so no report is owed.
    elif not latest_report:
        findings.append(
            {
                "category": "isr_ssr",
                "severity": "high",
                "sub_id": None,
                "company_name": None,
                "description": (
                    "No ISR/SSR report has been filed for this contract "
                    f"({applicability['reason']})"
                ),
                "subcontract_value": None,
            }
        )
    else:
        try:
            created = datetime.fromisoformat(latest_report["created_at"].replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - created.replace(tzinfo=timezone.utc)).days
            if age_days > _ISR_SSR_MAX_AGE_DAYS:
                period = latest_report["reporting_period"] if "reporting_period" in latest_report.keys() else "N/A"
                report_type = dict(latest_report).get("report_type", "ISR/SSR")
                findings.append(
                    {
                        "category": "isr_ssr",
                        "severity": "medium",
                        "sub_id": None,
                        "company_name": None,
                        "description": f"Latest ISR/SSR report is {age_days} days old "
                        f"(threshold: {_ISR_SSR_MAX_AGE_DAYS} days). "
                        f"Period: {period}, "
                        f"type: {report_type.upper() if report_type else 'ISR/SSR'}",
                        "subcontract_value": None,
                    }
                )
        except (ValueError, TypeError):
            pass

    # Summarize
    severity_counts = {}
    for f in findings:
        sev = f["severity"]
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    category_counts = {}
    for f in findings:
        cat = f["category"]
        category_counts[cat] = category_counts.get(cat, 0) + 1

    return {
        "status": "ok",
        "contract_id": contract_id,
        "total_findings": len(findings),
        "severity_counts": severity_counts,
        "category_counts": category_counts,
        "compliant": len(findings) == 0,
        # Reported rather than implied. A gate that suppresses a finding must say
        # so out loud: 'not_required' and 'undetermined' both produce no
        # isr_ssr finding, and without this key the two are indistinguishable
        # from a contract that is fully current on its filings.
        "isr_ssr_applicability": applicability,
        "findings": findings,
    }


# ── CLI ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="ICDEV™ GovProposal Subcontractor Tracker (Phase 60, FAR 52.219-9)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--create", action="store_true", help="Create a subcontractor")
    group.add_argument("--update", action="store_true", help="Update a subcontractor")
    group.add_argument("--list", action="store_true", help="List subcontractors for a contract")
    group.add_argument("--sb-compliance", action="store_true", help="Compute SB compliance")
    group.add_argument("--check-flowdown", action="store_true", help="Check flow-down compliance")
    group.add_argument("--check-cyber", action="store_true", help="Check cybersecurity compliance")
    group.add_argument("--create-report", action="store_true", help="Create ISR/SSR report")
    group.add_argument("--list-reports", action="store_true", help="List ISR/SSR reports")
    group.add_argument("--detect-noncompliance", action="store_true", help="Detect all noncompliance")

    parser.add_argument("--contract-id", help="Contract UUID")
    parser.add_argument("--sub-id", help="Subcontractor UUID")
    parser.add_argument("--data", help="JSON data for create/update")
    parser.add_argument("--business-size", help="Filter by business_size (e.g. small, sdb, wosb)")
    parser.add_argument("--period", help="Reporting period (e.g. 2025-Q1)")
    parser.add_argument("--type", default="isr", help="Report type: isr or ssr")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()
    data = json.loads(args.data) if args.data else {}

    if args.create:
        if not args.contract_id:
            result = {"status": "error", "message": "--contract-id is required for --create"}
        else:
            result = create_subcontractor(args.contract_id, data)
    elif args.update:
        if not args.sub_id:
            result = {"status": "error", "message": "--sub-id is required for --update"}
        else:
            result = update_subcontractor(args.sub_id, data)
    elif args.list:
        if not args.contract_id:
            result = {"status": "error", "message": "--contract-id is required for --list"}
        else:
            result = list_subcontractors(args.contract_id, business_size=args.business_size)
    elif args.sb_compliance:
        if not args.contract_id:
            result = {"status": "error", "message": "--contract-id is required for --sb-compliance"}
        else:
            result = compute_sb_compliance(args.contract_id)
    elif args.check_flowdown:
        if not args.contract_id:
            result = {"status": "error", "message": "--contract-id is required for --check-flowdown"}
        else:
            result = check_flowdown(args.contract_id)
    elif args.check_cyber:
        if not args.contract_id:
            result = {"status": "error", "message": "--contract-id is required for --check-cyber"}
        else:
            result = check_cybersecurity(args.contract_id)
    elif args.create_report:
        if not args.contract_id:
            result = {"status": "error", "message": "--contract-id is required for --create-report"}
        elif not args.period:
            result = {"status": "error", "message": "--period is required for --create-report"}
        else:
            result = create_sb_report(args.contract_id, args.period, report_type=args.type)
    elif args.list_reports:
        if not args.contract_id:
            result = {"status": "error", "message": "--contract-id is required for --list-reports"}
        else:
            result = list_sb_reports(args.contract_id)
    elif args.detect_noncompliance:
        if not args.contract_id:
            result = {"status": "error", "message": "--contract-id is required for --detect-noncompliance"}
        else:
            result = detect_noncompliance(args.contract_id)
    else:
        result = {"status": "error", "message": "Unknown command"}

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
