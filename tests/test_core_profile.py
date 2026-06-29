# CUI // SP-CTI
"""Tests for tools/config/core_profile.py."""

from __future__ import annotations

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.config.core_profile import (
    apply_active_profile_env_defaults,
    load_profiles,
    get_profile,
    profile_default,
    profile_env_overrides,
)


def test_load_profiles_has_expected_keys():
    profiles = load_profiles()
    assert set(profiles.keys()) >= {"local-dev", "air-gap", "saas-il4", "il6-secret",
                                     "commercial", "government"}


def test_air_gap_profile_is_airgapped():
    profile = get_profile("air-gap")
    assert profile is not None
    assert profile["airgap"] is True
    assert profile["llm"]["provider"] == "ollama"
    assert profile["storage_backend"] == "postgresql"


def test_local_dev_profile_allows_cloud():
    profile = get_profile("local-dev")
    assert profile is not None
    assert profile["airgap"] is False
    assert profile["llm"]["provider"] == "anthropic"


def test_profile_env_overrides_respect_existing_env(monkeypatch):
    monkeypatch.setenv("ICDEV_CORE_PROFILE", "air-gap")
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")  # existing env wins
    profile = get_profile("air-gap")
    assert profile is not None
    overrides = profile_env_overrides(profile)
    assert "ICDEV_STORAGE_BACKEND" not in overrides
    assert overrides["ICDEV_AIRGAP"] == "true"


def test_profile_default_prefers_env_over_profile(monkeypatch):
    monkeypatch.setenv("ICDEV_CORE_PROFILE", "air-gap")
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    assert profile_default("ICDEV_STORAGE_BACKEND", "postgresql") == "sqlite"


def test_profile_default_falls_back_to_profile(monkeypatch):
    monkeypatch.setenv("ICDEV_CORE_PROFILE", "air-gap")
    monkeypatch.delenv("ICDEV_STORAGE_BACKEND", raising=False)
    assert profile_default("ICDEV_STORAGE_BACKEND", "postgresql") == "postgresql"


def test_profile_default_falls_back_to_hardcoded_default(monkeypatch):
    monkeypatch.delenv("ICDEV_CORE_PROFILE", raising=False)
    monkeypatch.delenv("ICDEV_STORAGE_BACKEND", raising=False)
    assert profile_default("ICDEV_STORAGE_BACKEND", "sqlite") == "sqlite"


def test_apply_active_profile_env_defaults_sets_unset_keys(monkeypatch):
    monkeypatch.setenv("ICDEV_CORE_PROFILE", "air-gap")
    monkeypatch.delenv("ICDEV_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ICDEV_AIRGAP", raising=False)
    applied = apply_active_profile_env_defaults()
    assert "ICDEV_LLM_PROVIDER" in applied
    assert applied["ICDEV_LLM_PROVIDER"] == "ollama"
    assert "ICDEV_AIRGAP" in applied
    assert os.environ["ICDEV_LLM_PROVIDER"] == "ollama"


def test_apply_active_profile_env_defaults_respects_existing_env(monkeypatch):
    monkeypatch.setenv("ICDEV_CORE_PROFILE", "air-gap")
    monkeypatch.setenv("ICDEV_LLM_PROVIDER", "openai")  # existing env wins
    applied = apply_active_profile_env_defaults()
    assert "ICDEV_LLM_PROVIDER" not in applied
    assert os.environ["ICDEV_LLM_PROVIDER"] == "openai"


# ── DSW-4: White-label profiles ──────────────────────────────────────────────

def test_commercial_profile_banner_none():
    profile = get_profile("commercial")
    assert profile is not None
    assert profile.get("banner_mode") == "none"


def test_commercial_profile_brand_path_set():
    profile = get_profile("commercial")
    assert profile is not None
    assert "commercial" in profile.get("brand_path", "")


def test_commercial_profile_env_overrides_sets_banner_mode(monkeypatch):
    monkeypatch.delenv("ICDEV_BANNER_MODE", raising=False)
    monkeypatch.delenv("ICDEV_BRAND_PATH", raising=False)
    profile = get_profile("commercial")
    overrides = profile_env_overrides(profile)
    assert overrides.get("ICDEV_BANNER_MODE") == "none"
    assert "ICDEV_BRAND_PATH" in overrides
    assert "commercial" in overrides["ICDEV_BRAND_PATH"]


def test_commercial_profile_env_overrides_brand_path_is_absolute(monkeypatch):
    monkeypatch.delenv("ICDEV_BRAND_PATH", raising=False)
    profile = get_profile("commercial")
    overrides = profile_env_overrides(profile)
    brand_path = overrides.get("ICDEV_BRAND_PATH", "")
    assert Path(brand_path).is_absolute()
    assert Path(brand_path).exists(), f"brand_path does not exist: {brand_path}"


def test_government_profile_has_cui_banner():
    profile = get_profile("government")
    assert profile is not None
    assert profile.get("banner_mode") == "cui" or profile.get("enable_cui_banner") is True


def test_banner_mode_env_wins_over_profile(monkeypatch):
    monkeypatch.setenv("ICDEV_BANNER_MODE", "secret")
    profile = get_profile("commercial")
    overrides = profile_env_overrides(profile)
    assert "ICDEV_BANNER_MODE" not in overrides


def test_brand_path_env_wins_over_profile(monkeypatch):
    monkeypatch.setenv("ICDEV_BRAND_PATH", "/custom/brand.yaml")
    profile = get_profile("commercial")
    overrides = profile_env_overrides(profile)
    assert "ICDEV_BRAND_PATH" not in overrides
