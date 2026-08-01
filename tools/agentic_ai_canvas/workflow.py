# CUI // SP-CTI
"""AADC Workflow Bridge — HITL + loop_engine integration.

On design save:
  - If safety/rights-impacting: create HITL workflow instance
  - On "Launch Design": create loop_engine plan + seed Kanban tasks
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import uuid
from datetime import datetime, timezone

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# HITL workflow templates — seeded into icdev.db wf_templates
# ---------------------------------------------------------------------------

AADC_HITL_TEMPLATES = [
    {
        "name": "AADC Safety Review",
        "canvas_type": "aadc",
        "is_default": False,
        "is_system": True,
        "stages_json": json.dumps([
            {"stage": 1, "name": "AI Ethics Review",    "role": "ai_ethics_officer", "required": True},
            {"stage": 2, "name": "CAIO Sign-off",       "role": "caio",              "required": True},
            {"stage": 3, "name": "Legal Compliance",    "role": "legal_compliance",  "required": True},
        ]),
        "roles_json": json.dumps({"approvers": ["ai_ethics_officer", "caio", "legal_compliance"]}),
        "approval_policy": "all_required",
        "description": "3-stage review for safety-impacting or rights-impacting AI designs",
    },
    {
        "name": "AADC Standard Review",
        "canvas_type": "aadc",
        "is_default": True,
        "is_system": True,
        "stages_json": json.dumps([
            {"stage": 1, "name": "Design Owner Approval", "role": "design_owner", "required": True},
        ]),
        "roles_json": json.dumps({"approvers": ["design_owner"]}),
        "approval_policy": "all_required",
        "description": "Single-stage approval for standard (non-safety-impacting) AI designs",
    },
]


def seed_hitl_templates() -> None:
    """Seed AADC-specific HITL templates into icdev.db wf_templates if not already present."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            for tmpl in AADC_HITL_TEMPLATES:
                existing = conn.execute(
                    "SELECT id FROM wf_templates WHERE name=%s AND canvas_type=%s",
                    (tmpl["name"], tmpl["canvas_type"]),
                ).fetchone()
                if not existing:
                    tid = f"wft-aadc-{uuid.uuid4().hex[:8]}"
                    conn.execute(
                        """INSERT INTO wf_templates
                           (id, name, canvas_type, is_default, is_system,
                            stages_json, roles_json, approval_policy,
                            created_at, updated_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (tid, tmpl["name"], tmpl["canvas_type"],
                         1 if tmpl["is_default"] else 0,
                         1 if tmpl["is_system"] else 0,
                         tmpl["stages_json"], tmpl["roles_json"],
                         tmpl["approval_policy"],
                         _now(), _now()),
                    )
            conn.commit()
            logger.info("aadc.workflow: HITL templates seeded")
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("aadc.workflow: could not seed HITL templates: %s", exc)


def maybe_create_hitl_instance(design_id: str, safety_impacting: bool,
                                rights_impacting: bool, canvas_project_id: str | None = None
                                ) -> str | None:
    """Create a HITL workflow instance for a design if required.

    Returns the wf_instance_id if created, else None.
    """
    if not safety_impacting and not rights_impacting:
        return None

    require_hitl = True
    try:
        import os
        if os.environ.get("AADC_HITL_REQUIRE_APPROVAL", "true").lower() in ("false", "0", "no"):
            require_hitl = False
    except Exception:
        pass

    if not require_hitl:
        return None

    try:
        from tools.workflow_hitl.engine import WorkflowEngine
        from tools.workflow_hitl import template_manager
        from tools.agentic_ai_canvas.db.init_db import get_connection as aadc_conn

        # Pick the right template
        tmpl_name = "AADC Safety Review" if (safety_impacting or rights_impacting) else "AADC Standard Review"
        tmpl = template_manager.get_by_name(tmpl_name) if hasattr(template_manager, "get_by_name") else None
        if not tmpl:
            tmpl = template_manager.get_default("aadc")

        engine = WorkflowEngine()
        instance_id = engine.create_instance(
            canvas_type="aadc",
            project_id=canvas_project_id,
            template_id=tmpl["id"] if tmpl else None,
        )

        # Record the link in aadc_workflow_links
        conn = aadc_conn()
        try:
            conn.execute(
                "INSERT INTO aadc_workflow_links (id, design_id, wf_instance_id, status, created_at) "
                "VALUES (%s,%s,%s,%s,%s)",
                (f"wfl-{uuid.uuid4().hex[:8]}", design_id, instance_id, "pending", _now()),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("aadc.workflow: HITL instance %s created for design %s", instance_id, design_id)
        return instance_id

    except Exception as exc:
        logger.warning("aadc.workflow: could not create HITL instance: %s", exc)
        return None


def get_workflow_status(design_id: str) -> dict | None:
    """Return current HITL workflow status for a design, or None if no workflow."""
    try:
        from tools.agentic_ai_canvas.db.init_db import get_connection as aadc_conn
        conn = aadc_conn()
        try:
            link = conn.execute(
                "SELECT * FROM aadc_workflow_links WHERE design_id=%s ORDER BY created_at DESC LIMIT 1",
                (design_id,),
            ).fetchone()
            if not link:
                return None
            link_d = dict(link)
        finally:
            conn.close()

        # Try to get live status from HITL engine
        try:
            from tools.db.storage import get_connection
            conn2 = get_connection()
            try:
                inst = conn2.execute(
                    "SELECT * FROM wf_instances WHERE id=%s",
                    (link_d["wf_instance_id"],),
                ).fetchone()
                if inst:
                    return {**link_d, "wf_status": dict(inst).get("status", "unknown"),
                            "current_stage": dict(inst).get("current_stage", 1)}
            finally:
                conn2.close()
        except Exception:
            pass

        return link_d
    except Exception as exc:
        logger.warning("aadc.workflow: get_workflow_status error: %s", exc)
        return None


def is_approved(design_id: str) -> bool:
    """Return True if the design has an approved HITL workflow (or no workflow required)."""
    status = get_workflow_status(design_id)
    if status is None:
        return True  # No workflow required
    return status.get("status") == "approved" or status.get("wf_status") == "approved"


# ---------------------------------------------------------------------------
# Loop engine integration
# ---------------------------------------------------------------------------

def _cluster_task_id(design_id: str, cluster_name: str) -> str:
    """Deterministic task ID: same design + cluster always yields the same ID.

    This makes launch_to_kanban idempotent — re-launching skips tasks that
    already exist (ON CONFLICT DO NOTHING actually fires on the stable ID).
    """
    import hashlib
    slug = cluster_name.lower().replace(" ", "_")
    h = hashlib.sha256(f"{design_id}:{slug}".encode()).hexdigest()[:8]
    return f"aadc-{h}"


def _loop_id_for_design(design_id: str) -> str:
    """Deterministic loop ID so the workflow_loops row is also idempotent."""
    import hashlib
    h = hashlib.sha256(design_id.encode()).hexdigest()[:8]
    return f"wl-aadc-{h}"


def launch_to_kanban(design_id: str, design_name: str, graph_json: str,
                     project_id: str | None = None) -> dict:
    """Create a workflow loop and seed Kanban tasks from the design graph.

    Idempotent: deterministic IDs mean re-launching the same design returns
    the same task IDs without creating duplicates.  Tasks already in
    in_progress/done are left untouched.

    Returns {"loop_id": ..., "task_ids": [...], "skipped": [...]} or {"error": ...}
    """
    try:
        import json as _json
        g = _json.loads(graph_json) if isinstance(graph_json, str) else graph_json
        nodes = g.get("nodes", [])

        # Group nodes into clusters by category for task generation
        from tools.agentic_ai_canvas.constants import (
            AGENT_NODES, MODEL_NODES, MEMORY_NODES, SAFETY_NODES, GOVERNANCE_NODES,
        )

        clusters = {
            "Model Layer": [n for n in nodes if n.get("type") in MODEL_NODES],
            "Memory Layer": [n for n in nodes if n.get("type") in MEMORY_NODES],
            "Agent Layer": [n for n in nodes if n.get("type") in AGENT_NODES],
            "Safety Layer": [n for n in nodes if n.get("type") in SAFETY_NODES],
            "Governance Layer": [n for n in nodes if n.get("type") in GOVERNANCE_NODES],
        }
        clusters = {k: v for k, v in clusters.items() if v}  # drop empty

        loop_id = _loop_id_for_design(design_id)

        # Upsert the workflow_loops row (idempotent via deterministic ID)
        try:
            from tools.db.storage import get_connection
            conn = get_connection()
            try:
                conn.execute(
                    """INSERT INTO workflow_loops
                       (id, project_id, phase_name, plan_summary, created_at)
                       VALUES (%s,%s,%s,%s,%s)
                       ON CONFLICT DO NOTHING""",
                    (loop_id, project_id or design_id, "plan",
                     f"Implement AADC design: {design_name}", _now()),
                )
                conn.commit()
            except Exception as le:
                logger.warning("aadc.workflow: loop_engine insert: %s", le)
            finally:
                conn.close()
        except Exception as _exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning("launch_to_kanban: best-effort INSERT into workflow_loops failed (non-blocking): %s", _exc)

        # Seed Kanban tasks — deterministic IDs prevent duplicates
        task_ids: list[str] = []
        skipped: list[str] = []
        try:
            from tools.db.storage import get_connection
            conn = get_connection()
            try:
                for cluster_name, cluster_nodes in clusters.items():
                    tid = _cluster_task_id(design_id, cluster_name)
                    # Check if this task already exists (any non-suggested status)
                    existing = conn.execute(
                        "SELECT status FROM kanban_tasks WHERE id=%s", (tid,)
                    ).fetchone()
                    if existing:
                        skipped.append(tid)
                        task_ids.append(tid)
                        continue
                    labels = ", ".join(n.get("label", n.get("type", "")) for n in cluster_nodes[:4])
                    conn.execute(
                        """INSERT INTO kanban_tasks
                           (id, title, description, status, task_type, priority, created_at, updated_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT DO NOTHING""",
                        (tid,
                         f"[AADC] Implement {cluster_name} — {design_name}",
                         f"Build and integrate the {cluster_name} components from the Agentic AI design "
                         f"'{design_name}'. Nodes: {labels}.",
                         "backlog", "build", "high",
                         _now(), _now()),
                    )
                    task_ids.append(tid)
                conn.commit()
            finally:
                conn.close()
        except Exception as ke:
            logger.warning("aadc.workflow: kanban seed: %s", ke)

        # Record loop link in AADC DB (idempotent: same loop_id reuses the row)
        try:
            from tools.agentic_ai_canvas.db.init_db import get_connection as aadc_conn
            conn = aadc_conn()
            try:
                ll_id = f"ll-{_loop_id_for_design(design_id)}"
                conn.execute(
                    """INSERT INTO aadc_loop_links (id, design_id, loop_id, phase, created_at)
                       VALUES (%s,%s,%s,%s,%s) """,
                    (ll_id, design_id, loop_id, "plan", _now()),
                )
                conn.commit()
            except Exception as _exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
                # already exists — idempotent
                logger.warning(
                    "launch_to_kanban: best-effort INSERT into aadc_loop_links failed (non-blocking): %s",
                    _exc,
                )
            finally:
                conn.close()
        except Exception as _exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning("launch_to_kanban: best-effort INSERT into aadc_loop_links failed (non-blocking): %s", _exc)

        return {
            "loop_id": loop_id,
            "task_ids": task_ids,
            "skipped": skipped,
            "clusters": list(clusters.keys()),
        }

    except Exception as exc:
        logger.error("aadc.workflow: launch_to_kanban error: %s", exc)
        return {"error": str(exc)}
