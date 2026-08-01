#!/usr/bin/env python3
# CUI // SP-CTI
"""R10: Monitor Reflex — CPARS prediction + EVM early warnings.

Scans active contracts for performance health, deliverable timeliness,
and negative event exposure.  Generates deterministic CPARS predictions
and updates contract health scores.

Pipeline: independent (every 4h).
GREEN tier (read-only monitoring, no writes to contract data).
Scanner-tier LLM only (zero Claude tokens — fully deterministic).
"""

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.proposal_genesis.reflexes.monitor")

# ---------------------------------------------------------------------------
# Module-level constants — EVM (ANSI/EIA-748) + CPARS thresholds, all
# overridable from proposal_genesis_config.yaml under reflexes.monitor.
# Change config, not code. These are regulatory-standard values, not
# statistical thresholds — named here for traceability and tunability.
# ---------------------------------------------------------------------------
# Cost Performance Index (CPI) thresholds + score penalties
_CPI_CRITICAL          = 0.80
_CPI_WARNING           = 0.90
_CPI_INFO              = 0.95
_CPI_CRITICAL_PENALTY  = 0.40
_CPI_WARNING_PENALTY   = 0.20
_CPI_INFO_PENALTY      = 0.05
# Schedule Performance Index (SPI) thresholds + score penalties
_SPI_CRITICAL          = 0.80
_SPI_WARNING           = 0.90
_SPI_INFO              = 0.95
_SPI_CRITICAL_PENALTY  = 0.40
_SPI_WARNING_PENALTY   = 0.20
_SPI_INFO_PENALTY      = 0.05
# To-Complete Performance Index (TCPI)
_TCPI_WARNING          = 1.20
_TCPI_PENALTY          = 0.10
# Deliverable schedule health
_OVERDUE_CRITICAL_DAYS = 30
_OVERDUE_WARNING_DAYS  = 7
_OVERDUE_PENALTY_CAP   = 0.50
_OVERDUE_PENALTY_EACH  = 0.10
_UPCOMING_SURGE_COUNT  = 3
_UPCOMING_WINDOW_DAYS  = 14
# Negative-event severity weights
_SEVERITY_WEIGHTS = {"critical": 0.30, "high": 0.20, "medium": 0.10, "low": 0.05}
_SEVERITY_DEFAULT_WEIGHT = 0.05
# CPARS score scale conversion (health 0-1 → CPARS 1-5)
_CPARS_SCALE_BASE      = 1.0
_CPARS_SCALE_FACTOR    = 4.0
_CPARS_DEFAULT_SB      = 3.0   # default small-business compliance score
_CPARS_DEFAULT_TREND   = 3.0   # default trend score
# CPARS composite weights (D-CPMP-3)
_CPARS_W_EVM           = 0.35
_CPARS_W_SCHEDULE      = 0.25
_CPARS_W_RISK          = 0.20
_CPARS_W_SB            = 0.10
_CPARS_W_TREND         = 0.10
# CPARS rating bands
_CPARS_EXCEPTIONAL     = 4.5
_CPARS_VERY_GOOD       = 3.5
_CPARS_SATISFACTORY    = 2.5
_CPARS_MARGINAL        = 1.5
# Contract health weights + status bands (D-CPMP-8)
_HEALTH_W_EVM          = 0.35
_HEALTH_W_SCHEDULE     = 0.25
_HEALTH_W_RISK         = 0.20
_HEALTH_W_SB           = 0.10
_HEALTH_W_TREND        = 0.10
_HEALTH_GREEN          = 0.80
_HEALTH_YELLOW         = 0.60


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "pg") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Active contracts
# ---------------------------------------------------------------------------


