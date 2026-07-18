# CUI // SP-CTI
"""Perf/hygiene regression tests for the Pipeline Design Canvas (pdx-perf-01).

Covers four independent fixes on the pipeline save/twin path:

  1. SAVE-PATH conditional hooks — a PUT that does NOT change graph_json skips
     ALL post-save side-effects (KG reindex, auto-snapshot, ...); a changed graph
     still runs them; an ImportError inside a hook does not 500 the request.
  2. SNAPSHOT RETENTION — take_snapshot de-dups against the latest snapshot and
     caps AUTO-labeled snapshots at 20 per pipeline while preserving all
     manual/user-labeled snapshots; list_snapshots honors a LIMIT.
  3. TWIN N+1 — latest_snapshots_by_pipeline returns <=2 snapshots per pipeline
     from a single windowed query.
  4. API PAGINATION — GET /api/pipelines validates + clamps limit/offset.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask  # noqa: E402


# ══════════════════════════════════════════════════════════════════════════════
# Part A — twin.py DB functions against a real in-memory SQLite (wrapped in the
# production StorageConnection so PG-native %s placeholders translate to ?).
# ══════════════════════════════════════════════════════════════════════════════

_TWIN_SCHEMA = """
CREATE TABLE pipelines (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    graph_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}'
);
CREATE TABLE pdc_snapshots (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT,
    label TEXT,
    graph_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    node_count INTEGER DEFAULT 0,
    edge_count INTEGER DEFAULT 0,
    created_by TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


class _NoCloseConn:
    """Delegates to a StorageConnection but makes close() a no-op so the shared
    in-memory DB survives across the many _get_connection() calls twin makes."""

    def __init__(self, sconn):
        self._s = sconn

    def execute(self, sql, params=None):
        return self._s.execute(sql, params)

    def commit(self):
        return self._s.commit()

    def rollback(self):
        return self._s.rollback()

    def cursor(self):
        return self._s.cursor()

    def close(self):
        pass


@pytest.fixture()
def twin_conn():
    """In-memory SQLite with the twin schema, wired into tools.pipeline.twin."""
    from tools.db.storage import StorageConnection

    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript(_TWIN_SCHEMA)
    sconn = StorageConnection(raw, "sqlite")
    holder = _NoCloseConn(sconn)

    with patch("tools.pipeline.twin._get_connection", return_value=holder):
        yield raw  # hand back the raw conn for direct assertions


def _make_pipeline(raw, pipe_id, graph):
    raw.execute(
        "INSERT INTO pipelines (id, name, graph_json) VALUES (?,?,?)",
        (pipe_id, pipe_id, json.dumps(graph)),
    )
    raw.commit()


def _set_pipeline_graph(raw, pipe_id, graph):
    raw.execute(
        "UPDATE pipelines SET graph_json=? WHERE id=?",
        (json.dumps(graph), pipe_id),
    )
    raw.commit()


def _graph(n):
    """Distinct graph payload keyed by n."""
    return {"nodes": [{"id": f"n{n}", "type": "build-runner"}], "edges": []}


def _auto_count(raw, pipe_id):
    return raw.execute(
        "SELECT COUNT(*) FROM pdc_snapshots WHERE pipeline_id=? AND label LIKE 'auto-%'",
        (pipe_id,),
    ).fetchone()[0]


def _manual_count(raw, pipe_id):
    return raw.execute(
        "SELECT COUNT(*) FROM pdc_snapshots WHERE pipeline_id=? AND label NOT LIKE 'auto-%'",
        (pipe_id,),
    ).fetchone()[0]


def test_take_snapshot_dedup_skips_identical_graph(twin_conn):
    """A second snapshot of an unchanged graph writes no new row."""
    from tools.pipeline.twin import take_snapshot

    _make_pipeline(twin_conn, "p-dedup", _graph(1))
    s1 = take_snapshot("p-dedup", label="auto-save-2026-07-18")
    assert not s1.get("skipped")
    s2 = take_snapshot("p-dedup", label="auto-save-2026-07-18")
    assert s2.get("skipped") is True
    assert s2["id"] == s1["id"]
    total = twin_conn.execute(
        "SELECT COUNT(*) FROM pdc_snapshots WHERE pipeline_id=?", ("p-dedup",)
    ).fetchone()[0]
    assert total == 1


def test_take_snapshot_changed_graph_creates_new_row(twin_conn):
    from tools.pipeline.twin import take_snapshot

    _make_pipeline(twin_conn, "p-chg", _graph(1))
    take_snapshot("p-chg", label="auto-save-2026-07-18")
    _set_pipeline_graph(twin_conn, "p-chg", _graph(2))
    s2 = take_snapshot("p-chg", label="auto-save-2026-07-18")
    assert not s2.get("skipped")
    total = twin_conn.execute(
        "SELECT COUNT(*) FROM pdc_snapshots WHERE pipeline_id=?", ("p-chg",)
    ).fetchone()[0]
    assert total == 2


def test_auto_snapshot_retention_caps_at_20_and_preserves_manual(twin_conn):
    """25 auto snapshots -> 20 retained; interspersed manual snapshots all survive."""
    from tools.pipeline.twin import take_snapshot

    _make_pipeline(twin_conn, "p-ret", _graph(0))
    manual_expected = 0
    for i in range(25):
        _set_pipeline_graph(twin_conn, "p-ret", _graph(i + 1))
        take_snapshot("p-ret", label="auto-save-2026-07-18")
        # Every 8th iteration, drop a manual (user-labeled) snapshot too.
        if i % 8 == 0:
            _set_pipeline_graph(twin_conn, "p-ret", _graph(1000 + i))
            take_snapshot("p-ret", label=f"baseline-{i}")
            manual_expected += 1

    assert _auto_count(twin_conn, "p-ret") == 20, "auto snapshots must be capped at 20"
    assert _manual_count(twin_conn, "p-ret") == manual_expected, "manual snapshots must survive"


def test_list_snapshots_honors_limit(twin_conn):
    from tools.pipeline.twin import list_snapshots

    _make_pipeline(twin_conn, "p-list", _graph(0))
    for i in range(5):
        _set_pipeline_graph(twin_conn, "p-list", _graph(i + 1))
        from tools.pipeline.twin import take_snapshot
        take_snapshot("p-list", label=f"manual-{i}")
    assert len(list_snapshots("p-list", limit=2)) == 2
    assert len(list_snapshots("p-list")) == 5  # under the default cap of 100


def test_list_snapshots_default_limit_is_100(twin_conn):
    """The default limit is 100 (verified via SQL text, not by inserting 100 rows)."""
    from tools.pipeline import twin

    assert twin._DEFAULT_SNAPSHOT_LIST_LIMIT == 100


def test_latest_snapshots_by_pipeline_returns_at_most_two(twin_conn):
    """Windowed query yields <=2 newest snapshots per pipeline, newest first."""
    from tools.pipeline.twin import take_snapshot, latest_snapshots_by_pipeline

    for pid in ("w-a", "w-b"):
        _make_pipeline(twin_conn, pid, _graph(0))
        for i in range(3):
            _set_pipeline_graph(twin_conn, pid, _graph(i + 1))
            take_snapshot(pid, label=f"manual-{i}")

    result = latest_snapshots_by_pipeline(per_pipeline=2)
    assert set(result.keys()) == {"w-a", "w-b"}
    for pid in ("w-a", "w-b"):
        snaps = result[pid]
        assert len(snaps) == 2, f"{pid} should return exactly 2 snapshots"
        assert snaps[0]["created_at"] >= snaps[1]["created_at"], "newest first"


# ══════════════════════════════════════════════════════════════════════════════
# Part B — blueprint PUT save-path (conditional + guarded hooks) and pagination.
# ══════════════════════════════════════════════════════════════════════════════

def _make_app(existing: dict) -> tuple[Flask, MagicMock]:
    os.environ.setdefault("ICDEV_PIPELINE_ENABLED", "true")
    from tools.pipeline.blueprint import create_pipeline_blueprint

    bp = create_pipeline_blueprint()
    if bp is None:
        raise RuntimeError("Pipeline blueprint disabled — set ICDEV_PIPELINE_ENABLED=true")
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.secret_key = "test"
    app.register_blueprint(bp, url_prefix="/devops")

    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = dict(existing)
    cur.fetchall.return_value = []
    conn.execute.return_value = cur
    return app, conn


def _login(client, role="developer"):
    with client.session_transaction() as sess:
        sess["user_id"] = "test-user"
        sess["role"] = role


def _put(client, pipe_id, payload):
    return client.put(
        f"/devops/api/pipelines/{pipe_id}",
        data=json.dumps(payload),
        content_type="application/json",
    )


def test_unchanged_graph_put_skips_snapshot_and_hooks():
    """PUT with graph_json byte-equivalent to stored value runs NO post-save hooks."""
    pipe_id = str(uuid.uuid4())
    existing = {"id": pipe_id, "name": "X", "graph_json": '{"nodes": [], "edges": []}'}
    app, conn = _make_app(existing)
    snap = MagicMock()
    reindex = MagicMock()
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"), \
         patch("tools.pipeline.twin.take_snapshot", snap), \
         patch("tools.knowledge_graph.canvas_ask.reindex_canvas_on_save", reindex):
        client = app.test_client()
        _login(client)
        # Same graph (differently spaced) + a metadata change so there IS an update.
        resp = _put(client, pipe_id, {"name": "X2", "graph_json": '{"nodes":[],"edges":[]}'})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    snap.assert_not_called()
    reindex.assert_not_called()


def test_metadata_only_put_skips_hooks():
    """PUT with no graph_json field at all is a metadata update — no hooks."""
    pipe_id = str(uuid.uuid4())
    existing = {"id": pipe_id, "name": "X", "graph_json": '{"nodes": [], "edges": []}'}
    app, conn = _make_app(existing)
    snap = MagicMock()
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"), \
         patch("tools.pipeline.twin.take_snapshot", snap):
        client = app.test_client()
        _login(client)
        resp = _put(client, pipe_id, {"description": "new desc"})
    assert resp.status_code == 200
    snap.assert_not_called()


def test_changed_graph_put_still_snapshots():
    pipe_id = str(uuid.uuid4())
    existing = {"id": pipe_id, "name": "X", "graph_json": "{}"}
    app, conn = _make_app(existing)
    snap = MagicMock()
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"), \
         patch("tools.pipeline.twin.take_snapshot", snap), \
         patch("tools.knowledge_graph.canvas_ask.reindex_canvas_on_save"):
        client = app.test_client()
        _login(client)
        resp = _put(client, pipe_id, {"graph_json": '{"nodes":[{"id":"n1"}],"edges":[]}'})
    assert resp.status_code == 200
    snap.assert_called_once()


def test_importerror_in_canvas_ask_does_not_500_the_put():
    """A hook raising ImportError must be swallowed — the PUT still returns 200."""
    pipe_id = str(uuid.uuid4())
    existing = {"id": pipe_id, "name": "X", "graph_json": "{}"}
    app, conn = _make_app(existing)
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"), \
         patch("tools.pipeline.twin.take_snapshot", MagicMock()), \
         patch(
             "tools.knowledge_graph.canvas_ask.reindex_canvas_on_save",
             side_effect=ImportError("simulated import failure"),
         ):
        client = app.test_client()
        _login(client)
        resp = _put(client, pipe_id, {"graph_json": '{"nodes":[{"id":"n9"}],"edges":[]}'})
    assert resp.status_code == 200, resp.get_data(as_text=True)


