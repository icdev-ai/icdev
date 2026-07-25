# CUI // SP-CTI
"""Tests for the past-performance suggestion helper (crx-gov-01).

Fully offline/air-gap safe: no live embedding provider is required — the helper
degrades to a deterministic token-overlap similarity so ranking is exercised
without network access.

All fixture data is synthesized (public-knowledge phrasing only); ICDEV is
public, so no real customer performance data is used.

Invariants asserted:
  - completed synthetic contracts + an opportunity yield ranked suggestions
  - the most capability/agency/NAICS-similar contract ranks first
  - every suggestion carries a valid ``[source: cpmp_contracts:<id>]`` citation
    to a real CPMP record (grounding)
  - nothing is auto-inserted (``auto_inserted`` is always False; the helper is
    read-only over the CPMP tables)
  - a bare DB without the CPMP tables degrades to zero suggestions, no error
"""
import importlib
import uuid

import pytest

pps = importlib.import_module("tools.govcon.past_performance_suggester")
cg = importlib.import_module("tools.quality.citation_grounding")


# --- Self-contained schema (conftest MINIMAL schema is not auto-applied to a
#     bare get_connection()) ---------------------------------------------------

def _create_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cpmp_contracts (
            id TEXT PRIMARY KEY,
            contract_number TEXT,
            title TEXT,
            agency TEXT,
            agency_hierarchy TEXT,
            contract_type TEXT,
            naics_code TEXT,
            total_value REAL DEFAULT 0.0,
            cpars_rating_current TEXT,
            pop_start TEXT,
            pop_end TEXT,
            status TEXT DEFAULT 'active',
            opportunity_id TEXT,
            notes TEXT,
            metadata TEXT DEFAULT '{}',
            tenant_id TEXT,
            classification TEXT DEFAULT 'CUI'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cpmp_cpars_assessments (
            id TEXT PRIMARY KEY,
            contract_id TEXT,
            period_start TEXT,
            period_end TEXT,
            overall_rating TEXT,
            overall_score REAL,
            classification TEXT DEFAULT 'CUI'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pg_win_loss_records (
            id TEXT PRIMARY KEY,
            opportunity_id TEXT,
            outcome TEXT,
            created_at TEXT,
            classification TEXT DEFAULT 'CUI'
        )
        """
    )
    conn.commit()


def _insert_contract(conn, *, cid, number, title, agency, naics, notes,
                     value, status="complete", opp_id=None):
    conn.execute(
        "INSERT INTO cpmp_contracts (id, contract_number, title, agency, "
        "contract_type, naics_code, total_value, status, opportunity_id, notes) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (cid, number, title, agency, "FFP", naics, value, status, opp_id, notes),
    )


@pytest.fixture()
def seeded_conn():
    from tools.db.storage import get_connection

    conn = get_connection()
    _create_tables(conn)

    # Target opportunity: cyber zero-trust modernization for DHS, NAICS 541512.
    # C1 is the strong match; C2 partial (same agency, different domain);
    # C3 weak (different agency + domain).
    _insert_contract(
        conn, cid="c1", number="HSHQDC-20-C-0001",
        title="Zero Trust Architecture cybersecurity modernization",
        agency="Department of Homeland Security", naics="541512",
        notes="Implemented zero trust network access, identity, and continuous "
              "monitoring across enterprise cloud environments.",
        value=12_000_000.0, opp_id="opp-c1",
    )
    _insert_contract(
        conn, cid="c2", number="HSHQDC-19-C-0044",
        title="Help desk and end-user IT support services",
        agency="Department of Homeland Security", naics="541519",
        notes="Tier 1-3 service desk and desktop support.",
        value=3_500_000.0, opp_id="opp-c2",
    )
    _insert_contract(
        conn, cid="c3", number="GS-35F-0300",
        title="Grounds landscaping maintenance",
        agency="General Services Administration", naics="561730",
        notes="Mowing, irrigation, and seasonal planting.",
        value=800_000.0, status="active", opp_id="opp-c3",
    )
    # A draft contract must be excluded (not completed).
    _insert_contract(
        conn, cid="c4", number="DRAFT-001",
        title="Zero trust cybersecurity draft",
        agency="Department of Homeland Security", naics="541512",
        notes="not yet awarded", value=0.0, status="draft", opp_id=None,
    )

    conn.execute(
        "INSERT INTO cpmp_cpars_assessments (id, contract_id, period_start, "
        "period_end, overall_rating, overall_score) VALUES "
        "(%s, %s, %s, %s, %s, %s)",
        (str(uuid.uuid4()), "c1", "2021-01-01", "2022-01-01", "exceptional", 4.6),
    )
    conn.execute(
        "INSERT INTO pg_win_loss_records (id, opportunity_id, outcome, created_at) "
        "VALUES (%s, %s, %s, %s)",
        (str(uuid.uuid4()), "opp-c1", "won", "2022-02-01T00:00:00Z"),
    )
    conn.commit()
    try:
        yield conn
    finally:
        for t in ("pg_win_loss_records", "cpmp_cpars_assessments", "cpmp_contracts"):
            try:
                conn.execute(f"DROP TABLE IF EXISTS {t}")
            except Exception:
                pass
        conn.commit()
        conn.close()


OPPORTUNITY = {
    "title": "Enterprise Zero Trust Cybersecurity Modernization",
    "requirements": "Deploy zero trust architecture, identity and continuous "
                    "monitoring for cloud cybersecurity modernization.",
    "capabilities": ["zero trust", "cybersecurity", "continuous monitoring"],
    "agency": "Department of Homeland Security",
    "naics_code": "541512",
}


def test_ranked_suggestions_with_citations(seeded_conn):
    result = pps.suggest_references(OPPORTUNITY, top_k=5, conn=seeded_conn)

    assert result["auto_inserted"] is False
    assert result["count"] >= 1
    sugg = result["suggestions"]

    # Draft contract (c4) must never be suggested.
    assert all(s["contract_id"] != "c4" for s in sugg)

    # The strongest match ranks first.
    assert sugg[0]["contract_id"] == "c1"
    assert sugg[0]["rank"] == 1

    # Ranking is monotonic in score.
    scores = [s["score"] for s in sugg]
    assert scores == sorted(scores, reverse=True)

    # Every suggestion carries a valid, non-hallucinated CPMP citation.
    for s in sugg:
        assert cg.has_citations(s["summary"])
        ids = cg.parse_citations(s["citation"])
        assert any(i.startswith("cpmp_contracts:") for i in ids)
        report = cg.validate_citations(s["summary"], set(s["sources"]))
        assert report["hallucinated_citations"] == []

    # Composed metrics surface from CPARS + win/loss without new data sources.
    top = sugg[0]
    assert top["cpars_rating"] == "exceptional"
    assert top["outcome"] == "won"
    assert top["agency_match"] is True
    assert top["naics_match"] is True
    assert top["total_value"] == 12_000_000.0


def test_top_k_limits_results(seeded_conn):
    result = pps.suggest_references(OPPORTUNITY, top_k=1, conn=seeded_conn)
    assert len(result["suggestions"]) == 1
    assert result["suggestions"][0]["contract_id"] == "c1"


def test_nothing_written_back(seeded_conn):
    """Read-only invariant: calling the helper must not INSERT/UPDATE anything."""
    before = seeded_conn.execute("SELECT COUNT(*) FROM cpmp_contracts").fetchone()[0]
    pps.suggest_references(OPPORTUNITY, top_k=5, conn=seeded_conn)
    after = seeded_conn.execute("SELECT COUNT(*) FROM cpmp_contracts").fetchone()[0]
    assert before == after


def test_missing_tables_degrade_gracefully():
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        for t in ("cpmp_contracts", "cpmp_cpars_assessments", "pg_win_loss_records"):
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.commit()
        result = pps.suggest_references(OPPORTUNITY, top_k=5, conn=conn)
        assert result["count"] == 0
        assert result["suggestions"] == []
        assert result["auto_inserted"] is False
    finally:
        conn.close()


def test_deterministic_similarity_is_offline_safe(monkeypatch, seeded_conn):
    """Force the no-provider path and confirm ranking still works offline."""
    monkeypatch.setattr(pps, "_embed_similarities", lambda *a, **k: None)
    result = pps.suggest_references(OPPORTUNITY, top_k=5, conn=seeded_conn)
    assert result["method"] == "deterministic"
    assert result["suggestions"][0]["contract_id"] == "c1"
