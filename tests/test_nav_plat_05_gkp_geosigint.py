# CUI // SP-CTI
"""nav-plat-05 — Genesis/Oracle GKP promote/reject/auto-promote mutations must
require an authorized role, not merely an authenticated session.

The GKP (Genesis Knowledge Packet) promote / reject / auto-promote endpoints in
``tools/dashboard/app.py`` push knowledge into the live system. Before this fix
they relied on global auth with no role check, so any lowest-privilege
``developer`` session could promote or reject packets. Each mutating route is now
hard-gated with the shared dashboard ``@require_role("admin", "pm")`` decorator
(401 anonymous, 403 wrong role, allowed for admin/pm) and each successful
mutation appends a decision to the append-only ``audit_trail`` via
``_audit_gkp_mutation`` with the actor resolved from the session user
(``g.current_user``), never a request-body field.

Fixture conventions mirror ``tests/test_nav_sec_06_mutation_rbac.py`` (real
``create_app`` + seeded ``dashboard_users`` + ``session_transaction`` for the
inline ``app.py`` routes).

Area 2 of the audit — ``tools/geosigint/`` — does not exist in the tree (only
``apps/geosigint/`` does, handled by PR #582). ``test_tools_geosigint_absent``
codifies that verdict so a future reintroduction re-triggers this audit.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Building the real dashboard app (create_app) imports ~50 blueprints and is slow
# cold; relax the per-test timeout as tests/test_nav_sec_06_mutation_rbac.py does.
pytestmark = pytest.mark.timeout(180)


_AUTH_DDL = """
CREATE TABLE IF NOT EXISTS dashboard_users (
    id TEXT PRIMARY KEY, email TEXT UNIQUE, display_name TEXT,
    role TEXT DEFAULT 'admin', status TEXT DEFAULT 'active',
    created_by TEXT, created_at TIMESTAMP, updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dashboard_api_keys (
    id TEXT PRIMARY KEY, user_id TEXT, key_hash TEXT, key_prefix TEXT,
    label TEXT, status TEXT DEFAULT 'active', last_used_at TIMESTAMP,
    expires_at TIMESTAMP, created_at TIMESTAMP, revoked_at TIMESTAMP, revoked_by TEXT
);
CREATE TABLE IF NOT EXISTS dashboard_auth_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, event_type TEXT,
    ip_address TEXT, user_agent TEXT, details TEXT, created_at TIMESTAMP
);
"""

# (id, email, role)
_USERS = [
    ("u-admin", "admin@t.local", "admin"),
    ("u-pm", "pm@t.local", "pm"),
    ("u-dev", "dev@t.local", "developer"),
]

_NO_BYPASS_VARS = (
    "ICDEV_AUTH_BYPASS",
    "ICDEV_DASHBOARD_API_KEY",
    "ICDEV_DASHBOARD_DEV_AUTOLOGIN",
)

# Mutating GKP routes — all must be role-gated.
# ``?app=govchain`` selects a Genesis app whose ``promoter`` is None, so the
# allow-case handler takes the DB-fallback branch (no promoter subprocess) and
# returns 200/500 against the throwaway temp DB — never 401/403.
GKP_ROUTES = [
    ("POST", "/api/genesis/gkps/g1/promote?app=govchain"),
    ("POST", "/api/genesis/gkps/g1/reject?app=govchain"),
    ("POST", "/api/genesis/gkps/auto-promote?app=govchain"),
]


def _dispatch(client, method, path, headers=None, json_body=None):
    return client.open(
        path,
        method=method,
        json=json_body if json_body is not None else {},
        content_type="application/json",
        headers=headers or {},
    )


@pytest.fixture(scope="module")
def app_db(tmp_path_factory):
    """Real ``create_app`` wired to a temp SQLite DB with seeded role users.

    Role enforcement must stand on its own — no env key / dev-autologin / bypass.
    """
    db_path = str(tmp_path_factory.mktemp("nav_plat_05") / "db.sqlite")
    conn = sqlite3.connect(db_path)
    conn.executescript(_AUTH_DDL)
    for uid, email, role in _USERS:
        conn.execute(
            "INSERT OR IGNORE INTO dashboard_users (id, email, display_name, role) "
            "VALUES (?, ?, ?, ?)",
            (uid, email, email, role),
        )
    conn.commit()
    conn.close()

    saved = {k: os.environ.get(k) for k in (*_NO_BYPASS_VARS, "ICDEV_DB_PATH")}
    for var in _NO_BYPASS_VARS:
        os.environ.pop(var, None)
    os.environ["ICDEV_DB_PATH"] = db_path

    import tools.dashboard.config as _cfg_mod
    import tools.dashboard.app as _app_mod
    import tools.dashboard.auth as _auth_mod

    patchers = [patch.object(m, "DB_PATH", db_path) for m in (_cfg_mod, _app_mod, _auth_mod)]
    for p in patchers:
        p.start()
    try:
        try:
            app = _app_mod.create_app()
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"create_app() unavailable in this environment: {exc}")
        app.config["TESTING"] = True
        # A handler that raises should surface as a 500 (not propagate) so the
        # allow-case assertions ("not 401/403") still see a status code.
        app.config["PROPAGATE_EXCEPTIONS"] = False
        yield app
    finally:
        for p in patchers:
            p.stop()
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _client_as(app, user_id):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = user_id
    return c


# ── deny cases ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", GKP_ROUTES)
def test_gkp_anonymous_is_401(app_db, method, path):
    resp = _dispatch(app_db.test_client(), method, path)
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize("method,path", GKP_ROUTES)
def test_gkp_developer_is_403(app_db, method, path):
    resp = _dispatch(_client_as(app_db, "u-dev"), method, path)
    assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}"


# ── allow cases ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", GKP_ROUTES)
def test_gkp_admin_not_blocked(app_db, method, path):
    resp = _dispatch(_client_as(app_db, "u-admin"), method, path)
    assert resp.status_code not in (401, 403), f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize("method,path", GKP_ROUTES)
def test_gkp_pm_not_blocked(app_db, method, path):
    resp = _dispatch(_client_as(app_db, "u-pm"), method, path)
    assert resp.status_code not in (401, 403), f"{method} {path} -> {resp.status_code}"


# ── GKP reads stay open (not gated) ───────────────────────────────────────────

def test_gkp_list_read_stays_open(app_db):
    # GET the GKP list is a read — an authenticated developer must not be blocked.
    resp = _client_as(app_db, "u-dev").get("/api/genesis/gkps?app=govchain")
    assert resp.status_code not in (401, 403), resp.status_code


# ── audit trail: successful mutation records the session actor ────────────────

def test_gkp_promote_audits_session_actor(app_db, monkeypatch):
    """A successful promote appends an audit_trail row whose actor is the
    resolved session user (g.current_user), never a spoofed body field."""
    import tools.dashboard.app as _app_mod

    captured = {}

    class _FakeConn:
        def execute(self, sql, params=None):
            if "INSERT INTO audit_trail" in sql:
                captured["sql"] = sql
                captured["params"] = params
            return self

        def fetchone(self):
            return [0]

        def fetchall(self):
            return []

        def commit(self):
            pass

        def close(self):
            pass

    # Route both the audit insert (_get_db) and the govchain DB-fallback
    # (_genesis_db via get_connection) through the capture connection.
    monkeypatch.setattr(_app_mod, "get_connection", lambda *a, **k: _FakeConn())

    resp = _client_as(app_db, "u-admin").post(
        "/api/genesis/gkps/g1/promote?app=govchain",
        json={"actor": "spoofed-attacker"},
        content_type="application/json",
    )
    assert resp.status_code not in (401, 403), resp.get_data(as_text=True)
    assert "params" in captured, "no audit_trail INSERT was issued"
    # params = (event_type, action, details_json)
    event_type, action, details_json = captured["params"]
    assert event_type == "approval_granted", captured["params"]
    assert "genesis_gkp_promote:g1" in action, action
    assert '"actor": "u-admin"' in details_json, details_json
    assert "spoofed-attacker" not in details_json, details_json


# ── Area 2 audit verdict: tools/geosigint/ does not exist ─────────────────────

def test_tools_geosigint_absent():
    """The nav-plat-05 audit flagged ``tools/geosigint/`` as unexplored. It does
    not exist in the tree (only ``apps/geosigint/`` does — handled by PR #582).
    This codifies the audit verdict: a future reintroduction re-triggers review.
    """
    assert not (ROOT / "tools" / "geosigint").exists(), (
        "tools/geosigint/ reappeared — re-run the nav-plat-05 signal/emitter "
        "audit (stub-as-live, fail-open, unauth mutations, XSS, dead LLM calls)."
    )
    assert (ROOT / "apps" / "geosigint").exists(), "apps/geosigint/ unexpectedly missing"
