# CUI // SP-CTI
"""IQE Cloud Application Migration (CAM) collection adapters.

Registers 4 collections on the module-level Executor:
  cam.projects       — mc_projects with phase/AI opp counts
  cam.phases         — mc_project_phases joined to mc_projects
  cam.ai_opportunities — mc_ai_opportunities joined to mc_app_inventory
  cam.app_components — mc_app_inventory scoped to CAM sessions
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _cam_conn(conn: Any) -> tuple[Any, bool]:
    if conn is not None:
        return conn, False
    from tools.migration_canvas.db.init_db import get_connection  # noqa: PLC0415
    return get_connection(), True


def projects_adapter(conn: Any) -> list[dict]:
    """Return mc_projects rows with phase and AI opp counts."""
    c, owned = _cam_conn(conn)
    try:
        cur = c.execute(
            "SELECT p.id, p.name, p.customer, p.owner, p.status, "
            "p.classification, p.impact_level, p.ddc_design_id, p.idc_design_id, "
            "p.ndc_topology_id, p.created_at, p.updated_at, "
            "(SELECT COUNT(*) FROM mc_project_phases ph WHERE ph.project_id=p.id) AS phase_count, "
            "(SELECT COUNT(*) FROM mc_ai_opportunities ai WHERE ai.project_id=p.id) AS ai_opp_count, "
            "(SELECT COUNT(*) FROM mc_project_phases ph WHERE ph.project_id=p.id AND ph.status='completed') AS phases_done "
            "FROM mc_projects p ORDER BY p.updated_at DESC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


def phases_adapter(conn: Any) -> list[dict]:
    """Return mc_project_phases joined to mc_projects."""
    c, owned = _cam_conn(conn)
    try:
        cur = c.execute(
            "SELECT ph.id, ph.project_id, p.name AS project_name, "
            "ph.phase_num, ph.title, ph.description, ph.wave_num, "
            "ph.duration_days, ph.maintenance_window, ph.rollback_criteria, "
            "ph.status, ph.classification, ph.created_at "
            "FROM mc_project_phases ph "
            "JOIN mc_projects p ON p.id = ph.project_id "
            "ORDER BY p.name, ph.phase_num"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


def ai_opportunities_adapter(conn: Any) -> list[dict]:
    """Return mc_ai_opportunities joined to mc_app_inventory."""
    c, owned = _cam_conn(conn)
    try:
        cur = c.execute(
            "SELECT ao.id, ao.project_id, p.name AS project_name, "
            "ao.component_name, ao.opportunity_title, ao.aws_ai_service, "
            "ao.benefit_category, ao.effort_days, ao.status, ao.notes "
            "FROM mc_ai_opportunities ao "
            "JOIN mc_projects p ON p.id = ao.project_id "
            "ORDER BY ao.effort_days"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


def app_components_adapter(conn: Any) -> list[dict]:
    """Return mc_app_inventory scoped to CAM sessions (linked to mc_projects)."""
    c, owned = _cam_conn(conn)
    try:
        cur = c.execute(
            "SELECT ai.id, ai.session_id, ai.name, ai.version, ai.app_type, "
            "ai.language, ai.framework, ai.migration_strategy, ai.migration_status, "
            "ai.criticality, ai.environment, ai.classification "
            "FROM mc_app_inventory ai "
            "WHERE ai.session_id IN (SELECT mc_session_id FROM mc_projects WHERE mc_session_id IS NOT NULL) "
            "ORDER BY ai.name"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


register_collection("cam.projects",        projects_adapter)
register_collection("cam.phases",          phases_adapter)
register_collection("cam.ai_opportunities", ai_opportunities_adapter)
register_collection("cam.app_components",  app_components_adapter)
