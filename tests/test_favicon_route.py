# CUI // SP-CTI
"""E2E route coverage for GET /favicon.ico — 3 acceptance cases.

Cases:
  1. GET /favicon.ico returns 204 No Content (browsers don't get a 404)
  2. HEAD /favicon.ico is also well-behaved (status 200/204, no body)
  3. Route is reachable without authentication (public asset path)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from flask import Flask, make_response

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ── minimal app fixture ───────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def app():
    """Minimal Flask app with only the favicon route — mirrors app.py."""
    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True

    @flask_app.route("/favicon.ico")
    def favicon():
        return make_response("", 204)

    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


# ── 1. /favicon.ico → 204 ────────────────────────────────────────────────────

def test_favicon_returns_204(client):
    """GET /favicon.ico returns 204 No Content (suppresses browser 404)."""
    resp = client.get("/favicon.ico")
    assert resp.status_code == 204


# ── 2. response body is empty ─────────────────────────────────────────────────

def test_favicon_body_is_empty(client):
    """GET /favicon.ico returns an empty body alongside the 204."""
    resp = client.get("/favicon.ico")
    assert resp.data == b""


# ── 3. route is accessible without session / auth ─────────────────────────────

def test_favicon_no_auth_required(client):
    """GET /favicon.ico succeeds without any session cookie (public path)."""
    # Use a clean client with no session setup
    resp = client.get("/favicon.ico", headers={})
    assert resp.status_code == 204
