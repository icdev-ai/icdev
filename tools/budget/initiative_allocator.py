# CUI // SP-CTI
# ICDEV™ Budget — Initiative Allocator (Phase 6X, Budget Allocation by Initiative)
"""Budget allocation by initiative with Tier 1 / Tier 2 prioritization.

Concepts:
    - Initiative: a strategic pursuit (proposal, task order, sole-source bridge)
      that the program has decided to fund. Each initiative has a code, a
      fiscal year, and a USD ceiling.
    - Tier 1 (execution-ready): the initiative is bid-ready or awarded; funds
      are reserved for execution. Obligations on Tier-1 initiatives count
      against the contract/task order total.
    - Tier 2 (backup / pipeline): the initiative is a capture pursuit with
      lower probability of award; funds are placeholder ceilings held
      against portfolio risk. Promoting Tier 2 → Tier 1 triggers an audit
      trail event.
    - Obligation: a recorded commitment of dollars (e.g. task order award,
      contract MOD). Sum of obligations on an initiative is tracked in
      cpmp_budget_obligations; the allocation row carries the running
      total + available (allocated − obligated).
    - Available budget: the un-obligated portion of an allocation. This is
      the headline metric surfaced in the dashboard ("Available Budget vs.
      Allocated by Initiative").

Schema (cpmp_budget_allocations + cpmp_budget_obligations + cpmp_budget_tier_history):
    All three tables are append-only/immutable for audit; tier transitions
    create new history rows instead of mutating allocation rows.

Usage::

    from tools.budget.initiative_allocator import (
        AllocationTier, create_allocation, record_obligation,
        transition_tier, get_tier_summary, get_portfolio_budget_status,
    )

    # Tier 1 — execution-ready
    a = create_allocation(
        initiative_code="INIT-2026-CLD",
        title="Cloud Modernization Phase 2",
        tier=AllocationTier.TIER_1,
        fiscal_year=2026,
        allocated_usd=1_500_000.0,
        agency="DoD",
    )
    record_obligation(a["id"], 200_000.0, description="MOD-001 award")

    # Tier 2 — backup
    b = create_allocation(
        initiative_code="INIT-2026-FUTURE",
        tier=AllocationTier.TIER_2,
        fiscal_year=2026,
        allocated_usd=500_000.0,
    )
    transition_tier(b["id"], new_tier=AllocationTier.TIER_1, reason="Award confirmed")

CLI::

    python tools/budget/initiative_allocator.py --create --code INIT-001 --tier tier_1 --fy 2026 --amount 1500000
    python tools/budget/initiative_allocator.py --list --tier tier_1 --json
    python tools/budget/initiative_allocator.py --summary --fy 2026 --json
    python tools/budget/initiative_allocator.py --portfolio-status --fy 2026 --json
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.budget.initiative_allocator")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AllocationTier(str, Enum):
    TIER_1 = "tier_1"  # execution-ready
    TIER_2 = "tier_2"  # backup / pipeline


class AllocationStatus(str, Enum):
    ACTIVE = "active"
    DEPLETED = "depleted"
    DEFERRED = "deferred"
    CANCELLED = "cancelled"


VALID_TIERS = {t.value for t in AllocationTier}
VALID_STATUSES = {s.value for s in AllocationStatus}

WARNING_THRESHOLD_DEFAULT = 0.90  # 90% obligated → warning
CRITICAL_THRESHOLD_DEFAULT = 0.98


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AllocationError(Exception):
    """Generic allocation failure."""


class InvalidTierError(AllocationError):
    pass


class ObligationExceedsAllocationError(AllocationError):
    pass


# ---------------------------------------------------------------------------
# Schema (cpmp_budget_allocations, cpmp_budget_obligations, cpmp_budget_tier_history)
# ---------------------------------------------------------------------------


CREATE_ALLOCATIONS_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_budget_allocations (
    id TEXT PRIMARY KEY,
    initiative_code TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    fiscal_year INTEGER NOT NULL,
    tier TEXT NOT NULL CHECK(tier IN ('tier_1', 'tier_2')),
    allocated_usd REAL NOT NULL DEFAULT 0.0,
    obligated_usd REAL NOT NULL DEFAULT 0.0,
    available_usd REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','depleted','deferred','cancelled')),
    agency TEXT NOT NULL DEFAULT '',
    contract_id TEXT,
    owner TEXT NOT NULL DEFAULT '',
    justification TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    UNIQUE(initiative_code, fiscal_year)
)
"""

