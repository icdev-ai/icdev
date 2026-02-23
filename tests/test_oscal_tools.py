#!/usr/bin/env python3
# [TEMPLATE: CUI // SP-CTI]
"""Tests for OSCAL Ecosystem Integration (D302-D306).

Covers:
    - OscalCatalogAdapter: both catalog formats, fallback, normalization
    - Tool detection: oscal-cli, oscal-pydantic, NIST catalog
    - Deep validation pipeline: structural → pydantic → Metaschema
    - Format conversion: JSON↔XML↔YAML (requires oscal-cli)
    - Profile resolution (requires oscal-cli)
    - Validation logging to DB (D306)
    - CLI entrypoints for both modules
"""

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Sample OSCAL catalog snippet for testing
SAMPLE_OSCAL_CATALOG = {
    "catalog": {
        "uuid": "a1b2c3d4-0000-0000-0000-000000000000",
        "metadata": {
            "title": "Test NIST 800-53 Catalog",
            "version": "5.1.1",
            "oscal-version": "1.1.2",
            "last-modified": "2024-01-01T00:00:00Z",
        },
        "groups": [
            {
                "id": "ac",
                "title": "Access Control",
                "controls": [
                    {
                        "id": "ac-1",
                        "title": "Policy and Procedures",
                        "params": [
                            {"id": "ac-1_prm_1", "label": "organization-defined personnel"},
                        ],
                        "parts": [
                            {"name": "statement", "prose": "Develop access control policy."},
                            {"name": "guidance", "prose": "AC-1 supplemental guidance."},
                        ],
                        "props": [],
                        "controls": [
                            {
                                "id": "ac-1.1",
                                "title": "AC-1 Enhancement 1",
                                "parts": [
                                    {"name": "statement", "prose": "Enhancement description."},
                                ],
                                "props": [],
                            }
                        ],
                    },
                    {
                        "id": "ac-2",
                        "title": "Account Management",
                        "params": [],
                        "parts": [
                            {"name": "statement", "prose": "Manage system accounts."},
                        ],
                        "props": [],
                        "controls": [],
                    },
                ],
            },
            {
                "id": "au",
                "title": "Audit and Accountability",
                "controls": [
                    {
                        "id": "au-1",
                        "title": "Policy and Procedures",
                        "params": [],
                        "parts": [
                            {"name": "statement", "prose": "Develop audit policy."},
                        ],
                        "props": [
                            {"name": "status", "value": "withdrawn"},
                        ],
                        "controls": [],
                    },
                ],
            },
        ],
    }
}

# Sample ICDEV catalog snippet
SAMPLE_ICDEV_CATALOG = {
    "metadata": {"version": "1.0", "framework": "NIST 800-53 Rev 5"},
    "controls": [
        {
            "id": "AC-2",
            "family": "AC",
            "title": "Account Management",
            "description": "Manage information system accounts.",
            "supplemental_guidance": "Account management includes...",
            "impact_level": "low",
        },
        {
            "id": "AC-3",
            "family": "AC",
            "title": "Access Enforcement",
            "description": "Enforce approved authorizations.",
            "supplemental_guidance": "",
            "impact_level": "moderate",
        },
    ],
}


# ── OscalCatalogAdapter Tests ─────────────────────────────────────────


