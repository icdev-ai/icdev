# CUI // SP-CTI
"""Route-level auth / IDOR / upload-allowlist tests for the BI Dashboard canvas.

Covers the cnr-bi-01 (owner/tenant IDOR) and cnr-bi-02 (tenant threading +
upload allowlist) fixes at the HTTP boundary — the layer the earlier
end-to-end test never exercised. Each test drives the JSON API routes through a
Flask test client with a simulated auth middleware that mirrors
tools/dashboard/auth.py::_auth_before_request (session user_id -> g.current_user).
"""
from __future__ import annotations

import importlib
import sqlite3
from io import BytesIO

import pytest

from tools.bi_dashboard.db.init_db import _SCHEMA_SQLITE

# Fake user directory: two users share tenantA, one is admin, one lives in tenantB.
_USERS = {
    "userA": {"id": "userA", "role": "developer", "tenant_id": "tenantA"},
    "userB": {"id": "userB", "role": "developer", "tenant_id": "tenantA"},
    "admin": {"id": "admin", "role": "admin", "tenant_id": "tenantA"},
    "userC": {"id": "userC", "role": "developer", "tenant_id": "tenantB"},
}


@pytest.fixture
def sqlite_conn(tmp_path, monkeypatch):
    """Isolated SQLite DB with the bi_* schema, patched into get_connection().

    Patches via importlib.import_module + setattr (not the pytest string form) —
    tools.* / icdev.tools.* are distinct module objects for from-imports.
    """
    db_path = tmp_path / "bi_routes.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    for stmt in _SCHEMA_SQLITE.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
    conn.close()

    def _get_connection(*_a, **_kw):
        from tools.db.storage import StorageConnection
        return StorageConnection(sqlite3.connect(str(db_path), check_same_thread=False), "sqlite")

    for mod_name in ("tools.db.storage", "icdev.tools.db.storage"):
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        monkeypatch.setattr(mod, "get_connection", _get_connection)
    return db_path


@pytest.fixture
def app(sqlite_conn):
    from flask import Flask, g, session

    from tools.bi_dashboard.blueprint import create_bi_dashboard_blueprint

    flask_app = Flask(__name__)
    flask_app.secret_key = "test-secret"
    bp = create_bi_dashboard_blueprint()
    assert bp is not None
    flask_app.register_blueprint(bp, url_prefix="/bi_dashboard")

    @flask_app.before_request
    def _fake_auth():  # mirrors _auth_before_request: session user_id -> g.current_user
        uid = session.get("user_id")
        g.current_user = _USERS.get(uid)

    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


def _create(client, user_id, title="D"):
    _login(client, user_id)
    resp = client.post("/bi_dashboard/api/dashboards", json={"title": title, "tiles": []})
    assert resp.status_code == 201, resp.data
    return resp.get_json()["id"]


# ── auth ─────────────────────────────────────────────────────────────────

def test_api_list_requires_auth(client):
    resp = client.get("/bi_dashboard/api/dashboards")
    assert resp.status_code == 401


def test_api_upload_requires_auth(client):
    resp = client.post("/bi_dashboard/api/upload")
    assert resp.status_code == 401


# ── IDOR / owner scoping ───────────────────────────────────────────────────

def test_list_scoped_to_owner(client):
    _create(client, "userA", "A1")
    _create(client, "userA", "A2")
    _create(client, "userB", "B1")

    _login(client, "userA")
    a_ids = {d["id"] for d in client.get("/bi_dashboard/api/dashboards").get_json()["dashboards"]}
    _login(client, "userB")
    b_list = client.get("/bi_dashboard/api/dashboards").get_json()["dashboards"]

    assert len(a_ids) == 2
    assert len(b_list) == 1
    assert b_list[0]["id"] not in a_ids


def test_get_cross_owner_denied(client):
    dash_id = _create(client, "userA")
    _login(client, "userB")
    resp = client.get(f"/bi_dashboard/api/dashboards/{dash_id}")
    assert resp.status_code == 404  # never leak existence to a non-owner