CREATE_OBLIGATIONS_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_budget_obligations (
    id TEXT PRIMARY KEY,
    allocation_id TEXT NOT NULL,
    amount_usd REAL NOT NULL DEFAULT 0.0,
    description TEXT NOT NULL DEFAULT '',
    reference_id TEXT,
    recorded_by TEXT,
    recorded_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (allocation_id) REFERENCES cpmp_budget_allocations(id)
)
"""

CREATE_TIER_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_budget_tier_history (
    id TEXT PRIMARY KEY,
    allocation_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('allocation_created','tier_transition','status_change','obligation_recorded')),
    from_tier TEXT,
    to_tier TEXT,
    from_status TEXT,
    to_status TEXT,
    amount_usd REAL,
    reason TEXT NOT NULL DEFAULT '',
    actor TEXT,
    created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    FOREIGN KEY (allocation_id) REFERENCES cpmp_budget_allocations(id)
)
"""

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_cpmp_budget_allocations_tier ON cpmp_budget_allocations(tier)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_budget_allocations_fy ON cpmp_budget_allocations(fiscal_year)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_budget_allocations_status ON cpmp_budget_allocations(status)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_budget_obligations_alloc ON cpmp_budget_obligations(allocation_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_budget_tier_history_alloc ON cpmp_budget_tier_history(allocation_id)",
]


def ensure_tables(conn) -> None:
    """Bootstrap schema. Idempotent."""
    conn.execute(CREATE_ALLOCATIONS_DDL)
    conn.execute(CREATE_OBLIGATIONS_DDL)
    conn.execute(CREATE_TIER_HISTORY_DDL)
    for idx in CREATE_INDEXES:
        conn.execute(idx)
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_conn(db_path: Optional[str] = None):
    """Get a DB connection. If db_path given, force SQLite to that path."""
    from tools.db.storage import get_connection
    if db_path:
        return get_connection(db_path=db_path)
    return get_connection()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _row_to_dict(row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()} if row else {}


def _validate_tier(tier) -> str:
    if isinstance(tier, AllocationTier):
        return tier.value
    if isinstance(tier, str) and tier in VALID_TIERS:
        return tier
    raise InvalidTierError(f"tier must be one of {sorted(VALID_TIERS)}, got {tier!r}")


