# CUI // SP-CTI
"""nav-sec-06 — state-changing endpoints require an authorized role, not merely
an authenticated session.

Before this fix four groups of mutating endpoints relied on global auth with no
role check, so any lowest-privilege ``developer`` could drive them:

  1. Kanban scheduler control (``tools/dashboard/app.py``): pause / resume the
     scheduler, flip Manual-Build mode, and change the runner's build model.
     The audit ``actor`` was also read from the request body (spoofable).
  2. Pulse editorial actions (``tools/dashboard/app.py``): approve / reject /
     publish / unpublish / delete a blog post.
  3. AISG mutations (``tools/aisg/blueprint.py``): wizard-completion, pattern
     seed / deploy, knowledge-handoff export / import, visual-builder save /
     generate.
  4. Foundry compute trigger (``tools/foundry/blueprint.py``): ``/api/foundry/run``.

Each route is now hard-gated with the shared dashboard ``@require_role``
decorator (401 anonymous, 403 wrong role, allowed for the authorized roles), and
the kanban scheduler audit ``actor`` now comes from the resolved session user
(``g.current_user``) — never from a spoofable request-body field.

Fixture conventions mirror ``tests/test_nav_sec_05_strategos_rbac.py`` (fake-auth
X-Test-Role for blueprints) and ``tests/test_dashboard_kanban.py`` (real
``create_app`` + seeded ``dashboard_users`` + ``session_transaction`` for the
inline ``app.py`` routes).
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Building the real dashboard app (create_app) imports ~50 blueprints and takes
# ~15s cold; that exceeds the repo-wide 30s per-test timeout. The app_db fixture
# below is module-scoped (built once), but the first test that triggers it still
# pays the cold-import cost, so relax the timeout for this file.
pytestmark = pytest.mark.timeout(180)


# ── shared helpers ────────────────────────────────────────────────────────────

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
    ("u-reviewer", "rev@t.local", "reviewer"),
]

_NO_BYPASS_VARS = (
    "ICDEV_AUTH_BYPASS",
    "ICDEV_DASHBOARD_API_KEY",
    "ICDEV_DASHBOARD_DEV_AUTOLOGIN",
)


def _dispatch(client, method, path, headers=None, json_body=None):
    return client.open(
        path,
        method=method,
        json=json_body if json_body is not None else {},
        content_type="application/json",
        headers=headers or {},
    )


# ============================================================================
# app.py inline routes — kanban scheduler + pulse editorial actions
# ============================================================================

KANBAN_ROUTES = [
    ("POST", "/api/kanban/scheduler/pause"),
    ("POST", "/api/kanban/scheduler/resume"),
    ("POST", "/api/kanban/build-mode"),
    ("POST", "/api/kanban/build-model"),
]

PULSE_ROUTES = [
    ("POST", "/api/pulse/posts/p1/approve"),
    ("POST", "/api/pulse/posts/p1/reject"),
    ("POST", "/api/pulse/posts/p1/publish"),
    ("POST", "/api/pulse/posts/p1/unpublish"),
    ("DELETE", "/api/pulse/posts/p1"),
]

APP_ROUTES = KANBAN_ROUTES + PULSE_ROUTES


@pytest.fixture(scope="module")
def app_db(tmp_path_factory):
    """Real ``create_app`` wired to a temp SQLite DB with seeded role users.

    Module-scoped so the ~15s cold create_app cost is paid once. Role enforcement
    must stand on its own — no env key / dev-autologin / bypass.
    """
    db_path = str(tmp_path_factory.mktemp("nav_sec_06") / "db.sqlite")
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
        # A handler that raises should surface as a 500 response (not propagate)
        # so allow-case assertions ("not 401/403") still see a status code.
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

@pytest.mark.parametrize("method,path", APP_ROUTES)
def test_app_route_anonymous_is_401(app_db, method, path):
    resp = _dispatch(app_db.test_client(), method, path)
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize("method,path", APP_ROUTES)
def test_app_route_developer_is_403(app_db, method, path):
    resp = _dispatch(_client_as(app_db, "u-dev"), method, path)
    assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}"


# ── allow cases ───────────────────────────────────────────────────────────────

def test_kanban_admin_not_blocked(app_db, monkeypatch):
    # Stub the underlying kanban controls so the allow-case has no side effects
    # on the real scheduler / build-mode / model-override state.
    import tools.kanban.scheduler_control as sc
    import tools.kanban.build_mode as bm
    import tools.kanban.model_override as mo

    monkeypatch.setattr(sc, "pause", lambda **k: {"ok": True, "actor": k.get("actor")})
    monkeypatch.setattr(sc, "resume", lambda **k: {"ok": True, "actor": k.get("actor")})
    monkeypatch.setattr(bm, "set_manual", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(bm, "status", lambda: {"manual": False})
    monkeypatch.setattr(mo, "set_model", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(mo, "status", lambda: {"model": None})
    monkeypatch.setattr(mo, "available", lambda: [])

    client = _client_as(app_db, "u-admin")
    for method, path in KANBAN_ROUTES:
        resp = _dispatch(client, method, path, json_body={"manual": True})
        assert resp.status_code not in (401, 403), f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize("method,path", PULSE_ROUTES)
def test_pulse_reviewer_not_blocked(app_db, method, path):
    # reviewer is an editorial-class role: it clears the RBAC gate. The handler
    # itself may 404 (post missing) or 500 (pulse tables absent in the temp DB) —
    # the point is that it is NOT rejected by @require_role.
    resp = _dispatch(_client_as(app_db, "u-reviewer"), method, path)
    assert resp.status_code not in (401, 403), f"{method} {path} -> {resp.status_code}"


# ── kanban audit actor comes from the session, never the request body ─────────

def test_kanban_scheduler_actor_from_session_not_body(app_db, monkeypatch):
    """The ``actor`` handed to scheduler_control.pause must be the resolved
    session user (g.current_user), never a body-supplied ``actor`` field."""
    import tools.kanban.scheduler_control as sc

    captured = {}

    def _pause(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(sc, "pause", _pause)

    resp = _client_as(app_db, "u-admin").post(
        "/api/kanban/scheduler/pause",
        json={"actor": "spoofed-attacker", "reason": "manual"},
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert captured.get("actor") == "u-admin", captured
    assert captured.get("actor") != "spoofed-attacker", captured


def test_kanban_build_mode_actor_from_session_not_body(app_db, monkeypatch):
    """Manual-Build toggle records the session actor, not a spoofed body actor."""
    import tools.kanban.build_mode as bm

    captured = {}

    def _set_manual(manual, **kwargs):
        captured["manual"] = manual
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(bm, "set_manual", _set_manual)

    resp = _client_as(app_db, "u-admin").post(
        "/api/kanban/build-mode",
        json={"manual": True, "actor": "spoofed-attacker"},
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert captured.get("actor") == "u-admin", captured
    assert captured.get("actor") != "spoofed-attacker", captured


# ============================================================================
# AISG blueprint mutations
# ============================================================================

AISG_ROUTES = [
    ("POST", "/api/ai-wizard/submit"),
    ("POST", "/api/aisg/patterns/seed"),
    ("POST", "/api/aisg/patterns/px/deploy"),
    ("POST", "/api/ai-handoff/export"),
    ("POST", "/api/ai-handoff/import"),
    ("POST", "/api/ai-builder/save"),
    ("POST", "/api/ai-builder/generate"),
]


def _fake_auth_app(register):
    from flask import Flask, g, request, session

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.before_request
    def _fake_auth():
        role = request.headers.get("X-Test-Role")
        if role:
            g.current_user = {"id": "u-session", "role": role, "tenant_id": "t"}
            session["user_id"] = "u-session"

    register(app)
    return app


@pytest.fixture()
def aisg_client(tmp_path, monkeypatch):
    for var in _NO_BYPASS_VARS:
        monkeypatch.delenv(var, raising=False)
    # Isolate any handler DB access to a throwaway DB (allow-case handlers run).
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "aisg.db"))

    try:
        import tools.aisg.blueprint as _aisg_mod
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"aisg blueprint not importable: {exc}")

    # Reload for a pristine blueprint. ``create_app()`` — which the module-scoped
    # ``app_db`` fixture above runs — attaches ``guard_component_access`` to this
    # module-level blueprint *singleton* (tools/dashboard/lazy_canvas.py). That
    # mutation outlives the app it was made for, so a bare Flask app registering
    # the same object inherits a canvas-access guard that 403s every aisg route,
    # including the deliberately-open reads. These tests would then measure the
    # guard rather than ``require_role``, and their result depended on whether
    # anything had built the dashboard first — which is exactly what happened
    # once app_db stopped erroring out at create_app().
    importlib.reload(_aisg_mod)

    app = _fake_auth_app(lambda a: a.register_blueprint(_aisg_mod.bp))
    with app.test_client() as c:
        yield c


@pytest.mark.parametrize("method,path", AISG_ROUTES)
def test_aisg_anonymous_is_401(aisg_client, method, path):
    resp = _dispatch(aisg_client, method, path)
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize("method,path", AISG_ROUTES)
def test_aisg_developer_is_403(aisg_client, method, path):
    resp = _dispatch(aisg_client, method, path, {"X-Test-Role": "developer"})
    assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize("method,path", AISG_ROUTES)
def test_aisg_admin_not_blocked(aisg_client, method, path):
    resp = _dispatch(aisg_client, method, path, {"X-Test-Role": "admin"})
    assert resp.status_code not in (401, 403), f"{method} {path} -> {resp.status_code}"


def test_aisg_reads_stay_open(aisg_client):
    # GET list-patterns is a read: it must NOT require an approver role. An
    # authenticated developer (and even an anonymous caller) can read it.
    resp = aisg_client.get("/api/aisg/patterns", headers={"X-Test-Role": "developer"})
    assert resp.status_code not in (401, 403), resp.status_code


# ============================================================================
# Foundry blueprint — /api/foundry/run compute trigger
# ============================================================================

FOUNDRY_ROUTES = [("POST", "/api/foundry/run")]


@pytest.fixture()
def foundry_client(tmp_path, monkeypatch):
    for var in _NO_BYPASS_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ICDEV_FOUNDRY_ENABLED", "true")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "foundry.db"))

    try:
        from tools.foundry.blueprint import create_foundry_blueprint
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"foundry blueprint not importable: {exc}")

    bp = create_foundry_blueprint()
    if bp is None:  # pragma: no cover
        pytest.skip("foundry blueprint disabled")

    app = _fake_auth_app(lambda a: a.register_blueprint(bp))
    with app.test_client() as c:
        yield c


@pytest.mark.parametrize("method,path", FOUNDRY_ROUTES)
def test_foundry_anonymous_is_401(foundry_client, method, path):
    resp = _dispatch(foundry_client, method, path)
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize("method,path", FOUNDRY_ROUTES)
def test_foundry_developer_is_403(foundry_client, method, path):
    resp = _dispatch(foundry_client, method, path, {"X-Test-Role": "developer"})
    assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}"


@pytest.mark.parametrize("method,path", FOUNDRY_ROUTES)
def test_foundry_pm_not_blocked(foundry_client, method, path):
    resp = _dispatch(foundry_client, method, path, {"X-Test-Role": "pm"})
    assert resp.status_code not in (401, 403), f"{method} {path} -> {resp.status_code}"


def test_foundry_reads_stay_open(foundry_client):
    # GET runs list is a read — no approver role required.
    resp = foundry_client.get("/api/foundry/runs", headers={"X-Test-Role": "developer"})
    assert resp.status_code not in (401, 403), resp.status_code
