# CUI // SP-CTI
"""Unit tests for the /iqe Flask blueprint (tools/dashboard/api/iqe.py).

Four tests using Flask test_client:
  1. GET /iqe returns 200 with query list
  2. POST /iqe/run with a valid query file returns 200 + rows structure
  3. POST /iqe/run with a missing file returns 404
  4. POST /iqe/run with bad IQE syntax returns 400
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from tools.dashboard.api.iqe import iqe_api


@pytest.fixture()
def client():
    app = Flask(__name__)
    app.register_blueprint(iqe_api)
    app.config["TESTING"] = True
    return app.test_client()


# ---------------------------------------------------------------------------
# 1. GET /iqe — list queries
# ---------------------------------------------------------------------------

def test_list_queries_returns_ok(client, tmp_path):
    """GET /iqe returns 200 with a well-formed JSON response."""
    # Seed one .iqe file in a temp queries dir.
    (tmp_path / "network").mkdir()
    (tmp_path / "network" / "sample.iqe").write_text(
        "foreach d in devices select d.name", encoding="utf-8"
    )
    with patch("tools.dashboard.api.iqe._QUERIES_DIR", tmp_path):
        resp = client.get("/iqe")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["ok"] is True
    assert "queries" in data
    assert data["count"] == len(data["queries"])
    assert any(q["name"] == "sample" for q in data["queries"])


# ---------------------------------------------------------------------------
# 2. POST /iqe/run — valid query file
# ---------------------------------------------------------------------------

def test_run_valid_query(client, tmp_path):
    """POST /iqe/run with a parseable file returns 200 and rows structure."""
    iqe_file = tmp_path / "test.iqe"
    iqe_file.write_text(
        "foreach d in devices select d.name", encoding="utf-8"
    )
    fake_rows = [{"name": "router-1"}]
    with (
        patch("tools.dashboard.api.iqe._QUERIES_DIR", tmp_path),
        patch("tools.dashboard.api.iqe.execute_query", return_value=fake_rows),
        patch("tools.dashboard.api.iqe.get_connection", create=True),
    ):
        # Patch the inner import of get_connection inside run_query
        import tools.dashboard.api.iqe as iqe_mod
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with patch.object(iqe_mod, "execute_query", return_value=fake_rows):
            # Bypass DB by patching get_connection at module level
            with patch("tools.db.storage.get_connection", return_value=mock_conn):
                resp = client.post(
                    "/iqe/run",
                    json={"query_path": "test.iqe"},
                )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["ok"] is True
    assert "rows" in data
    assert "count" in data


# ---------------------------------------------------------------------------
# 3. POST /iqe/run — missing file → 404
# ---------------------------------------------------------------------------

def test_run_missing_query_returns_404(client, tmp_path):
    """POST /iqe/run with a nonexistent query_path returns 404."""
    with patch("tools.dashboard.api.iqe._QUERIES_DIR", tmp_path):
        resp = client.post("/iqe/run", json={"query_path": "does_not_exist.iqe"})
    assert resp.status_code == 404
    data = json.loads(resp.data)
    assert data["ok"] is False
    assert "not found" in data["error"]


# ---------------------------------------------------------------------------
# 4. POST /iqe/run — bad syntax → 400
# ---------------------------------------------------------------------------

def test_run_parse_error_returns_400(client, tmp_path):
    """POST /iqe/run with syntactically invalid IQE returns 400."""
    bad_file = tmp_path / "bad.iqe"
    bad_file.write_text("THIS IS NOT VALID IQE !!!", encoding="utf-8")
    with patch("tools.dashboard.api.iqe._QUERIES_DIR", tmp_path):
        resp = client.post("/iqe/run", json={"query_path": "bad.iqe"})
    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert data["ok"] is False
    assert "parse error" in data["error"]
