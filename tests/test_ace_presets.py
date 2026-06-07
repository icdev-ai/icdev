"""Tests for ACE preset + role API endpoints.

Covers:
* GET /api/ace/presets  → JSON structure, grouping by canvas
* GET /api/ace/roles    → lightweight role list
* Launch with preset_label is accepted
"""

import pytest
from flask import Flask


@pytest.fixture
def client():
    """Flask test client with only ACE API blueprints registered."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    # Import and register only the API blueprint to avoid template/DB deps
    from icdev.tools.ace.blueprint import ace_api_bp

    app.register_blueprint(ace_api_bp, url_prefix="/api/ace")
    return app.test_client()


class TestAcePresets:
    """GET /api/ace/presets"""

    def test_presets_status_ok(self, client):
        rv = client.get("/api/ace/presets")
        assert rv.status_code == 200

    def test_presets_top_keys(self, client):
        rv = client.get("/api/ace/presets")
        data = rv.get_json()
        assert "presets" in data
        assert "by_canvas" in data

    def test_presets_nonempty(self, client):
        rv = client.get("/api/ace/presets")
        data = rv.get_json()
        assert isinstance(data["presets"], list)
        assert len(data["presets"]) > 0

    def test_preset_item_shape(self, client):
        rv = client.get("/api/ace/presets")
        data = rv.get_json()
        p = data["presets"][0]
        assert "label" in p
        assert "icon" in p
        assert "canvas" in p
        assert "prompt" in p
        assert "suggested_roles" in p
        assert isinstance(p["suggested_roles"], list)

    def test_by_canvas_grouping(self, client):
        rv = client.get("/api/ace/presets")
        data = rv.get_json()
        for canvas, items in data["by_canvas"].items():
            assert isinstance(items, list)
            assert len(items) > 0
            for item in items:
                assert any(p["label"] == item["label"] and p["canvas"] == canvas for p in data["presets"])


class TestAceRoles:
    """GET /api/ace/roles"""

    def test_roles_status_ok(self, client):
        rv = client.get("/api/ace/roles")
        assert rv.status_code == 200

    def test_roles_top_keys(self, client):
        rv = client.get("/api/ace/roles")
        data = rv.get_json()
        assert "roles" in data
        assert isinstance(data["roles"], list)

    def test_roles_include_new_roles(self, client):
        rv = client.get("/api/ace/roles")
        data = rv.get_json()
        role_ids = {r["role_id"] for r in data["roles"]}
        for rid in (
            "security_analyst",
            "compliance_manager",
            "data_analyst",
            "devops_engineer",
            "requirements_engineer",
            "business_analyst",
        ):
            assert rid in role_ids, f"expected role {rid!r} in response"

    def test_role_item_shape(self, client):
        rv = client.get("/api/ace/roles")
        data = rv.get_json()
        for r in data["roles"]:
            assert "role_id" in r
            assert "display_name" in r
            assert "description" in r
            assert "llm_function" in r


class TestLaunchWithPresetLabel:
    """POST /api/ace/launch accepts optional preset_label."""

    def test_launch_accepts_preset_label(self, client, monkeypatch):
        calls = []
        class FakeCtrl:
            def launch(self, **kwargs):
                calls.append(kwargs)
                return "ace-test-preset"
        fake = FakeCtrl()
        monkeypatch.setattr("icdev.tools.ace.blueprint.ACEController.get_instance", lambda: fake)

        payload = {
            "problem_text": "Run QA lint scan",
            "trigger_source": "test",
            "preset_label": "QA — Lint + Security Scan",
        }
        rv = client.post("/api/ace/launch", json=payload)
        assert rv.status_code == 202
        assert calls[-1].get("preset_label") == "QA — Lint + Security Scan"

    def test_launch_omits_preset_label_when_blank(self, client, monkeypatch):
        calls = []
        class FakeCtrl:
            def launch(self, **kwargs):
                calls.append(kwargs)
                return "ace-test-nopreset"
        fake = FakeCtrl()
        monkeypatch.setattr("icdev.tools.ace.blueprint.ACEController.get_instance", lambda: fake)

        payload = {"problem_text": "Run QA lint scan", "trigger_source": "test"}
        rv = client.post("/api/ace/launch", json=payload)
        assert rv.status_code == 202
        assert calls[-1].get("preset_label") == ""
