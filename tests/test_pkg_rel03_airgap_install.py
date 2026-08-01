# CUI // SP-CTI
"""Tests for the air-gap offline install verification (pkg-rel-03).

The release must prove the air-gap path end to end: install the wheel with NO
network, confirm the air-gap profile enables the expected components, and assert
the NEGATIVE — that no google-auth / google-generativeai / google-cloud-aiplatform
/ tensorboard slipped into an air-gap extra. These tests cover the pure helpers
that back those assertions; the full offline-venv step is exercised only when
build_release.py runs on a machine that can pre-stage a wheelhouse.

Run: pytest tests/test_pkg_rel03_airgap_install.py -v --tb=short
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.installer.build_release import (
    _AIRGAP_PROFILE,
    _FORBIDDEN_AIRGAP_PACKAGES,
    _airgap_expected_count,
    _forbidden_airgap_packages,
)


def _pip_list(*names: str) -> str:
    return json.dumps([{"name": n, "version": "1.0"} for n in names])


# ── _forbidden_airgap_packages ─────────────────────────────────────────────

def test_clean_airgap_install_has_no_forbidden_packages():
    listing = _pip_list("icdev", "openai", "anthropic", "numpy", "networkx")
    assert _forbidden_airgap_packages(listing) == []


def test_detects_google_auth():
    listing = _pip_list("icdev", "google-auth", "openai")
    assert "google-auth" in _forbidden_airgap_packages(listing)


def test_detects_all_forbidden_normalizing_underscores():
    # pip may report names with underscores or different case.
    listing = _pip_list("Google_Auth", "google-generativeai",
                        "google-cloud-aiplatform", "TensorBoard")
    found = _forbidden_airgap_packages(listing)
    assert set(found) == set(_FORBIDDEN_AIRGAP_PACKAGES)


def test_malformed_pip_output_is_safe():
    assert _forbidden_airgap_packages("not json") == []
    assert _forbidden_airgap_packages("") == []


# ── _airgap_expected_count ─────────────────────────────────────────────────

def test_airgap_profile_component_count_matches_yaml():
    from tools.config.core_profile import get_profile

    expected = len(get_profile(_AIRGAP_PROFILE)["default_enabled_components"])
    assert _airgap_expected_count() == expected
    assert _airgap_expected_count() > 0


def test_unknown_profile_returns_zero_count():
    assert _airgap_expected_count("no-such-profile") == 0
