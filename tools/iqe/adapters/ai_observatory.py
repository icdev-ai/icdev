# CUI // SP-CTI
"""IQE AI Observatory collection adapters.

Importing this module registers two collections on the module-level Executor:
  observatory.decisions          — All canvas AI decisions (canvas_ai_decisions);
                                   filter by canvas_type, decision_type, confidence, trace_id.
  observatory.confabulation_flags — Decisions where decision_type='confabulation_flag';
                                   filter by canvas_type, confidence.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def decisions_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return all rows from canvas_ai_decisions, newest first."""
    try:
        from tools.db.storage import get_connection  # noqa: PLC0415
        with get_connection() as _c:
            cur = _c.execute(
                "SELECT * FROM canvas_ai_decisions ORDER BY created_at DESC"
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def confabulation_flags_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return canvas_ai_decisions rows where decision_type='confabulation_flag'."""
    try:
        from tools.db.storage import get_connection  # noqa: PLC0415
        with get_connection() as _c:
            cur = _c.execute(
                "SELECT * FROM canvas_ai_decisions WHERE decision_type = 'confabulation_flag' "
                "ORDER BY created_at DESC"
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


register_collection("observatory.decisions", decisions_adapter)
register_collection("observatory.confabulation_flags", confabulation_flags_adapter)
