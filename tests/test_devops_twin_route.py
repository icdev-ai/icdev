# CUI // SP-CTI
"""5 route tests for GET /devops/twin and GET /devops/twin/<pipe_id>/delta."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _row(**kw):
    """Plain dict — row_to_dict accepts these (has .keys())."""
    return kw


def _mock_conn(fetchall=None, fetchone=None):
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = fetchall if fetchall is not None else []
    cur.fetchone.return_value = fetchone
    conn.execute.return_value = cur
    return conn


def _delta(added_nodes=None, removed_nodes=None, modified_nodes=None,
           added_edges=None, removed_edges=None, modified_edges=None):
    return {
        "snapshot_a": "snap-A",
        "snapshot_b": "snap-B",
        "nodes": {
            "added": added_nodes or [],
            "removed": removed_nodes or [],
            "modified": modified_nodes or [],
        },
        "edges": {
            "added": added_edges or [],
            "removed": removed_edges or [],
            "modified": modified_edges or [],
        },
    }


# ── app fixture ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def app():
    """Minimal Flask app with the pipeline blueprint registered."""
    with patch("tools.pipeline.blueprint.init_db"):
        from tools.pipeline.blueprint import create_pipeline_blueprint

        flask_app = Flask(__name__)
        flask_app.secret_key = "test-secret-key"
        flask_app.config["TESTING"] = True

        @flask_app.context_processor
        def _inject_base_ctx():
            return {"ROLE_VIEWS": {}, "current_role": None, "current_user": None}

        bp = create_pipeline_blueprint()
        flask_app.register_blueprint(bp, url_prefix="/devops")
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client):
    with client.session_transaction() as sess:
        sess["user_id"] = "test-user"


# ── 1. /devops/twin lists pipeline names ─────────────────────────────────────

def test_twin_list_shows_pipeline_names(client):
    """GET /devops/twin returns 200 and renders pipeline names."""
    _login(client)
    pipes = [_row(id="p1", name="Alpha Pipeline", description="", classification="CUI", updated_at="2026-01-01")]
    snaps = [_row(id="s1", label="snap-1", node_count=3, created_at="2026-01-01T00:00:00")]
    with patch("tools.pipeline.blueprint.get_connection", return_value=_mock_conn(fetchall=pipes)), \
         patch("tools.pipeline.twin.list_snapshots", return_value=snaps):
        resp = client.get("/devops/twin")
    assert resp.status_code == 200
    assert b"Alpha Pipeline" in resp.data


# ── 2. /devops/twin with no pipelines still returns 200 ──────────────────────

def test_twin_list_empty_returns_200(client):
    """GET /devops/twin with an empty pipeline table returns 200."""
    _login(client)
    with patch("tools.pipeline.blueprint.get_connection", return_value=_mock_conn(fetchall=[])):
        resp = client.get("/devops/twin")
    assert resp.status_code == 200


# ── 3. /devops/twin/<id>/delta renders diff view ─────────────────────────────

def test_twin_delta_renders_diff(client):
    """GET /devops/twin/<id>/delta?from=A&to=B returns 200 with gate info."""
    _login(client)
    pipe = _row(id="p1", name="Alpha Pipeline")
    with patch("tools.pipeline.blueprint.get_connection", return_value=_mock_conn(fetchone=pipe)), \
         patch("tools.pipeline.delta.compute_delta", return_value=_delta(
             added_nodes=[{"id": "n2", "type": "scan-semgrep"}]
         )):
        resp = client.get("/devops/twin/p1/delta?from=snap-A&to=snap-B")
    assert resp.status_code == 200
    assert b"Alpha Pipeline" in resp.data
    assert b"snap-A" in resp.data


# ── 4. /devops/twin/<id>/delta without params → redirect ─────────────────────

def test_twin_delta_missing_params_redirects(client):
    """GET /devops/twin/<id>/delta with no from/to redirects to twin page."""
    _login(client)
    resp = client.get("/devops/twin/p1/delta")
    assert resp.status_code in (301, 302)


# ── 5. /devops/twin/<id>/delta with unknown snapshot IDs → 404 ───────────────

def test_twin_delta_unknown_snapshots_returns_404(client):
    """GET /devops/twin/<id>/delta?from=bad&to=bad returns 404 when compute_delta raises."""
    _login(client)
    pipe = _row(id="p1", name="Alpha Pipeline")
    with patch("tools.pipeline.blueprint.get_connection", return_value=_mock_conn(fetchone=pipe)), \
         patch("tools.pipeline.delta.compute_delta",
               side_effect=ValueError("Snapshot not found: 'bad-snap-a'")):
        resp = client.get("/devops/twin/p1/delta?from=bad-snap-a&to=bad-snap-b")
    assert resp.status_code == 404
    assert b"Snapshot not found" in resp.data
