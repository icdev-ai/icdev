# CUI // SP-CTI
"""IQE RFI Response Workbench collection adapters.

Registers three collections:
  rfi_canvas.sessions  — workbench sessions (rfi_workbench_sessions)
  rfi_canvas.sections  — per-section drafts and HITL state (rfi_workbench_sections)
  rfi_canvas.exports   — completed exports (rfi_workbench_exports)
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _conn(conn: Any):
    if conn is None:
        from tools.db.storage import get_canvas_connection  # noqa: PLC0415
        conn = get_canvas_connection("ICDEV_DB_URL")
    return conn


def sessions_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT id, rfi_number, rfi_title, profile_name, status, "
            "total_sections, approved_sections, created_at, updated_at "
            "FROM rfi_workbench_sessions ORDER BY created_at DESC LIMIT 200"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def sections_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT sec.id, sec.session_id, s.rfi_number, sec.part, "
            "sec.item_number, sec.title, sec.status, sec.hitl_action, "
            "sec.writeguard_score, sec.generation_count, sec.updated_at "
            "FROM rfi_workbench_sections sec "
            "JOIN rfi_workbench_sessions s ON sec.session_id = s.id "
            "ORDER BY sec.updated_at DESC LIMIT 1000"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def exports_adapter(conn: Any) -> list[dict]:
    c = _conn(conn)
    try:
        cur = c.execute(
            "SELECT e.id, e.session_id, s.rfi_number, e.export_format, "
            "e.file_path, e.exported_at "
            "FROM rfi_workbench_exports e "
            "JOIN rfi_workbench_sessions s ON e.session_id = s.id "
            "ORDER BY e.exported_at DESC LIMIT 200"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def register_collections() -> None:
    register_collection("rfi_canvas.sessions", sessions_adapter)
    register_collection("rfi_canvas.sections", sections_adapter)
    register_collection("rfi_canvas.exports", exports_adapter)


def query(q: str, conn: Any = None) -> list[dict]:
    ql = q.lower()
    if "export" in ql or "download" in ql or "docx" in ql:
        return exports_adapter(conn)
    if "section" in ql or "draft" in ql or "hitl" in ql or "approve" in ql or "writeguard" in ql:
        return sections_adapter(conn)
    return sessions_adapter(conn)
