#!/usr/bin/env python3
# CUI // SP-CTI
"""prem-bid-01/02/03 — the price is real, it is auditable, and a win carries it.

Three bugs, and they compounded into one: **a bid could be priced with a made-up
number, the number could not be found afterwards, and winning threw it away.**

  1. rate_benchmarker.generate_cost_volume() priced an unrated LCAT at
     ``rate = 85.0  # default if no rate set``. That is a guess on a bid. It does not
     fail and it does not warn — the wrap rates and the price-to-win band are all
     computed on top of it, so the total looks exactly like a real one.

  2. It then THREW THE LINE ITEMS AWAY. They were computed, returned in the response
     dict, and never stored; only the totals went to pg_cost_volumes. So the $85 could
     not be found even by someone looking for it.

  3. portfolio_manager.transition_from_opportunity() dropped all the money. It never
     read pg_cost_volumes: total/funded/ceiling were left at 0.0, contract_type was
     hardcoded "FFP", and no CLINs were created. **A won bid produced a contract with
     no money in it**, and said "status: ok" about it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def db(icdev_db, monkeypatch):
    """Point both modules at the temp DB.

    Patch via importlib + setattr, NOT monkeypatch's string form: `tools.x` and
    `icdev.tools.x` resolve to DIFFERENT module objects (the root `tools/` package is a
    shim), so a string-form patch can hit the one the code under test is not using.
    """
    import importlib

    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(icdev_db))

    def _db():
        return get_connection(db_path=str(icdev_db))

    for mod_name in ("tools.govcon.rate_benchmarker", "tools.govcon.portfolio_manager"):
        mod = importlib.import_module(mod_name)
        monkeypatch.setattr(mod, "_get_db", _db)

    yield conn


def _alloc(conn, cv_id, lcat, fte, rate):
    import uuid
    conn.execute(
        "INSERT INTO pg_lcat_allocations (id, cost_volume_id, task_description, "
        "labor_category, fte_count, hourly_rate, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (str(uuid.uuid4()), cv_id, f"work for {lcat}", lcat, fte, rate, "2026-07-13"),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 1. An unrated LCAT is SURFACED, never guessed
# ---------------------------------------------------------------------------


def test_an_unrated_lcat_refuses_the_price_rather_than_guessing(db):
    """THE bug. A defaulted rate is a wrong price that looks like a right one."""
    from tools.govcon.rate_benchmarker import generate_cost_volume

    _alloc(db, "opp-1", "Senior Systems Engineer", 2.0, 150.0)
    _alloc(db, "opp-1", "Cyber Analyst", 1.0, None)          # <- no rate

    out = generate_cost_volume("opp-1")

    assert out["status"] == "unpriced"
    assert out["unrated_count"] == 1
    assert out["unrated"][0]["labor_category"] == "Cyber Analyst"
    assert "not guessed" in out["unrated"][0]["reason"]
    # And critically: no total was produced at all. There is nothing to mistake for a
    # real price.
    assert "total_evaluated_price" not in out


def test_no_lcat_is_ever_priced_at_the_old_85_dollar_default(db):
    from tools.govcon.rate_benchmarker import generate_cost_volume

    _alloc(db, "opp-1", "Cyber Analyst", 1.0, None)
    out = generate_cost_volume("opp-1", allow_unrated=True)

    priced_rates = [li["hourly_rate"] for li in out["line_items"]]
    assert 85.0 not in priced_rates
    assert out["line_items"] == []          # it was not priced at all...
    assert out["unrated_count"] == 1        # ...it was surfaced


def test_a_partial_price_is_never_reported_as_ok(db):
    """`partial` is not `ok`. A volume with holes in it must not be mistaken downstream
    for a complete price — that is how a hole in a bid reaches the customer."""
    from tools.govcon.rate_benchmarker import generate_cost_volume

    _alloc(db, "opp-1", "Senior Systems Engineer", 2.0, 150.0)
    _alloc(db, "opp-1", "Cyber Analyst", 1.0, None)

    out = generate_cost_volume("opp-1", allow_unrated=True)
    assert out["status"] == "partial"
    assert out["unrated_count"] == 1
    assert out["total_evaluated_price"] > 0     # the rated line WAS priced


def test_a_fully_rated_volume_prices_cleanly(db):
    """Guard against 'fixing' the guess by breaking pricing altogether."""
    from tools.govcon.rate_benchmarker import generate_cost_volume

    _alloc(db, "opp-1", "Senior Systems Engineer", 2.0, 150.0)
    out = generate_cost_volume("opp-1")

    assert out["status"] == "ok"
    assert out["unrated_count"] == 0
    # 2 FTE x 2080 hrs x $150
    assert out["line_items"][0]["annual_cost"] == pytest.approx(624000.0)
    assert out["total_evaluated_price"] > 624000.0     # wrap rates applied on top


# ---------------------------------------------------------------------------
# 2. The price can be audited line by line
# ---------------------------------------------------------------------------


def test_the_priced_line_items_are_PERSISTED_not_just_returned(db):
    """They used to be computed, returned, and thrown away — only the totals were
    stored. So you could see a number but not which LCAT at which rate produced it."""
    from tools.govcon.rate_benchmarker import generate_cost_volume

    _alloc(db, "opp-1", "Senior Systems Engineer", 2.0, 150.0)
    generate_cost_volume("opp-1")

    row = dict(db.execute(
        "SELECT hourly_rate, annual_cost, basis_of_estimate FROM pg_lcat_allocations "
        "WHERE cost_volume_id = %s", ("opp-1",),
    ).fetchone())

    assert row["hourly_rate"] == pytest.approx(150.0)
    assert row["annual_cost"] == pytest.approx(624000.0)
    assert "NOT defaulted" in row["basis_of_estimate"]


# ---------------------------------------------------------------------------
# 3. A won bid carries its money
# ---------------------------------------------------------------------------


def _won_opportunity(conn, opp_id="opp-1"):
    conn.execute(
        "INSERT INTO proposal_opportunities (id, solicitation_number, title, agency, "
        "naics_code, status, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (opp_id, "SOL-123", "A Real Program", "DoD", "541512", "won",
         "2026-07-13", "2026-07-13"),
    )
    conn.commit()


def test_a_won_bid_carries_its_price_into_the_contract(db):
    """Before this, the contract came out worth 0.0 and said status: ok."""
    from tools.govcon.portfolio_manager import transition_from_opportunity
    from tools.govcon.rate_benchmarker import generate_cost_volume

    _won_opportunity(db)
    _alloc(db, "opp-1", "Senior Systems Engineer", 2.0, 150.0)
    cv = generate_cost_volume("opp-1", contract_type="cpff")
    assert cv["status"] == "ok"

    out = transition_from_opportunity("opp-1")

    assert out["status"] == "proposed"
    assert out["total_value"] == pytest.approx(cv["total_evaluated_price"])
    assert out["total_value"] > 0
    # contract_type DERIVED from the volume we priced, not hardcoded "FFP"
    assert out["contract_type"] == "CPFF"

    row = dict(db.execute(
        "SELECT total_value, funded_value, ceiling_value, contract_type, status "
        "FROM cpmp_contracts WHERE id = %s", (out["contract_id"],),
    ).fetchone())
    assert row["total_value"] == pytest.approx(cv["total_evaluated_price"])
    assert row["contract_type"] == "CPFF"
    # 'draft' — a won bid does not self-approve itself into an active contract.
    assert row["status"] == "draft"


def test_clins_are_generated_so_the_money_can_be_burned_down(db):
    """A contract with a value but no CLINs cannot be invoiced against."""
    from tools.govcon.portfolio_manager import transition_from_opportunity
    from tools.govcon.rate_benchmarker import generate_cost_volume

    _won_opportunity(db)
    _alloc(db, "opp-1", "Senior Systems Engineer", 2.0, 150.0)
    _alloc(db, "opp-1", "Cyber Analyst", 1.0, 120.0)
    generate_cost_volume("opp-1")

    out = transition_from_opportunity("opp-1")
    assert out["clins_created"] == 2

    clins = db.execute(
        "SELECT clin_number, description, total_value FROM cpmp_clins "
        "WHERE contract_id = %s ORDER BY clin_number", (out["contract_id"],),
    ).fetchall()
    assert len(clins) == 2
    assert all(dict(c)["total_value"] > 0 for c in clins)


def test_a_transition_with_NO_cost_volume_says_so_loudly(db):
    """The old behaviour, made honest. If there is no price, the contract has no value —
    and the caller is TOLD, rather than getting a cheerful `status: ok` on a $0 contract."""
    from tools.govcon.portfolio_manager import transition_from_opportunity

    _won_opportunity(db)
    out = transition_from_opportunity("opp-1")

    assert out["total_value"] == 0.0
    assert out["clins_created"] == 0
    assert any("NO VALUE" in n for n in out["needs_attention"])


def test_period_of_performance_is_flagged_not_invented(db):
    """proposal_opportunities carries no PoP. Inventing dates would be the same failure
    as the $85 rate — a made-up number that looks like a real one."""
    from tools.govcon.portfolio_manager import transition_from_opportunity
    from tools.govcon.rate_benchmarker import generate_cost_volume

    _won_opportunity(db)
    _alloc(db, "opp-1", "Senior Systems Engineer", 2.0, 150.0)
    generate_cost_volume("opp-1")

    out = transition_from_opportunity("opp-1")
    assert any("period of performance is NOT set" in n for n in out["needs_attention"])

    row = dict(db.execute(
        "SELECT pop_start, pop_end FROM cpmp_contracts WHERE id = %s",
        (out["contract_id"],),
    ).fetchone())
    assert row["pop_start"] is None and row["pop_end"] is None


def test_a_ZERO_rate_is_real_data_not_a_missing_one(db):
    """`if not rate` is falsy for 0.0.

    The original bug replaced a legitimate $0.00 line — customer-furnished or
    government-furnished labour, a no-charge resource — with the $85 default. The one
    case where the data was RIGHT is the case it corrupted.

    My first fix inherited the same truthiness test and would have flagged a valid
    zero-rate line as UNRATED, blocking a price that was perfectly correct. Missing and
    zero are different things. (Caught by compass's tools/pricing/boe.py spec, which
    called this out explicitly.)
    """
    from tools.govcon.rate_benchmarker import generate_cost_volume

    _alloc(db, "opp-1", "Senior Systems Engineer", 2.0, 150.0)
    _alloc(db, "opp-1", "Gov-Furnished Support", 1.0, 0.0)   # a REAL zero rate

    out = generate_cost_volume("opp-1")

    assert out["status"] == "ok"          # not "unpriced" — nothing is missing
    assert out["unrated_count"] == 0
    assert len(out["line_items"]) == 2

    zero_line = [li for li in out["line_items"]
                 if li["labor_category"] == "Gov-Furnished Support"][0]
    assert zero_line["hourly_rate"] == 0.0
    assert zero_line["annual_cost"] == 0.0     # priced at $0, NOT at $85
