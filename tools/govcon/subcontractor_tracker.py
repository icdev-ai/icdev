# CUI // SP-CTI
# ICDEV GovProposal — Subcontractor Tracker (Phase 60, D-CPMP-1)
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
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

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


# ── Helpers ──────────────────────────────────────────────────────────

def _get_db():
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uuid():
    return str(uuid.uuid4())


def _audit(conn, action, details="", actor="subcontractor_tracker"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (id, timestamp, event_type, actor, action, details, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_uuid(), _now(), "cpmp.subcontractor_tracker", actor, action, details, "cpmp"),
        )
    except Exception:
        pass


def _record_status_change(conn, entity_type, entity_id, old_status, new_status, changed_by=None, reason=None):
    """Record status change in cpmp_status_history (append-only, NIST AU-2)."""
    conn.execute(
        "INSERT INTO cpmp_status_history (entity_type, entity_id, old_status, new_status, changed_by, reason) "
        "VALUES (?, ?, ?, ?, ?, ?)",
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
    if not conn.execute("SELECT id FROM cpmp_contracts WHERE id = ?", (contract_id,)).fetchone():
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    sub_id = _uuid()
    conn.execute(
        "INSERT INTO cpmp_subcontractors "
        "(id, contract_id, company_name, cage_code, uei, business_size, "
        "subcontract_value, performance_rating, "
        "flow_down_complete, cybersecurity_compliant, cmmc_level, isr_ssr_current, "
        "status, notes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sub_id,
            contract_id,
            data.get("company_name", "Unknown"),
            data.get("cage_code"),
            data.get("uei"),
            data.get("business_size", "large"),
            data.get("subcontract_value", 0.0),
            data.get("performance_rating"),
            data.get("flow_down_complete", 0),
            data.get("cybersecurity_compliant", 0),
            data.get("cmmc_level"),
            data.get("isr_ssr_current", 0),
            data.get("status", "active"),
            data.get("notes"),
            _now(),
            _now(),
        ),
    )
    _record_status_change(conn, "subcontractor", sub_id, None, data.get("status", "active"),
                          "system", "Subcontractor created")
    _audit(conn, "create_subcontractor",
           f"Created subcontractor {data.get('company_name', sub_id)} on contract {contract_id}")
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
    row = conn.execute("SELECT id, status FROM cpmp_subcontractors WHERE id = ?", (sub_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Subcontractor {sub_id} not found"}

    old_status = row["status"]

    updatable = [
        "company_name", "cage_code", "uei", "business_size",
        "subcontract_value", "performance_rating",
        "flow_down_complete", "cybersecurity_compliant", "cmmc_level", "isr_ssr_current",
        "status", "notes",
    ]
    sets = []
    params = []
    for field in updatable:
        if field in data:
            sets.append(f"{field} = ?")
            params.append(data[field])

    if not sets:
        conn.close()
        return {"status": "error", "message": "No updatable fields provided"}

    sets.append("updated_at = ?")
    params.append(_now())
    params.append(sub_id)

    conn.execute(f"UPDATE cpmp_subcontractors SET {', '.join(sets)} WHERE id = ?", params)

    # Record status change if status was modified
    if "status" in data and data["status"] != old_status:
        _record_status_change(conn, "subcontractor", sub_id, old_status, data["status"],
                              data.get("changed_by"), data.get("reason"))

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
        "SELECT business_size, subcontract_value FROM cpmp_subcontractors "
        "WHERE contract_id = ? AND status = 'active'",
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
        "SELECT * FROM cpmp_small_business_plan WHERE contract_id = ? ORDER BY created_at DESC LIMIT 1",
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
        "WHERE contract_id = ? AND status = 'active' AND flow_down_complete = 0 "
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
    threshold = _CFG.get("negative_events", {}).get("auto_detect", {}).get(
        "flowdown_failure", {}
    )
    # Extract numeric threshold from trigger string if available, else use default
    cyber_threshold = _CYBER_THRESHOLD

    conn = _get_db()
    rows = conn.execute(
        "SELECT id, company_name, cage_code, uei, business_size, subcontract_value, "
        "cybersecurity_compliant, cmmc_level, status "
        "FROM cpmp_subcontractors "
        "WHERE contract_id = ? AND status = 'active' "
        "AND subcontract_value > ? AND cybersecurity_compliant = 0 "
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
    if not conn.execute("SELECT id FROM cpmp_contracts WHERE id = ?", (contract_id,)).fetchone():
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    # Compute current actuals from subcontractor data
    rows = conn.execute(
        "SELECT business_size, subcontract_value FROM cpmp_subcontractors "
        "WHERE contract_id = ? AND status = 'active'",
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
        "FROM cpmp_small_business_plan WHERE contract_id = ? ORDER BY created_at DESC LIMIT 1",
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
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            report_id, contract_id, reporting_period, report_type, total_dollars,
            sb_goal, _pct(sb_dollars), sb_dollars,
            sdb_goal, _pct(sdb_dollars), sdb_dollars,
            wosb_goal, _pct(wosb_dollars), wosb_dollars,
            hubzone_goal, _pct(hubzone_dollars), hubzone_dollars,
            sdvosb_goal, _pct(sdvosb_dollars), sdvosb_dollars,
            compliant, "draft", None, "{}",
            _now(), _now(), "CUI // SP-CTI",
        ),
    )

    _audit(conn, "create_sb_report",
           f"Created {report_type.upper()} report for contract {contract_id}, period {reporting_period}")
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
            "SELECT * FROM cpmp_small_business_plan WHERE contract_id = ? "
            "ORDER BY reporting_period DESC, created_at DESC",
            (contract_id,),
        ).fetchall()
    except Exception:
        rows = conn.execute(
            "SELECT * FROM cpmp_small_business_plan WHERE contract_id = ? "
            "ORDER BY period_start DESC, created_at DESC",
            (contract_id,),
        ).fetchall()
    conn.close()
    return {"status": "ok", "total": len(rows), "reports": [dict(r) for r in rows]}


# ── Noncompliance Detection ─────────────────────────────────────────

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
        findings.append({
            "category": "flowdown",
            "severity": "high" if (sub.get("subcontract_value") or 0) > _CYBER_THRESHOLD else "medium",
            "sub_id": sub["id"],
            "company_name": sub["company_name"],
            "description": f"Flow-down clauses incomplete for {sub['company_name']}",
            "subcontract_value": sub.get("subcontract_value", 0.0),
        })

    # 2. Cybersecurity noncompliance
    cyber = check_cybersecurity(contract_id)
    for sub in cyber.get("non_compliant", []):
        findings.append({
            "category": "cybersecurity",
            "severity": "critical",
            "sub_id": sub["id"],
            "company_name": sub["company_name"],
            "description": f"Cybersecurity non-compliant: {sub['company_name']} "
                           f"(value: ${sub.get('subcontract_value', 0):,.2f})",
            "subcontract_value": sub.get("subcontract_value", 0.0),
        })

    # 3. CMMC noncompliance — subs without CMMC level
    conn = _get_db()
    cmmc_rows = conn.execute(
        "SELECT id, company_name, subcontract_value, cmmc_level "
        "FROM cpmp_subcontractors "
        "WHERE contract_id = ? AND status = 'active' AND cmmc_level IS NULL "
        "AND subcontract_value > ? "
        "ORDER BY subcontract_value DESC",
        (contract_id, _CYBER_THRESHOLD),
    ).fetchall()
    for row in cmmc_rows:
        findings.append({
            "category": "cmmc",
            "severity": "high",
            "sub_id": row["id"],
            "company_name": row["company_name"],
            "description": f"CMMC level not established for {row['company_name']}",
            "subcontract_value": row["subcontract_value"] or 0.0,
        })

    # 4. ISR/SSR currency — check if there is a recent report
    # Note: DB may have reporting_period+report_type or period_start+period_end depending on init version
    try:
        latest_report = conn.execute(
            "SELECT created_at, reporting_period, report_type FROM cpmp_small_business_plan "
            "WHERE contract_id = ? ORDER BY created_at DESC LIMIT 1",
            (contract_id,),
        ).fetchone()
    except Exception:
        # Fallback for older schema with period_start/period_end
        latest_report = conn.execute(
            "SELECT created_at, period_start AS reporting_period FROM cpmp_small_business_plan "
            "WHERE contract_id = ? ORDER BY created_at DESC LIMIT 1",
            (contract_id,),
        ).fetchone()
    conn.close()

    if not latest_report:
        findings.append({
            "category": "isr_ssr",
            "severity": "high",
            "sub_id": None,
            "company_name": None,
            "description": "No ISR/SSR report has been filed for this contract",
            "subcontract_value": None,
        })
    else:
        try:
            created = datetime.fromisoformat(latest_report["created_at"].replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - created.replace(tzinfo=timezone.utc)).days
            if age_days > _ISR_SSR_MAX_AGE_DAYS:
                period = latest_report["reporting_period"] if "reporting_period" in latest_report.keys() else "N/A"
                report_type = dict(latest_report).get("report_type", "ISR/SSR")
                findings.append({
                    "category": "isr_ssr",
                    "severity": "medium",
                    "sub_id": None,
                    "company_name": None,
                    "description": f"Latest ISR/SSR report is {age_days} days old "
                                   f"(threshold: {_ISR_SSR_MAX_AGE_DAYS} days). "
                                   f"Period: {period}, "
                                   f"type: {report_type.upper() if report_type else 'ISR/SSR'}",
                    "subcontract_value": None,
                })
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
        "findings": findings,
    }


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ICDEV GovProposal Subcontractor Tracker (Phase 60, FAR 52.219-9)"
    )
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
