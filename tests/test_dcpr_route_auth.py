# CUI // SP-CTI
"""Route-auth regression for the Data Canvas geoint/osint/pipeline block (dcpr-sec-02).

Verifies that the previously unauthenticated intel routes now enforce
``@dc_login_required``. Prior to this fix the GeoINT/OSINT/Pipeline-Ops page
routes and JSON endpoints — including the two MUTATING ingest endpoints
``POST /api/geoint/ingest`` and ``POST /api/osint/ingest`` — were reachable
without a session.

The blueprint's ``dc_login_required`` decorator, when there is no
``session['user_id']``:
  * JSON / ``/data/api/`` / mutating requests → 401 ``{"error": ...}``
  * page (GET, non-api) requests           → 302 redirect to ``/login``

Memory rule: ALWAYS test the deny case.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def client(monkeypatch):
    """Minimal Flask app hosting only the data_canvas blueprint at /data.

    No session is established, so every request is unauthenticated. Runtime is
    PostgreSQL in production; conftest.py hard-forces SQLite for the suite.
    """
    monkeypatch.setenv("ICDEV_DATA_CANVAS_ENABLED", "true")

    from flask import Flask

    from tools.data_canvas.blueprint import create_data_canvas_blueprint

    bp = create_data_canvas_blueprint()
    assert bp is not None, "data_canvas blueprint should be enabled for this test"

    app = Flask(__name__)
    app.secret_key = "dcpr-sec-02-test"
    app.config["TESTING"] = True
    app.register_blueprint(bp, url_prefix="/data")

    with app.test_client() as c:
        yield c


# ── Mutating ingest endpoints must reject unauthenticated POSTs ──────────────

@pytest.mark.parametrize(
    "path",
    ["/data/api/geoint/ingest", "/data/api/osint/ingest"],
)
def test_ingest_endpoints_require_auth(client, path):
    resp = client.post(path, json={"sources": []})
    assert resp.status_code == 401, (
        f"{path} must reject unauthenticated POST (got {resp.status_code})"
    )
    assert resp.get_json(silent=True) == {"error": "Authentication required"}


# ── GET intel JSON endpoints must reject unauthenticated reads ───────────────

@pytest.mark.parametrize(
    "path",
    ["/data/api/geoint/events", "/data/api/osint/signals"],
)
def test_intel_get_endpoints_require_auth(client, path):
    resp = client.get(path)
    assert resp.status_code == 401, (
        f"{path} must reject unauthenticated GET (got {resp.status_code})"
    )
    assert resp.get_json(silent=True) == {"error": "Authentication required"}


# ── Page routes redirect unauthenticated browsers to /login ──────────────────

@pytest.mark.parametrize("path", ["/data/geoint", "/data/osint", "/data/pipeline-ops"])
def test_intel_pages_redirect_when_unauthenticated(client, path):
    resp = client.get(path)
    assert resp.status_code in (301, 302), (
        f"{path} must redirect unauthenticated browser (got {resp.status_code})"
    )
    assert "/login" in resp.headers.get("Location", "")
