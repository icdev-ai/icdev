# [TEMPLATE: CUI // SP-CTI]
"""API-level tests for the pWin endpoints in tools/dashboard/api/govcon.py
(prop-cap-12): POST /api/govcon/proposals/<id>/pwin (compute), GET .../pwin
(retrieve), GET /api/govcon/pipeline-value (roll-up).

Fixture pattern (tmp_db/auth/app, real dashboard auth via Bearer API keys)
mirrors the established tests/test_govcon_rbac.py convention for this exact
blueprint, rather than the fake-g.current_user before_request hack used for
tools/dashboard/api/proposals.py-family tests.
"""
import sqlite3

import pytest
from flask import Flask

from tools.db.init_icdev_db import DASHBOARD_AUTH_ALTER_SQL, PROPOSALS_ALTER_SQL, SCHEMA_SQL


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "icdev_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(SCHEMA_SQL)
    for stmt in DASHBOARD_AUTH_ALTER_SQL + PROPOSALS_ALTER_SQL:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
    conn.execute(
        "INSERT INTO proposal_opportunities "
        "(id, solicitation_number, title, agency, due_date, proposal_type, status, "
        " estimated_value_low, estimated_value_high) "
        "VALUES ('opp-1', 'SOL-0001', 'Cyber Sustainment IDIQ', 'DoD', '2026-12-31', 'FFP', "
        " 'writing', 1000000, 1000000)"
    )
    conn.commit()
    conn.close()

    import tools.dashboard.config
    import tools.dashboard.auth
    import tools.dashboard.api.govcon as govcon_mod

    monkeypatch.setattr(tools.dashboard.config, "DB_PATH", str(db_file))
    monkeypatch.setattr(tools.dashboard.auth, "DB_PATH", str(db_file))
    monkeypatch.setattr(govcon_mod, "DB_PATH", db_file)
    # bayesian_bid_scorer.get_connection() takes no args and resolves its
    # target via tools.db.storage.get_connection()'s own per-call
    # ICDEV_DB_PATH env read (+ automatic %s->? translation for SQLite) --
    # no separate monkeypatch needed, same as the established
    # test_govcon_rbac.py fixture for this blueprint.
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_file))
    return db_file


@pytest.fixture()
def auth(tmp_db):
    import tools.dashboard.auth as auth_mod

    return auth_mod


@pytest.fixture()
def app(auth):
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


# ── RBAC on the compute (write) endpoint ─────────────────────────────────


class TestPwinComputeRBAC:
    def test_denied_role_gets_403(self, auth, app):
        raw_key, _ = _key_for(auth, "dev@example.mil", "developer")
        with app.test_client() as client:
            resp = client.post("/api/govcon/proposals/opp-1/pwin", headers=_hdr(raw_key), json={})
            assert resp.status_code == 403

    def test_allowed_role_passes_gate(self, auth, app):
        # 'capture_mgr' isn't storable yet in dashboard_users.role's CHECK
        # constraint (that's prop-fix-08's scope) -- use 'admin', which IS
        # storable and IS in GOVCON_WRITE_ROLES, same workaround already
        # used by test_govcon_rbac.py::TestAllowedRoles.
        raw_key, _ = _key_for(auth, "cm@example.mil", "admin")
        with app.test_client() as client:
            resp = client.post("/api/govcon/proposals/opp-1/pwin", headers=_hdr(raw_key), json={})
            assert resp.status_code == 200

    def test_unauthenticated_gets_401(self, app):
        with app.test_client() as client:
            resp = client.post(
                "/api/govcon/proposals/opp-1/pwin",
                headers={"Authorization": "Bearer icdev_dash_invalid", "Content-Type": "application/json"},
                json={},
            )
            assert resp.status_code == 401


# ── Functional behavior ───────────────────────────────────────────────────


