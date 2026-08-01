# CUI // SP-CTI
"""Regression tests for PUT /devops/api/pipelines/<id> — partial-update semantics.

Background: A pre-existing bug caused every page open of a pipeline canvas
to issue a PUT with only {name, graph_json} (the JS savePipeline() payload).
The original handler used `data.get("description", "")` and
`data.get("classification", "public")` fallbacks, which silently clobbered
the server-side values with defaults. Result: opening a canvas zeroed out
description, classification, and target_csp.

Fix: PUT now uses PATCH semantics — only fields explicitly present in the
payload are written. To clear a field, callers must send `""` (empty
string) or `null`. Empty payload returns 400.

These tests verify:
  1. Partial PUT updates only sent fields, leaves others untouched
  2. Empty PUT payload is rejected with 400
  3. Explicit empty string DOES clear a field (intentional clear)
  4. Full PUT with all fields works as before (backward compat)
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _row(**kw):
    return kw


def _make_app_with_pipeline(existing: dict) -> tuple[Flask, MagicMock]:
    """Build a minimal Flask app wired to the real pc_api_update route,
    with a mock connection that returns `existing` for SELECTs."""
    import os

    # Force the blueprint factory on for this test; production sets this
    # in .env, but the test harness does not inherit dashboard env.
    os.environ.setdefault("ICDEV_PIPELINE_ENABLED", "true")
    from tools.pipeline.blueprint import create_pipeline_blueprint

    pipeline_bp = create_pipeline_blueprint()
    if pipeline_bp is None:
        raise RuntimeError(
            "Pipeline blueprint disabled — set ICDEV_PIPELINE_ENABLED=true"
        )

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.secret_key = "test"
    # Production registers this blueprint with url_prefix="/devops"; the test
    # app uses no prefix so the test paths are simpler. Both forms hit the
    # same pc_api_update handler.
    app.register_blueprint(pipeline_bp, url_prefix="/devops")

    conn = MagicMock()
    cur = MagicMock()
    # SELECT existing row
    cur.fetchone.return_value = _row(**existing)
    conn.execute.return_value = cur

    return app, conn


def _login(client):
    with client.session_transaction() as sess:
        sess["user_id"] = "test-user"
        # pdx-sec-03: pipeline write routes now require a write-tier role
        # (developer/pm/isso/admin). An authorized editor is a developer.
        sess["role"] = "developer"


# ── tests ────────────────────────────────────────────────────────────────────

def test_partial_put_does_not_clobber_omitted_fields():
    """Sending only {name, graph_json} must NOT reset description, classification, or target_csp.

    This is the core regression: before the fix, opening a canvas (which triggers
    savePipeline with just name+graph_json) silently overwrote the other 3 fields
    with their defaults. After the fix, those fields are preserved.
    """
    pipe_id = str(uuid.uuid4())
    existing = {
        "id": pipe_id,
        "name": "BCAP IL5",
        "description": "Defense BCAP reference build",
        "graph_json": "{}",
        "classification": "CUI",
        "target_csp": "aws-il5",
    }
    app, conn = _make_app_with_pipeline(existing)
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"), \
         patch("tools.knowledge_graph.canvas_ask.reindex_canvas_on_save"), \
         patch("tools.pipeline.twin.take_snapshot", create=True):
        client = app.test_client()
        _login(client)
        resp = client.put(
            f"/devops/api/pipelines/{pipe_id}",
            data=json.dumps({"name": "BCAP IL5 (renamed)", "graph_json": '{"nodes":[],"edges":[]}'}),
            content_type="application/json",
        )
    assert resp.status_code == 200, resp.get_data(as_text=True)

    # Inspect the actual UPDATE SQL that was executed
    update_calls = [c for c in conn.execute.call_args_list
                    if "UPDATE pipelines" in str(c.args[0])]
    assert update_calls, "PUT should have issued an UPDATE"
    sql = update_calls[0].args[0]
    sql_lc = sql.lower()
    # Only the fields we sent should be in the SET clause
    assert "name=%s" in sql_lc
    assert "graph_json=%s" in sql_lc
    assert "description=%s" not in sql_lc, "description not in payload — must not be SET"
    assert "classification=%s" not in sql_lc, "classification not in payload — must not be SET"
    assert "target_csp=%s" not in sql_lc, "target_csp not in payload — must not be SET"
    # updated_at is always written
    assert "updated_at=%s" in sql_lc

    # Params should be exactly [name, graph_json, updated_at, pipe_id]
    params = update_calls[0].args[1]
    assert isinstance(params, (list, tuple)), f"params must be sequence, got {type(params)}"
    assert len(params) == 4, f"expected 4 params, got {len(params)}: {params!r}"
    assert params[0] == "BCAP IL5 (renamed)", f"name mismatch: {params[0]!r}"
    # graph_json is validated + canonicalized at the write boundary (pdx-sec-01),
    # so compare parsed structure rather than exact bytes (json.dumps adds spaces).
    assert json.loads(params[1]) == {"nodes": [], "edges": []}, f"graph_json mismatch: {params[1]!r}"
    assert isinstance(params[2], str) and len(params[2]) >= 10, (
        f"updated_at must be ISO timestamp string, got {params[2]!r}"
    )
    assert params[3] == pipe_id, f"pipe_id mismatch: {params[3]!r} != {pipe_id!r}"


def test_empty_put_payload_is_rejected():
    """Empty {} must return 400 — protects against accidentally wiping all fields."""
    pipe_id = str(uuid.uuid4())
    existing = {
        "id": pipe_id, "name": "X", "description": "Y", "graph_json": "{}",
        "classification": "CUI", "target_csp": "aws",
    }
    app, conn = _make_app_with_pipeline(existing)
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"):
        client = app.test_client()
        _login(client)
        resp = client.put(f"/devops/api/pipelines/{pipe_id}",
                          data="{}", content_type="application/json")
    assert resp.status_code == 400
    assert b"No updatable fields" in resp.data


def test_explicit_empty_string_clears_field():
    """Sending description='' MUST clear it (intentional blank)."""
    pipe_id = str(uuid.uuid4())
    existing = {
        "id": pipe_id, "name": "X", "description": "to be cleared", "graph_json": "{}",
        "classification": "CUI", "target_csp": "aws",
    }
    app, conn = _make_app_with_pipeline(existing)
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"), \
         patch("tools.knowledge_graph.canvas_ask.reindex_canvas_on_save"), \
         patch("tools.pipeline.twin.take_snapshot", create=True):
        client = app.test_client()
        _login(client)
        resp = client.put(
            f"/devops/api/pipelines/{pipe_id}",
            data=json.dumps({"description": ""}),
            content_type="application/json",
        )
    assert resp.status_code == 200
    update_calls = [c for c in conn.execute.call_args_list
                    if "UPDATE pipelines" in str(c.args[0])]
    assert update_calls
    sql_lc = update_calls[0].args[0].lower()
    assert "description=%s" in sql_lc
    # Other fields absent from payload — must not be SET
    assert "name=%s" not in sql_lc
    assert "classification=%s" not in sql_lc
    # Param value is the empty string (the explicit clear)
    params = update_calls[0].args[1]
    assert "" in params


def test_full_put_with_all_fields_backward_compat():
    """Sending all 5 fields still works — no regression for explicit full updates."""
    pipe_id = str(uuid.uuid4())
    existing = {
        "id": pipe_id, "name": "Old", "description": "old", "graph_json": "{}",
        "classification": "public", "target_csp": "generic",
    }
    app, conn = _make_app_with_pipeline(existing)
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"), \
         patch("tools.knowledge_graph.canvas_ask.reindex_canvas_on_save"), \
         patch("tools.pipeline.twin.take_snapshot", create=True):
        client = app.test_client()
        _login(client)
        resp = client.put(
            f"/devops/api/pipelines/{pipe_id}",
            data=json.dumps({
                "name": "New Name",
                "description": "New desc",
                "graph_json": '{"nodes":[],"edges":[]}',
                "classification": "SECRET",
                "target_csp": "onprem-dod",
            }),
            content_type="application/json",
        )
    assert resp.status_code == 200
    update_calls = [c for c in conn.execute.call_args_list
                    if "UPDATE pipelines" in str(c.args[0])]
    sql_lc = update_calls[0].args[0].lower()
    # All 5 fields present in SET
    for col in ("name", "description", "graph_json", "classification", "target_csp"):
        assert f"{col}=%s" in sql_lc, f"{col} should be in SET for full PUT"


def test_savepipeline_payload_preserves_all_metadata():
    """End-to-end: the exact payload that the JS savePipeline() sends
    ({name, graph_json}) must NOT reset description/classification/target_csp.

    This mirrors what happens in the browser when a user opens a canvas
    and the auto-save timer fires after 3s.
    """
    pipe_id = str(uuid.uuid4())
    existing = {
        "id": pipe_id, "name": "BCAP IL5", "description": "Defense BCAP ref",
        "graph_json": "{}", "classification": "CUI", "target_csp": "aws-il5",
    }
    app, conn = _make_app_with_pipeline(existing)
    captured_sql = []
    with patch("tools.pipeline.blueprint.get_connection", return_value=conn), \
         patch("tools.pipeline.blueprint._audit"), \
         patch("tools.knowledge_graph.canvas_ask.reindex_canvas_on_save"), \
         patch("tools.pipeline.twin.take_snapshot", create=True):
        client = app.test_client()
        _login(client)
        # Exact JS payload from savePipeline()
        js_payload = json.dumps({
            "name": "BCAP IL5",
            "graph_json": json.dumps({"nodes": [], "edges": []}),
        })
        resp = client.put(f"/devops/api/pipelines/{pipe_id}",
                          data=js_payload, content_type="application/json")
        captured_sql = [str(c.args[0]) for c in conn.execute.call_args_list
                        if "UPDATE pipelines" in str(c.args[0])]
    assert resp.status_code == 200
    assert len(captured_sql) == 1
    # The bug: server wrote ALL 5 fields. The fix: only name + graph_json.
    assert "description=%s" not in captured_sql[0]
    assert "classification=%s" not in captured_sql[0]
    assert "target_csp=%s" not in captured_sql[0]