def _log_event(
    conn,
    allocation_id: str,
    event_type: str,
    reason: str = "",
    from_tier: Optional[str] = None,
    to_tier: Optional[str] = None,
    from_status: Optional[str] = None,
    to_status: Optional[str] = None,
    amount_usd: Optional[float] = None,
    actor: Optional[str] = None,
) -> None:
    conn.execute(
        """INSERT INTO cpmp_budget_tier_history
           (id, allocation_id, event_type, from_tier, to_tier, from_status, to_status,
            amount_usd, reason, actor, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _gen_id("hist"),
            allocation_id,
            event_type,
            from_tier,
            to_tier,
            from_status,
            to_status,
            amount_usd,
            reason,
            actor,
            _now_iso(),
        ),
    )


# ---------------------------------------------------------------------------
# Public API — CRUD
# ---------------------------------------------------------------------------


def create_allocation(
    initiative_code: str,
    title: str = "",
    tier=AllocationTier.TIER_1,
    fiscal_year: int = 0,
    allocated_usd: float = 0.0,
    agency: str = "",
    contract_id: Optional[str] = None,
    owner: str = "",
    justification: str = "",
) -> Dict[str, Any]:
    """Create a new budget allocation. Returns the inserted row."""
    if not initiative_code:
        raise AllocationError("initiative_code is required")
    if fiscal_year <= 0:
        raise AllocationError("fiscal_year must be positive")
    if allocated_usd < 0:
        raise AllocationError("allocated_usd must be non-negative")

    tier_value = _validate_tier(tier)

    conn = _get_conn()
    ensure_tables(conn)

    # Uniqueness check
    existing = conn.execute(
        "SELECT id FROM cpmp_budget_allocations WHERE initiative_code = ? AND fiscal_year = ?",
        (initiative_code, fiscal_year),
    ).fetchone()
    if existing:
        conn.close()
        raise AllocationError(
            f"Allocation for initiative_code='{initiative_code}' fiscal_year={fiscal_year} already exists"
        )

    alloc_id = _gen_id("alloc")
    now = _now_iso()
    conn.execute(
        """INSERT INTO cpmp_budget_allocations
           (id, initiative_code, title, fiscal_year, tier, allocated_usd, obligated_usd,
            available_usd, status, agency, contract_id, owner, justification,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 0.0, ?, 'active', ?, ?, ?, ?, ?, ?)""",
        (
            alloc_id,
            initiative_code,
            title,
            fiscal_year,
            tier_value,
            allocated_usd,
            allocated_usd,  # available_usd starts at allocated
            agency,
            contract_id,
            owner,
            justification,
            now,
            now,
        ),
    )

    _log_event(
        conn,
        alloc_id,
        "allocation_created",
        reason=justification or "Initial allocation",
        to_tier=tier_value,
        to_status=AllocationStatus.ACTIVE.value,
        amount_usd=allocated_usd,
        actor=owner,
    )
    conn.commit()

    row = conn.execute(
        "SELECT * FROM cpmp_budget_allocations WHERE id = ?", (alloc_id,)
    ).fetchone()
    conn.close()
    return _row_to_dict(row)


def get_allocation(allocation_id: str) -> Dict[str, Any]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM cpmp_budget_allocations WHERE id = ?", (allocation_id,)
    ).fetchone()
    conn.close()
    if not row:
        raise AllocationError(f"Allocation {allocation_id} not found")
    return _row_to_dict(row)


def list_allocations(
    tier: Optional[AllocationTier] = None,
    fiscal_year: Optional[int] = None,
    status: Optional[AllocationStatus] = None,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM cpmp_budget_allocations WHERE 1=1"
    params: List[Any] = []
    if tier is not None:
        sql += " AND tier = ?"
        params.append(_validate_tier(tier))
    if fiscal_year is not None:
        sql += " AND fiscal_year = ?"
        params.append(fiscal_year)
    if status is not None:
        sql += " AND status = ?"
        params.append(status.value if isinstance(status, AllocationStatus) else status)
    sql += " ORDER BY tier, fiscal_year DESC, initiative_code"

    conn = _get_conn()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def update_allocation(
    allocation_id: str,
    title: Optional[str] = None,
    agency: Optional[str] = None,
    owner: Optional[str] = None,
    justification: Optional[str] = None,
    allocated_usd: Optional[float] = None,
    contract_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Update mutable metadata. Cannot change tier/status here (use transition_*)."""
    alloc = get_allocation(allocation_id)

    if allocated_usd is not None and allocated_usd < 0:
        raise AllocationError("allocated_usd must be non-negative")
    if allocated_usd is not None and allocated_usd < alloc["obligated_usd"]:
        raise AllocationError(
            f"allocated_usd ({allocated_usd}) cannot be less than already-obligated "
            f"({alloc['obligated_usd']})"
        )

    updates = []
    params: List[Any] = []
    if title is not None:
        updates.append("title = ?")
        params.append(title)
    if agency is not None:
        updates.append("agency = ?")
        params.append(agency)
    if owner is not None:
        updates.append("owner = ?")
        params.append(owner)
    if justification is not None:
        updates.append("justification = ?")
        params.append(justification)
    if allocated_usd is not None:
        updates.append("allocated_usd = ?")
        params.append(allocated_usd)
        updates.append("available_usd = ?")
        params.append(allocated_usd - alloc["obligated_usd"])
    if contract_id is not None:
        updates.append("contract_id = ?")
        params.append(contract_id)

    if not updates:
        return alloc

    updates.append("updated_at = ?")
    params.append(_now_iso())
    params.append(allocation_id)

    conn = _get_conn()
    conn.execute(
        f"UPDATE cpmp_budget_allocations SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    conn.commit()
    conn.close()
    return get_allocation(allocation_id)


def delete_allocation(allocation_id: str) -> Dict[str, Any]:
    """Soft-delete by setting status='cancelled' (append-only audit friendly)."""
    conn = _get_conn()
    existing = conn.execute(
        "SELECT * FROM cpmp_budget_allocations WHERE id = ?", (allocation_id,)
    ).fetchone()
    if not existing:
        conn.close()
        raise AllocationError(f"Allocation {allocation_id} not found")
    if existing["obligated_usd"] > 0:
        conn.close()
        raise AllocationError(
            "Cannot cancel an allocation that has recorded obligations; "
            "de-obligate first."
        )
    conn.execute(
        "UPDATE cpmp_budget_allocations SET status='cancelled', updated_at=? WHERE id = ?",
        (_now_iso(), allocation_id),
    )
    _log_event(
        conn,
        allocation_id,
        "status_change",
        from_status=existing["status"],
        to_status="cancelled",
        reason="Allocation cancelled",
    )
    conn.commit()
    conn.close()
    return get_allocation(allocation_id)


# ---------------------------------------------------------------------------
# Public API — obligations
# ---------------------------------------------------------------------------


def record_obligation(
    allocation_id: str,
    amount_usd: float,
    description: str = "",
    reference_id: Optional[str] = None,
    recorded_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Record an obligation against an allocation.

    Increments obligated_usd, decrements available_usd, transitions status to
    'depleted' if available reaches 0, and writes an audit row.
    """
    if amount_usd < 0:
        raise AllocationError("amount_usd must be non-negative")

    alloc = get_allocation(allocation_id)
    new_obligated = alloc["obligated_usd"] + amount_usd
    if new_obligated > alloc["allocated_usd"]:
        raise ObligationExceedsAllocationError(
            f"Obligation of ${amount_usd:,.2f} would bring total obligation to "
            f"${new_obligated:,.2f}, exceeding allocation of "
            f"${alloc['allocated_usd']:,.2f}"
        )

    conn = _get_conn()
    obl_id = _gen_id("obl")
    now = _now_iso()
    conn.execute(
        """INSERT INTO cpmp_budget_obligations
           (id, allocation_id, amount_usd, description, reference_id, recorded_by, recorded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (obl_id, allocation_id, amount_usd, description, reference_id, recorded_by, now),
    )

    new_available = alloc["allocated_usd"] - new_obligated
    new_status = alloc["status"]
    if new_available <= 0:
        new_status = AllocationStatus.DEPLETED.value
    elif alloc["status"] != AllocationStatus.ACTIVE.value:
        # If it was deferred/cancelled, leave alone — record_obligation is
        # a no-op on terminal states (caller should not be here).
        new_status = alloc["status"]

    conn.execute(
        """UPDATE cpmp_budget_allocations
           SET obligated_usd = ?, available_usd = ?, status = ?, updated_at = ?
           WHERE id = ?""",
        (new_obligated, new_available, new_status, now, allocation_id),
    )

    _log_event(
        conn,
        allocation_id,
        "obligation_recorded",
        from_status=alloc["status"],
        to_status=new_status,
        amount_usd=amount_usd,
        reason=description,
        actor=recorded_by,
    )
    conn.commit()
    conn.close()

    return get_allocation(allocation_id)


def list_obligations(allocation_id: str) -> List[Dict[str, Any]]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM cpmp_budget_obligations WHERE allocation_id = ? ORDER BY recorded_at",
        (allocation_id,),
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Public API — tier / status transitions
# ---------------------------------------------------------------------------


def transition_tier(
    allocation_id: str,
    new_tier,
    reason: str = "",
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    """Promote or demote an initiative between Tier 1 and Tier 2."""
    target_tier = _validate_tier(new_tier)
    alloc = get_allocation(allocation_id)
    if alloc["tier"] == target_tier:
        return alloc  # no-op

    conn = _get_conn()
    conn.execute(
        "UPDATE cpmp_budget_allocations SET tier = ?, updated_at = ? WHERE id = ?",
        (target_tier, _now_iso(), allocation_id),
    )
    _log_event(
        conn,
        allocation_id,
        "tier_transition",
        from_tier=alloc["tier"],
        to_tier=target_tier,
        reason=reason or f"Tier transition {alloc['tier']} → {target_tier}",
        actor=actor,
    )
    conn.commit()
    conn.close()
    return get_allocation(allocation_id)


def transition_status(
    allocation_id: str,
    new_status,
    reason: str = "",
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    """Move an allocation between active / deferred / cancelled."""
    if isinstance(new_status, AllocationStatus):
        target = new_status.value
    elif isinstance(new_status, str) and new_status in VALID_STATUSES:
        target = new_status
    else:
        raise AllocationError(f"Invalid status: {new_status!r}")

    alloc = get_allocation(allocation_id)
    if alloc["status"] == target:
        return alloc

    conn = _get_conn()
    conn.execute(
        "UPDATE cpmp_budget_allocations SET status = ?, updated_at = ? WHERE id = ?",
        (target, _now_iso(), allocation_id),
    )
    _log_event(
        conn,
        allocation_id,
        "status_change",
        from_status=alloc["status"],
        to_status=target,
        reason=reason,
        actor=actor,
    )
    conn.commit()
    conn.close()
    return get_allocation(allocation_id)


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------


def get_tier_summary(fiscal_year: Optional[int] = None) -> Dict[str, Any]:
    """Aggregate allocated/obligated/available per tier for a fiscal year."""
    conn = _get_conn()
    params: List[Any] = []
    where = ""
    if fiscal_year is not None:
        where = "WHERE fiscal_year = ?"
        params.append(fiscal_year)

    rows = conn.execute(
        f"""SELECT tier, status,
                   COUNT(*) AS count,
                   COALESCE(SUM(allocated_usd), 0.0) AS allocated_usd,
                   COALESCE(SUM(obligated_usd), 0.0) AS obligated_usd,
                   COALESCE(SUM(available_usd), 0.0) AS available_usd
            FROM cpmp_budget_allocations
            {where}
            GROUP BY tier, status
            ORDER BY tier, status""",
        params,
    ).fetchall()
    conn.close()

    summary: Dict[str, Any] = {
        "fiscal_year": fiscal_year,
        "tier_1": _empty_tier_buckets(),
        "tier_2": _empty_tier_buckets(),
        "total": _empty_tier_buckets(),
    }
    for r in rows:
        tier_key = r["tier"]
        bucket = summary[tier_key]
        if r["status"] == AllocationStatus.ACTIVE.value:
            bucket["active_count"] += r["count"]
        bucket["count"] += r["count"]
        bucket["allocated_usd"] += r["allocated_usd"]
        bucket["obligated_usd"] += r["obligated_usd"]
        bucket["available_usd"] += r["available_usd"]

        for k in ("count", "allocated_usd", "obligated_usd", "available_usd"):
            summary["total"][k] += r[k]

    return summary


def _empty_tier_buckets() -> Dict[str, Any]:
    return {
        "count": 0,
        "active_count": 0,
        "allocated_usd": 0.0,
        "obligated_usd": 0.0,
        "available_usd": 0.0,
    }


def get_portfolio_budget_status(
    fiscal_year: Optional[int] = None,
    warning_threshold: float = WARNING_THRESHOLD_DEFAULT,
    critical_threshold: float = CRITICAL_THRESHOLD_DEFAULT,
) -> Dict[str, Any]:
    """Portfolio-level budget status with warning/overspend detection."""
    conn = _get_conn()
    params: List[Any] = []
    where = ""
    if fiscal_year is not None:
        where = "WHERE fiscal_year = ?"
        params.append(fiscal_year)

    rows = conn.execute(
        f"""SELECT id, initiative_code, title, tier, allocated_usd, obligated_usd, available_usd
            FROM cpmp_budget_allocations
            {where}
            ORDER BY tier, initiative_code""",
        params,
    ).fetchall()
    conn.close()

    warnings: List[str] = []
    overspends: List[str] = []
    healthy = 0
    total_allocated = 0.0
    total_obligated = 0.0
    total_available = 0.0

    for r in rows:
        allocated = r["allocated_usd"]
        obligated = r["obligated_usd"]
        available = r["available_usd"]
        total_allocated += allocated
        total_obligated += obligated
        total_available += available

        if allocated <= 0:
            continue

        if available < 0 or obligated > allocated:
            overspends.append(
                f"{r['initiative_code']} [{r['tier']}]: obligated=${obligated:,.2f} "
                f"> allocated=${allocated:,.2f} (over by ${obligated - allocated:,.2f})"
            )
            continue

        utilization = obligated / allocated if allocated > 0 else 0
        if utilization >= critical_threshold:
            warnings.append(
                f"{r['initiative_code']} [{r['tier']}]: {utilization*100:.1f}% obligated — CRITICAL"
            )
        elif utilization >= warning_threshold:
            warnings.append(
                f"{r['initiative_code']} [{r['tier']}]: {utilization*100:.1f}% obligated — near ceiling"
            )
        else:
            healthy += 1

    if overspends:
        overall_status = "overspend"
    elif warnings:
        overall_status = "warning"
    else:
        overall_status = "healthy"

    return {
        "fiscal_year": fiscal_year,
        "status": overall_status,
        "warning_threshold": warning_threshold,
        "critical_threshold": critical_threshold,
        "totals": {
            "allocated_usd": total_allocated,
            "obligated_usd": total_obligated,
            "available_usd": total_available,
            "initiative_count": len(rows),
            "healthy_count": healthy,
        },
        "warnings": warnings,
        "overspends": overspends,
    }


def get_initiative_history(allocation_id: str) -> List[Dict[str, Any]]:
    """Return audit history for an allocation (creation, transitions, obligations)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM cpmp_budget_tier_history WHERE allocation_id = ? ORDER BY created_at",
        (allocation_id,),
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initiative budget allocator (Tier 1 execution-ready / Tier 2 backup)"
    )
    parser.add_argument("--create", action="store_true", help="Create a new allocation")
    parser.add_argument("--code", help="Initiative code (e.g. INIT-2026-CLD)")
    parser.add_argument("--title", default="", help="Initiative title")
    parser.add_argument("--tier", choices=sorted(VALID_TIERS), help="Tier (tier_1 or tier_2)")
    parser.add_argument("--fy", type=int, help="Fiscal year")
    parser.add_argument("--amount", type=float, help="Allocated USD")
    parser.add_argument("--agency", default="", help="Agency")
    parser.add_argument("--owner", default="", help="Owner email")
    parser.add_argument("--justification", default="", help="Justification")

    parser.add_argument("--list", action="store_true", help="List allocations")
    parser.add_argument("--status-filter", choices=sorted(VALID_STATUSES), help="Filter by status")

    parser.add_argument("--get", help="Get allocation by ID")
    parser.add_argument("--obligation", type=float, help="Record obligation amount")
    parser.add_argument("--description", default="", help="Obligation description / tier transition reason")
    parser.add_argument("--transition-to", choices=sorted(VALID_TIERS), help="Transition tier")
    parser.add_argument("--summary", action="store_true", help="Tier summary")
    parser.add_argument("--portfolio-status", action="store_true", help="Portfolio budget status")
    parser.add_argument("--history", help="Get initiative history for allocation ID")
    parser.add_argument("--init-tables", action="store_true", help="Bootstrap tables")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")

    args = parser.parse_args()

    result: Any = None
    if args.init_tables:
        conn = _get_conn()
        ensure_tables(conn)
        conn.close()
        result = {"status": "ok", "message": "Initiative budget tables initialized"}
    elif args.create:
        result = create_allocation(
            initiative_code=args.code or "",
            title=args.title,
            tier=args.tier or AllocationTier.TIER_1.value,
            fiscal_year=args.fy or 0,
            allocated_usd=args.amount or 0.0,
            agency=args.agency,
            owner=args.owner,
            justification=args.justification,
        )
    elif args.list:
        kwargs = {}
        if args.tier:
            kwargs["tier"] = args.tier
        if args.fy:
            kwargs["fiscal_year"] = args.fy
        if args.status_filter:
            kwargs["status"] = args.status_filter
        result = list_allocations(**kwargs)
    elif args.get:
        result = get_allocation(args.get)
    elif args.obligation is not None:
        result = record_obligation(
            args.get, args.obligation, description=args.description, recorded_by=args.owner
        )
    elif args.transition_to:
        result = transition_tier(
            args.get, new_tier=args.transition_to, reason=args.description, actor=args.owner
        )
    elif args.summary:
        result = get_tier_summary(fiscal_year=args.fy)
    elif args.portfolio_status:
        result = get_portfolio_budget_status(fiscal_year=args.fy)
    elif args.history:
        result = get_initiative_history(args.history)
    else:
        parser.print_help()
        sys.exit(0)

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        if isinstance(result, list):
            for row in result:
                print(
                    f"{row.get('tier','?'):7s} {row.get('initiative_code','?'):24s} "
                    f"${row.get('allocated_usd',0):>14,.2f} "
                    f"${row.get('obligated_usd',0):>14,.2f} "
                    f"${row.get('available_usd',0):>14,.2f} "
                    f"{row.get('status','?')}"
                )
        elif isinstance(result, dict):
            for k, v in result.items():
                print(f"{k}: {v}")
        else:
            print(result)


if __name__ == "__main__":
    main()
