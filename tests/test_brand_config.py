# CUI // SP-CTI
"""Tests for DSW-1: brand and banner configuration (tools/dashboard/brand.py)."""
from __future__ import annotations

import textwrap

import pytest


@pytest.fixture(autouse=True)
def _reset_cache():
    """Clear module-level caches before each test so env/file changes take effect."""
    from tools.dashboard import brand as brand_mod
    brand_mod._brand_cache = None
    brand_mod._banner_cache = None
    yield
    brand_mod._brand_cache = None
    brand_mod._banner_cache = None


# ---------------------------------------------------------------------------
# get_brand()
# ---------------------------------------------------------------------------

class TestGetBrand:
    def test_returns_dict_with_required_keys(self):
        from tools.dashboard.brand import get_brand
        b = get_brand()
        for key in ("product_name", "product_short", "tagline", "primary_color",
                    "secondary_color", "copyright", "logo_path"):
            assert key in b, f"Missing key: {key}"

    def test_default_product_name(self, monkeypatch):
        monkeypatch.delenv("ICDEV_BRAND_PRODUCT_NAME", raising=False)
        from tools.dashboard.brand import get_brand
        assert "ICDEV" in get_brand()["product_name"]

    def test_env_overrides_product_name(self, monkeypatch):
        monkeypatch.setenv("ICDEV_BRAND_PRODUCT_NAME", "AcmeOps™")
        from tools.dashboard.brand import get_brand
        assert get_brand()["product_name"] == "AcmeOps™"

    def test_env_overrides_primary_color(self, monkeypatch):
        monkeypatch.setenv("ICDEV_BRAND_PRIMARY_COLOR", "#ff0000")
        from tools.dashboard.brand import get_brand
        assert get_brand()["primary_color"] == "#ff0000"

    def test_custom_brand_yaml(self, tmp_path, monkeypatch):
        brand_file = tmp_path / "brand.yaml"
        brand_file.write_text(textwrap.dedent("""
            product_name: "WhiteLabel™"
            product_short: "WL"
            primary_color: "#00ff00"
            copyright: "© 2026 WhiteLabel Inc."
        """), encoding="utf-8")
        monkeypatch.setenv("ICDEV_BRAND_PATH", str(brand_file))
        monkeypatch.delenv("ICDEV_BRAND_PRODUCT_NAME", raising=False)
        from tools.dashboard.brand import get_brand
        b = get_brand()
        assert b["product_name"] == "WhiteLabel™"
        assert b["primary_color"] == "#00ff00"
        assert b["copyright"] == "© 2026 WhiteLabel Inc."

    def test_missing_brand_yaml_returns_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ICDEV_BRAND_PATH", str(tmp_path / "nonexistent.yaml"))
        monkeypatch.delenv("ICDEV_BRAND_PRODUCT_NAME", raising=False)
        from tools.dashboard.brand import get_brand
        b = get_brand()
        assert b["product_name"]  # still has a default


# ---------------------------------------------------------------------------
# get_banner()
# ---------------------------------------------------------------------------

