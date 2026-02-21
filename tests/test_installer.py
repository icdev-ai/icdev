# CUI // SP-CTI
"""Tests for ICDEV Installer — module registry, compliance configurator, installer.

Validates ModuleRegistry dependency tracking, ComplianceConfigurator framework
mapping, ModularDBInitializer selective table creation, and the main installer
dry-run / profile / add-module / add-compliance flows.

Run: pytest tests/test_installer.py -v --tb=short
"""

import json
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

try:
    from tools.installer.module_registry import ModuleRegistry, DEFAULT_MODULES
    from tools.installer.compliance_configurator import (
        ComplianceConfigurator,
        FRAMEWORK_MODULE_MAP,
        DATA_CATEGORY_FRAMEWORK_MAP,
        POSTURE_DESCRIPTIONS,
    )
    from tools.installer.installer import (
        ModularDBInitializer,
        install,
        add_module,
        add_compliance,
        show_status,
        _topological_sort,
    )
except ImportError:
    pytestmark = pytest.mark.skip("tools.installer modules not available")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def registry_path(tmp_path):
    """Return a path for a fresh registry JSON file (does not exist yet)."""
    return tmp_path / "installation.json"


@pytest.fixture
def manifest_path(tmp_path):
    """Return a path to a manifest YAML that does not exist (uses defaults)."""
    return tmp_path / "manifest.yaml"


@pytest.fixture
def registry(manifest_path, registry_path):
    """Create a fresh ModuleRegistry backed by tmp_path files."""
    return ModuleRegistry(
        manifest_path=manifest_path,
        registry_path=registry_path,
    )


@pytest.fixture
def configurator(manifest_path, registry_path):
    """Create a fresh ComplianceConfigurator backed by tmp_path files."""
    return ComplianceConfigurator(
        manifest_path=manifest_path,
        registry_path=registry_path,
    )


# ============================================================================
# TestModuleRegistry
# ============================================================================

class TestModuleRegistry:
    """Tests for tools.installer.module_registry.ModuleRegistry."""

    def test_get_installed_empty_on_fresh_registry(self, registry):
        """Fresh registry has no installed modules."""
        installed = registry.get_installed()
        assert installed == {}

    def test_install_module_no_deps(self, registry):
        """Installing 'core' (no dependencies) succeeds and records timestamp."""
        result = registry.install_module("core")
        assert result["success"] is True
        assert result["module_id"] == "core"
        assert "installed_at" in result

    def test_install_module_records_in_registry(self, registry):
        """After install, module appears in get_installed."""
        registry.install_module("core")
        installed = registry.get_installed()
        assert "core" in installed
        assert installed["core"]["installed"] is True

    def test_install_module_fails_on_unmet_deps(self, registry):
        """Installing 'builder' without 'core' and 'llm' fails."""
        result = registry.install_module("builder")
        assert result["success"] is False
        assert "missing_dependencies" in result
        assert "core" in result["missing_dependencies"]

    def test_install_module_succeeds_after_deps_installed(self, registry):
        """Installing 'llm' succeeds once 'core' is installed."""
        registry.install_module("core")
        result = registry.install_module("llm")
        assert result["success"] is True

    def test_install_unknown_module_fails(self, registry):
        """Installing a nonexistent module returns error."""
        result = registry.install_module("nonexistent_module_xyz")
        assert result["success"] is False
        assert "Unknown module" in result["error"]

    def test_get_available_returns_no_dep_modules_first(self, registry):
        """On fresh registry, only 'core' (no deps) should be available."""
        available = registry.get_available()
        available_ids = [m["module_id"] for m in available]
        assert "core" in available_ids
        # Modules with unsatisfied deps should NOT be available
        assert "builder" not in available_ids

    def test_get_available_expands_after_install(self, registry):
        """After installing 'core', modules depending only on core appear."""
        registry.install_module("core")
        available = registry.get_available()
        available_ids = [m["module_id"] for m in available]
        assert "llm" in available_ids
        assert "security" in available_ids

    def test_check_dependencies_satisfied(self, registry):
        """check_dependencies returns satisfied=True when all deps met."""
        registry.install_module("core")
        result = registry.check_dependencies("llm")
        assert result["satisfied"] is True
        assert result["missing"] == []

    def test_check_dependencies_missing(self, registry):
        """check_dependencies returns missing list when deps unmet."""
        result = registry.check_dependencies("builder")
        assert result["satisfied"] is False
        assert "core" in result["missing"]

    def test_validate_detects_missing_deps(self, registry_path, manifest_path):
        """validate detects if an installed module's dependency is missing."""
        # Manually create a broken registry with builder but not core/llm
        registry_data = {
            "installed_at": "2026-01-01T00:00:00+00:00",
            "last_updated": "2026-01-01T00:00:00+00:00",
            "profile": "default",
            "platform": "linux",
            "os": "linux",
            "modules": {
                "builder": {"installed": True, "version": "1.0.0", "installed_at": "2026-01-01"},
            },
            "compliance_posture": [],
            "cui_enabled": False,
        }
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(json.dumps(registry_data))

        reg = ModuleRegistry(manifest_path=manifest_path, registry_path=registry_path)
        result = reg.validate()
        assert result["valid"] is False
        assert len(result["issues"]) > 0
        # Should mention core or llm dependency issue
        issue_text = " ".join(result["issues"])
        assert "core" in issue_text or "llm" in issue_text

    def test_export_config_returns_full_state(self, registry):
        """export_config returns dict with expected top-level keys."""
        config = registry.export_config()
        assert "installed_at" in config
        assert "modules" in config
        assert "compliance_posture" in config
        assert "cui_enabled" in config