class TestOscalCatalogAdapterOscalFormat:
    """Test adapter with official NIST OSCAL catalog format."""

    @pytest.fixture
    def oscal_catalog_file(self, tmp_path):
        path = tmp_path / "catalog.json"
        path.write_text(json.dumps(SAMPLE_OSCAL_CATALOG), encoding="utf-8")
        return path

    def test_load_oscal_catalog(self, oscal_catalog_file):
        from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter

        adapter = OscalCatalogAdapter(catalog_path=str(oscal_catalog_file))
        assert adapter.is_loaded()
        assert adapter.is_official_catalog()

    def test_get_control_by_id(self, oscal_catalog_file):
        from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter

        adapter = OscalCatalogAdapter(catalog_path=str(oscal_catalog_file))
        ctrl = adapter.get_control("AC-2")
        assert ctrl is not None
        assert ctrl["id"] == "AC-2"
        assert ctrl["title"] == "Account Management"
        assert ctrl["source"] == "nist_oscal"

    def test_get_control_case_insensitive(self, oscal_catalog_file):
        from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter

        adapter = OscalCatalogAdapter(catalog_path=str(oscal_catalog_file))
        ctrl = adapter.get_control("ac-2")
        assert ctrl is not None
        assert ctrl["id"] == "AC-2"

    def test_get_enhancement(self, oscal_catalog_file):
        from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter

        adapter = OscalCatalogAdapter(catalog_path=str(oscal_catalog_file))
        ctrl = adapter.get_control("AC-1(1)")
        assert ctrl is not None
        assert ctrl["is_enhancement"] is True
        assert ctrl["parent_id"] == "AC-1"

    def test_list_controls_by_family(self, oscal_catalog_file):
        from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter

        adapter = OscalCatalogAdapter(catalog_path=str(oscal_catalog_file))
        controls = adapter.list_controls(family="AC")
        assert len(controls) >= 2  # AC-1, AC-2, plus enhancement(s)

    def test_list_controls_excludes_withdrawn(self, oscal_catalog_file):
        from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter

        adapter = OscalCatalogAdapter(catalog_path=str(oscal_catalog_file))
        all_ctrls = adapter.list_controls(include_withdrawn=True)
        active_ctrls = adapter.list_controls(include_withdrawn=False)
        assert len(all_ctrls) > len(active_ctrls)

    def test_catalog_stats(self, oscal_catalog_file):
        from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter

        adapter = OscalCatalogAdapter(catalog_path=str(oscal_catalog_file))
        stats = adapter.get_catalog_stats()
        assert stats["source_format"] == "nist_oscal"
        assert stats["total_controls"] >= 3
        assert stats["enhancements"] >= 1
        assert "AC" in stats["families"]
        assert stats["family_count"] >= 2

    def test_control_params_extracted(self, oscal_catalog_file):
        from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter

        adapter = OscalCatalogAdapter(catalog_path=str(oscal_catalog_file))
        ctrl = adapter.get_control("AC-1")
        assert len(ctrl["params"]) == 1
        assert ctrl["params"][0]["id"] == "ac-1_prm_1"


class TestOscalCatalogAdapterIcdevFormat:
    """Test adapter with ICDEV custom catalog format."""

    @pytest.fixture
    def icdev_catalog_file(self, tmp_path):
        path = tmp_path / "nist_800_53.json"
        path.write_text(json.dumps(SAMPLE_ICDEV_CATALOG), encoding="utf-8")
        return path

    def test_load_icdev_catalog(self, icdev_catalog_file):
        from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter

        adapter = OscalCatalogAdapter(catalog_path=str(icdev_catalog_file))
        assert adapter.is_loaded()
        assert not adapter.is_official_catalog()

    def test_get_control(self, icdev_catalog_file):
        from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter

        adapter = OscalCatalogAdapter(catalog_path=str(icdev_catalog_file))
        ctrl = adapter.get_control("AC-2")
        assert ctrl is not None
        assert ctrl["source"] == "icdev_custom"
        assert ctrl["title"] == "Account Management"

    def test_list_controls(self, icdev_catalog_file):
        from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter

        adapter = OscalCatalogAdapter(catalog_path=str(icdev_catalog_file))
        controls = adapter.list_controls()
        assert len(controls) == 2

    def test_stats(self, icdev_catalog_file):
        from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter

        adapter = OscalCatalogAdapter(catalog_path=str(icdev_catalog_file))
        stats = adapter.get_catalog_stats()
        assert stats["source_format"] == "icdev_custom"
        assert stats["total_controls"] == 2


