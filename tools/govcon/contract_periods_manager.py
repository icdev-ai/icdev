# CUI // SP-CTI
# ICDEV™ GovProposal — Contract Periods Manager (Phase 60, D-CPMP-10)
# Base+option period structure and funding/obligation tracking.

"""
Contract Periods Manager — period-of-performance structure and obligation tracking.

Key functions:
    - create_period()         — add a base or option period to a contract
    - list_periods()          — list all periods with computed remaining_obligation
    - exercise_option()       — mark an option period exercised, roll up contract totals
    - get_obligation_summary()— burn-rate vs obligation roll-up for a contract

Period types: base | option_1 | option_2 | option_3 | option_4 | option_5
Period statuses: active | exercised | unexercised | expired

Usage:
    python tools/govcon/contract_periods_manager.py --list --contract-id <id> --json
    python tools/govcon/contract_periods_manager.py --summary --contract-id <id> --json
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.contract_periods_manager")

PERIOD_TYPES = ("base", "option_1", "option_2", "option_3", "option_4", "option_5")
PERIOD_STATUSES = ("active", "exercised", "unexercised", "expired")

_OPTION_NUMBER = {t: i for i, t in enumerate(PERIOD_TYPES)}


def _get_db():
    conn = get_connection()
    conn.set_security_context(None)  # rls-bypass: cpmp tables use CUI universally; RLS JOIN injection breaks subqueries
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


def _audit(conn, action, details="", actor="contract_periods_manager"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (event_type, actor, action, details, session_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("hook_event_logged", actor, action, details, "cpmp"),
        )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_audit: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


def _get_billed(conn, contract_id: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(billed_value), 0) as billed FROM cpmp_clins WHERE contract_id = %s",
        (contract_id,),
    ).fetchone()
    return float(row["billed"]) if row else 0.0


# ── CRUD ─────────────────────────────────────────────────────────────


def create_period(
    contract_id: str,
    period_type: str,
    pop_start: str | None = None,
    pop_end: str | None = None,
    obligated_value: float = 0.0,
    funded_value: float = 0.0,
    ceiling_value: float = 0.0,
    notes: str | None = None,
    created_by: str | None = None,
) -> dict:
    """Create a period of performance record for a contract.

    period_type must be one of: base, option_1, option_2, option_3, option_4, option_5.
    The base period is automatically set to 'active'; options default to 'unexercised'.
    """
    if period_type not in PERIOD_TYPES:
        return {"status": "error", "message": f"period_type must be one of {PERIOD_TYPES}"}

    conn = _get_db()
    contract = conn.execute("SELECT id FROM cpmp_contracts WHERE id = %s", (contract_id,)).fetchone()
    if not contract:
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    # Reject duplicate period_type for the same contract
    existing = conn.execute(
        "SELECT id FROM cpmp_contract_periods WHERE contract_id = %s AND period_type = %s",
        (contract_id, period_type),
    ).fetchone()
    if existing:
        conn.close()
        return {"status": "error", "message": f"Period '{period_type}' already exists for contract {contract_id}"}

    period_id = _uuid()
    option_number = _OPTION_NUMBER.get(period_type, 0)
    status = "active" if period_type == "base" else "unexercised"

    conn.execute(
        "INSERT INTO cpmp_contract_periods "
        "(id, contract_id, period_type, option_number, pop_start, pop_end, "
        "obligated_value, funded_value, ceiling_value, status, notes, "
        "created_at, updated_at, classification) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            period_id, contract_id, period_type, option_number,
            pop_start, pop_end, obligated_value, funded_value, ceiling_value,
            status, notes, _now(), _now(), "CUI",
        ),
    )

    # If base period, update the contract's own pop_start/pop_end and period_type
    if period_type == "base":
        conn.execute(
            "UPDATE cpmp_contracts SET period_type = %s, option_number = %s, "
            "pop_start = COALESCE(pop_start, %s), pop_end = COALESCE(pop_end, %s), "
            "updated_at = %s WHERE id = %s",
            ("base", 0, pop_start, pop_end, _now(), contract_id),
        )

    _audit(conn, "create_period", f"contract={contract_id} type={period_type} id={period_id}", actor=created_by or "system")
    conn.commit()
    conn.close()

    return {"status": "ok", "period_id": period_id, "period_type": period_type, "contract_id": contract_id}


def list_periods(contract_id: str) -> dict:
    """List all periods for a contract with computed remaining_obligation."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM cpmp_contract_periods WHERE contract_id = %s ORDER BY option_number ASC",
        (contract_id,),
    ).fetchall()
    billed = _get_billed(conn, contract_id)
    conn.close()

    periods = []
    for r in rows:
        p = dict(r)
        obligated = float(p.get("obligated_value") or 0)
        p["remaining_obligation"] = round(obligated - billed, 2)
        p["burn_rate_pct"] = round(billed / obligated * 100, 1) if obligated > 0 else 0.0
        periods.append(p)

    return {"status": "ok", "contract_id": contract_id, "total": len(periods), "periods": periods}