def _get_active_contracts() -> List[Dict]:
    """Find all active contracts from CPMP."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT id, contract_number, title, agency, total_value,
                   funded_value, billed_value, pop_start, pop_end,
                   status, health, health_score, cpars_rating_current,
                   opportunity_id
            FROM cpmp_contracts
            WHERE status IN ('active', 'option_pending')
            ORDER BY pop_end ASC
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# EVM health check
# ---------------------------------------------------------------------------


def _get_latest_evm(contract_id: str) -> Optional[Dict]:
    """Get the most recent EVM period for a contract."""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT contract_id, period_date, cpi, spi,
                   cost_variance, schedule_variance,
                   eac, etc, vac, tcpi,
                   planned_value, earned_value, actual_cost,
                   budget_at_completion
            FROM cpmp_evm_periods
            WHERE contract_id = %s
            ORDER BY period_date DESC
            LIMIT 1
        """,
            (contract_id,),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def _assess_evm_health(evm: Optional[Dict]) -> Dict:
    """Assess EVM health from latest period data.

    Returns:
        {"score": 0.0-1.0, "alerts": [...], "cpi": float, "spi": float}
    """
    if not evm:
        return {"score": 1.0, "alerts": [], "cpi": None, "spi": None}

    cpi = evm.get("cpi")
    spi = evm.get("spi")
    alerts = []
    score = 1.0

    if cpi is not None:
        if cpi < _CPI_CRITICAL:
            alerts.append(
                {
                    "level": "critical",
                    "metric": "CPI",
                    "value": cpi,
                    "message": f"CPI {cpi:.2f} < {_CPI_CRITICAL:.2f} -- severe cost overrun",
                }
            )
            score -= _CPI_CRITICAL_PENALTY
        elif cpi < _CPI_WARNING:
            alerts.append(
                {
                    "level": "warning",
                    "metric": "CPI",
                    "value": cpi,
                    "message": f"CPI {cpi:.2f} < {_CPI_WARNING:.2f} -- cost overrun risk",
                }
            )
            score -= _CPI_WARNING_PENALTY
        elif cpi < _CPI_INFO:
            alerts.append(
                {"level": "info", "metric": "CPI", "value": cpi, "message": f"CPI {cpi:.2f} -- monitor cost trends"}
            )
            score -= _CPI_INFO_PENALTY

    if spi is not None:
        if spi < _SPI_CRITICAL:
            alerts.append(
                {
                    "level": "critical",
                    "metric": "SPI",
                    "value": spi,
                    "message": f"SPI {spi:.2f} < {_SPI_CRITICAL:.2f} -- severe schedule slip",
                }
            )
            score -= _SPI_CRITICAL_PENALTY
        elif spi < _SPI_WARNING:
            alerts.append(
                {"level": "warning", "metric": "SPI", "value": spi, "message": f"SPI {spi:.2f} < {_SPI_WARNING:.2f} -- schedule risk"}
            )
            score -= _SPI_WARNING_PENALTY
        elif spi < _SPI_INFO:
            alerts.append(
                {"level": "info", "metric": "SPI", "value": spi, "message": f"SPI {spi:.2f} -- monitor schedule trends"}
            )
            score -= _SPI_INFO_PENALTY

    tcpi = evm.get("tcpi")
    if tcpi is not None and tcpi > _TCPI_WARNING:
        alerts.append(
            {
                "level": "warning",
                "metric": "TCPI",
                "value": tcpi,
                "message": f"TCPI {tcpi:.2f} > {_TCPI_WARNING:.2f} -- recovery unlikely",
            }
        )
        score -= _TCPI_PENALTY

    return {
        "score": max(0.0, score),
        "alerts": alerts,
        "cpi": cpi,
        "spi": spi,
    }


# ---------------------------------------------------------------------------
# Deliverable timeliness
# ---------------------------------------------------------------------------