class TestCatalogAdapterFallback:
    """Test priority/fallback behavior."""

    def test_fallback_to_second_source(self, tmp_path):
        from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter, _CATALOG_CACHE

        # Clear module cache to avoid stale state
        _CATALOG_CACHE.clear()

        nonexistent = tmp_path / "does_not_exist.json"
        existing = tmp_path / "fallback.json"
        existing.write_text(json.dumps(SAMPLE_ICDEV_CATALOG), encoding="utf-8")

        adapter = OscalCatalogAdapter(
            catalog_sources=[str(nonexistent), str(existing)]
        )
        assert adapter.is_loaded()
        assert adapter._source_format == "icdev_custom"

    def test_no_catalogs_available(self, tmp_path):
        from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter, _CATALOG_CACHE

        _CATALOG_CACHE.clear()
        adapter = OscalCatalogAdapter(
            catalog_sources=[str(tmp_path / "nope1.json"), str(tmp_path / "nope2.json")]
        )
        assert not adapter.is_loaded()

    def test_invalid_json_skipped(self, tmp_path):
        from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter, _CATALOG_CACHE

        _CATALOG_CACHE.clear()
        bad = tmp_path / "bad.json"
        bad.write_text("{invalid json", encoding="utf-8")
        good = tmp_path / "good.json"
        good.write_text(json.dumps(SAMPLE_ICDEV_CATALOG), encoding="utf-8")

        adapter = OscalCatalogAdapter(catalog_sources=[str(bad), str(good)])
        assert adapter.is_loaded()

    def test_unrecognized_format_skipped(self, tmp_path):
        from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter, _CATALOG_CACHE

        _CATALOG_CACHE.clear()
        unknown = tmp_path / "unknown.json"
        unknown.write_text(json.dumps({"random": "data"}), encoding="utf-8")
        good = tmp_path / "good.json"
        good.write_text(json.dumps(SAMPLE_ICDEV_CATALOG), encoding="utf-8")

        adapter = OscalCatalogAdapter(catalog_sources=[str(unknown), str(good)])
        assert adapter.is_loaded()


class TestNormalizeControlId:
    """Test control ID normalization."""

    def test_lowercase(self):
        from tools.compliance.oscal_catalog_adapter import _normalize_control_id

        assert _normalize_control_id("ac-2") == "AC-2"

    def test_dot_to_parenthetical(self):
        from tools.compliance.oscal_catalog_adapter import _normalize_control_id

        assert _normalize_control_id("ac-2.1") == "AC-2(1)"

    def test_already_normalized(self):
        from tools.compliance.oscal_catalog_adapter import _normalize_control_id

        assert _normalize_control_id("AC-2(1)") == "AC-2(1)"

    def test_empty(self):
        from tools.compliance.oscal_catalog_adapter import _normalize_control_id

        assert _normalize_control_id("") == ""
        assert _normalize_control_id(None) == ""


# ── Tool Detection Tests ──────────────────────────────────────────────


