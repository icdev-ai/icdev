# CUI // SP-CTI
# ICDEV GovProposal — Portfolio Manager (Phase 60, D-CPMP-8, D-CPMP-9)
# Portfolio dashboard, contract health scoring, proposal-to-contract transition bridge.

"""
Portfolio Manager — Portfolio summary, health scoring, and proposal→contract transition.

Key functions:
    - get_portfolio_summary(): Aggregate stats across all contracts
    - compute_contract_health(contract_id): Weighted health score (D-CPMP-8, D21)
    - transition_from_opportunity(opp_id): Create contract from won proposal (D-CPMP-9)

Health weights (configurable in args/govcon_config.yaml):
    EVM 0.30 + deliverables 0.25 + CPARS 0.20 + negative_events 0.15 + funding 0.10

Usage:
    python tools/govcon/portfolio_manager.py --portfolio --json
    python tools/govcon/portfolio_manager.py --health --contract-id <id> --json
    python tools/govcon/portfolio_manager.py --transition --opportunity-id <id> --json
"""

import argparse
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))
_CONFIG_PATH = _ROOT / "args" / "govcon_config.yaml"


def _load_config():
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f).get("cpmp", {})
    return {}


_CFG = _load_config()

HEALTH_WEIGHTS = _CFG.get("health_weights", {
    "evm": 0.30,
    "deliverables": 0.25,
    "cpars": 0.20,
    "negative_events": 0.15,
    "funding": 0.10,
})

EVM_CFG = _CFG.get("evm", {})
CPI_YELLOW = EVM_CFG.get("cpi_yellow_threshold", 0.95)
CPI_RED = EVM_CFG.get("cpi_red_threshold", 0.85)
SPI_YELLOW = EVM_CFG.get("spi_yellow_threshold", 0.95)
SPI_RED = EVM_CFG.get("spi_red_threshold", 0.85)


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


def _audit(conn, action, details="", actor="portfolio_manager"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (id, timestamp, event_type, actor, action, details, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_uuid(), _now(), "cpmp.portfolio_manager", actor, action, details, "cpmp"),
        )
    except Exception:
        pass


def _record_status_change(conn, entity_type, entity_id, old_status, new_status, changed_by=None, reason=None):
    conn.execute(
        "INSERT INTO cpmp_status_history (id, entity_type, entity_id, old_status, new_status, changed_by, reason, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (_uuid(), entity_type, entity_id, old_status, new_status, changed_by, reason, _now()),
    )


# ── Health Scoring ───────────────────────────────────────────────────

def _score_evm(conn, contract_id):
    """EVM dimension: latest CPI and SPI → 0.0-1.0 score."""
    row = conn.execute(
        "SELECT cpi, spi FROM cpmp_evm_periods WHERE contract_id = ? ORDER BY period_date DESC LIMIT 1",
        (contract_id,),
    ).fetchone()
    if not row or row["cpi"] is None:
        return 1.0  # no EVM data → assume healthy

    cpi = row["cpi"] or 1.0
    spi = row["spi"] or 1.0

    # Score: 1.0 if both >= yellow, 0.5 if yellow zone, 0.0 if red zone
    cpi_score = 1.0 if cpi >= CPI_YELLOW else (0.5 if cpi >= CPI_RED else max(0.0, cpi / CPI_RED * 0.5))
    spi_score = 1.0 if spi >= SPI_YELLOW else (0.5 if spi >= SPI_RED else max(0.0, spi / SPI_RED * 0.5))
    return (cpi_score + spi_score) / 2.0


def _score_deliverables(conn, contract_id):
    """Deliverables dimension: ratio of on-time/accepted vs overdue/rejected."""
    total = conn.execute(
        "SELECT COUNT(*) FROM cpmp_deliverables WHERE contract_id = ?", (contract_id,)
    ).fetchone()[0]
    if total == 0:
        return 1.0

    overdue = conn.execute(
        "SELECT COUNT(*) FROM cpmp_deliverables WHERE contract_id = ? AND status = 'overdue'",
        (contract_id,),
    ).fetchone()[0]
    rejected = conn.execute(
        "SELECT COUNT(*) FROM cpmp_deliverables WHERE contract_id = ? AND status = 'rejected'",
        (contract_id,),
    ).fetchone()[0]

    bad = overdue + rejected
    return max(0.0, 1.0 - (bad / total))


def _score_cpars(conn, contract_id):
    """CPARS dimension: latest overall rating or 1.0 if no assessment."""
    rating_scores = {
        "exceptional": 1.0, "very_good": 0.85, "satisfactory": 0.65,
        "marginal": 0.40, "unsatisfactory": 0.15,
    }
    row = conn.execute(
        "SELECT overall_rating FROM cpmp_cpars_assessments WHERE contract_id = ? ORDER BY period_end DESC LIMIT 1",
        (contract_id,),
    ).fetchone()
    if not row or row["overall_rating"] is None:
        return 1.0
    rating = row["overall_rating"]
    if isinstance(rating, str):
        return rating_scores.get(rating, 0.65)
    return min(1.0, max(0.0, float(rating)))