def get_period(period_id: str) -> dict:
    """Fetch a single period."""
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM cpmp_contract_periods WHERE id = %s", (period_id,)
    ).fetchone()
    conn.close()
    if not row:
        return {"status": "error", "message": f"Period {period_id} not found"}
    return {"status": "ok", "period": dict(row)}


def exercise_option(period_id: str, obligated_value: float, exercised_by: str | None = None) -> dict:
    """Exercise an option period — mark as exercised and roll up contract obligated_value.

    Also updates the contract's period_type and option_number to reflect the new active period.
    """
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM cpmp_contract_periods WHERE id = %s", (period_id,)
    ).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Period {period_id} not found"}
    if row["status"] == "exercised":
        conn.close()
        return {"status": "error", "message": "Option already exercised"}
    if row["period_type"] == "base":
        conn.close()
        return {"status": "error", "message": "Cannot exercise base period"}

    now = _now()
    conn.execute(
        "UPDATE cpmp_contract_periods SET status = 'exercised', obligated_value = %s, "
        "exercised_at = %s, exercised_by = %s, updated_at = %s WHERE id = %s",
        (obligated_value, now, exercised_by, now, period_id),
    )

    # Roll up: contract.obligated_value = SUM(exercised periods obligated_value) + base obligated_value
    sum_row = conn.execute(
        "SELECT COALESCE(SUM(obligated_value), 0) as total "
        "FROM cpmp_contract_periods "
        "WHERE contract_id = %s AND status IN ('active', 'exercised')",
        (row["contract_id"],),
    ).fetchone()
    total_obligated = float(sum_row["total"]) if sum_row else obligated_value

    conn.execute(
        "UPDATE cpmp_contracts SET obligated_value = %s, period_type = %s, option_number = %s, updated_at = %s WHERE id = %s",
        (total_obligated, row["period_type"], row["option_number"], now, row["contract_id"]),
    )

    _audit(
        conn,
        "exercise_option",
        f"period={period_id} type={row['period_type']} obligated={obligated_value}",
        actor=exercised_by or "system",
    )
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "period_id": period_id,
        "period_type": row["period_type"],
        "contract_id": row["contract_id"],
        "obligated_value": obligated_value,
        "contract_obligated_value": total_obligated,
    }


# ── Obligation Summary ────────────────────────────────────────────────


