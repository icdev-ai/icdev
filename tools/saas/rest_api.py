#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""ICDEV SaaS -- REST API v1 Blueprint.

Flask Blueprint providing multi-tenant REST API endpoints for the ICDEV
SaaS platform.  All endpoints require authentication (handled by the auth
middleware which sets g.tenant_id, g.user_id, g.user_role).

Endpoint groups:
    /api/v1/tenants/me        - Current tenant info & settings
    /api/v1/users             - User & team management
    /api/v1/keys              - API key management
    /api/v1/projects          - Project CRUD (delegates to existing tools)
    /api/v1/projects/<id>/... - Compliance & security (delegates to tools)
    /api/v1/projects/<id>/devsecops - DevSecOps profile management
    /api/v1/projects/<id>/zta - Zero Trust Architecture maturity
    /api/v1/projects/<id>/simulations - Simulation scenario management
    /api/v1/projects/<id>/mosa - MOSA assessment
    /api/v1/projects/<id>/supply-chain/graph - Supply chain dependency graph
    /api/v1/marketplace/search - Marketplace asset search
    /api/v1/oscal/...         - OSCAL tool detection & catalog (D302-D306)
    /api/v1/projects/<id>/oscal - OSCAL validation & conversion
    /api/v1/audit/...         - Production readiness audit & remediation (D291-D300)
    /api/v1/events            - SSE platform audit event stream
    /api/v1/usage             - Usage & billing data

Usage:
    from tools.saas.rest_api import api_bp
    app.register_blueprint(api_bp)
"""

import hashlib
import json as json_mod
import logging
import os
import secrets
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from flask import Blueprint, Response, g, jsonify, request, stream_with_context

logger = logging.getLogger("saas.rest_api")

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
api_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")

# ---------------------------------------------------------------------------
# Platform DB helper
# ---------------------------------------------------------------------------
PLATFORM_DB_PATH = Path(
    os.environ.get("PLATFORM_DB_PATH", str(BASE_DIR / "data" / "platform.db"))
)


def _platform_conn():
    """Open a connection to the platform database."""
    conn = sqlite3.connect(str(PLATFORM_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _utcnow():
    """Return current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Lazy imports for ICDEV tools (avoids import-time side effects)
# ---------------------------------------------------------------------------
def _import_tenant_db():
    from tools.saas.tenant_db_adapter import (
        call_tool_with_tenant_db,
        get_tenant_db_path,
        verify_project_belongs_to_tenant,
    )
    return call_tool_with_tenant_db, get_tenant_db_path, verify_project_belongs_to_tenant


def _import_tenant_manager():
    from tools.saas.tenant_manager import (
        add_user,
        get_tenant,
        list_users,
        remove_user,
        update_tenant,
    )
    return get_tenant, update_tenant, list_users, add_user, remove_user


def _error(message, code="ERROR", status=400):
    """Return a standard JSON error response."""
    return jsonify({"error": message, "code": code}), status


def _require_role(*allowed_roles):
    """Check if the current user's role is in the allowed list."""
    role = getattr(g, "user_role", None)
    if role not in allowed_roles:
        return _error(
            "Insufficient permissions. Required role: {}".format(
                " or ".join(allowed_roles)),
            code="FORBIDDEN", status=403,
        )
    return None


# ============================================================================
# TENANT MANAGEMENT
# ============================================================================

@api_bp.route("/tenants/me", methods=["GET"])
def get_current_tenant():
    """GET /api/v1/tenants/me -- Return current tenant info."""
    try:
        get_tenant, _, _, _, _ = _import_tenant_manager()
        tenant = get_tenant(g.tenant_id)
        if not tenant:
            return _error("Tenant not found", code="NOT_FOUND", status=404)
        return jsonify({"tenant": tenant})
    except Exception as exc:
        logger.error("get_current_tenant error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/tenants/me", methods=["PATCH"])