# ============================================================================
# TestComplianceConfigurator
# ============================================================================

class TestComplianceConfigurator:
    """Tests for tools.installer.compliance_configurator.ComplianceConfigurator."""

    def test_configure_fedramp_high_includes_compliance_base(self, configurator):
        """fedramp_high posture requires compliance_base module."""
        result = configurator.configure_posture(["fedramp_high"])
        assert "compliance_base" in result["required_modules"]
        assert "fedramp_high" in result["required_modules"]

    def test_configure_cui_framework_sets_cui_enabled(self, configurator):
        """CUI-related frameworks (fedramp, cmmc, etc.) set cui_enabled=True."""
        result = configurator.configure_posture(["fedramp_moderate"])
        assert result["cui_enabled"] is True

    def test_configure_hipaa_sets_cui_disabled(self, configurator):
        """HIPAA alone does not set cui_enabled (not in _CUI_FRAMEWORKS)."""
        result = configurator.configure_posture(["hipaa"])
        assert result["cui_enabled"] is False

    def test_detect_cui_data_category(self, configurator):
        """CUI data category detects fedramp, cmmc, fips frameworks."""
        detected = configurator.detect_from_data_categories(["CUI"])
        assert "fedramp_moderate" in detected
        assert "cmmc" in detected
        assert "fips_199_200" in detected

    def test_detect_phi_data_category(self, configurator):
        """PHI data category detects hipaa and hitrust frameworks."""
        detected = configurator.detect_from_data_categories(["PHI"])
        assert "hipaa" in detected
        assert "hitrust" in detected

    def test_validate_posture_detects_gaps(self, configurator):
        """validate_posture reports missing modules when posture declared but not installed."""
        result = configurator.validate_posture(
            installed_modules={},
            declared_posture=["fedramp_high"],
        )
        assert result["satisfied"] is False
        assert "compliance_base" in result["gaps"]
        assert "fedramp_high" in result["gaps"]

    def test_validate_posture_satisfied_when_all_installed(self, configurator):
        """validate_posture is satisfied when all required modules are installed."""
        result = configurator.validate_posture(
            installed_modules={
                "compliance_base": {"installed": True},
                "hipaa": {"installed": True},
            },
            declared_posture=["hipaa"],
        )
        assert result["satisfied"] is True
        assert result["gaps"] == []

    def test_list_postures_returns_all_frameworks(self, configurator):
        """list_postures returns at least 15 framework postures."""
        postures = configurator.list_postures()
        assert len(postures) >= 15
        posture_ids = [p["id"] for p in postures]
        assert "fedramp_moderate" in posture_ids
        assert "hipaa" in posture_ids
        assert "cmmc" in posture_ids


# ============================================================================
# TestModularDBInitializer
# ============================================================================

