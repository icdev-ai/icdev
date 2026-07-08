# [TEMPLATE: CUI // SP-CTI]
"""Tests for tools/govcon/competitor_profiler.py (prop-cap-13).

Aggregates competitor intelligence from govcon_awards: profile_vendor
(per-vendor award/agency/NAICS/set-aside breakdown), get_leaderboard
(ranked by award count, filterable by naics/agency), compare_vendors
(side-by-side profiles). Pre-existing module (Phase 59/D367); this file
closes a zero-test-coverage gap discovered while researching prop-cap-13,
same pattern as prop-cap-11/prop-cap-12.
"""
import sqlite3

import pytest

from tools.db.storage import translate_sql


class _TranslatingConn:
    """Wraps a raw sqlite3 connection, translating %s -> ? before executing.

    competitor_profiler.py's SQL is authored in Postgres-style %s syntax
    (correct for the real backend); a bare sqlite3.connect() mock doesn't
    understand %s. Same root cause already fixed in prop-fix-10/11,
    prop-cap-11, and prop-cap-12.
    """
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        return self._conn.execute(translate_sql(sql, backend="sqlite"), params)

    def executemany(self, sql, seq):
        return self._conn.executemany(translate_sql(sql, backend="sqlite"), seq)

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_file = tmp_path / "competitor_profiler_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE govcon_awards (
            id TEXT PRIMARY KEY,
            solicitation_number TEXT,
            title TEXT,
            agency TEXT,
            naics_code TEXT,
            awardee_name TEXT,
            contract_number TEXT,
            award_amount REAL,
            award_date TEXT,
            set_aside_type TEXT,
            content_hash TEXT,
            discovered_at TEXT
        );
    """)
    conn.commit()
    conn.close()

    import tools.govcon.competitor_profiler as cp

    def _get_conn():
        c = sqlite3.connect(str(db_file))
        c.row_factory = sqlite3.Row
        return _TranslatingConn(c)

    monkeypatch.setattr(cp, "get_connection", _get_conn)
    return db_file


def _conn(db_file):
    c = sqlite3.connect(str(db_file))
    c.row_factory = sqlite3.Row
    return c


def _seed_awards(db_file, rows):
    conn = _conn(db_file)
    conn.executemany(
        "INSERT INTO govcon_awards "
        "(id, solicitation_number, title, agency, naics_code, awardee_name, "
        " contract_number, award_amount, award_date, set_aside_type, content_hash, discovered_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


# ── profile_vendor ───────────────────────────────────────────────────────


def test_profile_vendor_no_awards_returns_message(db):
    from tools.govcon.competitor_profiler import profile_vendor

    result = profile_vendor("Nonexistent Corp")
    assert result["status"] == "ok"
    assert result["message"] == "No awards found"


def test_profile_vendor_aggregates_totals(db):
    from tools.govcon.competitor_profiler import profile_vendor

    _seed_awards(db, [
        ("a1", "SOL-1", "T1", "DoD", "541512", "Booz Allen", "C1", 1_000_000, "2026-01-01", None, "h1", "2026-01-01"),
        ("a2", "SOL-2", "T2", "DHS", "541512", "Booz Allen", "C2", 2_000_000, "2026-02-01", "small_business", "h2", "2026-02-01"),
    ])
    result = profile_vendor("Booz Allen")
    assert result["status"] == "ok"
    assert result["total_awards"] == 2
    assert result["total_value"] == 3_000_000
    assert result["avg_award_value"] == 1_500_000


def test_profile_vendor_agency_and_naics_breakdown(db):
    from tools.govcon.competitor_profiler import profile_vendor

    _seed_awards(db, [
        ("a1", "SOL-1", "T1", "DoD", "541512", "Deloitte", "C1", 1_000_000, "2026-01-01", None, "h1", "2026-01-01"),
        ("a2", "SOL-2", "T2", "DoD", "541519", "Deloitte", "C2", 500_000, "2026-02-01", None, "h2", "2026-02-01"),
    ])
    result = profile_vendor("Deloitte")
    assert result["agencies"]["DoD"]["count"] == 2
    assert result["agencies"]["DoD"]["value"] == 1_500_000
    assert set(result["naics"].keys()) == {"541512", "541519"}


def test_profile_vendor_set_aside_breakdown_defaults_full_and_open(db):
    from tools.govcon.competitor_profiler import profile_vendor

    _seed_awards(db, [
        ("a1", "SOL-1", "T1", "DoD", "541512", "Leidos", "C1", 1_000_000, "2026-01-01", None, "h1", "2026-01-01"),
    ])
    result = profile_vendor("Leidos")
    assert result["set_asides"] == {"Full & Open": 1}


def test_profile_vendor_matches_partial_name(db):
    from tools.govcon.competitor_profiler import profile_vendor

    _seed_awards(db, [
        ("a1", "SOL-1", "T1", "DoD", "541512", "Booz Allen Hamilton", "C1", 1_000_000, "2026-01-01", None, "h1", "2026-01-01"),
    ])
    result = profile_vendor("Booz Allen")
    assert result["total_awards"] == 1


def test_profile_vendor_recent_awards_capped_at_ten(db):
    from tools.govcon.competitor_profiler import profile_vendor

    _seed_awards(db, [
        (f"a{i}", f"SOL-{i}", f"T{i}", "DoD", "541512", "Serco", f"C{i}", 100_000,
         f"2026-01-{i:02d}", None, f"h{i}", f"2026-01-{i:02d}")
        for i in range(1, 13)
    ])
    result = profile_vendor("Serco")
    assert result["total_awards"] == 12
    assert len(result["recent_awards"]) == 10


# ── get_leaderboard ───────────────────────────────────────────────────────


def test_leaderboard_ranks_by_award_count_descending(db):
    from tools.govcon.competitor_profiler import get_leaderboard

    _seed_awards(db, [
        ("a1", "SOL-1", "T1", "DoD", "541512", "Vendor A", "C1", 1_000_000, "2026-01-01", None, "h1", "2026-01-01"),
        ("a2", "SOL-2", "T2", "DoD", "541512", "Vendor A", "C2", 1_000_000, "2026-01-02", None, "h2", "2026-01-02"),
        ("a3", "SOL-3", "T3", "DoD", "541512", "Vendor B", "C3", 5_000_000, "2026-01-03", None, "h3", "2026-01-03"),
    ])
    result = get_leaderboard()
    assert result["status"] == "ok"
    assert result["leaderboard"][0]["vendor"] == "Vendor A"
    assert result["leaderboard"][0]["awards"] == 2
    assert result["leaderboard"][0]["rank"] == 1
    assert result["leaderboard"][1]["vendor"] == "Vendor B"
    assert result["leaderboard"][1]["rank"] == 2


def test_leaderboard_filters_by_naics(db):
    from tools.govcon.competitor_profiler import get_leaderboard

    _seed_awards(db, [
        ("a1", "SOL-1", "T1", "DoD", "541512", "Vendor A", "C1", 1_000_000, "2026-01-01", None, "h1", "2026-01-01"),
        ("a2", "SOL-2", "T2", "DoD", "541990", "Vendor B", "C2", 1_000_000, "2026-01-02", None, "h2", "2026-01-02"),
    ])
    result = get_leaderboard(naics="541512")
    vendors = {row["vendor"] for row in result["leaderboard"]}
    assert vendors == {"Vendor A"}


def test_leaderboard_filters_by_agency_substring(db):
    from tools.govcon.competitor_profiler import get_leaderboard

    _seed_awards(db, [
        ("a1", "SOL-1", "T1", "Department of Defense", "541512", "Vendor A", "C1", 1_000_000, "2026-01-01", None, "h1", "2026-01-01"),
        ("a2", "SOL-2", "T2", "DHS", "541512", "Vendor B", "C2", 1_000_000, "2026-01-02", None, "h2", "2026-01-02"),
    ])
    result = get_leaderboard(agency="Defense")
    vendors = {row["vendor"] for row in result["leaderboard"]}
    assert vendors == {"Vendor A"}


def test_leaderboard_respects_limit(db):
    from tools.govcon.competitor_profiler import get_leaderboard

    _seed_awards(db, [
        (f"a{i}", f"SOL-{i}", f"T{i}", "DoD", "541512", f"Vendor {i}", f"C{i}", 100_000,
         "2026-01-01", None, f"h{i}", "2026-01-01")
        for i in range(5)
    ])
    result = get_leaderboard(limit=2)
    assert len(result["leaderboard"]) == 2


def test_leaderboard_computes_diversity_counts(db):
    from tools.govcon.competitor_profiler import get_leaderboard

    _seed_awards(db, [
        ("a1", "SOL-1", "T1", "DoD", "541512", "Vendor A", "C1", 1_000_000, "2026-01-01", None, "h1", "2026-01-01"),
        ("a2", "SOL-2", "T2", "DHS", "541990", "Vendor A", "C2", 1_000_000, "2026-01-02", None, "h2", "2026-01-02"),
    ])
    result = get_leaderboard()
    row = result["leaderboard"][0]
    assert row["naics_diversity"] == 2
    assert row["agency_diversity"] == 2


def test_leaderboard_empty_when_no_awards(db):
    from tools.govcon.competitor_profiler import get_leaderboard

    result = get_leaderboard()
    assert result["leaderboard"] == []


# ── compare_vendors ───────────────────────────────────────────────────────


def test_compare_vendors_returns_one_profile_per_vendor(db):
    from tools.govcon.competitor_profiler import compare_vendors

    _seed_awards(db, [
        ("a1", "SOL-1", "T1", "DoD", "541512", "Vendor A", "C1", 1_000_000, "2026-01-01", None, "h1", "2026-01-01"),
        ("a2", "SOL-2", "T2", "DoD", "541512", "Vendor B", "C2", 2_000_000, "2026-01-02", None, "h2", "2026-01-02"),
    ])
    result = compare_vendors(["Vendor A", "Vendor B"])
    assert result["vendors_compared"] == 2
    names = {p["vendor"] for p in result["profiles"]}
    assert names == {"Vendor A", "Vendor B"}


def test_compare_vendors_strips_whitespace(db):
    from tools.govcon.competitor_profiler import compare_vendors

    _seed_awards(db, [
        ("a1", "SOL-1", "T1", "DoD", "541512", "Vendor A", "C1", 1_000_000, "2026-01-01", None, "h1", "2026-01-01"),
    ])
    result = compare_vendors([" Vendor A ", "Nonexistent"])
    profiles_by_vendor = {p["vendor"]: p for p in result["profiles"]}
    assert profiles_by_vendor["Vendor A"]["total_awards"] == 1
    assert profiles_by_vendor["Nonexistent"]["message"] == "No awards found"
