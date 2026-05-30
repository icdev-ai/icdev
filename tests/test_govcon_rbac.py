# [TEMPLATE: CUI // SP-CTI]
"""
RBAC wiring tests for GovCon (prop-fix-09).

Verifies that @require_role(...) is applied to the write/sensitive GovCon API
endpoints in tools/dashboard/api/govcon.py:
    sam/scan, sam/import, extract-requirements, map-capabilities,
    auto-compliance, bid-recommendation, auto-draft

Contract (per task): deny -> 403 (not 500) + log_auth_event("permission_denied");
unauthenticated -> 401; un-gated read endpoints stay open to any authenticated
user. Roles per prop-fix-08 (GOVCON_WRITE_ROLES = admin, bd, capture_mgr).
"""

import sqlite3

import pytest
from flask import Flask

from tools.db.init_icdev_db import DASHBOARD_AUTH_ALTER_SQL, SCHEMA_SQL


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Create a temporary ICDEV™ database and monkeypatch DB_PATH everywhere."""
    db_file = tmp_path / "icdev_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(SCHEMA_SQL)
    for stmt in DASHBOARD_AUTH_ALTER_SQL:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()

    import tools.dashboard.config
    import tools.dashboard.auth
    import tools.dashboard.api.govcon as govcon_mod

    monkeypatch.setattr(tools.dashboard.config, "DB_PATH", str(db_file))
    monkeypatch.setattr(tools.dashboard.auth, "DB_PATH", str(db_file))
    # govcon api uses its own module-level DB_PATH (ICDEV_DB_PATH)
    monkeypatch.setattr(govcon_mod, "DB_PATH", db_file)
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_file))
    return db_file


@pytest.fixture()
def auth(tmp_db):
    import tools.dashboard.auth as auth_mod

    return auth_mod


@pytest.fixture()
def app(auth):
    """Minimal Flask app with the real govcon_api blueprint + auth middleware."""
    from tools.dashboard.api.govcon import govcon_api

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(govcon_api)
    auth.register_dashboard_auth(app)
    return app


def _key_for(auth, email, role):
    user = auth.create_user(email, email.split("@")[0], role=role)
    return auth.create_api_key_for_user(user["id"])["raw_key"], user


def _hdr(raw_key):
    return {"Authorization": f"Bearer {raw_key}", "Content-Type": "application/json"}


# Representative (method, path) for each gated write/sensitive endpoint.
GATED_ENDPOINTS = [
    ("POST", "/api/govcon/sam/scan"),
    ("POST", "/api/govcon/sam/import/some-id"),
    ("POST", "/api/govcon/opportunities/opp-1/extract-requirements"),
    ("POST", "/api/govcon/opportunities/opp-1/map-capabilities"),
    ("POST", "/api/govcon/opportunities/opp-1/auto-compliance"),
    ("GET", "/api/govcon/opportunities/opp-1/bid-recommendation"),
    ("POST", "/api/govcon/opportunities/opp-1/auto-draft"),
]


# ===================================================================
# 1. Denied role -> 403 (not 500) on every gated endpoint
# ===================================================================


class TestDeniedRole:
    @pytest.mark.parametrize("method,path", GATED_ENDPOINTS)
    def test_denied_role_gets_403(self, auth, app, method, path):
        # 'developer' is NOT in GOVCON_WRITE_ROLES
        raw_key, _ = _key_for(auth, "dev@example.mil", "developer")
        with app.test_client() as client:
            resp = client.open(path, method=method, headers=_hdr(raw_key), json={})
            assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}"

    def test_deny_logs_permission_denied(self, auth, app, tmp_db):
        raw_key, user = _key_for(auth, "denylog@example.mil", "developer")
        with app.test_client() as client:
            client.post("/api/govcon/sam/scan", headers=_hdr(raw_key), json={})
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM dashboard_auth_log WHERE user_id = ? AND event_type = 'permission_denied'",
            (user["id"],),
        ).fetchall()
        conn.close()
        assert len(rows) >= 1


# ===================================================================
# 2. Unauthenticated -> 401
# ===================================================================


class TestUnauthenticated:
    def test_invalid_credentials_gets_401(self, app):
        # Invalid bearer forces the 401 path before any env-key auto-login.
        with app.test_client() as client:
            resp = client.post(
                "/api/govcon/sam/scan",
                headers={
                    "Authorization": "Bearer icdev_dash_invalid",
                    "Content-Type": "application/json",
                },
                json={},
            )
            assert resp.status_code == 401


# ===================================================================
# 3. Allowed roles pass the RBAC gate (never 401/403)
# ===================================================================


class TestAllowedRoles:
    def test_admin_passes_gate(self, auth, app):
        # 'admin' is storable today and is in GOVCON_WRITE_ROLES.
        raw_key, _ = _key_for(auth, "admin@example.mil", "admin")
        with app.test_client() as client:
            resp = client.post("/api/govcon/sam/scan", headers=_hdr(raw_key), json={})
            # Gate passed: body may 200 or 500 (tool deps), but never 401/403.
            assert resp.status_code not in (401, 403), f"admin -> {resp.status_code}"

    def test_write_roles_match_fix08_vocabulary(self):
        # bd/capture_mgr become storable only after prop-fix-08 extends the
        # dashboard_users.role CHECK constraint; the gate already references
        # them so wiring is forward-compatible. Assert the constant here.
        from tools.dashboard.api.govcon import GOVCON_WRITE_ROLES

        assert GOVCON_WRITE_ROLES == ("admin", "bd", "capture_mgr")


# ===================================================================
# 4. Un-gated read endpoints remain open to any authenticated user
# ===================================================================


class TestUngatedReads:
    def test_list_opportunities_open_to_developer(self, auth, app):
        # /sam/opportunities is a read endpoint, intentionally NOT gated.
        raw_key, _ = _key_for(auth, "reader@example.mil", "developer")
        with app.test_client() as client:
            resp = client.get("/api/govcon/sam/opportunities", headers=_hdr(raw_key))
            assert resp.status_code not in (401, 403)