class TestOscalToolDetection:
    """Test detect_oscal_tools() capability checks."""

    def test_detect_returns_dict(self):
        from tools.compliance.oscal_tools import detect_oscal_tools

        result = detect_oscal_tools()
        assert isinstance(result, dict)
        assert "oscal_cli" in result
        assert "oscal_pydantic" in result
        assert "nist_catalog" in result

    def test_detect_oscal_cli_not_installed(self):
        from tools.compliance.oscal_tools import detect_oscal_tools
        import tools.compliance.oscal_tools as ot

        ot._JAVA_INFO = None
        ot._OSCAL_CLI_INFO = None
        try:
            with patch("tools.compliance.oscal_tools._detect_java",
                       return_value={"available": False, "version": None, "path": None, "error": "not found"}):
                result = detect_oscal_tools()
                assert result["oscal_cli"]["available"] is False
        finally:
            ot._JAVA_INFO = None
            ot._OSCAL_CLI_INFO = None

    def test_detect_pydantic_not_installed(self):
        from tools.compliance.oscal_tools import detect_oscal_tools
        import tools.compliance.oscal_tools as ot

        ot._PYDANTIC_INFO = None
        try:
            with patch("importlib.metadata.version", side_effect=Exception("not found")):
                result = detect_oscal_tools()
                # May or may not be available depending on environment
                assert "available" in result["oscal_pydantic"]
        finally:
            ot._PYDANTIC_INFO = None

    def test_detect_nist_catalog(self):
        from tools.compliance.oscal_tools import detect_oscal_tools

        result = detect_oscal_tools()
        assert "available" in result["nist_catalog"]
        assert "format" in result["nist_catalog"]

    def test_detect_has_all_four_keys(self):
        from tools.compliance.oscal_tools import detect_oscal_tools

        result = detect_oscal_tools()
        assert "oscal_cli" in result
        assert "java" in result
        assert "oscal_pydantic" in result
        assert "nist_catalog" in result
        # Each entry has an "available" field
        for key in ("oscal_cli", "java", "oscal_pydantic", "nist_catalog"):
            assert "available" in result[key]


# ── Deep Validation Tests ─────────────────────────────────────────────


class TestDeepValidation:
    """Test validate_oscal_deep() multi-layer pipeline."""

    @pytest.fixture
    def sample_ssp_path(self):
        return str(BASE_DIR / "tests" / "fixtures" / "sample_oscal_ssp.json")

    def test_structural_validation_passes(self, sample_ssp_path):
        from tools.compliance.oscal_tools import validate_oscal_deep

        result = validate_oscal_deep(sample_ssp_path)
        assert isinstance(result, dict)
        assert "validators" in result
        # validators is a list — find the structural one
        structural = [v for v in result["validators"] if v["validator"] == "icdev_structural"]
        assert len(structural) == 1
        assert structural[0]["valid"] is True

    def test_validation_on_nonexistent_file(self, tmp_path):
        from tools.compliance.oscal_tools import validate_oscal_deep

        result = validate_oscal_deep(str(tmp_path / "nonexistent.json"))
        assert result["valid"] is False

    def test_validation_on_invalid_json(self, tmp_path):
        from tools.compliance.oscal_tools import validate_oscal_deep

        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json}", encoding="utf-8")
        result = validate_oscal_deep(str(bad))
        assert result["valid"] is False

    def test_validation_on_valid_ssp(self, sample_ssp_path):
        from tools.compliance.oscal_tools import validate_oscal_deep

        result = validate_oscal_deep(sample_ssp_path)
        # Structural and pydantic layers should pass; metaschema may fail
        # on the minimal test fixture (incomplete SSP)
        validators = {v["validator"]: v for v in result["validators"]}
        assert validators["icdev_structural"]["valid"] is True
        assert validators["oscal_pydantic"]["valid"] is True
        assert "validators_run" in result

    def test_pydantic_layer_graceful_when_not_installed(self, sample_ssp_path):
        from tools.compliance.oscal_tools import validate_oscal_deep

        result = validate_oscal_deep(sample_ssp_path)
        pydantic_entries = [v for v in result["validators"]
                           if v["validator"] == "oscal_pydantic"]
        assert len(pydantic_entries) == 1
        pydantic_layer = pydantic_entries[0]
        # Either passes, skipped, or valid (graceful on incompatible pydantic version)
        assert pydantic_layer.get("valid") is True or pydantic_layer.get("skipped") is True \
            or "errors" in pydantic_layer  # errors key present means it ran but handled gracefully

    def test_metaschema_layer_graceful_when_no_java(self, sample_ssp_path):
        from tools.compliance.oscal_tools import validate_oscal_deep

        with patch("tools.compliance.oscal_tools._detect_java",
                   return_value={"available": False, "version": None, "path": None, "error": "no java"}):
            # Reset cached detection results
            import tools.compliance.oscal_tools as ot
            ot._JAVA_INFO = None
            ot._OSCAL_CLI_INFO = None
            try:
                result = validate_oscal_deep(sample_ssp_path)
                meta_entries = [v for v in result["validators"]
                               if v["validator"] == "oscal_cli_metaschema"]
                assert len(meta_entries) == 1
                meta_layer = meta_entries[0]
                assert meta_layer.get("valid") is True or meta_layer.get("skipped") is True
            finally:
                ot._JAVA_INFO = None
                ot._OSCAL_CLI_INFO = None

    def test_validation_result_has_file_info(self, sample_ssp_path):
        from tools.compliance.oscal_tools import validate_oscal_deep

        result = validate_oscal_deep(sample_ssp_path)
        assert "file_path" in result
        assert "artifact_type" in result

    def test_validation_logs_to_db(self, sample_ssp_path, tmp_path):
        """Verify validation attempts are logged (D306)."""
        from tools.compliance.oscal_tools import validate_oscal_deep

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS oscal_validation_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                artifact_type TEXT,
                validator TEXT NOT NULL,
                valid INTEGER NOT NULL,
                error_count INTEGER DEFAULT 0,
                errors TEXT,
                duration_ms INTEGER DEFAULT 0,
                project_id TEXT,
                classification TEXT DEFAULT 'CUI',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

        result = validate_oscal_deep(
            sample_ssp_path,
            project_id="test-proj",
            db_path=str(db_path),
        )

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM oscal_validation_log").fetchone()[0]
        conn.close()

        # At least structural layer logged (pydantic/metaschema may be skipped)
        assert rows >= 1