def test_put_unknown_pipeline_returns_404():
    pipe_id = str(uuid.uuid4())
    app, _ = _make_app({"id": pipe_id, "name": "X", "graph_json": "{}"})
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = None  # pipeline does not exist
    conn.execute.return_value = cur
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"):
        client = app.test_client()
        _login(client)
        resp = _put(client, pipe_id, {"name": "Y"})
    assert resp.status_code == 404


# ── Pagination ────────────────────────────────────────────────────────────────

def test_list_pipelines_respects_limit_and_offset():
    app, conn = _make_app({"id": "x", "name": "x", "graph_json": "{}"})
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn):
        client = app.test_client()
        _login(client)
        resp = client.get("/devops/api/pipelines?limit=5&offset=10")
    assert resp.status_code == 200
    select_calls = [
        c for c in conn.execute.call_args_list
        if "FROM pipelines" in str(c.args[0]) and "LIMIT" in str(c.args[0])
    ]
    assert select_calls, "list should issue a LIMIT/OFFSET SELECT"
    params = select_calls[0].args[1]
    assert tuple(params) == (5, 10)


def test_list_pipelines_clamps_limit_to_max_200():
    app, conn = _make_app({"id": "x", "name": "x", "graph_json": "{}"})
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn):
        client = app.test_client()
        _login(client)
        resp = client.get("/devops/api/pipelines?limit=9999")
    assert resp.status_code == 200
    select_calls = [
        c for c in conn.execute.call_args_list
        if "FROM pipelines" in str(c.args[0]) and "LIMIT" in str(c.args[0])
    ]
    assert select_calls[0].args[1][0] == 200


def test_list_pipelines_rejects_garbage_limit():
    app, conn = _make_app({"id": "x", "name": "x", "graph_json": "{}"})
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn):
        client = app.test_client()
        _login(client)
        resp = client.get("/devops/api/pipelines?limit=abc")
    assert resp.status_code == 400


def test_list_pipelines_rejects_garbage_offset():
    app, conn = _make_app({"id": "x", "name": "x", "graph_json": "{}"})
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn):
        client = app.test_client()
        _login(client)
        resp = client.get("/devops/api/pipelines?offset=xyz")
    assert resp.status_code == 400
