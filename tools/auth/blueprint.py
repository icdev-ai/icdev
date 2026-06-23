# CUI // SP-CTI
"""Flask blueprint: SAML 2.0 SSO routes for ICDEV enterprise auth."""
from __future__ import annotations

import uuid

from flask import Blueprint, Response, redirect, request, session

bp = Blueprint("auth_saml", __name__, url_prefix="/auth/saml")


@bp.route("/<provider_id>/metadata", methods=["GET"])
def sp_metadata(provider_id: str) -> Response:
    """Return SP EntityDescriptor XML for the given SAML provider."""
    from tools.auth.saml import generate_sp_metadata

    try:
        xml = generate_sp_metadata(provider_id)
    except ValueError as exc:
        return Response(str(exc), status=404)
    return Response(xml, mimetype="application/xml; charset=utf-8")


@bp.route("/<provider_id>/login", methods=["GET"])
def saml_login(provider_id: str) -> Response:
    """Initiate SAML login — redirect user to IdP with AuthnRequest."""
    from tools.auth.saml import initiate_saml_login

    relay_state = request.args.get("next", "/")
    try:
        redirect_url = initiate_saml_login(provider_id, relay_state=relay_state)
    except ValueError as exc:
        return Response(str(exc), status=404)
    return redirect(redirect_url)


@bp.route("/<provider_id>/acs", methods=["POST"])
def acs(provider_id: str) -> Response:
    """Assertion Consumer Service — receive IdP POST, create session."""
    from tools.auth.saml import process_acs_response

    saml_response = request.form.get("SAMLResponse", "")
    relay_state = request.form.get("RelayState", "/")
    try:
        result = process_acs_response(saml_response, relay_state=relay_state)
    except ValueError as exc:
        return Response(f"Invalid SAML response: {exc}", status=400)

    _persist_sso_session(provider_id, result)
    session["sso_name_id"] = result["name_id"]
    session["sso_provider_id"] = provider_id
    return redirect(relay_state or "/")


@bp.route("/oidc/<provider_id>/login", methods=["GET"])
def oidc_login(provider_id: str) -> Response:
    """Initiate OIDC login — redirect user to IdP authorization endpoint."""
    from tools.auth.oidc import get_oidc_login_url

    try:
        url, state = get_oidc_login_url(provider_id)
    except ValueError as exc:
        return Response(str(exc), status=404)
    session["oidc_state"] = state
    session["oidc_provider_id"] = provider_id
    return redirect(url)


@bp.route("/oidc/<provider_id>/callback", methods=["GET"])
def oidc_callback(provider_id: str) -> Response:
    """OIDC callback — exchange code, persist session, redirect."""
    from tools.auth.oidc import exchange_oidc_code

    code = request.args.get("code", "")
    state = request.args.get("state", "")
    if not code:
        return Response("Missing authorization code", status=400)
    try:
        user_info = exchange_oidc_code(provider_id, code=code, state=state)
    except Exception as exc:
        return Response(f"OIDC exchange failed: {exc}", status=400)
    session["sso_name_id"] = user_info.get("email") or user_info.get("sub")
    session["sso_provider_id"] = provider_id
    return redirect("/")


def _persist_sso_session(provider_id: str, result: dict) -> None:
    from tools.db import storage

    sid = str(uuid.uuid4())
    with storage.get_connection() as conn:
        conn.execute(
            "INSERT INTO sso_sessions (id, tenant_id, provider_id, name_id) "
            "VALUES (?, ?, ?, ?)",
            (sid, "default", provider_id, result.get("name_id")),
        )
        conn.commit()