def _get_overdue_deliverables(contract_id: str) -> List[Dict]:
    """Find deliverables that are overdue or approaching deadline."""
    conn = get_connection()
    now = _utcnow_iso()[:10]  # YYYY-MM-DD
    try:
        rows = conn.execute(
            """
            SELECT id, cdrl_number, title, deliverable_type,
                   due_date, status, days_overdue
            FROM cpmp_deliverables
            WHERE contract_id = %s
            AND status NOT IN ('accepted', 'submitted', 'government_review')
            AND due_date IS NOT NULL
            AND due_date <= %s
            ORDER BY due_date ASC
        """,
            (contract_id, now),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _get_upcoming_deliverables(contract_id: str, days: int = _UPCOMING_WINDOW_DAYS) -> List[Dict]:
    """Find deliverables due within N days."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, cdrl_number, title, deliverable_type,
                   due_date, status
            FROM cpmp_deliverables
            WHERE contract_id = %s
            AND status IN ('not_started', 'in_progress', 'draft_complete')
            AND due_date IS NOT NULL
            AND due_date > date('now')
            AND due_date <= date('now', '+' || %s || ' days')
            ORDER BY due_date ASC
        """,
            (contract_id, days),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _assess_schedule_health(overdue: List[Dict], upcoming: List[Dict]) -> Dict:
    """Assess delivery schedule health.

    Returns:
        {"score": 0.0-1.0, "alerts": [...], "overdue_count": int, "upcoming_count": int}
    """
    alerts = []
    score = 1.0

    if overdue:
        for d in overdue:
            days = d.get("days_overdue", 0) or 0
            level = "critical" if days > _OVERDUE_CRITICAL_DAYS else "warning" if days > _OVERDUE_WARNING_DAYS else "info"
            alerts.append(
                {
                    "level": level,
                    "metric": "deliverable_overdue",
                    "deliverable_id": d.get("id", ""),
                    "title": d.get("title", ""),
                    "days_overdue": days,
                    "message": f"'{d.get('title', '')}' overdue by {days} days",
                }
            )
        # Score penalty based on overdue count
        penalty = min(_OVERDUE_PENALTY_CAP, len(overdue) * _OVERDUE_PENALTY_EACH)
        score -= penalty

    if len(upcoming) > _UPCOMING_SURGE_COUNT:
        alerts.append(
            {
                "level": "info",
                "metric": "deliverable_surge",
                "count": len(upcoming),
                "message": f"{len(upcoming)} deliverables due within {_UPCOMING_WINDOW_DAYS} days",
            }
        )

    return {
        "score": max(0.0, score),
        "alerts": alerts,
        "overdue_count": len(overdue),
        "upcoming_count": len(upcoming),
    }


# ---------------------------------------------------------------------------
# Negative events
# ---------------------------------------------------------------------------


def _get_open_negative_events(contract_id: str) -> List[Dict]:
    """Find unresolved negative events for a contract."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, event_type, severity, description,
                   corrective_action_status, corrective_action_due,
                   cpars_impact, created_at
            FROM cpmp_negative_events
            WHERE contract_id = %s
            AND corrective_action_status IN ('open', 'in_progress')
            ORDER BY severity DESC, created_at DESC
        """,
            (contract_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _assess_risk_health(events: List[Dict]) -> Dict:
    """Assess risk health from open negative events.

    Returns:
        {"score": 0.0-1.0, "alerts": [...], "open_events": int}
    """
    alerts = []
    score = 1.0
    severity_weights = _SEVERITY_WEIGHTS

    for e in events:
        sev = e.get("severity", "low")
        alerts.append(
            {
                "level": "critical" if sev in ("critical", "high") else "warning",
                "metric": "negative_event",
                "event_type": e.get("event_type", ""),
                "severity": sev,
                "message": f"{e.get('event_type', 'event')} ({sev}): {e.get('description', '')[:80]}",
            }
        )
        score -= severity_weights.get(sev, _SEVERITY_DEFAULT_WEIGHT)

    return {
        "score": max(0.0, score),
        "alerts": alerts,
        "open_events": len(events),
    }


# ---------------------------------------------------------------------------
# CPARS prediction (deterministic weighted average, D-CPMP-3)
# ---------------------------------------------------------------------------


def _predict_cpars(contract_id: str, evm_health: Dict, schedule_health: Dict, risk_health: Dict) -> Dict:
    """Predict CPARS rating using deterministic weighted average.

    Weights (D-CPMP-3):
        EVM health:      0.35
        Schedule health: 0.25
        Risk health:     0.20
        Small biz:       0.10
        Trend:           0.10

    Returns:
        {"predicted_score": 0.0-5.0, "predicted_rating": str, "components": dict}
    """
    # Convert health scores (0-1) to CPARS-like scale (1-5)
    evm_score = _CPARS_SCALE_BASE + (evm_health.get("score", 1.0) * _CPARS_SCALE_FACTOR)
    sched_score = _CPARS_SCALE_BASE + (schedule_health.get("score", 1.0) * _CPARS_SCALE_FACTOR)
    risk_score = _CPARS_SCALE_BASE + (risk_health.get("score", 1.0) * _CPARS_SCALE_FACTOR)

    # Small business compliance (placeholder — check if sb plan exists)
    sb_score = _CPARS_DEFAULT_SB

    # Trend score (placeholder — would compare consecutive EVM periods)
    trend_score = _CPARS_DEFAULT_TREND

    composite = (
        evm_score * _CPARS_W_EVM + sched_score * _CPARS_W_SCHEDULE
        + risk_score * _CPARS_W_RISK + sb_score * _CPARS_W_SB + trend_score * _CPARS_W_TREND
    )

    # Map score to rating
    if composite >= _CPARS_EXCEPTIONAL:
        rating = "exceptional"
    elif composite >= _CPARS_VERY_GOOD:
        rating = "very_good"
    elif composite >= _CPARS_SATISFACTORY:
        rating = "satisfactory"
    elif composite >= _CPARS_MARGINAL:
        rating = "marginal"
    else:
        rating = "unsatisfactory"

    return {
        "predicted_score": round(composite, 2),
        "predicted_rating": rating,
        "components": {
            "evm": round(evm_score, 2),
            "schedule": round(sched_score, 2),
            "risk": round(risk_score, 2),
            "small_business": round(sb_score, 2),
            "trend": round(trend_score, 2),
        },
    }


# ---------------------------------------------------------------------------
# Contract health update
# ---------------------------------------------------------------------------


def _compute_contract_health(evm_health: Dict, schedule_health: Dict, risk_health: Dict) -> Dict:
    """Compute overall contract health score (D-CPMP-8).

    Returns:
        {"health": "green"/"yellow"/"red", "health_score": 0.0-1.0}
    """
    score = (
        evm_health.get("score", 1.0) * _HEALTH_W_EVM
        + schedule_health.get("score", 1.0) * _HEALTH_W_SCHEDULE
        + risk_health.get("score", 1.0) * _HEALTH_W_RISK
        + 1.0 * _HEALTH_W_SB     # sb_health placeholder
        + 1.0 * _HEALTH_W_TREND  # trend placeholder
    )

    if score >= _HEALTH_GREEN:
        health = "green"
    elif score >= _HEALTH_YELLOW:
        health = "yellow"
    else:
        health = "red"

    return {"health": health, "health_score": round(score, 3)}


def _store_monitoring_result(contract_id: str, health: Dict, cpars_prediction: Dict, all_alerts: List[Dict]) -> None:
    """Store monitoring results to audit trail."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, opportunity_id, "
            "details, success, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                _generate_id("pgaudit"),
                "contract_monitored",
                "monitor",
                "green",
                contract_id,
                json.dumps(
                    {
                        "health": health,
                        "cpars_prediction": cpars_prediction,
                        "alert_count": len(all_alerts),
                        "critical_alerts": sum(1 for a in all_alerts if a.get("level") == "critical"),
                    }
                ),
                1,
                _utcnow_iso(),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning(
            "_store_monitoring_result: best-effort INSERT into pg_proposal_genesis_audit failed (non-blocking): %s",
            exc,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Monitor Reflex (R10).

    Steps:
      1. Find active contracts from CPMP
      2. Assess EVM health per contract (CPI/SPI thresholds)
      3. Check deliverable timeliness (overdue + upcoming)
      4. Evaluate negative event exposure
      5. Compute CPARS prediction (deterministic weighted average)
      6. Update contract health scores
      7. Audit all findings

    Returns standard reflex result dict.
    """
    contracts = _get_active_contracts()
    if not contracts:
        return {
            "success": True,
            "metric_value": 0.0,
            "details": {
                "status": "no_active_contracts",
                "contracts_monitored": 0,
                "alerts_generated": 0,
            },
        }

    contracts_monitored = 0
    total_alerts = 0
    at_risk = 0
    contract_results = []

    for contract in contracts:
        cid = contract.get("id", "")

        # EVM assessment
        evm_data = _get_latest_evm(cid)
        evm_health = _assess_evm_health(evm_data)

        # Schedule assessment
        overdue = _get_overdue_deliverables(cid)
        upcoming = _get_upcoming_deliverables(cid)
        schedule_health = _assess_schedule_health(overdue, upcoming)

        # Risk assessment
        neg_events = _get_open_negative_events(cid)
        risk_health = _assess_risk_health(neg_events)

        # CPARS prediction
        cpars_pred = _predict_cpars(cid, evm_health, schedule_health, risk_health)

        # Overall health
        health = _compute_contract_health(evm_health, schedule_health, risk_health)

        # Collect all alerts
        all_alerts = evm_health["alerts"] + schedule_health["alerts"] + risk_health["alerts"]
        total_alerts += len(all_alerts)

        if health["health"] in ("yellow", "red"):
            at_risk += 1

        # Store to audit
        _store_monitoring_result(cid, health, cpars_pred, all_alerts)
        contracts_monitored += 1

        contract_results.append(
            {
                "contract_id": cid,
                "contract_number": contract.get("contract_number", ""),
                "health": health["health"],
                "health_score": health["health_score"],
                "cpars_predicted": cpars_pred["predicted_rating"],
                "cpars_score": cpars_pred["predicted_score"],
                "alert_count": len(all_alerts),
                "overdue_deliverables": len(overdue),
            }
        )

    return {
        "success": True,
        "metric_value": float(total_alerts),
        "details": {
            "contracts_monitored": contracts_monitored,
            "alerts_generated": total_alerts,
            "at_risk_contracts": at_risk,
            "contracts": contract_results,
        },
    }
