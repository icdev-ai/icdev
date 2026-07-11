# CUI // SP-CTI
"""Route + completeness tests for the /cortex canvas skeleton (ctx-canvas-01).

Exercises the three blueprint routes through a Flask test client and asserts
the canvas satisfies the 8-point completeness gate and advertises exactly the
seven Cortex facades.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def client():
    """Full dashboard app with the cortex blueprint guaranteed registered.

    The canvas is default-disabled, so it may not be auto-registered; register
    it on demand and create its SQLite tables for the DB-backed routes.
    """
    from tools.cortex.blueprint import cortex_bp
    from tools.dashboard.app import app

    if "cortex" not in app.blueprints:
        app.register_blueprint(cortex_bp)

    app.config["TESTING"] = True
    with app.app_context():
        try:
            from tools.cortex.db.init_db import init_db
            init_db()
        except Exception:
            pass
    with app.test_client() as test_client:
        yield test_client


class TestPageRoutes:
    def test_index_returns_200_and_branding(self, client):
        resp = client.get("/cortex/")
        assert resp.status_code == 200
        assert b"Cortex" in resp.data
        assert b"CUI // SP-CTI" in resp.data

    def test_index_advertises_modes_and_lenses(self, client):
        resp = client.get("/cortex/")
        body = resp.data
        # Mode picker + at least one facade label and one domain lens.
        assert b"cortex-mode" in body
        assert b"cortex-domain" in body
        assert b"Ask (Analyst)" in body
        assert b"Compliance" in body

    def test_index_includes_iqe_widget(self, client):
        resp = client.get("/cortex/")
        assert b"/cortex/api/iqe-query" in resp.data


class TestChatStub:
    def test_chat_returns_ungrounded_stub(self, client):
        resp = client.post(
            "/cortex/api/chat",
            data=json.dumps({"question": "hello cortex", "mode": "ask", "domain": "compliance"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["stub"] is True
        assert data["grounded"] is False
        # TRUST: a stub never fabricates citations.
        assert data["citations"] == []
        assert data["mode"] == "ask"
        assert data["domain"] == "compliance"
        assert data["session_id"]

    def test_chat_normalizes_unknown_mode_and_domain(self, client):
        resp = client.post(
            "/cortex/api/chat",
            data=json.dumps({"question": "q", "mode": "bogus", "domain": "nope"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["mode"] == "ask"          # DEFAULT_MODE
        assert data["domain"] == "general"    # DEFAULT_DOMAIN

    def test_chat_requires_question(self, client):
        resp = client.post(
            "/cortex/api/chat",
            data=json.dumps({"question": "   "}),
            content_type="application/json",
        )
        assert resp.status_code == 400


class TestIQERoute:
    def test_iqe_query_requires_question(self, client):
        resp = client.post(
            "/cortex/api/iqe-query",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_iqe_query_executes(self, client):
        resp = client.post(
            "/cortex/api/iqe-query",
            data=json.dumps({"question": "show recent sessions"}),
            content_type="application/json",
        )
        # Executes end-to-end (empty tables → empty result), never 500.
        assert resp.status_code == 200


class TestCompletenessGate:
    def test_registry_entry_passes_8_gate(self):
        from tools.config.component_registry import validate_canvas_completeness

        report = validate_canvas_completeness("cortex", repo_root=ROOT)
        failed = [i.point for i in report.items if i.required and not i.present]
        assert report.passed, f"cortex completeness gate failed on: {failed}"

    def test_registered_as_canvas(self):
        from tools.config.component_registry import get_registry

        comp = get_registry().get("cortex")
        assert comp is not None
        assert comp.kind == "canvas"
        assert comp.url_prefix == "/cortex"
        assert comp.env_flag == "ICDEV_CORTEX_ENABLED"


class TestConstants:
    def test_advertises_seven_facades(self):
        from tools.cortex import constants

        keys = [m["key"] for m in constants.CORTEX_MODES]
        assert keys == [
            "complete", "classify", "extract", "search", "ask", "govern", "agent",
        ]

    def test_default_mode_and_domain_are_valid(self):
        from tools.cortex import constants

        assert constants.DEFAULT_MODE in constants.CORTEX_MODE_KEYS
        assert constants.DEFAULT_DOMAIN in constants.CORTEX_DOMAIN_KEYS