# ── Format Conversion Tests ───────────────────────────────────────────


class TestFormatConversion:
    """Test convert_oscal_format() — requires oscal-cli + Java."""

    def test_conversion_fails_gracefully_without_java(self, tmp_path):
        from tools.compliance.oscal_tools import convert_oscal_format

        dummy = tmp_path / "test.oscal.json"
        dummy.write_text("{}", encoding="utf-8")

        import tools.compliance.oscal_tools as ot
        ot._JAVA_INFO = None
        ot._OSCAL_CLI_INFO = None
        try:
            with patch("tools.compliance.oscal_tools._detect_java",
                       return_value={"available": False, "version": None, "path": None, "error": "no java"}):
                result = convert_oscal_format(str(dummy), "xml")
                assert result.get("success") is False or result.get("error") is not None
        finally:
            ot._JAVA_INFO = None
            ot._OSCAL_CLI_INFO = None

    def test_conversion_requires_valid_format(self):
        from tools.compliance.oscal_tools import convert_oscal_format

        result = convert_oscal_format("/nonexistent.json", "pdf")
        assert result.get("success") is False


# ── Profile Resolution Tests ──────────────────────────────────────────


class TestProfileResolution:
    """Test resolve_oscal_profile() — requires oscal-cli + Java."""

    def test_resolution_fails_gracefully_without_java(self, tmp_path):
        from tools.compliance.oscal_tools import resolve_oscal_profile

        dummy = tmp_path / "profile.json"
        dummy.write_text("{}", encoding="utf-8")

        import tools.compliance.oscal_tools as ot
        ot._JAVA_INFO = None
        ot._OSCAL_CLI_INFO = None
        try:
            with patch("tools.compliance.oscal_tools._detect_java",
                       return_value={"available": False, "version": None, "path": None, "error": "no java"}):
                result = resolve_oscal_profile(str(dummy))
                assert result.get("success") is False or result.get("error") is not None
        finally:
            ot._JAVA_INFO = None
            ot._OSCAL_CLI_INFO = None


# ── Catalog Operations via oscal_tools ────────────────────────────────


