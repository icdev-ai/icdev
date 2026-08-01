# CUI // SP-CTI
"""penta-aimc-03 — AIMC /ai-ml mutating routes require auth + operator role.

Before this fix every /ai-ml mutation route relied solely on
guard_component_access, which is fail-open (allows when there is no user/tenant)
unless ICDEV_ENFORCE_CANVAS_ACCESS is set. Each mutating route is now hard-gated
with the established dashboard @require_role decorator (401 unauthenticated,
403 wrong role), while read-only pages and GET/list JSON routes stay ungated.
"""
from __future__ import annotations

import pytest

# (method, path) for every mutating route now gated by @require_role.
MUTATING = [
    ("post", "/ai-ml/api/designs"),
    ("put", "/ai-ml/api/designs/d1"),
    ("delete", "/ai-ml/api/designs/d1"),
    ("post", "/ai-ml/api/designs/d1/assess"),
    ("post", "/ai-ml/api/designs/d1/assess-gov"),
    # penta-aimc-04 removed the design-scoped /adapt and /deploy-plan duplicates;
    # only the standalone /api/adapt/recommend and /api/deploy/plan remain.
    ("post", "/ai-ml/api/adapt/recommend"),
    ("post", "/ai-ml/api/deploy/plan"),
    ("post", "/ai-ml/api/designs/d1/artifact/model-card"),
    ("post", "/ai-ml/api/designs/d1/artifact/deploy-manifest"),
    ("post", "/ai-ml/api/templates/t1/apply/d1"),
    ("post", "/ai-ml/api/snippets/s1/insert/d1"),
    ("post", "/ai-ml/api/modernize/recommend"),
]

# Read-only routes that must NOT be turned into 401/403 by this change.
READ_ONLY = [
    "/ai-ml/api/designs",
    "/ai-ml/api/stats",
    "/ai-ml/api/models",
]


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "icdev.db"))

    # Point the AIMC canvas DB at a temp SQLite file (factory calls init_db).
    import tools.aiml_canvas.db.init_db as aimc_init
    monkeypatch.setattr(aimc_init, "_AIMC_BACKEND", "sqlite")
    monkeypatch.setattr(aimc_init, "DB_PATH", tmp_path / "aiml_canvas.db")

    from flask import Flask, g, request
    from tools.aiml_canvas.blueprint import create_aiml_blueprint

    app = Flask(__name__)
    app.secret_key = "test-secret"

    @app.before_request
    def _fake_auth():
        # Stand in for the dashboard auth middleware: a header == logged-in user;
        # absent header == anonymous (g.current_user unset).
        role = request.headers.get("X-Test-Role")
        if role:
            g.current_user = {"id": "u-test", "role": role, "tenant_id": "t-test"}

    bp = create_aiml_blueprint()
    assert bp is not None
    app.register_blueprint(bp, url_prefix="/ai-ml")
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _call(client, method, path, role=None):
    headers = {"X-Test-Role": role} if role else {}
    fn = getattr(client, method)
    if method in ("post", "put"):
        return fn(path, headers=headers, json={})
    return fn(path, headers=headers)


@pytest.mark.parametrize("method,path", MUTATING)
def test_mutating_route_requires_authentication(client, method, path):
    resp = _call(client, method, path)  # anonymous
    assert resp.status_code == 401, f"{method.upper()} {path} -> {resp.status_code}"


@pytest.mark.parametrize("method,path", MUTATING)
def test_mutating_route_rejects_wrong_role(client, method, path):
    resp = _call(client, method, path, role="cor")  # authenticated, unprivileged
    assert resp.status_code == 403, f"{method.upper()} {path} -> {resp.status_code}"


def test_adapt_recommend_passes_auth_for_admin(client):
    # Admin clears auth; the view runs and returns a recommendation (200).
    resp = _call(client, "post", "/ai-ml/api/adapt/recommend", role="admin")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.status_code not in (401, 403)


def test_deploy_plan_passes_auth_for_developer(client):
    resp = _call(client, "post", "/ai-ml/api/deploy/plan", role="developer")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.status_code not in (401, 403)


def test_create_design_passes_auth_for_pm(client):
    resp = _call(client, "post", "/ai-ml/api/designs", role="pm")
    # 201 Created past auth — proves pm role is permitted, not merely non-403.
    assert resp.status_code == 201, resp.get_data(as_text=True)


@pytest.mark.parametrize("path", READ_ONLY)
def test_read_only_routes_not_auth_gated_anonymous(client, path):
    resp = client.get(path)  # anonymous
    assert resp.status_code not in (401, 403), f"GET {path} -> {resp.status_code}"


@pytest.mark.parametrize("path", READ_ONLY)
def test_read_only_routes_render_for_logged_in_user(client, path):
    resp = client.get(path, headers={"X-Test-Role": "developer"})
    assert resp.status_code == 200, f"GET {path} -> {resp.status_code}"
