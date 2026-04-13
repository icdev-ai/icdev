"""Tests for tools.trading.factors.election_phase."""

from datetime import date

from tools.trading.factors.election_phase import (
    PHASE_ELECTION,
    PHASE_MIDTERM,
    PHASE_POST,
    PHASE_PRE,
    classify,
    is_sweet_spot,
    phase_multiplier,
)


def test_classify_anchor_is_election_year():
    info = classify(date(2024, 11, 5))
    assert info.phase == PHASE_ELECTION
    assert info.cycle_year == 4
    assert info.avg_annual_return_pct == 6.0


def test_classify_post_election_year():
    info = classify(date(2025, 6, 15))
    assert info.phase == PHASE_POST
    assert info.cycle_year == 1


def test_classify_midterm_year():
    info = classify(date(2026, 4, 12))
    assert info.phase == PHASE_MIDTERM
    assert info.cycle_year == 2
    assert info.quarter == 2


def test_classify_pre_election_year():
    info = classify(date(2027, 3, 1))
    assert info.phase == PHASE_PRE
    assert info.avg_annual_return_pct == 10.2


def test_classify_accepts_iso_string():
    info = classify("2028-01-15")
    assert info.phase == PHASE_ELECTION


def test_sweet_spot_midterm_q4_inside():
    assert is_sweet_spot(date(2026, 11, 15)) is True


def test_sweet_spot_pre_election_q1_inside():
    assert is_sweet_spot(date(2027, 2, 10)) is True


def test_sweet_spot_midterm_q1_outside():
    assert is_sweet_spot(date(2026, 2, 1)) is False


def test_sweet_spot_pre_election_q3_outside():
    assert is_sweet_spot(date(2027, 9, 1)) is False


def test_phase_multiplier_ordering():
    # Pre-election (Y3) should have highest multiplier, midterm (Y2) lowest.
    assert phase_multiplier(date(2027, 6, 1)) > phase_multiplier(date(2025, 6, 1))
    assert phase_multiplier(date(2027, 6, 1)) > phase_multiplier(date(2026, 6, 1))
    assert phase_multiplier(date(2026, 6, 1)) < phase_multiplier(date(2024, 6, 1))


def test_to_dict_roundtrip():
    info = classify(date(2026, 4, 12))
    d = info.to_dict()
    assert set(d) == {
        "phase",
        "cycle_year",
        "year",
        "quarter",
        "is_sweet_spot",
        "avg_annual_return_pct",
        "premium_multiplier",
    }
    assert d["phase"] == PHASE_MIDTERM
