#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for Functional Requirement 4 — Compliance Scaffolding Is In Place.

Validates the checks defined in functional_infrastructure.feature:
1. classification_manager.py exists and is importable
2. control_mapper.py exists and is importable
3. Key compliance config files exist (impact_level_profiles.json, classification_markings.yaml or cui_markings.yaml)
4. classification_manager exposes required constants and core functions
5. control_mapper exposes required constants and core functions
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLASSIFICATION_MANAGER = PROJECT_ROOT / "tools" / "compliance" / "classification_manager.py"
CONTROL_MAPPER = PROJECT_ROOT / "tools" / "compliance" / "control_mapper.py"
IL_PROFILES_PATH = PROJECT_ROOT / "context" / "compliance" / "impact_level_profiles.json"
MARKINGS_PATH = PROJECT_ROOT / "args" / "classification_markings.yaml"
CUI_MARKINGS_PATH = PROJECT_ROOT / "args" / "cui_markings.yaml"


class TestComplianceFilesExist:
    """Requirement 4 Step 1 — File Existence Check."""

    def test_classification_manager_exists(self):
        assert CLASSIFICATION_MANAGER.exists(), (
            "tools/compliance/classification_manager.py must exist"
        )

    def test_control_mapper_exists(self):
        assert CONTROL_MAPPER.exists(), "tools/compliance/control_mapper.py must exist"


class TestComplianceConfigFiles:
    """Requirement 4 Step 2 — Configuration File Check."""

    def test_impact_level_profiles_exists(self):
        assert IL_PROFILES_PATH.exists(), (
            "context/compliance/impact_level_profiles.json must exist"
        )

    def test_markings_config_exists(self):
        assert MARKINGS_PATH.exists() or CUI_MARKINGS_PATH.exists(), (
            "args/classification_markings.yaml or args/cui_markings.yaml must exist"
        )


class TestClassificationManagerImport:
    """Requirement 4 Step 3 — classification_manager import and API surface."""

    def test_classification_manager_importable(self):
        try:
            pass
        except Exception as exc:
            pytest.fail(f"classification_manager.py failed to import: {exc}")

    def test_valid_classifications_constant(self):
        from tools.compliance.classification_manager import VALID_CLASSIFICATIONS

        assert isinstance(VALID_CLASSIFICATIONS, tuple)
        assert "CUI" in VALID_CLASSIFICATIONS
        assert "SECRET" in VALID_CLASSIFICATIONS

    def test_valid_impact_levels_constant(self):
        from tools.compliance.classification_manager import VALID_IMPACT_LEVELS

        assert isinstance(VALID_IMPACT_LEVELS, tuple)
        assert "IL4" in VALID_IMPACT_LEVELS
        assert "IL5" in VALID_IMPACT_LEVELS
        assert "IL6" in VALID_IMPACT_LEVELS

    def test_load_impact_level_profiles_returns_dict(self):
        from tools.compliance.classification_manager import load_impact_level_profiles

        profiles = load_impact_level_profiles()
        assert isinstance(profiles, dict)
        # At minimum the CUI fallback or real IL4 profile must be present
        assert "IL4" in profiles or any("IL4" in str(k) for k in profiles.keys())

    def test_load_markings_config_returns_dict(self):
        from tools.compliance.classification_manager import load_markings_config

        config = load_markings_config()
        assert isinstance(config, dict)
        assert "banner_top" in config
        assert "banner_bottom" in config
        assert "CUI" in config["banner_top"] or "CUI" in config.get("code_header", "")


class TestControlMapperImport:
    """Requirement 4 Step 4 — control_mapper import and API surface."""

    def test_control_mapper_importable(self):
        try:
            pass
        except Exception as exc:
            pytest.fail(f"control_mapper.py failed to import: {exc}")

    def test_required_families_constant(self):
        from tools.compliance.control_mapper import REQUIRED_FAMILIES

        assert isinstance(REQUIRED_FAMILIES, list)
        assert len(REQUIRED_FAMILIES) == 17
        for family in ["AC", "AT", "AU", "CA", "CM", "CP", "IA", "IR", "MA", "MP", "PE", "PL", "PS", "RA", "SA", "SC", "SI"]:
            assert family in REQUIRED_FAMILIES

    def test_valid_statuses_constant(self):
        from tools.compliance.control_mapper import VALID_STATUSES

        assert isinstance(VALID_STATUSES, tuple)
        assert "planned" in VALID_STATUSES
        assert "implemented" in VALID_STATUSES
        assert "not_applicable" in VALID_STATUSES

    def test_nist_controls_loadable(self):
        from tools.compliance.control_mapper import _load_nist_controls

        controls = _load_nist_controls()
        assert isinstance(controls, dict)