class TestModularDBInitializer:
    """Tests for tools.installer.installer.ModularDBInitializer."""

    def test_init_creates_tables_for_requested_modules(self, tmp_path):
        """initialize creates DB file and returns table info."""
        db_path = tmp_path / "test.db"
        initializer = ModularDBInitializer(
            manifest_path=tmp_path / "nonexistent.yaml",
            db_path=db_path,
        )
        result = initializer.initialize(["core"], dry_run=False)
        assert "table_count" in result
        assert result["db_path"] == str(db_path)

    def test_init_preserves_existing_tables(self, tmp_path):
        """Running initialize twice does not drop existing tables."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE IF NOT EXISTS custom_table (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO custom_table VALUES ('test-1')")
        conn.commit()
        conn.close()

        initializer = ModularDBInitializer(
            manifest_path=tmp_path / "nonexistent.yaml",
            db_path=db_path,
        )
        initializer.initialize(["core"], dry_run=False)

        # Verify custom_table still exists with its data
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT id FROM custom_table").fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "test-1"

    def test_handles_unknown_table_group(self, tmp_path):
        """Unknown module IDs are handled gracefully (no crash)."""
        db_path = tmp_path / "test.db"
        initializer = ModularDBInitializer(
            manifest_path=tmp_path / "nonexistent.yaml",
            db_path=db_path,
        )
        result = initializer.initialize(["nonexistent_module_xyz"], dry_run=False)
        assert result["table_count"] == 0

    def test_dry_run_returns_plan(self, tmp_path):
        """dry_run=True returns plan without creating DB file."""
        db_path = tmp_path / "dryrun.db"
        initializer = ModularDBInitializer(
            manifest_path=tmp_path / "nonexistent.yaml",
            db_path=db_path,
        )
        result = initializer.initialize(["core"], dry_run=True)
        assert result["dry_run"] is True
        assert "db_path" in result

    def test_init_empty_module_list(self, tmp_path):
        """Empty module list produces zero tables."""
        db_path = tmp_path / "test.db"
        initializer = ModularDBInitializer(
            manifest_path=tmp_path / "nonexistent.yaml",
            db_path=db_path,
        )
        result = initializer.initialize([], dry_run=False)
        assert result["table_count"] == 0

    def test_init_returns_db_path_in_result(self, tmp_path):
        """initialize returns the db_path in the result dict."""
        db_path = tmp_path / "result_check.db"
        initializer = ModularDBInitializer(
            manifest_path=tmp_path / "nonexistent.yaml",
            db_path=db_path,
        )
        result = initializer.initialize([], dry_run=False)
        assert result["db_path"] == str(db_path)


# ============================================================================
# TestInstaller
# ============================================================================

class TestInstaller:
    """Tests for tools.installer.installer top-level functions."""

    def test_dry_run_returns_plan_without_executing(self, tmp_path, monkeypatch):
        """install() with dry_run=True returns plan and does not create files."""
        monkeypatch.setattr(
            "tools.installer.installer.DATA_DIR", tmp_path / "data"
        )
        monkeypatch.setattr(
            "tools.installer.installer.DB_PATH", tmp_path / "data" / "icdev.db"
        )
        monkeypatch.setattr(
            "tools.installer.installer.REGISTRY_PATH", tmp_path / "data" / "installation.json"
        )
        result = install(
            modules=["core"],
            compliance_frameworks=[],
            profile_name="test",
            platform_target="docker",
            dry_run=True,
        )
        assert result["dry_run"] is True
        assert result["success"] is True
        assert "core" in result["modules"]

    def test_install_records_profile_name(self, tmp_path, monkeypatch):
        """install() records the profile name in the result."""
        import yaml

        # Create a minimal manifest with 'core' module
        manifest_path = tmp_path / "manifest.yaml"
        manifest_data = {
            "modules": {
                "core": {
                    "name": "Core",
                    "version": "1.0.0",
                    "dependencies": [],
                    "category": "foundation",
                },
            },
        }
        manifest_path.write_text(yaml.dump(manifest_data, default_flow_style=False))

        monkeypatch.setattr("tools.installer.installer.DATA_DIR", tmp_path / "data")
        monkeypatch.setattr("tools.installer.installer.DB_PATH", tmp_path / "data" / "icdev.db")
        monkeypatch.setattr(
            "tools.installer.installer.REGISTRY_PATH", tmp_path / "data" / "installation.json"
        )
        monkeypatch.setattr("tools.installer.installer.MANIFEST_PATH", manifest_path)
        result = install(
            modules=["core"],
            compliance_frameworks=[],
            profile_name="my_profile",
            platform_target="k8s",
            dry_run=True,
        )
        assert result["success"] is True
        assert result["profile"] == "my_profile"

    def test_add_module_resolves_dependencies(self, tmp_path, monkeypatch):
        """add_module with dry_run includes transitive deps."""
        import yaml

        # Create a manifest with core and llm (llm depends on core)
        manifest_path = tmp_path / "manifest.yaml"
        manifest_data = {
            "modules": {
                "core": {
                    "name": "Core",
                    "version": "1.0.0",
                    "dependencies": [],
                    "category": "foundation",
                },
                "llm": {
                    "name": "LLM Router",
                    "version": "1.0.0",
                    "dependencies": ["core"],
                    "category": "foundation",
                },
            },
        }
        manifest_path.write_text(yaml.dump(manifest_data, default_flow_style=False))

        monkeypatch.setattr("tools.installer.installer.DATA_DIR", tmp_path / "data")
        monkeypatch.setattr("tools.installer.installer.DB_PATH", tmp_path / "data" / "icdev.db")
        monkeypatch.setattr(
            "tools.installer.installer.REGISTRY_PATH", tmp_path / "data" / "installation.json"
        )
        monkeypatch.setattr("tools.installer.installer.MANIFEST_PATH", manifest_path)
        result = add_module("llm", dry_run=True)
        assert result["success"] is True
        assert result["dry_run"] is True
        # llm depends on core, so both should be planned
        assert "core" in result["modules_to_install"]
        assert "llm" in result["modules_to_install"]

    def test_add_module_unknown_fails(self, tmp_path, monkeypatch):
        """add_module for unknown module returns error."""
        monkeypatch.setattr("tools.installer.installer.DATA_DIR", tmp_path / "data")
        monkeypatch.setattr("tools.installer.installer.DB_PATH", tmp_path / "data" / "icdev.db")
        monkeypatch.setattr(
            "tools.installer.installer.REGISTRY_PATH", tmp_path / "data" / "installation.json"
        )
        monkeypatch.setattr(
            "tools.installer.installer.MANIFEST_PATH", tmp_path / "nonexistent.yaml"
        )
        result = add_module("totally_fake_module_xyz", dry_run=True)
        assert result["success"] is False
        assert "Unknown module" in result["error"]

    def test_add_compliance_updates_posture(self, tmp_path, monkeypatch):
        """add_compliance with dry_run returns expected framework info."""
        monkeypatch.setattr("tools.installer.installer.DATA_DIR", tmp_path / "data")
        monkeypatch.setattr("tools.installer.installer.DB_PATH", tmp_path / "data" / "icdev.db")
        monkeypatch.setattr(
            "tools.installer.installer.REGISTRY_PATH", tmp_path / "data" / "installation.json"
        )
        monkeypatch.setattr(
            "tools.installer.installer.MANIFEST_PATH", tmp_path / "nonexistent.yaml"
        )
        result = add_compliance("hipaa", dry_run=True)
        assert result["success"] is True
        assert result["framework_id"] == "hipaa"
        assert "compliance_base" in result["modules_to_install"]

    def test_add_compliance_unknown_framework_fails(self, tmp_path, monkeypatch):
        """add_compliance for unknown framework returns error."""
        monkeypatch.setattr("tools.installer.installer.DATA_DIR", tmp_path / "data")
        monkeypatch.setattr("tools.installer.installer.DB_PATH", tmp_path / "data" / "icdev.db")
        monkeypatch.setattr(
            "tools.installer.installer.REGISTRY_PATH", tmp_path / "data" / "installation.json"
        )
        monkeypatch.setattr(
            "tools.installer.installer.MANIFEST_PATH", tmp_path / "nonexistent.yaml"
        )
        result = add_compliance("fake_framework_xyz", dry_run=True)
        assert result["success"] is False
        assert "Unknown framework" in result["error"]


# CUI // SP-CTI
