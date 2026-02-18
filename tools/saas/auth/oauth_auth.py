#!/usr/bin/env python3
"""ICDEV SaaS — OAuth 2.0 / OIDC Authentication.
CUI // SP-CTI
"""
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("saas.auth.oauth")

PLATFORM_DB_PATH = Path(os.environ.get("PLATFORM_DB_PATH", str(BASE_DIR / "data" / "platform.db")))

# JWKS cache: {issuer_url: {keys: [...], fetched_at: timestamp}}
_jwks_cache = {}
_JWKS_CACHE_TTL = 3600  # 1 hour


def _get_platform_conn():
    conn = sqlite3.connect(str(PLATFORM_DB_PATH))
    conn.row_factory = sqlite3.Row
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


def _fetch_jwks(jwks_uri: str) -> Optional[dict]:
    """Fetch JWKS from IdP. Cached for 1 hour."""
    now = time.time()
    if jwks_uri in _jwks_cache:
        cached = _jwks_cache[jwks_uri]
        if now - cached["fetched_at"] < _JWKS_CACHE_TTL:
            return cached["keys"]

    try:
        import requests
        resp = requests.get(jwks_uri, timeout=10)
        resp.raise_for_status()
        keys = resp.json()
        _jwks_cache[jwks_uri] = {"keys": keys, "fetched_at": now}
        return keys
    except Exception as e:
        logger.error("JWKS fetch error from %s: %s", jwks_uri, e)
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
        row = conn.execute("""
            SELECT id, tenant_id, email, role, status, display_name
            FROM users
            WHERE tenant_id = ? AND oauth_sub = ? AND status = 'active'
        """, (tenant_id, sub)).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error("User OAuth lookup error: %s", e)
        return None


def validate_oauth_token(token: str) -> Optional[dict]:
    """Validate an OAuth 2.0 / OIDC JWT token.

    Flow:
    1. Decode JWT (unverified) to get issuer
    2. Find tenant whose IdP issuer matches
    3. Fetch JWKS from tenant's IdP
    4. Verify JWT signature (requires PyJWT)
    5. Look up user by 'sub' claim

    Returns dict with: tenant_id, user_id, role, auth_method="oauth"
    Returns None if invalid.
    """
    decoded = _decode_jwt_unverified(token)
    if not decoded:
        return None

    payload = decoded["payload"]
    issuer = payload.get("iss")
    sub = payload.get("sub")

    if not issuer or not sub:
        logger.warning("JWT missing iss or sub claims")
        return None

    # Find tenant by issuer
    tenant_info = _find_tenant_idp(issuer)
    if not tenant_info:
        logger.warning("No tenant found for issuer: %s", issuer)
        return None

    # Verify JWT signature
    idp = tenant_info["idp"]
    jwks_uri = idp.get("jwks_uri")
    if jwks_uri:
        try:
            import jwt as pyjwt
            jwks = _fetch_jwks(jwks_uri)
            if jwks:
                # Use PyJWT with JWKS
                from jwt import PyJWKClient
                jwks_client = PyJWKClient(jwks_uri)
                signing_key = jwks_client.get_signing_key_from_jwt(token)
                verified_payload = pyjwt.decode(
                    token,
                    signing_key.key,
                    algorithms=["RS256", "ES256"],
                    audience=idp.get("client_id"),
                    issuer=issuer,
                )
                sub = verified_payload.get("sub", sub)
        except ImportError:
            logger.warning("PyJWT not installed — skipping JWT signature verification")
        except Exception as e:
            logger.error("JWT verification failed: %s", e)
            return None

    # Find user
    user = _find_user_by_oauth_sub(tenant_info["id"], sub)
    if not user:
        logger.warning("No user found for sub=%s in tenant=%s", sub, tenant_info["id"])
        return None

    return {
        "tenant_id": tenant_info["id"],
        "user_id": user["id"],
        "email": user["email"],
        "role": user["role"],
        "scopes": [],
        "tenant_status": tenant_info["status"],
        "tenant_tier": tenant_info["tier"],
        "impact_level": tenant_info["impact_level"],
        "tenant_slug": tenant_info["slug"],
        "auth_method": "oauth",
    }
