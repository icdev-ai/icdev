# CUI // SP-CTI
"""OIDC (OpenID Connect) integration for ICDEV enterprise SSO.

Uses authlib for OAuth2/OIDC flows; wraps provider config from sso_providers.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

_BASE_URL = os.environ.get("ICDEV_BASE_URL", "http://localhost:5050").rstrip("/")

# Module-level OIDC client registry: provider_id -> authlib OAuth2Session
_CLIENTS: dict[str, Any] = {}


def _get_provider(provider_id: str, protocol: str = "oidc") -> dict:
    from tools.db import storage

    with storage.get_connection() as conn:
        row = conn.execute(
            "SELECT id, tenant_id, name, protocol, entity_id, metadata_url, "
            "client_id, client_secret_enc, claims_mapping, enabled "
            "FROM sso_providers WHERE id = %s AND protocol = %s",
            (provider_id, protocol),
        ).fetchone()
    if row is None:
        raise ValueError(f"OIDC provider not found: {provider_id!r}")
    keys = [
        "id", "tenant_id", "name", "protocol", "entity_id",
        "metadata_url", "client_id", "client_secret_enc", "claims_mapping", "enabled",
    ]
    return dict(zip(keys, tuple(row)))


def register_oidc_client(provider: dict) -> Any:
    """Build and cache an authlib OAuth2Session for the given provider dict.

    Returns the OAuth2Session instance.
    """
    from authlib.integrations.requests_client import OAuth2Session

    pid = provider["id"]
    client_id = provider.get("client_id") or ""
    client_secret = provider.get("client_secret_enc") or ""
    client = OAuth2Session(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=f"{_BASE_URL}/auth/oidc/{pid}/callback",
        scope="openid email profile",
    )
    _CLIENTS[pid] = client
    return client


def _get_or_register(provider_id: str) -> tuple[Any, dict]:
    """Return (client, provider_dict), registering if needed."""
    if provider_id not in _CLIENTS:
        provider = _get_provider(provider_id)
        register_oidc_client(provider)
    else:
        provider = _get_provider(provider_id)
    return _CLIENTS[provider_id], provider


def get_oidc_login_url(provider_id: str, redirect_uri: str | None = None) -> tuple[str, str]:
    """Return (authorization_url, state) for the OIDC login redirect.

    The caller must store `state` in the Flask session for CSRF validation.
    """
    client, provider = _get_or_register(provider_id)
    auth_endpoint = (provider.get("metadata_url") or provider.get("entity_id") or "").strip()
    if not auth_endpoint:
        raise ValueError(f"No OIDC authorization endpoint configured for {provider_id!r}")

    if redirect_uri:
        client.redirect_uri = redirect_uri

    url, state = client.create_authorization_url(auth_endpoint)
    return url, state


def exchange_oidc_code(
    provider_id: str,
    code: str,
    state: str,
    token_endpoint: str | None = None,
) -> dict[str, Any]:
    """Exchange authorization code for tokens and return user_info dict.

    `token_endpoint` defaults to provider.metadata_url if not given.
    Persists tokens in sso_sessions. Returns decoded id_token claims.
    """
    client, provider = _get_or_register(provider_id)
    tok_url = token_endpoint or (provider.get("metadata_url") or "").strip()
    if not tok_url:
        raise ValueError(f"No token endpoint configured for {provider_id!r}")

    redirect_uri = f"{_BASE_URL}/auth/oidc/{provider_id}/callback"
    token = client.fetch_token(
        tok_url,
        code=code,
        state=state,
        redirect_uri=redirect_uri,
    )

    # Decode id_token claims (without signature verification — IdP already validated)
    claims: dict[str, Any] = {}
    id_token = token.get("id_token") or token.get("access_token") or ""
    if id_token and "." in id_token:
        import base64 as _b64

        payload_part = id_token.split(".")[1]
        # Add padding
        payload_part += "=" * (-len(payload_part) % 4)
        try:
            claims = json.loads(_b64.urlsafe_b64decode(payload_part))
        except Exception:
            pass

    # Apply claims_mapping from provider config
    claims_mapping: dict[str, str] = {}
    raw_mapping = provider.get("claims_mapping") or ""
    if raw_mapping:
        try:
            claims_mapping = json.loads(raw_mapping)
        except Exception:
            pass

    user_info: dict[str, Any] = {}
    if claims_mapping:
        for icdev_field, oidc_claim in claims_mapping.items():
            if oidc_claim in claims:
                user_info[icdev_field] = claims[oidc_claim]
    else:
        # Sensible defaults
        user_info = {
            "email": claims.get("email") or claims.get("sub"),
            "name": claims.get("name"),
            "sub": claims.get("sub"),
        }

    _persist_oidc_session(provider_id, provider.get("tenant_id", "default"), token, user_info)
    return user_info


def _persist_oidc_session(
    provider_id: str,
    tenant_id: str,
    token: dict,
    user_info: dict,
) -> None:
    from tools.db import storage

    sid = str(uuid.uuid4())
    name_id = user_info.get("email") or user_info.get("sub") or ""
    id_token = token.get("id_token", "")
    with storage.get_connection() as conn:
        conn.execute(
            "INSERT INTO sso_sessions "
            "(id, tenant_id, provider_id, name_id, id_token) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, tenant_id, provider_id, name_id, id_token),
        )
        conn.commit()
