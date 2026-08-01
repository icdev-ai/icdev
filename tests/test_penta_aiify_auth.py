# CUI // SP-CTI
"""penta-aiify-02 — destructive/expensive AI-ify routes require auth + role.

Before this fix the mutating routes were reachable unauthenticated in default
deployments (canvas guard is fail-open unless ICDEV_ENFORCE_CANVAS_ACCESS, and
default_roles was []). Each mutating route is now hard-gated with the established
dashboard @require_role decorator (401 unauthenticated, 403 wrong role), while
read-only GET pages remain ungated so they still render for logged-in users.

Routes under test:
  DELETE /ai-ify/api/scan/all         (wipes ALL scans/opps/scores/roadmaps)
  DELETE /ai-ify/api/scan/<id>        (delete one scan)
  POST   /ai-ify/api/scan             (run a scan)
  POST   /ai-ify/api/send-to-kanban   (seed kanban tasks)
  POST   /ai-ify/api/run-innovation   (spawn innovation pipeline thread)
"""
from __future__ import annotations

import pytest

MUTATING = [
    ("delete", "/ai-ify/api/scan/all"),
    ("delete", "/ai-ify/api/scan/7"),
    ("post", "/ai-ify/api/scan"),
    ("post", "/ai-ify/api/send-to-kanban"),
    ("post", "/ai-ify/api/run-innovation"),
]


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "icdev.db"))
    monkeypatch.setenv("AIIFY_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("AIIFY_DB_PATH", str(tmp_path / "aiify_canvas.db"))

    from flask import Flask, g, request
    import tools.aiify.blueprint as bp

    # Force the blueprint's one-shot DB init to run against THIS test's temp DB.
    monkeypatch.setattr(bp, "_INIT_DONE", False)

    app = Flask(__name__)
    app.secret_key = "test-secret"

    @app.before_request
    def _fake_auth():
        # Simulate the dashboard auth middleware: a header stands in for a
        # logged-in user. Absent header == anonymous (g.current_user unset).
        role = request.headers.get("X-Test-Role")
        if role:
            g.current_user = {"id": "u-test", "role": role, "tenant_id": "t-test"}

    app.register_blueprint(bp.aiify_bp)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _call(client, method, path, role=None):
    headers = {"X-Test-Role": role} if role else {}
    fn = getattr(client, method)
    # POST routes read JSON bodies; send an empty object so parsing succeeds.
    if method == "post":
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


def test_delete_all_succeeds_for_admin(client):
    # Genuine success: past auth, deletes from the (empty) temp AI-ify DB.
    resp = _call(client, "delete", "/ai-ify/api/scan/all", role="admin")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json().get("deleted_scans") == 0


def test_scan_post_passes_auth_for_admin(client):
    # Admin clears auth; input validation (missing input_ref) then returns 400.
    resp = _call(client, "post", "/ai-ify/api/scan", role="admin")
    assert resp.status_code == 400
    assert resp.status_code not in (401, 403)


def test_send_to_kanban_passes_auth_for_admin(client):
    # Admin clears auth; missing roadmap_id then returns 400.
    resp = _call(client, "post", "/ai-ify/api/send-to-kanban", role="admin")
    assert resp.status_code == 400
    assert resp.status_code not in (401, 403)


def test_get_index_renders_for_logged_in_user(client, monkeypatch):
    # Read-only pages are NOT gated by the new decorators: the view runs and
    # reaches template rendering for a logged-in user (render mocked to avoid
    # pulling the whole dashboard base template into a minimal app).
    import tools.aiify.blueprint as bp
    monkeypatch.setattr(bp, "render_template", lambda *a, **k: "RENDERED")
    resp = client.get("/ai-ify/", headers={"X-Test-Role": "admin"})
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "RENDERED"


def test_get_index_not_auth_gated(client, monkeypatch):
    # Even anonymous GET of a read-only page must not be turned into 401/403 by
    # this change (only mutating routes are gated).
    import tools.aiify.blueprint as bp
    monkeypatch.setattr(bp, "render_template", lambda *a, **k: "RENDERED")
    resp = client.get("/ai-ify/")
    assert resp.status_code not in (401, 403)
