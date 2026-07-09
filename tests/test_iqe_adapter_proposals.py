# [TEMPLATE: CUI // SP-CTI]
"""Tests for tools/iqe/adapters/proposals.py (prop-iqe-01).

Registers 4 read-only IQE collections mirroring tools/iqe/adapters/govcon.py's
pattern: proposals.opportunities, proposals.sections, proposals.compliance,
proposals.reviews. Each adapter takes a connection and returns real rows as
dicts; none take SQL parameters, so a bare sqlite3.Connection (no %s
translation needed) is sufficient here.
"""
import sqlite3

import pytest


_SCHEMA = """
CREATE TABLE proposal_opportunities (
    id TEXT PRIMARY KEY, title TEXT, agency TEXT, sub_agency TEXT,
    solicitation_number TEXT, naics_code TEXT, due_date TEXT, status TEXT,
    proposal_type TEXT, set_aside_type TEXT, estimated_value_low REAL,
    estimated_value_high REAL, win_probability REAL, capture_phase TEXT,
    capture_manager TEXT, proposal_manager TEXT, classification TEXT, created_at TEXT
);
CREATE TABLE proposal_sections (
    id TEXT PRIMARY KEY, opportunity_id TEXT, volume_id TEXT, section_number TEXT,
    title TEXT, writer TEXT, reviewer TEXT, status TEXT, priority TEXT,
    page_limit INTEGER, word_limit INTEGER, current_word_count INTEGER,
    current_page_count INTEGER, due_date TEXT
);
CREATE TABLE proposal_compliance_matrix (
    id TEXT PRIMARY KEY, opportunity_id TEXT, section_ref TEXT, volume_ref TEXT,
    requirement_text TEXT, requirement_type TEXT, compliance_status TEXT,
    response_summary TEXT, sort_order INTEGER, classification TEXT, created_at TEXT
);
CREATE TABLE proposal_reviews (
    id TEXT PRIMARY KEY, opportunity_id TEXT, review_type TEXT, status TEXT,
    scheduled_date TEXT, started_at TEXT, completed_at TEXT, lead_reviewer TEXT,
    overall_rating TEXT, classification TEXT, created_at TEXT
);
"""


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(_SCHEMA)
    c.commit()
    yield c
    c.close()


# ── opportunities_adapter ────────────────────────────────────────────────


def test_opportunities_adapter_empty(conn):
    from tools.iqe.adapters.proposals import opportunities_adapter

    assert opportunities_adapter(conn) == []


def test_opportunities_adapter_returns_real_rows(conn):
    from tools.iqe.adapters.proposals import opportunities_adapter

    conn.execute(
        "INSERT INTO proposal_opportunities (id, title, agency, status, due_date, created_at) "
        "VALUES ('opp-1', 'Cyber IDIQ', 'DoD', 'writing', '2026-12-31', '2026-01-01')"
    )
    conn.commit()
    rows = opportunities_adapter(conn)
    assert len(rows) == 1
    assert rows[0]["id"] == "opp-1"
    assert rows[0]["title"] == "Cyber IDIQ"
    assert rows[0]["status"] == "writing"


def test_opportunities_adapter_returns_empty_list_on_missing_table():
    from tools.iqe.adapters.proposals import opportunities_adapter

    c = sqlite3.connect(":memory:")
    try:
        assert opportunities_adapter(c) == []
    finally:
        c.close()


# ── sections_adapter ─────────────────────────────────────────────────────


def test_sections_adapter_returns_real_rows(conn):
    from tools.iqe.adapters.proposals import sections_adapter

    conn.execute(
        "INSERT INTO proposal_sections (id, opportunity_id, section_number, title, status) "
        "VALUES ('sec-1', 'opp-1', '2.1', 'Technical Approach', 'drafting')"
    )
    conn.commit()
    rows = sections_adapter(conn)
    assert len(rows) == 1
    assert rows[0]["title"] == "Technical Approach"
    assert rows[0]["status"] == "drafting"


def test_sections_adapter_empty(conn):
    from tools.iqe.adapters.proposals import sections_adapter

    assert sections_adapter(conn) == []


# ── compliance_adapter ───────────────────────────────────────────────────


def test_compliance_adapter_returns_real_rows(conn):
    from tools.iqe.adapters.proposals import compliance_adapter

    conn.execute(
        "INSERT INTO proposal_compliance_matrix "
        "(id, opportunity_id, section_ref, requirement_text, requirement_type, compliance_status, sort_order) "
        "VALUES ('cm-1', 'opp-1', 'L.2.1', 'Shall provide technical approach', 'L', 'not_addressed', 1)"
    )
    conn.commit()
    rows = compliance_adapter(conn)
    assert len(rows) == 1
    assert rows[0]["compliance_status"] == "not_addressed"
    assert rows[0]["requirement_type"] == "L"


def test_compliance_adapter_empty(conn):
    from tools.iqe.adapters.proposals import compliance_adapter

    assert compliance_adapter(conn) == []


# ── reviews_adapter ───────────────────────────────────────────────────────


def test_reviews_adapter_returns_real_rows(conn):
    from tools.iqe.adapters.proposals import reviews_adapter

    conn.execute(
        "INSERT INTO proposal_reviews (id, opportunity_id, review_type, status, overall_rating, created_at) "
        "VALUES ('rev-1', 'opp-1', 'pink_team', 'completed', 'pass_with_findings', '2026-06-01')"
    )
    conn.commit()
    rows = reviews_adapter(conn)
    assert len(rows) == 1
    assert rows[0]["review_type"] == "pink_team"
    assert rows[0]["overall_rating"] == "pass_with_findings"


def test_reviews_adapter_empty(conn):
    from tools.iqe.adapters.proposals import reviews_adapter

    assert reviews_adapter(conn) == []


# ── registration ──────────────────────────────────────────────────────────


def test_all_four_collections_registered():
    import tools.iqe.adapters.proposals  # noqa: F401
    from tools.iqe.executor import _default

    for name in (
        "proposals.opportunities", "proposals.sections",
        "proposals.compliance", "proposals.reviews",
    ):
        assert name in _default._registry
