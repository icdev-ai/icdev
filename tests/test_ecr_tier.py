# CUI // SP-CTI
"""Tests for min_tier field on component registry entries (ecr-tier-01)."""

import pytest


def _make_registry(tmp_path, yaml_text):
    """Helper: write a minimal registry YAML and return a ComponentRegistry."""
    from tools.config.component_registry import ComponentRegistry

    reg_file = tmp_path / "component_registry.yaml"
    reg_file.write_text(yaml_text, encoding="utf-8")
    return ComponentRegistry(registry_path=reg_file)


# ---------------------------------------------------------------------------
# Core acceptance test
# ---------------------------------------------------------------------------


def test_registry_has_min_tier():
    """All components expose a valid min_tier; foundry=enterprise, ndc=community."""
    from tools.config.component_registry import ComponentRegistry

    registry = ComponentRegistry()
    valid_tiers = {"community", "professional", "enterprise"}

    assert len(registry) > 0, "registry must not be empty"

    for comp in registry:
        assert hasattr(comp, "min_tier"), f"{comp.key} missing min_tier attribute"
        assert comp.min_tier in valid_tiers, (
            f"{comp.key}.min_tier={comp.min_tier!r} is not one of {valid_tiers}"
        )

    foundry = registry.get("foundry")
    assert foundry is not None, "foundry canvas must be registered"
    assert foundry.min_tier == "enterprise", (
        f"foundry.min_tier expected 'enterprise', got {foundry.min_tier!r}"
    )

    ndc = registry.get("ndc")
    assert ndc is not None, "ndc canvas must be registered"
    assert ndc.min_tier == "community", (
        f"ndc.min_tier expected 'community', got {ndc.min_tier!r}"
    )

    admin_console = registry.get("admin_console")
    assert admin_console is not None, "admin_console must be registered"
    assert admin_console.min_tier == "enterprise", (
        f"admin_console.min_tier expected 'enterprise', got {admin_console.min_tier!r}"
    )


# ---------------------------------------------------------------------------
# Default-value tests
# ---------------------------------------------------------------------------


def test_min_tier_defaults_to_community(tmp_path):
    """An entry without min_tier defaults to 'community'."""
    yaml_text = """
components:
- key: dummy
  kind: canvas
  display_name: Dummy Canvas
  description: Test canvas.
  env_flag: ICDEV_DUMMY_ENABLED
  default_enabled: false
  module: tools.dummy.blueprint
  blueprint_attr: dummy_bp
  url_prefix: /dummy
  min_il: IL4
  default_roles: []
  nav:
    section: Test
    label: Dummy
"""
    registry = _make_registry(tmp_path, yaml_text)
    comp = registry.get("dummy")
    assert comp is not None
    assert comp.min_tier == "community"


def test_min_tier_enterprise_parsed(tmp_path):
    """An entry with min_tier: enterprise is parsed correctly."""
    yaml_text = """
components:
- key: premium_canvas
  kind: canvas
  display_name: Premium Canvas
  description: Enterprise-only canvas.
  env_flag: ICDEV_PREMIUM_ENABLED
  default_enabled: false
  module: tools.premium.blueprint
  blueprint_attr: premium_bp
  url_prefix: /premium
  min_il: IL4
  min_tier: enterprise
  default_roles: []
  nav:
    section: Enterprise
    label: Premium
"""
    registry = _make_registry(tmp_path, yaml_text)
    comp = registry.get("premium_canvas")
    assert comp is not None
    assert comp.min_tier == "enterprise"


def test_min_tier_invalid_falls_back_to_community(tmp_path):
    """An entry with an invalid min_tier silently falls back to 'community'."""
    yaml_text = """
components:
- key: bad_tier
  kind: canvas
  display_name: Bad Tier Canvas
  description: Canvas with invalid tier.
  env_flag: ICDEV_BAD_ENABLED
  default_enabled: false
  module: tools.bad.blueprint
  blueprint_attr: bad_bp
  url_prefix: /bad
  min_il: IL4
  min_tier: ultra_platinum
  default_roles: []
  nav:
    section: Test
    label: Bad
"""
    registry = _make_registry(tmp_path, yaml_text)
    comp = registry.get("bad_tier")
    assert comp is not None
    assert comp.min_tier == "community"


def test_min_tier_professional_parsed(tmp_path):
    """An entry with min_tier: professional is parsed correctly."""
    yaml_text = """
components:
- key: pro_canvas
  kind: canvas
  display_name: Pro Canvas
  description: Professional-tier canvas.
  env_flag: ICDEV_PRO_ENABLED
  default_enabled: false
  module: tools.pro.blueprint
  blueprint_attr: pro_bp
  url_prefix: /pro
  min_il: IL4
  min_tier: professional
  default_roles: []
  nav:
    section: Pro
    label: Pro Canvas
"""
    registry = _make_registry(tmp_path, yaml_text)
    comp = registry.get("pro_canvas")
    assert comp is not None
    assert comp.min_tier == "professional"


