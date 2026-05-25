# CUI // SP-CTI
"""Cloud Application Migration (CAM) engine.

Provides data-loading functions for the CAM project hub and project detail view.
All service names, SOP categories, and AI service labels come from cam_constants.py
(which reads context/migration/service_mappings.yaml) — nothing hardcoded here.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging

logger = get_logger("icdev.cam_engine")


def _mc_conn():
    from tools.migration_canvas.db.init_db import get_connection
    return get_connection()


def _canvas_conn(canvas: str):
    """Return a connection to the given canvas DB."""
    if canvas == "ddc":
        from tools.data_canvas.db.init_db import get_connection
    elif canvas == "idc":
        from tools.infra_canvas.db.init_db import get_connection
    elif canvas == "ndc":
        from tools.network.db.init_db import get_connection
    else:
        return _mc_conn()
    return get_connection()


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    return dict(row) if hasattr(row, "keys") else {}


def _find_sop(conn, sop_source: str, *keywords: str) -> tuple[str, str] | tuple[None, None]:
    """Return (sop_id, sop_title) from the appropriate canvas SOP table.

    Tries 'approved' → 'review' → 'draft' statuses in order.
    Falls back to any status if none found under standard status names.
    """
    from tools.migration_canvas.cam_constants import CANVAS_SOP_TABLES, CANVAS_SOP_ID_COL

    table = CANVAS_SOP_TABLES.get(sop_source, "mc_sops")
    id_col = CANVAS_SOP_ID_COL.get(sop_source, "id")

    # Determine status column name (all canvas SOP tables use 'status' except mc_sops which uses 'approval_status')
    status_col = "approval_status" if sop_source == "mc" else "status"

    try:
        c = _canvas_conn(sop_source)
        try:
            for status in ("approved", "review", "draft"):
                for kw in keywords:
                    row = c.execute(
                        f"SELECT {id_col}, title FROM {table} "
                        f"WHERE {status_col}=? AND lower(title) LIKE ? LIMIT 1",
                        (status, f"%{kw.lower()}%"),
                    ).fetchone()
                    if row:
                        return row[0], row[1]
            # Fallback: any status
            for kw in keywords:
                row = c.execute(
                    f"SELECT {id_col}, title FROM {table} WHERE lower(title) LIKE ? LIMIT 1",
                    (f"%{kw.lower()}%",),
                ).fetchone()
                if row:
                    return row[0], row[1]
        finally:
            c.close()
    except Exception as exc:
        logger.debug("_find_sop(%s, %s): %s", sop_source, keywords, exc)
    return None, None


def _normalize_step(step: dict) -> dict:
    """Normalize any SOP step schema variant to canonical {number, text, verify, time_est, rollback}."""
    from tools.network.blueprint_helpers import _normalize_sop_step
    return _normalize_sop_step(step)


def get_projects(conn=None) -> list[dict]:
    """Return all CAM projects with aggregate counts."""
    owned = conn is None
    if owned:
        conn = _mc_conn()
    try:
        rows = conn.execute(
            "SELECT p.*, "
            "  (SELECT COUNT(*) FROM mc_project_phases ph WHERE ph.project_id=p.id) AS phase_count, "
            "  (SELECT COUNT(*) FROM mc_ai_opportunities ai WHERE ai.project_id=p.id) AS ai_opp_count, "
            "  (SELECT COUNT(*) FROM mc_project_phases ph WHERE ph.project_id=p.id AND ph.status='completed') AS phases_done "
            "FROM mc_projects p ORDER BY p.updated_at DESC"
        ).fetchall()
        projects = [_row_to_dict(r) for r in rows]
        for p in projects:
            # Parse source/target stack JSON
            for field in ("source_stack_json", "target_stack_json"):
                raw = p.get(field) or "[]"
                try:
                    p[field] = json.loads(raw)
                except Exception:
                    p[field] = []
        return projects
    finally:
        if owned:
            conn.close()


def get_project_detail(conn=None, project_id: str = "") -> dict | None:
    """Load full project detail: project + phases + SOP links + app components + data migrations + AI opps."""
    owned = conn is None
    if owned:
        conn = _mc_conn()
    try:
        row = conn.execute("SELECT * FROM mc_projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            return None
        project = _row_to_dict(row)
        for field in ("source_stack_json", "target_stack_json"):
            raw = project.get(field) or "[]"
            try:
                project[field] = json.loads(raw)
            except Exception:
                project[field] = []

        # Load phases with SOP links
        phase_rows = conn.execute(
            "SELECT * FROM mc_project_phases WHERE project_id=? ORDER BY phase_num",
            (project_id,)
        ).fetchall()
        phases = []
        for ph_row in phase_rows:
            ph = _row_to_dict(ph_row)
            # Load SOP links for this phase
            sop_rows = conn.execute(
                "SELECT * FROM mc_project_phase_sops WHERE phase_id=? ORDER BY display_order",
                (ph["id"],)
            ).fetchall()
            linked_sops = []
            for sop_row in sop_rows:
                sop_link = _row_to_dict(sop_row)
                # Fetch SOP steps from the appropriate canvas DB
                steps = _load_sop_steps(sop_link.get("sop_source", "mc"), sop_link.get("sop_id", ""))
                sop_link["steps"] = steps
                linked_sops.append(sop_link)
            ph["linked_sops"] = linked_sops
            phases.append(ph)
        project["phases"] = phases

        # Load app components from mc_app_inventory (scoped to project's mc_session_id)
        session_id = project.get("mc_session_id")
        components = []
        if session_id:
            comp_rows = conn.execute(
                "SELECT ai.*, dm.source_type AS dm_source, dm.target_type AS dm_target, "
                "  dm.migration_method AS dm_method, dm.estimated_size_gb AS dm_size_gb, "
                "  dm.status AS dm_status "
                "FROM mc_app_inventory ai "
                "LEFT JOIN mc_data_migration dm ON dm.app_id = ai.id "
                "WHERE ai.session_id=? ORDER BY ai.name",
                (session_id,)
            ).fetchall()
            components = [_row_to_dict(r) for r in comp_rows]
            for c in components:
                for field in ("dependencies_json",):
                    raw = c.get(field) or "[]"
                    try:
                        c[field] = json.loads(raw)
                    except Exception:
                        c[field] = []
        project["components"] = components

        # Load wave plan
        wave_rows = conn.execute(
            "SELECT * FROM mc_wave_plans WHERE design_id=? ORDER BY wave_number",
            (project_id,)
        ).fetchall()
        project["waves"] = [_row_to_dict(r) for r in wave_rows]

        # Load AI opportunities
        ai_rows = conn.execute(
            "SELECT * FROM mc_ai_opportunities WHERE project_id=? ORDER BY effort_days",
            (project_id,)
        ).fetchall()
        project["ai_opportunities"] = [_row_to_dict(r) for r in ai_rows]

        # Resolve cross-canvas URLs
        project["canvas_links"] = get_canvas_links(project)

        return project
    finally:
        if owned:
            conn.close()


def _load_sop_steps(sop_source: str, sop_id: str) -> list[dict]:
    """Load and normalize SOP steps from the appropriate canvas DB."""
    from tools.migration_canvas.cam_constants import CANVAS_SOP_TABLES, CANVAS_SOP_ID_COL, CANVAS_SOP_STEPS_COL
    if not sop_id:
        return []
    table = CANVAS_SOP_TABLES.get(sop_source, "mc_sops")
    id_col = CANVAS_SOP_ID_COL.get(sop_source, "id")
    steps_col = CANVAS_SOP_STEPS_COL.get(sop_source, "steps")
    try:
        c = _canvas_conn(sop_source)
        try:
            row = c.execute(f"SELECT {steps_col} FROM {table} WHERE {id_col}=?", (sop_id,)).fetchone()
            if not row:
                return []
            raw = row[0] if row else None
            if not raw:
                return []
            steps_data = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(steps_data, list):
                return []
            return [_normalize_step(s) for s in steps_data if isinstance(s, dict)]
        finally:
            c.close()
    except Exception as exc:
        logger.debug("_load_sop_steps(%s, %s): %s", sop_source, sop_id, exc)
        return []


def get_canvas_links(project: dict) -> dict:
    """Return URLs for cross-canvas navigation buttons."""
    return {
        "ddc": f"/data/canvas/{project.get('ddc_design_id')}" if project.get("ddc_design_id") else None,
        "idc": f"/infra/canvas/{project.get('idc_design_id')}" if project.get("idc_design_id") else None,
        "ndc": f"/network/canvas/{project.get('ndc_topology_id')}" if project.get("ndc_topology_id") else None,
    }
