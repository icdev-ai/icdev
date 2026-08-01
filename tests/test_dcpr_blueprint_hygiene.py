#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Data Canvas blueprint hygiene fixes (dcpr-qa-03).

Covers two guarantees added by the QA hardening pass:

  1. GET /data/api/pipeline/status must FAIL VISIBLY when its backing DB
     call raises — returning ``degraded: true`` with null metrics, never the
     fabricated demo numbers (accuracy 94.2 / throughput 3200) that used to be
     emitted on any error and masked real DB failures.

  2. The three previously-unauthenticated constant routes
     (/api/objects, /api/classification-levels, /api/rules) now require auth,
     matching the rest of the blueprint's convention.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _make_app():
    """Build a minimal Flask app with the DDC blueprint registered at /data."""
    from flask import Flask

    app = Flask(__name__, template_folder=str(_ROOT / "tools" / "dashboard" / "templates"))
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"

    with patch("tools.data_canvas.db.init_db.init_db"):
        from tools.data_canvas.blueprint import create_data_canvas_blueprint

        bp = create_data_canvas_blueprint()
    assert bp is not None, "DDC blueprint disabled — set ICDEV_DATA_CANVAS_ENABLED"
    app.register_blueprint(bp, url_prefix="/data")
    return app


# ── Test 1: pipeline/status degrades (does not fabricate) on DB failure ───────

def test_pipeline_status_degrades_on_db_failure():
    """When the backing DB call raises, the endpoint must report a degraded
    status with null metrics — not the 94.2 / 3200 demo values."""
    app = _make_app()
    client = app.test_client()
    # /data/api/pipeline/status is @dc_login_required (sec-02) — authenticate.
    with client.session_transaction() as sess:
        sess["user_id"] = "test-user"

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated DB outage")

    # The route imports get_connection from tools.db.storage at call time.
    with patch("tools.db.storage.get_connection", side_effect=_boom):
        resp = client.get("/data/api/pipeline/status")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    # Must be flagged as degraded, not silently "healthy".
    assert data.get("degraded") is True
    # Must NOT emit the fabricated demo metrics.
    assert data.get("accuracy") != 94.2
    assert data.get("throughput") != 3200
    assert data.get("accuracy") is None
    assert data.get("throughput") is None


def test_pipeline_status_healthy_shape_has_degraded_false():
    """A successful call carries degraded=False (the honest default)."""
    app = _make_app()
    client = app.test_client()
    # /data/api/pipeline/status is @dc_login_required (sec-02) — authenticate.
    with client.session_transaction() as sess:
        sess["user_id"] = "test-user"
    # No DB patch: on this fresh checkout the query may or may not find tables,
    # but the handler swallows per-query errors internally and returns 200.
    resp = client.get("/data/api/pipeline/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    assert "degraded" in data


# ── Test 2: the three constant routes now require authentication ──────────────

_NEWLY_AUTHED_ROUTES = [
    "/data/api/objects",
    "/data/api/classification-levels",
    "/data/api/rules",
]


def test_constant_routes_require_auth():
    """Without a session, the three constant routes return 401 JSON."""
    app = _make_app()
    client = app.test_client()
    for route in _NEWLY_AUTHED_ROUTES:
        resp = client.get(route)
        assert resp.status_code == 401, f"{route} should require auth, got {resp.status_code}"
        body = resp.get_json()
        assert body and "error" in body, f"{route} should return a JSON error body"


def test_constant_routes_allow_authenticated():
    """With a valid session, the three constant routes return 200 payloads."""
    app = _make_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "test-user"
    for route in _NEWLY_AUTHED_ROUTES:
        resp = client.get(route)
        assert resp.status_code == 200, f"{route} authed should be 200, got {resp.status_code}"
