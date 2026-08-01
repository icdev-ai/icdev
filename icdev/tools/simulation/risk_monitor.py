#!/usr/bin/env python3
# CUI // SP-CTI
"""Live Risk Monitor — continuous composite and CPARS risk scoring.

Implements the exact formulas from the Digital Transformation article:

Composite_Risk = (0.35 x Overdue_Tasks) + (0.25 x Resource_Utilization)
               + (0.25 x Dependency_Violations) + (0.15 x Compliance_Gaps)

CPARS_Risk     = (0.35 x Overdue_CDRLs) + (0.25 x Rejected_Deliverables)
               + (0.25 x Non_Compliant_Items) + (0.15 x Late_Submissions)

Runs on-demand or as a periodic daemon (via Genesis scheduler).
Emits alerts when thresholds are crossed.

Usage:
    python tools/simulation/risk_monitor.py --project proj-123 --json
    python tools/simulation/risk_monitor.py --project proj-123 --contract c-456 --json
    python tools/simulation/risk_monitor.py --daemon --interval 300 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.simulation.risk_monitor")

# ---------------------------------------------------------------------------
# Composite Risk Formula (Article §Live Risk Correlation)
# ---------------------------------------------------------------------------

COMPOSITE_WEIGHTS = {
    "overdue_tasks": 0.35,
    "resource_utilization": 0.25,
    "dependency_violations": 0.25,
    "compliance_gaps": 0.15,
}

# Thresholds for alert severity
COMPOSITE_THRESHOLDS = {
    "green": 0.30,  # ≤ 30% = healthy
    "yellow": 0.55,  # ≤ 55% = watch
    "orange": 0.75,  # ≤ 75% = elevated
    # > 75% = red / critical
}


def calculate_composite_risk(project_id, db_path=None):
    """Calculate composite program risk score.

    Returns:
        {
            "project_id": str,
            "composite_risk": float (0.0-1.0),
            "severity": "GREEN"|"YELLOW"|"ORANGE"|"RED",
            "components": {
                "overdue_tasks": {"raw": int, "normalized": float, "weight": 0.35},
                "resource_utilization": {"raw": float, "normalized": float, "weight": 0.25},
                "dependency_violations": {"raw": int, "normalized": float, "weight": 0.25},
                "compliance_gaps": {"raw": int, "normalized": float, "weight": 0.15},
            },
            "formula": str,
            "alerts": [str],
            "calculated_at": str,
        }
    """
    conn = get_connection(db_path)
    components = {}
    alerts = []

    # 1. Overdue Tasks — stories past due / total active stories
    total_active = _q(
        conn,
        "SELECT COUNT(*) FROM safe_decomposition WHERE project_id = ? AND status IN ('in_progress', 'planned')",
        (project_id,),
    )
    overdue = _q(
        conn,
        "SELECT COUNT(*) FROM safe_decomposition "
        "WHERE project_id = ? AND status = 'in_progress' "
        "AND due_date < datetime('now')",
        (project_id,),
    )
    overdue_pct = overdue / total_active if total_active > 0 else 0.0
    components["overdue_tasks"] = {
        "raw": overdue,
        "total": total_active,
        "normalized": round(min(1.0, overdue_pct), 4),
        "weight": COMPOSITE_WEIGHTS["overdue_tasks"],
    }
    if overdue_pct > 0.3:
        alerts.append(f"HIGH: {overdue}/{total_active} tasks overdue ({overdue_pct:.0%})")

    # 2. Resource Utilization — active work items / (FTE × capacity)
    fte_count = _q(conn, "SELECT COUNT(*) FROM project_team_members WHERE project_id = ?", (project_id,))
    if fte_count == 0:
        story_count = _q(
            conn, "SELECT COUNT(*) FROM safe_decomposition WHERE project_id = ? AND level = 'story'", (project_id,)
        )
        fte_count = max(1, story_count // 15) if story_count > 0 else 5
    capacity = fte_count * 3  # 3 stories per FTE at a time
    active_items = _q(
        conn, "SELECT COUNT(*) FROM safe_decomposition WHERE project_id = ? AND status = 'in_progress'", (project_id,)
    )
    utilization = min(1.0, active_items / capacity if capacity > 0 else 0.0)
    components["resource_utilization"] = {
        "raw": round(utilization, 4),
        "fte": fte_count,
        "active_items": active_items,
        "capacity": capacity,
        "normalized": round(utilization, 4),
        "weight": COMPOSITE_WEIGHTS["resource_utilization"],
    }
    if utilization > 0.85:
        alerts.append(f"HIGH: Resource utilization at {utilization:.0%} — contention likely")

    # 3. Dependency Violations — broken/missing deps / total deps
    total_deps = _q(conn, "SELECT COUNT(*) FROM sysml_relationships WHERE project_id = ?", (project_id,))
    # Violations: orphan references (target doesn't exist in elements)
    violations = _q(
        conn,
        "SELECT COUNT(*) FROM sysml_relationships r "
        "WHERE r.project_id = ? AND NOT EXISTS "
        "(SELECT 1 FROM sysml_elements e WHERE e.id = r.target_id "
        "AND e.project_id = r.project_id)",
        (project_id,),
    )
    dep_pct = violations / total_deps if total_deps > 0 else 0.0
    components["dependency_violations"] = {
        "raw": violations,
        "total": total_deps,
        "normalized": round(min(1.0, dep_pct), 4),
        "weight": COMPOSITE_WEIGHTS["dependency_violations"],
    }
    if violations > 0:
        alerts.append(f"WARN: {violations} dependency violations detected")

    # 4. Compliance Gaps — unimplemented controls / total controls
    total_controls = _q(conn, "SELECT COUNT(*) FROM project_controls WHERE project_id = ?", (project_id,))
    implemented = _q(
        conn,
        "SELECT COUNT(*) FROM project_controls WHERE project_id = ? AND implementation_status = 'implemented'",
        (project_id,),
    )
    gap_count = max(0, total_controls - implemented)
    gap_pct = gap_count / total_controls if total_controls > 0 else 0.0
    components["compliance_gaps"] = {
        "raw": gap_count,
        "total": total_controls,
        "normalized": round(min(1.0, gap_pct), 4),
        "weight": COMPOSITE_WEIGHTS["compliance_gaps"],
    }
    if gap_pct > 0.2:
        alerts.append(f"WARN: {gap_count}/{total_controls} compliance controls not implemented ({gap_pct:.0%})")

    conn.close()

    # Calculate weighted composite
    composite = sum(components[k]["normalized"] * components[k]["weight"] for k in COMPOSITE_WEIGHTS)
    composite = round(min(1.0, composite), 4)

    # Determine severity
    if composite <= COMPOSITE_THRESHOLDS["green"]:
        severity = "GREEN"
    elif composite <= COMPOSITE_THRESHOLDS["yellow"]:
        severity = "YELLOW"
    elif composite <= COMPOSITE_THRESHOLDS["orange"]:
        severity = "ORANGE"
    else:
        severity = "RED"

    formula = (
        f"({COMPOSITE_WEIGHTS['overdue_tasks']} x {components['overdue_tasks']['normalized']:.3f}) + "
        f"({COMPOSITE_WEIGHTS['resource_utilization']} x {components['resource_utilization']['normalized']:.3f}) + "
        f"({COMPOSITE_WEIGHTS['dependency_violations']} x {components['dependency_violations']['normalized']:.3f}) + "
        f"({COMPOSITE_WEIGHTS['compliance_gaps']} x {components['compliance_gaps']['normalized']:.3f}) "
        f"= {composite:.4f}"
    )

    return {
        "project_id": project_id,
        "composite_risk": composite,
        "severity": severity,
        "components": components,
        "formula": formula,
        "alerts": alerts,
        "calculated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# CPARS Risk Formula (Article §CPARS Risk Scoring)
# ---------------------------------------------------------------------------

CPARS_WEIGHTS = {
    "overdue_cdrls": 0.35,
    "rejected_deliverables": 0.25,
    "non_compliant_items": 0.25,
    "late_submissions": 0.15,
}

CPARS_RATING_MAP = {
    "exceptional": (0.0, 0.10),
    "very_good": (0.10, 0.25),
    "satisfactory": (0.25, 0.45),
    "marginal": (0.45, 0.65),
    "unsatisfactory": (0.65, 1.0),
}


def calculate_cpars_risk(contract_id, db_path=None):
    """Calculate CPARS risk score for a contract.

    Returns:
        {
            "contract_id": str,
            "cpars_risk": float (0.0-1.0),
            "projected_rating": str,
            "components": {...},
            "formula": str,
            "alerts": [str],
            "calculated_at": str,
        }
    """
    conn = get_connection(db_path)
    components = {}
    alerts = []

    # 1. Overdue CDRLs
    total_cdrls = _q(conn, "SELECT COUNT(*) FROM cpmp_deliverables WHERE contract_id = ?", (contract_id,))
    overdue_cdrls = _q(
        conn, "SELECT COUNT(*) FROM cpmp_deliverables WHERE contract_id = ? AND status = 'overdue'", (contract_id,)
    )
    overdue_pct = overdue_cdrls / total_cdrls if total_cdrls > 0 else 0.0
    components["overdue_cdrls"] = {
        "raw": overdue_cdrls,
        "total": total_cdrls,
        "normalized": round(min(1.0, overdue_pct), 4),
        "weight": CPARS_WEIGHTS["overdue_cdrls"],
    }
    if overdue_cdrls > 0:
        alerts.append(f"CRITICAL: {overdue_cdrls} CDRLs overdue")

    # 2. Rejected Deliverables
    total_submitted = _q(
        conn,
        "SELECT COUNT(*) FROM cpmp_deliverables "
        "WHERE contract_id = ? AND status IN "
        "('submitted', 'accepted', 'rejected')",
        (contract_id,),
    )
    rejected = _q(
        conn, "SELECT COUNT(*) FROM cpmp_deliverables WHERE contract_id = ? AND status = 'rejected'", (contract_id,)
    )
    rejected_pct = rejected / total_submitted if total_submitted > 0 else 0.0
    components["rejected_deliverables"] = {
        "raw": rejected,
        "total": total_submitted,
        "normalized": round(min(1.0, rejected_pct), 4),
        "weight": CPARS_WEIGHTS["rejected_deliverables"],
    }
    if rejected > 0:
        alerts.append(f"WARN: {rejected} deliverables rejected")

    # 3. Non-Compliant Items
    total_items = _q(conn, "SELECT COUNT(*) FROM cpmp_compliance_items WHERE contract_id = ?", (contract_id,))
    non_compliant = _q(
        conn,
        "SELECT COUNT(*) FROM cpmp_compliance_items WHERE contract_id = ? AND status = 'non_compliant'",
        (contract_id,),
    )
    nc_pct = non_compliant / total_items if total_items > 0 else 0.0
    components["non_compliant_items"] = {
        "raw": non_compliant,
        "total": total_items,
        "normalized": round(min(1.0, nc_pct), 4),
        "weight": CPARS_WEIGHTS["non_compliant_items"],
    }

    # 4. Late Submissions — deliverables submitted after due date
    late = _q(
        conn,
        "SELECT COUNT(*) FROM cpmp_deliverables "
        "WHERE contract_id = ? AND submitted_at > due_date "
        "AND submitted_at IS NOT NULL AND due_date IS NOT NULL",
        (contract_id,),
    )
    late_pct = late / total_cdrls if total_cdrls > 0 else 0.0
    components["late_submissions"] = {
        "raw": late,
        "total": total_cdrls,
        "normalized": round(min(1.0, late_pct), 4),
        "weight": CPARS_WEIGHTS["late_submissions"],
    }
    if late > 2:
        alerts.append(f"WARN: {late} late submissions detected")

    conn.close()

    # Calculate weighted CPARS risk
    cpars_risk = sum(components[k]["normalized"] * components[k]["weight"] for k in CPARS_WEIGHTS)
    cpars_risk = round(min(1.0, cpars_risk), 4)

    # Project rating
    projected_rating = "satisfactory"
    for rating, (low, high) in CPARS_RATING_MAP.items():
        if low <= cpars_risk < high:
            projected_rating = rating
            break

    formula = (
        f"({CPARS_WEIGHTS['overdue_cdrls']} x {components['overdue_cdrls']['normalized']:.3f}) + "
        f"({CPARS_WEIGHTS['rejected_deliverables']} x {components['rejected_deliverables']['normalized']:.3f}) + "
        f"({CPARS_WEIGHTS['non_compliant_items']} x {components['non_compliant_items']['normalized']:.3f}) + "
        f"({CPARS_WEIGHTS['late_submissions']} x {components['late_submissions']['normalized']:.3f}) "
        f"= {cpars_risk:.4f}"
    )

    return {
        "contract_id": contract_id,
        "cpars_risk": cpars_risk,
        "projected_rating": projected_rating,
        "components": components,
        "formula": formula,
        "alerts": alerts,
        "calculated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Risk History persistence
# ---------------------------------------------------------------------------


def _persist_risk_snapshot(result, risk_type, db_path=None):
    """Save a risk calculation to history for trend tracking."""
    conn = get_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO risk_monitor_history
               (id, project_id, risk_type, risk_score, severity,
                components, alerts, calculated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                str(uuid4()),
                result.get("project_id") or result.get("contract_id"),
                risk_type,
                result.get("composite_risk") or result.get("cpars_risk"),
                result.get("severity") or result.get("projected_rating"),
                json.dumps(result.get("components", {})),
                json.dumps(result.get("alerts", [])),
                result.get("calculated_at"),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # Table may not exist yet — non-blocking
        logger.warning(
            "_persist_risk_snapshot: best-effort INSERT into risk_monitor_history failed (non-blocking): %s",
            exc,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _q(conn, sql, params=()):
    """Safe single-value query, returns 0 on any error."""
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Live Risk Monitor — composite + CPARS scoring")
    parser.add_argument("--project-id", dest="project_id", help="Project ID for composite risk")
    parser.add_argument("--project", dest="project_id", help=argparse.SUPPRESS)  # backward compat — use --project-id
    parser.add_argument("--contract", help="Contract ID for CPARS risk")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--gate", action="store_true", help="Exit 1 if RED/unsatisfactory")
    parser.add_argument("--persist", action="store_true", help="Save snapshot to risk_monitor_history")
    args = parser.parse_args()

    results = []

    if args.project_id:
        r = calculate_composite_risk(args.project_id)
        if args.persist:
            _persist_risk_snapshot(r, "composite")
        results.append(r)

    if args.contract:
        r = calculate_cpars_risk(args.contract)
        if args.persist:
            _persist_risk_snapshot(r, "cpars")
        results.append(r)

    if not results:
        parser.print_help()
        return

    if args.json:
        out = results[0] if len(results) == 1 else results
        print(json.dumps(out, indent=2, default=str))
    else:
        for r in results:
            if "composite_risk" in r:
                print(f"Project:    {r['project_id']}")
                print(f"Composite:  {r['composite_risk']:.4f}")
                print(f"Severity:   {r['severity']}")
                print(f"Formula:    {r['formula']}")
            elif "cpars_risk" in r:
                print(f"Contract:   {r['contract_id']}")
                print(f"CPARS Risk: {r['cpars_risk']:.4f}")
                print(f"Rating:     {r['projected_rating']}")
                print(f"Formula:    {r['formula']}")
            for a in r.get("alerts", []):
                print(f"  ALERT: {a}")

    if args.gate:
        for r in results:
            if r.get("severity") == "RED":
                sys.exit(1)
            if r.get("projected_rating") == "unsatisfactory":
                sys.exit(1)


if __name__ == "__main__":
    main()
