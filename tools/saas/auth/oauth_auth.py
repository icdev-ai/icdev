#!/usr/bin/env python3

"""ICDEV™ SaaS — OAuth 2.0 / OIDC Authentication.
CUI // SP-CTI
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402
from tools.db.storage import get_connection  # noqa: E402

logger = get_logger("saas.auth.oauth")

PLATFORM_DB_PATH = Path(os.environ.get("PLATFORM_DB_PATH", str(BASE_DIR / "data" / "platform.db")))

# One PyJWKClient per JWKS URI: it caches the key set and refetches on an
# unknown kid, so a key rotation at the IdP is picked up without a restart.
_jwks_clients = {}


def _get_platform_conn():
    conn = get_connection()
    return conn


def _decode_jwt_unverified(token: str) -> Optional[dict]:
    """Decode JWT header without verification to extract kid and issuer."""
    try:
        import base64

        parts = token.split(".")
        if len(parts) != 3:
            return None
        # Decode header
        header_b64 = parts[0] + "=" * (4 - len(parts[0]) % 4)
        header = json.loads(base64.urlsafe_b64decode(header_b64))
        # Decode payload
        payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return {"header": header, "payload": payload}
    except Exception as e:
        logger.error("JWT decode error: %s", e)
        return None


def _find_tenant_idp(issuer: str) -> Optional[dict]:
    """Find tenant whose IdP config matches this issuer."""
    try:
        conn = _get_platform_conn()
        rows = conn.execute("""
            SELECT id, slug, impact_level, tier, status, idp_config
            FROM tenants WHERE status = 'active'
        """).fetchall()
        conn.close()

        for row in rows:
            row = dict(row)
            if row["idp_config"]:
                try:
                    idp = json.loads(row["idp_config"]) if isinstance(row["idp_config"], str) else row["idp_config"]
                    if idp.get("issuer_url") == issuer:
                        return {**row, "idp": idp}
                except Exception:
                    continue
        return None
    except Exception as e:
        logger.error("Tenant IdP lookup error: %s", e)
        return None


def _find_user_by_oauth_sub(tenant_id: str, sub: str) -> Optional[dict]:
    """Find user by OAuth subject claim."""
    try:
        conn = _get_platform_conn()
        row = conn.execute(
            """
            SELECT id, tenant_id, email, role, status, display_name
            FROM users
            WHERE tenant_id = %s AND oauth_sub = %s AND status = 'active'
        """,
            (tenant_id, sub),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error("User OAuth lookup error: %s", e)
        return None


# Most- to least-privileged; when a user's groups map to several roles, the
# first one here wins (owui-idp-01). Matches the role columns of rbac.PERMISSIONS.
ROLE_PRECEDENCE = ("tenant_admin", "developer", "compliance_officer", "auditor", "viewer")


def role_from_groups(idp: dict, claims: dict) -> Optional[str]:
    """Map the token's group claim onto an ICDEV role via ``idp['group_role_map']``.

    Returns None when no group maps. A leading ``/`` (Keycloak's group path form)
    is ignored, so ``/icdev-developers`` and ``icdev-developers`` are one group.
    """
    mapping = {str(k).lstrip("/"): str(v) for k, v in (idp.get("group_role_map") or {}).items()}
    groups = claims.get(idp.get("groups_claim") or "groups") or []
    if isinstance(groups, str):
        groups = [groups]
    roles = {mapping[g] for g in (str(x).lstrip("/") for x in groups) if g in mapping}
    for role in ROLE_PRECEDENCE:
        if role in roles:
            return role
    return None


def _verify_jwt(token: str, idp: dict, issuer: str) -> Optional[dict]:
    """Verify signature, issuer, audience and expiry; None on ANY doubt.

    Fail closed (owui-idp-01): this used to skip verification when the tenant's
    IdP had no ``jwks_uri`` or PyJWT was not installed, and then trusted the
    UNVERIFIED payload -- any token whose ``iss`` matched a tenant got in.
    """
    jwks_uri = idp.get("jwks_uri")
    if not jwks_uri:
        logger.error("IdP for issuer %s has no jwks_uri -- refusing unverifiable JWT", issuer)
        return None
    try:
        import jwt as pyjwt
        from jwt import PyJWKClient
    except ImportError:
        logger.error("PyJWT not installed -- refusing JWT (cannot verify signature)")
        return None
    try:
        client = _jwks_clients.get(jwks_uri)
        if client is None:
            client = _jwks_clients[jwks_uri] = PyJWKClient(jwks_uri, lifespan=3600)
        signing_key = client.get_signing_key_from_jwt(token)
        return pyjwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=idp.get("audience") or idp.get("client_id"),
            issuer=issuer,
            options={"require": ["exp", "iss", "sub", "aud"]},
        )
    except Exception as e:
        logger.error("JWT verification failed: %s", e)
        return None


def validate_oauth_token(token: str) -> Optional[dict]:
    """Validate an OAuth 2.0 / OIDC JWT token.

    Flow:
    1. Decode JWT (unverified) ONLY to pick the tenant by issuer
    2. Verify signature/iss/aud/exp against that tenant's JWKS (fail closed)
    3. Look up the provisioned user by the VERIFIED 'sub' claim
    4. Role: from the verified group claim when the IdP declares
       ``group_role_map`` (no mapped group -> refused), else the user's row

    Returns dict with: tenant_id, user_id, role, auth_method="oauth"
    Returns None if invalid.
    """
    decoded = _decode_jwt_unverified(token)
    if not decoded:
        return None

    issuer = decoded["payload"].get("iss")
    if not issuer:
        logger.warning("JWT missing iss claim")
        return None

    tenant_info = _find_tenant_idp(issuer)
    if not tenant_info:
        logger.warning("No tenant found for issuer: %s", issuer)
        return None

    idp = tenant_info["idp"]
    claims = _verify_jwt(token, idp, issuer)
    if not claims:
        return None
    sub = claims["sub"]

    user = _find_user_by_oauth_sub(tenant_info["id"], sub)
    if not user:
        logger.warning("No user found for sub=%s in tenant=%s", sub, tenant_info["id"])
        return None

    role = user["role"]
    if idp.get("group_role_map"):
        role = role_from_groups(idp, claims)
        if not role:
            logger.warning("No group of sub=%s maps to an ICDEV role in tenant=%s", sub, tenant_info["id"])
            return None

    return {
        "tenant_id": tenant_info["id"],
        "user_id": user["id"],
        "email": user["email"],
        "role": role,
        "scopes": [],
        "tenant_status": tenant_info["status"],
        "tenant_tier": tenant_info["tier"],
        "impact_level": tenant_info["impact_level"],
        "tenant_slug": tenant_info["slug"],
        "auth_method": "oauth",
    }
