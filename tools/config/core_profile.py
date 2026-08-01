# CUI // SP-CTI
"""Core Profile loader — reads args/core_profiles.yaml.

Provides enterprise deployment presets (local-dev, air-gap, saas-il4, il6-secret)
as a simple Python API. Values are intentionally defaults; env vars still win.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.core_profile")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_PATH = BASE_DIR / "args" / "core_profiles.yaml"


def _import_yaml() -> Any:
    try:
        import yaml

        return yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load core profiles") from exc


def load_profiles(path: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """Load core profiles from YAML and return {profile_name: profile_dict}."""
    yaml = _import_yaml()
    path = Path(path or DEFAULT_PATH)
    if not path.exists():
        logger.warning("Core profiles file not found: %s", path)
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    profiles = data.get("profiles", {})
    if not isinstance(profiles, dict):
        logger.error("core_profiles.yaml 'profiles' must be a mapping")
        return {}
    return profiles


def get_profile(name: str, path: Path | str | None = None) -> dict[str, Any] | None:
    """Return a single profile by name, or None if not found."""
    return load_profiles(path).get(name)


def get_active_profile(path: Path | str | None = None) -> dict[str, Any] | None:
    """Return the active profile from env var ICDEV_CORE_PROFILE, or None."""
    profile_name = os.environ.get("ICDEV_CORE_PROFILE")
    if not profile_name:
        return None
    profile = get_profile(profile_name, path)
    if profile is None:
        logger.warning("ICDEV_CORE_PROFILE=%s not found in core profiles", profile_name)
    return profile


def profile_env_overrides(profile: dict[str, Any]) -> dict[str, str]:
    """Return a flat dict of env-style overrides implied by a profile.

    This is used by tools that read env vars directly (e.g., storage backend,
    dashboard config). Only returns keys that are not already set in the
    environment so env vars always win.
    """
    overrides: dict[str, str] = {}

    storage_backend = profile.get("storage_backend")
    if storage_backend and not os.environ.get("ICDEV_STORAGE_BACKEND"):
        overrides["ICDEV_STORAGE_BACKEND"] = storage_backend

    license_tier = profile.get("license_tier")
    if license_tier and not os.environ.get("ICDEV_LICENSE_TIER"):
        overrides["ICDEV_LICENSE_TIER"] = license_tier

    if profile.get("airgap") and not os.environ.get("ICDEV_AIRGAP"):
        overrides["ICDEV_AIRGAP"] = "true"

    if profile.get("enable_byok") and not os.environ.get("ICDEV_BYOK_ENABLED"):
        overrides["ICDEV_BYOK_ENABLED"] = "true"

    if profile.get("enable_cui_banner") and not os.environ.get("ICDEV_CUI_BANNER_ENABLED"):
        overrides["ICDEV_CUI_BANNER_ENABLED"] = "true"

    if profile.get("classification") and not os.environ.get("ICDEV_CLASSIFICATION"):
        overrides["ICDEV_CLASSIFICATION"] = profile["classification"]

    if profile.get("banner_mode") and not os.environ.get("ICDEV_BANNER_MODE"):
        overrides["ICDEV_BANNER_MODE"] = profile["banner_mode"]

    if profile.get("brand_path") and not os.environ.get("ICDEV_BRAND_PATH"):
        raw = profile["brand_path"]
        resolved = str(BASE_DIR / raw) if not Path(raw).is_absolute() else raw
        overrides["ICDEV_BRAND_PATH"] = resolved

    llm = profile.get("llm", {})
    if llm.get("provider") and not os.environ.get("ICDEV_LLM_PROVIDER"):
        overrides["ICDEV_LLM_PROVIDER"] = llm["provider"]
    if llm.get("default_model") and not os.environ.get("ICDEV_LLM_DEFAULT_MODEL"):
        overrides["ICDEV_LLM_DEFAULT_MODEL"] = llm["default_model"]

    return overrides


def _active_profile_env_defaults(path: Path | str | None = None) -> dict[str, str]:
    """Load the active profile and return env-style defaults not already set."""
    profile = get_active_profile(path)
    if profile is None:
        return {}
    return profile_env_overrides(profile)


def profile_default(env_var: str, fallback: Any, path: Path | str | None = None) -> Any:
    """Return env var value, active-profile default, or fallback.

    Use this in modules that need to respect `ICDEV_CORE_PROFILE` without
    requiring `icdev profile apply` to have been run first.
    """
    env_val = os.environ.get(env_var)
    if env_val:
        return env_val
    profile_defaults = _active_profile_env_defaults(path)
    if env_var in profile_defaults:
        return profile_defaults[env_var]
    return fallback


def apply_active_profile_env_defaults(path: Path | str | None = None) -> dict[str, str]:
    """Set environment variables from the active profile if not already set.

    Returns the keys that were set. Safe to call repeatedly; idempotent.
    """
    applied: dict[str, str] = {}
    for key, value in _active_profile_env_defaults(path).items():
        if key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied
