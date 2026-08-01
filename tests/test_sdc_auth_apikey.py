# CUI // SP-CTI
"""Presented-key API auth semantics for Security Design Canvas — shx-auth-02.

``sc_login_required`` previously auto-authenticated *every* request whenever
ICDEV_DASHBOARD_API_KEY was merely SET in the environment — a presence-only
bypass. The corrected semantics:

  1. ICDEV_AUTH_BYPASS set  -> unconditional bypass (test-only opt-in).
  2. ICDEV_DASHBOARD_API_KEY set + request presents the matching key
     (X-ICDEV-API-Key header, or Authorization: Bearer <key>) -> authenticated
     as "api-key".
  3. Key var set but request omits / mismatches the key -> 401 on API routes.
  4. Neither var set, no session -> 401.
"""
from __future__ import annotations

import pytest

_API_ROUTE = "/security/api/zig/pillars"


@pytest.fixture()
def app():
    from flask import Flask
    from tools.security_canvas.blueprint import create_security_blueprint
    from tools.security_canvas.db.init_db import init_db

    # Ensure the canvas schema exists so an authenticated request reaches a 200.
    try:
        init_db()
    except Exception:
        pass

    import os
    os.environ.setdefault("ICDEV_SECURITY_ENABLED", "true")

    flask_app = Flask(__name__, template_folder="../tools/dashboard/templates")
    flask_app.config.update(TESTING=True, SECRET_KEY="test-secret", WTF_CSRF_ENABLED=False)

    bp = create_security_blueprint()
    assert bp is not None, "create_security_blueprint() returned None"
    flask_app.register_blueprint(bp, url_prefix="/security")
    return flask_app


def test_auth_bypass_env_authenticates(app, monkeypatch):
    """Branch 1: ICDEV_AUTH_BYPASS set -> request is allowed through (200)."""
    monkeypatch.setenv("ICDEV_AUTH_BYPASS", "true")
    monkeypatch.delenv("ICDEV_DASHBOARD_API_KEY", raising=False)
    with app.test_client() as client:
        resp = client.get(_API_ROUTE)
    assert resp.status_code == 200


def test_correct_presented_key_authenticates(app, monkeypatch):
    """Branch 2: key set + correct X-ICDEV-API-Key -> 200 and session user 'api-key'."""
    monkeypatch.delenv("ICDEV_AUTH_BYPASS", raising=False)
    monkeypatch.setenv("ICDEV_DASHBOARD_API_KEY", "secret-key-123")
    with app.test_client() as client:
        resp = client.get(_API_ROUTE, headers={"X-ICDEV-API-Key": "secret-key-123"})
        assert resp.status_code == 200
        with client.session_transaction() as sess:
            assert sess.get("user_id") == "api-key"


def test_correct_bearer_key_authenticates(app, monkeypatch):
    """Branch 2 (variant): Authorization: Bearer <key> is also accepted."""
    monkeypatch.delenv("ICDEV_AUTH_BYPASS", raising=False)
    monkeypatch.setenv("ICDEV_DASHBOARD_API_KEY", "secret-key-123")
    with app.test_client() as client:
        resp = client.get(_API_ROUTE, headers={"Authorization": "Bearer secret-key-123"})
        assert resp.status_code == 200
        with client.session_transaction() as sess:
            assert sess.get("user_id") == "api-key"


def test_key_set_but_absent_from_request_is_401(app, monkeypatch):
    """Branch 3a: key set in env but request presents nothing -> 401 on API route."""
    monkeypatch.delenv("ICDEV_AUTH_BYPASS", raising=False)
    monkeypatch.setenv("ICDEV_DASHBOARD_API_KEY", "secret-key-123")
    with app.test_client() as client:
        resp = client.get(_API_ROUTE)
    assert resp.status_code == 401


def test_key_set_but_wrong_in_request_is_401(app, monkeypatch):
    """Branch 3b: key set in env but request presents the wrong key -> 401."""
    monkeypatch.delenv("ICDEV_AUTH_BYPASS", raising=False)
    monkeypatch.setenv("ICDEV_DASHBOARD_API_KEY", "secret-key-123")
    with app.test_client() as client:
        resp = client.get(_API_ROUTE, headers={"X-ICDEV-API-Key": "wrong-key"})
    assert resp.status_code == 401


def test_neither_var_set_is_401(app, monkeypatch):
    """Branch 4: no bypass, no key, no session -> 401 on API route."""
    monkeypatch.delenv("ICDEV_AUTH_BYPASS", raising=False)
    monkeypatch.delenv("ICDEV_DASHBOARD_API_KEY", raising=False)
    with app.test_client() as client:
        resp = client.get(_API_ROUTE)
    assert resp.status_code == 401