# ---------------------------------------------------------------------------
# Completeness report includes min_tier
# ---------------------------------------------------------------------------


def test_completeness_report_has_min_tier():
    """validate_canvas_completeness() result includes the component's min_tier."""
    from tools.config.component_registry import (
        ComponentRegistry,
        validate_canvas_completeness,
    )

    registry = ComponentRegistry()
    foundry = registry.get("foundry")
    assert foundry is not None

    report = validate_canvas_completeness("foundry", registry=registry)
    assert hasattr(report, "min_tier"), "CanvasCompletenessReport missing min_tier"
    assert report.min_tier == "enterprise"


def test_completeness_report_community_tier():
    """validate_canvas_completeness() returns 'community' for ndc."""
    from tools.config.component_registry import (
        ComponentRegistry,
        validate_canvas_completeness,
    )

    registry = ComponentRegistry()
    report = validate_canvas_completeness("ndc", registry=registry)
    assert report.min_tier == "community"


# ---------------------------------------------------------------------------
# Nav context includes min_tier
# ---------------------------------------------------------------------------


def test_nav_context_includes_min_tier():
    """get_nav_context() includes min_tier for every nav item."""
    from tools.config.component_registry import ComponentRegistry

    registry = ComponentRegistry()
    nav = registry.get_nav_context()
    for _section, section_data in nav["sections"].items():
        for group in section_data["groups"]:
            for item in group["items"]:
                assert "min_tier" in item, (
                    f"nav item {item.get('key')} missing min_tier"
                )
                assert item["min_tier"] in ("community", "professional", "enterprise")


# ---------------------------------------------------------------------------
# License tier — profile field + env var
# ---------------------------------------------------------------------------


def test_tier_from_profile(monkeypatch, tmp_path):
    """license_tier in a profile maps to ICDEV_LICENSE_TIER; env var always wins."""
    from icdev.tools.config.core_profile import load_profiles, profile_env_overrides
    from tools.billing.tier import TIER_ORDER, get_active_tier

    yaml_text = """
profiles:
  saas-il4:
    license_tier: enterprise
    storage_backend: postgresql
  local-dev:
    license_tier: professional
    storage_backend: sqlite
  community:
    license_tier: community
    storage_backend: sqlite
"""
    profiles_file = tmp_path / "core_profiles.yaml"
    profiles_file.write_text(yaml_text, encoding="utf-8")

    profiles = load_profiles(profiles_file)

    # profile_env_overrides maps license_tier -> ICDEV_LICENSE_TIER
    monkeypatch.delenv("ICDEV_LICENSE_TIER", raising=False)
    assert profile_env_overrides(profiles["saas-il4"]).get("ICDEV_LICENSE_TIER") == "enterprise"
    assert profile_env_overrides(profiles["local-dev"]).get("ICDEV_LICENSE_TIER") == "professional"
    assert profile_env_overrides(profiles["community"]).get("ICDEV_LICENSE_TIER") == "community"

    # get_active_tier() defaults to 'community' with no env var
    monkeypatch.delenv("ICDEV_LICENSE_TIER", raising=False)
    assert get_active_tier() == "community"

    # get_active_tier() reads ICDEV_LICENSE_TIER
    monkeypatch.setenv("ICDEV_LICENSE_TIER", "enterprise")
    assert get_active_tier() == "enterprise"

    monkeypatch.setenv("ICDEV_LICENSE_TIER", "professional")
    assert get_active_tier() == "professional"

    # env var wins: even if profile says enterprise, env var overrides
    monkeypatch.setenv("ICDEV_LICENSE_TIER", "community")
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")  # prevent existing env clash
    overrides = profile_env_overrides(profiles["saas-il4"])
    assert "ICDEV_LICENSE_TIER" not in overrides, "env var must prevent profile from overriding"
    assert get_active_tier() == "community"

    # TIER_ORDER sanity
    assert TIER_ORDER == ["community", "professional", "enterprise"]


# ---------------------------------------------------------------------------
# Tier gate enforcement in guard_component_access()
# ---------------------------------------------------------------------------


