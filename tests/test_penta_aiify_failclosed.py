# CUI // SP-CTI
"""penta-aiify-03 — fail-closed HITL gate + explicit PG-fallback controls.

Two controls that historically failed OPEN are hardened here:

  1. send-to-kanban's PRD HITL approval gate. The decision lookup used to be
     wrapped in ``try/except: pass`` — a DB error meant "no known rejections",
     so tasks proceeded. It now fails CLOSED: a lookup error returns 503 and
     creates zero tasks, unless an authorized operator passes an explicit
     ``force`` override (which is audited and still routes tasks to the
     non-dispatchable 'suggested' quarantine).

  2. tools/aiify/db/init_db.py::get_connection. A PostgreSQL connection failure
     used to silently fall back to a *separate* SQLite store, forking canvas
     data and masking outages. It now re-raises by default; SQLite fallback is
     allowed only when AIIFY_ALLOW_SQLITE_FALLBACK is explicitly enabled.
"""
from __future__ import annotations

import importlib

import pytest


def _storage():
    """The exact module object that init_db resolves via
    ``from tools.db.storage import get_canvas_connection`` — so monkeypatching
    its attributes actually intercepts the call (shim-aware; see MEMORY)."""
    return importlib.import_module("tools.db.storage")


# ─────────────────────────────────────────────────────────────────────────────
# init_db.get_connection — explicit PG fallback
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def pg_env(tmp_path, monkeypatch):
    """Pin the aiify backend to postgresql and isolate the SQLite fallback file."""
    import tools.aiify.db.init_db as init_db

    monkeypatch.setenv("AIIFY_STORAGE_BACKEND", "postgresql")
    monkeypatch.delenv("AIIFY_ALLOW_SQLITE_FALLBACK", raising=False)
    # DB_PATH is resolved at import; redirect the fallback SQLite file to tmp so
    # a fallback never writes into the repo data/ dir.
    monkeypatch.setattr(init_db, "DB_PATH", tmp_path / "aiify_fallback.db")
    return init_db


def _boom(*_a, **_k):
    raise RuntimeError("simulated PostgreSQL outage")


def test_pg_failure_raises_without_flag(pg_env, monkeypatch):
    storage = _storage()
    monkeypatch.setattr(storage, "get_canvas_connection", _boom)

    with pytest.raises(RuntimeError, match="simulated PostgreSQL outage"):
        pg_env.get_connection()


def test_pg_failure_falls_back_with_flag(pg_env, monkeypatch):
    storage = _storage()
    monkeypatch.setattr(storage, "get_canvas_connection", _boom)
    monkeypatch.setenv("AIIFY_ALLOW_SQLITE_FALLBACK", "true")

    conn = pg_env.get_connection()
    try:
        # A real, usable SQLite connection — proves the guarded fallback engaged.
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()


def test_sqlite_backend_unaffected(tmp_path, monkeypatch):
    """When the backend is explicitly sqlite, the PG branch is never taken."""
    import tools.aiify.db.init_db as init_db
    monkeypatch.setenv("AIIFY_STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(init_db, "DB_PATH", tmp_path / "aiify_sqlite.db")
    # get_canvas_connection must NOT be consulted on the sqlite path.
    storage = _storage()
    monkeypatch.setattr(storage, "get_canvas_connection", _boom)
    conn = init_db.get_connection()
    try:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# send-to-kanban — fail-closed HITL gate
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path, monkeypatch):
    """Flask app with the aiify blueprint + a stand-in dashboard auth hook.

    Storage is pinned to SQLite; the aiify canvas connection is served through
    the translating StorageConnection (get_canvas_connection) so the blueprint's
    PostgreSQL-authored ``%s`` queries run unmodified — exactly the shape they
    take against the PG primary in production.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "icdev.db"))
    monkeypatch.setenv("AIIFY_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("AIIFY_DB_PATH", str(tmp_path / "aiify_canvas.db"))

    from flask import Flask, g, request
    import tools.aiify.blueprint as bp
    import tools.aiify.db.init_db as init_db
    from tools.db.storage import get_canvas_connection

    # DB_PATH is resolved at import — realign the module's SQLite path with this
    # test's AIIFY_DB_PATH so init_db() and get_canvas_connection() touch the
    # SAME file.
    monkeypatch.setattr(init_db, "DB_PATH", tmp_path / "aiify_canvas.db")
    monkeypatch.setattr(bp, "_INIT_DONE", False)

    # Serve every _conn() through the translating canvas connection so %s works
    # on the SQLite-backed test DB (production runs this against PG).
    monkeypatch.setattr(bp, "_conn", lambda: get_canvas_connection("AIIFY_DB_PATH"))

    app = Flask(__name__)
    app.secret_key = "test-secret"

    @app.before_request
    def _fake_auth():
        role = request.headers.get("X-Test-Role")
        if role:
            g.current_user = {"id": "u-test", "role": role, "tenant_id": "t-test"}

    app.register_blueprint(bp.aiify_bp)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_roadmap(monkeypatch=None):
    """Insert a scan + one-phase roadmap into the aiify canvas DB."""
    import json
    import tools.aiify.db.init_db as init_db
    from tools.db.storage import get_canvas_connection

    init_db.init_db()  # ensure aiify schema exists in the temp canvas DB

    conn = get_canvas_connection("AIIFY_DB_PATH")
    try:
        conn.execute(
            "INSERT INTO aiify_scans (scan_id, input_type, input_ref, status) "
            "VALUES (%s, %s, %s, %s)",
            (1, "path", "tools/demo", "completed"),
        )
        phases = [{
            "phase_id": "P1",
            "label": "P1 — Quick Wins",
            "opportunities": [{"opportunity_id": 1}],
            "total_effort_days": 3,
        }]
        conn.execute(
            "INSERT INTO aiify_roadmaps (scan_id, roadmap_id, title, phases) "
            "VALUES (%s, %s, %s, %s)",
            (1, "rm-failclosed01", "Demo roadmap", json.dumps(phases)),
        )
        conn.commit()
    finally:
        conn.close()


def _count_tasks(db_path):
    from tools.db.storage import get_connection
    conn = get_connection(db_path=str(db_path))
    try:
        try:
            rows = conn.execute("SELECT id, status FROM kanban_tasks").fetchall()
        except Exception:
            # kanban_tasks never created == zero tasks promoted.
            return []
        return [dict(r) for r in rows]
    finally:
        conn.close()


def test_gate_lookup_error_returns_503_and_creates_zero_tasks(client, tmp_path, monkeypatch):
    import tools.aiify.blueprint as bp
    _seed_roadmap()

    # Simulate the HITL decision lookup throwing (transient datastore error).
    monkeypatch.setattr(bp, "_load_prd_hitl_decisions", _boom)

    resp = client.post(
        "/ai-ify/api/send-to-kanban",
        headers={"X-Test-Role": "admin"},
        json={"roadmap_id": "rm-failclosed01", "phase_id": "all"},
    )
    assert resp.status_code == 503, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body.get("gate_unavailable") is True

    # Fail-closed: not a single task may have been promoted.
    assert _count_tasks(tmp_path / "icdev.db") == []


def test_gate_lookup_error_with_force_override_proceeds_to_quarantine(client, tmp_path, monkeypatch):
    import tools.aiify.blueprint as bp
    _seed_roadmap()

    monkeypatch.setattr(bp, "_load_prd_hitl_decisions", _boom)

    resp = client.post(
        "/ai-ify/api/send-to-kanban",
        headers={"X-Test-Role": "admin"},
        json={"roadmap_id": "rm-failclosed01", "phase_id": "all", "force": True},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json().get("created", 0) > 0

    tasks = _count_tasks(tmp_path / "icdev.db")
    assert tasks, "force override should still promote tasks"
    # Overriding an UNAVAILABLE gate never grants dispatchability: quarantine only.
    assert all(t["status"] == "suggested" for t in tasks), [t["status"] for t in tasks]


def test_gate_healthy_lookup_promotes_normally(client, tmp_path):
    """Sanity: with the gate reachable (no approvals), promotion still works and
    tasks land in 'suggested' — proving the 503 is specific to lookup failure."""
    _seed_roadmap()
    resp = client.post(
        "/ai-ify/api/send-to-kanban",
        headers={"X-Test-Role": "admin"},
        json={"roadmap_id": "rm-failclosed01", "phase_id": "all"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    tasks = _count_tasks(tmp_path / "icdev.db")
    assert tasks
    assert all(t["status"] == "suggested" for t in tasks)
