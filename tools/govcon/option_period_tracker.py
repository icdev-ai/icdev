#!/usr/bin/env python3
# CUI // SP-CTI
"""Option Period Tracker — manage and monitor contract option exercise windows.

Tracks base + option periods for GovCon contracts; alerts at 90/60/30-day
exercise windows; provides AI-assisted go/no-go recommendation.
"""

import json
import os
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.option_period_tracker")

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

RISK_TIERS = {
    "critical": 30,   # ≤30 days → CAT2 escalation
    "warning":  60,   # ≤60 days → kanban high
    "watch":    90,   # ≤90 days → kanban medium
}

# Representative score for a qualitative health label, used only when no
# numeric health_score has been computed. Keeps the go/no-go assessment
# consistent with the contract's health flag instead of defaulting to RED.
_HEALTH_LABEL_SCORE = {
    "green": 0.85,
    "yellow": 0.60,
    "red": 0.30,
}

STATUS_VALUES = ("pending", "exercised", "lapsed", "waived")


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    conn.set_security_context(None)  # rls-bypass: govcon service-layer; govcon tables lack tenant_id/classification columns
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _uuid():
    return str(uuid.uuid4())


def _audit(conn, action: str, details: str = "", actor: str = "option_tracker"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (event_type, actor, action, details, session_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("hook_event_logged", actor, action, details, "option_tracker"),
        )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_audit: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


def list_option_periods(contract_id: str) -> Dict[str, Any]:
    """List all option periods for a contract, sorted by option_number."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM cpmp_option_periods WHERE contract_id = %s ORDER BY option_number ASC",
            (contract_id,),
        ).fetchall()
        options = []
        today = date.today()
        for r in rows:
            o = dict(r)
            deadline = o.get("exercise_deadline")
            if deadline and o.get("status") == "pending":
                try:
                    days = (date.fromisoformat(deadline) - today).days
                    o["days_to_deadline"] = days
                    o["risk_tier"] = _risk_tier(days)
                except Exception:
                    o["days_to_deadline"] = None
                    o["risk_tier"] = None
            else:
                o["days_to_deadline"] = None
                o["risk_tier"] = None
            options.append(o)
        return {"status": "ok", "contract_id": contract_id, "options": options, "total": len(options)}
    finally:
        conn.close()


def create_option_period(contract_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new option period record."""
    required = ("option_number", "exercise_deadline")
    missing = [f for f in required if not data.get(f)]
    if missing:
        return {"status": "error", "message": f"Missing required fields: {missing}"}

    conn = _get_db()
    try:
        # Verify contract exists
        contract = conn.execute(
            "SELECT id, contract_number FROM cpmp_contracts WHERE id = %s", (contract_id,)
        ).fetchone()
        if not contract:
            return {"status": "error", "message": f"Contract {contract_id} not found"}

        option_id = _uuid()
        conn.execute(
            """INSERT INTO cpmp_option_periods
               (id, contract_id, option_number, description, period_start, period_end,
                ceiling_value, exercise_deadline, exercise_notice_days,
                status, classification, tenant_id, metadata)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s)""",
            (
                option_id,
                contract_id,
                int(data["option_number"]),
                data.get("description", ""),
                data.get("period_start"),
                data.get("period_end"),
                float(data.get("ceiling_value", 0.0)),
                data["exercise_deadline"],
                int(data.get("exercise_notice_days", 60)),
                data.get("classification", "CUI"),
                data.get("tenant_id"),
                json.dumps(data.get("metadata", {})),
            ),
        )
        _audit(conn, "option_period_created", f"contract={contract_id} option={data['option_number']}")
        conn.commit()
        return {"status": "ok", "option_id": option_id}
    finally:
        conn.close()


def update_option_period(option_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Update mutable fields on an option period."""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM cpmp_option_periods WHERE id = %s", (option_id,)).fetchone()
        if not row:
            return {"status": "error", "message": "Option period not found"}

        allowed = ("description", "period_start", "period_end", "ceiling_value",
                   "exercise_deadline", "exercise_notice_days")
        updates = {k: v for k, v in data.items() if k in allowed and v is not None}
        if not updates:
            return {"status": "error", "message": "No valid fields to update"}

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        set_clause += ", updated_at = datetime('now')"
        conn.execute(
            f"UPDATE cpmp_option_periods SET {set_clause} WHERE id = %s",
            list(updates.values()) + [option_id],
        )
        _audit(conn, "option_period_updated", f"option_id={option_id}")
        conn.commit()
        return {"status": "ok", "option_id": option_id}
    finally:
        conn.close()


def exercise_option(option_id: str, exercised_by: str = "system") -> Dict[str, Any]:
    """Mark an option as exercised."""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM cpmp_option_periods WHERE id = %s", (option_id,)).fetchone()
        if not row:
            return {"status": "error", "message": "Option period not found"}
        if row["status"] != "pending":
            return {"status": "error", "message": f"Option is already {row['status']}"}

        today = date.today().isoformat()
        conn.execute(
            "UPDATE cpmp_option_periods SET status='exercised', exercised_date=%s, exercised_by=%s, "
            "updated_at=datetime('now') WHERE id=%s",
            (today, exercised_by, option_id),
        )
        _audit(conn, "option_exercised", f"option_id={option_id} by={exercised_by}")
        conn.commit()
        return {"status": "ok", "option_id": option_id, "exercised_date": today}
    finally:
        conn.close()


def get_portfolio_countdown() -> Dict[str, Any]:
    """Return all pending option periods sorted by exercise_deadline with risk tier."""
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT o.*, c.contract_number, c.title AS contract_title, c.agency
               FROM cpmp_option_periods o
               JOIN cpmp_contracts c ON c.id = o.contract_id
               WHERE o.status = 'pending'
               ORDER BY o.exercise_deadline ASC""",
        ).fetchall()

        today = date.today()
        countdown = []
        for r in rows:
            o = dict(r)
            try:
                days = (date.fromisoformat(o["exercise_deadline"]) - today).days
            except Exception:
                days = None
            o["days_to_deadline"] = days
            o["risk_tier"] = _risk_tier(days) if days is not None else None
            countdown.append(o)

        return {
            "status": "ok",
            "total": len(countdown),
            "critical": sum(1 for o in countdown if o["risk_tier"] == "critical"),
            "warning": sum(1 for o in countdown if o["risk_tier"] == "warning"),
            "watch": sum(1 for o in countdown if o["risk_tier"] == "watch"),
            "options": countdown,
        }
    finally:
        conn.close()


def ai_exercise_recommendation(option_id: str) -> Dict[str, Any]:
    """Generate AI go/no-go recommendation for exercising an option.

    Gathers contract health snapshot; calls LLM if available; falls back to
    deterministic rules-based assessment.
    """
    conn = _get_db()
    try:
        row = conn.execute(
            """SELECT o.*, c.contract_number, c.title AS contract_title,
                      c.health, c.health_score, c.cpars_rating_current
               FROM cpmp_option_periods o
               JOIN cpmp_contracts c ON c.id = o.contract_id
               WHERE o.id = %s""",
            (option_id,),
        ).fetchone()
        if not row:
            return {"status": "error", "message": "Option period not found"}
        option = dict(row)
        contract_id = option["contract_id"]
    finally:
        conn.close()

    # Gather context. A NULL numeric health_score must NOT be coerced to 0.0
    # (which the deterministic rules read as RED). When no score has been
    # computed yet, fall back to the qualitative health label so an unscored
    # GREEN contract is not misreported as RED.
    raw_score = option.get("health_score")
    health = option.get("health", "unknown") or "unknown"
    if raw_score is None:
        health_score = _HEALTH_LABEL_SCORE.get(str(health).lower())
    else:
        health_score = float(raw_score)
    cpars_rating = option.get("cpars_rating_current", "unknown")

    try:
        from tools.govcon.evm_engine import aggregate_contract_evm
        evm = aggregate_contract_evm(contract_id)
        indicators = evm.get("indicators", {})
        cpi = indicators.get("cpi")
        spi = indicators.get("spi")
    except Exception:
        cpi, spi = None, None

    # Try LLM
    narrative = _try_llm_recommendation(option, health_score, cpi, spi, cpars_rating)

    # Deterministic fallback
    if not narrative:
        narrative = _deterministic_recommendation(health_score, cpi, spi, cpars_rating, option)

    # Persist recommendation
    conn2 = _get_db()
    try:
        ts = datetime.now(timezone.utc).isoformat()
        conn2.execute(
            "UPDATE cpmp_option_periods SET ai_recommendation=%s, ai_recommendation_ts=%s, "
            "updated_at=datetime('now') WHERE id=%s",
            (narrative, ts, option_id),
        )
        conn2.commit()
    finally:
        conn2.close()

    return {
        "status": "ok",
        "option_id": option_id,
        "recommendation": narrative,
        "context": {
            "health": health,
            "health_score": health_score,
            "cpi": cpi,
            "spi": spi,
            "cpars_rating": cpars_rating,
        },
    }


def _risk_tier(days: Optional[int]) -> Optional[str]:
    if days is None:
        return None
    if days <= RISK_TIERS["critical"]:
        return "critical"
    if days <= RISK_TIERS["warning"]:
        return "warning"
    if days <= RISK_TIERS["watch"]:
        return "watch"
    return None


def _try_llm_recommendation(option, health_score, cpi, spi, cpars_rating) -> Optional[str]:
    try:
        from tools.llm.router import LLMRouter, LLMRequest

        router = LLMRouter()
        prompt = (
            f"You are a Federal PMO advisor. Provide a concise go/no-go recommendation "
            f"for exercising Option {option['option_number']} on contract "
            f"{option.get('contract_number', 'N/A')} ({option.get('contract_title', 'N/A')}).\n\n"
            f"Contract health: {option.get('health','unknown')} "
            f"(score {f'{health_score:.2f}/1.0' if health_score is not None else 'not yet computed'})\n"
            f"CPI: {cpi if cpi else 'N/A'} | SPI: {spi if spi else 'N/A'}\n"
            f"Current CPARS rating: {cpars_rating}\n"
            f"Option ceiling: ${option.get('ceiling_value', 0):,.0f}\n"
            f"Exercise deadline: {option.get('exercise_deadline')}\n\n"
            f"In 3-4 sentences, state whether to exercise, key risk factors, and any conditions."
        )
        resp = router.route(LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            function="pmo_corrective_action",
        ))
        return resp.content if resp else None
    except Exception:
        return None


