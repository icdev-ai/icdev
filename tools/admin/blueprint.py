# CUI // SP-CTI
"""ICDEV™ Admin Console Blueprint.

Provides:
  /admin/                          — component management dashboard
  /api/admin/tenants/<id>/components     GET  — list all components + tenant override state
  /api/admin/tenants/<id>/components/<k> POST — set override {enabled: bool}
  /api/admin/tenants/<id>/components/<k> DELETE — clear override (revert to env default)
  /api/admin/audit/component-changes     GET  — paginated component_audit_log
  /api/admin/audit/access-grants         GET  — paginated canvas_access_grants

Access: ICDEV_ADMIN_CONSOLE_ENABLED=true + admin role.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from flask import Blueprint, abort, g, jsonify, render_template, request

from tools.logging.icdev_logger import get_logger

log = get_logger(__name__)

_ENABLED = os.environ.get("ICDEV_ADMIN_CONSOLE_ENABLED", "false").lower() in ("true", "1", "yes")

_ADMIN_ROLES = {"admin", "superadmin", "system_admin"}


def _require_admin():
    """Abort 403 unless the current user has an admin role."""
    user = getattr(g, "current_user", None) or {}
    role = str(user.get("role", "") or "")
    # In local-dev (no enforced auth) we allow through
    enforce = os.environ.get("ICDEV_ENFORCE_CANVAS_ACCESS", "").lower() in ("true", "1", "yes")
    if enforce and role not in _ADMIN_ROLES:
        abort(403, "Admin role required")


def create_admin_blueprint() -> Blueprint | None:
    if not _ENABLED:
        log.info("Admin Console disabled via ICDEV_ADMIN_CONSOLE_ENABLED=false")
        return None

    bp = Blueprint("admin_console", __name__, template_folder="../../tools/dashboard/templates")

    bp.before_request(_require_admin)

    # ------------------------------------------------------------------ #
    # Pages
    # ------------------------------------------------------------------ #

    @bp.route("/admin/")
    @bp.route("/admin")
    def index():
        try:
            from tools.config.component_registry import get_registry
            registry = get_registry()
            all_components = registry.list_all()
        except Exception:
            all_components = []
        return render_template(
            "admin/page.html",
            components=all_components,
        )

    # ------------------------------------------------------------------ #
    # REST API — Tenant Component Overrides
    # ------------------------------------------------------------------ #

    @bp.route("/api/admin/tenants/<tenant_id>/components", methods=["GET"])
    def api_list_components(tenant_id: str):
        """List all components with their enablement state for a tenant."""
        try:
            from tools.config.component_registry import get_registry
            registry = get_registry()
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        result = []
        for comp in registry.list_all():
            env_enabled = comp.is_enabled()
            tenant_enabled = registry.is_enabled_for_tenant(comp.key, tenant_id)
            overrides = registry.list_tenant_overrides(tenant_id)
            override_row = next((o for o in overrides if o["component_key"] == comp.key), None)
            result.append({
                "key": comp.key,
                "kind": comp.kind,
                "display_name": comp.display_name,
                "env_enabled": env_enabled,
                "tenant_enabled": tenant_enabled,
                "has_override": override_row is not None,
                "override": override_row,
            })
        return jsonify({"tenant_id": tenant_id, "components": result})

    @bp.route("/api/admin/tenants/<tenant_id>/components/<component_key>", methods=["POST"])
    def api_set_component_override(tenant_id: str, component_key: str):
        """Set a per-tenant component override."""
        payload = request.get_json(silent=True) or {}
        enabled = payload.get("enabled")
        if enabled is None:
            return jsonify({"error": "Missing 'enabled' field"}), 400
        enabled = bool(enabled)

        user = getattr(g, "current_user", None) or {}
        actor = str(user.get("email", "") or user.get("id", "") or "admin")

        try:
            from tools.config.component_registry import get_registry
            registry = get_registry()
            ok = registry.set_tenant_component_override(tenant_id, component_key, enabled, updated_by=actor)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        return jsonify({
            "tenant_id": tenant_id,
            "component_key": component_key,
            "enabled": enabled,
            "updated_by": actor,
            "success": ok,
        })

    @bp.route("/api/admin/tenants/<tenant_id>/components/<component_key>", methods=["DELETE"])
    def api_clear_component_override(tenant_id: str, component_key: str):
        """Clear a per-tenant component override (reverts to env default)."""
        user = getattr(g, "current_user", None) or {}
        actor = str(user.get("email", "") or user.get("id", "") or "admin")

        try:
            from tools.config.component_registry import get_registry
            registry = get_registry()
            ok = registry.clear_tenant_component_override(tenant_id, component_key)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        return jsonify({
            "tenant_id": tenant_id,
            "component_key": component_key,
            "cleared": ok,
            "actor": actor,
        })

    # ------------------------------------------------------------------ #
    # REST API — Audit Logs
    # ------------------------------------------------------------------ #

    @bp.route("/api/admin/audit/component-changes", methods=["GET"])
    def api_audit_component_changes():
        """Paginated component_audit_log entries."""
        limit = min(int(request.args.get("limit", 100)), 500)
        offset = int(request.args.get("offset", 0))
        event_type = request.args.get("event_type")
        component_key = request.args.get("component_key")

        try:
            from tools.db.storage import get_connection
            with get_connection() as conn:
                where_clauses = []
                params: list = []
                if event_type:
                    where_clauses.append("event_type = ?")
                    params.append(event_type)
                if component_key:
                    where_clauses.append("component_key = ?")
                    params.append(component_key)
                where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
                rows = conn.execute(
                    f"SELECT id, event_type, actor, tenant_id, component_key, "
                    f"profile_name, details, classification, recorded_at "
                    f"FROM component_audit_log {where_sql} "
                    f"ORDER BY recorded_at DESC LIMIT ? OFFSET ?",
                    params + [limit, offset],
                ).fetchall()
            return jsonify({
                "entries": [dict(r) for r in rows],
                "limit": limit,
                "offset": offset,
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/admin/audit/access-grants", methods=["GET"])
    def api_audit_access_grants():
        """Paginated canvas_access_grants entries."""
        limit = min(int(request.args.get("limit", 100)), 500)
        offset = int(request.args.get("offset", 0))
        tenant_id = request.args.get("tenant_id")
        canvas = request.args.get("canvas_name")

        try:
            from tools.db.storage import get_connection
            with get_connection() as conn:
                where_clauses = []
                params: list = []
                if tenant_id:
                    where_clauses.append("tenant_id = ?")
                    params.append(tenant_id)
                if canvas:
                    where_clauses.append("canvas_name = ?")
                    params.append(canvas)
                where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
                rows = conn.execute(
                    f"SELECT id, tenant_id, principal_type, principal_id, canvas_name, "
                    f"access_level, granted_by, granted_at, expires_at, revoked_at "
                    f"FROM canvas_access_grants {where_sql} "
                    f"ORDER BY granted_at DESC LIMIT ? OFFSET ?",
                    params + [limit, offset],
                ).fetchall()
            return jsonify({
                "grants": [dict(r) for r in rows],
                "limit": limit,
                "offset": offset,
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/admin/stats", methods=["GET"])
    def api_stats():
        """Return admin console summary stats."""
        try:
            from tools.config.component_registry import get_registry
            registry = get_registry()
            n_canvases = sum(1 for _ in registry.iter_canvases())
            n_enabled = sum(1 for c in registry.iter_canvases() if c.is_enabled())
        except Exception:
            n_canvases = n_enabled = 0
        return jsonify({
            "total_canvases": n_canvases,
            "enabled_canvases": n_enabled,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    return bp
