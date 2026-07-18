# CUI // SP-CTI
"""RBAC regression tests for the tenant Admin Console (kanban nav-sec-02).

Regression under test: `_require_admin()` used to only abort 403 when the env
var ICDEV_ENFORCE_CANVAS_ACCESS was truthy. On a default install (flag unset)
ANY authenticated (non-admin) user could hit the mutating Admin Console
endpoints — a P0 fail-open authorization bug.

These tests assert authorization is enforced UNCONDITIONALLY:
  * a non-admin authenticated user gets 403 on every mutating endpoint class,
  * an admin user is allowed through (never 401/403 at the auth gate),
  * with the flag explicitly UNSET (the default install), a non-admin still
    gets 403 — the actual regression guard (ABAC lesson: test the deny case).
"""

import json
import os
import sys

import pytest

# Force SQLite + enable the admin console blueprint for tests.
os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")
os.environ.setdefault("ICDEV_ADMIN_CONSOLE_ENABLED", "true")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_app(user):
    """Build a minimal Flask app with the admin blueprint and a fixed user.

    `user` is the dict assigned to g.current_user (or None for anonymous).
    """
    from flask import Flask, g

    from tools.admin.blueprint import create_admin_blueprint

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"

    bp = create_admin_blueprint()
    assert bp is not None, "Admin blueprint must be created when ICDEV_ADMIN_CONSOLE_ENABLED=true"
    app.register_blueprint(bp)

    @app.before_request
    def _set_user():
        g.current_user = user
        g.tenant_id = "test-tenant"
        g.security_context = {"classification": "CUI", "tenant_id": "test-tenant"}

    return app


# The mutating endpoint classes gated by _require_admin. Each is (method, path, body).
_MUTATING_ENDPOINTS = [
    ("POST", "/api/admin/tenants/test-tenant/components/idc", {"enabled": False}),
    ("POST", "/api/admin/tenants/test-tenant/sso-providers", {"name": "evil-idp", "protocol": "saml"}),
    ("DELETE", "/api/admin/tenants/test-tenant/sso-providers/some-provider-id", None),
    ("POST", "/api/admin/tenants/test-tenant/api-keys", {"name": "evil-key", "scopes": "admin"}),
    ("POST", "/api/admin/tenants/test-tenant/erasure", {"confirmation_token": "CONFIRM_ERASURE"}),
]


def _invoke(client, method, path, body):
    kwargs = {}
    if body is not None:
        kwargs["data"] = json.dumps(body)
        kwargs["content_type"] = "application/json"
    return client.open(path, method=method, **kwargs)


_NON_ADMIN = {"id": "u-1", "role": "user", "email": "user@test.example"}
_ADMIN = {"id": "a-1", "role": "admin", "email": "admin@test.example"}


class TestNonAdminDenied:
    """Non-admin authenticated user must be 403 on every mutating endpoint."""

    @pytest.mark.parametrize("method,path,body", _MUTATING_ENDPOINTS)
    def test_non_admin_forbidden(self, method, path, body, monkeypatch):
        # Flag truthy — must still deny non-admin.
        monkeypatch.setenv("ICDEV_ENFORCE_CANVAS_ACCESS", "true")
        client = _make_app(_NON_ADMIN).test_client()
        resp = _invoke(client, method, path, body)
        assert resp.status_code == 403, f"{method} {path} should be 403 for non-admin, got {resp.status_code}"

    @pytest.mark.parametrize("method,path,body", _MUTATING_ENDPOINTS)
    def test_non_admin_forbidden_flag_unset(self, method, path, body, monkeypatch):
        """THE REGRESSION TEST: with ICDEV_ENFORCE_CANVAS_ACCESS unset (default
        install), a non-admin must STILL be denied. Previously this was 200/allow."""
        monkeypatch.delenv("ICDEV_ENFORCE_CANVAS_ACCESS", raising=False)
        client = _make_app(_NON_ADMIN).test_client()
        resp = _invoke(client, method, path, body)
        assert resp.status_code == 403, (
            f"{method} {path} FAILED-OPEN: non-admin got {resp.status_code} with flag unset"
        )

    @pytest.mark.parametrize("method,path,body", _MUTATING_ENDPOINTS)
    def test_anonymous_forbidden_flag_unset(self, method, path, body, monkeypatch):
        """No user in g.current_user (empty role) is also denied."""
        monkeypatch.delenv("ICDEV_ENFORCE_CANVAS_ACCESS", raising=False)
        client = _make_app(None).test_client()
        resp = _invoke(client, method, path, body)
        assert resp.status_code == 403


class TestAdminAllowed:
    """Admin passes the auth gate (never 401/403). Downstream status varies with
    DB availability, so we only assert the request is not blocked by _require_admin."""

    @pytest.mark.parametrize("method,path,body", _MUTATING_ENDPOINTS)
    def test_admin_passes_auth_gate(self, method, path, body, monkeypatch):
        monkeypatch.delenv("ICDEV_ENFORCE_CANVAS_ACCESS", raising=False)
        client = _make_app(_ADMIN).test_client()
        resp = _invoke(client, method, path, body)
        assert resp.status_code not in (401, 403), (
            f"{method} {path} admin blocked at auth gate with {resp.status_code}"
        )

    def test_admin_component_override_succeeds(self, monkeypatch):
        """Positive path: admin can flip a component override (200)."""
        monkeypatch.delenv("ICDEV_ENFORCE_CANVAS_ACCESS", raising=False)
        client = _make_app(_ADMIN).test_client()
        resp = client.post(
            "/api/admin/tenants/test-tenant/components/idc",
            data=json.dumps({"enabled": False}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json().get("component_key") == "idc"

    def test_superadmin_role_allowed(self, monkeypatch):
        """Other admin roles in _ADMIN_ROLES are also allowed."""
        monkeypatch.delenv("ICDEV_ENFORCE_CANVAS_ACCESS", raising=False)
        client = _make_app({"id": "s-1", "role": "superadmin"}).test_client()
        resp = client.post(
            "/api/admin/tenants/test-tenant/components/idc",
            data=json.dumps({"enabled": True}),
            content_type="application/json",
        )
        assert resp.status_code == 200
