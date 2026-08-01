# CUI // SP-CTI
"""Security tests for pdx-sec-01 — graph_json write-boundary validation + safe render.

Defect (stored XSS, P0): POST/PUT /devops/api/pipelines accepted graph_json as an
arbitrary string (only a 5 MB size check), and pc_edit_canvas rendered it via
``{{ graph_json | safe }}``. A node label containing ``</script><script>...`` would
break out of the bootstrap <script> block and execute for every viewer.

Fixes verified here:
  1. Write boundary: create/update json.loads() the payload, require a dict with
     nodes[]/edges[] lists, store the canonical json.dumps(); invalid -> HTTP 422.
  2. Render: canvas.html uses ``{{ graph_json | tojson }}`` so the stored string is
     emitted as a safely-escaped JS string literal (angle brackets -> \\u003c/\\u003e),
     defeating the script-closing breakout.
  3. Corrupt stored graph_json on read routes returns HTTP 422 (not a 500).
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _row(**kw):
    """Plain dict — row_to_dict accepts these (has .keys())."""
    return kw


def _mock_conn(fetchone=None, fetchall=None):
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = fetchone
    cur.fetchall.return_value = fetchall if fetchall is not None else []
    conn.execute.return_value = cur
    return conn


@pytest.fixture(scope="module")
def app():
    import os

    os.environ.setdefault("ICDEV_PIPELINE_ENABLED", "true")
    with patch("tools.pipeline.blueprint.init_db"):
        from tools.pipeline.blueprint import create_pipeline_blueprint

        flask_app = Flask(__name__)
        flask_app.secret_key = "test-secret-key"
        flask_app.config["TESTING"] = True

        @flask_app.context_processor
        def _inject_base_ctx():
            return {"ROLE_VIEWS": {}, "current_role": None, "current_user": None}

        bp = create_pipeline_blueprint()
        assert bp is not None, "ICDEV_PIPELINE_ENABLED must be true"
        flask_app.register_blueprint(bp, url_prefix="/devops")
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client):
    with client.session_transaction() as sess:
        sess["user_id"] = "test-user"
        # pdx-sec-03: pipeline write routes now require a write-tier role
        # (developer/pm/isso/admin). An authorized editor is a developer.
        sess["role"] = "developer"


# ── 1. write boundary — CREATE ────────────────────────────────────────────────

def test_create_rejects_non_json_graph(client):
    """POST with a non-JSON graph_json string returns 422, no INSERT."""
    _login(client)
    conn = _mock_conn()
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"):
        resp = client.post(
            "/devops/api/pipelines",
            data=json.dumps({"name": "X", "graph_json": "not json at all"}),
            content_type="application/json",
        )
    assert resp.status_code == 422, resp.get_data(as_text=True)
    insert_calls = [c for c in conn.execute.call_args_list
                    if "INSERT INTO pipelines" in str(c.args[0])]
    assert not insert_calls, "invalid payload must not reach the INSERT"


def test_create_canonicalizes_valid_graph(client):
    """POST with a valid graph stores the canonical json.dumps()."""
    _login(client)
    conn = _mock_conn()
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"), \
         patch("tools.knowledge_graph.canvas_ask.reindex_canvas_on_save"):
        resp = client.post(
            "/devops/api/pipelines",
            data=json.dumps({"name": "X", "graph_json": {"nodes": [], "edges": []}}),
            content_type="application/json",
        )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    insert_calls = [c for c in conn.execute.call_args_list
                    if "INSERT INTO pipelines" in str(c.args[0])]
    assert insert_calls
    stored_graph = insert_calls[0].args[1][3]  # 4th column = graph_json
    assert json.loads(stored_graph) == {"nodes": [], "edges": []}


# ── 2. write boundary — UPDATE (PUT) ──────────────────────────────────────────

def test_put_rejects_non_json_graph(client):
    """PUT with a non-JSON graph_json string returns 422, no UPDATE."""
    pipe_id = str(uuid.uuid4())
    _login(client)
    conn = _mock_conn(fetchone=_row(id=pipe_id, name="X", graph_json="{}"))
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"):
        resp = client.put(
            f"/devops/api/pipelines/{pipe_id}",
            data=json.dumps({"graph_json": "<not json>"}),
            content_type="application/json",
        )
    assert resp.status_code == 422, resp.get_data(as_text=True)
    update_calls = [c for c in conn.execute.call_args_list
                    if "UPDATE pipelines" in str(c.args[0])]
    assert not update_calls, "invalid payload must not reach the UPDATE"


def test_put_rejects_graph_missing_edges(client):
    """PUT with a dict lacking edges[] returns 422 (contract: nodes[] AND edges[])."""
    pipe_id = str(uuid.uuid4())
    _login(client)
    conn = _mock_conn(fetchone=_row(id=pipe_id, name="X", graph_json="{}"))
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"):
        resp = client.put(
            f"/devops/api/pipelines/{pipe_id}",
            data=json.dumps({"graph_json": '{"nodes":[]}'}),
            content_type="application/json",
        )
    assert resp.status_code == 422, resp.get_data(as_text=True)


# ── 3. safe render — stored </script> label is escaped ────────────────────────

def test_stored_script_label_renders_escaped(client):
    """A stored </script><script> breakout label must render escaped in the canvas.

    With ``{{ graph_json | tojson }}`` the angle brackets are emitted as \\u003c /
    \\u003e, so the attacker's raw script-closing sequence never appears verbatim.
    """
    pipe_id = str(uuid.uuid4())
    _login(client)
    malicious = json.dumps({
        "nodes": [{"id": "n1", "label": "</script><script>alert(1)</script>"}],
        "edges": [],
    })
    row = _row(
        id=pipe_id, name="Evil Pipeline", description="", graph_json=malicious,
        classification="public", target_csp="generic",
    )
    conn = _mock_conn(fetchone=row)
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn):
        resp = client.get(f"/devops/canvas/{pipe_id}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.data
    # The attacker's raw breakout sequence must NOT be present verbatim.
    assert b"</script><script>alert(1)</script>" not in body
    assert b"<script>alert(1)" not in body
    # Proof the payload was escaped rather than dropped: the \\u003c marker is present.
    assert b"\\u003c" in body


# ── 4. corrupt stored graph on a read route -> 422 (not 500) ──────────────────

def test_analyze_corrupt_graph_returns_422(client):
    """A pre-existing corrupt graph_json row yields 422 on analyze, not an unhandled 500."""
    pipe_id = str(uuid.uuid4())
    _login(client)
    conn = _mock_conn(fetchone=_row(graph_json="}{ this is not json"))
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn):
        resp = client.post(
            f"/devops/api/pipelines/{pipe_id}/analyze",
            data=json.dumps({"analysis_type": "security_coverage"}),
            content_type="application/json",
        )
    assert resp.status_code == 422, resp.get_data(as_text=True)
    assert b"corrupt graph" in resp.data


def test_twin_snapshot_corrupt_graph_returns_422(client):
    """take_snapshot on a corrupt graph_json surfaces as HTTP 422 (CorruptGraphError)."""
    pipe_id = str(uuid.uuid4())
    _login(client)
    conn = _mock_conn(fetchone=_row(graph_json="not json", name="X"))
    with patch("tools.pipeline.twin._get_connection", return_value=conn):
        resp = client.post(
            f"/devops/api/pipelines/{pipe_id}/twin/snapshot",
            data=json.dumps({}),
            content_type="application/json",
        )
    assert resp.status_code == 422, resp.get_data(as_text=True)
    assert b"corrupt graph" in resp.data
