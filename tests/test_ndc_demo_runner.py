# CUI // SP-CTI
"""Route tests for the NDC Demo Runner persistence path fixed in ndc-sql-02.

The demo-runner routes previously wrote to ``showcase_demo_runs`` with raw
``sqlite3.connect(... data/icdev.db)`` using ``%s`` placeholders. Because
sqlite3's paramstyle is ``?``, every statement raised
``sqlite3.ProgrammingError`` which the bare ``except Exception`` swallowed:
``POST /api/demo-run`` reported success while persisting nothing, and
``GET /api/demo-runs`` history was always empty. The fix routes persistence
through the platform storage layer (``tools.db.storage.get_connection``), which
translates ``%s`` to the backend's placeholder style.

These tests drive the Flask test client over ``create_network_blueprint()``
with the platform storage layer pinned to a temp SQLite file:

* (a) POST a demo run -> 200 / ok:true, and the row is readable back through
  the same storage layer.
* (b) GET /api/demo-runs returns the inserted run.
* (c) failure path: when ``get_connection`` raises, the response surfaces
  ``ok:false`` / ``error`` with a 5xx status instead of faking success.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def demo_ctx(tmp_path, monkeypatch):
    """Build the Network blueprint with the platform storage layer on temp SQLite.

    ``showcase_demo_runs`` lives in the MAIN platform DB, so we pin
    ``tools.db.storage`` to a throwaway SQLite file via ICDEV_DB_PATH and force
    the sqlite backend, then stub out the real demo engine so the route does not
    depend on the full NDC stack.
    """
    monkeypatch.setenv("ICDEV_NETWORK_ENABLED", "true")
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    db_file = tmp_path / "platform_demo_test.db"
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_file))

    # Stub the demo engine so the route exercises persistence, not the full run.
    import tools.ndc.demo_runner as demo_engine

    def _fake_run(scenarios=None, audience="exec"):
        return {
            "status": "passed",
            "elapsed_ms": 42,
            "scenarios_passed": 3,
            "scenarios_total": 3,
            "results": {"A": "ok", "B": "ok", "C": "ok"},
        }

    monkeypatch.setattr(demo_engine, "run_ndc_demo", _fake_run)

    from flask import Flask

    from tools.network.blueprint import create_network_blueprint

    bp = create_network_blueprint()
    assert bp is not None, "create_network_blueprint returned None (disabled?)"

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(bp)

    return app


def _read_runs():
    """Read showcase_demo_runs directly through the storage layer."""
    from tools.db.storage import get_connection

    conn = get_connection()
    conn.set_security_context(None)
    try:
        rows = conn.execute(
            "SELECT run_id, audience, status, scenarios_passed, scenarios_total "
            "FROM showcase_demo_runs ORDER BY created_at DESC"
        ).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        if hasattr(row, "keys"):
            out.append(dict(row))
        else:
            out.append({
                "run_id": row[0], "audience": row[1], "status": row[2],
                "scenarios_passed": row[3], "scenarios_total": row[4],
            })
    return out


def test_post_demo_run_persists(demo_ctx):
    """(a) POST /api/demo-run returns ok:true and actually writes a row."""
    app = demo_ctx
    client = app.test_client()

    resp = client.post("/api/demo-run", json={"audience": "exec", "scenarios": "all"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is True, body
    run_id = body["run_id"]
    assert run_id

    rows = _read_runs()
    assert len(rows) == 1, f"expected exactly one persisted run, got {rows}"
    assert rows[0]["run_id"] == run_id
    assert rows[0]["audience"] == "exec"
    assert rows[0]["status"] == "passed"


def test_get_demo_runs_returns_inserted(demo_ctx):
    """(b) GET /api/demo-runs returns the run persisted by a prior POST."""
    app = demo_ctx
    client = app.test_client()

    post = client.post("/api/demo-run", json={"audience": "tech", "scenarios": "all"})
    assert post.status_code == 200, post.get_data(as_text=True)
    run_id = post.get_json()["run_id"]

    resp = client.get("/api/demo-runs")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    runs = resp.get_json()
    assert isinstance(runs, list), runs
    assert any(r["run_id"] == run_id for r in runs), runs
    match = next(r for r in runs if r["run_id"] == run_id)
    assert match["audience"] == "tech"


def test_post_demo_run_persistence_failure_surfaced(demo_ctx, monkeypatch):
    """(c) When persistence fails, the response surfaces ok:false / error."""
    app = demo_ctx
    client = app.test_client()

    import importlib

    storage = importlib.import_module("tools.db.storage")

    def _boom(*_a, **_k):
        raise RuntimeError("simulated storage outage")

    monkeypatch.setattr(storage, "get_connection", _boom)

    resp = client.post("/api/demo-run", json={"audience": "exec", "scenarios": "all"})
    assert resp.status_code >= 500, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is False, body
    assert body.get("error"), body
    assert "simulated storage outage" in body["error"]


def test_get_demo_runs_read_failure_surfaced(demo_ctx, monkeypatch):
    """(c') A failing read returns an explicit error, not silently-empty history."""
    app = demo_ctx
    client = app.test_client()

    import importlib

    storage = importlib.import_module("tools.db.storage")

    def _boom(*_a, **_k):
        raise RuntimeError("simulated read outage")

    monkeypatch.setattr(storage, "get_connection", _boom)

    resp = client.get("/api/demo-runs")
    assert resp.status_code >= 500, resp.get_data(as_text=True)
    body = resp.get_json()
    assert isinstance(body, dict), body
    assert body["ok"] is False, body
    assert "simulated read outage" in body.get("error", "")
