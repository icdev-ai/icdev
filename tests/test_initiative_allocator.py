# CUI // SP-CTI
"""Tests for tools/budget/initiative_allocator.py — budget allocation by initiative.

Covers:
- create_allocation with tier 1 (execution-ready) and tier 2 (backup)
- get_tier_summary aggregates allocated/obligated/available per tier
- record_obligation tracks total obligation; available = allocated - obligated
- transition_tier moves an initiative between tiers (1<->2) with audit
- get_portfolio_budget_status returns portfolio-level rollup + overspend alerts
- list_allocations supports filtering by tier, fiscal_year, status
- Validation: negative allocated_usd rejected, obligation > allocated blocked

Schema bootstrapped in conftest.py:
    cpmp_budget_allocations
    cpmp_budget_obligations
    cpmp_budget_tier_history
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Ensure repo root on sys.path
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.budget.initiative_allocator import (  # noqa: E402
    AllocationTier,
    AllocationStatus,
    create_allocation,
    list_allocations,
    get_allocation,
    record_obligation,
    transition_tier,
    get_tier_summary,
    get_portfolio_budget_status,
    get_initiative_history,
    AllocationError,
    ObligationExceedsAllocationError,
    InvalidTierError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_table(tmp_path, monkeypatch):
    """Use a per-test SQLite DB so we don't pollute the dev DB."""
    db = tmp_path / "initiative_allocator_test.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    # Force the module to re-import under the new env
    for mod in [
        "tools.budget.initiative_allocator",
        "tools.db.storage",
    ]:
        if mod in sys.modules:
            del sys.modules[mod]

    from tools.budget.initiative_allocator import ensure_tables
    from tools.db.storage import get_connection
    conn = get_connection(db_path=str(db))
    ensure_tables(conn)
    conn.close()
    yield


