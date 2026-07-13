#!/usr/bin/env python3
# CUI // SP-CTI
"""prem-bid-04 — who owns the price.

compass merges the supplier rate cards and knows what an LCAT actually costs from a given
vendor on a given date. ICDEV does not: it prices from pg_lcat_allocations, whose
hourly_rate is frequently NULL.

So compass is the PRICING AUTHORITY. ICDEV computing its own number would give two prices
for one bid — worse than having none, because somebody then has to decide which one is
real, and they will decide it late.

Accepting is not believing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.govcon.cost_volume_intake import accept_cost_volume  # noqa: E402


def _priced(**over):
    p = {
        "status": "ok",
        "contract_type": "ffp",
        "total_price": 299376.0,
        "build_up": {"direct_labor": 100000.0, "subcontractor_cost": 75000.0,
                     "odc_cost": 10000.0},
        "line_items": [
            {"labor_category": "Senior Engineer", "annual_cost": 100000.0},
            {"labor_category": "Analyst", "annual_cost": 75000.0},
        ],
        "wrap_rates": {"fringe_pct": 30.0, "overhead_pct": 25.0, "ga_pct": 12.0,
                       "fee_pct": 8.0},
        "ptw": {"low": {"total_price": 250000.0}, "high": {"total_price": 340000.0}},
    }
    p.update(over)
    return p


@pytest.fixture()
def conn(icdev_db):
    from tools.db.storage import get_connection

    c = get_connection(db_path=str(icdev_db))
    yield c


def test_a_compass_priced_volume_is_accepted_and_recorded(conn):
    out = accept_cost_volume(opportunity_id="opp-1", priced=_priced(), conn=conn)

    assert out["status"] == "accepted"
    assert out["total_evaluated_price"] == 299376.0
    assert out["priced_by"] == "compass"

    row = dict(conn.execute(
        "SELECT total_evaluated_price, pricing_strategy, ptw_estimate_low "
        "FROM pg_cost_volumes WHERE id = %s", (out["cost_volume_id"],)).fetchone())
    assert row["total_evaluated_price"] == 299376.0
    # Provenance STORED, not assumed. "Why is this $4.2M" has a different answer
    # depending on who priced it, and the row should say which.
    assert row["pricing_strategy"] == "priced_by:compass"
    assert row["ptw_estimate_low"] == 250000.0


def test_a_volume_that_declares_itself_PARTIAL_is_refused(conn):
    """compass refuses to emit one. The server does not take that on trust — this is the
    last place to catch a hole in a bid before it is stored as a price."""
    out = accept_cost_volume(
        opportunity_id="opp-1",
        priced=_priced(status="partial", unrated=[{"labor_category": "Cyber Analyst"}]),
        conn=conn)

    assert out["status"] == "refused"
    assert "declares itself 'partial'" in out["reason"]
    assert conn.execute("SELECT count(*) AS n FROM pg_cost_volumes").fetchone()["n"] == 0


def test_a_total_that_does_not_reconcile_with_its_LINES_is_refused(conn):
    """A total that does not add up to its own line items is not a price, it is a number
    — and an unauditable price on a bid is one nobody can defend."""
    out = accept_cost_volume(
        opportunity_id="opp-1",
        priced=_priced(line_items=[{"annual_cost": 100000.0}]),   # claims 175k of labour
        conn=conn)

    assert out["status"] == "refused"
    assert "does not reconcile" in out["reason"]
    assert out["line_sum"] == 100000.0
    assert out["claimed"] == 175000.0


def test_float_noise_across_many_lines_does_not_trip_the_reconciliation(conn):
    """Refusing a real price over a rounding cent would make the check useless — people
    would turn it off."""
    lines = [{"annual_cost": 33333.33} for _ in range(3)]      # 99999.99, not 100000
    out = accept_cost_volume(
        opportunity_id="opp-1",
        priced=_priced(build_up={"direct_labor": 100000.0, "subcontractor_cost": 0.0,
                                 "odc_cost": 0.0},
                       line_items=lines),
        conn=conn)
    assert out["status"] == "accepted"


def test_a_volume_with_no_total_is_refused(conn):
    out = accept_cost_volume(opportunity_id="opp-1",
                             priced=_priced(total_price=None), conn=conn)
    assert out["status"] == "refused"
    assert "nothing to accept" in out["reason"]


def test_an_unknown_pricing_source_is_refused(conn):
    out = accept_cost_volume(opportunity_id="opp-1", priced=_priced(),
                             source="whoever", conn=conn)
    assert out["status"] == "refused"


def test_a_volume_with_no_line_items_is_still_accepted(conn):
    """Some volumes are a single negotiated number. There is nothing to reconcile against
    — that is not the same as failing to reconcile."""
    out = accept_cost_volume(opportunity_id="opp-1",
                             priced=_priced(line_items=[]), conn=conn)
    assert out["status"] == "accepted"
