#!/usr/bin/env python3
# CUI // SP-CTI
"""Shared-IdP OIDC token passthrough for Open WebUI (owui-idp-01).

Open WebUI forwards each user's own IdP access token to ICDEV
(``auth_type: system_oauth``), so ``/v1`` and ``/mcp/v1`` see the person, not
one shared API key. That only means something if ``oauth_auth`` VERIFIES the
token: it used to skip verification when PyJWT was absent or the tenant's IdP
had no ``jwks_uri``, and then trusted the unverified payload.

Tokens here are signed with a throwaway RSA key and verified against a local
JWKS -- no network. The tenant/user lookups (PostgreSQL ``%s`` SQL) are stubbed.
"""

import sys
import time
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

jwt = pytest.importorskip("jwt")
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from flask import Flask  # noqa: E402

from tools.saas import openai_compat  # noqa: E402
from tools.saas.auth import middleware, oauth_auth, rbac  # noqa: E402

ISSUER = "http://idp.test/realms/icdev"
JWKS_URI = ISSUER + "/protocol/openid-connect/certs"
AUDIENCE = "icdev-gateway"
IDP = {
    "issuer_url": ISSUER,
    "jwks_uri": JWKS_URI,
    "audience": AUDIENCE,
    "groups_claim": "groups",
    "group_role_map": {"/icdev-admins": "tenant_admin", "icdev-developers": "developer",
                       "icdev-compliance": "compliance_officer"},
}
USERS = {"sub-alice": "viewer", "sub-bob": "viewer"}


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


KEY = _key()
OTHER_KEY = _key()


class _LocalJWKClient:
    """Stands in for PyJWKClient: answers from KEY's public half only."""

    def __init__(self, uri, **_kw):
        assert uri == JWKS_URI

    def get_signing_key_from_jwt(self, token):
        return jwt.PyJWK.from_dict({**jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True),
                                    "kid": "k1", "alg": "RS256"})


@pytest.fixture(autouse=True)
def idp(monkeypatch):
    cfg = dict(IDP)
    monkeypatch.setattr(jwt, "PyJWKClient", _LocalJWKClient)
    monkeypatch.setattr(oauth_auth, "_jwks_clients", {})
    monkeypatch.setattr(oauth_auth, "_find_tenant_idp", lambda iss: (
        {"id": "tenant-1", "slug": "acme", "impact_level": "IL4", "tier": "starter",
         "status": "active", "idp": cfg} if iss == ISSUER else None))
    monkeypatch.setattr(oauth_auth, "_find_user_by_oauth_sub", lambda tid, sub: (
        {"id": "u-" + sub, "tenant_id": tid, "email": sub + "@acme.test", "role": USERS[sub],
         "status": "active"} if sub in USERS else None))
    return cfg


def _token(sub="sub-alice", groups=("icdev-developers",), key=KEY, **over):
    claims = {"iss": ISSUER, "sub": sub, "aud": AUDIENCE, "exp": int(time.time()) + 300,
              "groups": list(groups), **over}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


# ---------------------------------------------------------------------------
# validate_oauth_token
# ---------------------------------------------------------------------------
def test_verified_token_takes_its_role_from_the_group_claim():
    info = oauth_auth.validate_oauth_token(_token(groups=["icdev-developers"]))
    assert info["role"] == "developer"
    assert info["user_id"] == "u-sub-alice" and info["auth_method"] == "oauth"


def test_most_privileged_mapped_group_wins_and_keycloak_paths_match():
    info = oauth_auth.validate_oauth_token(_token(groups=["icdev-developers", "/icdev-admins"]))
    assert info["role"] == "tenant_admin"


def test_no_mapped_group_is_refused_not_defaulted():
    assert oauth_auth.validate_oauth_token(_token(groups=["marketing"])) is None


def test_signature_from_another_key_is_refused():
    assert oauth_auth.validate_oauth_token(_token(key=OTHER_KEY)) is None


@pytest.mark.parametrize("over", [{"aud": "someone-else"}, {"exp": int(time.time()) - 60}])
def test_wrong_audience_or_expired_is_refused(over):
    assert oauth_auth.validate_oauth_token(_token(**over)) is None


def test_unsigned_token_is_refused():
    forged = jwt.encode({"iss": ISSUER, "sub": "sub-alice", "aud": AUDIENCE,
                         "exp": int(time.time()) + 300, "groups": ["/icdev-admins"]}, key=None, algorithm="none")
    assert oauth_auth.validate_oauth_token(forged) is None


