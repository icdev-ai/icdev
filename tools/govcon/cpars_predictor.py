# CUI // SP-CTI
# ICDEV GovProposal — CPARS Predictor (Phase 60, D-CPMP-3)
# Deterministic weighted CPARS scoring with NDAA negative-event penalties.

"""
CPARS Predictor — Deterministic weighted scoring for CPARS prediction.

Computes 5 dimension scores (quality, schedule, cost, management, small_business),
applies NDAA negative-event penalty table, and maps the result to a CPARS rating.

Completed corrective actions reduce penalties by the configurable
corrective_action_discount (default 50%).

Key functions:
    - predict_cpars(contract_id): Full CPARS prediction with dimension breakdown
    - create_assessment(contract_id, period_start, period_end): New CPARS assessment
    - update_assessment(assessment_id, data): Update assessment fields
    - get_assessment(assessment_id): Get single assessment
    - list_assessments(contract_id): List all assessments for a contract
    - get_cpars_trend(contract_id): Time-series of CPARS scores for charting

Tables used:
    - cpmp_cpars_assessments (CRUD)
    - cpmp_deliverables (read for quality/schedule scoring)
    - cpmp_evm_periods (read for cost scoring)
    - cpmp_negative_events (read for management scoring, NDAA penalties)
    - cpmp_small_business_plan (read for SB scoring)
    - cpmp_status_history (write on status changes)

Usage:
    python tools/govcon/cpars_predictor.py --predict --contract-id <id> --json
    python tools/govcon/cpars_predictor.py --create --contract-id <id> --period-start 2025-01 --period-end 2025-06 --json
    python tools/govcon/cpars_predictor.py --update --assessment-id <id> --data '{}' --json
    python tools/govcon/cpars_predictor.py --get --assessment-id <id> --json
    python tools/govcon/cpars_predictor.py --list --contract-id <id> --json
    python tools/govcon/cpars_predictor.py --trend --contract-id <id> --json
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
_CPARS_CFG = _CFG.get("cpars", {})

PREDICTION_WEIGHTS = _CPARS_CFG.get("prediction_weights", {
    "quality": 0.25,
    "schedule": 0.25,
    "cost": 0.20,
    "management": 0.15,
    "small_business": 0.15,
})

RATING_THRESHOLDS = _CPARS_CFG.get("rating_thresholds", {
    "exceptional": 0.90,
    "very_good": 0.75,
    "satisfactory": 0.60,
    "marginal": 0.40,
})

CORRECTIVE_ACTION_DISCOUNT = _CPARS_CFG.get("corrective_action_discount", 0.50)

_NEG_CFG = _CFG.get("negative_events", {})
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


def _audit(conn, action, details="", actor="cpars_predictor"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (id, timestamp, event_type, actor, action, details, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_uuid(), _now(), "cpmp.cpars_predictor", actor, action, details, "cpmp"),
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


def _score_to_rating(score):
    """Map a 0.0-1.0 score to a CPARS rating string."""
    if score >= RATING_THRESHOLDS.get("exceptional", 0.90):
        return "exceptional"
    if score >= RATING_THRESHOLDS.get("very_good", 0.75):
        return "very_good"
    if score >= RATING_THRESHOLDS.get("satisfactory", 0.60):
        return "satisfactory"
    if score >= RATING_THRESHOLDS.get("marginal", 0.40):
        return "marginal"
    return "unsatisfactory"


# -- Dimension Scorers -----------------------------------------------------

def _score_quality(conn, contract_id):
    """Quality dimension: ratio of accepted deliverables / total submitted.

    Only counts deliverables that have reached submitted status or beyond
    (submitted, government_review, accepted, rejected, resubmitted).
    Returns 1.0 if no submitted deliverables exist yet.
    """
    submitted_statuses = (
        "submitted", "government_review", "accepted", "rejected", "resubmitted",
    )
    placeholders = ",".join("?" for _ in submitted_statuses)

    total = conn.execute(
        f"SELECT COUNT(*) FROM cpmp_deliverables "
        f"WHERE contract_id = ? AND status IN ({placeholders})",
        (contract_id, *submitted_statuses),
    ).fetchone()[0]

    if total == 0:
        return 1.0

    accepted = conn.execute(
        "SELECT COUNT(*) FROM cpmp_deliverables "
        "WHERE contract_id = ? AND status = 'accepted'",
        (contract_id,),
    ).fetchone()[0]

    return accepted / total


def _score_schedule(conn, contract_id):
    """Schedule dimension: 1.0 - (overdue_count / total_deliverables), min 0.0.

    Returns 1.0 if no deliverables exist.
    """
    total = conn.execute(
        "SELECT COUNT(*) FROM cpmp_deliverables WHERE contract_id = ?",
        (contract_id,),
    ).fetchone()[0]

    if total == 0:
        return 1.0

    overdue = conn.execute(
        "SELECT COUNT(*) FROM cpmp_deliverables "
        "WHERE contract_id = ? AND status = 'overdue'",
        (contract_id,),
    ).fetchone()[0]

    return max(0.0, 1.0 - (overdue / total))


def _score_cost(conn, contract_id):
    """Cost dimension: latest CPI from cpmp_evm_periods, capped at 1.0.

    CPI > 1.0 is favorable but capped. Returns 1.0 if no EVM data.
    """
    row = conn.execute(
        "SELECT cpi FROM cpmp_evm_periods "
        "WHERE contract_id = ? ORDER BY period_date DESC LIMIT 1",
        (contract_id,),
    ).fetchone()

    if not row or row["cpi"] is None:
        return 1.0

    return min(1.0, max(0.0, row["cpi"]))


def _score_management(conn, contract_id):
    """Management dimension: 1.0 - (open_negative_events * 0.15), min 0.0.

    Counts negative events with corrective_action_status in ('open', 'in_progress').
    """
    open_count = conn.execute(
        "SELECT COUNT(*) FROM cpmp_negative_events "
        "WHERE contract_id = ? AND corrective_action_status IN ('open', 'in_progress')",
        (contract_id,),
    ).fetchone()[0]

    return max(0.0, 1.0 - (open_count * 0.15))


def _score_small_business(conn, contract_id):
    """Small business dimension: actual/goal ratio average from latest record.

    Reads the most recent cpmp_small_business_plan row for the contract and
    computes an average of actual_pct / goal_pct across all non-null SB categories.
    Returns 1.0 if no SB plan or no goals set.
    """
    row = conn.execute(
        "SELECT * FROM cpmp_small_business_plan "
        "WHERE contract_id = ? ORDER BY created_at DESC LIMIT 1",
        (contract_id,),
    ).fetchone()

    if not row:
        return 1.0

    row_dict = dict(row)

    # SB categories stored as goal/actual column pairs
    sb_categories = [
        "sb", "sdb", "wosb", "hubzone", "sdvosb",
    ]

    ratios = []
    for cat in sb_categories:
        goal_key = f"{cat}_goal_pct"
        actual_key = f"{cat}_actual_pct"
        goal = row_dict.get(goal_key)
        actual = row_dict.get(actual_key)
        if goal is not None and goal > 0 and actual is not None:
            ratios.append(min(1.0, actual / goal))

    if not ratios:
        return 1.0

    return sum(ratios) / len(ratios)


# -- NDAA Penalty ----------------------------------------------------------

def _compute_ndaa_penalty(conn, contract_id):
    """Compute total NDAA penalty from open/in-progress negative events.

    Each event type maps to a penalty amount from the penalty table.
    Completed corrective actions reduce the penalty by corrective_action_discount.

    Returns (total_penalty, penalty_details list).
    """
    events = conn.execute(
        "SELECT event_type, corrective_action_status FROM cpmp_negative_events "
        "WHERE contract_id = ? AND corrective_action_status IN ('open', 'in_progress', 'completed')",
        (contract_id,),
    ).fetchall()

    total_penalty = 0.0
    details = []

    for event in events:
        event_type = event["event_type"]
        ca_status = event["corrective_action_status"]
        base_penalty = PENALTY_TABLE.get(event_type, 0.0)

        if ca_status == "completed":
            effective_penalty = base_penalty * (1.0 - CORRECTIVE_ACTION_DISCOUNT)
        else:
            effective_penalty = base_penalty

        total_penalty += effective_penalty
        details.append({
            "event_type": event_type,
            "corrective_action_status": ca_status,
            "base_penalty": base_penalty,
            "effective_penalty": round(effective_penalty, 4),
        })

    return round(total_penalty, 4), details


# -- Prediction ------------------------------------------------------------

def predict_cpars(contract_id):
    """Main CPARS prediction function.

    Steps:
        a. Compute 5 dimension scores (0.0-1.0 each)
        b. Apply NDAA penalty from negative events
        c. Weighted average minus total NDAA penalty
        d. Map to CPARS rating

    Returns:
        dict with dimension_scores, ndaa_penalty, predicted_score, predicted_rating
    """
    conn = _get_db()
    row = conn.execute(
        "SELECT id FROM cpmp_contracts WHERE id = ?", (contract_id,)
    ).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    # a. Compute dimension scores
    dimension_scores = {
        "quality": _score_quality(conn, contract_id),
        "schedule": _score_schedule(conn, contract_id),
        "cost": _score_cost(conn, contract_id),
        "management": _score_management(conn, contract_id),
        "small_business": _score_small_business(conn, contract_id),
    }

    # b. NDAA penalty
    ndaa_penalty, penalty_details = _compute_ndaa_penalty(conn, contract_id)

    # c. Weighted average minus penalty
    weighted_sum = sum(
        dimension_scores[dim] * PREDICTION_WEIGHTS.get(dim, 0.0)
        for dim in dimension_scores
    )
    predicted_score = max(0.0, min(1.0, weighted_sum - ndaa_penalty))

    # d. Map to rating
    predicted_rating = _score_to_rating(predicted_score)

    _audit(conn, "predict_cpars",
           f"Contract {contract_id}: score={predicted_score:.4f}, rating={predicted_rating}")
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "contract_id": contract_id,
        "dimension_scores": {k: round(v, 4) for k, v in dimension_scores.items()},
        "weights": PREDICTION_WEIGHTS,
        "ndaa_penalty": ndaa_penalty,
        "ndaa_penalty_details": penalty_details,
        "predicted_score": round(predicted_score, 4),
        "predicted_rating": predicted_rating,
        "rating_thresholds": RATING_THRESHOLDS,
    }


# -- Assessments -----------------------------------------------------------

def create_assessment(contract_id, period_start, period_end, data=None):
    """Create a new CPARS assessment record.

    Auto-populates predicted_overall and predicted_score from predict_cpars().
    Status starts as 'draft'.
    """
    conn = _get_db()
    row = conn.execute(
        "SELECT id FROM cpmp_contracts WHERE id = ?", (contract_id,)
    ).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    data = data or {}
    assessment_id = _uuid()

    # Run prediction to auto-populate
    prediction = predict_cpars(contract_id)
    predicted_score = prediction.get("predicted_score", 0.0) if prediction.get("status") == "ok" else None
    predicted_rating = prediction.get("predicted_rating") if prediction.get("status") == "ok" else None

    # Count current negative events for this contract
    neg_event_count = conn.execute(
        "SELECT COUNT(*) FROM cpmp_negative_events "
        "WHERE contract_id = ? AND corrective_action_status IN ('open', 'in_progress')",
        (contract_id,),
    ).fetchone()[0]

    conn.execute(
        "INSERT INTO cpmp_cpars_assessments "
        "(id, contract_id, period_start, period_end, quality_rating, schedule_rating, "
        "cost_rating, management_rating, small_business_rating, overall_rating, "
        "predicted_overall, narrative, negative_event_count, status, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            assessment_id, contract_id, period_start, period_end,
            data.get("quality_rating"),
            data.get("schedule_rating"),
            data.get("cost_rating"),
            data.get("management_rating"),
            data.get("small_business_rating"),
            data.get("overall_rating"),
            predicted_rating,
            data.get("narrative"),
            neg_event_count,
            "draft",
            _now(), _now(),
        ),
    )

    _record_status_change(conn, "cpars_assessment", assessment_id, None, "draft",
                          "system", "Assessment created")
    _audit(conn, "create_assessment",
           f"Created CPARS assessment {assessment_id} for contract {contract_id} "
           f"({period_start} to {period_end})")
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "assessment_id": assessment_id,
        "contract_id": contract_id,
        "period_start": period_start,
        "period_end": period_end,
        "predicted_score": predicted_score,
        "predicted_rating": predicted_rating,
    }


def update_assessment(assessment_id, data):
    """Update mutable fields on a CPARS assessment.

    Allowed fields: quality_rating, schedule_rating, cost_rating,
    management_rating, small_business_rating, overall_rating, narrative, status.
    """
    conn = _get_db()
    row = conn.execute(
        "SELECT id, status FROM cpmp_cpars_assessments WHERE id = ?",
        (assessment_id,),
    ).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Assessment {assessment_id} not found"}

    old_status = row["status"]
    updatable = [
        "quality_rating", "schedule_rating", "cost_rating",
        "management_rating", "small_business_rating", "overall_rating",
        "narrative", "status",
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
    params.append(assessment_id)

    conn.execute(
        f"UPDATE cpmp_cpars_assessments SET {', '.join(sets)} WHERE id = ?",
        params,
    )

    # Record status change if status was updated
    if "status" in data and data["status"] != old_status:
        _record_status_change(conn, "cpars_assessment", assessment_id,
                              old_status, data["status"], None,
                              "Assessment status updated")

    _audit(conn, "update_assessment",
           f"Updated assessment {assessment_id}: {list(data.keys())}")
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "assessment_id": assessment_id,
        "updated_fields": list(data.keys()),
    }


def get_assessment(assessment_id):
    """Get a single CPARS assessment with its prediction data."""
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM cpmp_cpars_assessments WHERE id = ?",
        (assessment_id,),
    ).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Assessment {assessment_id} not found"}

    assessment = dict(row)

    # Attach status history
    history = conn.execute(
        "SELECT * FROM cpmp_status_history "
        "WHERE entity_type = 'cpars_assessment' AND entity_id = ? "
        "ORDER BY created_at DESC",
        (assessment_id,),
    ).fetchall()
    assessment["status_history"] = [dict(h) for h in history]

    # Attach live prediction for comparison
    prediction = predict_cpars(assessment["contract_id"])
    if prediction.get("status") == "ok":
        assessment["live_prediction"] = {
            "predicted_score": prediction["predicted_score"],
            "predicted_rating": prediction["predicted_rating"],
            "dimension_scores": prediction["dimension_scores"],
            "ndaa_penalty": prediction["ndaa_penalty"],
        }

    conn.close()
    return {"status": "ok", "assessment": assessment}


def list_assessments(contract_id):
    """List all CPARS assessments for a contract, ordered by period_end descending."""
    conn = _get_db()
    row = conn.execute(
        "SELECT id FROM cpmp_contracts WHERE id = ?", (contract_id,)
    ).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    rows = conn.execute(
        "SELECT * FROM cpmp_cpars_assessments "
        "WHERE contract_id = ? ORDER BY period_end DESC",
        (contract_id,),
    ).fetchall()
    conn.close()

    return {
        "status": "ok",
        "contract_id": contract_id,
        "total": len(rows),
        "assessments": [dict(r) for r in rows],
    }


def get_cpars_trend(contract_id):
    """Return time-series of CPARS scores for charting.

    Returns both actual overall_score and predicted_score per assessment period,
    ordered chronologically.
    """
    conn = _get_db()
    row = conn.execute(
        "SELECT id FROM cpmp_contracts WHERE id = ?", (contract_id,)
    ).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    rows = conn.execute(
        "SELECT id, period_start, period_end, overall_rating, "
        "predicted_overall, quality_rating, schedule_rating, "
        "cost_rating, management_rating, small_business_rating, "
        "negative_event_count, status "
        "FROM cpmp_cpars_assessments "
        "WHERE contract_id = ? ORDER BY period_end ASC",
        (contract_id,),
    ).fetchall()

    trend = []
    for r in rows:
        entry = dict(r)
        trend.append(entry)

    conn.close()

    return {
        "status": "ok",
        "contract_id": contract_id,
        "total_periods": len(trend),
        "trend": trend,
        "rating_thresholds": RATING_THRESHOLDS,
    }


# -- CLI -------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ICDEV GovProposal CPARS Predictor (Phase 60, D-CPMP-3)"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--predict", action="store_true",
                       help="Run CPARS prediction for a contract")
    group.add_argument("--create", action="store_true",
                       help="Create a new CPARS assessment")
    group.add_argument("--update", action="store_true",
                       help="Update an existing CPARS assessment")
    group.add_argument("--get", action="store_true",
                       help="Get a single CPARS assessment")
    group.add_argument("--list", action="store_true",
                       help="List all CPARS assessments for a contract")
    group.add_argument("--trend", action="store_true",
                       help="Get CPARS score trend for charting")

    parser.add_argument("--contract-id", help="Contract ID")
    parser.add_argument("--assessment-id", help="Assessment ID")
    parser.add_argument("--period-start", help="Assessment period start (e.g. 2025-01)")
    parser.add_argument("--period-end", help="Assessment period end (e.g. 2025-06)")
    parser.add_argument("--data", help="JSON data for create/update")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()
    data = json.loads(args.data) if args.data else {}

    if args.predict:
        if not args.contract_id:
            print("Error: --contract-id required", file=sys.stderr)
            sys.exit(1)
        result = predict_cpars(args.contract_id)

    elif args.create:
        if not args.contract_id:
            print("Error: --contract-id required", file=sys.stderr)
            sys.exit(1)
        if not args.period_start or not args.period_end:
            print("Error: --period-start and --period-end required", file=sys.stderr)
            sys.exit(1)
        result = create_assessment(args.contract_id, args.period_start,
                                   args.period_end, data)

    elif args.update:
        if not args.assessment_id:
            print("Error: --assessment-id required", file=sys.stderr)
            sys.exit(1)
        if not data:
            print("Error: --data required for update", file=sys.stderr)
            sys.exit(1)
        result = update_assessment(args.assessment_id, data)

    elif args.get:
        if not args.assessment_id:
            print("Error: --assessment-id required", file=sys.stderr)
            sys.exit(1)
        result = get_assessment(args.assessment_id)

    elif args.list:
        if not args.contract_id:
            print("Error: --contract-id required", file=sys.stderr)
            sys.exit(1)
        result = list_assessments(args.contract_id)

    elif args.trend:
        if not args.contract_id:
            print("Error: --contract-id required", file=sys.stderr)
            sys.exit(1)
        result = get_cpars_trend(args.contract_id)

    else:
        result = {"status": "error", "message": "Unknown command"}

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