def test_put_cross_owner_denied(client):
    dash_id = _create(client, "userA")
    _login(client, "userB")
    resp = client.put(f"/bi_dashboard/api/dashboards/{dash_id}", json={"tiles": [{"spec": {}, "w": 6}]})
    assert resp.status_code == 403
    # confirm it was NOT mutated — owner still sees empty tiles
    _login(client, "userA")
    assert client.get(f"/bi_dashboard/api/dashboards/{dash_id}").get_json()["tiles"] == []


def test_delete_cross_owner_denied(client):
    dash_id = _create(client, "userA")
    _login(client, "userB")
    resp = client.delete(f"/bi_dashboard/api/dashboards/{dash_id}")
    assert resp.status_code == 403
    _login(client, "userA")
    assert client.get(f"/bi_dashboard/api/dashboards/{dash_id}").status_code == 200


def test_owner_can_crud_own(client):
    dash_id = _create(client, "userA")
    _login(client, "userA")
    assert client.get(f"/bi_dashboard/api/dashboards/{dash_id}").status_code == 200
    assert client.put(f"/bi_dashboard/api/dashboards/{dash_id}",
                      json={"tiles": [{"spec": {}, "w": 12}]}).status_code == 200
    assert client.delete(f"/bi_dashboard/api/dashboards/{dash_id}").status_code == 200
    assert client.get(f"/bi_dashboard/api/dashboards/{dash_id}").status_code == 404


def test_admin_can_access_others(client):
    dash_id = _create(client, "userA")
    _login(client, "admin")
    assert client.get(f"/bi_dashboard/api/dashboards/{dash_id}").status_code == 200
    assert client.put(f"/bi_dashboard/api/dashboards/{dash_id}",
                      json={"tiles": []}).status_code == 200


# ── tenant isolation ───────────────────────────────────────────────────────

def test_cross_tenant_hidden(client):
    dash_id = _create(client, "userA")          # tenantA
    _login(client, "userC")                      # tenantB
    assert client.get(f"/bi_dashboard/api/dashboards/{dash_id}").status_code == 404
    # PUT/DELETE across tenants: dashboard is not visible in the tenant -> 404
    assert client.put(f"/bi_dashboard/api/dashboards/{dash_id}", json={"tiles": []}).status_code == 404
    assert client.delete(f"/bi_dashboard/api/dashboards/{dash_id}").status_code == 404


def test_upload_ingests_into_callers_tenant(client):
    _login(client, "userA")
    csv = BytesIO(b"region,sales\nEast,100\nWest,200\n")
    resp = client.post("/bi_dashboard/api/upload",
                       data={"file": (csv, "sales.csv")}, content_type="multipart/form-data")
    assert resp.status_code == 200, resp.data
    assert resp.get_json()["success"] is True
    # userA (tenantA) sees the dataset; userC (tenantB) does not
    _login(client, "userA")
    assert len(client.get("/bi_dashboard/api/datasets").get_json()["datasets"]) == 1
    _login(client, "userC")
    assert client.get("/bi_dashboard/api/datasets").get_json()["datasets"] == []


# ── upload allowlist (cnr-bi-02) ────────────────────────────────────────────

def test_upload_rejects_disallowed_extension(client):
    _login(client, "userA")
    bad = BytesIO(b"#!/bin/sh\necho pwned\n")
    resp = client.post("/bi_dashboard/api/upload",
                       data={"file": (bad, "evil.sh")}, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "unsupported file type" in resp.get_json()["error"]


def test_upload_accepts_csv(client):
    _login(client, "userA")
    csv = BytesIO(b"region,sales\nEast,100\nWest,200\n")
    resp = client.post("/bi_dashboard/api/upload",
                       data={"file": (csv, "sales.csv")}, content_type="multipart/form-data")
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True
