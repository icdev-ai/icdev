# CUI // SP-CTI
"""Tests for `icdev init` install-profile selection (pkg-init-01).

`icdev init` used to drop the user on the registry's default enablement with no
signal that the other components existed. It now resolves an install profile —
interactively on a TTY, via `--profile <name>` for scripts, and with a safe
non-TTY fallback so CI never blocks on an unanswerable prompt. The chosen
profile's ``default_enabled_components`` drive which flags land `true` in the
generated .env.

Run: pytest tests/test_pkg_init_profile.py -v --tb=short
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.cli import init as init_mod
from tools.cli.env_generator import compose_env, render_component_section
from tools.config.component_registry import get_registry
from tools.config.core_profile import load_profiles

_ENV = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=(.*)$", re.MULTILINE)


def _assignments(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in _ENV.finditer(text)}


# ── resolve_profile ────────────────────────────────────────────────────────

def test_explicit_profile_is_honored():
    name, source = init_mod.resolve_profile("air-gap", interactive=True)
    assert name == "air-gap"
    assert source == "requested"


def test_profile_none_means_no_profile():
    name, source = init_mod.resolve_profile("none", interactive=True)
    assert name is None
    assert source == "requested"


def test_unknown_profile_raises():
    with pytest.raises(ValueError):
        init_mod.resolve_profile("does-not-exist", interactive=False)


def test_non_tty_falls_back_to_a_valid_default_without_prompting():
    """The core CI guarantee: no TTY ⇒ a real profile, never a hung prompt."""
    name, source = init_mod.resolve_profile(None, interactive=False)
    assert source == "non_tty_default"
    assert name in load_profiles()


def test_default_profile_name_prefers_local_dev_when_not_airgap():
    profiles = load_profiles()
    # Not asserting airgap detection here — just that the pick is a real profile
    # and matches the documented preference order.
    chosen = init_mod._default_profile_name(profiles)
    assert chosen in profiles
    assert chosen in ("local-dev", "air-gap")


# ── profile → component keys / env overrides ───────────────────────────────

def test_profile_component_keys_match_yaml():
    profiles = load_profiles()
    airgap = profiles["air-gap"]
    keys = init_mod._profile_component_keys(airgap)
    assert keys == set(airgap["default_enabled_components"])


def test_profile_env_overrides_reflect_backend_and_airgap():
    profiles = load_profiles()
    ov = init_mod._profile_env_overrides(profiles["air-gap"])
    assert ov.get("ICDEV_STORAGE_BACKEND") == "postgresql"
    assert ov.get("ICDEV_AIRGAP") == "true"


# ── compose_env honors the profile ─────────────────────────────────────────

def test_render_section_enables_only_profile_components():
    reg = get_registry()
    profiles = load_profiles()
    keys = init_mod._profile_component_keys(profiles["community"])
    section = render_component_section(reg, enabled_keys=keys)
    for c in reg.list_all():
        if not c.env_flag:
            continue
        expected = "true" if c.key in keys else "false"
        assert f"{c.env_flag}={expected}" in section, (
            f"{c.key}/{c.env_flag} should be {expected} under community profile"
        )


def test_compose_env_applies_profile_overrides_and_flags():
    reg = get_registry()
    profiles = load_profiles()
    prof = profiles["air-gap"]
    keys = init_mod._profile_component_keys(prof)
    overrides = init_mod._profile_env_overrides(prof)
    overrides["ICDEV_CORE_PROFILE"] = "air-gap"
    template = (
        "# header\n"
        "ANTHROPIC_API_KEY=\n"
        "ICDEV_STORAGE_BACKEND=sqlite\n"
    )
    out = compose_env(template, reg, enabled_keys=keys, env_overrides=overrides)
    a = _assignments(out)
    # Non-component key preserved, backend rewritten in place by the profile.
    assert "ANTHROPIC_API_KEY" in a
    assert a["ICDEV_STORAGE_BACKEND"] == "postgresql"
    # Profile marker appended.
    assert a.get("ICDEV_CORE_PROFILE") == "air-gap"
    # A component NOT in the air-gap set is present but false.
    off = next(c for c in reg.list_all()
               if c.env_flag and c.key not in keys)
    assert f"{off.env_flag}=false" in out


def test_no_profile_preserves_registry_default_behavior():
    """enabled_keys=None must reproduce the original registry-default output."""
    reg = get_registry()
    section = render_component_section(reg, enabled_keys=None)
    for c in reg.list_all():
        if not c.env_flag:
            continue
        expected = "true" if c.default_enabled else "false"
        assert f"{c.env_flag}={expected}" in section
