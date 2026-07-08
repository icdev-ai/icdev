# [TEMPLATE: CUI // SP-CTI]
"""Tests for tools/govcon/crm_heat.py::get_engagement_heat_by_agency (prop-cap-14).

Joins pg_crm_accounts (by agency) to each account's most recent
pg_crm_engagement_scores row, aggregating to a per-agency heat reading
(level/score/interaction_count/last_interaction_at/account_count) for the
GovCon BD pipeline view.
"""
import sqlite3

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_file = tmp_path / "crm_heat_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE pg_crm_accounts (
            id TEXT PRIMARY KEY, name TEXT, agency TEXT
        );
        CREATE TABLE pg_crm_engagement_scores (
            id TEXT PRIMARY KEY, account_id TEXT, score REAL,
            interaction_count INTEGER, last_interaction_at TEXT, created_at TEXT
        );
    """)
    conn.commit()
    conn.close()

    monkeypatch.setenv("ICDEV_DB_PATH", str(db_file))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    return db_file


def _conn(db_file):
    c = sqlite3.connect(str(db_file))
    c.row_factory = sqlite3.Row
    return c


def _seed_account(db_file, account_id, agency, name="Test Account"):
    conn = _conn(db_file)
    conn.execute(
        "INSERT INTO pg_crm_accounts (id, name, agency) VALUES (?, ?, ?)",
        (account_id, name, agency),
    )
    conn.commit()
    conn.close()


def _seed_score(db_file, account_id, score, interaction_count, last_interaction_at, created_at):
    conn = _conn(db_file)
    conn.execute(
        "INSERT INTO pg_crm_engagement_scores "
        "(id, account_id, score, interaction_count, last_interaction_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (f"score-{account_id}-{created_at}", account_id, score, interaction_count, last_interaction_at, created_at),
    )
    conn.commit()
    conn.close()


def test_empty_agency_list_returns_empty_dict(db):
    from tools.govcon.crm_heat import get_engagement_heat_by_agency

    assert get_engagement_heat_by_agency([]) == {}
    assert get_engagement_heat_by_agency([None, ""]) == {}


def test_agency_with_no_crm_account_absent_from_result(db):
    from tools.govcon.crm_heat import get_engagement_heat_by_agency

    result = get_engagement_heat_by_agency(["Nonexistent Agency"])
    assert result == {}


def test_account_with_no_score_row_absent_from_result(db):
    from tools.govcon.crm_heat import get_engagement_heat_by_agency

    _seed_account(db, "acct-1", "DoD")
    result = get_engagement_heat_by_agency(["DoD"])
    assert result == {}


def test_single_account_hot_score(db):
    from tools.govcon.crm_heat import get_engagement_heat_by_agency

    _seed_account(db, "acct-1", "DoD")
    _seed_score(db, "acct-1", 75.0, 12, "2026-06-01T00:00:00Z", "2026-06-01T00:00:00Z")

    result = get_engagement_heat_by_agency(["DoD"])
    assert result["DoD"]["level"] == "hot"
    assert result["DoD"]["score"] == 75.0
    assert result["DoD"]["interaction_count"] == 12
    assert result["DoD"]["account_count"] == 1


def test_single_account_warm_score(db):
    from tools.govcon.crm_heat import get_engagement_heat_by_agency

    _seed_account(db, "acct-1", "DHS")
    _seed_score(db, "acct-1", 40.0, 5, "2026-06-01T00:00:00Z", "2026-06-01T00:00:00Z")

    result = get_engagement_heat_by_agency(["DHS"])
    assert result["DHS"]["level"] == "warm"


def test_single_account_cold_score(db):
    from tools.govcon.crm_heat import get_engagement_heat_by_agency

    _seed_account(db, "acct-1", "GSA")
    _seed_score(db, "acct-1", 5.0, 1, "2026-06-01T00:00:00Z", "2026-06-01T00:00:00Z")

    result = get_engagement_heat_by_agency(["GSA"])
    assert result["GSA"]["level"] == "cold"


def test_uses_most_recent_score_per_account(db):
    from tools.govcon.crm_heat import get_engagement_heat_by_agency

    _seed_account(db, "acct-1", "DoD")
    _seed_score(db, "acct-1", 10.0, 1, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
    _seed_score(db, "acct-1", 80.0, 3, "2026-06-01T00:00:00Z", "2026-06-01T00:00:00Z")

    result = get_engagement_heat_by_agency(["DoD"])
    assert result["DoD"]["score"] == 80.0
    assert result["DoD"]["interaction_count"] == 3


def test_multiple_accounts_same_agency_averaged_and_summed(db):
    from tools.govcon.crm_heat import get_engagement_heat_by_agency

    _seed_account(db, "acct-1", "DoD", name="Sub Agency A")
    _seed_account(db, "acct-2", "DoD", name="Sub Agency B")
    _seed_score(db, "acct-1", 80.0, 10, "2026-06-01T00:00:00Z", "2026-06-01T00:00:00Z")
    _seed_score(db, "acct-2", 40.0, 5, "2026-05-01T00:00:00Z", "2026-05-01T00:00:00Z")

    result = get_engagement_heat_by_agency(["DoD"])
    assert result["DoD"]["score"] == 60.0
    assert result["DoD"]["interaction_count"] == 15
    assert result["DoD"]["account_count"] == 2
    assert result["DoD"]["last_interaction_at"] == "2026-06-01T00:00:00Z"


def test_multiple_agencies_independent(db):
    from tools.govcon.crm_heat import get_engagement_heat_by_agency

    _seed_account(db, "acct-1", "DoD")
    _seed_score(db, "acct-1", 90.0, 20, "2026-06-01T00:00:00Z", "2026-06-01T00:00:00Z")
    _seed_account(db, "acct-2", "DHS")
    _seed_score(db, "acct-2", 10.0, 1, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")

    result = get_engagement_heat_by_agency(["DoD", "DHS", "Nonexistent"])
    assert result["DoD"]["level"] == "hot"
    assert result["DHS"]["level"] == "cold"
    assert "Nonexistent" not in result


def test_missing_crm_tables_returns_empty_dict_not_raises(tmp_path, monkeypatch):
    from tools.govcon.crm_heat import get_engagement_heat_by_agency

    db_file = tmp_path / "no_crm_tables.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE dummy (id TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_file))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    result = get_engagement_heat_by_agency(["DoD"])
    assert result == {}
