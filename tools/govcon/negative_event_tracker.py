# CUI // SP-CTI
# ICDEV GovProposal — Negative Event Tracker (Phase 60, D-CPMP-7)
# FY2026 NDAA event-based CPARS negative reporting with auto-detection.

"""
Negative Event Tracker — FY2026 NDAA event-based CPARS negative reporting.

Records, auto-detects, and scores negative performance events that affect
CPARS ratings. Auto-detection scans deliverables, EVM periods, quality
rejections, and subcontractor flowdown compliance. Penalty impacts are
computed from a configurable penalty table and feed into the CPARS
prediction engine.

All inserts to cpmp_negative_events are append-only (D6, NIST AU-2).
The corrective_action_status field IS updatable (tracks remediation
progress, not the event record itself).

Usage:
    python tools/govcon/negative_event_tracker.py --record --contract-id <id> --event-type delinquent_delivery --severity high --description "..." --json
    python tools/govcon/negative_event_tracker.py --auto-detect --contract-id <id> --json
    python tools/govcon/negative_event_tracker.py --impact --contract-id <id> --json
    python tools/govcon/negative_event_tracker.py --update-corrective --event-id <id> --status completed --json
    python tools/govcon/negative_event_tracker.py --list --contract-id <id> [--severity high] [--status open] --json
    python tools/govcon/negative_event_tracker.py --ndaa-check --contract-id <id> --json
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


# -- Config ----------------------------------------------------------------

def _load_config():
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f).get("cpmp", {})
    return {}


_CFG = _load_config()
_NEG_CFG = _CFG.get("negative_events", {})
_AUTO_CFG = _NEG_CFG.get("auto_detect", {})

PENALTY_TABLE = _NEG_CFG.get("penalty_table", {
    "delinquent_delivery": 0.05,
    "cost_overrun": 0.08,
    "quality_rejection": 0.06,
    "cybersecurity_breach": 0.10,
    "flowdown_failure": 0.04,
    "safety_violation": 0.12,
    "compliance_violation": 0.06,
    "cure_notice": 0.15,
    "show_cause": 0.20,
    "stop_work": 0.25,
    "termination_default": 0.50,
    "fraud_waste_abuse": 0.50,
})

CORRECTIVE_ACTION_DISCOUNT = _CFG.get("cpars", {}).get("corrective_action_discount", 0.50)

VALID_EVENT_TYPES = (
    "delinquent_delivery", "cost_overrun", "quality_rejection",
    "cybersecurity_breach", "flowdown_failure", "safety_violation",
    "compliance_violation", "cure_notice", "show_cause",
    "stop_work", "termination_default", "fraud_waste_abuse",
)

VALID_SEVERITIES = ("low", "medium", "high", "critical")

VALID_CORRECTIVE_STATUSES = ("open", "in_progress", "completed", "verified")


# -- Helpers ---------------------------------------------------------------

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


def _audit(conn, action, details="", actor="negative_event_tracker"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (id, timestamp, event_type, actor, action, details, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_uuid(), _now(), "cpmp.negative_event_tracker", actor, action, details, "cpmp"),
        )
    except Exception:
        pass


def _record_status_change(conn, entity_type, entity_id, old_status, new_status,
                          changed_by=None, reason=None):
    """Record status change in cpmp_status_history (append-only, NIST AU-2)."""
    conn.execute(
        "INSERT INTO cpmp_status_history (entity_type, entity_id, old_status, new_status, changed_by, reason) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (entity_type, entity_id, old_status, new_status, changed_by, reason),
    )


def _contract_filter(base_query, contract_id, params):
    """Append optional contract_id filter to a query."""
    if contract_id:
        base_query += " AND contract_id = ?"
        params.append(contract_id)
    return base_query, params


# -- Core Functions --------------------------------------------------------

def record_event(contract_id, event_type, severity, description, **kwargs):
    """
    Insert a negative event into cpmp_negative_events (append-only).

    Args:
        contract_id: Contract UUID.
        event_type: One of VALID_EVENT_TYPES.
        severity: One of VALID_SEVERITIES.
        description: Human-readable description of the event.
        **kwargs: corrective_action, detected_by, deliverable_id,
                  subcontractor_id, source_entity_type, source_entity_id.
                  If deliverable_id is provided, source_entity_type='deliverable'
                  and source_entity_id=deliverable_id. If subcontractor_id is
                  provided, source_entity_type='subcontractor' and
                  source_entity_id=subcontractor_id.

    Returns:
        dict with status and event_id.
    """
    if event_type not in VALID_EVENT_TYPES:
        return {"status": "error", "message": f"Invalid event_type: {event_type}. Valid: {list(VALID_EVENT_TYPES)}"}
    if severity not in VALID_SEVERITIES:
        return {"status": "error", "message": f"Invalid severity: {severity}. Valid: {list(VALID_SEVERITIES)}"}

    conn = _get_db()
    if not conn.execute("SELECT id FROM cpmp_contracts WHERE id = ?", (contract_id,)).fetchone():
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    cpars_impact = PENALTY_TABLE.get(event_type, 0.0)
    event_id = _uuid()
    now = _now()

    # Map deliverable_id/subcontractor_id to source_entity_type/source_entity_id
    source_entity_type = kwargs.get("source_entity_type")
    source_entity_id = kwargs.get("source_entity_id")
    if kwargs.get("deliverable_id") and not source_entity_type:
        source_entity_type = "deliverable"
        source_entity_id = kwargs["deliverable_id"]
    elif kwargs.get("subcontractor_id") and not source_entity_type:
        source_entity_type = "subcontractor"
        source_entity_id = kwargs["subcontractor_id"]

    detected_by = kwargs.get("detected_by", "manual")

    conn.execute(
        "INSERT INTO cpmp_negative_events "
        "(id, contract_id, event_type, severity, description, "
        "corrective_action, corrective_action_status, cpars_impact, "
        "detected_by, source_entity_type, source_entity_id, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event_id, contract_id, event_type, severity, description,
            kwargs.get("corrective_action"),
            "open",
            cpars_impact,
            detected_by,
            source_entity_type,
            source_entity_id,
            now,
            now,
        ),
    )

    _record_status_change(
        conn, "negative_event", event_id, None, "open",
        detected_by,
        f"Negative event recorded: {event_type} ({severity})",
    )
    _audit(conn, "record_event",
           f"Event {event_id}: {event_type}/{severity} on contract {contract_id}")
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "event_id": event_id,
        "event_type": event_type,
        "severity": severity,
        "cpars_impact": cpars_impact,
    }


def auto_detect_delinquent(contract_id=None):
    """
    Scan cpmp_deliverables for overdue items and create negative events.

    For each overdue deliverable not already tracked as a negative event,
    create a delinquent_delivery event.

    Returns:
        dict with count of new events created.
    """
    if not _AUTO_CFG.get("delinquent_delivery", {}).get("enabled", True):
        return {"status": "ok", "new_events": 0, "message": "delinquent_delivery auto-detect disabled"}

    conn = _get_db()
    now_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    query = (
        "SELECT d.id, d.contract_id, d.title, d.due_date, d.days_overdue "
        "FROM cpmp_deliverables d "
        "WHERE d.due_date < ? AND d.status NOT IN ('accepted') "
        "AND d.days_overdue > 0"
    )
    params = [now_date]
    if contract_id:
        query += " AND d.contract_id = ?"
        params.append(contract_id)

    overdue_rows = conn.execute(query, params).fetchall()

    new_count = 0
    for row in overdue_rows:
        # Check if already tracked
        existing = conn.execute(
            "SELECT id FROM cpmp_negative_events "
            "WHERE contract_id = ? AND event_type = 'delinquent_delivery' "
            "AND source_entity_id = ? AND corrective_action_status IN ('open', 'in_progress')",
            (row["contract_id"], row["id"]),
        ).fetchone()

        if existing:
            continue

        days = row["days_overdue"] or 0
        severity = "critical" if days > 30 else "high" if days > 14 else "medium" if days > 7 else "low"
        event_id = _uuid()
        now = _now()

        conn.execute(
            "INSERT INTO cpmp_negative_events "
            "(id, contract_id, event_type, severity, description, "
            "corrective_action, corrective_action_status, cpars_impact, "
            "detected_by, source_entity_type, source_entity_id, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id, row["contract_id"], "delinquent_delivery", severity,
                f"Deliverable '{row['title']}' is {days} days overdue (due {row['due_date']})",
                None,
                "open",
                PENALTY_TABLE.get("delinquent_delivery", 0.05),
                "auto_detect",
                "deliverable",
                row["id"],
                now,
                now,
            ),
        )
        _record_status_change(
            conn, "negative_event", event_id, None, "open",
            "auto_detect", f"Auto-detected delinquent delivery: {row['title']}",
        )
        new_count += 1

    if new_count > 0:
        _audit(conn, "auto_detect_delinquent",
               f"Auto-detected {new_count} delinquent deliveries"
               + (f" for contract {contract_id}" if contract_id else ""))
    conn.commit()
    conn.close()

    return {"status": "ok", "new_events": new_count, "event_type": "delinquent_delivery"}


def auto_detect_cost_overrun(contract_id=None):
    """
    Check cpmp_evm_periods for CPI < 0.85 for 3 consecutive periods.

    Creates a cost_overrun negative event if not already tracked.

    Returns:
        dict with count of new events created.
    """
    if not _AUTO_CFG.get("cost_overrun", {}).get("enabled", True):
        return {"status": "ok", "new_events": 0, "message": "cost_overrun auto-detect disabled"}

    conn = _get_db()

    # Get distinct contract IDs with EVM data
    contract_query = "SELECT DISTINCT contract_id FROM cpmp_evm_periods WHERE cpi IS NOT NULL"
    contract_params = []
    if contract_id:
        contract_query += " AND contract_id = ?"
        contract_params.append(contract_id)

    contracts = conn.execute(contract_query, contract_params).fetchall()

    new_count = 0
    for c_row in contracts:
        cid = c_row["contract_id"]

        # Get last 3 periods ordered by period_date descending
        periods = conn.execute(
            "SELECT cpi, period_date FROM cpmp_evm_periods "
            "WHERE contract_id = ? AND cpi IS NOT NULL "
            "ORDER BY period_date DESC LIMIT 3",
            (cid,),
        ).fetchall()

        if len(periods) < 3:
            continue

        # Check if all 3 consecutive periods have CPI < 0.85
        all_below = all(p["cpi"] < 0.85 for p in periods)
        if not all_below:
            continue

        # Check if already tracked for this contract (open/in-progress)
        existing = conn.execute(
            "SELECT id FROM cpmp_negative_events "
            "WHERE contract_id = ? AND event_type = 'cost_overrun' "
            "AND corrective_action_status IN ('open', 'in_progress')",
            (cid,),
        ).fetchone()

        if existing:
            continue

        avg_cpi = sum(p["cpi"] for p in periods) / 3
        event_id = _uuid()
        now = _now()
        severity = "critical" if avg_cpi < 0.70 else "high" if avg_cpi < 0.80 else "medium"

        conn.execute(
            "INSERT INTO cpmp_negative_events "
            "(id, contract_id, event_type, severity, description, "
            "corrective_action, corrective_action_status, cpars_impact, "
            "detected_by, source_entity_type, source_entity_id, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id, cid, "cost_overrun", severity,
                f"CPI below 0.85 for 3 consecutive periods (avg CPI: {avg_cpi:.3f})",
                None,
                "open",
                PENALTY_TABLE.get("cost_overrun", 0.08),
                "auto_detect",
                None,
                None,
                now,
                now,
            ),
        )
        _record_status_change(
            conn, "negative_event", event_id, None, "open",
            "auto_detect", f"Auto-detected cost overrun: avg CPI {avg_cpi:.3f}",
        )
        new_count += 1

    if new_count > 0:
        _audit(conn, "auto_detect_cost_overrun",
               f"Auto-detected {new_count} cost overrun events"
               + (f" for contract {contract_id}" if contract_id else ""))
    conn.commit()
    conn.close()

    return {"status": "ok", "new_events": new_count, "event_type": "cost_overrun"}


def auto_detect_quality_rejection(contract_id=None):
    """
    Check cpmp_deliverables for rejection_count >= 2.

    Counts 'rejected' status entries in cpmp_status_history for each
    deliverable. Creates a quality_rejection event if threshold exceeded
    and not already tracked.

    Returns:
        dict with count of new events created.
    """
    if not _AUTO_CFG.get("quality_rejection", {}).get("enabled", True):
        return {"status": "ok", "new_events": 0, "message": "quality_rejection auto-detect disabled"}

    conn = _get_db()

    # Find deliverables with 2+ rejections via status_history
    query = (
        "SELECT d.id AS deliverable_id, d.contract_id, d.title, "
        "COUNT(h.id) AS rejection_count "
        "FROM cpmp_deliverables d "
        "JOIN cpmp_status_history h ON h.entity_type = 'deliverable' "
        "  AND h.entity_id = d.id AND h.new_status = 'rejected' "
        "WHERE 1=1"
    )
    params = []
    if contract_id:
        query += " AND d.contract_id = ?"
        params.append(contract_id)

    query += " GROUP BY d.id HAVING COUNT(h.id) >= 2"

    rows = conn.execute(query, params).fetchall()

    new_count = 0
    for row in rows:
        # Check if already tracked
        existing = conn.execute(
            "SELECT id FROM cpmp_negative_events "
            "WHERE contract_id = ? AND event_type = 'quality_rejection' "
            "AND source_entity_id = ? AND corrective_action_status IN ('open', 'in_progress')",
            (row["contract_id"], row["deliverable_id"]),
        ).fetchone()

        if existing:
            continue

        rejection_count = row["rejection_count"]
        severity = "critical" if rejection_count >= 4 else "high" if rejection_count >= 3 else "medium"
        event_id = _uuid()
        now = _now()

        conn.execute(
            "INSERT INTO cpmp_negative_events "
            "(id, contract_id, event_type, severity, description, "
            "corrective_action, corrective_action_status, cpars_impact, "
            "detected_by, source_entity_type, source_entity_id, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id, row["contract_id"], "quality_rejection", severity,
                f"Deliverable '{row['title']}' rejected {rejection_count} times",
                None,
                "open",
                PENALTY_TABLE.get("quality_rejection", 0.06),
                "auto_detect",
                "deliverable",
                row["deliverable_id"],
                now,
                now,
            ),
        )
        _record_status_change(
            conn, "negative_event", event_id, None, "open",
            "auto_detect", f"Auto-detected quality rejection: {row['title']} ({rejection_count}x)",
        )
        new_count += 1

    if new_count > 0:
        _audit(conn, "auto_detect_quality_rejection",
               f"Auto-detected {new_count} quality rejection events"
               + (f" for contract {contract_id}" if contract_id else ""))
    conn.commit()
    conn.close()

    return {"status": "ok", "new_events": new_count, "event_type": "quality_rejection"}


def auto_detect_flowdown_failure(contract_id=None):
    """
    Check cpmp_subcontractors with subcontract_value > 100000 and
    flow_down_complete = 0. Creates a flowdown_failure event if not
    already tracked.

    Returns:
        dict with count of new events created.
    """
    if not _AUTO_CFG.get("flowdown_failure", {}).get("enabled", True):
        return {"status": "ok", "new_events": 0, "message": "flowdown_failure auto-detect disabled"}

    conn = _get_db()

    query = (
        "SELECT id, contract_id, company_name, subcontract_value "
        "FROM cpmp_subcontractors "
        "WHERE subcontract_value > 100000 AND flow_down_complete = 0 "
        "AND status = 'active'"
    )
    params = []
    if contract_id:
        query += " AND contract_id = ?"
        params.append(contract_id)

    rows = conn.execute(query, params).fetchall()

    new_count = 0
    for row in rows:
        # Check if already tracked
        existing = conn.execute(
            "SELECT id FROM cpmp_negative_events "
            "WHERE contract_id = ? AND event_type = 'flowdown_failure' "
            "AND source_entity_id = ? AND corrective_action_status IN ('open', 'in_progress')",
            (row["contract_id"], row["id"]),
        ).fetchone()

        if existing:
            continue

        severity = "high" if row["subcontract_value"] > 500000 else "medium"
        event_id = _uuid()
        now = _now()

        conn.execute(
            "INSERT INTO cpmp_negative_events "
            "(id, contract_id, event_type, severity, description, "
            "corrective_action, corrective_action_status, cpars_impact, "
            "detected_by, source_entity_type, source_entity_id, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id, row["contract_id"], "flowdown_failure", severity,
                f"Subcontractor '{row['company_name']}' (value ${row['subcontract_value']:,.2f}) "
                f"has incomplete flowdown requirements",
                None,
                "open",
                PENALTY_TABLE.get("flowdown_failure", 0.04),
                "auto_detect",
                "subcontractor",
                row["id"],
                now,
                now,
            ),
        )
        _record_status_change(
            conn, "negative_event", event_id, None, "open",
            "auto_detect", f"Auto-detected flowdown failure: {row['company_name']}",
        )
        new_count += 1

    if new_count > 0:
        _audit(conn, "auto_detect_flowdown_failure",
               f"Auto-detected {new_count} flowdown failure events"
               + (f" for contract {contract_id}" if contract_id else ""))
    conn.commit()
    conn.close()

    return {"status": "ok", "new_events": new_count, "event_type": "flowdown_failure"}


def auto_detect_all(contract_id=None):
    """
    Run all 4 auto-detection functions.

    Returns:
        dict with summary of all detection results.
    """
    results = {
        "delinquent_delivery": auto_detect_delinquent(contract_id),
        "cost_overrun": auto_detect_cost_overrun(contract_id),
        "quality_rejection": auto_detect_quality_rejection(contract_id),
        "flowdown_failure": auto_detect_flowdown_failure(contract_id),
    }

    total_new = sum(r.get("new_events", 0) for r in results.values())

    return {
        "status": "ok",
        "total_new_events": total_new,
        "contract_id": contract_id,
        "detections": results,
    }


def compute_cpars_impact(contract_id):
    """
    Sum total CPARS impact from all open/in-progress negative events.

    Applies corrective_action_discount (default 0.50) for events with
    completed corrective actions.

    Returns:
        dict with total penalty and per-event breakdown.
    """
    conn = _get_db()

    if not conn.execute("SELECT id FROM cpmp_contracts WHERE id = ?", (contract_id,)).fetchone():
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    rows = conn.execute(
        "SELECT id, event_type, severity, cpars_impact, corrective_action_status, description "
        "FROM cpmp_negative_events WHERE contract_id = ?",
        (contract_id,),
    ).fetchall()

    total_penalty = 0.0
    breakdown = []

    for row in rows:
        base_impact = row["cpars_impact"] or PENALTY_TABLE.get(row["event_type"], 0.0)
        ca_status = row["corrective_action_status"]

        # Completed/verified corrective actions receive discount
        if ca_status in ("completed", "verified"):
            effective_impact = base_impact * CORRECTIVE_ACTION_DISCOUNT
            discount_applied = True
        elif ca_status in ("open", "in_progress"):
            effective_impact = base_impact
            discount_applied = False
        else:
            effective_impact = base_impact
            discount_applied = False

        # Only open/in_progress events count toward active penalty
        if ca_status in ("open", "in_progress"):
            total_penalty += effective_impact

        breakdown.append({
            "event_id": row["id"],
            "event_type": row["event_type"],
            "severity": row["severity"],
            "base_impact": base_impact,
            "effective_impact": round(effective_impact, 4),
            "corrective_action_status": ca_status,
            "discount_applied": discount_applied,
            "counts_toward_penalty": ca_status in ("open", "in_progress"),
            "description": row["description"],
        })

    conn.close()

    return {
        "status": "ok",
        "contract_id": contract_id,
        "total_penalty": round(total_penalty, 4),
        "total_events": len(breakdown),
        "open_events": sum(1 for b in breakdown if b["counts_toward_penalty"]),
        "corrective_action_discount": CORRECTIVE_ACTION_DISCOUNT,
        "breakdown": breakdown,
    }


def update_corrective_action(event_id, status, action_text=None):
    """
    Update corrective_action_status on a negative event record.

    Note: even though cpmp_negative_events is append-only for the event
    itself, the corrective_action_status field IS updatable (it tracks
    remediation progress, not the event record).

    Args:
        event_id: Negative event UUID.
        status: New corrective action status (open, in_progress, completed, verified).
        action_text: Optional corrective action description text.

    Returns:
        dict with status and transition info.
    """
    if status not in VALID_CORRECTIVE_STATUSES:
        return {"status": "error",
                "message": f"Invalid corrective action status: {status}. Valid: {list(VALID_CORRECTIVE_STATUSES)}"}

    conn = _get_db()
    row = conn.execute(
        "SELECT id, contract_id, corrective_action_status FROM cpmp_negative_events WHERE id = ?",
        (event_id,),
    ).fetchone()

    if not row:
        conn.close()
        return {"status": "error", "message": f"Negative event {event_id} not found"}

    old_status = row["corrective_action_status"]

    # Build update
    sets = ["corrective_action_status = ?"]
    params = [status]

    if action_text is not None:
        sets.append("corrective_action = ?")
        params.append(action_text)

    sets.append("updated_at = ?")
    params.append(_now())

    params.append(event_id)
    conn.execute(
        f"UPDATE cpmp_negative_events SET {', '.join(sets)} WHERE id = ?",
        params,
    )

    _record_status_change(
        conn, "negative_event", event_id, old_status, status,
        "corrective_action_update",
        f"Corrective action: {old_status} -> {status}" + (f": {action_text}" if action_text else ""),
    )
    _audit(conn, "update_corrective_action",
           f"Event {event_id}: {old_status} -> {status}")
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "event_id": event_id,
        "old_status": old_status,
        "new_status": status,
    }


def list_events(contract_id, severity=None, status=None):
    """
    List negative events for a contract with optional filters.

    Args:
        contract_id: Contract UUID.
        severity: Optional severity filter (low, medium, high, critical).
        status: Optional corrective action status filter (open, in_progress, completed, verified).

    Returns:
        dict with list of events.
    """
    conn = _get_db()
    query = "SELECT * FROM cpmp_negative_events WHERE contract_id = ?"
    params = [contract_id]

    if severity:
        query += " AND severity = ?"
        params.append(severity)
    if status:
        query += " AND corrective_action_status = ?"
        params.append(status)

    query += " ORDER BY created_at DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    return {
        "status": "ok",
        "contract_id": contract_id,
        "total": len(rows),
        "events": [dict(r) for r in rows],
    }


def check_ndaa_thresholds(contract_id):
    """
    Check if any NDAA thresholds trigger government notification.

    Notification triggers:
        - Any critical severity event (open or in_progress)
        - Any termination-level event type (termination_default, fraud_waste_abuse)
        - Total active penalty > 0.30

    Returns:
        dict with threshold check results and notification requirement.
    """
    conn = _get_db()

    if not conn.execute("SELECT id FROM cpmp_contracts WHERE id = ?", (contract_id,)).fetchone():
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    # Check critical severity events
    critical_events = conn.execute(
        "SELECT id, event_type, description FROM cpmp_negative_events "
        "WHERE contract_id = ? AND severity = 'critical' "
        "AND corrective_action_status IN ('open', 'in_progress')",
        (contract_id,),
    ).fetchall()

    # Check termination-level events
    termination_types = ("termination_default", "fraud_waste_abuse")
    termination_events = conn.execute(
        "SELECT id, event_type, description FROM cpmp_negative_events "
        "WHERE contract_id = ? AND event_type IN (?, ?) "
        "AND corrective_action_status IN ('open', 'in_progress')",
        (contract_id, *termination_types),
    ).fetchall()

    conn.close()

    # Compute impact for penalty threshold
    impact = compute_cpars_impact(contract_id)
    total_penalty = impact.get("total_penalty", 0.0)

    triggers = []

    if critical_events:
        triggers.append({
            "trigger": "critical_severity",
            "description": f"{len(critical_events)} critical severity event(s) open",
            "events": [dict(e) for e in critical_events],
        })

    if termination_events:
        triggers.append({
            "trigger": "termination_level",
            "description": f"{len(termination_events)} termination-level event(s) open",
            "events": [dict(e) for e in termination_events],
        })

    if total_penalty > 0.30:
        triggers.append({
            "trigger": "penalty_threshold",
            "description": f"Total active penalty ({total_penalty:.4f}) exceeds 0.30 threshold",
            "total_penalty": total_penalty,
        })

    notification_required = len(triggers) > 0

    return {
        "status": "ok",
        "contract_id": contract_id,
        "notification_required": notification_required,
        "trigger_count": len(triggers),
        "triggers": triggers,
        "total_penalty": total_penalty,
        "critical_event_count": len(critical_events),
        "termination_event_count": len(termination_events),
    }


# -- CLI -------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ICDEV GovProposal — Negative Event Tracker (Phase 60, D-CPMP-7)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--record", action="store_true",
                       help="Record a negative event")
    group.add_argument("--auto-detect", action="store_true",
                       help="Run all auto-detection functions")
    group.add_argument("--impact", action="store_true",
                       help="Compute CPARS impact for a contract")
    group.add_argument("--update-corrective", action="store_true",
                       help="Update corrective action status on an event")
    group.add_argument("--list", action="store_true",
                       help="List negative events for a contract")
    group.add_argument("--ndaa-check", action="store_true",
                       help="Check NDAA notification thresholds")

    parser.add_argument("--contract-id", help="Contract UUID")
    parser.add_argument("--event-id", help="Negative event UUID")
    parser.add_argument("--event-type", help="Event type (e.g. delinquent_delivery)")
    parser.add_argument("--severity", help="Severity (low, medium, high, critical)")
    parser.add_argument("--description", help="Event description")
    parser.add_argument("--deliverable-id", help="Related deliverable UUID (sets source_entity_type=deliverable)")
    parser.add_argument("--subcontractor-id", help="Related subcontractor UUID (sets source_entity_type=subcontractor)")
    parser.add_argument("--corrective-action", help="Corrective action text")
    parser.add_argument("--detected-by", help="Detection method (default: manual)", default="manual")
    parser.add_argument("--status", help="Corrective action status filter or new status")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    if args.record:
        if not args.contract_id or not args.event_type or not args.severity or not args.description:
            print(json.dumps({"status": "error",
                              "message": "--record requires --contract-id, --event-type, --severity, --description"}))
            sys.exit(1)
        kwargs = {}
        if args.deliverable_id:
            kwargs["deliverable_id"] = args.deliverable_id
        if args.subcontractor_id:
            kwargs["subcontractor_id"] = args.subcontractor_id
        if args.corrective_action:
            kwargs["corrective_action"] = args.corrective_action
        if args.detected_by:
            kwargs["detected_by"] = args.detected_by
        result = record_event(args.contract_id, args.event_type, args.severity,
                              args.description, **kwargs)

    elif args.auto_detect:
        result = auto_detect_all(contract_id=args.contract_id)

    elif args.impact:
        if not args.contract_id:
            print(json.dumps({"status": "error", "message": "--impact requires --contract-id"}))
            sys.exit(1)
        result = compute_cpars_impact(args.contract_id)

    elif args.update_corrective:
        if not args.event_id or not args.status:
            print(json.dumps({"status": "error",
                              "message": "--update-corrective requires --event-id and --status"}))
            sys.exit(1)
        result = update_corrective_action(args.event_id, args.status,
                                          action_text=args.corrective_action)

    elif args.list:
        if not args.contract_id:
            print(json.dumps({"status": "error", "message": "--list requires --contract-id"}))
            sys.exit(1)
        result = list_events(args.contract_id, severity=args.severity, status=args.status)

    elif args.ndaa_check:
        if not args.contract_id:
            print(json.dumps({"status": "error", "message": "--ndaa-check requires --contract-id"}))
            sys.exit(1)
        result = check_ndaa_thresholds(args.contract_id)

    else:
        result = {"status": "error", "message": "Unknown command"}

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
