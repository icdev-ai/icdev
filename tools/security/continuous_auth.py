#!/usr/bin/env python3
# CUI // SP-CTI
"""Continuous Authentication — session risk scoring and step-up trigger (G-14).

Computes a risk score for an active session based on observable signals
(session age, IP address change, anomalous request rate) and determines
whether a step-up authentication challenge is required.

Public API:
    assess_session_risk(user_id, request_context) -> RiskScore
    record_risk_event(user_id, risk) -> None
    check_step_up_required() -> bool

NIST 800-53: IA-11, AC-12, AC-11
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid

from tools.logging.icdev_logger import get_logger

logger = get_logger("security.continuous_auth")

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (ValueError, TypeError):
        return default


def _env_bool(name: str, default: bool = True) -> bool:
    val = os.environ.get(name, "").lower().strip()
    if val == "":
        return default
    return val in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# RiskScore dataclass
# ---------------------------------------------------------------------------


@dataclass
class RiskScore:
    score: float
    factors: list[str] = field(default_factory=list)
    require_step_up: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "factors": self.factors,
            "require_step_up": self.require_step_up,
        }


# ---------------------------------------------------------------------------
# Core risk assessment
# ---------------------------------------------------------------------------


def assess_session_risk(user_id: str, request_context: dict) -> RiskScore:
    """Compute a composite risk score for the current session.

    Risk factors (additive, capped per factor):
      - Session age:       up to 0.5  (proportional to max_age_minutes)
      - IP address change: 0.3        (if ip differs from session baseline)
      - Anomalous rate:   0.2         (if request_rate_per_minute > threshold)

    Args:
        user_id: Authenticated user identifier.
        request_context: Dict with optional keys:
            ip_address (str), session_created_at (ISO 8601 str),
            session_ip (str), request_rate_per_minute (float), path (str).

    Returns:
        RiskScore with aggregated score and contributing factors.
    """
    if not _env_bool("ICDEV_CONTINUOUS_AUTH_ENABLED", True):
        logger.debug("Continuous auth disabled — returning zero risk")
        return RiskScore(score=0.0, factors=[], require_step_up=False)

    max_age_minutes = _env_float("ICDEV_SESSION_MAX_AGE_MINUTES", 480.0)
    step_up_threshold = _env_float("ICDEV_STEP_UP_RISK_THRESHOLD", 0.7)
    rate_threshold = _env_float("ICDEV_STEP_UP_RATE_THRESHOLD", 60.0)

    score = 0.0
    factors: list[str] = []

    # --- Factor 1: Session age ---
    session_created_at_raw: str = request_context.get("session_created_at", "") or ""
    if session_created_at_raw:
        try:
            created_dt = datetime.fromisoformat(session_created_at_raw)
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            now_utc = datetime.now(timezone.utc)
            age_minutes = max(0.0, (now_utc - created_dt).total_seconds() / 60.0)
            age_contribution = min(1.0, age_minutes / max(1.0, max_age_minutes)) * 0.5
            if age_contribution > 0:
                score += age_contribution
                factors.append(f"session age {age_minutes:.1f}m of max {max_age_minutes:.0f}m")
        except (ValueError, OverflowError) as exc:
            logger.warning(
                "Could not parse session_created_at",
                extra={"extra": {"value": session_created_at_raw, "error": str(exc)}},
            )

    # --- Factor 2: IP address change ---
    ip_address: str = request_context.get("ip_address", "") or ""
    session_ip: str = request_context.get("session_ip", "") or ""
    if ip_address and session_ip and ip_address != session_ip:
        score += 0.3
        factors.append("IP address changed")
        logger.info(
            "IP address change detected",
            extra={"extra": {"user_id": user_id, "session_ip": session_ip, "request_ip": ip_address}},
        )

    # --- Factor 3: Anomalous request rate ---
    try:
        request_rate: float = float(request_context.get("request_rate_per_minute", 0.0) or 0.0)
    except (ValueError, TypeError):
        request_rate = 0.0
    if request_rate > rate_threshold:
        score += 0.2
        factors.append("anomalous request rate")
        logger.info(
            "Anomalous request rate detected",
            extra={"extra": {"user_id": user_id, "rate": request_rate, "threshold": rate_threshold}},
        )

    require_step_up = score >= step_up_threshold
    risk = RiskScore(score=round(score, 4), factors=factors, require_step_up=require_step_up)

    logger.debug(
        "Session risk assessed",
        extra={
            "extra": {
                "user_id": user_id,
                "score": risk.score,
                "factors": risk.factors,
                "require_step_up": risk.require_step_up,
            }
        },
    )
    return risk


# ---------------------------------------------------------------------------
# Risk event persistence
# ---------------------------------------------------------------------------

_SESSION_RISK_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS session_risk_log (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'risk_assessment',
    risk_score REAL NOT NULL,
    details TEXT,
    recorded_at TEXT NOT NULL
)
"""


def record_risk_event(user_id: str, risk: RiskScore) -> None:
    """Persist a risk assessment event to session_risk_log (best-effort).

    Uses get_connection() from tools.db.storage. Silently swallows all
    errors so that a DB outage never blocks the authentication path.
    """
    try:
        from tools.db.storage import get_connection  # type: ignore[import-untyped]
    except ImportError:
        logger.debug("tools.db.storage not available; skipping risk event persistence")
        return

    try:
        conn = get_connection()
        conn.execute(_SESSION_RISK_TABLE_DDL)
        conn.execute(
            "INSERT INTO session_risk_log (id, user_id, event_type, risk_score, details, recorded_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                str(uuid.uuid4()),
                user_id,
                "risk_assessment",
                risk.score,
                json.dumps(risk.to_dict()),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        logger.debug(
            "Risk event recorded",
            extra={"extra": {"user_id": user_id, "score": risk.score}},
        )
    except Exception as exc:
        logger.warning(
            "Failed to record risk event (best-effort, continuing)",
            extra={"extra": {"user_id": user_id, "error": str(exc)}},
        )


# ---------------------------------------------------------------------------
# Flask integration helper (no module-level Flask import)
# ---------------------------------------------------------------------------


def check_step_up_required() -> bool:
    """Return True if the current Flask request context signals step-up is needed.

    Reads ``g.require_step_up`` when inside a Flask request context.
    Returns False safely when called outside a request context.
    """
    try:
        from flask import g  # type: ignore[import-untyped]
        return bool(getattr(g, "require_step_up", False))
    except RuntimeError:
        # No active Flask application / request context
        return False
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# CLI (optional standalone runner)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Continuous Auth — session risk scorer")
    parser.add_argument("--user-id", default="test_user", help="User ID")
    parser.add_argument("--ip", default="", help="Request IP address")
    parser.add_argument("--session-ip", default="", help="Session baseline IP")
    parser.add_argument("--session-created-at", default="", help="Session creation ISO timestamp")
    parser.add_argument("--rate", type=float, default=0.0, help="Request rate per minute")
    parser.add_argument("--json", dest="as_json", action="store_true", help="JSON output")
    args = parser.parse_args()

    ctx: dict[str, Any] = {}
    if args.ip:
        ctx["ip_address"] = args.ip
    if args.session_ip:
        ctx["session_ip"] = args.session_ip
    if args.session_created_at:
        ctx["session_created_at"] = args.session_created_at
    if args.rate:
        ctx["request_rate_per_minute"] = args.rate

    result = assess_session_risk(args.user_id, ctx)
    if args.as_json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"Risk score: {result.score:.4f}")
        print(f"Factors:    {', '.join(result.factors) or 'none'}")
        print(f"Step-up:    {result.require_step_up}")