class TestPwinComputeAndRetrieve:
    def test_compute_persists_and_is_retrievable(self, auth, app):
        # 'capture_mgr' isn't storable yet in dashboard_users.role's CHECK
        # constraint (that's prop-fix-08's scope) -- use 'admin', which IS
        # storable and IS in GOVCON_WRITE_ROLES, same workaround already
        # used by test_govcon_rbac.py::TestAllowedRoles.
        raw_key, _ = _key_for(auth, "cm@example.mil", "admin")
        with app.test_client() as client:
            compute_resp = client.post(
                "/api/govcon/proposals/opp-1/pwin", headers=_hdr(raw_key),
                json={"incumbency": 0.9, "crm_engagement": 0.8, "competitive_position": 0.7,
                      "compliance_coverage": 0.9, "past_performance_fit": 0.85},
            )
            assert compute_resp.status_code == 200
            data = compute_resp.get_json()
            assert data["pwin_pct"] > 70
            assert "factor_breakdown" in data

            get_resp = client.get("/api/govcon/proposals/opp-1/pwin", headers=_hdr(raw_key))
            assert get_resp.status_code == 200
            assert get_resp.get_json()["pwin_pct"] == data["pwin_pct"]

    def test_get_unscored_opportunity_returns_404(self, auth, app):
        # 'capture_mgr' isn't storable yet in dashboard_users.role's CHECK
        # constraint (that's prop-fix-08's scope) -- use 'admin', which IS
        # storable and IS in GOVCON_WRITE_ROLES, same workaround already
        # used by test_govcon_rbac.py::TestAllowedRoles.
        raw_key, _ = _key_for(auth, "cm@example.mil", "admin")
        with app.test_client() as client:
            resp = client.get("/api/govcon/proposals/opp-1/pwin", headers=_hdr(raw_key))
            assert resp.status_code == 404

    def test_compute_pulls_estimated_value_from_opportunity_when_omitted(self, auth, app):
        """estimated_value not in the POST body -> falls back to
        proposal_opportunities.estimated_value_low/high midpoint (both set
        to 1,000,000 in the tmp_db fixture)."""
        # 'capture_mgr' isn't storable yet in dashboard_users.role's CHECK
        # constraint (that's prop-fix-08's scope) -- use 'admin', which IS
        # storable and IS in GOVCON_WRITE_ROLES, same workaround already
        # used by test_govcon_rbac.py::TestAllowedRoles.
        raw_key, _ = _key_for(auth, "cm@example.mil", "admin")
        with app.test_client() as client:
            resp = client.post("/api/govcon/proposals/opp-1/pwin", headers=_hdr(raw_key), json={})
            data = resp.get_json()
            # All-neutral factors -> pwin_score 0.5 -> weighted_value = 500,000
            assert data["weighted_value"] == 500_000.0

    def test_compute_writes_back_win_probability_to_opportunity(self, auth, app, tmp_db):
        # 'capture_mgr' isn't storable yet in dashboard_users.role's CHECK
        # constraint (that's prop-fix-08's scope) -- use 'admin', which IS
        # storable and IS in GOVCON_WRITE_ROLES, same workaround already
        # used by test_govcon_rbac.py::TestAllowedRoles.
        raw_key, _ = _key_for(auth, "cm@example.mil", "admin")
        with app.test_client() as client:
            resp = client.post(
                "/api/govcon/proposals/opp-1/pwin", headers=_hdr(raw_key),
                json={"incumbency": 1.0, "crm_engagement": 1.0, "competitive_position": 1.0,
                      "compliance_coverage": 1.0, "past_performance_fit": 1.0},
            )
            pct = resp.get_json()["pwin_pct"]

        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute("SELECT win_probability FROM proposal_opportunities WHERE id = 'opp-1'").fetchone()
        conn.close()
        assert row[0] == pct


class TestPipelineValueEndpoint:
    def test_pipeline_value_open_to_any_authenticated_user(self, auth, app):
        """GET /pipeline-value is a read endpoint, intentionally not
        role-gated (matches GovCon's read-vs-write RBAC convention)."""
        raw_key, _ = _key_for(auth, "reader@example.mil", "developer")
        with app.test_client() as client:
            resp = client.get("/api/govcon/pipeline-value", headers=_hdr(raw_key))
            assert resp.status_code == 200
            data = resp.get_json()
            assert "total_weighted_pipeline_value" in data
            assert "opportunities" in data

    def test_pipeline_value_reflects_computed_pwin(self, auth, app):
        # 'capture_mgr' isn't storable yet in dashboard_users.role's CHECK
        # constraint (that's prop-fix-08's scope) -- use 'admin', which IS
        # storable and IS in GOVCON_WRITE_ROLES, same workaround already
        # used by test_govcon_rbac.py::TestAllowedRoles.
        raw_key, _ = _key_for(auth, "cm@example.mil", "admin")
        with app.test_client() as client:
            client.post(
                "/api/govcon/proposals/opp-1/pwin", headers=_hdr(raw_key),
                json={"incumbency": 1.0, "crm_engagement": 1.0, "competitive_position": 1.0,
                      "compliance_coverage": 1.0, "past_performance_fit": 1.0},
            )
            resp = client.get("/api/govcon/pipeline-value", headers=_hdr(raw_key))
            data = resp.get_json()
            assert data["scored_count"] == 1
            item = next(o for o in data["opportunities"] if o["opportunity_id"] == "opp-1")
            assert item["has_pwin_model"] is True
            assert item["pwin_pct"] > 90
