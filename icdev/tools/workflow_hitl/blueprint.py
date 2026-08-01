# CUI // SP-CTI
"""Flask blueprint for ICDEV HITL Workflow Management — mounts at /api/v1/wf/.

Gated by the ICDEV_HITL_ENABLED environment variable.  Every route returns 503
when the feature flag is off.  Auth is a placeholder (comment-tagged) — full
session validation is handled by the ICDEV auth middleware registered on the
parent Flask app.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import dataclasses
import os

from flask import Blueprint, request, jsonify, g

from tools.workflow_hitl import (
    template_manager,
    team_manager,
    feedback as feedback_mod,
    external_steps,
    document_manager,
    citation_manager,
    document_ingestion,
)
from tools.workflow_hitl.engine import WorkflowEngine
from tools.workflow_hitl import report_generator
from tools.db.storage import get_connection
from tools.dashboard.auth import require_role

logger = get_logger(__name__)

# ── Segregation-of-duties gate for HITL governance mutations (nav-sec-04) ──────
# Every instance-level *state-changing* route (approve / kickback / escalate /
# cancel) is restricted to reviewer/approver-class roles. `developer` is
# deliberately excluded: before this fix any authenticated user — including a
# developer whose own work is under review — could approve or kick back a
# governance gate and be recorded as the approver, defeating segregation of
# duties for the Human-In-The-Loop layer. All roles below already exist in
# tools.dashboard.auth.VALID_DASHBOARD_ROLES. Read-only routes stay ungated so
# they still render for any logged-in user.
_WF_APPROVAL_ROLES = ("admin", "pm", "isso", "co", "cor", "reviewer")

# ── Feature-flag guard ────────────────────────────────────────────────────────

def _hitl_enabled() -> bool:
    # Force re-read from .env so toggling the flag takes effect without a full restart.
    try:
        from dotenv import load_dotenv as _ld
        from pathlib import Path as _P
        _ld(_P(__file__).resolve().parents[2] / ".env", override=True)
    except Exception:
        pass
    return os.getenv("ICDEV_HITL_ENABLED", "false").lower() in ("true", "1", "yes")


def _disabled_response():
    return jsonify({"error": "HITL workflow is disabled. Set ICDEV_HITL_ENABLED=true to enable."}), 503


# ── Auth helper ───────────────────────────────────────────────────────────────

def _get_current_user() -> str | None:
    """Return the authenticated user identifier from the resolved auth context.

    In production the ICDEV dashboard auth middleware validates the session /
    API key and populates ``g.current_user`` (a dict with ``id`` + ``role``);
    this is the authoritative identity and is what gets recorded as the
    approver on governance-gate decisions. It is NEVER read from the request
    body, which a caller could spoof. In dev mode (ICDEV_DEV_MODE=true) we fall
    back to the X-Dev-User header so engineers can test without a session.
    """
    user = getattr(g, "current_user", None)
    if user:
        if isinstance(user, dict):
            return user.get("id")
        try:
            return user["id"]
        except Exception:
            return getattr(user, "id", None)
    dev_mode = os.getenv("ICDEV_DEV_MODE", "false").lower() in ("true", "1")
    if dev_mode:
        return request.headers.get("X-Dev-User", "dev-user")
    session_token = request.cookies.get("session")
    if not session_token:
        return None
    # TODO: validate session_token against auth service / DB and return user_id
    return session_token  # placeholder — swap with real user resolution


# ── Blueprint factory ─────────────────────────────────────────────────────────

def create_wf_blueprint() -> Blueprint:
    bp = Blueprint("wf", __name__, url_prefix="/api/v1/wf")

    # ── Templates ─────────────────────────────────────────────────────────────

    @bp.route("/templates", methods=["GET"])
    def list_templates_route():
        if not _hitl_enabled():
            return _disabled_response()
        try:
            canvas_type = request.args.get("canvas_type")
            templates = template_manager.list_templates(canvas_type=canvas_type)
            return jsonify({"templates": templates, "count": len(templates)})
        except Exception as exc:
            logger.exception("list_templates failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/templates", methods=["POST"])
    def create_template_route():
        if not _hitl_enabled():
            return _disabled_response()
        try:
            current_user = _get_current_user()
            data = request.get_json(force=True) or {}
            name = data.get("name")
            if not name:
                return jsonify({"error": "name is required"}), 400
            template_id = template_manager.create(
                name=name,
                canvas_type=data.get("canvas_type"),
                stages_json=data.get("stages_json"),
                roles_json=data.get("roles_json"),
                approval_policy=data.get("approval_policy", "any_one"),
                kickback_limit=int(data.get("kickback_limit", 3)),
                created_by=current_user,
            )
            return jsonify({"id": template_id}), 201
        except Exception as exc:
            logger.exception("create_template failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/templates/<template_id>", methods=["GET"])
    def get_template_route(template_id: str):
        if not _hitl_enabled():
            return _disabled_response()
        try:
            tmpl = template_manager.get(template_id)
            if not tmpl:
                return jsonify({"error": f"Template {template_id!r} not found"}), 404
            return jsonify(tmpl)
        except Exception as exc:
            logger.exception("get_template failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/templates/<template_id>", methods=["PUT"])
    def update_template_route(template_id: str):
        if not _hitl_enabled():
            return _disabled_response()
        try:
            tmpl = template_manager.get(template_id)
            if not tmpl:
                return jsonify({"error": f"Template {template_id!r} not found"}), 404
            if tmpl.get("is_system"):
                return jsonify({"error": "System templates cannot be modified."}), 403
            data = request.get_json(force=True) or {}
            updated = template_manager.update(template_id, **data)
            if not updated:
                return jsonify({"error": "No valid fields to update or template not found"}), 400
            return jsonify({"updated": True, "id": template_id})
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except Exception as exc:
            logger.exception("update_template failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/templates/<template_id>/fork", methods=["POST"])
    def fork_template_route(template_id: str):
        if not _hitl_enabled():
            return _disabled_response()
        try:
            current_user = _get_current_user()
            data = request.get_json(force=True) or {}
            new_name = data.get("name")
            if not new_name:
                return jsonify({"error": "name is required for the forked template"}), 400
            new_id = template_manager.fork(template_id, new_name, created_by=current_user)
            return jsonify({"id": new_id, "forked_from": template_id}), 201
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
        except Exception as exc:
            logger.exception("fork_template failed")
            return jsonify({"error": str(exc)}), 500

    # ── Teams ─────────────────────────────────────────────────────────────────

    @bp.route("/teams", methods=["GET"])
    def list_teams_route():
        if not _hitl_enabled():
            return _disabled_response()
        try:
            canvas_type = request.args.get("canvas_type")
            teams = team_manager.list_teams(canvas_type=canvas_type)
            return jsonify({"teams": teams, "count": len(teams)})
        except Exception as exc:
            logger.exception("list_teams failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/teams", methods=["POST"])
    def create_team_route():
        if not _hitl_enabled():
            return _disabled_response()
        try:
            current_user = _get_current_user()
            data = request.get_json(force=True) or {}
            name = data.get("name")
            if not name:
                return jsonify({"error": "name is required"}), 400
            team_id = team_manager.create_team(
                name=name,
                description=data.get("description"),
                canvas_type=data.get("canvas_type"),
                created_by=current_user,
            )
            return jsonify({"id": team_id}), 201
        except Exception as exc:
            logger.exception("create_team failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/teams/<team_id>", methods=["GET"])
    def get_team_route(team_id: str):
        if not _hitl_enabled():
            return _disabled_response()
        try:
            team = team_manager.get_team(team_id)
            if not team:
                return jsonify({"error": f"Team {team_id!r} not found"}), 404
            return jsonify(team)
        except Exception as exc:
            logger.exception("get_team failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/teams/<team_id>/members", methods=["POST"])
    def add_member_route(team_id: str):
        if not _hitl_enabled():
            return _disabled_response()
        try:
            team = team_manager.get_team(team_id)
            if not team:
                return jsonify({"error": f"Team {team_id!r} not found"}), 404
            data = request.get_json(force=True) or {}
            user_id = data.get("user_id")
            role_label = data.get("role_label")
            if not user_id or not role_label:
                return jsonify({"error": "user_id and role_label are required"}), 400
            member_id = team_manager.add_member(team_id, user_id, role_label)
            return jsonify({"id": member_id, "team_id": team_id, "user_id": user_id, "role_label": role_label}), 201
        except Exception as exc:
            logger.exception("add_member failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/teams/<team_id>/members/<uid>", methods=["DELETE"])
    def remove_member_route(team_id: str, uid: str):
        if not _hitl_enabled():
            return _disabled_response()
        try:
            team = team_manager.get_team(team_id)
            if not team:
                return jsonify({"error": f"Team {team_id!r} not found"}), 404
            team_manager.remove_member(team_id, uid)
            return jsonify({"removed": True, "team_id": team_id, "user_id": uid})
        except Exception as exc:
            logger.exception("remove_member failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/teams/<team_id>/assignments", methods=["GET"])
    def list_assignments_route(team_id: str):
        if not _hitl_enabled():
            return _disabled_response()
        try:
            team = team_manager.get_team(team_id)
            if not team:
                return jsonify({"error": f"Team {team_id!r} not found"}), 404
            assignments = team_manager.list_assignments(team_id)
            return jsonify({"assignments": assignments, "count": len(assignments)})
        except Exception as exc:
            logger.exception("list_assignments failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/teams/<team_id>/assignments", methods=["POST"])
    def assign_team_route(team_id: str):
        if not _hitl_enabled():
            return _disabled_response()
        try:
            team = team_manager.get_team(team_id)
            if not team:
                return jsonify({"error": f"Team {team_id!r} not found"}), 404
            data = request.get_json(force=True) or {}
            scope_type = data.get("scope_type")
            scope_id = data.get("scope_id")
            if not scope_type or not scope_id:
                return jsonify({"error": "scope_type and scope_id are required"}), 400
            assign_id = team_manager.assign_team(
                team_id=team_id,
                scope_type=scope_type,
                scope_id=scope_id,
                template_id=data.get("template_id"),
            )
            return jsonify({"id": assign_id, "team_id": team_id, "scope_type": scope_type, "scope_id": scope_id}), 201
        except Exception as exc:
            logger.exception("assign_team failed")
            return jsonify({"error": str(exc)}), 500

    # ── Instances ─────────────────────────────────────────────────────────────

    @bp.route("/instances", methods=["GET"])
    def list_instances_route():
        if not _hitl_enabled():
            return _disabled_response()
        try:
            task_id = request.args.get("task_id")
            project_id = request.args.get("project_id")
            status = request.args.get("status")
            limit = int(request.args.get("limit", 100))

            conn = get_connection()
            try:
                clauses, params = [], []
                if task_id:
                    clauses.append("task_id=%s")
                    params.append(task_id)
                if project_id:
                    clauses.append("project_id=%s")
                    params.append(project_id)
                if status:
                    clauses.append("status=%s")
                    params.append(status)
                where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
                rows = conn.execute(
                    f"SELECT * FROM wf_instances {where} ORDER BY created_at DESC LIMIT %s",
                    params + [limit],
                ).fetchall()
                instances = [dict(r) for r in rows]
            finally:
                conn.close()

            return jsonify({"instances": instances, "count": len(instances)})
        except Exception as exc:
            logger.exception("list_instances failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/instances/<instance_id>", methods=["GET"])
    def get_instance_route(instance_id: str):
        if not _hitl_enabled():
            return _disabled_response()
        try:
            engine = WorkflowEngine()
            status = engine.get_status(instance_id)
            if not status:
                return jsonify({"error": f"Instance {instance_id!r} not found"}), 404
            return jsonify(status)
        except Exception as exc:
            logger.exception("get_instance failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/instances", methods=["POST"])
    def create_instance_route():
        if not _hitl_enabled():
            return _disabled_response()
        try:
            data = request.get_json(force=True) or {}
            engine = WorkflowEngine()
            instance_id = engine.create_instance(
                task_id=data.get("task_id"),
                project_id=data.get("project_id"),
                canvas_type=data.get("canvas_type"),
                template_id=data.get("template_id"),
            )
            return jsonify({"id": instance_id}), 201
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 422
        except Exception as exc:
            logger.exception("create_instance failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/instances/<instance_id>/approve", methods=["POST"])
    @require_role(*_WF_APPROVAL_ROLES)
    def approve_instance_route(instance_id: str):
        if not _hitl_enabled():
            return _disabled_response()
        try:
            current_user = _get_current_user()
            if not current_user:
                return jsonify({"error": "Unauthorized"}), 401

            # Verify instance exists
            engine = WorkflowEngine()
            status = engine.get_status(instance_id)
            if not status:
                return jsonify({"error": f"Instance {instance_id!r} not found"}), 404

            # Find the active pending approval
            pending = next(
                (a for a in status.get("approvals", []) if a.get("status") == "pending"),
                None,
            )
            if not pending:
                return jsonify({"error": "No pending approval gate found for this instance"}), 409

            data = request.get_json(force=True) or {}
            decision = data.get("decision", "approve")
            if decision not in ("approve", "conditional"):
                decision = "approve"

            feedback_id = feedback_mod.submit_feedback(
                approval_id=pending["id"],
                decision=decision,
                submitted_by=current_user,
                rating=data.get("rating"),
                comments=data.get("comments"),
                feedback_types=data.get("feedback_types"),
                improvement_tags=data.get("improvement_tags"),
            )
            return jsonify({"feedback_id": feedback_id, "decision": decision, "instance_id": instance_id})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
        except Exception as exc:
            logger.exception("approve_instance failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/instances/<instance_id>/kickback", methods=["POST"])
    @require_role(*_WF_APPROVAL_ROLES)
    def kickback_instance_route(instance_id: str):
        if not _hitl_enabled():
            return _disabled_response()
        try:
            current_user = _get_current_user()
            if not current_user:
                return jsonify({"error": "Unauthorized"}), 401

            engine = WorkflowEngine()
            status = engine.get_status(instance_id)
            if not status:
                return jsonify({"error": f"Instance {instance_id!r} not found"}), 404

            pending = next(
                (a for a in status.get("approvals", []) if a.get("status") == "pending"),
                None,
            )
            if not pending:
                return jsonify({"error": "No pending approval gate found for this instance"}), 409

            data = request.get_json(force=True) or {}
            kickback_reason = data.get("kickback_reason", "")

            feedback_id = feedback_mod.submit_feedback(
                approval_id=pending["id"],
                decision="kickback",
                submitted_by=current_user,
                rating=data.get("rating"),
                feedback_types=data.get("feedback_types"),
                kickback_reason=kickback_reason,
            )
            return jsonify({"feedback_id": feedback_id, "decision": "kickback", "instance_id": instance_id})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            logger.exception("kickback_instance failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/instances/<instance_id>/escalate", methods=["POST"])
    @require_role(*_WF_APPROVAL_ROLES)
    def escalate_instance_route(instance_id: str):
        if not _hitl_enabled():
            return _disabled_response()
        try:
            engine = WorkflowEngine()
            status = engine.get_status(instance_id)
            if not status:
                return jsonify({"error": f"Instance {instance_id!r} not found"}), 404

            data = request.get_json(force=True) or {}
            reason = data.get("reason", "Manual escalation via API")
            result = engine.escalate(instance_id, reason=reason)
            return jsonify(result)
        except Exception as exc:
            logger.exception("escalate_instance failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/instances/<instance_id>/cancel", methods=["POST"])
    @require_role(*_WF_APPROVAL_ROLES)
    def cancel_instance_route(instance_id: str):
        if not _hitl_enabled():
            return _disabled_response()
        try:
            engine = WorkflowEngine()
            status = engine.get_status(instance_id)
            if not status:
                return jsonify({"error": f"Instance {instance_id!r} not found"}), 404

            result = engine.cancel(instance_id)
            return jsonify(result)
        except Exception as exc:
            logger.exception("cancel_instance failed")
            return jsonify({"error": str(exc)}), 500

    # ── Feedback ──────────────────────────────────────────────────────────────

    @bp.route("/feedback", methods=["GET"])
    def list_feedback_route():
        if not _hitl_enabled():
            return _disabled_response()
        try:
            canvas_type = request.args.get("canvas_type")
            task_id = request.args.get("task_id")
            period_start = request.args.get("period_start")
            period_end = request.args.get("period_end")
            limit = int(request.args.get("limit", 100))

            results = feedback_mod.get_all(
                canvas_type=canvas_type,
                task_id=task_id,
                period_start=period_start,
                period_end=period_end,
                limit=limit,
            )
            return jsonify({"feedback": results, "count": len(results)})
        except Exception as exc:
            logger.exception("list_feedback failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/feedback/insights", methods=["GET"])
    def feedback_insights_route():
        if not _hitl_enabled():
            return _disabled_response()
        try:
            conn = get_connection()
            try:
                rows = conn.execute(
                    "SELECT * FROM wf_feedback_insights ORDER BY created_at DESC LIMIT 200"
                ).fetchall()
                insights = [dict(r) for r in rows]
            finally:
                conn.close()
            return jsonify({"insights": insights, "count": len(insights)})
        except Exception as exc:
            logger.exception("feedback_insights failed")
            return jsonify({"error": str(exc)}), 500

    # ── External steps ────────────────────────────────────────────────────────

    @bp.route("/external", methods=["GET"])
    def list_external_steps_route():
        if not _hitl_enabled():
            return _disabled_response()
        try:
            conn = get_connection()
            try:
                rows = conn.execute(
                    "SELECT * FROM wf_external_steps WHERE status IN ('pending','sent','waiting') ORDER BY created_at DESC"
                ).fetchall()
                steps = [dict(r) for r in rows]
            finally:
                conn.close()
            return jsonify({"external_steps": steps, "count": len(steps)})
        except Exception as exc:
            logger.exception("list_external_steps failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/external/<step_id>/complete", methods=["POST"])
    def complete_external_step_route(step_id: str):
        if not _hitl_enabled():
            return _disabled_response()
        try:
            # Webhook path: verify HMAC token if ?token= query param is present
            token = request.args.get("token")
            if token is not None:
                if not external_steps.verify_webhook_token(token, step_id):
                    return jsonify({"error": "Invalid webhook token"}), 403

            current_user = _get_current_user() or "webhook"
            success = external_steps.mark_complete(step_id, completed_by=current_user)
            if not success:
                return jsonify({"error": f"External step {step_id!r} not found"}), 404
            return jsonify({"completed": True, "step_id": step_id})
        except Exception as exc:
            logger.exception("complete_external_step failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/external/<step_id>/retry", methods=["POST"])
    def retry_external_step_route(step_id: str):
        if not _hitl_enabled():
            return _disabled_response()
        try:
            conn = get_connection()
            try:
                row = conn.execute("SELECT * FROM wf_external_steps WHERE id=%s", (step_id,)).fetchone()
                step = dict(row) if row else None
            finally:
                conn.close()

            if not step:
                return jsonify({"error": f"External step {step_id!r} not found"}), 404

            # Reset to pending so send() will dispatch again
            from tools.workflow_hitl.external_steps import _update_step, send
            _update_step(step_id, status="pending")
            external_ref = send(step_id)
            return jsonify({"retried": True, "step_id": step_id, "external_ref": external_ref})
        except Exception as exc:
            logger.exception("retry_external_step failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/integrations", methods=["GET"])
    def list_integrations_route():
        if not _hitl_enabled():
            return _disabled_response()
        try:
            import yaml
            cfg_path = os.path.join(
                os.path.dirname(__file__), "..", "..", "args", "workflow_hitl_integrations.yaml"
            )
            with open(cfg_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            integrations_raw = cfg.get("integrations", {})
            # Return system name + enabled flag only (never expose secrets)
            integrations = [
                {"system": name, "enabled": bool(conf.get("enabled", False))}
                for name, conf in integrations_raw.items()
            ]
            return jsonify({"integrations": integrations})
        except FileNotFoundError:
            return jsonify({"integrations": []})
        except Exception as exc:
            logger.exception("list_integrations failed")
            return jsonify({"error": str(exc)}), 500

    # ── Document templates ────────────────────────────────────────────────────

    @bp.route("/doc-templates", methods=["GET"])
    def list_doc_templates_route():
        if not _hitl_enabled():
            return _disabled_response()
        try:
            canvas_type = request.args.get("canvas_type")
            doc_type = request.args.get("doc_type")
            templates = document_manager.list_templates(canvas_type=canvas_type, doc_type=doc_type)
            return jsonify({"doc_templates": templates, "count": len(templates)})
        except Exception as exc:
            logger.exception("list_doc_templates failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/doc-templates", methods=["POST"])
    def create_doc_template_route():
        if not _hitl_enabled():
            return _disabled_response()
        try:
            current_user = _get_current_user()
            data = request.get_json(force=True) or {}
            name = data.get("name")
            doc_type = data.get("doc_type")
            schema_json = data.get("schema_json")
            if not name or not doc_type or schema_json is None:
                return jsonify({"error": "name, doc_type, and schema_json are required"}), 400
            doc_id = document_manager.create_template(
                name=name,
                doc_type=doc_type,
                schema_json=schema_json,
                canvas_type=data.get("canvas_type"),
                stage_scope=data.get("stage_scope"),
                is_ai_reference=bool(data.get("is_ai_reference", False)),
                is_human_required=bool(data.get("is_human_required", False)),
                version=str(data.get("version", "1")),
                created_by=current_user,
            )
            return jsonify({"id": doc_id}), 201
        except Exception as exc:
            logger.exception("create_doc_template failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/doc-templates/<doc_template_id>", methods=["GET"])
    def get_doc_template_route(doc_template_id: str):
        if not _hitl_enabled():
            return _disabled_response()
        try:
            tmpl = document_manager.get_template(doc_template_id)
            if not tmpl:
                return jsonify({"error": f"Document template {doc_template_id!r} not found"}), 404
            return jsonify(tmpl)
        except Exception as exc:
            logger.exception("get_doc_template failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/doc-templates/<doc_template_id>/standards", methods=["GET"])
    def get_applicable_standards_route(doc_template_id: str):
        if not _hitl_enabled():
            return _disabled_response()
        try:
            tmpl = document_manager.get_template(doc_template_id)
            if not tmpl:
                return jsonify({"error": f"Document template {doc_template_id!r} not found"}), 404
            canvas_type = request.args.get("canvas_type") or tmpl.get("canvas_type")
            stage = request.args.get("stage") or tmpl.get("stage_scope")
            standards = document_manager.get_applicable_standards(
                canvas_type=canvas_type,
                stage=stage,
            )
            return jsonify({"standards": standards, "count": len(standards)})
        except Exception as exc:
            logger.exception("get_applicable_standards failed")
            return jsonify({"error": str(exc)}), 500

    # ── Document submissions ──────────────────────────────────────────────────

    @bp.route("/approvals/<approval_id>/documents", methods=["POST"])
    def submit_document_route(approval_id: str):
        if not _hitl_enabled():
            return _disabled_response()
        try:
            current_user = _get_current_user()
            if not current_user:
                return jsonify({"error": "Unauthorized"}), 401

            conn = get_connection()
            try:
                approval = conn.execute(
                    "SELECT * FROM wf_approvals WHERE id=%s", (approval_id,)
                ).fetchone()
                approval = dict(approval) if approval else None
            finally:
                conn.close()

            if not approval:
                return jsonify({"error": f"Approval {approval_id!r} not found"}), 404

            data = request.get_json(force=True) or {}
            doc_template_id = data.get("doc_template_id")
            submission_json = data.get("submission_json")
            if not doc_template_id or submission_json is None:
                return jsonify({"error": "doc_template_id and submission_json are required"}), 400

            sub_id = document_manager.submit_document(
                approval_id=approval_id,
                instance_id=approval["instance_id"],
                doc_template_id=doc_template_id,
                stage=approval.get("stage", ""),
                submitted_by=current_user,
                submission_json=submission_json,
            )
            return jsonify({"id": sub_id, "approval_id": approval_id}), 201
        except Exception as exc:
            logger.exception("submit_document failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/approvals/<approval_id>/documents", methods=["GET"])
    def list_documents_route(approval_id: str):
        if not _hitl_enabled():
            return _disabled_response()
        try:
            conn = get_connection()
            try:
                approval = conn.execute(
                    "SELECT id FROM wf_approvals WHERE id=%s", (approval_id,)
                ).fetchone()
            finally:
                conn.close()

            if not approval:
                return jsonify({"error": f"Approval {approval_id!r} not found"}), 404

            submissions = document_manager.get_submissions(approval_id)
            return jsonify({"submissions": submissions, "count": len(submissions)})
        except Exception as exc:
            logger.exception("list_documents failed")
            return jsonify({"error": str(exc)}), 500

    # ── Citations ─────────────────────────────────────────────────────────────

    @bp.route("/instances/<instance_id>/citations", methods=["GET"])
    def get_instance_citations_route(instance_id: str):
        if not _hitl_enabled():
            return _disabled_response()
        try:
            conn = get_connection()
            try:
                inst = conn.execute(
                    "SELECT id FROM wf_instances WHERE id=%s", (instance_id,)
                ).fetchone()
            finally:
                conn.close()

            if not inst:
                return jsonify({"error": f"Instance {instance_id!r} not found"}), 404

            citations = citation_manager.get_by_instance(instance_id)
            return jsonify({"citations": citations, "count": len(citations)})
        except Exception as exc:
            logger.exception("get_instance_citations failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/feedback/<feedback_id>/citations", methods=["POST"])
    def add_feedback_citation_route(feedback_id: str):
        if not _hitl_enabled():
            return _disabled_response()
        try:
            current_user = _get_current_user()
            if not current_user:
                return jsonify({"error": "Unauthorized"}), 401

            conn = get_connection()
            try:
                fb = conn.execute(
                    "SELECT * FROM wf_feedback WHERE id=%s", (feedback_id,)
                ).fetchone()
                fb = dict(fb) if fb else None
            finally:
                conn.close()

            if not fb:
                return jsonify({"error": f"Feedback {feedback_id!r} not found"}), 404

            data = request.get_json(force=True) or {}
            source_doc = data.get("source_doc")
            if not source_doc:
                return jsonify({"error": "source_doc is required"}), 400

            cite_id = citation_manager.add_citation(
                instance_id=fb["instance_id"],
                stage=fb.get("stage", ""),
                source_doc=source_doc,
                cited_by=current_user,
                cited_in_type="feedback",
                cited_in_id=feedback_id,
                section=data.get("section"),
                page_number=data.get("page_number"),
                excerpt=data.get("excerpt"),
                source_type=data.get("source_type"),
                doc_version=data.get("doc_version"),
            )
            return jsonify({"id": cite_id, "feedback_id": feedback_id}), 201
        except Exception as exc:
            logger.exception("add_feedback_citation failed")
            return jsonify({"error": str(exc)}), 500

    # ── Coverage ──────────────────────────────────────────────────────────────

    @bp.route("/coverage", methods=["GET"])
    def coverage_route():
        if not _hitl_enabled():
            return _disabled_response()
        try:
            conn = get_connection()
            try:
                active_count = conn.execute(
                    "SELECT COUNT(*) FROM wf_instances WHERE status='active'"
                ).fetchone()[0]

                # Breakdown by canvas_type
                canvas_rows = conn.execute(
                    "SELECT canvas_type, COUNT(*) AS cnt FROM wf_instances WHERE status='active' GROUP BY canvas_type"
                ).fetchall()
                by_canvas = {(r[0] or "unknown"): r[1] for r in canvas_rows}

                # Breakdown by status (all statuses, not just active)
                status_rows = conn.execute(
                    "SELECT status, COUNT(*) AS cnt FROM wf_instances GROUP BY status"
                ).fetchall()
                by_status = {r[0]: r[1] for r in status_rows}

                # Tasks with a pending HITL gate (active instances linked to a task)
                tasks_pending = conn.execute(
                    "SELECT COUNT(DISTINCT task_id) FROM wf_instances "
                    "WHERE status='active' AND task_id IS NOT NULL"
                ).fetchone()[0]
            finally:
                conn.close()

            return jsonify({
                "active_instances": active_count,
                "by_canvas": by_canvas,
                "by_status": by_status,
                "tasks_with_pending_gate": tasks_pending,
            })
        except Exception as exc:
            logger.exception("coverage failed")
            return jsonify({"error": str(exc)}), 500

    # ── Document Ingestion routes ─────────────────────────────────────────────

    @bp.route("/doc-templates/<doc_template_id>/ingest", methods=["POST"])
    def ingest_document_route(doc_template_id: str):
        if not _hitl_enabled():
            return jsonify({"error": "HITL not enabled"}), 503
        try:
            if "file" not in request.files:
                return jsonify({"error": "No file provided"}), 400
            f = request.files["file"]
            if not f.filename:
                return jsonify({"error": "Empty filename"}), 400
            current_user = _get_current_user() or "anonymous"
            import tempfile
            import pathlib
            suffix = pathlib.Path(f.filename).suffix
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                f.save(tmp.name)
                tmp_path = tmp.name
            try:
                ingest_id = document_ingestion.ingest_file(
                    doc_template_id=doc_template_id,
                    file_path=tmp_path,
                    filename=f.filename,
                    ingested_by=current_user,
                )
            finally:
                pathlib.Path(tmp_path).unlink(missing_ok=True)
            return jsonify({"ingest_id": ingest_id, "filename": f.filename}), 201
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            logger.exception("ingest_document_route failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/doc-templates/<doc_template_id>/ingested-files", methods=["GET"])
    def list_ingested_files_route(doc_template_id: str):
        if not _hitl_enabled():
            return jsonify({"error": "HITL not enabled"}), 503
        try:
            files = document_ingestion.get_ingested_files(doc_template_id)
            return jsonify({"files": files, "count": len(files)})
        except Exception as exc:
            logger.exception("list_ingested_files failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/doc-templates/<doc_template_id>/ingested-files/<ingest_id>", methods=["DELETE"])
    def delete_ingested_file_route(doc_template_id: str, ingest_id: str):
        if not _hitl_enabled():
            return jsonify({"error": "HITL not enabled"}), 503
        try:
            ok = document_ingestion.delete_ingested_file(ingest_id)
            if not ok:
                return jsonify({"error": "Not found"}), 404
            return jsonify({"deleted": True})
        except Exception as exc:
            logger.exception("delete_ingested_file failed")
            return jsonify({"error": str(exc)}), 500

    # ── Report Generation routes ──────────────────────────────────────────────

    @bp.route("/report-types", methods=["GET"])
    def list_report_types_route():
        if not _hitl_enabled():
            return jsonify({"error": "HITL not enabled"}), 503
        try:
            from tools.workflow_hitl.report_schema import list_report_types
            types = list_report_types()
            return jsonify({"report_types": types})
        except Exception as exc:
            logger.exception("list_report_types failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/report-types/<report_type>/sections", methods=["GET"])
    def get_report_sections_route(report_type: str):
        if not _hitl_enabled():
            return jsonify({"error": "HITL not enabled"}), 503
        try:
            from tools.workflow_hitl.report_schema import get_sections
            sections = get_sections(report_type)
            return jsonify({"sections": [dataclasses.asdict(s) for s in sections]})
        except Exception as exc:
            logger.exception("get_report_sections failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/instances/<instance_id>/generate-report", methods=["POST"])
    def generate_report_route(instance_id: str):
        if not _hitl_enabled():
            return jsonify({"error": "HITL not enabled"}), 503
        current_user = _get_current_user()
        if not current_user:
            return jsonify({"error": "Unauthorized"}), 401
        try:
            data = request.get_json(silent=True) or {}
            report_type = data.get("report_type", "standard_audit")
            style_guide_id = data.get("style_guide_id")
            report_id = report_generator.generate_report(
                instance_id=instance_id,
                report_type=report_type,
                style_guide_id=style_guide_id,
                generated_by=current_user,
            )
            return jsonify({"report_id": report_id}), 202
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            logger.exception("generate_report_route failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/reports", methods=["GET"])
    def list_reports_route():
        if not _hitl_enabled():
            return jsonify({"error": "HITL not enabled"}), 503
        try:
            instance_id = request.args.get("instance_id")
            reports = report_generator.list_reports(instance_id=instance_id)
            return jsonify({"reports": reports, "count": len(reports)})
        except Exception as exc:
            logger.exception("list_reports failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/reports/<report_id>", methods=["GET"])
    def get_report_route(report_id: str):
        if not _hitl_enabled():
            return jsonify({"error": "HITL not enabled"}), 503
        try:
            report = report_generator.get_report(report_id)
            if not report:
                return jsonify({"error": "Report not found"}), 404
            fmt = request.args.get("format", "json")
            if fmt == "html" and report.get("content_html"):
                from flask import Response
                return Response(report["content_html"], mimetype="text/html")
            return jsonify(report)
        except Exception as exc:
            logger.exception("get_report failed")
            return jsonify({"error": str(exc)}), 500

    return bp


def create_wf_page_blueprint() -> Blueprint:
    """Page blueprint for HITL Workflow — mounts UI routes at /workflow."""
    page_bp = Blueprint("wf_pages", __name__, url_prefix="/workflow")

    @page_bp.route("/")
    @page_bp.route("")
    def wf_queue():
        if not _hitl_enabled():
            from flask import abort
            abort(503)
        from flask import render_template as _rt
        return _rt("workflow_hitl.html")

    @page_bp.route("/teams")
    def wf_teams():
        if not _hitl_enabled():
            from flask import abort
            abort(503)
        from flask import render_template as _rt
        return _rt("workflow_teams.html")

    @page_bp.route("/hitl")
    def wf_hitl_redirect():
        from flask import redirect
        return redirect("/workflow/")

    return page_bp