def test_idp_without_jwks_uri_fails_closed(idp):
    # Previously: no jwks_uri -> signature never checked -> unverified payload trusted.
    del idp["jwks_uri"]
    assert oauth_auth.validate_oauth_token(_token(key=OTHER_KEY)) is None


def test_missing_pyjwt_fails_closed(monkeypatch):
    # Previously: ImportError -> "skipping JWT signature verification" -> accepted.
    monkeypatch.setitem(sys.modules, "jwt", None)
    assert oauth_auth.validate_oauth_token(_token()) is None


def test_without_group_map_the_provisioned_role_stands(idp):
    del idp["group_role_map"]
    assert oauth_auth.validate_oauth_token(_token(groups=["/icdev-admins"]))["role"] == "viewer"


def test_unprovisioned_subject_is_refused():
    assert oauth_auth.validate_oauth_token(_token(sub="sub-mallory")) is None


def test_v1_has_its_own_chat_category_and_mcp_keeps_compliance():
    # /v1 fell to the default "projects" category, which refused compliance officers a POST.
    assert rbac.get_endpoint_category("/v1/chat/completions") == "chat"
    assert rbac.get_endpoint_category("/mcp/v1/") == "compliance"
    assert rbac.require_permission("compliance_officer", "/v1/chat/completions", "POST")
    assert not rbac.require_permission("viewer", "/v1/chat/completions", "POST")
    assert rbac.require_permission("viewer", "/v1/models", "GET")


# ---------------------------------------------------------------------------
# Through the real auth middleware: /v1 sees the person, not a shared key
# ---------------------------------------------------------------------------
@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(middleware, "_log_auth_event", lambda *a, **k: None)
    monkeypatch.setattr(openai_compat, "_served_models", lambda cfg: openai_compat._models(cfg))
    app = Flask(__name__)
    app.config["TESTING"] = True
    middleware.register_auth_middleware(app)
    app.register_blueprint(openai_compat.openai_bp)
    return app.test_client()


def _model_ids(client, token, **headers):
    resp = client.get("/v1/models", headers={"Authorization": f"Bearer {token}", **headers})
    assert resp.status_code == 200, resp.data
    return {m["id"] for m in resp.get_json()["data"]}


def test_two_idp_users_with_different_groups_see_different_models(gateway):
    admin = _model_ids(gateway, _token(sub="sub-alice", groups=["/icdev-admins"]))
    dev = _model_ids(gateway, _token(sub="sub-bob", groups=["icdev-developers"]))
    assert "icdev/kanban" in admin           # required_roles: [tenant_admin]
    assert "icdev/kanban" not in dev
    assert "icdev/intake" in dev and "icdev/general" in dev


def test_forged_open_webui_identity_headers_grant_nothing(gateway):
    forged = {"X-OpenWebUI-User-Email": "admin@acme.test", "X-OpenWebUI-User-Role": "admin",
              "X-OpenWebUI-User-Id": "u-sub-alice"}
    assert gateway.get("/v1/models", headers=forged).status_code == 401
    # With a real developer token, the headers do not lift the role either.
    dev = _model_ids(gateway, _token(sub="sub-bob", groups=["icdev-developers"]), **forged)
    assert "icdev/kanban" not in dev


def test_compliance_officer_may_chat_and_viewer_may_only_list(gateway, monkeypatch):
    monkeypatch.setattr(openai_compat, "_router", lambda: (_ for _ in ()).throw(RuntimeError("no router")))
    body = {"model": "icdev/nope", "messages": [{"role": "user", "content": "hi"}]}
    officer = gateway.post("/v1/chat/completions", json=body,
                           headers={"Authorization": f"Bearer {_token(groups=['icdev-compliance'])}"})
    assert officer.status_code != 403           # reaches the endpoint (404: unknown model)
    USERS["sub-carol"] = "viewer"
    try:
        idp_less = dict(IDP)
        del idp_less["group_role_map"]
        monkeypatch.setattr(oauth_auth, "_find_tenant_idp", lambda iss: (
            {"id": "tenant-1", "slug": "acme", "impact_level": "IL4", "tier": "starter",
             "status": "active", "idp": idp_less}))
        viewer = gateway.post("/v1/chat/completions", json=body,
                              headers={"Authorization": f"Bearer {_token(sub='sub-carol')}"})
        assert viewer.status_code == 403
    finally:
        del USERS["sub-carol"]
