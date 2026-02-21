# CUI // SP-CTI
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.devsecops.profile_manager — DevSecOps profile CRUD, maturity
detection, maturity assessment, and config fallback behavior."""

import json
import sqlite3
from unittest.mock import patch

import pytest

from tools.devsecops.profile_manager import (
    DB_PATH,
    _load_config,
    assess_maturity,
    create_profile,
    detect_maturity_from_text,
    get_profile,
    update_profile,
)

# ---------------------------------------------------------------------------
# Schema extension — devsecops_profiles table (not in conftest MINIMAL_ICDEV_SCHEMA)
# ---------------------------------------------------------------------------
DEVSECOPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS devsecops_profiles (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    maturity_level TEXT CHECK(maturity_level IN (
        'level_1_initial', 'level_2_managed', 'level_3_defined',
        'level_4_measured', 'level_5_optimized'
    )),
    active_stages TEXT,
    stage_configs TEXT,
    detected_at TEXT,
    confirmed_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id)
);
"""


@pytest.fixture
def devsecops_db(icdev_db):
    """Extend the shared icdev_db fixture with the devsecops_profiles table."""
    conn = sqlite3.connect(str(icdev_db))
    conn.executescript(DEVSECOPS_SCHEMA)
    conn.close()
    return icdev_db


# ---------------------------------------------------------------------------
# TestCreateProfile
# ---------------------------------------------------------------------------

class TestCreateProfile:
    """create_profile: insert a new DevSecOps profile into the database."""

    def test_create_with_default_maturity(self, devsecops_db):
        with patch("tools.devsecops.profile_manager.DB_PATH", devsecops_db):
            result = create_profile("proj-test-001")

        assert result["status"] == "created"
        assert result["project_id"] == "proj-test-001"
        assert result["maturity_level"] == "level_3_defined"
        assert "id" in result
        assert result["id"].startswith("dsp-")

    def test_create_with_explicit_maturity(self, devsecops_db):
        with patch("tools.devsecops.profile_manager.DB_PATH", devsecops_db):
            result = create_profile("proj-test-001", maturity_level="level_2_managed")

        assert result["maturity_level"] == "level_2_managed"
        assert "sast" in result["active_stages"]
        assert "sca" in result["active_stages"]

    def test_create_with_invalid_maturity_returns_error(self, devsecops_db):
        with patch("tools.devsecops.profile_manager.DB_PATH", devsecops_db):
            result = create_profile("proj-test-001", maturity_level="level_99_fake")

        assert "error" in result
        assert "Invalid maturity level" in result["error"]
        assert "valid_levels" in result

    def test_create_with_explicit_stages(self, devsecops_db):
        with patch("tools.devsecops.profile_manager.DB_PATH", devsecops_db):
            result = create_profile("proj-test-001", stages=["sast", "dast"])

        assert result["active_stages"] == ["dast", "sast"]

    def test_create_with_stage_configs(self, devsecops_db):
        cfg = {"sast": {"tool": "bandit"}}
        with patch("tools.devsecops.profile_manager.DB_PATH", devsecops_db):
            result = create_profile("proj-test-001", stage_configs=cfg)

        assert result["stage_configs"] == cfg

    def test_create_persists_to_database(self, devsecops_db):
        with patch("tools.devsecops.profile_manager.DB_PATH", devsecops_db):
            create_profile("proj-test-001")
            fetched = get_profile("proj-test-001")

        assert "error" not in fetched
        assert fetched["project_id"] == "proj-test-001"


# ---------------------------------------------------------------------------
# TestGetProfile
# ---------------------------------------------------------------------------

class TestGetProfile:
    """get_profile: retrieve a stored profile by project ID."""

    def test_get_existing_profile(self, devsecops_db):
        with patch("tools.devsecops.profile_manager.DB_PATH", devsecops_db):
            create_profile("proj-test-001", maturity_level="level_4_measured")
            result = get_profile("proj-test-001")

        assert result["project_id"] == "proj-test-001"
        assert result["maturity_level"] == "level_4_measured"
        assert isinstance(result["active_stages"], list)
        assert "created_at" in result
        assert "updated_at" in result

    def test_get_nonexistent_profile_returns_error(self, devsecops_db):
        with patch("tools.devsecops.profile_manager.DB_PATH", devsecops_db):
            result = get_profile("proj-does-not-exist")

        assert "error" in result
        assert "No DevSecOps profile" in result["error"]
        assert "hint" in result


# ---------------------------------------------------------------------------
# TestUpdateProfile
# ---------------------------------------------------------------------------

