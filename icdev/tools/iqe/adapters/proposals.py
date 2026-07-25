# CUI // SP-CTI
"""IQE adapter — Proposals canvas (prop-iqe-01).

Collections:
    proposals.opportunities — proposal_opportunities (proposal lifecycle records)
    proposals.sections      — proposal_sections (volume/section drafting status)
    proposals.compliance    — proposal_compliance_matrix (L/M/N compliance tracking)
    proposals.reviews       — proposal_reviews (color-team review events)
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
            "estimated_value_low, estimated_value_high, win_probability, "
            "capture_phase, capture_manager, proposal_manager, "
            "classification, created_at "
            "FROM proposal_opportunities ORDER BY due_date ASC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def sections_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, opportunity_id, volume_id, section_number, title, "
            "writer, reviewer, status, priority, page_limit, word_limit, "
            "current_word_count, current_page_count, due_date "
            "FROM proposal_sections ORDER BY section_number ASC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def compliance_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, opportunity_id, section_ref, volume_ref, requirement_text, "
            "requirement_type, compliance_status, response_summary, "
            "classification, created_at "
            "FROM proposal_compliance_matrix ORDER BY sort_order ASC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def reviews_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, opportunity_id, review_type, status, scheduled_date, "
            "started_at, completed_at, lead_reviewer, overall_rating, "
            "classification, created_at "
            "FROM proposal_reviews ORDER BY created_at DESC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


register_collection("proposals.opportunities", opportunities_adapter)
register_collection("proposals.sections", sections_adapter)
register_collection("proposals.compliance", compliance_adapter)
register_collection("proposals.reviews", reviews_adapter)
