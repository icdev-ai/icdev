# [TEMPLATE: CUI // SP-CTI]
"""Tests for the govcon.requirements collection added to
tools/iqe/adapters/govcon.py (prop-iqe-01) — reads rfp_requirement_patterns
(extracted shall/must/will patterns), joining IQE coverage for the GovCon
canvas alongside the pre-existing opportunities/awards/blackhat/competitors
collections (prop-cap-13).
"""
import sqlite3

import pytest


_SCHEMA = """
CREATE TABLE rfp_requirement_patterns (
    id TEXT PRIMARY KEY, pattern_name TEXT, description TEXT, domain_category TEXT,
    frequency INTEGER, keywords TEXT, representative_text TEXT,
    capability_coverage REAL, status TEXT, first_seen TEXT, last_seen TEXT,
    classification TEXT
);
"""


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(_SCHEMA)
    c.commit()
    yield c
    c.close()


def test_requirements_adapter_empty(conn):
    from tools.iqe.adapters.govcon import requirements_adapter

    assert requirements_adapter(conn) == []


def test_requirements_adapter_returns_real_rows(conn):
    from tools.iqe.adapters.govcon import requirements_adapter

    conn.execute(
        "INSERT INTO rfp_requirement_patterns "
        "(id, pattern_name, domain_category, frequency, status, first_seen, last_seen) "
        "VALUES ('pat-1', 'Zero Trust Architecture', 'devsecops', 5, 'gap_identified', '2026-01-01', '2026-06-01')"
    )
    conn.commit()
    rows = requirements_adapter(conn)
    assert len(rows) == 1
    assert rows[0]["pattern_name"] == "Zero Trust Architecture"
    assert rows[0]["domain_category"] == "devsecops"
    assert rows[0]["status"] == "gap_identified"


def test_requirements_adapter_orders_by_frequency_descending(conn):
    from tools.iqe.adapters.govcon import requirements_adapter

    conn.executemany(
        "INSERT INTO rfp_requirement_patterns "
        "(id, pattern_name, domain_category, frequency, status, first_seen, last_seen) "
        "VALUES (?, ?, 'devsecops', ?, 'new', '2026-01-01', '2026-01-01')",
        [("pat-low", "Low Freq", 1), ("pat-high", "High Freq", 10)],
    )
    conn.commit()
    rows = requirements_adapter(conn)
    assert [r["pattern_name"] for r in rows] == ["High Freq", "Low Freq"]


def test_requirements_adapter_returns_empty_list_on_missing_table():
    from tools.iqe.adapters.govcon import requirements_adapter

    c = sqlite3.connect(":memory:")
    try:
        assert requirements_adapter(c) == []
    finally:
        c.close()


def test_requirements_collection_registered():
    import tools.iqe.adapters.govcon  # noqa: F401
    from tools.iqe.executor import _default

    assert "govcon.requirements" in _default._registry
