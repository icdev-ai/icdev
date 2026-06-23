# CUI // SP-CTI
"""V&V tests for ECR tier gate (ecr-tier-05).

Covers:
  test_registry_has_min_tier      — every canvas entry has a min_tier field
  test_tier_from_profile          — ECR_TIER env var drives get_active_tier()
  test_tier_gate_blocks           — community tier is blocked from enterprise canvas
  test_enterprise_tier_allowed    — enterprise tier is allowed everywhere
  test_tier_gate_template         — tier_gate.html renders via Flask test client
  test_license_api_returns_tier   — GET /api/license/tier returns JSON with active_tier
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

# Ensure repo root on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _registry_path() -> Path:
    return Path(__file__).resolve().parent.parent / "args" / "canvas_registry.yaml"


def _load_registry() -> list:
    data = yaml.safe_load(_registry_path().read_text(encoding="utf-8"))
    return data.get("canvases", [])


# ---------------------------------------------------------------------------
# test_registry_has_min_tier
# ---------------------------------------------------------------------------

def test_registry_has_min_tier():
    """Every canvas entry in canvas_registry.yaml must have a min_tier field."""
    canvases = _load_registry()
    assert canvases, "canvas_registry.yaml has no canvases"
    missing = [c["name"] for c in canvases if "min_tier" not in c]
    assert not missing, f"Canvases missing min_tier: {missing}"

    valid_tiers = {"community", "starter", "professional", "enterprise"}
    invalid = [
        f"{c['name']}={c['min_tier']}"
        for c in canvases
        if c.get("min_tier") not in valid_tiers
    ]
    assert not invalid, f"Canvases with unknown min_tier: {invalid}"


# ---------------------------------------------------------------------------
# test_tier_from_profile
# ---------------------------------------------------------------------------

def test_tier_from_profile(monkeypatch):
    """get_active_tier() returns the value set in ECR_TIER env var."""
    monkeypatch.setenv("ECR_TIER", "enterprise")
    from tools.license import tier as tier_mod
    import importlib
    importlib.reload(tier_mod)
    assert tier_mod.get_active_tier() == "enterprise"

    monkeypatch.setenv("ECR_TIER", "starter")
    importlib.reload(tier_mod)
    assert tier_mod.get_active_tier() == "starter"

    # Unknown tier falls back to community
    monkeypatch.setenv("ECR_TIER", "unknown_tier")
    importlib.reload(tier_mod)
    assert tier_mod.get_active_tier() == "community"


# ---------------------------------------------------------------------------
# test_tier_gate_blocks
# ---------------------------------------------------------------------------

def test_tier_gate_blocks(monkeypatch):
    """Community tier must be blocked from canvases with min_tier=enterprise."""
    monkeypatch.setenv("ECR_TIER", "community")
    from tools.license import tier as tier_mod
    import importlib
    importlib.reload(tier_mod)

    # Find an enterprise-only canvas from the registry
    enterprise_canvases = [
        c["name"] for c in _load_registry() if c.get("min_tier") == "enterprise"
    ]
    assert enterprise_canvases, "No enterprise-only canvases in registry for test"

    for canvas_name in enterprise_canvases:
        allowed = tier_mod.is_canvas_allowed(canvas_name, active_tier="community")
        assert not allowed, f"Community should be blocked from '{canvas_name}' (enterprise)"


# ---------------------------------------------------------------------------
# test_enterprise_tier_allowed
# ---------------------------------------------------------------------------

def test_enterprise_tier_allowed():
    """Enterprise tier must be allowed for every canvas in the registry."""
    from tools.license import tier as tier_mod

    for canvas in _load_registry():
        allowed = tier_mod.is_canvas_allowed(canvas["name"], active_tier="enterprise")
        assert allowed, f"Enterprise should be allowed for '{canvas['name']}'"


# ---------------------------------------------------------------------------
# test_tier_gate_template
# ---------------------------------------------------------------------------

def test_tier_gate_template(tmp_path, monkeypatch):
    """tier_gate.html renders without error via a minimal Flask app."""
    pytest.importorskip("flask")
    from flask import Flask, render_template_string

    template_path = (
        Path(__file__).resolve().parent.parent
        / "tools" / "dashboard" / "templates" / "errors" / "tier_gate.html"
    )
    assert template_path.exists(), f"tier_gate.html not found at {template_path}"

    template_src = template_path.read_text(encoding="utf-8")

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True

    with flask_app.app_context():
        rendered = render_template_string(
            template_src,
            required_tier="enterprise",
            active_tier="community",
            upgrade_url="#upgrade",
        )

    assert "enterprise" in rendered.lower()
    assert "community" in rendered.lower()
    assert "Upgrade" in rendered or "upgrade" in rendered.lower()


# ---------------------------------------------------------------------------
# test_license_api_returns_tier
# ---------------------------------------------------------------------------

def test_license_api_returns_tier(monkeypatch):
    """GET /api/license/tier returns JSON with active_tier key."""
    pytest.importorskip("flask")
    from flask import Flask, jsonify

    monkeypatch.setenv("ECR_TIER", "starter")

    from tools.license.tier import (
        TIER_ORDER,
        get_active_tier,
        get_available_features,
        get_locked_features,
    )

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True

    @flask_app.route("/api/license/tier")
    def license_tier():
        active = get_active_tier()
        return jsonify({
            "active_tier": active,
            "tier_order": TIER_ORDER,
            "available_features": get_available_features(active),
            "locked_features": get_locked_features(active),
        })

    with flask_app.test_client() as client:
        resp = client.get("/api/license/tier")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.get_json()
        assert data is not None, "Response is not valid JSON"
        assert "active_tier" in data, f"'active_tier' key missing from response: {data}"
        assert data["active_tier"] == "starter"
        assert "tier_order" in data
        assert "available_features" in data
        assert "locked_features" in data
        # starter tier: pipeline and infra are available, enterprise canvases are locked
        assert "pipeline" in data["available_features"]
        assert "aac" in data["locked_features"]
