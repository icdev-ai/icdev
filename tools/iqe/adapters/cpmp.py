# CUI // SP-CTI
"""IQE adapter — CPMP (Contract Portfolio Management) canvas.

Collections:
    cpmp.contracts    — cpmp_contracts (active contract portfolio)
    cpmp.deliverables — cpmp_deliverables (CDRL deliverable tracking)
    cpmp.clins        — cpmp_clins (contract line item numbers)
    cpmp.cpars        — cpmp_cpars_assessments (contractor performance assessments)
    cpmp.evm          — cpmp_evm_periods (earned value management data)
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _conn(conn: Any):
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    return conn


def _rollback(c: Any) -> None:
    # A failed adapter query must not leave the shared PG connection in an
    # aborted transaction, or every later collection reads as empty.
    try:
        c.rollback()
    except Exception:
        pass


def contracts_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, contract_number, title, agency, contract_type, "
            "total_value, funded_value, obligated_value, pop_start, pop_end, "
            "status, health, health_score, cor_email, classification, created_at "
            "FROM cpmp_contracts ORDER BY created_at DESC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        _rollback(c)
        return []


def deliverables_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, contract_id, cdrl_number, title, due_date, status, "
            "deliverable_type, classification, created_at "
            "FROM cpmp_deliverables ORDER BY due_date ASC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        _rollback(c)
        return []


def clins_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, contract_id, clin_number, description, clin_type, "
            "funded_value, obligated_value, status, classification, created_at "
            "FROM cpmp_clins ORDER BY clin_number ASC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        _rollback(c)
        return []


def cpars_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, contract_id, period_start, period_end, quality_rating, "
            "schedule_rating, cost_rating, management_rating, "
            "small_business_rating, overall_rating, narrative, "
            "classification, created_at "
            "FROM cpmp_cpars_assessments ORDER BY created_at DESC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        _rollback(c)
        return []


def evm_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, contract_id, period_date, bcws, bcwp, acwp, "
            "cpi, spi, eac, bac, vac, classification, created_at "
            "FROM cpmp_evm_periods ORDER BY period_date DESC LIMIT 500"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        _rollback(c)
        return []


register_collection("cpmp.contracts", contracts_adapter)
register_collection("cpmp.deliverables", deliverables_adapter)
register_collection("cpmp.clins", clins_adapter)
register_collection("cpmp.cpars", cpars_adapter)
register_collection("cpmp.evm", evm_adapter)
