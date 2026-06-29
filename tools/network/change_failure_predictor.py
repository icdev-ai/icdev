"""CUI // SP-CTI -- Change Failure Probability Scorer (PNA module)"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from tools.logging.icdev_logger import get_logger
from tools.network.db.init_db import get_connection

log = get_logger(__name__)

_HITL_THRESHOLD = 0.65
_AUTO_APPROVE_THRESHOLD = 0.25


def _count_blast_radius(blast_json: Optional[str]) -> int:
    """Count devices in a blast_radius_json field. Returns 0 on empty/invalid input."""
    if not blast_json:
        return 0
    try:
        data = json.loads(blast_json)
        return len(data) if isinstance(data, list) else 0
    except (json.JSONDecodeError, TypeError):
        return 0

_VERDICT_BASE = {
    "fail": 0.65,
    "warn": 0.40,
    "pass": 0.15,
    "skipped": 0.20,
}


def _count_blast_radius(blast_json: Optional[str]) -> int:
    if not blast_json:
        return 0
    try:
        items = json.loads(blast_json)
        return len(items) if isinstance(items, list) else 0
    except (json.JSONDecodeError, TypeError):
        return 0


def _get_concurrent_changes(conn, device_name: str) -> int:
    try:
        cur = conn.execute(
            """
            SELECT COUNT(*) FROM nc_change_risk
            WHERE device_name = %s
              AND predicted_at >= datetime('now', '-4 hours')
            """,
            (device_name,),
        )
        row = cur.fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


def _risk_tier(score: float) -> str:
    if score >= 0.80:
        return "critical"
    if score >= 0.60:
        return "high"
    if score >= 0.40:
        return "medium"
    return "low"


def _score_plan_row(conn, row: dict) -> dict:
    device_name = row.get("device_name", "")
    verdict = row.get("simulation_status", "skipped")
    blast_json = row.get("blast_radius_json")

    blast_count = _count_blast_radius(blast_json)
    concurrent = _get_concurrent_changes(conn, device_name)

    base = _VERDICT_BASE.get(verdict, 0.20)
    blast_factor = min(0.20, blast_count * 0.01)
    concurrency_factor = min(0.15, concurrent * 0.05)

    probability = round(min(1.0, base + blast_factor + concurrency_factor), 4)
    tier = _risk_tier(probability)

    risk_factors = {
        "simulation_verdict": verdict,
        "blast_radius_devices": blast_count,
        "concurrent_changes": concurrent,
        "base_probability": base,
    }

    return {
        "device_name": device_name,
        "failure_probability": probability,
        "risk_tier": tier,
        "risk_factors_json": json.dumps(risk_factors),
    }


def _insert_change_risk(conn, rec: dict) -> None:
    conn.execute(
        """
        INSERT INTO nc_change_risk
            (change_request_id, device_name, action_type,
             failure_probability, blast_radius_size,
             concurrent_change_count, maintenance_window_compliant,
             device_criticality, risk_factors_json, risk_tier,
             simulation_verdict, model_version, predicted_at, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
        """,
        (
            rec.get("change_request_id", "auto"),
            rec["device_name"],
            rec.get("action_type"),
            rec["failure_probability"],
            rec.get("blast_radius_size", 0),
            rec.get("concurrent_change_count", 0),
            1,
            3,
            rec["risk_factors_json"],
            rec["risk_tier"],
            rec.get("simulation_verdict"),
            "1.0",
            rec.get("predicted_at", datetime.now(timezone.utc).isoformat()),
        ),
    )


def predict_change_failure(plan_id=None):
    conn = get_connection()
    try:
        where = ""
        params = []
        if plan_id:
            where = "WHERE plan_id = ?"
            params.append(plan_id)
        cur = conn.execute(
            f"SELECT * FROM nc_patch_plans {where} ORDER BY created_at DESC LIMIT 200",
            params,
        )
        try:
            cols = [c[0] for c in cur.description]
            plan_rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        except Exception:
            plan_rows = [dict(r) if hasattr(r, "keys") else {} for r in cur.fetchall()]
    except Exception as exc:
        log.warning("Failed to query nc_patch_plans: %s", exc)
        plan_rows = []

    if not plan_rows:
        return {"scored": 0, "warning": "No patch plans found to score."}

    scored = []
    for row in plan_rows:
        try:
            result = _score_plan_row(conn, row)
            result["change_request_id"] = row.get("plan_id", "auto")
            result["action_type"] = row.get("action")
            result["simulation_verdict"] = row.get("simulation_status")
            result["predicted_at"] = datetime.now(timezone.utc).isoformat()
            _insert_change_risk(conn, result)
            conn.commit()
            scored.append(result)
        except Exception as exc:
            log.warning("Failed to score plan row: %s", exc)

    return {"scored": len(scored), "results": scored}


def score_change_risk(change_request_id: str, device_name: str, action_type=None,
                      blast_radius_json="[]", simulation_verdict="skipped"):
    row = {
        "device_name": device_name,
        "simulation_status": simulation_verdict,
        "blast_radius_json": blast_radius_json,
        "action": action_type,
        "plan_id": change_request_id,
    }
    with get_connection() as conn:
        result = _score_plan_row(conn, row)
        result["change_request_id"] = change_request_id
        result["action_type"] = action_type
        result["simulation_verdict"] = simulation_verdict
        result["predicted_at"] = datetime.now(timezone.utc).isoformat()
        try:
            _insert_change_risk(conn, result)
            conn.commit()
        except Exception as exc:
            log.warning("Failed to insert change risk: %s", exc)
        return result


def get_change_risks(device_name=None, risk_tier=None, limit=50):
    conn = get_connection()
    where, params = [], []
    if device_name:
        where.append("device_name = ?")
        params.append(device_name)
    if risk_tier:
        where.append("risk_tier = ?")
        params.append(risk_tier)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"SELECT * FROM nc_change_risk {clause} ORDER BY failure_probability DESC LIMIT ?"
    params.append(limit)
    cur = conn.execute(sql, params)
    try:
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        rows = cur.fetchall()
        return [dict(r) if hasattr(r, "keys") else {} for r in rows]


def get_change_risk_summary():
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT risk_tier, COUNT(*) FROM nc_change_risk GROUP BY risk_tier"
        )
        by_tier = {row[0]: row[1] for row in cur.fetchall()}
    except Exception:
        by_tier = {}

    try:
        cur = conn.execute("SELECT AVG(failure_probability) FROM nc_change_risk")
        row = cur.fetchone()
        avg_prob = round(row[0], 4) if row and row[0] else 0.0
    except Exception:
        avg_prob = 0.0

    return {
        "total_changes": sum(by_tier.values()),
        "by_tier": by_tier,
        "avg_failure_probability": avg_prob,
    }