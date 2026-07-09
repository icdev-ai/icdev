# CUI // SP-CTI
"""IQE adapter — GovCon / Proposals canvas (prop-cap-13, prop-iqe-01).

Collections:
    govcon.opportunities  — proposal_opportunities (active pipeline)
    govcon.awards         — govcon_awards (competitor award history)
    govcon.blackhat       — proposal_blackhat_assessments (black-hat models)
    govcon.competitors    — proposal_competitors (per-opportunity competitor sub-table)
    govcon.requirements   — rfp_requirement_patterns (extracted shall/must/will patterns)
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _conn(conn: Any):
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    return conn


def opportunities_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, title, agency, sub_agency, solicitation_number, naics_code, "
            "due_date, status, proposal_type, set_aside_type, "
            "estimated_value_low, estimated_value_high, "
            "win_probability, ptw_low, ptw_high, capture_phase, capture_manager, "
            "classification, created_at "
            "FROM proposal_opportunities ORDER BY due_date ASC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def awards_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT awardee_name, contract_number, award_amount, agency, naics_code, "
            "set_aside_type, award_date, "
            "period_of_performance_start, period_of_performance_end "
            "FROM govcon_awards ORDER BY award_date DESC LIMIT 500"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def blackhat_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        c.execute("""
            CREATE TABLE IF NOT EXISTS proposal_blackhat_assessments (
                id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL,
                competitor_name TEXT NOT NULL, approach_hypothesis TEXT,
                price_estimate_low REAL, price_estimate_high REAL,
                strengths TEXT, weaknesses TEXT, win_strategy TEXT,
                differentiators TEXT, risk_factors TEXT,
                ptw_posture TEXT DEFAULT 'competitive',
                leaderboard_rank INTEGER, award_count INTEGER,
                total_award_value REAL, naics_diversity INTEGER,
                agency_diversity INTEGER, classification TEXT DEFAULT 'CUI',
                created_at TEXT NOT NULL, updated_at TEXT, created_by TEXT
            )
        """)
        cur = c.execute(
            "SELECT id, opportunity_id, competitor_name, approach_hypothesis, "
            "ptw_posture, price_estimate_low, price_estimate_high, "
            "strengths, weaknesses, win_strategy, differentiators, risk_factors, "
            "leaderboard_rank, award_count, total_award_value, "
            "naics_diversity, agency_diversity, classification, created_at "
            "FROM proposal_blackhat_assessments ORDER BY created_at DESC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def competitors_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, opportunity_id, company_name, incumbent, strengths, weaknesses, "
            "estimated_price, win_probability_pct, notes, classification "
            "FROM proposal_competitors ORDER BY company_name"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def requirements_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, pattern_name, description, domain_category, frequency, "
            "keywords, representative_text, capability_coverage, status, "
            "first_seen, last_seen, classification "
            "FROM rfp_requirement_patterns ORDER BY frequency DESC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


register_collection("govcon.opportunities", opportunities_adapter)
register_collection("govcon.awards", awards_adapter)
register_collection("govcon.blackhat", blackhat_adapter)
register_collection("govcon.competitors", competitors_adapter)
register_collection("govcon.requirements", requirements_adapter)
