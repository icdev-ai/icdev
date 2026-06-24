# CUI // SP-CTI
"""IQE adapter for MCIP DAT — registers dat.events and dat.dti_history collections."""
from __future__ import annotations

from tools.iqe.executor import register_collection


def _dat_events(query_ast, conn=None):
    """Query mcip_dat_events via IQE."""
    from tools.db.storage import get_connection
    _conn = conn or get_connection()
    try:
        rows = _conn.execute(
            "SELECT id, source_type, sender, recipient, classification, "
            "tension_signal, ingested_at FROM mcip_dat_events ORDER BY ingested_at DESC LIMIT 200"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        if conn is None:
            _conn.close()


def _dat_dti_history(query_ast, conn=None):
    """Query mcip_dti_scores via IQE."""
    from tools.db.storage import get_connection
    _conn = conn or get_connection()
    try:
        rows = _conn.execute(
            "SELECT id, score, cable_sub, unsc_sub, backchannel_sub, "
            "event_count, computed_at FROM mcip_dti_scores ORDER BY computed_at DESC LIMIT 200"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        if conn is None:
            _conn.close()


register_collection("dat.events", _dat_events)
register_collection("dat.dti_history", _dat_dti_history)