def get_obligation_summary(contract_id: str) -> dict:
    """Compute burn-rate vs obligation for a contract.

    Returns:
        total_obligated   — sum of obligated_value across exercised+active periods
        total_billed      — sum of cpmp_clins.billed_value for this contract
        remaining_obligation — total_obligated - total_billed
        burn_rate_pct     — total_billed / total_obligated * 100
        by_period         — per-period obligation breakdown
    """
    conn = _get_db()

    contract = conn.execute(
        "SELECT id, obligated_value, funded_value, total_value, billed_value FROM cpmp_contracts WHERE id = %s",
        (contract_id,),
    ).fetchone()
    if not contract:
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    billed = _get_billed(conn, contract_id)
    # Prefer obligated_value from contract roll-up; fall back to funded_value
    contract_obligated = float(contract["obligated_value"] or contract["funded_value"] or 0)

    periods_raw = conn.execute(
        "SELECT id, period_type, option_number, pop_start, pop_end, "
        "obligated_value, funded_value, ceiling_value, status "
        "FROM cpmp_contract_periods WHERE contract_id = %s ORDER BY option_number ASC",
        (contract_id,),
    ).fetchall()
    conn.close()

    # If periods exist, use their sum; otherwise fall back to contract-level value
    if periods_raw:
        total_obligated = sum(
            float(p["obligated_value"] or 0)
            for p in periods_raw
            if p["status"] in ("active", "exercised")
        ) or contract_obligated
    else:
        total_obligated = contract_obligated

    remaining = total_obligated - billed
    burn_rate = round(billed / total_obligated * 100, 1) if total_obligated > 0 else 0.0

    by_period = []
    for p in periods_raw:
        p_oblig = float(p["obligated_value"] or 0)
        by_period.append({
            "id": p["id"],
            "period_type": p["period_type"],
            "option_number": p["option_number"],
            "pop_start": p["pop_start"],
            "pop_end": p["pop_end"],
            "obligated_value": p_oblig,
            "funded_value": float(p["funded_value"] or 0),
            "ceiling_value": float(p["ceiling_value"] or 0),
            "status": p["status"],
        })

    return {
        "status": "ok",
        "contract_id": contract_id,
        "total_obligated": round(total_obligated, 2),
        "total_billed": round(billed, 2),
        "remaining_obligation": round(remaining, 2),
        "burn_rate_pct": burn_rate,
        "by_period": by_period,
    }


# ── CLI ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="ICDEV™ Contract Periods Manager (Phase 60, D-CPMP-10)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List periods for a contract")
    group.add_argument("--summary", action="store_true", help="Get obligation burn-rate summary")
    group.add_argument("--create", action="store_true", help="Create a period")
    group.add_argument("--exercise", action="store_true", help="Exercise an option period")

    parser.add_argument("--contract-id")
    parser.add_argument("--period-id")
    parser.add_argument("--period-type", choices=list(PERIOD_TYPES))
    parser.add_argument("--pop-start")
    parser.add_argument("--pop-end")
    parser.add_argument("--obligated-value", type=float, default=0.0)
    parser.add_argument("--funded-value", type=float, default=0.0)
    parser.add_argument("--ceiling-value", type=float, default=0.0)
    parser.add_argument("--notes")
    parser.add_argument("--exercised-by")
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.list:
        if not args.contract_id:
            import sys; print("Error: --contract-id required", file=sys.stderr); sys.exit(1)
        result = list_periods(args.contract_id)
    elif args.summary:
        if not args.contract_id:
            import sys; print("Error: --contract-id required", file=sys.stderr); sys.exit(1)
        result = get_obligation_summary(args.contract_id)
    elif args.create:
        if not args.contract_id or not args.period_type:
            import sys; print("Error: --contract-id and --period-type required", file=sys.stderr); sys.exit(1)
        result = create_period(
            args.contract_id, args.period_type,
            pop_start=args.pop_start, pop_end=args.pop_end,
            obligated_value=args.obligated_value, funded_value=args.funded_value,
            ceiling_value=args.ceiling_value, notes=args.notes,
        )
    elif args.exercise:
        if not args.period_id:
            import sys; print("Error: --period-id required", file=sys.stderr); sys.exit(1)
        result = exercise_option(args.period_id, args.obligated_value, args.exercised_by)
    else:
        result = {"status": "error", "message": "Unknown command"}

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
