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


# ── Regression 4: the brief could not tell a portfolio from test residue ──────
#
# The 2026-08-17 brief opened "5 contract(s) are at YELLOW risk — monitor
# closely this week. Portfolio average CPI is 0.97." Measured against the live
# board the same week, all 9 cpmp_contracts rows carried an EMPTY
# contract_number and were titled "Untitled Contract" (x5), "probe" (x2),
# "bypass probe" and "GCPL Seed Contract"; all 43 cpmp_evm_periods rows held
# just TWO distinct (cpi, spi) pairs, so every contract reported the same
# 0.9689/0.9333 and the "portfolio average" was one seeded constant restated.
# Nothing in the brief said so. An executive read a measurement that was E2E
# and probe residue, and the three "Worst CPI Contracts" rows were mutually
# indistinguishable because the identity column was blank.
#
# The states are kept apart because each sends the reader somewhere different:
# `unmeasurable` (no contracts — load the portfolio), `synthetic` (rows are
# placeholders — the figures describe nothing), `degraded` (real contracts, but
# an EVM feed with no variance — fix the feed), `measured` (act on the brief).


def _placeholder_contracts(n=9):
    """The live board's shape: no contract number, seed/probe titles."""
    titles = [
        "Untitled Contract", "Untitled Contract", "probe", "probe",
        "GCPL Seed Contract", "Untitled Contract", "Untitled Contract",
        "Untitled Contract", "bypass probe",
    ]
    return [
        {"id": f"c{i}", "contract_number": "", "title": titles[i % len(titles)],
         "cpi": 0.9689, "spi": 0.9333}
        for i in range(n)
    ]


def _real_contracts():
    return [
        {"id": "c1", "contract_number": "W15QKN-24-C-0001", "title": "Enclave Sustainment",
         "cpi": 0.88, "spi": 0.91},
        {"id": "c2", "contract_number": "N00178-25-D-0042", "title": "Transport Modernization",
         "cpi": 1.02, "spi": 0.99},
    ]


def test_no_contracts_is_unmeasurable_not_a_healthy_portfolio(no_side_channels):
    """An empty portfolio must not read as a portfolio with nothing wrong."""
    with patch(
        "tools.govcon.portfolio_manager.get_portfolio_summary",
        return_value=_summary(total_contracts=0, active_contracts=0, contracts=[],
                              health_distribution={"green": 0, "yellow": 0, "red": 0},
                              overdue_deliverables=0),
    ):
        snap = reflex._gather_portfolio_snapshot()

    assert snap["data_quality"]["state"] == reflex.DQ_UNMEASURABLE
    narrative = reflex._deterministic_narrative(snap)
    assert "No significant portfolio issues" not in narrative
    assert "UNMEASURABLE" in narrative


def test_placeholder_portfolio_is_not_narrated_as_a_measurement(no_side_channels):
    """The 2026-08-17 case: probe/seed rows presented as an executive brief."""
    with patch(
        "tools.govcon.portfolio_manager.get_portfolio_summary",
        return_value=_summary(contracts=_placeholder_contracts(),
                              health_distribution={"green": 3, "yellow": 5, "red": 0},
                              overdue_deliverables=26),
    ):
        snap = reflex._gather_portfolio_snapshot()

    dq = snap["data_quality"]
    assert dq["state"] == reflex.DQ_SYNTHETIC
    assert "unidentified_contracts" in dq["reasons"]
    assert "placeholder_titles" in dq["reasons"]

    narrative = reflex._deterministic_narrative(snap)
    # The advisory must lead — not trail the conclusions it disqualifies.
    assert narrative.startswith("DATA QUALITY")
    assert reflex.DQ_SYNTHETIC.upper() in narrative
    # The underlying figures may still be stated, but never unqualified.
    assert "5 contract(s) are at YELLOW risk" in narrative


def test_constant_cpi_across_contracts_is_flagged_as_non_discriminating(no_side_channels):
    """A single seeded value restated per contract is not portfolio dispersion."""
    flat = [
        {"id": "c1", "contract_number": "W15QKN-24-C-0001", "title": "Enclave Sustainment",
         "cpi": 0.9689, "spi": 0.9333},
        {"id": "c2", "contract_number": "N00178-25-D-0042", "title": "Transport Modernization",
         "cpi": 0.9689, "spi": 0.9333},
        {"id": "c3", "contract_number": "FA8750-25-C-0007", "title": "Mission Data",
         "cpi": 0.9689, "spi": 0.9333},
    ]
    with patch(
        "tools.govcon.portfolio_manager.get_portfolio_summary",
        return_value=_summary(contracts=flat),
    ):
        snap = reflex._gather_portfolio_snapshot()

    assert snap["cpi_distinct_values"] == 1
    assert snap["cpi_sample_size"] == 3
    dq = snap["data_quality"]
    assert dq["state"] == reflex.DQ_DEGRADED
    assert "no_cpi_variance" in dq["reasons"]
    # The average is still arithmetically true and is still published.
    assert snap["avg_portfolio_cpi"] == 0.969


def test_a_real_portfolio_is_measured_and_carries_no_advisory(no_side_channels):
    """Negative control: a real brief must not be cluttered with a banner."""
    with patch(
        "tools.govcon.portfolio_manager.get_portfolio_summary",
        return_value=_summary(contracts=_real_contracts(), total_contracts=2,
                              health_distribution={"green": 1, "yellow": 1, "red": 0}),
    ):
        snap = reflex._gather_portfolio_snapshot()

    assert snap["data_quality"]["state"] == reflex.DQ_MEASURED
    assert snap["data_quality"]["reasons"] == []
    assert snap["cpi_distinct_values"] == 2
    narrative = reflex._deterministic_narrative(snap)
    assert "DATA QUALITY" not in narrative


def test_a_contract_with_no_number_still_gets_an_actionable_label():
    """Blank contract_number rendered as "—", making every row the same row."""
    label = reflex._contract_label(
        {"id": "0f28acca-ee2b-4a1e-9f00-112233445566", "contract_number": "",
         "title": "bypass probe"}
    )
    assert label not in ("", "—", "N/A", None)
    assert "bypass probe" in label
    # Two placeholder contracts must not collapse to the same label.
    other = reflex._contract_label(
        {"id": "8143e17a-0a0b-4c2d-8e11-665544332211", "contract_number": "",
         "title": "bypass probe"}
    )
    assert label != other
    # A real contract number is used verbatim and is not decorated.
    assert reflex._contract_label(
        {"id": "c1", "contract_number": "W15QKN-24-C-0001", "title": "Enclave"}
    ) == "W15QKN-24-C-0001"


def test_html_report_declares_a_non_measured_data_quality_state():
    """The banner is structural — a reader of the HTML cannot miss the state."""
    snap = {
        "total_contracts": 9,
        "health": {"green": 3, "yellow": 5, "red": 0},
        "data_quality": {
            "state": reflex.DQ_SYNTHETIC,
            "reasons": ["unidentified_contracts", "placeholder_titles"],
            "detail": "9 of 9 contract record(s) carry no contract number.",
        },
    }
    html = reflex._render_html_report(snap, "narrative body", "2026-08-17")
    assert "Data Quality" in html
    assert reflex.DQ_SYNTHETIC.upper() in html

    clean = dict(snap, data_quality={"state": reflex.DQ_MEASURED, "reasons": [], "detail": ""})
    assert "Data Quality" not in reflex._render_html_report(clean, "narrative body", "2026-08-17")
