# [TEMPLATE: CUI // SP-CTI]
"""Tests for tools/govcon/sam_scanner.py::list_forecast_notices (prop-cap-14).

SAM.gov's Opportunities API has no distinct "Forecast" notice type;
presolicitation ('p') is the closest advance-notice equivalent available
through the public API, so this treats notice_type='p' as the forecast
feed. Reuses the existing sam_gov_opportunities cache -- no new scanning
logic, just a filtered read.
"""
import sqlite3

import pytest


_SCHEMA = """
CREATE TABLE sam_gov_opportunities (
    id TEXT PRIMARY KEY,
    solicitation_number TEXT,
    title TEXT,
    agency TEXT,
    agency_hierarchy TEXT,
    naics_code TEXT,
    classification_code TEXT,
    notice_type TEXT,
    posted_date TEXT,
    response_deadline TEXT,
    description TEXT,
    point_of_contact TEXT,
    set_aside_type TEXT,
    place_of_performance TEXT,
    attachment_urls TEXT,
    active TEXT DEFAULT 'true',
    content_hash TEXT,
    metadata TEXT,
    first_seen TEXT,
    last_synced TEXT,
    classification TEXT DEFAULT 'CUI'
);
"""


@pytest.fixture()
def db(tmp_path):
    db_file = tmp_path / "sam_forecast_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return db_file


def _conn(db_file):
    c = sqlite3.connect(str(db_file))
    c.row_factory = sqlite3.Row
    return c


def _seed(db_file, rows):
    conn = _conn(db_file)
    conn.executemany(
        "INSERT INTO sam_gov_opportunities "
        "(id, solicitation_number, title, agency, notice_type, posted_date, active, content_hash, first_seen, last_synced) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_no_notices_returns_empty(db):
    from tools.govcon.sam_scanner import list_forecast_notices

    result = list_forecast_notices(db_path=db)
    assert result == {"notices": [], "count": 0}


def test_only_presolicitation_notices_returned(db):
    from tools.govcon.sam_scanner import list_forecast_notices

    _seed(db, [
        ("n1", "SOL-1", "Presolicitation Notice", "DoD", "p", "2026-06-01", "true", "h1", "2026-06-01", "2026-06-01"),
        ("n2", "SOL-2", "Live Solicitation", "DoD", "o", "2026-06-02", "true", "h2", "2026-06-02", "2026-06-02"),
        ("n3", "SOL-3", "Sources Sought", "DoD", "r", "2026-06-03", "true", "h3", "2026-06-03", "2026-06-03"),
    ])
    result = list_forecast_notices(db_path=db)
    assert result["count"] == 1
    assert result["notices"][0]["id"] == "n1"


def test_excludes_inactive_notices(db):
    from tools.govcon.sam_scanner import list_forecast_notices

    _seed(db, [
        ("n1", "SOL-1", "Active Presol", "DoD", "p", "2026-06-01", "true", "h1", "2026-06-01", "2026-06-01"),
        ("n2", "SOL-2", "Archived Presol", "DoD", "p", "2026-01-01", "false", "h2", "2026-01-01", "2026-01-01"),
    ])
    result = list_forecast_notices(db_path=db)
    assert result["count"] == 1
    assert result["notices"][0]["id"] == "n1"


def test_ordered_most_recently_posted_first(db):
    from tools.govcon.sam_scanner import list_forecast_notices

    _seed(db, [
        ("n1", "SOL-1", "Older", "DoD", "p", "2026-01-01", "true", "h1", "2026-01-01", "2026-01-01"),
        ("n2", "SOL-2", "Newer", "DoD", "p", "2026-06-01", "true", "h2", "2026-06-01", "2026-06-01"),
    ])
    result = list_forecast_notices(db_path=db)
    ids = [n["id"] for n in result["notices"]]
    assert ids == ["n2", "n1"]


def test_respects_limit(db):
    from tools.govcon.sam_scanner import list_forecast_notices

    _seed(db, [
        (f"n{i}", f"SOL-{i}", f"Presol {i}", "DoD", "p", f"2026-01-{i:02d}", "true", f"h{i}", f"2026-01-{i:02d}", f"2026-01-{i:02d}")
        for i in range(1, 6)
    ])
    result = list_forecast_notices(db_path=db, limit=2)
    assert result["count"] == 2
