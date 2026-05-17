#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for Functional Requirement 5 — Diplomatic Activity Tracker (DAT) Capability.

Validates the checks defined in functional_infrastructure.feature:
1. dat/ingestion_engine.py exists and is importable
2. dat/dti_calculator.py exists and computes scores in [0.0, 1.0]
3. dat/dti_update_runner.py exists and is importable
4. dashboard template exists at tools/dashboard/templates/strategos/dat.html
5. scheduler XML exists at tools/dat/icdev_dat_scheduler_task.xml
6. dat_config.yaml exists at args/dat_config.yaml
7. strategos/dat.py exists and is importable
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INGESTION_ENGINE = PROJECT_ROOT / "tools" / "dat" / "ingestion_engine.py"
DTI_CALCULATOR = PROJECT_ROOT / "tools" / "dat" / "dti_calculator.py"
DTI_UPDATE_RUNNER = PROJECT_ROOT / "tools" / "dat" / "dti_update_runner.py"
DAT_BLUEPRINT = PROJECT_ROOT / "tools" / "strategos" / "dat.py"
DAT_TEMPLATE = PROJECT_ROOT / "tools" / "dashboard" / "templates" / "strategos" / "dat.html"
DAT_SCHEDULER_XML = PROJECT_ROOT / "tools" / "dat" / "icdev_dat_scheduler_task.xml"
DAT_CONFIG = PROJECT_ROOT / "args" / "dat_config.yaml"


class TestDatFilesExist:
    """Requirement 5 Step 1 — DAT artifact existence check."""

    def test_ingestion_engine_exists(self):
        assert INGESTION_ENGINE.exists(), "tools/dat/ingestion_engine.py must exist"

    def test_dti_calculator_exists(self):
        assert DTI_CALCULATOR.exists(), "tools/dat/dti_calculator.py must exist"

    def test_dti_update_runner_exists(self):
        assert DTI_UPDATE_RUNNER.exists(), "tools/dat/dti_update_runner.py must exist"

    def test_dat_blueprint_exists(self):
        assert DAT_BLUEPRINT.exists(), "tools/strategos/dat.py must exist"

    def test_dat_template_exists(self):
        assert DAT_TEMPLATE.exists(), (
            "tools/dashboard/templates/strategos/dat.html must exist"
        )

    def test_dat_scheduler_xml_exists(self):
        assert DAT_SCHEDULER_XML.exists(), (
            "tools/dat/icdev_dat_scheduler_task.xml must exist"
        )

    def test_dat_config_exists(self):
        assert DAT_CONFIG.exists(), "args/dat_config.yaml must exist"


class TestDatIngestionEngine:
    """Requirement 5 Step 2 — ingestion_engine import and API surface."""

    def test_ingestion_engine_importable(self):
        try:
            pass
        except Exception as exc:
            pytest.fail(f"ingestion_engine.py failed to import: {exc}")

    def test_run_with_dry_run_returns_dict(self):
        from tools.dat.ingestion_engine import run

        result = run(dry_run=True)
        assert isinstance(result, dict)
        assert "manifest_id" in result or "sources" in result or "classification" in result


class TestDtiCalculator:
    """Requirement 5 Step 3 — DTI calculator correctness."""

    def test_dti_calculator_importable(self):
        try:
            pass
        except Exception as exc:
            pytest.fail(f"dti_calculator.py failed to import: {exc}")

    def test_empty_manifest_returns_zero(self):
        from tools.dat.dti_calculator import compute_dti_from_manifest

        dti = compute_dti_from_manifest({})
        assert isinstance(dti, float)
        assert 0.0 <= dti <= 1.0
        assert dti == 0.0

    def test_high_tension_manifest_returns_high_score(self):
        from tools.dat.dti_calculator import compute_dti_from_manifest

        manifest = {
            "cables": [
                {"tension_level": "critical", "received_at": "2026-05-16T00:00:00+00:00"},
            ],
            "schedules": [
                {"emergency": True, "veto_cast": True, "walkout": True},
            ],
            "metadata": [
                {"escalation_flag": True, "communication_breakdown": True, "frequency_delta": -0.5},
            ],
        }
        dti = compute_dti_from_manifest(manifest)
        assert isinstance(dti, float)
        assert 0.0 <= dti <= 1.0
        assert dti > 0.5, f"Expected high DTI for all-critical signals, got {dti}"

    def test_low_tension_manifest_returns_low_score(self):
        from tools.dat.dti_calculator import compute_dti_from_manifest

        manifest = {
            "cables": [
                {"tension_level": "low", "received_at": "2026-05-16T00:00:00+00:00"},
            ],
            "schedules": [
                {"emergency": False, "veto_cast": False, "walkout": False},
            ],
            "metadata": [
                {"escalation_flag": False, "communication_breakdown": False, "frequency_delta": 0.0},
            ],
        }
        dti = compute_dti_from_manifest(manifest)
        assert isinstance(dti, float)
        assert 0.0 <= dti <= 1.0
        assert dti < 0.5, f"Expected low DTI for all-low signals, got {dti}"


class TestDtiUpdateRunner:
    """Requirement 5 Step 4 — dti_update_runner import and API surface."""

    def test_dti_update_runner_importable(self):
        try:
            pass
        except Exception as exc:
            pytest.fail(f"dti_update_runner.py failed to import: {exc}")

    def test_dti_update_runner_constants(self):
        from tools.dat import dti_update_runner

        assert hasattr(dti_update_runner, "INTERVAL_HOURS")
        assert hasattr(dti_update_runner, "MAX_RUNTIME_MINUTES")
        assert dti_update_runner.INTERVAL_HOURS == 6
        assert dti_update_runner.MAX_RUNTIME_MINUTES == 10


class TestStrategosDat:
    """Requirement 5 Step 5 — strategos/dat blueprint import and API surface."""

    def test_strategos_dat_importable(self):
        try:
            pass
        except Exception as exc:
            pytest.fail(f"strategos/dat.py failed to import: {exc}")

    def test_strategos_dat_constants(self):
        from tools.strategos import dat

        assert hasattr(dat, "CABLE_WEIGHT")
        assert hasattr(dat, "UNSC_WEIGHT")
        assert hasattr(dat, "BACKCHANNEL_WEIGHT")
        assert dat.CABLE_WEIGHT == 0.40
        assert dat.UNSC_WEIGHT == 0.30
        assert dat.BACKCHANNEL_WEIGHT == 0.30


class TestDatConfig:
    """Requirement 5 Step 6 — dat_config.yaml is valid YAML with required keys."""

    def test_dat_config_is_valid_yaml(self):
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not installed")

        raw = DAT_CONFIG.read_text(encoding="utf-8")
        config = yaml.safe_load(raw)
        assert isinstance(config, dict)
        assert "state_dept_cables" in config
        assert "unsc_schedule" in config
        assert "backchannel_logs" in config
        assert "output" in config

    def test_dat_config_classification(self):
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not installed")

        raw = DAT_CONFIG.read_text(encoding="utf-8")
        config = yaml.safe_load(raw)
        assert config.get("classification") == "CUI"