def _sample_kwargs(**overrides):
    base = {
        "initiative_code": "INIT-001",
        "title": "Cloud Modernization Phase 2",
        "tier": AllocationTier.TIER_1,
        "fiscal_year": 2026,
        "allocated_usd": 1_000_000.0,
        "agency": "DoD",
        "owner": "pm@example.gov",
        "justification": "Execution-ready — vehicle awarded Q1",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# create_allocation
# ---------------------------------------------------------------------------


class TestCreateAllocation:
    def test_creates_tier1_allocation(self):
        alloc = create_allocation(**_sample_kwargs())
        assert alloc["id"]
        assert alloc["tier"] == "tier_1"
        assert alloc["status"] == AllocationStatus.ACTIVE
        assert alloc["allocated_usd"] == 1_000_000.0
        assert alloc["obligated_usd"] == 0.0
        assert alloc["available_usd"] == 1_000_000.0
        assert alloc["created_at"]

    def test_creates_tier2_backup_allocation(self):
        alloc = create_allocation(
            **_sample_kwargs(
                initiative_code="INIT-002",
                title="Future Pursuit — backup",
                tier=AllocationTier.TIER_2,
                allocated_usd=500_000.0,
                justification="Capture pipeline — not yet awarded",
            )
        )
        assert alloc["tier"] == "tier_2"
        assert alloc["status"] == AllocationStatus.ACTIVE
        assert alloc["available_usd"] == 500_000.0

    def test_negative_allocation_rejected(self):
        with pytest.raises(AllocationError, match="non-negative"):
            create_allocation(**_sample_kwargs(allocated_usd=-1.0))

    def test_invalid_tier_rejected(self):
        with pytest.raises(InvalidTierError):
            create_allocation(**_sample_kwargs(tier="tier_3"))

    def test_optional_contract_id(self):
        alloc = create_allocation(**_sample_kwargs(contract_id="c-001"))
        assert alloc["contract_id"] == "c-001"

    def test_duplicate_initiative_code_fiscal_year_rejected(self):
        create_allocation(**_sample_kwargs(initiative_code="DUP-1", fiscal_year=2026))
        with pytest.raises(AllocationError, match="exists"):
            create_allocation(**_sample_kwargs(initiative_code="DUP-1", fiscal_year=2026))


# ---------------------------------------------------------------------------
# list_allocations
# ---------------------------------------------------------------------------


class TestListAllocations:
    def test_list_empty(self):
        assert list_allocations() == []

    def test_list_all(self):
        create_allocation(**_sample_kwargs(initiative_code="A", tier=AllocationTier.TIER_1))
        create_allocation(**_sample_kwargs(initiative_code="B", tier=AllocationTier.TIER_2))
        rows = list_allocations()
        assert len(rows) == 2
        codes = {r["initiative_code"] for r in rows}
        assert codes == {"A", "B"}

    def test_filter_by_tier(self):
        create_allocation(**_sample_kwargs(initiative_code="A", tier=AllocationTier.TIER_1))
        create_allocation(**_sample_kwargs(initiative_code="B", tier=AllocationTier.TIER_2))
        rows = list_allocations(tier=AllocationTier.TIER_1)
        assert len(rows) == 1
        assert rows[0]["initiative_code"] == "A"

    def test_filter_by_fiscal_year(self):
        create_allocation(**_sample_kwargs(initiative_code="A", fiscal_year=2026))
        create_allocation(**_sample_kwargs(initiative_code="B", fiscal_year=2027))
        rows = list_allocations(fiscal_year=2026)
        assert len(rows) == 1
        assert rows[0]["initiative_code"] == "A"


# ---------------------------------------------------------------------------
# get_allocation
# ---------------------------------------------------------------------------


class TestGetAllocation:
    def test_get_existing(self):
        created = create_allocation(**_sample_kwargs())
        fetched = get_allocation(created["id"])
        assert fetched["id"] == created["id"]
        assert fetched["allocated_usd"] == 1_000_000.0

    def test_get_missing_raises(self):
        with pytest.raises(AllocationError, match="not found"):
            get_allocation("nope")


# ---------------------------------------------------------------------------
# record_obligation
# ---------------------------------------------------------------------------


class TestRecordObligation:
    def test_records_obligation_and_updates_available(self):
        alloc = create_allocation(**_sample_kwargs(allocated_usd=1_000_000.0))
        updated = record_obligation(
            allocation_id=alloc["id"],
            amount_usd=200_000.0,
            description="Task order award",
        )
        assert updated["obligated_usd"] == 200_000.0
        assert updated["available_usd"] == 800_000.0
        # Underlying row in DB is updated
        fetched = get_allocation(alloc["id"])
        assert fetched["obligated_usd"] == 200_000.0
        assert fetched["available_usd"] == 800_000.0

    def test_cumulative_obligations(self):
        alloc = create_allocation(**_sample_kwargs(allocated_usd=1_000_000.0))
        record_obligation(alloc["id"], 100_000.0, "MOD 1")
        record_obligation(alloc["id"], 250_000.0, "MOD 2")
        record_obligation(alloc["id"], 50_000.0, "MOD 3")
        fetched = get_allocation(alloc["id"])
        assert fetched["obligated_usd"] == 400_000.0
        assert fetched["available_usd"] == 600_000.0

    def test_obligation_exceeding_allocation_blocked(self):
        alloc = create_allocation(**_sample_kwargs(allocated_usd=100_000.0))
        with pytest.raises(ObligationExceedsAllocationError):
            record_obligation(alloc["id"], 150_000.0, "overrun")

    def test_negative_obligation_rejected(self):
        alloc = create_allocation(**_sample_kwargs(allocated_usd=1_000_000.0))
        with pytest.raises(AllocationError, match="non-negative"):
            record_obligation(alloc["id"], -10.0, "negative")


# ---------------------------------------------------------------------------
# transition_tier
# ---------------------------------------------------------------------------


class TestTransitionTier:
    def test_tier2_to_tier1_promotion(self):
        alloc = create_allocation(**_sample_kwargs(tier=AllocationTier.TIER_2))
        promoted = transition_tier(
            allocation_id=alloc["id"],
            new_tier=AllocationTier.TIER_1,
            reason="Award confirmed Q2",
        )
        assert promoted["tier"] == "tier_1"
        history = get_initiative_history(alloc["id"])
        assert any(
            h["event_type"] == "tier_transition"
            and h["from_tier"] == "tier_2"
            and h["to_tier"] == "tier_1"
            for h in history
        )

    def test_invalid_target_tier(self):
        alloc = create_allocation(**_sample_kwargs())
        with pytest.raises(InvalidTierError):
            transition_tier(alloc["id"], new_tier="tier_3", reason="bogus")

    def test_history_includes_creation_event(self):
        alloc = create_allocation(**_sample_kwargs())
        history = get_initiative_history(alloc["id"])
        assert history[0]["event_type"] == "allocation_created"


# ---------------------------------------------------------------------------
# get_tier_summary
# ---------------------------------------------------------------------------


class TestGetTierSummary:
    def test_empty_tier_summary(self):
        summary = get_tier_summary(fiscal_year=2026)
        assert summary["tier_1"]["count"] == 0
        assert summary["tier_1"]["allocated_usd"] == 0.0
        assert summary["tier_2"]["count"] == 0

    def test_aggregates_across_initiatives(self):
        # Three tier-1 initiatives
        a = create_allocation(**_sample_kwargs(initiative_code="A", tier=AllocationTier.TIER_1, allocated_usd=1_000_000.0))
        create_allocation(**_sample_kwargs(initiative_code="B", tier=AllocationTier.TIER_1, allocated_usd=500_000.0))
        create_allocation(**_sample_kwargs(initiative_code="C", tier=AllocationTier.TIER_1, allocated_usd=250_000.0))
        # Two tier-2 (backup) initiatives
        create_allocation(**_sample_kwargs(initiative_code="D", tier=AllocationTier.TIER_2, allocated_usd=750_000.0))
        create_allocation(**_sample_kwargs(initiative_code="E", tier=AllocationTier.TIER_2, allocated_usd=400_000.0))

        # Record some obligations
        record_obligation(a["id"], 300_000.0, "Q1 award")

        summary = get_tier_summary(fiscal_year=2026)
        assert summary["tier_1"]["count"] == 3
        assert summary["tier_1"]["allocated_usd"] == 1_750_000.0
        assert summary["tier_1"]["obligated_usd"] == 300_000.0
        assert summary["tier_1"]["available_usd"] == 1_450_000.0
        assert summary["tier_2"]["count"] == 2
        assert summary["tier_2"]["allocated_usd"] == 1_150_000.0
        assert summary["tier_2"]["obligated_usd"] == 0.0
        assert summary["tier_2"]["available_usd"] == 1_150_000.0

    def test_grand_totals(self):
        create_allocation(**_sample_kwargs(initiative_code="A", tier=AllocationTier.TIER_1, allocated_usd=1_000_000.0))
        create_allocation(**_sample_kwargs(initiative_code="B", tier=AllocationTier.TIER_2, allocated_usd=400_000.0))
        summary = get_tier_summary(fiscal_year=2026)
        assert summary["total"]["allocated_usd"] == 1_400_000.0
        assert summary["total"]["available_usd"] == 1_400_000.0


# ---------------------------------------------------------------------------
# get_portfolio_budget_status
# ---------------------------------------------------------------------------


class TestGetPortfolioBudgetStatus:
    def test_returns_alerts_when_over_budget(self):
        alloc = create_allocation(**_sample_kwargs(allocated_usd=100_000.0))
        # 95% obligated — should be flagged as WARNING
        record_obligation(alloc["id"], 95_000.0, "near ceiling")
        status = get_portfolio_budget_status(fiscal_year=2026, warning_threshold=0.90)
        assert len(status["warnings"]) >= 1
        assert any("95" in w for w in status["warnings"])

    def test_no_alerts_when_healthy(self):
        create_allocation(**_sample_kwargs(initiative_code="A", allocated_usd=1_000_000.0))
        status = get_portfolio_budget_status(fiscal_year=2026, warning_threshold=0.90)
        assert status["warnings"] == []
        assert status["status"] == "healthy"

    def test_overspend_alert(self):
        # Force an overspend by using get_portfolio_budget_status directly
        # (the API normally blocks it via record_obligation, but we test
        # detection of legacy data where obligations > allocations).
        alloc = create_allocation(**_sample_kwargs(allocated_usd=100_000.0))
        # Bypass validation by inserting directly
        from tools.budget.initiative_allocator import _get_conn
        conn = _get_conn()
        conn.execute(
            "INSERT INTO cpmp_budget_obligations (id, allocation_id, amount_usd, description, recorded_by, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "obl-legacy",
                alloc["id"],
                150_000.0,
                "legacy data — pre-validator",
                "test",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.execute(
            "UPDATE cpmp_budget_allocations SET obligated_usd = 150000.0, available_usd = -50000.0 WHERE id = ?",
            (alloc["id"],),
        )
        conn.commit()
        conn.close()

        status = get_portfolio_budget_status(fiscal_year=2026, warning_threshold=0.90)
        assert status["status"] == "overspend"
        assert len(status["overspends"]) >= 1
