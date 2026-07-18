# CUI // SP-CTI
"""API contract hygiene tests for the Observability Design Canvas (obx-hyg-02).

Covers:
  1. Design create still returns 201 even when the post-commit KG reindex hook
     raises (the design was already committed — a hook failure must not 500).
  2. Design update/delete return 404 for unknown IDs and success for known ones.
  3. The governance endpoint never leaks its DB connection: when the governance
     computation raises, the route returns a JSON error AND closes the conn.
  4. runbooks.py / sops.py filtered queries work through the wrapped SQLite
     StorageConnection (proving %s -> ? translation on the fallback path), and
     a raw sqlite3 connection rejects %s while the wrapper accepts it.

Self-contained: builds its own Flask app + blueprint per test, uses importlib +
setattr for shim-aware monkeypatching, and does not touch conftest.
"""
from __future__ import annotations

import importlib
import sqlite3
import uuid

import pytest
from flask import Flask


# ── Helpers ──────────────────────────────────────────────────────────────────


def _build_client():
    """Create a fresh Flask app with the ODC blueprint and an authed session."""
    from tools.observability_canvas.blueprint import create_observability_blueprint

    bp = create_observability_blueprint()
    assert bp is not None, "ODC blueprint disabled — set ICDEV_OBSERVABILITY_ENABLED=true"
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(bp, url_prefix="/observability")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "tester"
    return client


def _init_db_mod():
    return importlib.import_module("tools.observability_canvas.db.init_db")


# ── (a) create-path post-commit hook is guarded ──────────────────────────────


