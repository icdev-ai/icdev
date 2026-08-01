# CUI // SP-CTI
"""IQE Mission Canvas collection adapters.

Importing this module registers four collections on the module-level Executor,
each backed by a real Mission Canvas table (cnr-mc-02). The collection names are
the canonical ``mission.*`` namespace used by the registry, the blueprint IQE
route, and the seed queries:

  mission.sessions — Mission designs (mission_designs); filter by design_type,
                     classification.
  mission.twins    — Digital twin snapshots (mission_twin_snapshots); filter by
                     status, classification.
  mission.evidence — Traceable evidence records (mission_evidence); filter by
                     evidence_type, classification.
  mission.alerts   — Security-posture rows (mission_security_posture); filter by
                     fedramp_status, il_level, zta_score.

Previously this module registered metadata-only collections
(mission_canvas.missions/objectives/events) with NO query functions and imported
a nonexistent ``tools.iqe.registry`` module, so IQE queries never resolved.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _mc_conn(conn: Any) -> tuple[Any, bool]:
    """Return (connection, owned). When the executor passes conn=None, open a
    Mission Canvas connection ourselves and flag it for closing."""
    if conn is not None:
        return conn, False
    from tools.mission_canvas.db.init_db import get_connection  # noqa: PLC0415
    return get_connection(), True


def _fetch(conn: Any, sql: str) -> list[dict]:
    c, owned = _mc_conn(conn)
    try:
        cur = c.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            try:
                c.close()
            except Exception:
                pass


def sessions_adapter(conn: Any) -> list[dict]:
    """Return rows from mission_designs (a mission = a design/session)."""
    return _fetch(
        conn,
        "SELECT id, name, description, design_type, classification, "
        "created_at, updated_at FROM mission_designs",
    )


def twins_adapter(conn: Any) -> list[dict]:
    """Return digital-twin snapshots from mission_twin_snapshots."""
    return _fetch(
        conn,
        "SELECT id, design_id, snapshot_name, status, classification, "
        "created_at FROM mission_twin_snapshots",
    )


def evidence_adapter(conn: Any) -> list[dict]:
    """Return traceable evidence records from mission_evidence."""
    return _fetch(
        conn,
        "SELECT id, design_id, evidence_type, title, source, classification, "
        "created_at FROM mission_evidence",
    )


def alerts_adapter(conn: Any) -> list[dict]:
    """Return security-posture rows (surfaced as alerts) from mission_security_posture."""
    return _fetch(
        conn,
        "SELECT id, design_id, zta_score, fedramp_status, il_level, "
        "assessed_at, created_at FROM mission_security_posture",
    )


register_collection("mission.sessions", sessions_adapter)
register_collection("mission.twins", twins_adapter)
register_collection("mission.evidence", evidence_adapter)
register_collection("mission.alerts", alerts_adapter)