def update_current_tenant():
    """PATCH /api/v1/tenants/me -- Update tenant settings."""
    role_err = _require_role("tenant_admin")
    if role_err:
        return role_err

    try:
        _, update_tenant, _, _, _ = _import_tenant_manager()
        data = request.get_json(force=True, silent=True) or {}
        if not data:
            return _error("Request body required")

        allowed_keys = {"settings", "artifact_config", "bedrock_config",
                        "idp_config", "name"}
        filtered = {k: v for k, v in data.items() if k in allowed_keys}
        if not filtered:
            return _error("No valid fields to update. Allowed: {}".format(
                ", ".join(sorted(allowed_keys))))

        result = update_tenant(g.tenant_id, **filtered)
        return jsonify({"tenant": result})
    except ValueError as exc:
        return _error(str(exc), status=400)
    except Exception as exc:
        logger.error("update_current_tenant error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


# ============================================================================
# USER & TEAM MANAGEMENT
# ============================================================================

@api_bp.route("/users", methods=["GET"])
def list_tenant_users():
    """GET /api/v1/users -- List users in the current tenant."""
    try:
        _, _, list_users, _, _ = _import_tenant_manager()
        users = list_users(g.tenant_id)
        return jsonify({"users": users, "total": len(users)})
    except Exception as exc:
        logger.error("list_tenant_users error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/users", methods=["POST"])
def create_user():
    """POST /api/v1/users -- Add a user to the current tenant."""
    role_err = _require_role("tenant_admin")
    if role_err:
        return role_err

    try:
        _, _, _, add_user, _ = _import_tenant_manager()
        data = request.get_json(force=True, silent=True) or {}
        email = data.get("email", "").strip()
        if not email:
            return _error("email is required")

        role = data.get("role", "developer")
        display_name = data.get("display_name", email.split("@")[0])
        auth_method = data.get("auth_method", "api_key")

        user = add_user(
            tenant_id=g.tenant_id,
            email=email,
            display_name=display_name,
            role=role,
            auth_method=auth_method,
        )
        return jsonify({"user": user}), 201
    except ValueError as exc:
        return _error(str(exc), status=400)
    except Exception as exc:
        logger.error("create_user error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/users/<user_id>", methods=["PATCH"])
def update_user(user_id):
    """PATCH /api/v1/users/<user_id> -- Update a user's role or status."""
    role_err = _require_role("tenant_admin")
    if role_err:
        return role_err

    try:
        data = request.get_json(force=True, silent=True) or {}
        conn = _platform_conn()
        now = _utcnow()

        # Build SET clause from allowed fields
        allowed = {"role", "display_name", "auth_method"}
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            conn.close()
            return _error("No valid fields to update")

        set_parts = ["{} = ?".format(k) for k in updates]
        set_parts.append("updated_at = ?")
        values = list(updates.values()) + [now, user_id, g.tenant_id]

        conn.execute(
            "UPDATE users SET {} WHERE id = ? AND tenant_id = ?".format(
                ", ".join(set_parts)),
            values,
        )
        conn.commit()

        row = conn.execute(
            """SELECT id, email, display_name, role, auth_method,
                      status, created_at
               FROM users WHERE id = ? AND tenant_id = ?""",
            (user_id, g.tenant_id),
        ).fetchone()
        conn.close()

        if not row:
            return _error("User not found", code="NOT_FOUND", status=404)

        return jsonify({"user": dict(row)})
    except Exception as exc:
        logger.error("update_user error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/users/<user_id>", methods=["DELETE"])
def delete_user(user_id):
    """DELETE /api/v1/users/<user_id> -- Deactivate a user."""
    role_err = _require_role("tenant_admin")
    if role_err:
        return role_err

    try:
        _, _, _, _, remove_user = _import_tenant_manager()
        result = remove_user(g.tenant_id, user_id)
        return jsonify({"result": result})
    except ValueError as exc:
        return _error(str(exc), status=404)
    except Exception as exc:
        logger.error("delete_user error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


# ============================================================================
# LLM PROVIDER KEY MANAGEMENT (Phase 32 — D141)
# ============================================================================

@api_bp.route("/llm-keys", methods=["POST"])
def create_llm_key():
    """POST /api/v1/llm-keys -- Store a new LLM provider key."""
    role_err = _require_role("tenant_admin")
    if role_err:
        return role_err

    data = request.get_json(force=True, silent=True) or {}
    provider = data.get("provider", "").strip().lower()
    api_key_value = data.get("api_key", "").strip()
    key_label = data.get("key_label", "").strip()

    if not provider:
        return _error("provider is required")
    if not api_key_value:
        return _error("api_key is required")

    try:
        from tools.saas.tenant_llm_keys import store_tenant_llm_key
        result = store_tenant_llm_key(
            tenant_id=g.tenant_id,
            provider=provider,
            plaintext_key=api_key_value,
            key_label=key_label or provider,
            created_by=getattr(g, "user_id", None),
        )
        return jsonify({"llm_key": result}), 201
    except ImportError as exc:
        logger.error("create_llm_key import error: %s", exc)
        return _error(
            "LLM key management is not available. Missing dependency: {}".format(exc),
            code="SERVICE_UNAVAILABLE", status=503,
        )
    except RuntimeError as exc:
        logger.error("create_llm_key runtime error: %s", exc)
        return _error(str(exc), code="SERVICE_UNAVAILABLE", status=503)
    except ValueError as exc:
        return _error(str(exc), status=400)
    except Exception as exc:
        logger.error("create_llm_key error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/llm-keys", methods=["GET"])
def list_llm_keys_route():
    """GET /api/v1/llm-keys -- List LLM provider keys (redacted)."""
    try:
        from tools.saas.tenant_llm_keys import list_tenant_llm_keys
        keys = list_tenant_llm_keys(g.tenant_id)
        return jsonify({"llm_keys": keys, "total": len(keys)})
    except ImportError as exc:
        logger.error("list_llm_keys import error: %s", exc)
        return _error(
            "LLM key management is not available. Missing dependency: {}".format(exc),
            code="SERVICE_UNAVAILABLE", status=503,
        )
    except Exception as exc:
        logger.error("list_llm_keys error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/llm-keys/<key_id>", methods=["DELETE"])
def revoke_llm_key_route(key_id):
    """DELETE /api/v1/llm-keys/<key_id> -- Revoke an LLM provider key."""
    role_err = _require_role("tenant_admin")
    if role_err:
        return role_err

    try:
        from tools.saas.tenant_llm_keys import revoke_tenant_llm_key
        success = revoke_tenant_llm_key(g.tenant_id, key_id)
        if not success:
            return _error("LLM key not found", code="NOT_FOUND", status=404)
        return jsonify({"result": {"id": key_id, "status": "revoked"}})
    except ImportError as exc:
        logger.error("revoke_llm_key import error: %s", exc)
        return _error(
            "LLM key management is not available. Missing dependency: {}".format(exc),
            code="SERVICE_UNAVAILABLE", status=503,
        )
    except Exception as exc:
        logger.error("revoke_llm_key error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


# ============================================================================
# API KEY MANAGEMENT
# ============================================================================

@api_bp.route("/keys", methods=["GET"])
def list_api_keys():
    """GET /api/v1/keys -- List API keys for the current user/tenant."""
    try:
        conn = _platform_conn()
        # tenant_admin sees all keys; others see only their own
        if g.user_role == "tenant_admin":
            rows = conn.execute(
                """SELECT id, tenant_id, user_id, key_prefix, name,
                          status, created_at, expires_at
                   FROM api_keys WHERE tenant_id = ?
                   ORDER BY created_at DESC""",
                (g.tenant_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, tenant_id, user_id, key_prefix, name,
                          status, created_at, expires_at
                   FROM api_keys WHERE tenant_id = ? AND user_id = ?
                   ORDER BY created_at DESC""",
                (g.tenant_id, g.user_id),
            ).fetchall()
        conn.close()
        keys = [dict(r) for r in rows]
        return jsonify({"keys": keys, "total": len(keys)})
    except Exception as exc:
        logger.error("list_api_keys error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/keys", methods=["POST"])
def create_api_key():
    """POST /api/v1/keys -- Generate a new API key."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        name = data.get("name", "api-key")
        data.get("scopes", [])

        random_hex = secrets.token_hex(16)
        full_key = "icdev_" + random_hex
        prefix = random_hex[:8]
        key_hash = hashlib.sha256(full_key.encode("utf-8")).hexdigest()
        key_id = "key-" + uuid.uuid4().hex[:12]
        now = _utcnow()

        conn = _platform_conn()
        conn.execute(
            """INSERT INTO api_keys
               (id, tenant_id, user_id, key_hash, key_prefix, name,
                status, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (key_id, g.tenant_id, g.user_id, key_hash, prefix, name,
             now, None),
        )
        conn.commit()
        conn.close()

        return jsonify({
            "key": {
                "id": key_id,
                "key": full_key,
                "prefix": prefix,
                "name": name,
                "created_at": now,
                "note": "Save this key now. It cannot be retrieved later.",
            }
        }), 201
    except Exception as exc:
        logger.error("create_api_key error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/keys/<key_id>", methods=["DELETE"])
def revoke_api_key(key_id):
    """DELETE /api/v1/keys/<key_id> -- Revoke an API key."""
    try:
        conn = _platform_conn()
        _utcnow()

        # Verify ownership (admin can revoke any, others only their own)
        row = conn.execute(
            "SELECT id, user_id FROM api_keys WHERE id = ? AND tenant_id = ?",
            (key_id, g.tenant_id),
        ).fetchone()

        if not row:
            conn.close()
            return _error("API key not found", code="NOT_FOUND", status=404)

        if g.user_role != "tenant_admin" and row["user_id"] != g.user_id:
            conn.close()
            return _error("Cannot revoke another user's key",
                          code="FORBIDDEN", status=403)

        conn.execute(
            "UPDATE api_keys SET status = 'revoked' WHERE id = ?",
            (key_id,),
        )
        conn.commit()
        conn.close()

        return jsonify({"result": {"id": key_id, "status": "revoked"}})
    except Exception as exc:
        logger.error("revoke_api_key error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


# ============================================================================
# PROJECTS (delegates to existing tools)
# ============================================================================

@api_bp.route("/projects", methods=["POST"])
def create_project():
    """POST /api/v1/projects -- Create a new project on the tenant DB."""
    try:
        call_tool, get_db_path, _ = _import_tenant_db()
        from tools.project.project_create import create_project as tool_create

        data = request.get_json(force=True, silent=True) or {}
        name = data.get("name", "").strip()
        if not name:
            return _error("name is required")

        result = call_tool(
            tool_create,
            g.tenant_id,
            name=name,
            project_type=data.get("type", "webapp"),
            classification=data.get("classification", "CUI"),
            description=data.get("description", ""),
            tech_backend=data.get("tech_backend", ""),
            tech_frontend=data.get("tech_frontend", ""),
            tech_database=data.get("tech_database", ""),
            impact_level=data.get("impact_level", "IL4"),
        )
        return jsonify({"project": result}), 201
    except ValueError as exc:
        return _error(str(exc), status=400)
    except Exception as exc:
        logger.error("create_project error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/projects", methods=["GET"])
def list_projects():
    """GET /api/v1/projects -- List projects on the tenant DB."""
    try:
        call_tool, _, _ = _import_tenant_db()
        from tools.project.project_list import list_projects as tool_list

        status_filter = request.args.get("status")
        result = call_tool(
            tool_list,
            g.tenant_id,
            status_filter=status_filter,
            output_format="detailed",
        )
        return jsonify(result)
    except Exception as exc:
        logger.error("list_projects error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/projects/<project_id>", methods=["GET"])
def get_project_status(project_id):
    """GET /api/v1/projects/<id> -- Get project status from tenant DB."""
    try:
        call_tool, _, verify = _import_tenant_db()
        if not verify(g.tenant_id, project_id):
            return _error("Project not found", code="NOT_FOUND", status=404)

        from tools.project.project_status import get_project_status as tool_status
        result = call_tool(tool_status, g.tenant_id, project_id=project_id)
        return jsonify({"project": result})
    except ValueError as exc:
        return _error(str(exc), code="NOT_FOUND", status=404)
    except Exception as exc:
        logger.error("get_project_status error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


# ============================================================================
# COMPLIANCE (delegates to existing tools)
# ============================================================================

@api_bp.route("/projects/<project_id>/ssp", methods=["POST"])
def generate_ssp(project_id):
    """POST /api/v1/projects/<id>/ssp -- Generate System Security Plan."""
    try:
        call_tool, _, verify = _import_tenant_db()
        if not verify(g.tenant_id, project_id):
            return _error("Project not found", code="NOT_FOUND", status=404)

        from tools.compliance.ssp_generator import generate_ssp as tool_ssp
        result = call_tool(tool_ssp, g.tenant_id, project_id=project_id)
        return jsonify({"ssp": result}), 201
    except ValueError as exc:
        return _error(str(exc), status=400)
    except Exception as exc:
        logger.error("generate_ssp error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/projects/<project_id>/poam", methods=["POST"])
def generate_poam(project_id):
    """POST /api/v1/projects/<id>/poam -- Generate Plan of Action & Milestones."""
    try:
        call_tool, _, verify = _import_tenant_db()
        if not verify(g.tenant_id, project_id):
            return _error("Project not found", code="NOT_FOUND", status=404)

        from tools.compliance.poam_generator import generate_poam as tool_poam
        result = call_tool(tool_poam, g.tenant_id, project_id=project_id)
        return jsonify({"poam": result}), 201
    except ValueError as exc:
        return _error(str(exc), status=400)
    except Exception as exc:
        logger.error("generate_poam error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/projects/<project_id>/stig", methods=["POST"])
def run_stig_check(project_id):
    """POST /api/v1/projects/<id>/stig -- Run STIG compliance check."""
    try:
        call_tool, _, verify = _import_tenant_db()
        if not verify(g.tenant_id, project_id):
            return _error("Project not found", code="NOT_FOUND", status=404)

        from tools.compliance.stig_checker import run_stig_check as tool_stig
        data = request.get_json(force=True, silent=True) or {}
        project_dir = data.get("project_dir", "")

        result = call_tool(
            tool_stig, g.tenant_id,
            project_id=project_id,
            project_dir=project_dir if project_dir else None,
        )
        return jsonify({"stig": result}), 201
    except ValueError as exc:
        return _error(str(exc), status=400)
    except Exception as exc:
        logger.error("run_stig_check error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/projects/<project_id>/sbom", methods=["POST"])
def generate_sbom(project_id):
    """POST /api/v1/projects/<id>/sbom -- Generate Software Bill of Materials."""
    try:
        call_tool, _, verify = _import_tenant_db()
        if not verify(g.tenant_id, project_id):
            return _error("Project not found", code="NOT_FOUND", status=404)

        from tools.compliance.sbom_generator import generate_sbom as tool_sbom
        data = request.get_json(force=True, silent=True) or {}
        project_dir = data.get("project_dir", "")

        result = call_tool(
            tool_sbom, g.tenant_id,
            project_id=project_id,
            project_dir=project_dir if project_dir else None,
        )
        return jsonify({"sbom": result}), 201
    except ValueError as exc:
        return _error(str(exc), status=400)
    except Exception as exc:
        logger.error("generate_sbom error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/projects/<project_id>/fips199", methods=["POST"])
def run_fips199(project_id):
    """POST /api/v1/projects/<id>/fips199 -- Run FIPS 199 categorization."""
    try:
        call_tool, _, verify = _import_tenant_db()
        if not verify(g.tenant_id, project_id):
            return _error("Project not found", code="NOT_FOUND", status=404)

        from tools.compliance.fips199_categorizer import categorize_project
        result = call_tool(
            categorize_project, g.tenant_id,
            project_id=project_id,
        )
        return jsonify({"fips199": result}), 201
    except ValueError as exc:
        return _error(str(exc), status=400)
    except Exception as exc:
        logger.error("run_fips199 error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/projects/<project_id>/fips200", methods=["POST"])
def run_fips200(project_id):
    """POST /api/v1/projects/<id>/fips200 -- Run FIPS 200 validation."""
    try:
        call_tool, _, verify = _import_tenant_db()
        if not verify(g.tenant_id, project_id):
            return _error("Project not found", code="NOT_FOUND", status=404)

        from tools.compliance.fips200_validator import validate_fips200
        data = request.get_json(force=True, silent=True) or {}
        project_dir = data.get("project_dir")

        result = call_tool(
            validate_fips200, g.tenant_id,
            project_id=project_id,
            project_dir=project_dir,
        )
        return jsonify({"fips200": result}), 201
    except ValueError as exc:
        return _error(str(exc), status=400)
    except Exception as exc:
        logger.error("run_fips200 error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


# ============================================================================
# CMMC ASSESSMENT (Phase 4 — Enhancement #8)
# ============================================================================

@api_bp.route("/projects/<project_id>/cmmc", methods=["POST"])
def run_cmmc_assessment(project_id):
    """POST /api/v1/projects/<id>/cmmc -- Run CMMC assessment."""
    try:
        call_tool, _, verify = _import_tenant_db()
        if not verify(g.tenant_id, project_id):
            return _error("Project not found", code="NOT_FOUND", status=404)

        data = request.get_json(force=True, silent=True) or {}
        level = data.get("level", 2)

        from tools.compliance.cmmc_assessor import assess_cmmc
        result = call_tool(
            assess_cmmc, g.tenant_id,
            project_id=project_id,
            level=level,
        )
        return jsonify({"cmmc": result}), 201
    except ImportError as exc:
        logger.error("run_cmmc_assessment import error: %s", exc)
        return _error(
            "CMMC assessor not available: {}".format(exc),
            code="SERVICE_UNAVAILABLE", status=503,
        )
    except ValueError as exc:
        return _error(str(exc), status=400)
    except Exception as exc:
        logger.error("run_cmmc_assessment error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


# ============================================================================
# OSCAL ECOSYSTEM (D302-D306)
# ============================================================================

@api_bp.route("/oscal/detect", methods=["GET"])
def oscal_detect_tools():
    """GET /api/v1/oscal/detect -- Detect available OSCAL ecosystem tools."""
    try:
        from tools.compliance.oscal_tools import detect_oscal_tools
        result = detect_oscal_tools()
        return jsonify(result)
    except ImportError as exc:
        return _error(
            "OSCAL tools not available: {}".format(exc),
            code="SERVICE_UNAVAILABLE", status=503,
        )
    except Exception as exc:
        logger.error("oscal_detect error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/oscal/catalog/stats", methods=["GET"])
def oscal_catalog_stats():
    """GET /api/v1/oscal/catalog/stats -- Get OSCAL catalog statistics."""
    try:
        from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter
        adapter = OscalCatalogAdapter()
        stats = adapter.get_catalog_stats()
        return jsonify(stats)
    except ImportError as exc:
        return _error(
            "OSCAL catalog adapter not available: {}".format(exc),
            code="SERVICE_UNAVAILABLE", status=503,
        )
    except Exception as exc:
        logger.error("oscal_catalog_stats error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/oscal/catalog/<control_id>", methods=["GET"])
def oscal_catalog_lookup(control_id):
    """GET /api/v1/oscal/catalog/<id> -- Look up a control from OSCAL catalog."""
    try:
        from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter
        adapter = OscalCatalogAdapter()
        control = adapter.get_control(control_id)
        if not control:
            return _error("Control {} not found".format(control_id), code="NOT_FOUND", status=404)
        return jsonify({"control": control})
    except ImportError as exc:
        return _error(
            "OSCAL catalog adapter not available: {}".format(exc),
            code="SERVICE_UNAVAILABLE", status=503,
        )
    except Exception as exc:
        logger.error("oscal_catalog_lookup error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/projects/<project_id>/oscal/validate", methods=["POST"])
def oscal_validate(project_id):
    """POST /api/v1/projects/<id>/oscal/validate -- Deep-validate OSCAL file."""
    try:
        call_tool, _, verify = _import_tenant_db()
        if not verify(g.tenant_id, project_id):
            return _error("Project not found", code="NOT_FOUND", status=404)

        data = request.get_json(force=True, silent=True) or {}
        file_path = data.get("file_path")
        if not file_path:
            return _error("file_path required", status=400)

        from tools.compliance.oscal_tools import validate_oscal_deep
        result = call_tool(
            validate_oscal_deep, g.tenant_id,
            file_path=file_path,
        )
        return jsonify({"oscal_validation": result}), 200
    except ImportError as exc:
        return _error(
            "OSCAL tools not available: {}".format(exc),
            code="SERVICE_UNAVAILABLE", status=503,
        )
    except ValueError as exc:
        return _error(str(exc), status=400)
    except Exception as exc:
        logger.error("oscal_validate error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/projects/<project_id>/oscal/convert", methods=["POST"])
def oscal_convert(project_id):
    """POST /api/v1/projects/<id>/oscal/convert -- Convert OSCAL format."""
    try:
        call_tool, _, verify = _import_tenant_db()
        if not verify(g.tenant_id, project_id):
            return _error("Project not found", code="NOT_FOUND", status=404)

        data = request.get_json(force=True, silent=True) or {}
        input_path = data.get("input_path")
        output_format = data.get("output_format", "xml")
        if not input_path:
            return _error("input_path required", status=400)

        from tools.compliance.oscal_tools import convert_oscal_format
        result = call_tool(
            convert_oscal_format, g.tenant_id,
            input_path=input_path,
            output_format=output_format,
        )
        return jsonify({"oscal_conversion": result}), 200
    except ImportError as exc:
        return _error(
            "OSCAL tools not available: {}".format(exc),
            code="SERVICE_UNAVAILABLE", status=503,
        )
    except ValueError as exc:
        return _error(str(exc), status=400)
    except Exception as exc:
        logger.error("oscal_convert error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


# ============================================================================
# PRODUCTION AUDIT & REMEDIATION (D291-D300)
# ============================================================================

@api_bp.route("/audit/latest", methods=["GET"])
def audit_latest():
    """GET /api/v1/audit/latest -- Get most recent production audit."""
    try:
        import json as _json
        call_tool, get_conn, _ = _import_tenant_db()
        conn = get_conn(g.tenant_id)
        row = conn.execute(
            "SELECT * FROM production_audits ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row:
            return _error("No audits found", code="NOT_FOUND", status=404)
        result = dict(row)
        for field in ("blockers", "warnings", "report_json"):
            if result.get(field) and isinstance(result[field], str):
                try:
                    result[field] = _json.loads(result[field])
                except (_json.JSONDecodeError, TypeError):
                    pass
        return jsonify(result)
    except Exception as exc:
        logger.error("audit_latest error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/audit/history", methods=["GET"])
def audit_history():
    """GET /api/v1/audit/history -- Get production audit history."""
    try:
        call_tool, get_conn, _ = _import_tenant_db()
        conn = get_conn(g.tenant_id)
        limit = min(int(request.args.get("limit", 20)), 100)
        offset = int(request.args.get("offset", 0))
        rows = conn.execute(
            "SELECT id, overall_pass, total_checks, passed, failed, warned, skipped, "
            "categories_run, duration_ms, created_at FROM production_audits "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM production_audits").fetchone()[0]
        conn.close()
        return jsonify({"audits": [dict(r) for r in rows], "total": total})
    except Exception as exc:
        logger.error("audit_history error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/audit/run", methods=["POST"])
def audit_run():
    """POST /api/v1/audit/run -- Trigger production audit."""
    import subprocess as _sp
    import sys as _sys
    data = request.get_json(force=True, silent=True) or {}
    categories = data.get("categories")

    _base = Path(__file__).resolve().parent.parent.parent
    cmd = [_sys.executable, str(_base / "tools" / "testing" / "production_audit.py"), "--json"]
    if categories:
        cmd.extend(["--category", categories])
    try:
        proc = _sp.run(cmd, capture_output=True, text=True, timeout=300, stdin=_sp.DEVNULL, cwd=str(_base))
        import json as _json
        try:
            result = _json.loads(proc.stdout)
        except (_json.JSONDecodeError, TypeError):
            result = {"stdout": proc.stdout, "stderr": proc.stderr, "returncode": proc.returncode}
        return jsonify(result)
    except _sp.TimeoutExpired:
        return _error("Audit timed out (300s)", code="TIMEOUT", status=504)
    except Exception as exc:
        logger.error("audit_run error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/audit/remediate", methods=["POST"])
def audit_remediate():
    """POST /api/v1/audit/remediate -- Trigger production remediation."""
    import subprocess as _sp
    import sys as _sys
    data = request.get_json(force=True, silent=True) or {}
    dry_run = data.get("dry_run", False)

    _base = Path(__file__).resolve().parent.parent.parent
    cmd = [_sys.executable, str(_base / "tools" / "testing" / "production_remediate.py"), "--json"]
    if dry_run:
        cmd.append("--dry-run")
    else:
        cmd.append("--auto")
    try:
        proc = _sp.run(cmd, capture_output=True, text=True, timeout=300, stdin=_sp.DEVNULL, cwd=str(_base))
        import json as _json
        try:
            result = _json.loads(proc.stdout)
        except (_json.JSONDecodeError, TypeError):
            result = {"stdout": proc.stdout, "stderr": proc.stderr, "returncode": proc.returncode}
        return jsonify(result)
    except _sp.TimeoutExpired:
        return _error("Remediation timed out (300s)", code="TIMEOUT", status=504)
    except Exception as exc:
        logger.error("audit_remediate error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


# ============================================================================
# SECURITY SCANNING
# ============================================================================

@api_bp.route("/projects/<project_id>/scan/sast", methods=["POST"])
def run_sast_scan(project_id):
    """POST /api/v1/projects/<id>/scan/sast -- Run SAST security scan."""
    try:
        _, _, verify = _import_tenant_db()
        if not verify(g.tenant_id, project_id):
            return _error("Project not found", code="NOT_FOUND", status=404)

        from tools.security.sast_runner import run_sast
        data = request.get_json(force=True, silent=True) or {}
        project_dir = data.get("project_dir", "")
        if not project_dir:
            return _error("project_dir is required in request body")

        result = run_sast(project_dir=project_dir)
        return jsonify({"sast": result}), 201
    except ValueError as exc:
        return _error(str(exc), status=400)
    except Exception as exc:
        logger.error("run_sast_scan error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/projects/<project_id>/scan/deps", methods=["POST"])
def run_dep_scan(project_id):
    """POST /api/v1/projects/<id>/scan/deps -- Run dependency audit."""
    try:
        _, _, verify = _import_tenant_db()
        if not verify(g.tenant_id, project_id):
            return _error("Project not found", code="NOT_FOUND", status=404)

        from tools.security.dependency_auditor import audit_python
        data = request.get_json(force=True, silent=True) or {}
        project_dir = data.get("project_dir", "")
        if not project_dir:
            return _error("project_dir is required in request body")

        result = audit_python(project_dir=project_dir)
        return jsonify({"dependency_audit": result}), 201
    except ValueError as exc:
        return _error(str(exc), status=400)
    except Exception as exc:
        logger.error("run_dep_scan error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


# ============================================================================
# AUDIT TRAIL
# ============================================================================

@api_bp.route("/projects/<project_id>/audit", methods=["GET"])
def get_project_audit(project_id):
    """GET /api/v1/projects/<id>/audit -- Get audit trail for a project."""
    try:
        _, _, verify = _import_tenant_db()
        if not verify(g.tenant_id, project_id):
            return _error("Project not found", code="NOT_FOUND", status=404)

        from tools.saas.tenant_db_adapter import get_tenant_db_connection
        conn = get_tenant_db_connection(g.tenant_id)

        limit = request.args.get("limit", 100, type=int)
        offset = request.args.get("offset", 0, type=int)

        rows = conn.execute(
            """SELECT id, project_id, event_type, actor, action,
                      details, classification, created_at
               FROM audit_trail
               WHERE project_id = ?
               ORDER BY created_at DESC
               LIMIT ? OFFSET ?""",
            (project_id, min(limit, 500), offset),
        ).fetchall()

        total_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM audit_trail WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        conn.close()

        events = [dict(r) for r in rows]
        total = total_row["cnt"] if total_row else 0

        return jsonify({
            "audit": events,
            "total": total,
            "limit": min(limit, 500),
            "offset": offset,
        })
    except Exception as exc:
        logger.error("get_project_audit error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


# ============================================================================
# USAGE & BILLING
# ============================================================================

@api_bp.route("/usage", methods=["GET"])
def get_usage():
    """GET /api/v1/usage -- Get usage records for the current tenant.

    Query parameters:
        period: Filter window — ``7d``, ``30d``, or ``90d``.  If omitted,
                all records are included (no time filter).
        limit:  Maximum number of recent records to return (default 100,
                capped at 500).
    """
    try:
        conn = _platform_conn()
        period = request.args.get("period", "").strip()
        limit = request.args.get("limit", 100, type=int)

        # Validate and resolve period to a cutoff timestamp
        period_map = {"7d": 7, "30d": 30, "90d": 90}
        cutoff = None
        if period:
            days = period_map.get(period)
            if days is None:
                conn.close()
                return _error(
                    "Invalid period '{}'. Valid values: 7d, 30d, 90d".format(period),
                    code="BAD_REQUEST",
                    status=400,
                )
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=days)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Build WHERE clause parts
        where_parts = ["tenant_id = ?"]
        params_base = [g.tenant_id]
        if cutoff:
            where_parts.append("recorded_at >= ?")
            params_base.append(cutoff)
        where_clause = " AND ".join(where_parts)

        # Summary statistics
        summary_row = conn.execute(
            """SELECT COUNT(*) as total_calls,
                      COALESCE(SUM(tokens_used), 0) as total_tokens,
                      COALESCE(AVG(duration_ms), 0) as avg_duration_ms
               FROM usage_records
               WHERE {}""".format(where_clause),
            params_base,
        ).fetchone()

        # Top endpoints
        top_endpoints = conn.execute(
            """SELECT endpoint, COUNT(*) as call_count,
                      COALESCE(AVG(duration_ms), 0) as avg_ms
               FROM usage_records
               WHERE {}
               GROUP BY endpoint
               ORDER BY call_count DESC
               LIMIT 10""".format(where_clause),
            params_base,
        ).fetchall()

        # Recent records
        recent = conn.execute(
            """SELECT endpoint, method, status_code, duration_ms,
                      tokens_used, recorded_at
               FROM usage_records
               WHERE {}
               ORDER BY recorded_at DESC
               LIMIT ?""".format(where_clause),
            params_base + [min(limit, 500)],
        ).fetchall()

        conn.close()

        return jsonify({
            "usage": {
                "tenant_id": g.tenant_id,
                "period": period or "all",
                "summary": {
                    "total_api_calls": summary_row["total_calls"] if summary_row else 0,
                    "total_tokens": summary_row["total_tokens"] if summary_row else 0,
                    "avg_duration_ms": round(
                        summary_row["avg_duration_ms"], 1) if summary_row else 0,
                },
                "top_endpoints": [dict(r) for r in top_endpoints],
                "recent": [dict(r) for r in recent],
            }
        })
    except Exception as exc:
        logger.error("get_usage error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


# ============================================================================
# DEVSECOPS PROFILE (Phase 24)
# ============================================================================

@api_bp.route("/projects/<project_id>/devsecops", methods=["GET"])
def get_devsecops_profile(project_id):
    """GET /api/v1/projects/<id>/devsecops -- Get DevSecOps profile."""
    try:
        _, _, verify = _import_tenant_db()
        if not verify(g.tenant_id, project_id):
            return _error("Project not found", code="NOT_FOUND", status=404)

        call_tool, _, _ = _import_tenant_db()
        from tools.devsecops.profile_manager import get_profile
        result = call_tool(get_profile, g.tenant_id, project_id=project_id)
        return jsonify({"devsecops": result})
    except ImportError as exc:
        logger.error("get_devsecops_profile import error: %s", exc)
        return _error(
            "DevSecOps profile manager not available: {}".format(exc),
            code="SERVICE_UNAVAILABLE", status=503,
        )
    except ValueError as exc:
        return _error(str(exc), code="NOT_FOUND", status=404)
    except Exception as exc:
        logger.error("get_devsecops_profile error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/projects/<project_id>/devsecops", methods=["POST"])
def create_devsecops_profile(project_id):
    """POST /api/v1/projects/<id>/devsecops -- Create DevSecOps profile."""
    role_err = _require_role("tenant_admin", "isso")
    if role_err:
        return role_err

    try:
        _, _, verify = _import_tenant_db()
        if not verify(g.tenant_id, project_id):
            return _error("Project not found", code="NOT_FOUND", status=404)

        call_tool, _, _ = _import_tenant_db()
        from tools.devsecops.profile_manager import create_profile

        data = request.get_json(force=True, silent=True) or {}
        maturity_level = data.get("maturity_level")
        stages = data.get("stages")
        stage_configs = data.get("stage_configs")

        result = call_tool(
            create_profile, g.tenant_id,
            project_id=project_id,
            maturity_level=maturity_level,
            stages=stages,
            stage_configs=stage_configs,
        )
        return jsonify({"devsecops": result}), 201
    except ImportError as exc:
        logger.error("create_devsecops_profile import error: %s", exc)
        return _error(
            "DevSecOps profile manager not available: {}".format(exc),
            code="SERVICE_UNAVAILABLE", status=503,
        )
    except ValueError as exc:
        return _error(str(exc), status=400)
    except Exception as exc:
        logger.error("create_devsecops_profile error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


# ============================================================================
# ZERO TRUST ARCHITECTURE (Phase 25)
# ============================================================================

@api_bp.route("/projects/<project_id>/zta", methods=["GET"])
def get_zta_maturity(project_id):
    """GET /api/v1/projects/<id>/zta -- Score all ZTA pillars."""
    try:
        _, _, verify = _import_tenant_db()
        if not verify(g.tenant_id, project_id):
            return _error("Project not found", code="NOT_FOUND", status=404)

        call_tool, _, _ = _import_tenant_db()
        from tools.devsecops.zta_maturity_scorer import score_all_pillars
        result = call_tool(
            score_all_pillars, g.tenant_id,
            project_id=project_id,
        )
        return jsonify({"zta": result})
    except ImportError as exc:
        logger.error("get_zta_maturity import error: %s", exc)
        return _error(
            "ZTA maturity scorer not available: {}".format(exc),
            code="SERVICE_UNAVAILABLE", status=503,
        )
    except ValueError as exc:
        return _error(str(exc), code="NOT_FOUND", status=404)
    except Exception as exc:
        logger.error("get_zta_maturity error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


# ============================================================================
# MARKETPLACE SEARCH (Phase 22)
# ============================================================================

@api_bp.route("/marketplace/search", methods=["GET"])
def search_marketplace():
    """GET /api/v1/marketplace/search -- Search marketplace assets.

    Query parameters:
        q:            Search query string (required).
        asset_type:   Filter by asset type (skill, goal, hardprompt, etc.).
        impact_level: Filter by impact level (IL2, IL4, IL5, IL6).
        limit:        Maximum results (default 50, capped at 200).
    """
    q = request.args.get("q", "").strip()
    if not q:
        return _error("Query parameter 'q' is required", code="BAD_REQUEST", status=400)

    asset_type = request.args.get("asset_type")
    impact_level = request.args.get("impact_level")
    limit = request.args.get("limit", 50, type=int)
    limit = min(max(limit, 1), 200)

    try:
        from tools.marketplace.search_engine import search_assets
        result = search_assets(
            query=q,
            asset_type=asset_type,
            impact_level=impact_level,
            tenant_id=g.tenant_id,
            limit=limit,
        )
        return jsonify({"marketplace": result})
    except ImportError as exc:
        logger.error("search_marketplace import error: %s", exc)
        return _error(
            "Marketplace search not available: {}".format(exc),
            code="SERVICE_UNAVAILABLE", status=503,
        )
    except Exception as exc:
        logger.error("search_marketplace error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


# ============================================================================
# SIMULATION SCENARIOS (RICOAS Phase 3)
# ============================================================================

@api_bp.route("/projects/<project_id>/simulations", methods=["GET"])
def list_simulations(project_id):
    """GET /api/v1/projects/<id>/simulations -- List simulation scenarios."""
    try:
        _, _, verify = _import_tenant_db()
        if not verify(g.tenant_id, project_id):
            return _error("Project not found", code="NOT_FOUND", status=404)

        call_tool, _, _ = _import_tenant_db()
        from tools.simulation.simulation_engine import list_scenarios
        result = call_tool(
            list_scenarios, g.tenant_id,
            project_id=project_id,
        )
        return jsonify({"simulations": result})
    except ImportError as exc:
        logger.error("list_simulations import error: %s", exc)
        return _error(
            "Simulation engine not available: {}".format(exc),
            code="SERVICE_UNAVAILABLE", status=503,
        )
    except ValueError as exc:
        return _error(str(exc), code="NOT_FOUND", status=404)
    except Exception as exc:
        logger.error("list_simulations error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


@api_bp.route("/projects/<project_id>/simulations", methods=["POST"])
def create_simulation(project_id):
    """POST /api/v1/projects/<id>/simulations -- Create a simulation scenario."""
    role_err = _require_role("tenant_admin", "pm", "developer")
    if role_err:
        return role_err

    try:
        _, _, verify = _import_tenant_db()
        if not verify(g.tenant_id, project_id):
            return _error("Project not found", code="NOT_FOUND", status=404)

        data = request.get_json(force=True, silent=True) or {}
        scenario_name = data.get("scenario_name", "").strip()
        if not scenario_name:
            return _error("scenario_name is required")

        scenario_type = data.get("scenario_type", "what_if")
        valid_types = ("what_if", "coa_comparison", "risk_analysis")
        if scenario_type not in valid_types:
            return _error(
                "Invalid scenario_type '{}'. Valid: {}".format(
                    scenario_type, ", ".join(valid_types)),
                code="BAD_REQUEST", status=400,
            )

        modifications = data.get("modifications", {})
        base_session_id = data.get("base_session_id")

        call_tool, _, _ = _import_tenant_db()
        from tools.simulation.simulation_engine import create_scenario
        result = call_tool(
            create_scenario, g.tenant_id,
            project_id=project_id,
            scenario_name=scenario_name,
            scenario_type=scenario_type,
            modifications=modifications,
            base_session_id=base_session_id,
        )
        return jsonify({"simulation": result}), 201
    except ImportError as exc:
        logger.error("create_simulation import error: %s", exc)
        return _error(
            "Simulation engine not available: {}".format(exc),
            code="SERVICE_UNAVAILABLE", status=503,
        )
    except ValueError as exc:
        return _error(str(exc), status=400)
    except Exception as exc:
        logger.error("create_simulation error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


# ============================================================================
# MOSA ASSESSMENT (Phase 26)
# ============================================================================

@api_bp.route("/projects/<project_id>/mosa", methods=["GET"])
def get_mosa_assessment(project_id):
    """GET /api/v1/projects/<id>/mosa -- Run MOSA assessment."""
    try:
        _, _, verify = _import_tenant_db()
        if not verify(g.tenant_id, project_id):
            return _error("Project not found", code="NOT_FOUND", status=404)

        from tools.compliance.mosa_assessor import MOSAAssessor
        assessor = MOSAAssessor()
        # Inject tenant DB path for isolation
        call_tool, _, _ = _import_tenant_db()
        result = call_tool(
            assessor.assess, g.tenant_id,
            project_id=project_id,
        )
        return jsonify({"mosa": result})
    except ImportError as exc:
        logger.error("get_mosa_assessment import error: %s", exc)
        return _error(
            "MOSA assessor not available: {}".format(exc),
            code="SERVICE_UNAVAILABLE", status=503,
        )
    except ValueError as exc:
        return _error(str(exc), code="NOT_FOUND", status=404)
    except Exception as exc:
        logger.error("get_mosa_assessment error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


# ============================================================================
# SUPPLY CHAIN DEPENDENCY GRAPH (RICOAS Phase 2)
# ============================================================================

@api_bp.route("/projects/<project_id>/supply-chain/graph", methods=["GET"])
def get_supply_chain_graph(project_id):
    """GET /api/v1/projects/<id>/supply-chain/graph -- Build dependency graph."""
    try:
        _, _, verify = _import_tenant_db()
        if not verify(g.tenant_id, project_id):
            return _error("Project not found", code="NOT_FOUND", status=404)

        call_tool, _, _ = _import_tenant_db()
        from tools.supply_chain.dependency_graph import build_graph
        result = call_tool(
            build_graph, g.tenant_id,
            project_id=project_id,
        )
        return jsonify({"supply_chain": result})
    except ImportError as exc:
        logger.error("get_supply_chain_graph import error: %s", exc)
        return _error(
            "Supply chain dependency graph not available: {}".format(exc),
            code="SERVICE_UNAVAILABLE", status=503,
        )
    except ValueError as exc:
        return _error(str(exc), code="NOT_FOUND", status=404)
    except Exception as exc:
        logger.error("get_supply_chain_graph error: %s", exc)
        return _error(str(exc), code="INTERNAL_ERROR", status=500)


# ============================================================================
# PLATFORM EVENTS (SSE)
# ============================================================================

@api_bp.route("/events", methods=["GET"])
def stream_platform_events():
    """GET /api/v1/events -- SSE stream of platform audit events.

    Returns ``text/event-stream`` that polls the ``audit_platform`` table
    and emits new events as ``data:`` lines.  The stream sends a heartbeat
    comment every 15 seconds to keep the connection alive.

    Query parameters:
        last_id: Resume from this audit event ID (default 0).
    """
    last_id = request.args.get("last_id", 0, type=int)
    # Capture tenant_id before entering the generator — the Flask request
    # context (and therefore ``g``) is not available inside the streaming
    # generator function.
    tenant_id = g.tenant_id

    def _generate():
        nonlocal last_id
        # Limit iterations to prevent infinite loops in non-browser contexts
        max_iterations = 600  # ~5 minutes at 0.5s sleep
        iteration = 0
        while iteration < max_iterations:
            iteration += 1
            try:
                conn = _platform_conn()
                rows = conn.execute(
                    """SELECT id, tenant_id, actor, action, resource_type,
                              resource_id, details, created_at
                       FROM audit_platform
                       WHERE id > ? AND (tenant_id = ? OR tenant_id IS NULL)
                       ORDER BY id ASC
                       LIMIT 50""",
                    (last_id, tenant_id),
                ).fetchall()
                conn.close()

                for row in rows:
                    event_data = dict(row)
                    last_id = event_data["id"]
                    yield "data: {}\n\n".format(
                        json_mod.dumps(event_data, default=str))

                if not rows:
                    # Heartbeat comment to keep connection alive
                    yield ": heartbeat\n\n"

            except Exception as exc:
                logger.error("stream_platform_events error: %s", exc)
                yield "event: error\ndata: {}\n\n".format(
                    json_mod.dumps({"error": str(exc)}))

            time.sleep(0.5)

    return Response(
        stream_with_context(_generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================================
# HEALTH (sub-path, in addition to gateway-level /health)
# ============================================================================

@api_bp.route("/health", methods=["GET"])
def api_health():
    """GET /api/v1/health -- API-level health check."""
    return jsonify({
        "status": "ok",
        "service": "icdev-saas-api",
        "version": "1.0.0",
        "classification": "CUI // SP-CTI",
    })
