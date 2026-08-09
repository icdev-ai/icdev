#!/usr/bin/env python3
"""ICDEV™ SaaS — SAML 2.0 Authentication Routes.
CUI // SP-CTI

Flask blueprint exposing:
  GET  /saml/metadata        — SP metadata XML
  GET  /saml/login           — Initiate SP-initiated SSO (redirect to IdP)
  POST /saml/acs             — Assertion Consumer Service (handle IdP response)
  POST /saml/slo             — Single Logout Service

All routes are tenant-aware via the ``X-Tenant-ID`` header or query param.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from flask import (
    Blueprint,
    g,
    jsonify,
    make_response,
    redirect,
    request,
    session,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("saas.auth.saml_routes")

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------

saml_bp = Blueprint("saml", __name__, url_prefix="/saml")


# ---------------------------------------------------------------------------
# Tenant resolution
# ---------------------------------------------------------------------------

def _resolve_tenant_id() -> Optional[str]:
    """Resolve tenant_id from header, query param, or session."""
    tenant_id = request.headers.get("X-Tenant-ID", "").strip()
    if tenant_id:
        return tenant_id
    tenant_id = request.args.get("tenant_id", "").strip()
    if tenant_id:
        return tenant_id
    tenant_id = session.get("tenant_id", "").strip()
    if tenant_id:
        return tenant_id
    # Fallback: lookup by slug
    slug = request.args.get("tenant", "").strip()
    if slug:
        try:
            from tools.db.storage import get_connection

            conn = get_connection()
            row = conn.execute("SELECT id FROM tenants WHERE slug = %s", (slug,)).fetchone()
            conn.close()
            if row:
                return row["id"]
        except Exception:
            pass
    return None


def _get_tenant_info(tenant_id: str) -> Optional[dict]:
    """Fetch tenant info from platform DB."""
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT id, slug, name, status, impact_level, tier, idp_config FROM tenants WHERE id = %s",
            (tenant_id,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as exc:
        logger.warning("Could not fetch tenant info: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def _log_saml_event(
    event_type: str,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    details: Optional[dict] = None,
):
    """Log SAML authentication event to platform audit trail."""
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        conn.execute(
            """
            INSERT INTO audit_platform (tenant_id, user_id, event_type, action, details, ip_address, user_agent, recorded_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
            (
                tenant_id,
                user_id,
                event_type,
                event_type,
                json.dumps(details or {}),
                request.remote_addr,
                request.headers.get("User-Agent", "")[:256],
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("Could not log SAML event: %s", exc)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@saml_bp.route("/metadata", methods=["GET"])
def saml_metadata():
    """GET /saml/metadata — Return Service Provider metadata XML.

    Query params:
      tenant_id (optional) — Tenant UUID; if omitted, uses env defaults.
    """
    tenant_id = _resolve_tenant_id()
    from tools.saas.auth.saml_auth import generate_sp_metadata, resolve_saml_config

    if tenant_id:
        config = resolve_saml_config(tenant_id)
    else:
        config = resolve_saml_config("")

    metadata_xml = generate_sp_metadata(config)
    response = make_response(metadata_xml)
    response.headers["Content-Type"] = "application/samlmetadata+xml"
    return response


@saml_bp.route("/login", methods=["GET"])
def saml_login():
    """GET /saml/login — Initiate SP-initiated SSO.

    Redirects the browser to the IdP's SingleSignOnService URL with a
    SAML AuthNRequest.

    Query params:
      tenant_id (required if not in session/header)
      relay_state (optional) — opaque state string returned by IdP
      redirect (optional) — final redirect URL after successful auth
    """
    tenant_id = _resolve_tenant_id()
    if not tenant_id:
        return jsonify({"error": "tenant_id required", "code": "MISSING_TENANT"}), 400

    tenant_info = _get_tenant_info(tenant_id)
    if not tenant_info:
        return jsonify({"error": "Tenant not found", "code": "TENANT_NOT_FOUND"}), 404
    if tenant_info["status"] != "active":
        return jsonify({"error": "Tenant not active", "code": "TENANT_INACTIVE"}), 403

    relay_state = request.args.get("relay_state", "")
    redirect_url = request.args.get("redirect", "")

    # Persist redirect URL in session for post-ACS redirect
    if redirect_url:
        session["saml_redirect"] = redirect_url
    session["saml_tenant_id"] = tenant_id
    session["saml_relay_state"] = relay_state

    from tools.saas.auth.saml_auth import generate_authn_request, resolve_saml_config

    config = resolve_saml_config(tenant_id)
    idp = config.get("idp", {})
    if not idp.get("entity_id") or not idp.get("single_sign_on_service", {}).get("url"):
        _log_saml_event("saml.login.failed", tenant_id, details={"reason": "idp_not_configured"})
        return jsonify({"error": "SAML IdP not configured for this tenant", "code": "IDP_NOT_CONFIGURED"}), 400

    try:
        req = generate_authn_request(config, relay_state=relay_state)
    except Exception as exc:
        logger.error("Failed to generate SAML AuthNRequest: %s", exc)
        _log_saml_event("saml.login.failed", tenant_id, details={"reason": "authn_request_error", "error": str(exc)})
        return jsonify({"error": "Failed to initiate SAML login", "code": "AUTHN_REQUEST_ERROR"}), 500

    _log_saml_event("saml.login.initiated", tenant_id, details={"request_id": req["id"], "idp_entity_id": idp.get("entity_id", "")})
    return redirect(req["url"])


@saml_bp.route("/acs", methods=["POST"])
def saml_acs():
    """POST /saml/acs — Assertion Consumer Service.

    Receives the SAMLResponse from the IdP via HTTP-POST binding.
    Validates the response, extracts user attributes, and creates a session.

    Form fields:
      SAMLResponse (base64) — SAML assertion from IdP
      RelayState (optional) — opaque state from login request
    """
    saml_response_b64 = request.form.get("SAMLResponse", "")
    relay_state = request.form.get("RelayState", "")

    if not saml_response_b64:
        return jsonify({"error": "SAMLResponse missing", "code": "MISSING_SAML_RESPONSE"}), 400

    tenant_id = session.get("saml_tenant_id") or _resolve_tenant_id()
    if not tenant_id:
        return jsonify({"error": "tenant_id missing from session", "code": "MISSING_TENANT_SESSION"}), 400

    tenant_info = _get_tenant_info(tenant_id)
    if not tenant_info:
        return jsonify({"error": "Tenant not found", "code": "TENANT_NOT_FOUND"}), 404
    if tenant_info["status"] != "active":
        return jsonify({"error": "Tenant not active", "code": "TENANT_INACTIVE"}), 403

    from tools.saas.auth.saml_auth import validate_saml_response

    try:
        user = validate_saml_response(saml_response_b64, tenant_id, relay_state=relay_state)
    except Exception as exc:
        logger.error("SAML response validation error: %s", exc)
        _log_saml_event("saml.acs.failed", tenant_id, details={"reason": "validation_error", "error": str(exc)})
        return jsonify({"error": "SAML validation failed", "code": "SAML_VALIDATION_FAILED"}), 401

    if not user:
        _log_saml_event("saml.acs.failed", tenant_id, details={"reason": "user_not_found_or_provisioning_failed"})
        return jsonify({"error": "Authentication failed — user not recognized", "code": "USER_NOT_FOUND"}), 401

    # Update last_login
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        conn.execute(
            "UPDATE users SET last_login = %s WHERE id = %s",
            (datetime.now(timezone.utc).isoformat(), user["user_id"]),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    # Set session
    session["user_id"] = user["user_id"]
    session["tenant_id"] = user["tenant_id"]
    session["auth_method"] = "saml"
    session["saml_name_id"] = user.get("saml_name_id", "")

    # Set Flask g for immediate use
    g.tenant_id = user["tenant_id"]
    g.user_id = user["user_id"]
    g.user_role = user["role"]
    g.auth_info = user
    g.tenant_name = user.get("tenant_slug", "")

    _log_saml_event(
        "saml.acs.success",
        tenant_id,
        user["user_id"],
        details={
            "name_id": user.get("saml_name_id", "")[:20],
            "role": user["role"],
            "email": user.get("email", "")[:40],
        },
    )

    # Redirect or return JSON
    redirect_url = session.pop("saml_redirect", "")
    if redirect_url:
        return redirect(redirect_url)

    if request.is_json or request.headers.get("Accept", "").startswith("application/json"):
        return jsonify({
            "status": "ok",
            "user_id": user["user_id"],
            "email": user.get("email", ""),
            "role": user["role"],
            "tenant_id": user["tenant_id"],
            "auth_method": "saml",
        })

    # Browser fallback: redirect to dashboard home
    return redirect("/")


@saml_bp.route("/slo", methods=["POST", "GET"])
def saml_slo():
    """POST/GET /saml/slo — Single Logout Service.

    Receives a LogoutRequest from the IdP or processes a local logout.
    """
    tenant_id = session.get("tenant_id") or _resolve_tenant_id()
    user_id = session.get("user_id")

    if request.method == "POST":
        saml_request_b64 = request.form.get("SAMLRequest", "")
        if saml_request_b64:
            # Process IdP-initiated logout
            _log_saml_event("saml.slo.idp_initiated", tenant_id, user_id, details={"method": "POST"})

    # Always clear local session
    session.clear()
    _log_saml_event("saml.slo.success", tenant_id, user_id)

    redirect_url = request.args.get("redirect", "/")
    return redirect(redirect_url)


@saml_bp.route("/logout", methods=["GET", "POST"])
def saml_logout():
    """GET/POST /saml/logout — Initiate SP-initiated SLO or simple local logout."""
    tenant_id = session.get("tenant_id")
    user_id = session.get("user_id")

    session.clear()
    _log_saml_event("saml.logout", tenant_id, user_id)

    redirect_url = request.args.get("redirect", "/")
    return redirect(redirect_url)


# ---------------------------------------------------------------------------
# Health / status
# ---------------------------------------------------------------------------

@saml_bp.route("/status", methods=["GET"])
def saml_status():
    """GET /saml/status — Return SAML configuration status for tenant."""
    tenant_id = _resolve_tenant_id()
    if not tenant_id:
        return jsonify({"error": "tenant_id required", "code": "MISSING_TENANT"}), 400

    from tools.saas.auth.saml_auth import resolve_saml_config

    config = resolve_saml_config(tenant_id)
    idp = config.get("idp", {})
    sp = config.get("sp", {})

    configured = bool(idp.get("entity_id") and idp.get("single_sign_on_service", {}).get("url"))

    return jsonify({
        "configured": configured,
        "tenant_id": tenant_id,
        "sp_entity_id": sp.get("entity_id", ""),
        "acs_url": sp.get("assertion_consumer_service", {}).get("url", ""),
        "idp_entity_id": idp.get("entity_id", ""),
        "sso_url": idp.get("single_sign_on_service", {}).get("url", ""),
        "authn_requests_signed": config.get("security", {}).get("authn_requests_signed", False),
        "want_assertions_signed": config.get("security", {}).get("want_assertions_signed", True),
    })


__all__ = ["saml_bp", "saml_metadata", "saml_login", "saml_acs", "saml_slo", "saml_logout", "saml_status"]