def _deterministic_recommendation(health_score, cpi, spi, cpars_rating, option) -> str:
    lines = []
    if health_score is None:
        lines.append(
            "Contract health has not been scored — insufficient data for a "
            "data-driven go/no-go. Verify funding, CPARS, and COR performance "
            "confirmation before exercising this option."
        )
    elif health_score >= 0.75:
        lines.append("Contract health is GREEN — recommend exercising this option.")
    elif health_score >= 0.50:
        lines.append("Contract health is YELLOW — exercise with remediation conditions.")
    else:
        lines.append("Contract health is RED — exercise with caution; consider cure notice first.")

    if cpi is not None:
        if cpi < 0.85:
            lines.append(f"Cost performance (CPI {cpi:.2f}) is below red threshold — budget overrun risk is high.")
        elif cpi < 0.95:
            lines.append(f"Cost performance (CPI {cpi:.2f}) is trending yellow — monitor closely in option period.")
        else:
            lines.append(f"Cost performance (CPI {cpi:.2f}) is healthy.")

    if spi is not None and spi < 0.90:
        lines.append(f"Schedule performance (SPI {spi:.2f}) indicates schedule slip — add recovery milestones to option SOW.")

    if cpars_rating in ("marginal", "unsatisfactory"):
        lines.append(f"Current CPARS rating ({cpars_rating}) is below acceptable — corrective action plan required before option exercise.")

    return " ".join(lines)


if __name__ == "__main__":
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(description="Option Period Tracker CLI")
    parser.add_argument("--list", metavar="CONTRACT_ID")
    parser.add_argument("--countdown", action="store_true")
    parser.add_argument("--recommend", metavar="OPTION_ID")
    parser.add_argument("--exercise", metavar="OPTION_ID")
    parser.add_argument("--by", default="cli", help="Actor for exercise")
    args = parser.parse_args()

    if args.list:
        print(_json.dumps(list_option_periods(args.list), indent=2, default=str))
    elif args.countdown:
        print(_json.dumps(get_portfolio_countdown(), indent=2, default=str))
    elif args.recommend:
        print(_json.dumps(ai_exercise_recommendation(args.recommend), indent=2, default=str))
    elif args.exercise:
        print(_json.dumps(exercise_option(args.exercise, args.by), indent=2, default=str))
    else:
        parser.print_help()
