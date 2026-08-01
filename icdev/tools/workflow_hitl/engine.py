# CUI // SP-CTI
"""WorkflowEngine — state machine for HITL approval instances."""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import uuid
from datetime import datetime, timezone

from tools.db.storage import column_exists, get_connection
from tools.workflow_hitl.constants import WfStatus

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowEngine:

    def create_instance(
        self,
        task_id: str | None = None,
        project_id: str | None = None,
        canvas_type: str | None = None,
        template_id: str | None = None,
    ) -> str:
        """Create a workflow instance and open the first approval gate.

        Resolution order for template:
          1. Explicit template_id arg
          2. Team assignment template for this task/project
          3. Canvas-default system template
          4. Global system default template
        """
        from tools.workflow_hitl import team_manager, template_manager

        # Resolve template
        tmpl = None
        if template_id:
            tmpl = template_manager.get(template_id)
        if not tmpl and task_id:
            resolved = team_manager.resolve_team(task_id)
            if resolved and resolved.get("template"):
                tmpl = resolved["template"]
        if not tmpl:
            tmpl = template_manager.get_default(canvas_type)
        if not tmpl:
            raise RuntimeError(
                f"No workflow template found for canvas_type={canvas_type!r}. "
                "Run template_manager.seed_system_templates() first."
            )

        instance_id = f"wfi-{uuid.uuid4().hex[:12]}"
        now = _now()
        stages = json.loads(tmpl["stages_json"])

        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO wf_instances
                   (id, template_id, task_id, project_id, canvas_type,
                    current_stage, kickback_count, status, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,0,%s,%s,%s)""",
                (instance_id, tmpl["id"], task_id, project_id, canvas_type,
                 stages[0]["name"] if stages else "build", WfStatus.ACTIVE, now, now),
            )
            # Update kanban_tasks.hitl_stage if task_id given
            if task_id:
                try:
                    conn.execute(
                        "UPDATE kanban_tasks SET hitl_stage=%s WHERE id=%s",
                        (stages[0]["name"] if stages else "build", task_id),
                    )
                except Exception:
                    pass
            try:
                conn.commit()
            except Exception:
                pass
        finally:
            conn.close()

        # Open first approval gate
        self._open_approval(instance_id, stages[0], tmpl)
        logger.info("Created wf_instance %s for task=%s template=%s", instance_id, task_id, tmpl["id"])
        return instance_id

    def get_status(self, instance_id: str) -> dict | None:
        conn = get_connection()
        try:
            inst = conn.execute("SELECT * FROM wf_instances WHERE id=%s", (instance_id,)).fetchone()
            if not inst:
                return None
            result = dict(inst)
            result["approvals"] = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM wf_approvals WHERE instance_id=%s ORDER BY created_at",
                    (instance_id,),
                ).fetchall()
            ]
            result["feedback"] = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM wf_feedback WHERE instance_id=%s ORDER BY submitted_at",
                    (instance_id,),
                ).fetchall()
            ]
            result["external_steps"] = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM wf_external_steps WHERE instance_id=%s ORDER BY created_at",
                    (instance_id,),
                ).fetchall()
            ]
            result["citations"] = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM wf_citations WHERE instance_id=%s ORDER BY stage, created_at",
                    (instance_id,),
                ).fetchall()
            ]
            return result
        finally:
            conn.close()

    def advance_stage(self, instance_id: str) -> dict:
        """Advance the instance to the next stage in stages_json.

        Returns {"advanced": True, "new_stage": "..."} or
                {"blocked": True, "missing_docs": [...]} or
                {"completed": True} when all stages done.
        """
        from tools.workflow_hitl import template_manager, document_manager

        conn = get_connection()
        try:
            inst = conn.execute("SELECT * FROM wf_instances WHERE id=%s", (instance_id,)).fetchone()
            if not inst:
                raise ValueError(f"Instance {instance_id!r} not found")
            inst = dict(inst)

            if inst["status"] in (WfStatus.APPROVED, WfStatus.REJECTED,
                                   WfStatus.ESCALATED, WfStatus.CANCELLED):
                return {"error": f"Instance is already in terminal status: {inst['status']}"}

            tmpl = template_manager.get(inst["template_id"])
            stages = json.loads(tmpl["stages_json"])
            current_idx = next(
                (i for i, s in enumerate(stages) if s["name"] == inst["current_stage"]), -1
            )

            # Check document gate for current stage
            current_stage_config = stages[current_idx] if current_idx >= 0 else {}
            pending_approval = conn.execute(
                "SELECT * FROM wf_approvals WHERE instance_id=%s AND stage=%s AND status='pending' LIMIT 1",
                (instance_id, inst["current_stage"]),
            ).fetchone()

            if pending_approval and current_stage_config.get("required_docs"):
                missing = document_manager.get_missing_docs(
                    pending_approval["id"], current_stage_config
                )
                if missing:
                    return {"blocked": True, "missing_docs": missing}
        finally:
            conn.close()

        next_idx = current_idx + 1
        if next_idx >= len(stages):
            # All stages complete → approved
            self._set_status(instance_id, WfStatus.APPROVED)
            if inst.get("task_id"):
                self._clear_hitl_stage(inst["task_id"])
            logger.info("Instance %s fully approved", instance_id)
            return {"completed": True}

        next_stage = stages[next_idx]
        now = _now()
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE wf_instances SET current_stage=%s, updated_at=%s WHERE id=%s",
                (next_stage["name"], now, instance_id),
            )
            if inst.get("task_id"):
                try:
                    conn.execute(
                        "UPDATE kanban_tasks SET hitl_stage=%s WHERE id=%s",
                        (next_stage["name"], inst["task_id"]),
                    )
                except Exception:
                    pass
            try:
                conn.commit()
            except Exception:
                pass
        finally:
            conn.close()

        self._open_approval(instance_id, next_stage, tmpl)
        logger.info("Instance %s advanced to stage %s", instance_id, next_stage["name"])
        return {"advanced": True, "new_stage": next_stage["name"]}

    def kickback(self, instance_id: str, kickback_reason: str, submitted_by: str) -> dict:
        """Kickback instance to build stage, increment kickback_count."""
        conn = get_connection()
        try:
            inst = conn.execute("SELECT * FROM wf_instances WHERE id=%s", (instance_id,)).fetchone()
            if not inst:
                raise ValueError(f"Instance {instance_id!r} not found")
            inst = dict(inst)

            new_count = inst["kickback_count"] + 1
            from tools.workflow_hitl import template_manager
            tmpl = template_manager.get(inst["template_id"])
            limit = tmpl["kickback_limit"] if tmpl else 3

            if new_count >= limit:
                conn.close()
                return self.escalate(instance_id, reason=f"Kickback limit ({limit}) reached.")

            now = _now()
            conn.execute(
                "UPDATE wf_instances SET current_stage='build', kickback_count=%s, updated_at=%s WHERE id=%s",
                (new_count, now, instance_id),
            )
            # Mark pending approval as kicked back
            conn.execute(
                "UPDATE wf_approvals SET status='kickback', updated_at=%s WHERE instance_id=%s AND status='pending'",
                (now, instance_id),
            )
            if inst.get("task_id"):
                try:
                    conn.execute(
                        "UPDATE kanban_tasks SET hitl_stage='build' WHERE id=%s",
                        (inst["task_id"],),
                    )
                except Exception:
                    pass
            try:
                conn.commit()
            except Exception:
                pass
        finally:
            conn.close()

        logger.info("Instance %s kicked back (count=%d) by %s", instance_id, new_count, submitted_by)

        # Notify
        try:
            from tools.workflow_hitl.notifier import send_kickback_notice
            send_kickback_notice(instance_id, kickback_reason, submitted_by)
        except Exception as exc:
            logger.warning("Kickback notification failed: %s", exc)

        return {"kicked_back": True, "kickback_count": new_count, "stage": "build"}

    def escalate(self, instance_id: str, reason: str = "") -> dict:
        self._set_status(instance_id, WfStatus.ESCALATED)
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE wf_approvals SET status='escalated', updated_at=%s WHERE instance_id=%s AND status='pending'",
                (_now(), instance_id),
            )
            try:
                conn.commit()
            except Exception:
                pass
        finally:
            conn.close()
        logger.warning("Instance %s escalated: %s", instance_id, reason)
        return {"escalated": True, "reason": reason}

    def cancel(self, instance_id: str) -> dict:
        self._set_status(instance_id, WfStatus.CANCELLED)
        return {"cancelled": True}

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _open_approval(self, instance_id: str, stage_config: dict, tmpl: dict):
        """Create a wf_approvals gate for the given stage.

        For external steps, also triggers external_steps.create + send.
        """
        step_type = stage_config.get("step_type", "manual")

        if step_type == "automated":
            # AI-automated: look up applicable standards for context
            conn = get_connection()
            try:
                inst = conn.execute("SELECT canvas_type FROM wf_instances WHERE id=%s", (instance_id,)).fetchone()
                canvas_type = inst[0] if inst else None
            finally:
                conn.close()
            try:
                from tools.workflow_hitl import document_manager
                standards = document_manager.get_applicable_standards(canvas_type, stage_config["name"])
                if standards:
                    logger.info(
                        "Stage %s has %d AI standards: %s",
                        stage_config["name"], len(standards),
                        [s["name"] for s in standards],
                    )
            except Exception:
                pass
            # CoT trace for auto-advance decision
            reasoning_trace = (
                f"Auto-advancing automated stage '{stage_config['name']}' "
                f"for instance {instance_id} (canvas={canvas_type}). "
                f"No human gate required per template."
            )
            try:
                from tools.canvas.ai_trace_mixin import record_canvas_decision
                trace_id = record_canvas_decision(
                    canvas_type=canvas_type or "workflow",
                    record_id=instance_id,
                    decision_type="chain_of_thought",
                    decision=f"Auto-advance {stage_config['name']}",
                    rationale=reasoning_trace,
                    actor="workflow_hitl",
                )
            except Exception:
                trace_id = ""
            # Ensure wf_approvals.cot_trace_id exists and store the trace
            approval_id = f"wfa-{uuid.uuid4().hex[:12]}"
            conn = get_connection()
            try:
                # Backend-aware column probe (pgrt-sweep-06) — no PRAGMA/translation reliance.
                if not column_exists(conn, "wf_approvals", "cot_trace_id"):
                    conn.execute("ALTER TABLE wf_approvals ADD COLUMN cot_trace_id TEXT")
                conn.execute(
                    "INSERT INTO wf_approvals "
                    "(id, instance_id, stage, team_id, assigned_to, status, cot_trace_id, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (approval_id, instance_id, stage_config["name"], None, None, "approved", trace_id, _now(), _now()),
                )
                conn.commit()
            except Exception as _exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
                logger.warning("_open_approval: best-effort INSERT into wf_approvals failed (non-blocking): %s", _exc)
            finally:
                conn.close()
            # Auto-advance past automated stages immediately
            self.advance_stage(instance_id)
            return

        # Resolve team for assignment
        approval_id = f"wfa-{uuid.uuid4().hex[:12]}"
        now = _now()

        conn = get_connection()
        try:
            inst = conn.execute("SELECT task_id FROM wf_instances WHERE id=%s", (instance_id,)).fetchone()
            task_id = inst[0] if inst else None
        finally:
            conn.close()

        team_id = None
        try:
            from tools.workflow_hitl import team_manager
            resolved = team_manager.resolve_team(task_id) if task_id else None
            if resolved and resolved.get("team"):
                team_id = resolved["team"]["id"]
        except Exception:
            pass

        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO wf_approvals
                   (id, instance_id, stage, team_id, assigned_to, status, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,NULL,'pending',%s,%s)""",
                (approval_id, instance_id, stage_config["name"], team_id, now, now),
            )
            try:
                conn.commit()
            except Exception:
                pass
        finally:
            conn.close()

        # Trigger external step if needed
        if step_type in ("external_email", "external_ticket", "external_wiki"):
            try:
                from tools.workflow_hitl import external_steps
                step_id = external_steps.create(instance_id, stage_config["name"], step_type, stage_config)
                external_steps.send(step_id)
            except Exception as exc:
                logger.warning("External step failed for %s: %s", stage_config["name"], exc)
        elif team_id:
            try:
                from tools.workflow_hitl.notifier import send_review_request
                from tools.workflow_hitl import team_manager
                team = team_manager.get_team(team_id)
                if team:
                    send_review_request(approval_id, team)
            except Exception as exc:
                logger.warning("Review request notification failed: %s", exc)

    def _set_status(self, instance_id: str, status: str):
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE wf_instances SET status=%s, updated_at=%s WHERE id=%s",
                (status, _now(), instance_id),
            )
            try:
                conn.commit()
            except Exception:
                pass
        finally:
            conn.close()

    def _clear_hitl_stage(self, task_id: str):
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE kanban_tasks SET hitl_stage=NULL WHERE id=%s", (task_id,)
            )
            try:
                conn.commit()
            except Exception:
                pass
        finally:
            conn.close()
