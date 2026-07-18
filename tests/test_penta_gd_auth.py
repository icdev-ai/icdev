# CUI // SP-CTI
"""penta-gd-01 — AI GameDay auth + tenant scoping regression tests.

Covers:
  * Every route requires authentication (unauth API -> 401, page -> redirect).
  * Facilitator/admin operations enforce the component registry min_il gate.
  * The three enumeration endpoints (responses / leaderboard / simulate-state)
    are tenant-scoped: tenant A cannot read tenant B's data.

Uses the shared conftest schema + storage translate layer (no raw sqlite3 in
the query path). The blueprint is mounted on a bare Flask app whose
before_request seeds g.current_user / g.tenant_id, mirroring the dashboard
auth contract (tools/dashboard/auth.py). g.security_context is intentionally
left unset so RLS predicate injection does not fire on the ttx_* tables.
"""

from __future__ import annotations

import pytest
from flask import Flask, g


@pytest.fixture
def gd_db(tmp_path, monkeypatch):
    """Point get_connection() at a temp SQLite DB seeded with the shared schema."""
    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()
    conn.close()
    return db_path


def _make_app(authed_tenant=None, role="admin", impact_level="IL5"):
    app = Flask(__name__)
    app.secret_key = "penta-gd-01-test"
    app.config["TESTING"] = True

    from apps.ai_gameday.blueprint import bp

    app.register_blueprint(bp)

    @app.route("/login", endpoint="login_page")
    def _login_page():  # pragma: no cover - trivial
        return "login", 200

    @app.before_request
    def _auth():
        if authed_tenant is not None:
            g.current_user = {
                "id": "u-test",
                "role": role,
                "tenant_id": authed_tenant,
                "impact_level": impact_level,
            }
            g.tenant_id = authed_tenant
        # unauthenticated: leave g.current_user unset

    return app


def _seed_session(scenario_slug, tenant_id, facilitator="Fac"):
    from tools.ttx.session_manager import create_session

    return create_session(
        scenario_slug=scenario_slug,
        session_mode="live",
        facilitator_name=facilitator,
        tenant_id=tenant_id,
    )


def _seed_response(session_id, response_text):
    """Seed a team + inject + response + score for a session (translate layer)."""
    from tools.db.storage import get_connection

    conn = get_connection()
    conn.execute(
        "INSERT INTO ttx_teams (session_id, team_name, join_code) VALUES (%s, %s, %s)",
        (session_id, "Team B", f"JC{session_id}B"),
    )
    conn.commit()
    team_id = conn.execute(
        "SELECT team_id FROM ttx_teams WHERE join_code = %s", (f"JC{session_id}B",)
    ).fetchone()["team_id"]
    inject_id = f"inj-{session_id}"
    conn.execute(
        "INSERT INTO ttx_injects (inject_id, session_id, slug, title) VALUES (%s, %s, %s, %s)",
        (inject_id, session_id, "slug1", "Inject 1"),
    )
    conn.execute(
        "INSERT INTO ttx_responses (team_id, inject_id, response_text) VALUES (%s, %s, %s)",
        (team_id, inject_id, response_text),
    )
    conn.commit()
    return team_id, inject_id


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/gameday/session"),
        ("patch", "/api/gameday/session/1/state"),
        ("post", "/api/gameday/inject/x/dispatch"),
        ("post", "/api/gameday/response"),
        ("post", "/api/gameday/api-log"),
        ("get", "/api/gameday/session/1/responses"),
        ("get", "/api/gameday/session/1/leaderboard"),
        ("get", "/api/gameday/session/1/simulate-state"),
        ("post", "/api/gameday/ai-league/start"),
        ("post", "/api/gameday/scenarios"),
    ],
)
def test_api_routes_require_auth(gd_db, method, path):
    app = _make_app(authed_tenant=None)
    client = app.test_client()
    resp = getattr(client, method)(path, json={})
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


def test_page_route_redirects_when_unauthenticated(gd_db):
    app = _make_app(authed_tenant=None)
    client = app.test_client()
    resp = client.get("/gameday")
    assert resp.status_code in (301, 302)
    assert "/login" in resp.headers.get("Location", "")


# ---------------------------------------------------------------------------
# Registry min_il enforcement on facilitator operations
# ---------------------------------------------------------------------------

def test_facilitator_op_blocked_below_min_il(gd_db):
    # Registry min_il for gameday is IL4; an IL2 user must be rejected.
    app = _make_app(authed_tenant="tenant-a", role="pm", impact_level="IL2")
    client = app.test_client()
    resp = client.post("/api/gameday/session", json={"scenario_slug": "x"})
    assert resp.status_code == 403


def test_facilitator_op_allowed_at_min_il(gd_db):
    app = _make_app(authed_tenant="tenant-a", role="pm", impact_level="IL4")
    client = app.test_client()
    resp = client.post("/api/gameday/session", json={"scenario_slug": "demo"})
    # Not a 401/403 — the auth+IL gate passed (creation may 200/201/400 downstream).
    assert resp.status_code not in (401, 403)


# ---------------------------------------------------------------------------
# Tenant scoping on enumeration endpoints
# ---------------------------------------------------------------------------

def test_cross_tenant_responses_denied(gd_db):
    sess_b = _seed_session("scenario-b", tenant_id="tenant-b")
    sid = sess_b["session_id"]
    _seed_response(sid, "SECRET-B-RESPONSE")

    # Tenant A cannot see tenant B's responses.
    app_a = _make_app(authed_tenant="tenant-a")
    r = app_a.test_client().get(f"/api/gameday/session/{sid}/responses")
    assert r.status_code == 404
    assert r.get_json()["responses"] == []

    # Tenant B can.
    app_b = _make_app(authed_tenant="tenant-b")
    r = app_b.test_client().get(f"/api/gameday/session/{sid}/responses")
    assert r.status_code == 200
    body = r.get_json()
    assert body["total"] >= 1
    assert any("SECRET-B-RESPONSE" in (row.get("response_preview") or "") for row in body["responses"])


def test_cross_tenant_leaderboard_denied(gd_db):
    sess_b = _seed_session("scenario-b", tenant_id="tenant-b")
    sid = sess_b["session_id"]

    app_a = _make_app(authed_tenant="tenant-a")
    r = app_a.test_client().get(f"/api/gameday/session/{sid}/leaderboard")
    assert r.status_code == 404
    assert r.get_json()["leaderboard"] == []

    app_b = _make_app(authed_tenant="tenant-b")
    r = app_b.test_client().get(f"/api/gameday/session/{sid}/leaderboard")
    assert r.status_code == 200


def test_cross_tenant_simulate_state_denied(gd_db):
    sess_b = _seed_session("scenario-b", tenant_id="tenant-b")
    sid = sess_b["session_id"]

    app_a = _make_app(authed_tenant="tenant-a")
    r = app_a.test_client().get(f"/api/gameday/session/{sid}/simulate-state")
    assert r.status_code == 404

    app_b = _make_app(authed_tenant="tenant-b")
    r = app_b.test_client().get(f"/api/gameday/session/{sid}/simulate-state")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