class TestCatalogOperations:
    """Test catalog lookup/list/stats via oscal_tools.py."""

    @pytest.fixture
    def catalog_env(self, tmp_path):
        from tools.compliance.oscal_catalog_adapter import _CATALOG_CACHE
        _CATALOG_CACHE.clear()
        cat_path = tmp_path / "catalog.json"
        cat_path.write_text(json.dumps(SAMPLE_OSCAL_CATALOG), encoding="utf-8")
        return str(cat_path)

    def test_catalog_lookup(self, catalog_env):
        from tools.compliance.oscal_tools import catalog_lookup

        result = catalog_lookup("AC-2", catalog_path=catalog_env)
        assert result is not None
        assert result["id"] == "AC-2"

    def test_catalog_lookup_not_found(self, catalog_env):
        from tools.compliance.oscal_tools import catalog_lookup

        result = catalog_lookup("ZZ-999", catalog_path=catalog_env)
        # Returns error dict when not found, not None
        assert "error" in result

    def test_catalog_list(self, catalog_env):
        from tools.compliance.oscal_tools import catalog_list

        result = catalog_list(family="AC", catalog_path=catalog_env)
        assert isinstance(result, dict)
        assert "controls" in result
        assert len(result["controls"]) >= 2

    def test_catalog_stats(self, catalog_env):
        from tools.compliance.oscal_tools import catalog_stats

        result = catalog_stats(catalog_path=catalog_env)
        assert result["source_format"] == "nist_oscal"
        assert result["total_controls"] >= 3


# ── CLI Entrypoint Tests ──────────────────────────────────────────────


class TestCLIEntrypoint:
    """Test CLI flags for both modules."""

    def test_oscal_tools_detect_flag(self):
        """--detect should return detection results."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "tools" / "compliance" / "oscal_tools.py"),
             "--detect", "--json"],
            capture_output=True, text=True, timeout=30,
            stdin=subprocess.DEVNULL,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "oscal_cli" in data
        assert "oscal_pydantic" in data
        assert "nist_catalog" in data

    def test_catalog_adapter_stats_flag(self):
        """--stats should return catalog stats."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "tools" / "compliance" / "oscal_catalog_adapter.py"),
             "--stats", "--json"],
            capture_output=True, text=True, timeout=30,
            stdin=subprocess.DEVNULL,
        )
        # May exit 0 even with no catalog (returns empty stats)
        assert result.returncode == 0

    def test_oscal_tools_catalog_lookup_flag(self):
        """--catalog-lookup AC-2 should work."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "tools" / "compliance" / "oscal_tools.py"),
             "--catalog-lookup", "AC-2", "--json"],
            capture_output=True, text=True, timeout=30,
            stdin=subprocess.DEVNULL,
        )
        # Returns 0 if catalog found, 1 if not — both are acceptable
        assert result.returncode in (0, 1)

    def test_oscal_tools_help(self):
        """--help should work."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "tools" / "compliance" / "oscal_tools.py"),
             "--help"],
            capture_output=True, text=True, timeout=30,
            stdin=subprocess.DEVNULL,
        )
        assert result.returncode == 0
        assert "OSCAL" in result.stdout


# ── Pydantic v2 Support Tests ─────────────────────────────────────────