class TestGetBanner:
    def test_returns_dict_with_required_keys(self):
        from tools.dashboard.brand import get_banner
        b = get_banner()
        for key in ("enabled", "mode", "text", "background_color", "text_color", "position"):
            assert key in b, f"Missing key: {key}"

    def test_cui_mode_preset(self, tmp_path, monkeypatch):
        banner_file = tmp_path / "banner.yaml"
        banner_file.write_text("mode: cui\n", encoding="utf-8")
        monkeypatch.setenv("ICDEV_BANNER_PATH", str(banner_file))
        monkeypatch.delenv("ICDEV_BANNER_MODE", raising=False)
        monkeypatch.delenv("ICDEV_CUI_BANNER_ENABLED", raising=False)
        from tools.dashboard.brand import get_banner
        b = get_banner()
        assert b["enabled"] is True
        assert "CUI" in b["text"]
        assert b["mode"] == "cui"

    def test_none_mode_disables_banner(self, monkeypatch):
        monkeypatch.setenv("ICDEV_BANNER_MODE", "none")
        from tools.dashboard.brand import get_banner
        b = get_banner()
        assert b["enabled"] is False

    def test_secret_mode_preset(self, monkeypatch):
        monkeypatch.setenv("ICDEV_BANNER_MODE", "secret")
        from tools.dashboard.brand import get_banner
        b = get_banner()
        assert b["enabled"] is True
        assert b["text"] == "SECRET"
        assert "#8b0000" in b["background_color"].lower() or "8b0000" in b["background_color"].lower()

    def test_il6_mode_preset(self, monkeypatch):
        monkeypatch.setenv("ICDEV_BANNER_MODE", "il6")
        from tools.dashboard.brand import get_banner
        b = get_banner()
        assert b["enabled"] is True
        assert "SECRET" in b["text"]

    def test_env_mode_wins_over_yaml(self, tmp_path, monkeypatch):
        banner_file = tmp_path / "banner.yaml"
        banner_file.write_text("mode: cui\n", encoding="utf-8")
        monkeypatch.setenv("ICDEV_BANNER_PATH", str(banner_file))
        monkeypatch.setenv("ICDEV_BANNER_MODE", "none")
        from tools.dashboard.brand import get_banner
        assert get_banner()["enabled"] is False

    def test_backward_compat_cui_banner_enabled(self, tmp_path, monkeypatch):
        # When ICDEV_CUI_BANNER_ENABLED=true and no ICDEV_BANNER_MODE or banner.yaml
        # is present, mode should fall back to cui (backward compat for old deployments).
        monkeypatch.delenv("ICDEV_BANNER_MODE", raising=False)
        monkeypatch.setenv("ICDEV_CUI_BANNER_ENABLED", "true")
        monkeypatch.setenv("ICDEV_BANNER_PATH", str(tmp_path / "nonexistent.yaml"))
        from tools.dashboard.brand import get_banner
        b = get_banner()
        assert b["mode"] == "cui"
        assert b["enabled"] is True

    def test_custom_mode_with_override_text(self, tmp_path, monkeypatch):
        banner_file = tmp_path / "banner.yaml"
        banner_file.write_text(textwrap.dedent("""
            mode: custom
            text: "ACME INTERNAL USE ONLY"
            background_color: "#003366"
            text_color: "#ffffff"
            position: both
        """), encoding="utf-8")
        monkeypatch.setenv("ICDEV_BANNER_PATH", str(banner_file))
        monkeypatch.delenv("ICDEV_BANNER_MODE", raising=False)
        monkeypatch.delenv("ICDEV_CUI_BANNER_ENABLED", raising=False)
        from tools.dashboard.brand import get_banner
        b = get_banner()
        assert b["text"] == "ACME INTERNAL USE ONLY"
        assert b["background_color"] == "#003366"
        assert b["position"] == "both"


# ---------------------------------------------------------------------------
# brand_context_processor()
# ---------------------------------------------------------------------------

class TestBrandContextProcessor:
    def test_returns_brand_and_banner_keys(self):
        from tools.dashboard.brand import brand_context_processor
        ctx = brand_context_processor()
        assert "brand" in ctx
        assert "banner" in ctx

    def test_brand_is_dict(self):
        from tools.dashboard.brand import brand_context_processor
        ctx = brand_context_processor()
        assert isinstance(ctx["brand"], dict)
        assert "product_name" in ctx["brand"]

    def test_banner_is_dict(self):
        from tools.dashboard.brand import brand_context_processor
        ctx = brand_context_processor()
        assert isinstance(ctx["banner"], dict)
        assert "enabled" in ctx["banner"]

    def test_reload_cache(self, monkeypatch):
        from tools.dashboard import brand as brand_mod
        monkeypatch.setenv("ICDEV_BRAND_PRODUCT_NAME", "First™")
        b1 = brand_mod.get_brand()
        assert b1["product_name"] == "First™"
        monkeypatch.setenv("ICDEV_BRAND_PRODUCT_NAME", "Second™")
        brand_mod.reload_cache()
        b2 = brand_mod.get_brand()
        assert b2["product_name"] == "Second™"