def _score_negative_events(conn, contract_id):
    """Negative events dimension: penalize for open/in-progress events."""
    open_events = conn.execute(
        "SELECT COUNT(*) FROM cpmp_negative_events "
        "WHERE contract_id = ? AND corrective_action_status IN ('open', 'in_progress')",
        (contract_id,),
    ).fetchone()[0]

    critical = conn.execute(
        "SELECT COUNT(*) FROM cpmp_negative_events "
        "WHERE contract_id = ? AND severity = 'critical' AND corrective_action_status IN ('open', 'in_progress')",
        (contract_id,),
    ).fetchone()[0]

    # Each open event reduces score by 0.1, critical by 0.2
    penalty = (open_events - critical) * 0.1 + critical * 0.2
    return max(0.0, 1.0 - penalty)


def _score_funding(conn, contract_id):
    """Funding dimension: funded_value / total_value ratio."""
    row = conn.execute(
        "SELECT total_value, funded_value FROM cpmp_contracts WHERE id = ?",
        (contract_id,),
    ).fetchone()
    if not row or not row["total_value"] or row["total_value"] == 0:
        return 1.0

    # Aggregate billed_value from CLINs (billed_value lives on cpmp_clins, not contracts)
    billed_row = conn.execute(
        "SELECT COALESCE(SUM(billed_value), 0) as billed FROM cpmp_clins WHERE contract_id = ?",
        (contract_id,),
    ).fetchone()
    billed = billed_row["billed"] if billed_row else 0

    funded_ratio = (row["funded_value"] or 0) / row["total_value"]
    billed_ratio = billed / max(row["funded_value"] or 1, 1)

    # Score: high if well-funded and not over-burned
    score = funded_ratio * 0.6 + max(0.0, 1.0 - billed_ratio) * 0.4
    return min(1.0, max(0.0, score))