class TestUpdateProfile:
    """update_profile: modify active stages and maturity level."""

    def test_enable_stage(self, devsecops_db):
        with patch("tools.devsecops.profile_manager.DB_PATH", devsecops_db):
            create_profile("proj-test-001", maturity_level="level_2_managed")
            result = update_profile("proj-test-001", enable=["dast"])

        assert result["status"] == "updated"
        assert "dast" in result["active_stages"]

    def test_disable_stage(self, devsecops_db):
        with patch("tools.devsecops.profile_manager.DB_PATH", devsecops_db):
            create_profile("proj-test-001", stages=["sast", "sca", "dast"])
            result = update_profile("proj-test-001", disable=["dast"])

        assert "dast" not in result["active_stages"]
        assert "sast" in result["active_stages"]

    def test_update_maturity_level(self, devsecops_db):
        with patch("tools.devsecops.profile_manager.DB_PATH", devsecops_db):
            create_profile("proj-test-001")
            result = update_profile("proj-test-001", maturity_level="level_5_optimized")

        assert result["maturity_level"] == "level_5_optimized"

    def test_update_nonexistent_profile_returns_error(self, devsecops_db):
        with patch("tools.devsecops.profile_manager.DB_PATH", devsecops_db):
            result = update_profile("proj-ghost", enable=["sast"])

        assert "error" in result


# ---------------------------------------------------------------------------
# TestDetectMaturity
# ---------------------------------------------------------------------------

class TestDetectMaturity:
    """detect_maturity_from_text: pure-function keyword-based detection."""

    def test_no_keywords_returns_level_1(self):
        result = detect_maturity_from_text("We need a web application.")
        assert result["maturity_estimate"] == "level_1_initial"
        assert result["detected_stages"] == []
        assert result["stage_count"] == 0

    def test_detects_sast_keyword(self):
        result = detect_maturity_from_text("We already use static analysis in CI.")
        assert "sast" in result["detected_stages"]

    def test_detects_multiple_stages(self):
        result = detect_maturity_from_text(
            "We run static analysis, dependency scan, secret scanning, and container scanning."
        )
        assert "sast" in result["detected_stages"]
        assert "sca" in result["detected_stages"]
        assert "secret_detection" in result["detected_stages"]
        assert "container_scan" in result["detected_stages"]

    def test_zta_keyword_detected(self):
        result = detect_maturity_from_text("Our architecture requires zero trust networking.")
        assert result["zta_detected"] is True

    def test_zta_not_detected_without_keyword(self):
        result = detect_maturity_from_text("Standard network architecture with firewalls.")
        assert result["zta_detected"] is False

    def test_greenfield_resets_to_level_1(self):
        result = detect_maturity_from_text(
            "We use static analysis and dependency scan but we are starting from scratch."
        )
        assert result["greenfield"] is True
        assert result["maturity_estimate"] == "level_1_initial"

    def test_case_insensitive_matching(self):
        result = detect_maturity_from_text("We run STATIC ANALYSIS on every commit.")
        assert "sast" in result["detected_stages"]


# ---------------------------------------------------------------------------
# TestAssessMaturity
# ---------------------------------------------------------------------------

class TestAssessMaturity:
    """assess_maturity: evaluate profile against maturity requirements."""

    def test_assess_level_3_requirements_met(self, devsecops_db):
        with patch("tools.devsecops.profile_manager.DB_PATH", devsecops_db):
            create_profile("proj-test-001", maturity_level="level_3_defined")
            result = assess_maturity("proj-test-001")

        assert result["project_id"] == "proj-test-001"
        assert result["current_level"] == "level_3_defined"
        assert result["requirements_met"] is True
        assert result["next_level"] == "level_4_measured"
        assert isinstance(result["gaps_for_next_level"], list)

    def test_assess_level_5_has_no_next_level(self, devsecops_db):
        all_stages = [
            "sast", "sca", "secret_detection", "container_scan",
            "policy_as_code", "sbom_attestation", "image_signing", "rasp",
        ]
        with patch("tools.devsecops.profile_manager.DB_PATH", devsecops_db):
            create_profile("proj-test-001", maturity_level="level_5_optimized",
                           stages=all_stages)
            result = assess_maturity("proj-test-001")

        assert result["current_level"] == "level_5_optimized"
        assert result["next_level"] is None
        assert result["recommendation"] == "At maximum maturity"

    def test_assess_nonexistent_returns_error(self, devsecops_db):
        with patch("tools.devsecops.profile_manager.DB_PATH", devsecops_db):
            result = assess_maturity("proj-ghost")

        assert "error" in result


# ---------------------------------------------------------------------------
# TestConfigFallback
# ---------------------------------------------------------------------------

class TestConfigFallback:
    """_load_config: YAML loading with built-in fallback defaults."""

    def test_fallback_has_devsecops_stages(self):
        with patch("tools.devsecops.profile_manager.yaml", None):
            config = _load_config()

        assert "devsecops_stages" in config
        assert "sast" in config["devsecops_stages"]
        assert config["devsecops_stages"]["sast"]["default"] is True

    def test_fallback_has_maturity_levels(self):
        with patch("tools.devsecops.profile_manager.yaml", None):
            config = _load_config()

        assert "maturity_levels" in config
        assert "level_1_initial" in config["maturity_levels"]
        assert "level_5_optimized" in config["maturity_levels"]

    def test_fallback_level_3_requires_four_stages(self):
        with patch("tools.devsecops.profile_manager.yaml", None):
            config = _load_config()

        level_3 = config["maturity_levels"]["level_3_defined"]
        assert level_3["min_stages"] == 4
        assert set(level_3["required_stages"]) == {
            "sast", "sca", "secret_detection", "container_scan"
        }


# CUI // SP-CTI
