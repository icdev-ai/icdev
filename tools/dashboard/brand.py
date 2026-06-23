# CUI // SP-CTI
"""Brand and banner configuration for ICDEV™ Design System (DSW-1).

Loads args/brand.yaml and args/banner.yaml and exposes them to Flask templates
via a context processor. Supports white-label deployments by:
  - Overriding any value via environment variables (ICDEV_BRAND_*)
  - Pointing ICDEV_BRAND_PATH / ICDEV_BANNER_PATH at custom YAML files
  - Setting ICDEV_BANNER_MODE=none to suppress all classification markings

Usage in Flask app:
    from tools.dashboard.brand import brand_context_processor
    app.context_processor(brand_context_processor)

Usage in Jinja2 templates:
    {{ brand.product_name }}      → "ICDEV™" or white-label name
    {{ banner.enabled }}          → True/False
    {{ banner.text }}             → "CUI // SP-CTI" or custom text
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent

_BANNER_PRESETS: dict[str, dict[str, Any]] = {
    "none": {
        "enabled": False,
        "text": "",
        "background_color": "transparent",
        "text_color": "#ffffff",
    },
    "cui": {
        "enabled": True,
        "text": "CUI // SP-CTI",
        "background_color": "#5a4e00",
        "text_color": "#ffd700",
    },
    "secret": {
        "enabled": True,
        "text": "SECRET",
        "background_color": "#8b0000",
        "text_color": "#ffffff",
    },
    "il6": {
        "enabled": True,
        "text": "SECRET // SI",
        "background_color": "#4a0080",
        "text_color": "#ffffff",
    },
    "demo": {
        "enabled": True,
        "text": "DEMO MODE — Read Only",
        "background_color": "#1a472a",
        "text_color": "#ffffff",
    },
}

_brand_cache: dict[str, Any] | None = None
_banner_cache: dict[str, Any] | None = None


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _brand_path() -> Path:
    custom = os.environ.get("ICDEV_BRAND_PATH")
    return Path(custom) if custom else BASE_DIR / "args" / "brand.yaml"


def _banner_path() -> Path:
    custom = os.environ.get("ICDEV_BANNER_PATH")
    return Path(custom) if custom else BASE_DIR / "args" / "banner.yaml"


def get_brand(reload: bool = False) -> dict[str, Any]:
    """Return the active brand config dict."""
    global _brand_cache
    if _brand_cache is None or reload:
        data = _load_yaml(_brand_path())
        _brand_cache = {
            "product_name": os.environ.get(
                "ICDEV_BRAND_PRODUCT_NAME", data.get("product_name", "ICDEV™")
            ),
            "product_short": os.environ.get(
                "ICDEV_BRAND_PRODUCT_SHORT", data.get("product_short", "ICDEV")
            ),
            "tagline": data.get("tagline", "AI-Native Enterprise Platform"),
            "version": os.environ.get(
                "ICDEV_BRAND_VERSION", data.get("version", "")
            ),
            "logo_path": data.get("logo_path", "img/icdev-logo.svg"),
            "logo_width": data.get("logo_width", "120px"),
            "favicon_path": data.get("favicon_path", "img/favicon.ico"),
            "primary_color": os.environ.get(
                "ICDEV_BRAND_PRIMARY_COLOR", data.get("primary_color", "#2563eb")
            ),
            "secondary_color": os.environ.get(
                "ICDEV_BRAND_SECONDARY_COLOR", data.get("secondary_color", "#7c3aed")
            ),
            "copyright": data.get("copyright", "© 2026 ICDEV. All rights reserved."),
            "support_url": data.get("support_url", ""),
            "docs_url": data.get("docs_url", ""),
            "privacy_url": data.get("privacy_url", ""),
        }
    return _brand_cache


def get_banner(reload: bool = False) -> dict[str, Any]:
    """Return the active banner config dict.

    Reads args/banner.yaml, applies the named preset, then overlays any
    custom fields. env var ICDEV_BANNER_MODE wins over the yaml mode.
    Backward-compat: ICDEV_CUI_BANNER_ENABLED=true implies mode=cui when
    ICDEV_BANNER_MODE is not set.
    """
    global _banner_cache
    if _banner_cache is None or reload:
        data = _load_yaml(_banner_path())

        # Resolve mode — priority: ICDEV_BANNER_MODE env > CUI_BANNER_ENABLED env > DEMO_MODE > yaml > default
        mode = os.environ.get("ICDEV_BANNER_MODE", "").strip().lower()
        _from_env = bool(mode)
        if not mode:
            # Backward-compat: ICDEV_CUI_BANNER_ENABLED=true (old flag) implies cui
            if os.environ.get("ICDEV_CUI_BANNER_ENABLED", "").lower() in ("1", "true", "yes"):
                mode = "cui"
                _from_env = True
        if not mode:
            # Demo mode fallback: ICDEV_DEMO_MODE=true implies demo banner when no explicit mode set
            if os.environ.get("ICDEV_DEMO_MODE", "").lower() in ("1", "true", "yes"):
                mode = "demo"
                _from_env = True
        if not mode:
            mode = str(data.get("mode", "none")).strip().lower()
        if mode not in _BANNER_PRESETS:
            mode = "none"

        banner = _BANNER_PRESETS[mode].copy()
        banner["mode"] = mode

        # Apply yaml text/color overrides ONLY when mode=custom or mode came from yaml
        # (not when the mode was forced by env var, which should use the clean preset)
        _apply_yaml_overrides = not _from_env or mode == "custom"
        if _apply_yaml_overrides:
            if data.get("text"):
                banner["text"] = data["text"]
            if data.get("background_color"):
                banner["background_color"] = data["background_color"]
            if data.get("text_color"):
                banner["text_color"] = data["text_color"]

        banner["position"] = data.get("position", "top")
        banner["font_size"] = data.get("font_size", "0.75rem")
        banner["font_weight"] = data.get("font_weight", "600")
        banner["letter_spacing"] = data.get("letter_spacing", "0.08em")

        _banner_cache = banner
    return _banner_cache


def brand_context_processor() -> dict[str, Any]:
    """Flask context processor — injects brand + banner into all templates."""
    return {
        "brand": get_brand(),
        "banner": get_banner(),
    }


def reload_cache() -> None:
    """Force reload on next access (useful after hot-reloading brand/banner yaml)."""
    global _brand_cache, _banner_cache
    _brand_cache = None
    _banner_cache = None
