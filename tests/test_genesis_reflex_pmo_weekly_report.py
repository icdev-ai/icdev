# CUI // SP-CTI
"""Tests for tools/genesis/reflexes/pmo_weekly_report.py — the weekly PMO brief.

Three regressions are pinned here. All three failed silently and all three
pushed the portfolio brief toward the same wrong conclusion — "no significant
portfolio issues detected this week" — which is what the reflex emits when its
snapshot comes back empty.

1. ``get_portfolio_summary()`` returns ``{"status": ..., "portfolio": {...}}``.
   The reflex read the aggregates off the top level, so every ``.get(key, 0)``
   returned its default. The brief reported 0 contracts, no health distribution
   and no CPI/SPI against a live portfolio of 9 contracts, and would have
   reported the same all-clear had every contract been RED.

2. The portfolio contract list LEFT JOINs ``cpmp_evm_periods`` on the latest
   ``period_date``. A contract with more than one row for that date fans out,
   so a single contract could fill all three "worst CPI" slots and its CPI/SPI
   counted once per duplicate in the portfolio averages.

3. ``pmo_ai_advisor.auto_detect_issues`` compared ``ctx.get("max_risk_exposure",
   0) >= 16``. ``MAX(exposure)`` is NULL for a contract with no open risks, so
   the key was present holding None and the default never applied. The
   ``TypeError`` aborted detection for that contract, and the reflex's per-
   contract ``except Exception: pass`` swallowed it — leaving the brief's
   "Critical Issues" section permanently empty.
"""
from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

reflex = importlib.import_module("tools.genesis.reflexes.pmo_weekly_report")
advisor = importlib.import_module("tools.govcon.pmo_ai_advisor")


def _summary(**portfolio):
    """A get_portfolio_summary() return in its real, nested shape."""
    base = {
        "total_contracts": 9,
        "active_contracts": 7,
        "total_value": 1_000_000.0,
        "burn_rate_pct": 42.5,
        "overdue_deliverables": 3,
        "health_distribution": {"green": 5, "yellow": 1, "red": 1},
        "upcoming_deliverables": [],
        "contracts": [],
    }
    base.update(portfolio)
    return {"status": "ok", "portfolio": base}


@pytest.fixture
def no_side_channels():
    """Silence the option-countdown and issue-detection lookups.

    Both open real connections; this module's assertions are about how the
    reflex reads the portfolio payload, not about those two sources.
    """
    with patch.object(reflex, "_gather_portfolio_snapshot", reflex._gather_portfolio_snapshot):
        with patch(
            "tools.govcon.option_period_tracker.get_portfolio_countdown",
            return_value={"critical": 0, "warning": 0, "options": []},
        ):
            with patch.object(advisor, "auto_detect_issues", return_value={"issues": []}):
                yield


def test_snapshot_reads_aggregates_from_nested_portfolio(no_side_channels):
    """Regression 1: the aggregates live under "portfolio", not at the top."""
    with patch(
        "tools.govcon.portfolio_manager.get_portfolio_summary",
        return_value=_summary(),
    ):
        snap = reflex._gather_portfolio_snapshot()

    assert snap.get("portfolio_error") is None
    assert snap["total_contracts"] == 9
    assert snap["active_contracts"] == 7
    assert snap["health"] == {"green": 5, "yellow": 1, "red": 1}
    assert snap["overdue_deliverables"] == 3
    # burn rate is published as burn_rate_pct, not burn_rate
    assert snap["burn_rate"] == 42.5


def test_red_portfolio_is_never_reported_as_all_clear(no_side_channels):
    """The bug's actual consequence: a RED portfolio read as a quiet week."""
    with patch(
        "tools.govcon.portfolio_manager.get_portfolio_summary",
        return_value=_summary(health_distribution={"green": 0, "yellow": 2, "red": 7}),
    ):
        snap = reflex._gather_portfolio_snapshot()

    narrative = reflex._deterministic_narrative(snap)
    assert "No significant portfolio issues" not in narrative
    assert "7 contract(s) are RED" in narrative


def test_flat_payload_does_not_crash_the_snapshot(no_side_channels):
    """A payload without the "portfolio" key degrades to empty, not an exception."""
    with patch(
        "tools.govcon.portfolio_manager.get_portfolio_summary",
        return_value={"status": "error", "message": "db down"},
    ):
        snap = reflex._gather_portfolio_snapshot()

    assert snap["total_contracts"] == 0
    assert snap["contracts"] == []


def test_duplicate_evm_rows_collapse_to_one_row_per_contract(no_side_channels):
    """Regression 2: the EVM LEFT JOIN fans a contract out across the report."""
    fanned = [
        {"id": "c1", "contract_number": "N1", "cpi": 0.70, "spi": 0.80},
        {"id": "c1", "contract_number": "N1", "cpi": 0.70, "spi": 0.80},
        {"id": "c1", "contract_number": "N1", "cpi": 0.70, "spi": 0.80},
        {"id": "c2", "contract_number": "N2", "cpi": 1.00, "spi": 1.00},
    ]
    with patch(
        "tools.govcon.portfolio_manager.get_portfolio_summary",
        return_value=_summary(contracts=fanned),
    ):
        snap = reflex._gather_portfolio_snapshot()

    assert len(snap["contracts"]) == 2
    # c1 must not occupy every "worst CPI" slot
    assert [c["id"] for c in snap["worst_cpi_contracts"]] == ["c1", "c2"]
    # and must not be weighted 3x in the averages: (0.70 + 1.00) / 2
    assert snap["avg_portfolio_cpi"] == 0.85
    assert snap["avg_portfolio_spi"] == 0.9


def test_auto_detect_issues_survives_null_max_risk_exposure():
    """Regression 3: MAX(exposure) is NULL when a contract has no open risks."""
    ctx = {
        "contract": {"contract_number": "N1"},
        "evm": {},
        "overdue_deliverables": 0,
        "noncompliant_subs": 0,
        "open_mods": 0,
        "open_risks": 0,
        "max_risk_exposure": None,  # the key exists, so .get's default never fired
    }
    with patch.object(advisor, "_gather_contract_context", return_value=ctx):
        result = advisor.auto_detect_issues("c1")

    assert result["status"] == "ok"
    assert not any(i["type"] == "critical_risk" for i in result["issues"])


def test_auto_detect_issues_still_flags_a_real_critical_risk():
    """The None-guard must not swallow a genuine exposure >= 16."""
    ctx = {
        "contract": {"contract_number": "N1"},
        "evm": {},
        "overdue_deliverables": 0,
        "noncompliant_subs": 0,
        "open_mods": 0,
        "open_risks": 2,
        "max_risk_exposure": 20,
    }
    with patch.object(advisor, "_gather_contract_context", return_value=ctx):
        result = advisor.auto_detect_issues("c1")

    critical = [i for i in result["issues"] if i["type"] == "critical_risk"]
    assert len(critical) == 1
    assert critical[0]["severity"] == "critical"
