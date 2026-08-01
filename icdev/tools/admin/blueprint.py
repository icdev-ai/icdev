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
import uuid
from datetime import datetime, timezone

from flask import Blueprint, abort, g, jsonify, render_template, request

from tools.logging.icdev_logger import get_logger

log = get_logger(__name__)

_ENABLED = os.environ.get("ICDEV_ADMIN_CONSOLE_ENABLED", "false").lower() in ("true", "1", "yes")

_ADMIN_ROLES = {"admin", "superadmin", "system_admin"}


def _require_admin():
    """Abort 403 unless the current user has an admin role.

    Authorization is enforced UNCONDITIONALLY. It deliberately does NOT depend
    on ICDEV_ENFORCE_CANVAS_ACCESS: that flag governs *canvas access*
    enforcement platform-wide and must never gate the tenant Admin Console's
    mutating endpoints (component overrides, SSO providers, API keys, GDPR
    erasure). Gating admin auth on an unset-by-default canvas flag was a
    fail-open regression — see kanban nav-sec-02.

    There is no local-dev bypass by design. Tests / local dev supply an admin
    role via g.current_user like any other authenticated caller.
    """
    user = getattr(g, "current_user", None) or {}
    role = str(user.get("role", "") or "")
    if role not in _ADMIN_ROLES:
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
                    f"ORDER BY recorded_at DESC LIMIT %s OFFSET %s",
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
                    f"ORDER BY granted_at DESC LIMIT %s OFFSET %s",
                    params + [limit, offset],
                ).fetchall()
            return jsonify({
                "grants": [dict(r) for r in rows],
                "limit": limit,
                "offset": offset,
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ------------------------------------------------------------------ #
    # REST API — SSO Providers
    # ------------------------------------------------------------------ #

    @bp.route("/api/admin/tenants/<tenant_id>/sso-providers", methods=["GET"])
    def api_list_sso_providers(tenant_id: str):
        """List SSO providers for a tenant. ?all=1 includes disabled ones."""
        from tools.db.storage import get_connection

        include_all = request.args.get("all", "0") in ("1", "true")
        where_extra = "" if include_all else " AND enabled = 1"
        try:
            with get_connection() as conn:
                rows = conn.execute(
                    "SELECT id, tenant_id, name, protocol, entity_id, metadata_url, "
                    "client_id, attr_mapping, enabled, created_at, updated_at "
                    f"FROM sso_providers WHERE tenant_id = %s{where_extra} ORDER BY created_at DESC",
                    (tenant_id,),
                ).fetchall()
            providers = []
            for r in rows:
                p = dict(r)
                p["login_url"] = (
                    f"/auth/saml/{p['id']}/login"
                    if p["protocol"] == "saml"
                    else f"/auth/saml/oidc/{p['id']}/login"
                )
                providers.append(p)
            return jsonify({"tenant_id": tenant_id, "providers": providers})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/admin/tenants/<tenant_id>/sso-providers", methods=["POST"])
    def api_create_sso_provider(tenant_id: str):
        """Create a new SSO provider for a tenant."""
        from tools.db.storage import get_connection

        payload = request.get_json(silent=True) or {}
        name = (payload.get("name") or "").strip()
        protocol = (payload.get("protocol") or "").lower()

        if not name:
            return jsonify({"error": "Missing 'name' field"}), 400
        if protocol not in ("saml", "oidc"):
            return jsonify({"error": "protocol must be 'saml' or 'oidc'"}), 400

        pid = str(uuid.uuid4())
        try:
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO sso_providers "
                    "(id, tenant_id, name, protocol, entity_id, metadata_url, client_id, attr_mapping) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        pid, tenant_id, name, protocol,
                        payload.get("entity_id"),
                        payload.get("metadata_url"),
                        payload.get("client_id"),
                        payload.get("attr_mapping"),
                    ),
                )
                conn.commit()
            login_url = (
                f"/auth/saml/{pid}/login" if protocol == "saml" else f"/auth/saml/oidc/{pid}/login"
            )
            return jsonify({
                "provider_id": pid,
                "tenant_id": tenant_id,
                "name": name,
                "protocol": protocol,
                "login_url": login_url,
            }), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/admin/tenants/<tenant_id>/sso-providers/<provider_id>", methods=["PUT"])
    def api_update_sso_provider(tenant_id: str, provider_id: str):
        """Update fields on an existing SSO provider."""
        from tools.db.storage import get_connection

        payload = request.get_json(silent=True) or {}
        _ALLOWED = ("name", "protocol", "entity_id", "metadata_url", "client_id", "attr_mapping")
        updates = {k: payload[k] for k in _ALLOWED if k in payload}

        if not updates:
            return jsonify({"error": "No updatable fields provided"}), 400
        if "protocol" in updates and updates["protocol"] not in ("saml", "oidc"):
            return jsonify({"error": "protocol must be 'saml' or 'oidc'"}), 400

        now = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates) + ", updated_at = ?"
        params = list(updates.values()) + [now, provider_id, tenant_id]
        try:
            with get_connection() as conn:
                cur = conn.execute(
                    f"UPDATE sso_providers SET {set_clause} WHERE id = %s AND tenant_id = %s",
                    params,
                )
                conn.commit()
                affected = cur.rowcount
            if affected == 0:
                return jsonify({"error": "Provider not found"}), 404
            return jsonify({"provider_id": provider_id, "tenant_id": tenant_id, "updated": True})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/admin/tenants/<tenant_id>/sso-providers/<provider_id>", methods=["DELETE"])
    def api_disable_sso_provider(tenant_id: str, provider_id: str):
        """Soft-disable an SSO provider (sets enabled=0). Row is preserved."""
        from tools.db.storage import get_connection

        now = datetime.now(timezone.utc).isoformat()
        try:
            with get_connection() as conn:
                cur = conn.execute(
                    "UPDATE sso_providers SET enabled = 0, updated_at = %s WHERE id = %s AND tenant_id = %s",
                    (now, provider_id, tenant_id),
                )
                conn.commit()
                affected = cur.rowcount
            if affected == 0:
                return jsonify({"error": "Provider not found"}), 404
            return jsonify({"provider_id": provider_id, "tenant_id": tenant_id, "disabled": True})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ------------------------------------------------------------------ #
    # REST API — SOC 2 Evidence Items
    # ------------------------------------------------------------------ #

    @bp.route("/api/admin/tenants/<tenant_id>/evidence", methods=["GET"])
    def api_list_evidence(tenant_id: str):
        """List evidence_items for a tenant. ?control_id=CC6.1 to filter."""
        from tools.db.storage import get_connection

        control_id = request.args.get("control_id")
        framework = request.args.get("framework", "soc2")
        try:
            with get_connection() as conn:
                if control_id:
                    rows = conn.execute(
                        "SELECT id, control_id, framework, tenant_id, evidence_type, "
                        "source_table, source_row_id, summary, collected_at, collector "
                        "FROM evidence_items WHERE tenant_id = %s AND control_id = %s "
                        "AND framework = %s ORDER BY collected_at DESC LIMIT 500",
                        (tenant_id, control_id, framework),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id, control_id, framework, tenant_id, evidence_type, "
                        "source_table, source_row_id, summary, collected_at, collector "
                        "FROM evidence_items WHERE tenant_id = %s AND framework = %s "
                        "ORDER BY collected_at DESC LIMIT 500",
                        (tenant_id, framework),
                    ).fetchall()
                ctrl_rows = conn.execute(
                    "SELECT DISTINCT control_id FROM evidence_items "
                    "WHERE tenant_id = %s ORDER BY control_id",
                    (tenant_id,),
                ).fetchall()
            cols = ["id", "control_id", "framework", "tenant_id", "evidence_type",
                    "source_table", "source_row_id", "summary", "collected_at", "collector"]
            items = [dict(zip(cols, r)) for r in rows]
            controls = [r[0] for r in ctrl_rows]
            return jsonify({"tenant_id": tenant_id, "items": items, "controls": controls})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/admin/tenants/<tenant_id>/evidence/export", methods=["GET"])
    def api_export_evidence(tenant_id: str):
        """Export SOC 2 evidence report as HTML or JSON."""
        import tempfile
        from pathlib import Path
        from tools.compliance.soc2_exporter import export_report

        fmt = request.args.get("format", "html")
        framework = request.args.get("framework", "soc2")
        with tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            export_report(tenant_id, tmp_path, fmt=fmt, framework=framework)
            content = Path(tmp_path).read_text(encoding="utf-8")
            mime = "text/html" if fmt == "html" else "application/json"
            from flask import Response as FlaskResponse
            return FlaskResponse(
                content, mimetype=mime,
                headers={"Content-Disposition": f'attachment; filename="soc2_report.{fmt}"'},
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ------------------------------------------------------------------ #
    # REST API — GDPR Erasure
    # ------------------------------------------------------------------ #

    @bp.route("/api/admin/tenants/<tenant_id>/erasure", methods=["POST"])
    def api_erasure(tenant_id: str):
        """Initiate GDPR right-to-erasure for a tenant.

        Body JSON:
            confirmation_token (str, required) — must equal the literal string
                "CONFIRM_ERASURE" to prevent accidental triggering.
            scope (str, optional) — defaults to 'pii'.
        """
        payload = request.get_json(silent=True) or {}
        token = payload.get("confirmation_token", "")
        if token != "CONFIRM_ERASURE":
            return jsonify({"error": "confirmation_token must be 'CONFIRM_ERASURE'"}), 400

        scope = str(payload.get("scope") or "pii")
        user = getattr(g, "current_user", None) or {}
        requested_by = str(user.get("email") or user.get("id") or "admin")

        try:
            from tools.compliance.gdpr_eraser import erase_tenant_data
            result = erase_tenant_data(tenant_id, requested_by, scope)
        except Exception as exc:
            log.error("GDPR erasure failed for tenant %s: %s", tenant_id, exc)
            return jsonify({"error": str(exc)}), 500

        log.info(
            "GDPR erasure completed: tenant=%s by=%s tables=%s",
            tenant_id,
            requested_by,
            result["tables_affected"],
        )
        return jsonify(result), 200

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

    # ------------------------------------------------------------------ #
    # REST API — Usage Analytics
    # ------------------------------------------------------------------ #

    @bp.route("/api/admin/tenants/<tenant_id>/usage", methods=["GET"])
    def api_tenant_usage(tenant_id: str):
        """Per-tenant usage breakdown.

        Query params:
          since  — ISO date string (default: 30 days ago)

        Returns:
          summary   — total quantity per event_type for the window
          daily     — [{date, event_type, total_quantity}, ...]
          current_month_total — sum of all events this calendar month
        """
        from tools.db.storage import get_connection

        since_raw = request.args.get("since", "")
        if since_raw:
            since = since_raw[:10]
        else:
            since_dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            from datetime import timedelta
            since_dt = since_dt - timedelta(days=30)
            since = since_dt.strftime("%Y-%m-%d")

        month_start = datetime.now(timezone.utc).strftime("%Y-%m-01")

        try:
            with get_connection() as conn:
                summary_rows = conn.execute(
                    "SELECT event_type, SUM(quantity) AS total_quantity "
                    "FROM usage_events "
                    "WHERE tenant_id = %s AND recorded_at >= %s "
                    "GROUP BY event_type ORDER BY total_quantity DESC",
                    (tenant_id, since),
                ).fetchall()

                daily_rows = conn.execute(
                    "SELECT substr(recorded_at, 1, 10) AS date, event_type, "
                    "SUM(quantity) AS total_quantity "
                    "FROM usage_events "
                    "WHERE tenant_id = %s AND recorded_at >= %s "
                    "GROUP BY substr(recorded_at, 1, 10), event_type "
                    "ORDER BY date, event_type",
                    (tenant_id, since),
                ).fetchall()

                month_row = conn.execute(
                    "SELECT SUM(quantity) AS total "
                    "FROM usage_events "
                    "WHERE tenant_id = %s AND recorded_at >= %s",
                    (tenant_id, month_start),
                ).fetchone()

            summary = {r[0]: r[1] for r in summary_rows}
            daily = [{"date": r[0], "event_type": r[1], "total_quantity": r[2]} for r in daily_rows]
            month_total = (month_row[0] or 0.0) if month_row else 0.0

            return jsonify({
                "tenant_id": tenant_id,
                "since": since,
                "summary": summary,
                "daily": daily,
                "current_month_total": month_total,
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ------------------------------------------------------------------ #
    # REST API — API Key Management
    # ------------------------------------------------------------------ #

    @bp.route("/api/admin/tenants/<tenant_id>/api-keys", methods=["GET"])
    def api_list_api_keys(tenant_id: str):
        """List API keys for a tenant. Never returns the raw key."""
        from tools.db.storage import get_connection
        try:
            with get_connection() as conn:
                rows = conn.execute(
                    "SELECT id, name, key_prefix, scopes, created_at, last_used_at, "
                    "expires_at, revoked_at FROM api_keys WHERE tenant_id = %s "
                    "ORDER BY created_at DESC",
                    (tenant_id,),
                ).fetchall()
            return jsonify({"tenant_id": tenant_id, "keys": [dict(r) for r in rows]})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/admin/tenants/<tenant_id>/api-keys", methods=["POST"])
    def api_create_api_key(tenant_id: str):
        """Create a new API key. Returns raw_key ONCE — store immediately."""
        from tools.auth.api_key import generate_api_key
        from tools.db.storage import get_connection
        payload = request.get_json(silent=True) or {}
        name = (payload.get("name") or "").strip()
        scopes = (payload.get("scopes") or "read").strip()
        expires_at = payload.get("expires_at")
        if not name:
            return jsonify({"error": "Missing 'name' field"}), 400
        try:
            prefix, raw_key, key_hash = generate_api_key(
                tenant_id, name, scopes=scopes, expires_at=expires_at
            )
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT id FROM api_keys WHERE key_hash = %s", (key_hash,)
                ).fetchone()
            key_id = row["id"] if row else None
            return jsonify({
                "key_id": key_id,
                "tenant_id": tenant_id,
                "name": name,
                "prefix": prefix,
                "raw_key": raw_key,
                "scopes": scopes,
            }), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/admin/tenants/<tenant_id>/api-keys/<key_id>", methods=["DELETE"])
    def api_revoke_api_key(tenant_id: str, key_id: str):
        """Soft-revoke an API key by setting revoked_at."""
        from tools.db.storage import get_connection
        now = datetime.now(timezone.utc).isoformat()
        try:
            with get_connection() as conn:
                cur = conn.execute(
                    "UPDATE api_keys SET revoked_at = %s WHERE id = %s AND tenant_id = %s",
                    (now, key_id, tenant_id),
                )
                conn.commit()
                affected = cur.rowcount
            if affected == 0:
                return jsonify({"error": "Key not found"}), 404
            return jsonify({"key_id": key_id, "tenant_id": tenant_id, "revoked": True})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ------------------------------------------------------------------ #
    # REST API — Webhook Endpoint Management
    # ------------------------------------------------------------------ #

    @bp.route("/api/admin/tenants/<tenant_id>/webhooks", methods=["GET"])
    def api_list_webhooks(tenant_id: str):
        """List webhook endpoints for a tenant."""
        from tools.db.storage import get_connection
        try:
            with get_connection() as conn:
                rows = conn.execute(
                    "SELECT id, url, event_types, enabled, created_at "
                    "FROM webhook_endpoints WHERE tenant_id = %s ORDER BY created_at DESC",
                    (tenant_id,),
                ).fetchall()
            return jsonify({"tenant_id": tenant_id, "endpoints": [dict(r) for r in rows]})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/admin/tenants/<tenant_id>/webhooks", methods=["POST"])
    def api_create_webhook(tenant_id: str):
        """Register a new webhook endpoint. Returns the signing secret ONCE."""
        import secrets as _secrets
        from tools.db.storage import get_connection
        payload = request.get_json(silent=True) or {}
        url = (payload.get("url") or "").strip()
        event_types = payload.get("event_types", "*")
        if not url:
            return jsonify({"error": "Missing 'url' field"}), 400
        if isinstance(event_types, list):
            event_types = ",".join(event_types)
        wid = str(uuid.uuid4())
        secret = _secrets.token_hex(32)
        now = datetime.now(timezone.utc).isoformat()
        try:
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO webhook_endpoints "
                    "(id, tenant_id, url, event_types, secret, enabled, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, 1, %s)",
                    (wid, tenant_id, url, event_types, secret, now),
                )
                conn.commit()
            return jsonify({
                "endpoint_id": wid,
                "tenant_id": tenant_id,
                "url": url,
                "event_types": event_types,
                "secret": secret,
            }), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/admin/tenants/<tenant_id>/webhooks/<endpoint_id>", methods=["DELETE"])
    def api_disable_webhook(tenant_id: str, endpoint_id: str):
        """Disable a webhook endpoint (soft delete, sets enabled=0)."""
        from tools.db.storage import get_connection
        try:
            with get_connection() as conn:
                cur = conn.execute(
                    "UPDATE webhook_endpoints SET enabled = 0 WHERE id = %s AND tenant_id = %s",
                    (endpoint_id, tenant_id),
                )
                conn.commit()
                affected = cur.rowcount
            if affected == 0:
                return jsonify({"error": "Endpoint not found"}), 404
            return jsonify({"endpoint_id": endpoint_id, "tenant_id": tenant_id, "disabled": True})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/admin/usage/overview", methods=["GET"])
    def api_usage_overview():
        """Cross-tenant usage overview (super-admin).

        Query params:
          since  — ISO date string (default: 30 days ago)
          limit  — max top-consumer rows (default 50)

        Returns:
          top_consumers — [{tenant_id, event_type, total_quantity}, ...]
          by_event_type — {event_type: total_quantity, ...}
        """
        from tools.db.storage import get_connection

        since_raw = request.args.get("since", "")
        if since_raw:
            since = since_raw[:10]
        else:
            from datetime import timedelta
            since_dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            since_dt = since_dt - timedelta(days=30)
            since = since_dt.strftime("%Y-%m-%d")

        limit = min(int(request.args.get("limit", 50)), 200)

        try:
            with get_connection() as conn:
                consumer_rows = conn.execute(
                    "SELECT tenant_id, event_type, SUM(quantity) AS total_quantity "
                    "FROM usage_events "
                    "WHERE recorded_at >= %s "
                    "GROUP BY tenant_id, event_type "
                    "ORDER BY total_quantity DESC LIMIT %s",
                    (since, limit),
                ).fetchall()

                type_rows = conn.execute(
                    "SELECT event_type, SUM(quantity) AS total_quantity "
                    "FROM usage_events "
                    "WHERE recorded_at >= %s "
                    "GROUP BY event_type ORDER BY total_quantity DESC",
                    (since,),
                ).fetchall()

            top_consumers = [
                {"tenant_id": r[0], "event_type": r[1], "total_quantity": r[2]}
                for r in consumer_rows
            ]
            by_event_type = {r[0]: r[1] for r in type_rows}

            return jsonify({
                "since": since,
                "top_consumers": top_consumers,
                "by_event_type": by_event_type,
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    return bp


def create_stripe_webhook_blueprint():
    """Return a Blueprint for POST /webhooks/stripe.

    This blueprint has NO auth — Stripe signature verification is used instead.
    Must be registered separately from the admin blueprint so that
    before_request(_require_admin) does not apply.
    """
    from tools.billing.stripe_handler import verify_stripe_signature, handle_webhook

    wb = Blueprint("stripe_webhooks", __name__)

    @wb.route("/webhooks/stripe", methods=["POST"])
    def stripe_webhook():
        payload = request.get_data()
        sig_header = request.headers.get("Stripe-Signature", "")

        if not verify_stripe_signature(payload, sig_header):
            log.warning("stripe_webhook: invalid signature")
            return jsonify({"error": "invalid signature"}), 403

        try:
            import json as _json
            body = _json.loads(payload)
        except Exception:
            return jsonify({"error": "invalid JSON"}), 400

        event_type = body.get("type", "")
        event_data = (body.get("data") or {}).get("object") or {}

        result = handle_webhook(event_type, event_data)
        log.info("stripe_webhook: %s handled=%s", event_type, result.get("handled"))
        return jsonify(result), 200

    return wb