def compute_contract_health(contract_id):
    """Compute deterministic weighted health score (D-CPMP-8, D21).

    Returns 0.0-1.0 score and green/yellow/red classification.
    """
    conn = _get_db()
    row = conn.execute("SELECT id FROM cpmp_contracts WHERE id = ?", (contract_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    scores = {
        "evm": _score_evm(conn, contract_id),
        "deliverables": _score_deliverables(conn, contract_id),
        "cpars": _score_cpars(conn, contract_id),
        "negative_events": _score_negative_events(conn, contract_id),
        "funding": _score_funding(conn, contract_id),
    }

    weighted = sum(scores[dim] * HEALTH_WEIGHTS.get(dim, 0) for dim in scores)
    health = "green" if weighted >= 0.75 else ("yellow" if weighted >= 0.50 else "red")

    # Update contract health
    conn.execute(
        "UPDATE cpmp_contracts SET health = ?, updated_at = ? WHERE id = ?",
        (health, _now(), contract_id),
    )
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "contract_id": contract_id,
        "health": health,
        "health_score": round(weighted, 4),
        "dimension_scores": {k: round(v, 4) for k, v in scores.items()},
        "weights": HEALTH_WEIGHTS,
    }


# ── Portfolio Summary ────────────────────────────────────────────────

def get_portfolio_summary():
    """Aggregate portfolio statistics across all contracts."""
    conn = _get_db()

    total = conn.execute("SELECT COUNT(*) FROM cpmp_contracts").fetchone()[0]
    active = conn.execute("SELECT COUNT(*) FROM cpmp_contracts WHERE status = 'active'").fetchone()[0]

    value_row = conn.execute(
        "SELECT COALESCE(SUM(c.total_value), 0) as total_val, "
        "COALESCE(SUM(c.funded_value), 0) as funded_val, "
        "COALESCE((SELECT SUM(cl.billed_value) FROM cpmp_clins cl "
        "JOIN cpmp_contracts cc ON cl.contract_id = cc.id "
        "WHERE cc.status IN ('active', 'option_pending')), 0) as billed_val "
        "FROM cpmp_contracts c WHERE c.status IN ('active', 'option_pending')"
    ).fetchone()

    overdue = conn.execute(
        "SELECT COUNT(*) FROM cpmp_deliverables d "
        "JOIN cpmp_contracts c ON d.contract_id = c.id "
        "WHERE d.status = 'overdue' AND c.status IN ('active', 'option_pending')"
    ).fetchone()[0]

    at_risk = conn.execute(
        "SELECT COUNT(*) FROM cpmp_contracts WHERE health = 'red' AND status IN ('active', 'option_pending')"
    ).fetchone()[0]

    # Health distribution
    health_dist = {}
    for h_row in conn.execute(
        "SELECT health, COUNT(*) as cnt FROM cpmp_contracts "
        "WHERE status IN ('active', 'option_pending') GROUP BY health"
    ).fetchall():
        health_dist[h_row["health"]] = h_row["cnt"]

    # Upcoming deliverables (next 30 days) — compute days_until_due
    upcoming = conn.execute(
        "SELECT d.*, c.contract_number, c.title as contract_title, "
        "CAST(julianday(d.due_date) - julianday('now') AS INTEGER) as days_until_due "
        "FROM cpmp_deliverables d JOIN cpmp_contracts c ON d.contract_id = c.id "
        "WHERE d.due_date BETWEEN date('now') AND date('now', '+30 days') "
        "AND d.status NOT IN ('accepted', 'rejected') "
        "ORDER BY d.due_date ASC LIMIT 20"
    ).fetchall()

    # Contract list for table — include latest CPI/SPI via LEFT JOIN
    contracts_raw = conn.execute(
        "SELECT c.id, c.contract_number, c.title, c.agency, c.contract_type, "
        "c.status, c.health, c.total_value, c.funded_value, c.pop_start, c.pop_end, "
        "c.cpars_rating_current, c.updated_at, "
        "evm.cpi, evm.spi "
        "FROM cpmp_contracts c "
        "LEFT JOIN ("
        "  SELECT contract_id, cpi, spi, "
        "  ROW_NUMBER() OVER (PARTITION BY contract_id ORDER BY period_date DESC) as rn "
        "  FROM cpmp_evm_periods"
        ") evm ON evm.contract_id = c.id AND evm.rn = 1 "
        "ORDER BY c.updated_at DESC"
    ).fetchall()

    # Build contract dicts with value alias for template
    contracts = []
    for c in contracts_raw:
        cd = dict(c)
        cd["value"] = cd.get("total_value", 0) or 0
        contracts.append(cd)

    burn_rate = (value_row["billed_val"] / max(value_row["funded_val"], 1)) * 100 if value_row["funded_val"] else 0

    # Ensure health_distribution always has all 3 keys
    for key in ("green", "yellow", "red"):
        health_dist.setdefault(key, 0)

    conn.close()
    return {
        "status": "ok",
        "portfolio": {
            "total_contracts": total,
            "active_contracts": active,
            "total_value": value_row["total_val"],
            "funded_value": value_row["funded_val"],
            "billed_value": value_row["billed_val"],
            "burn_rate_pct": round(burn_rate, 1),
            "overdue_deliverables": overdue,
            "at_risk_contracts": at_risk,
            "health_distribution": health_dist,
            "upcoming_deliverables": [dict(u) for u in upcoming],
            "contracts": contracts,
        },
    }


# ── Proposal → Contract Transition Bridge (D-CPMP-9) ────────────────

def transition_from_opportunity(opportunity_id, created_by=None):
    """Create a contract from a won proposal opportunity.

    Steps:
    1. Load opportunity data
    2. Create cpmp_contracts row
    3. Link opportunity_id and customer_delivery_id
    4. Seed deliverables from compliance matrix CDRLs
    5. Create initial WBS from proposal volumes
    6. Record transition in status history + audit trail
    """
    conn = _get_db()

    # 1. Load opportunity
    opp = conn.execute(
        "SELECT * FROM proposal_opportunities WHERE id = ?", (opportunity_id,)
    ).fetchone()
    if not opp:
        conn.close()
        return {"status": "error", "message": f"Opportunity {opportunity_id} not found"}
    if opp["status"] != "won":
        conn.close()
        return {"status": "error", "message": f"Opportunity status is '{opp['status']}', must be 'won'"}

    # Check not already transitioned
    existing = conn.execute(
        "SELECT id FROM cpmp_contracts WHERE opportunity_id = ?", (opportunity_id,)
    ).fetchone()
    if existing:
        conn.close()
        return {"status": "error", "message": f"Contract already exists for this opportunity: {existing['id']}"}

    # 2. Create contract
    contract_id = _uuid()
    conn.execute(
        "INSERT INTO cpmp_contracts "
        "(id, contract_number, title, agency, naics_code, contract_type, "
        "status, opportunity_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            contract_id,
            f"TBD-{opp['solicitation_number'] or opportunity_id[:8]}",
            opp["title"],
            opp["agency"],
            opp["naics_code"],
            "FFP",  # default, user updates later
            "draft",
            opportunity_id,
            _now(), _now(),
        ),
    )
    _record_status_change(conn, "contract", contract_id, None, "draft", created_by or "system",
                          f"Created from opportunity {opportunity_id}")

    # 3. Link opportunity → contract
    try:
        conn.execute("UPDATE proposal_opportunities SET contract_id = ? WHERE id = ?",
                      (contract_id, opportunity_id))
    except Exception:
        pass  # column may not exist yet

    # Link customer delivery if exists
    try:
        delivery = conn.execute(
            "SELECT id FROM customer_deliveries WHERE opportunity_id = ?", (opportunity_id,)
        ).fetchone()
        if delivery:
            conn.execute("UPDATE customer_deliveries SET contract_id = ? WHERE id = ?",
                          (contract_id, delivery["id"]))
            conn.execute("UPDATE cpmp_contracts SET customer_delivery_id = ? WHERE id = ?",
                          (delivery["id"], contract_id))
    except Exception:
        pass

    # 4. Seed deliverables from compliance matrix CDRLs
    deliverables_seeded = 0
    try:
        cdrl_items = conn.execute(
            "SELECT * FROM proposal_compliance_matrix "
            "WHERE opportunity_id = ? AND requirement_type = 'cdrl'",
            (opportunity_id,)
        ).fetchall()
        for item in cdrl_items:
            deliv_id = _uuid()
            conn.execute(
                "INSERT INTO cpmp_deliverables "
                "(id, contract_id, cdrl_number, title, description, deliverable_type, "
                "status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    deliv_id, contract_id,
                    item["requirement_id"] if "requirement_id" in item.keys() else None,
                    item["requirement_text"][:200] if "requirement_text" in item.keys() else "CDRL",
                    item["requirement_text"] if "requirement_text" in item.keys() else None,
                    "cdrl", "not_started", _now(), _now(),
                ),
            )
            _record_status_change(conn, "deliverable", deliv_id, None, "not_started", "system", "Seeded from compliance matrix")
            deliverables_seeded += 1
    except Exception:
        pass  # compliance matrix may not exist

    # 5. Create initial WBS from proposal volumes
    wbs_seeded = 0
    try:
        volumes = conn.execute(
            "SELECT * FROM proposal_volumes WHERE opportunity_id = ? ORDER BY volume_number",
            (opportunity_id,)
        ).fetchall()
        for vol in volumes:
            wbs_id = _uuid()
            conn.execute(
                "INSERT INTO cpmp_wbs "
                "(id, contract_id, wbs_number, title, level, status, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    wbs_id, contract_id,
                    f"1.{vol['volume_number']}" if "volume_number" in vol.keys() else f"1.{wbs_seeded + 1}",
                    vol["title"] if "title" in vol.keys() else f"Volume {wbs_seeded + 1}",
                    1, "not_started", _now(), _now(),
                ),
            )
            _record_status_change(conn, "wbs", wbs_id, None, "not_started", "system", "Seeded from proposal volume")
            wbs_seeded += 1
    except Exception:
        pass

    _audit(conn, "transition_from_opportunity",
           f"Created contract {contract_id} from opportunity {opportunity_id}. "
           f"Seeded {deliverables_seeded} deliverables, {wbs_seeded} WBS elements.")
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "contract_id": contract_id,
        "opportunity_id": opportunity_id,
        "deliverables_seeded": deliverables_seeded,
        "wbs_seeded": wbs_seeded,
    }


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ICDEV GovProposal Portfolio Manager (Phase 60)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--portfolio", action="store_true", help="Get portfolio summary")
    group.add_argument("--health", action="store_true", help="Compute contract health score")
    group.add_argument("--transition", action="store_true", help="Create contract from won opportunity")
    group.add_argument("--refresh-all-health", action="store_true", help="Recompute health for all active contracts")

    parser.add_argument("--contract-id")
    parser.add_argument("--opportunity-id")
    parser.add_argument("--created-by")
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.portfolio:
        result = get_portfolio_summary()
    elif args.health:
        if not args.contract_id:
            print("Error: --contract-id required", file=sys.stderr)
            sys.exit(1)
        result = compute_contract_health(args.contract_id)
    elif args.transition:
        if not args.opportunity_id:
            print("Error: --opportunity-id required", file=sys.stderr)
            sys.exit(1)
        result = transition_from_opportunity(args.opportunity_id, args.created_by)
    elif args.refresh_all_health:
        conn = _get_db()
        contracts = conn.execute(
            "SELECT id FROM cpmp_contracts WHERE status IN ('active', 'option_pending')"
        ).fetchall()
        conn.close()
        results = []
        for c in contracts:
            r = compute_contract_health(c["id"])
            results.append(r)
        result = {"status": "ok", "contracts_refreshed": len(results), "results": results}
    else:
        result = {"status": "error", "message": "Unknown command"}

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    import sys
    main()