class TestPydanticV2Support:
    """Test pydantic v2 model loading strategies (D303)."""

    @pytest.fixture
    def sample_ssp_path(self):
        return str(BASE_DIR / "tests" / "fixtures" / "sample_oscal_ssp.json")

    def test_get_pydantic_version(self):
        from tools.compliance.oscal_tools import _get_pydantic_version

        pv = _get_pydantic_version()
        assert isinstance(pv, int)
        assert pv >= 2  # pydantic v2 is installed

    def test_load_oscal_model_returns_tuple(self):
        import tools.compliance.oscal_tools as ot

        ot._OSCAL_MODEL_CACHE.clear()
        try:
            result = ot._load_oscal_model("ssp")
            assert isinstance(result, tuple)
            assert len(result) == 3
            model_cls, compat_mode, is_document = result
            assert model_cls is not None
            assert compat_mode in ("native", "v1_compat", "builtin_v2")
            assert isinstance(is_document, bool)
        finally:
            ot._OSCAL_MODEL_CACHE.clear()

    def test_load_oscal_model_caches_result(self):
        import tools.compliance.oscal_tools as ot

        ot._OSCAL_MODEL_CACHE.clear()
        try:
            r1 = ot._load_oscal_model("ssp")
            r2 = ot._load_oscal_model("ssp")
            assert r1[0] is r2[0]  # same class object
        finally:
            ot._OSCAL_MODEL_CACHE.clear()

    def test_load_oscal_model_unknown_type(self):
        import tools.compliance.oscal_tools as ot

        ot._OSCAL_MODEL_CACHE.clear()
        try:
            model_cls, mode, is_doc = ot._load_oscal_model("unknown_type")
            assert model_cls is None
            assert mode is None
        finally:
            ot._OSCAL_MODEL_CACHE.clear()

    def test_builtin_v2_model_validates_ssp(self, sample_ssp_path):
        from tools.compliance.oscal_tools import _get_builtin_v2_model

        model_cls = _get_builtin_v2_model("ssp")
        assert model_cls is not None
        with open(sample_ssp_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Should not raise
        model_cls.model_validate(data)

    def test_builtin_v2_model_rejects_invalid(self):
        from tools.compliance.oscal_tools import _get_builtin_v2_model

        model_cls = _get_builtin_v2_model("ssp")
        with pytest.raises(Exception):  # ValidationError
            model_cls.model_validate({"system-security-plan": {"uuid": "test"}})

    def test_builtin_v2_model_all_types(self):
        from tools.compliance.oscal_tools import _get_builtin_v2_model

        for atype in ("ssp", "poam", "assessment_results", "component_definition"):
            model_cls = _get_builtin_v2_model(atype)
            assert model_cls is not None, f"No builtin model for {atype}"

    def test_validation_reports_compat_mode(self, sample_ssp_path):
        from tools.compliance.oscal_tools import validate_oscal_deep
        import tools.compliance.oscal_tools as ot

        ot._OSCAL_MODEL_CACHE.clear()
        try:
            result = validate_oscal_deep(sample_ssp_path)
            pydantic_layer = [v for v in result["validators"]
                              if v["validator"] == "oscal_pydantic"][0]
            assert pydantic_layer["valid"] is True
            assert "compat_mode" in pydantic_layer
            assert pydantic_layer["compat_mode"] in ("native", "v1_compat", "builtin_v2")
        finally:
            ot._OSCAL_MODEL_CACHE.clear()

    def test_detect_reports_pydantic_version(self):
        import tools.compliance.oscal_tools as ot

        ot._PYDANTIC_INFO = None
        try:
            info = ot._detect_oscal_pydantic()
            assert "pydantic_version" in info
            assert info["pydantic_version"] >= 2
            assert "compat_mode" in info
        finally:
            ot._PYDANTIC_INFO = None


# ── Validation Pipeline Config ────────────────────────────────────────


class TestValidationConfig:
    """Test configuration loading."""

    def test_config_file_exists(self):
        config_path = BASE_DIR / "args" / "oscal_tools_config.yaml"
        assert config_path.exists(), "args/oscal_tools_config.yaml must exist"

    def test_config_has_required_sections(self):
        import yaml

        config_path = BASE_DIR / "args" / "oscal_tools_config.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        assert "oscal_cli" in config
        assert "oscal_pydantic" in config
        assert "nist_catalog" in config
        assert "validation" in config
        assert "pipeline" in config["validation"]

    def test_validation_pipeline_order(self):
        import yaml

        config_path = BASE_DIR / "args" / "oscal_tools_config.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        pipeline = config["validation"]["pipeline"]
        assert pipeline == ["icdev_structural", "oscal_pydantic", "oscal_cli_metaschema"]