def test_create_succeeds_even_when_reindex_hook_raises(monkeypatch):
    # Shim-aware: patch the reindex hook on the module the blueprint imports from.
    canvas_ask = importlib.import_module("tools.knowledge_graph.canvas_ask")

    def _boom(_canvas):
        raise RuntimeError("KG reindex intentionally failed")

    monkeypatch.setattr(canvas_ask, "reindex_canvas_on_save", _boom)

    client = _build_client()
    resp = client.post(
        "/observability/api/designs",
        json={"name": f"hooktest-{uuid.uuid4().hex[:6]}"},
    )
    # The design was committed before the hook fired — must be 201, not 500.
    assert resp.status_code == 201, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["id"]
    # The design is actually persisted.
    gc = _init_db_mod().get_connection
    conn = gc()
    try:
        row = conn.execute(
            "SELECT id FROM observability_designs WHERE id=%s", (body["id"],)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None


# ── (c) update / delete 404 for unknown ids ──────────────────────────────────


def test_update_unknown_id_returns_404_known_succeeds():
    client = _build_client()
    created = client.post(
        "/observability/api/designs", json={"name": "upd-known"}
    ).get_json()
    design_id = created["id"]

    ok = client.put(
        f"/observability/api/designs/{design_id}",
        json={"name": "upd-known-renamed", "graph_json": "{}"},
    )
    assert ok.status_code == 200
    assert ok.get_json().get("updated") is True

    missing = client.put(
        f"/observability/api/designs/does-not-exist-{uuid.uuid4().hex}",
        json={"name": "nope", "graph_json": "{}"},
    )
    assert missing.status_code == 404
    assert "error" in missing.get_json()


def test_delete_unknown_id_returns_404_known_succeeds():
    client = _build_client()
    created = client.post(
        "/observability/api/designs", json={"name": "del-known"}
    ).get_json()
    design_id = created["id"]

    ok = client.delete(f"/observability/api/designs/{design_id}")
    assert ok.status_code == 200
    assert ok.get_json().get("deleted") is True

    missing = client.delete(
        f"/observability/api/designs/does-not-exist-{uuid.uuid4().hex}"
    )
    assert missing.status_code == 404
    assert "error" in missing.get_json()


# ── (b) governance endpoint must not leak the connection ─────────────────────


class _TrackConn:
    """Delegating wrapper that counts explicit close() calls."""

    close_count = 0

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def close(self):
        type(self).close_count += 1
        return self._real.close()

    @property
    def row_factory(self):
        return self._real.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._real.row_factory = value

    def __enter__(self):
        self._real.__enter__()
        return self

    def __exit__(self, *exc):
        return self._real.__exit__(*exc)


def test_governance_error_returns_json_and_closes_connection(monkeypatch):
    init_db = _init_db_mod()
    orig_gc = init_db.get_connection

    # Seed a design whose graph_json parses to a NON-object (an int), which makes
    # the closure _compute_odc_governance raise (int has no .get) — the closure
    # itself is not monkeypatchable, so we induce the raise via the payload.
    design_id = f"gov-bad-{uuid.uuid4().hex[:8]}"
    seed = orig_gc()
    try:
        seed.execute(
            "INSERT INTO observability_designs (id, name, graph_json) VALUES (%s,%s,%s)",
            (design_id, "gov-bad", "123"),
        )
        seed.commit()
    finally:
        seed.close()

    # Patch get_connection BEFORE building the blueprint so the route's closure
    # binds the tracking wrapper.
    def _tracking_gc():
        return _TrackConn(orig_gc())

    monkeypatch.setattr(init_db, "get_connection", _tracking_gc)

    client = _build_client()
    _TrackConn.close_count = 0  # ignore closes from init_db during app build

    try:
        resp = client.post(f"/observability/api/designs/{design_id}/governance")
        assert resp.status_code == 500, resp.get_data(as_text=True)
        assert "error" in resp.get_json()
        # The connection opened by the governance route was released despite the raise.
        assert _TrackConn.close_count >= 1
    finally:
        # Remove the intentionally-malformed design so it can't pollute the
        # shared canvas DB for other tests (e.g. replay_verify scans all designs).
        cleanup = orig_gc()
        try:
            cleanup.execute(
                "DELETE FROM observability_designs WHERE id=%s", (design_id,)
            )
            cleanup.commit()
        finally:
            cleanup.close()


# ── (d) runbooks / sops filtered queries + %s translation ────────────────────


def test_runbooks_crud_via_wrapped_sqlite_storage_path():
    from tools.observability_canvas import runbooks

    _init_db_mod().init_db()  # ensure schema + seeds present
    rb = runbooks.create_runbook(
        {"title": "hyg-rb", "category": "hygiene-cat", "severity": "high"}
    )
    assert rb and rb["id"]

    # Filtered read exercises the %s placeholders normalized in get_all_runbooks.
    both = runbooks.get_all_runbooks(category="hygiene-cat", severity="high")
    assert any(r["id"] == rb["id"] for r in both)
    by_cat = runbooks.get_all_runbooks(category="hygiene-cat")
    assert any(r["id"] == rb["id"] for r in by_cat)
    by_sev = runbooks.get_all_runbooks(severity="high")
    assert any(r["id"] == rb["id"] for r in by_sev)

    assert runbooks.delete_runbook(rb["id"]) is True


def test_sops_crud_via_wrapped_sqlite_storage_path():
    from tools.observability_canvas import sops

    _init_db_mod().init_db()
    sop = sops.create_sop({"title": "hyg-sop", "sop_type": "hygiene-type"})
    assert sop and sop["id"]

    by_type = sops.get_all_sops(sop_type="hygiene-type")
    assert any(s["id"] == sop["id"] for s in by_type)
    by_type_status = sops.get_all_sops(sop_type="hygiene-type", approval_status="draft")
    assert any(s["id"] == sop["id"] for s in by_type_status)
    by_status = sops.get_all_sops(approval_status="draft")
    assert any(s["id"] == sop["id"] for s in by_status)

    assert sops.delete_sop(sop["id"]) is True


def test_raw_sqlite_rejects_percent_s_but_wrapper_translates(tmp_path):
    """Proves the fallback fix: raw sqlite3 raises on %s; StorageConnection wraps it."""
    from tools.db.storage import StorageConnection

    db_file = tmp_path / "raw_fallback.db"

    # Raw sqlite3 does NOT understand %s — this is the pre-existing bug.
    raw = sqlite3.connect(str(db_file))
    raw.execute("CREATE TABLE t (id TEXT PRIMARY KEY, name TEXT)")
    with pytest.raises(sqlite3.OperationalError):
        raw.execute("INSERT INTO t (id, name) VALUES (%s, %s)", ("a", "alpha"))
    raw.close()

    # The StorageConnection(sqlite) wrapper translates %s -> ? so it works.
    raw2 = sqlite3.connect(str(db_file))
    raw2.row_factory = sqlite3.Row
    wrapped = StorageConnection(raw2, "sqlite")
    wrapped.execute("INSERT INTO t (id, name) VALUES (%s, %s)", ("a", "alpha"))
    wrapped.commit()
    row = wrapped.execute("SELECT name FROM t WHERE id=%s", ("a",)).fetchone()
    assert row["name"] == "alpha"
    wrapped.close()
