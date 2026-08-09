# CUI // SP-CTI
# ICDEV™ GovProposal — Portfolio Manager (Phase 60, D-CPMP-8, D-CPMP-9)
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
    python tools/govcon/portfolio_manager.py --burn-rate-summary --json
"""

import argparse
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

import sys

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection

from tools.logging.icdev_logger import get_logger
logger = get_logger("icdev.govcon.portfolio_manager")

_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))
_CONFIG_PATH = _ROOT / "args" / "govcon_config.yaml"


def _load_config():
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f).get("cpmp", {})
    return {}


_CFG = _load_config()

HEALTH_WEIGHTS = _CFG.get(
    "health_weights",
    {
        "evm": 0.30,
        "deliverables": 0.25,
        "cpars": 0.20,
        "negative_events": 0.15,
        "funding": 0.10,
    },
)

EVM_CFG = _CFG.get("evm", {})
CPI_YELLOW = EVM_CFG.get("cpi_yellow_threshold", 0.95)
CPI_RED = EVM_CFG.get("cpi_red_threshold", 0.85)
SPI_YELLOW = EVM_CFG.get("spi_yellow_threshold", 0.95)
SPI_RED = EVM_CFG.get("spi_red_threshold", 0.85)

RECOMMENDATION_THRESHOLD = _CFG.get("recommendation_threshold", 0.75)

_DIM_RECOMMENDATIONS = {
    "evm": "Review earned value management — cost or schedule variance exceeds acceptable limits",
    "deliverables": "Address overdue or at-risk deliverables to prevent CPARS impact",
    "cpars": "Engage contracting officer to improve past performance ratings",
    "negative_events": "Resolve open negative events to reduce NDAA penalty exposure",
    "funding": "Review contract funding status and obligation rate",
}


def _build_recommendations(scores):
    return [
        {"dimension": dim, "action": action}
        for dim, action in _DIM_RECOMMENDATIONS.items()
        if scores.get(dim, 1.0) < RECOMMENDATION_THRESHOLD
    ]


def _get_db():
    conn = get_connection()
    # Govcon tools are service-layer operations — clear any Flask RLS context
    # so that complex JOIN queries (subquery aliases) don't fail with
    # "no such column: c.classification" when RLS injection misfires.
    conn.set_security_context(None)  # rls-bypass: govcon service-layer; JOIN subquery aliases fail with c.classification injection
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
            "INSERT INTO audit_trail (event_type, actor, action, details, session_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("hook_event_logged", actor, action, details, "cpmp"),
        )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_audit: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


def _record_status_change(conn, entity_type, entity_id, old_status, new_status, changed_by=None, reason=None):
    conn.execute(
        "INSERT INTO cpmp_status_history (entity_type, entity_id, old_status, new_status, changed_by, reason) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (entity_type, entity_id, old_status, new_status, changed_by, reason),
    )


# ── Health Scoring ───────────────────────────────────────────────────


def _score_evm(conn, contract_id):
    """EVM dimension: latest CPI and SPI → 0.0-1.0 score."""
    row = conn.execute(
        "SELECT cpi, spi FROM cpmp_evm_periods WHERE contract_id = %s ORDER BY period_date DESC LIMIT 1",
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
    total = conn.execute("SELECT COUNT(*) FROM cpmp_deliverables WHERE contract_id = %s", (contract_id,)).fetchone()[0]
    if total == 0:
        return 1.0

    overdue = conn.execute(
        "SELECT COUNT(*) FROM cpmp_deliverables WHERE contract_id = %s AND status = 'overdue'",
        (contract_id,),
    ).fetchone()[0]
    rejected = conn.execute(
        "SELECT COUNT(*) FROM cpmp_deliverables WHERE contract_id = %s AND status = 'rejected'",
        (contract_id,),
    ).fetchone()[0]

    bad = overdue + rejected
    return max(0.0, 1.0 - (bad / total))


def _score_cpars(conn, contract_id):
    """CPARS dimension: latest overall rating or 1.0 if no assessment."""
    rating_scores = {
        "exceptional": 1.0,
        "very_good": 0.85,
        "satisfactory": 0.65,
        "marginal": 0.40,
        "unsatisfactory": 0.15,
    }
    row = conn.execute(
        "SELECT overall_rating FROM cpmp_cpars_assessments WHERE contract_id = %s ORDER BY period_end DESC LIMIT 1",
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
        "WHERE contract_id = %s AND corrective_action_status IN ('open', 'in_progress')",
        (contract_id,),
    ).fetchone()[0]

    critical = conn.execute(
        "SELECT COUNT(*) FROM cpmp_negative_events "
        "WHERE contract_id = %s AND severity = 'critical' AND corrective_action_status IN ('open', 'in_progress')",
        (contract_id,),
    ).fetchone()[0]

    # Each open event reduces score by 0.1, critical by 0.2
    penalty = (open_events - critical) * 0.1 + critical * 0.2
    return max(0.0, 1.0 - penalty)


def _score_funding(conn, contract_id):
    """Funding dimension: funded_value / total_value ratio."""
    row = conn.execute(
        "SELECT total_value, funded_value FROM cpmp_contracts WHERE id = %s",
        (contract_id,),
    ).fetchone()
    if not row or not row["total_value"] or row["total_value"] == 0:
        return 1.0

    # Aggregate billed_value from CLINs (billed_value lives on cpmp_clins, not contracts)
    billed_row = conn.execute(
        "SELECT COALESCE(SUM(billed_value), 0) as billed FROM cpmp_clins WHERE contract_id = %s",
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
    row = conn.execute("SELECT id FROM cpmp_contracts WHERE id = %s", (contract_id,)).fetchone()
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
        "UPDATE cpmp_contracts SET health = %s, updated_at = %s WHERE id = %s",
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
        "recommendations": _build_recommendations(scores),
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
        "COALESCE(SUM(c.obligated_value), 0) as obligated_val, "
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

    # Upcoming deliverables (next 30 days) — use Python dates to avoid DB-specific functions
    from datetime import date as _date, timedelta as _timedelta
    _today = _date.today().isoformat()
    _in_30 = (_date.today() + _timedelta(days=30)).isoformat()
    upcoming_raw = conn.execute(
        "SELECT d.*, c.contract_number, c.title as contract_title "
        "FROM cpmp_deliverables d JOIN cpmp_contracts c ON d.contract_id = c.id "
        "WHERE d.due_date >= %s AND d.due_date <= %s "
        "AND d.status NOT IN ('accepted', 'rejected') "
        "ORDER BY d.due_date ASC LIMIT 20",
        (_today, _in_30)
    ).fetchall()
    upcoming = []
    for _row in upcoming_raw:
        _d = dict(_row)
        try:
            _d["days_until_due"] = (_date.fromisoformat(str(_d["due_date"])) - _date.today()).days
        except Exception:
            _d["days_until_due"] = None
        upcoming.append(_d)

    # Contract list for table — include latest CPI/SPI via LEFT JOIN
    contracts_raw = conn.execute(
        "SELECT c.id, c.contract_number, c.title, c.agency, c.contract_type, "
        "c.status, c.health, c.total_value, c.funded_value, c.pop_start, c.pop_end, "
        "c.cpars_rating_current, c.updated_at, "
        "evm.cpi, evm.spi "
        "FROM cpmp_contracts c "
        "LEFT JOIN ("
        "  SELECT e1.contract_id, e1.cpi, e1.spi "
        "  FROM cpmp_evm_periods e1 "
        "  WHERE e1.period_date = ("
        "    SELECT MAX(e2.period_date) FROM cpmp_evm_periods e2"
        "    WHERE e2.contract_id = e1.contract_id"
        "  )"
        ") evm ON evm.contract_id = c.id "
        "ORDER BY c.updated_at DESC"
    ).fetchall()

    # Build contract dicts with value alias for template
    contracts = []
    for c in contracts_raw:
        cd = dict(c)
        cd["value"] = cd.get("total_value", 0) or 0
        contracts.append(cd)

    obligated_val = value_row["obligated_val"] if value_row["obligated_val"] else value_row["funded_val"]
    burn_rate = (value_row["billed_val"] / max(obligated_val, 1)) * 100 if obligated_val else 0

    # Ensure health_distribution always has all 3 keys
    for key in ("green", "yellow", "red"):
        health_dist.setdefault(key, 0)

    conn.close()
    remaining_obligation = (obligated_val or 0) - (value_row["billed_val"] or 0)

    return {
        "status": "ok",
        "portfolio": {
            "total_contracts": total,
            "active_contracts": active,
            "total_value": value_row["total_val"],
            "funded_value": value_row["funded_val"],
            "obligated_value": obligated_val,
            "billed_value": value_row["billed_val"],
            "remaining_obligation": round(remaining_obligation, 2),
            "burn_rate_pct": round(burn_rate, 1),
            "overdue_deliverables": overdue,
            "at_risk_contracts": at_risk,
            "health_distribution": health_dist,
            "upcoming_deliverables": [dict(u) for u in upcoming],
            "contracts": contracts,
        },
    }


# ── Burn Rate / Obligation Aggregation ───────────────────────────────


def get_burn_rate_summary(status_filter=("active", "option_pending")):
    """Aggregate burn rate and outstanding obligation data grouped by contract ID.

    Delegates per-contract computation to contract_manager.get_obligation_summary
    so base-period vs. option-period logic lives in a single place.
    """
    from tools.govcon.contract_manager import get_obligation_summary

    conn = _get_db()
    query = "SELECT id, contract_number, title, status FROM cpmp_contracts"
    params = []
    if status_filter:
        placeholders = ", ".join(["%s"] * len(status_filter))
        query += f" WHERE status IN ({placeholders})"  # nosec B608 -- placeholders bound via params, not interpolated values
        params.extend(status_filter)
    contracts = conn.execute(query, params).fetchall()
    conn.close()

    by_contract = {}
    for c in contracts:
        summary = get_obligation_summary(c["id"])
        if summary.get("status") != "ok":
            continue
        by_contract[c["id"]] = {
            "contract_number": c["contract_number"],
            "title": c["title"],
            "status": c["status"],
            "burn_rate_pct": summary["burn_rate_pct"],
            "total_owed": summary["total_owed"],
            "spent_so_far": summary["spent_so_far"],
            "current_option": summary["current_option"],
        }

    return {"status": "ok", "total": len(by_contract), "contracts": by_contract}


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
    opp = conn.execute("SELECT * FROM proposal_opportunities WHERE id = %s", (opportunity_id,)).fetchone()
    if not opp:
        conn.close()
        return {"status": "error", "message": f"Opportunity {opportunity_id} not found"}
    if opp["status"] != "won":
        conn.close()
        return {"status": "error", "message": f"Opportunity status is '{opp['status']}', must be 'won'"}

    # Check not already transitioned
    existing = conn.execute("SELECT id FROM cpmp_contracts WHERE opportunity_id = %s", (opportunity_id,)).fetchone()
    if existing:
        conn.close()
        return {"status": "error", "message": f"Contract already exists for this opportunity: {existing['id']}"}

    # 2. Read the PRICE we actually bid (prem-bid-03).
    #
    # This used to hardcode contract_type="FFP" with the comment "default, user updates
    # later", and leave total_value / funded_value / ceiling_value / PoP entirely out of
    # the INSERT — so they defaulted to 0.0. A won bid produced a contract with NO MONEY
    # IN IT. Everything downstream that reads a contract value (EVM, CLIN burn, the
    # /cpmp dashboards) was reading zero, and nothing said why. The cost volume we spent
    # the whole bid building was sitting right there in pg_cost_volumes, unread.
    cv = conn.execute(
        "SELECT id, contract_type, total_evaluated_price, direct_labor_cost "
        "FROM pg_cost_volumes WHERE opportunity_id = %s "
        "ORDER BY created_at DESC LIMIT 1",
        (opportunity_id,),
    ).fetchone()
    cv = dict(cv) if cv else {}

    total_value = float(cv.get("total_evaluated_price") or 0.0)
    # contract_type is DERIVED from the volume we priced, not guessed. Falling back to
    # FFP only when there is genuinely no cost volume — and saying so in the audit.
    contract_type = str(cv.get("contract_type") or "FFP").upper()

    # Period of performance is deliberately NOT set here. proposal_opportunities carries
    # no PoP columns — there is nothing to derive it from. Inventing dates would be the
    # same failure as the $85 default rate: a made-up number that looks like a real one.
    # It is left NULL and reported in the return value, so contracts staff know it is
    # the one thing they must supply before this baseline is accepted.
    contract_id = _uuid()
    conn.execute(
        "INSERT INTO cpmp_contracts "
        "(id, contract_number, title, agency, naics_code, contract_type, "
        "total_value, funded_value, ceiling_value, "
        "status, opportunity_id, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            contract_id,
            f"TBD-{opp['solicitation_number'] or opportunity_id[:8]}",
            opp["title"],
            opp["agency"],
            opp["naics_code"],
            contract_type,
            total_value,
            # funded == total at award: nothing is incrementally funded until a mod says
            # so. ceiling == total for the base award; option years raise it via a mod.
            total_value,
            total_value,
            # 'draft', not 'active'. This is a PROPOSED baseline. A won bid does not get
            # to self-approve itself into an active contract — contracts staff accept it.
            "draft",
            opportunity_id,
            _now(),
            _now(),
        ),
    )

    # 2b. CLINs from the priced allocations. A contract with a value but no CLINs cannot
    # be invoiced against or burned down — the money exists as a single number and
    # nothing can be tracked against it.
    clin_count = 0
    if cv.get("id"):
        # NOTE the column is LYING. pg_lcat_allocations.cost_volume_id holds the
        # OPPORTUNITY id, not a cost-volume id — lcat_mapper.generate_boe() writes
        # opportunity_id into it, and generate_cost_volume() reads it back with the
        # opportunity_id. Querying it with the actual cv id (as the name invites) matches
        # nothing and silently produces ZERO CLINs. Renaming the column is a migration
        # for another day; trusting its name is the bug.
        allocs = conn.execute(
            "SELECT labor_category, fte_count, hourly_rate, annual_cost "
            "FROM pg_lcat_allocations WHERE cost_volume_id = %s "
            "ORDER BY annual_cost DESC",
            (opportunity_id,),
        ).fetchall()
        for i, a in enumerate(allocs, start=1):
            d = dict(a)
            if not d.get("annual_cost"):
                # An unpriced allocation must not become a $0 CLIN. A zero-value CLIN
                # reads as "this work is free", which is worse than its absence.
                continue
            try:
                conn.execute(
                    "INSERT INTO cpmp_clins "
                    "(id, contract_id, clin_number, description, clin_type, "
                    "total_value, funded_value, status, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        _uuid(), contract_id, f"{i:04d}",
                        f"{d['labor_category']} — {d.get('fte_count') or 0} FTE",
                        "labor",
                        float(d["annual_cost"]), float(d["annual_cost"]),
                        "active", _now(), _now(),
                    ),
                )
                clin_count += 1
            except Exception as exc:  # pragma: no cover - table shape varies by install
                logger.warning("could not create CLIN for %s: %s", d.get("labor_category"), exc)
    _record_status_change(
        conn,
        "contract",
        contract_id,
        None,
        "draft",
        created_by or "system",
        f"Created from opportunity {opportunity_id}",
    )

    # 3. Link opportunity → contract
    try:
        conn.execute("UPDATE proposal_opportunities SET contract_id = %s WHERE id = %s", (contract_id, opportunity_id))
    except Exception:
        pass  # column may not exist yet

    # Link customer delivery if exists
    try:
        delivery = conn.execute(
            "SELECT id FROM customer_deliveries WHERE opportunity_id = %s", (opportunity_id,)
        ).fetchone()
        if delivery:
            conn.execute("UPDATE customer_deliveries SET contract_id = %s WHERE id = %s", (contract_id, delivery["id"]))
            conn.execute(
                "UPDATE cpmp_contracts SET customer_delivery_id = %s WHERE id = %s", (delivery["id"], contract_id)
            )
    except Exception:
        pass

    # 4. Seed deliverables from compliance matrix CDRLs
    deliverables_seeded = 0
    try:
        cdrl_items = conn.execute(
            "SELECT * FROM proposal_compliance_matrix WHERE opportunity_id = %s AND requirement_type = 'cdrl'",
            (opportunity_id,),
        ).fetchall()
        for item in cdrl_items:
            deliv_id = _uuid()
            conn.execute(
                "INSERT INTO cpmp_deliverables "
                "(id, contract_id, cdrl_number, title, description, deliverable_type, "
                "status, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    deliv_id,
                    contract_id,
                    item["requirement_id"] if "requirement_id" in item.keys() else None,
                    item["requirement_text"][:200] if "requirement_text" in item.keys() else "CDRL",
                    item["requirement_text"] if "requirement_text" in item.keys() else None,
                    "cdrl",
                    "not_started",
                    _now(),
                    _now(),
                ),
            )
            _record_status_change(
                conn, "deliverable", deliv_id, None, "not_started", "system", "Seeded from compliance matrix"
            )
            deliverables_seeded += 1
    except Exception as _exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # compliance matrix may not exist
        logger.warning(
            "transition_from_opportunity: best-effort INSERT into cpmp_deliverables failed (non-blocking): %s",
            _exc,
        )

    # 5. Create initial WBS from proposal volumes
    wbs_seeded = 0
    try:
        volumes = conn.execute(
            "SELECT * FROM proposal_volumes WHERE opportunity_id = %s ORDER BY volume_number", (opportunity_id,)
        ).fetchall()
        for vol in volumes:
            wbs_id = _uuid()
            conn.execute(
                "INSERT INTO cpmp_wbs "
                "(id, contract_id, wbs_number, title, level, status, "
                "created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    wbs_id,
                    contract_id,
                    f"1.{vol['volume_number']}" if "volume_number" in vol.keys() else f"1.{wbs_seeded + 1}",
                    vol["title"] if "title" in vol.keys() else f"Volume {wbs_seeded + 1}",
                    1,
                    "not_started",
                    _now(),
                    _now(),
                ),
            )
            _record_status_change(conn, "wbs", wbs_id, None, "not_started", "system", "Seeded from proposal volume")
            wbs_seeded += 1
    except Exception as _exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("transition_from_opportunity: best-effort INSERT into cpmp_wbs failed (non-blocking): %s", _exc)

    _audit(
        conn,
        "transition_from_opportunity",
        f"Created contract {contract_id} from opportunity {opportunity_id}. "
        f"Seeded {deliverables_seeded} deliverables, {wbs_seeded} WBS elements.",
    )
    conn.commit()
    conn.close()

    # The money is REPORTED, not just written. A caller that gets back
    # total_value: 0.0 needs to see that immediately — before this, a won bid produced a
    # contract worth nothing and said "status: ok" about it.
    needs = []
    if not cv.get("id"):
        needs.append(
            "no cost volume found for this opportunity — the contract has NO VALUE. "
            "Price the bid (rate_benchmarker.generate_cost_volume) before transitioning, "
            "or set total_value by hand."
        )
    if not clin_count:
        needs.append("no CLINs created — the value cannot be invoiced against or burned down.")
    needs.append(
        "period of performance is NOT set: proposal_opportunities carries no PoP, and "
        "inventing dates would be a made-up number that looks like a real one. Contracts "
        "staff must supply pop_start / pop_end."
    )

    return {
        # 'proposed', not 'ok'. This is a baseline awaiting acceptance by contracts
        # staff (the contract row is 'draft'), not a finished contract.
        "status": "proposed",
        "contract_id": contract_id,
        "opportunity_id": opportunity_id,
        "contract_type": contract_type,
        "cost_volume_id": cv.get("id"),
        "total_value": total_value,
        "clins_created": clin_count,
        "deliverables_seeded": deliverables_seeded,
        "wbs_seeded": wbs_seeded,
        "needs_attention": needs,
    }


# ── CLI ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="ICDEV™ GovProposal Portfolio Manager (Phase 60)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--portfolio", action="store_true", help="Get portfolio summary")
    group.add_argument("--health", action="store_true", help="Compute contract health score")
    group.add_argument("--transition", action="store_true", help="Create contract from won opportunity")
    group.add_argument("--refresh-all-health", action="store_true", help="Recompute health for all active contracts")
    group.add_argument(
        "--burn-rate-summary", action="store_true", help="Aggregate burn rate/obligation data by contract"
    )

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
    elif args.burn_rate_summary:
        result = get_burn_rate_summary()
    else:
        result = {"status": "error", "message": "Unknown command"}

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    import sys

    main()