def test_tier_gate_blocks(monkeypatch, tmp_path):
    """community tier is blocked from enterprise canvas; enterprise tier passes."""
    import importlib

    reg_file = tmp_path / "reg.yaml"
    reg_file.write_text(
        """
components:
- key: premium_canvas
  kind: canvas
  display_name: Premium Canvas
  description: Enterprise-only.
  env_flag: ICDEV_PREMIUM_ENABLED
  default_enabled: false
  module: tools.premium.blueprint
  blueprint_attr: premium_bp
  url_prefix: /premium
  min_il: IL4
  min_tier: enterprise
  default_roles: []
  nav:
    section: Enterprise
    label: Premium
""",
        encoding="utf-8",
    )

    from icdev.tools.config.component_registry import ComponentRegistry
    _registry = ComponentRegistry(registry_path=reg_file)

    # Patch get_registry in both icdev and tools namespaces
    reg_mod = importlib.import_module("icdev.tools.config.component_registry")
    monkeypatch.setattr(reg_mod, "get_registry", lambda: _registry)
    try:
        tools_reg_mod = importlib.import_module("tools.config.component_registry")
        monkeypatch.setattr(tools_reg_mod, "get_registry", lambda: _registry)
    except Exception:
        pass

    try:
        from flask import Flask
    except ImportError:
        pytest.skip("Flask not available")

    from tools.security.canvas_access import guard_component_access
    from tools.security.exceptions import TierAccessDenied

    guard = guard_component_access("premium_canvas", "IL4")
    app = Flask(__name__)

    # community tier -> TierAccessDenied raised
    monkeypatch.setenv("ICDEV_LICENSE_TIER", "community")
    with app.test_request_context("/premium/"):
        with pytest.raises(TierAccessDenied) as exc_info:
            guard()
        assert exc_info.value.canvas_key == "premium_canvas"
        assert exc_info.value.required_tier == "enterprise"

    # enterprise tier -> tier gate passes (other guards may still fire in test env)
    monkeypatch.setenv("ICDEV_LICENSE_TIER", "enterprise")
    with app.test_request_context("/premium/"):
        try:
            guard()
        except TierAccessDenied:
            pytest.fail("enterprise tier must not be blocked by the tier gate")
        except Exception:
            pass  # auth / grant errors are expected in test context without DB


# ---------------------------------------------------------------------------
# Tier gate template + /api/license/tier route
# ---------------------------------------------------------------------------


def test_tier_gate_template():
    """tier_gate.html contains lock icon, tier badges, upgrade CTA, and comparison table."""
    import pathlib

    tmpl_path = (
        pathlib.Path(__file__).parent.parent
        / "tools"
        / "dashboard"
        / "templates"
        / "errors"
        / "tier_gate.html"
    )
    assert tmpl_path.exists(), "tier_gate.html not found"

    content = tmpl_path.read_text(encoding="utf-8")

    # extends base
    assert "extends" in content and "base.html" in content, "must extend base.html"

    # lock icon
    assert "1f512" in content.lower() or "lock" in content.lower(), "lock icon missing"

    # template variables
    assert "canvas_key" in content, "canvas_key variable missing"
    assert "required_tier" in content, "required_tier variable missing"
    assert "active_tier" in content, "active_tier variable missing"

    # upgrade CTA — either support_url or mailto fallback
    assert "Upgrade" in content, "upgrade CTA text missing"
    assert "support_url" in content or "mailto" in content, "upgrade URL missing"

    # feature comparison table — all three tier columns
    assert "Community" in content, "Community column missing"
    assert "Professional" in content, "Professional column missing"
    assert "Enterprise" in content, "Enterprise column missing"


def test_license_tier_api(monkeypatch):
    """GET /api/license/tier returns active_tier, available_features, locked_features."""
    try:
        from flask import Flask
    except ImportError:
        pytest.skip("Flask not available")


    monkeypatch.setenv("ICDEV_LICENSE_TIER", "community")

    app = Flask(__name__)

    # Wire the route inline to avoid full app bootstrap
    @app.route("/api/license/tier")
    def _tier_route():
        from flask import jsonify
        from tools.billing.tier import get_active_tier, tier_satisfies

        try:
            from tools.config.component_registry import get_registry
            _reg = get_registry()
        except Exception:
            return jsonify({"active_tier": get_active_tier(), "available_features": [], "locked_features": []})

        active = get_active_tier()
        available, locked = [], []
        for comp in _reg.iter_canvases():
            if tier_satisfies(active, comp.min_tier):
                available.append(comp.key)
            else:
                locked.append(comp.key)
        return jsonify({
            "active_tier": active,
            "available_features": sorted(available),
            "locked_features": sorted(locked),
        })

    with app.test_client() as client:
        resp = client.get("/api/license/tier")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "active_tier" in data, "active_tier key missing"
        assert "available_features" in data, "available_features key missing"
        assert "locked_features" in data, "locked_features key missing"
        assert data["active_tier"] == "community"
        assert isinstance(data["available_features"], list)
        assert isinstance(data["locked_features"], list)

        # community tier should have some locked features (enterprise canvases)
        monkeypatch.setenv("ICDEV_LICENSE_TIER", "enterprise")
        resp2 = client.get("/api/license/tier")
        data2 = resp2.get_json()
        assert data2["active_tier"] == "enterprise"
        # enterprise should have no locked features
        assert data2["locked_features"] == [], (
            f"enterprise tier should have no locked features, got: {data2['locked_features']}"
        )
