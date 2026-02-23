# [TEMPLATE: CUI // SP-CTI]
"""Integration tests for the ICDEV Crosswalk Engine (tools/compliance/crosswalk_engine.py).

Validates AC-2 cascade to FedRAMP/CMMC/800-171, coverage computation,
gap analysis, dual-hub bridge (NIST <-> ISO 27001), and summary stats.
"""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from tools.compliance.crosswalk_engine import (
        FRAMEWORK_KEYS,
        IL_KEYS,
        _ensure_crosswalk_tables,
        compute_crosswalk_coverage,
        get_controls_for_framework,
        get_controls_for_impact_level,
        get_crosswalk_summary,
        get_frameworks_for_control,
        get_gap_analysis,
        get_iso_for_nist_control,
        get_nist_for_iso_control,
        load_crosswalk,
        load_iso_bridge,
    )
except ImportError:
    pytestmark = pytest.mark.skip("tools.compliance.crosswalk_engine not available")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CROSSWALK_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'webapp',
    classification TEXT DEFAULT 'CUI',
    status TEXT DEFAULT 'active',
    directory_path TEXT DEFAULT '/tmp',
    impact_level TEXT DEFAULT 'IL5',
    fips199_overall TEXT
);

CREATE TABLE IF NOT EXISTS project_controls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    control_id TEXT NOT NULL,
    implementation_status TEXT DEFAULT 'planned',
    implementation_description TEXT,
    responsible_role TEXT,
    evidence_path TEXT,
    last_assessed TEXT,
    UNIQUE(project_id, control_id)
);

CREATE TABLE IF NOT EXISTS audit_trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT,
    project_id TEXT,
    details TEXT,
    affected_files TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now'))
);
"""


@pytest.fixture
def crosswalk_db(tmp_path):
    """Create a temporary database with crosswalk-related tables and seed data."""
    db_path = tmp_path / "icdev_crosswalk.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(CROSSWALK_DB_SCHEMA)

    # Seed project
    conn.execute(
        "INSERT INTO projects (id, name, type, status, directory_path, impact_level) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("proj-cw-1", "Crosswalk Test", "webapp", "active", "/tmp/cw", "IL5"),
    )

    # Seed some implemented controls
    for ctrl_id, status in [
        ("AC-1", "implemented"),
        ("AC-2", "implemented"),
        ("AC-3", "partially_implemented"),
        ("AU-1", "planned"),
        ("CM-1", "implemented"),
    ]:
        conn.execute(
            "INSERT INTO project_controls (project_id, control_id, implementation_status) "
            "VALUES (?, ?, ?)",
            ("proj-cw-1", ctrl_id, status),
        )

    conn.commit()
    conn.close()
    return db_path


@pytest.fixture(autouse=True)
def _clear_crosswalk_cache():
    """Clear the module-level crosswalk cache before each test."""
    import tools.compliance.crosswalk_engine as cwe
    cwe._CROSSWALK_CACHE = None
    cwe._ISO_BRIDGE_CACHE = None
    yield
    cwe._CROSSWALK_CACHE = None
    cwe._ISO_BRIDGE_CACHE = None


# ---------------------------------------------------------------------------
# AC-2 Cascade Tests
# ---------------------------------------------------------------------------

class TestAC2Cascade:
    """Verify AC-2 maps to FedRAMP, CMMC, 800-171, and other frameworks."""

    def test_ac2_exists_in_crosswalk(self):
        """AC-2 should be present in the crosswalk data."""
        frameworks = get_frameworks_for_control("AC-2")
        assert len(frameworks) > 0

    def test_ac2_maps_to_fedramp_moderate(self):
        frameworks = get_frameworks_for_control("AC-2")
        assert "fedramp_moderate" in frameworks

    def test_ac2_maps_to_fedramp_high(self):
        frameworks = get_frameworks_for_control("AC-2")
        assert "fedramp_high" in frameworks

    def test_ac2_maps_to_cmmc_level_2(self):
        frameworks = get_frameworks_for_control("AC-2")
        assert "cmmc_level_2" in frameworks
        assert "AC.L2-3.1.1" in str(frameworks["cmmc_level_2"])

    def test_ac2_maps_to_nist_800_171(self):
        frameworks = get_frameworks_for_control("AC-2")
        assert "nist_800_171" in frameworks


class TestControlNotFound:
    """Verify behavior for controls not in the crosswalk."""

    def test_unknown_control_returns_empty(self):
        frameworks = get_frameworks_for_control("ZZ-999")
        assert frameworks == {}


# ---------------------------------------------------------------------------
# Framework Query Tests
# ---------------------------------------------------------------------------

class TestFrameworkQuery:
    """Verify get_controls_for_framework returns correct results."""

    def test_fedramp_moderate_has_controls(self):
        controls = get_controls_for_framework("fedramp", "moderate")
        assert len(controls) > 0

    def test_fedramp_high_has_controls(self):
        controls = get_controls_for_framework("fedramp", "high")
        assert len(controls) > 0

    def test_cmmc_l2_has_controls(self):
        controls = get_controls_for_framework("cmmc", "l2")
        assert len(controls) > 0

    def test_unknown_framework_returns_empty(self):
        controls = get_controls_for_framework("nonexistent_framework")
        assert controls == []


# ---------------------------------------------------------------------------
# Impact Level Tests
# ---------------------------------------------------------------------------

class TestImpactLevel:
    """Verify get_controls_for_impact_level behavior."""

    def test_il4_returns_list(self):
        """IL4 query should return a list (may be empty if data uses il4_required key)."""
        controls = get_controls_for_impact_level("IL4")
        assert isinstance(controls, list)

    def test_il5_returns_list(self):
        controls = get_controls_for_impact_level("IL5")
        assert isinstance(controls, list)

    def test_il6_returns_list(self):
        controls = get_controls_for_impact_level("IL6")
        assert isinstance(controls, list)

    def test_invalid_il_raises(self):
        with pytest.raises(ValueError, match="Invalid impact level"):
            get_controls_for_impact_level("IL3")

    def test_il_keys_mapping_exists(self):
        """IL_KEYS should map IL4/IL5/IL6 to crosswalk keys."""
        assert "IL4" in IL_KEYS
        assert "IL5" in IL_KEYS
        assert "IL6" in IL_KEYS


# ---------------------------------------------------------------------------
# Coverage Computation Tests
# ---------------------------------------------------------------------------

class TestCoverageComputation:
    """Verify compute_crosswalk_coverage calculates per-framework coverage."""

    def test_coverage_returns_dict(self, crosswalk_db):
        coverage = compute_crosswalk_coverage("proj-cw-1", db_path=crosswalk_db)
        assert isinstance(coverage, dict)
        assert len(coverage) > 0

    def test_coverage_has_framework_keys(self, crosswalk_db):
        coverage = compute_crosswalk_coverage("proj-cw-1", db_path=crosswalk_db)
        # Should have entries for frameworks that have controls in the crosswalk
        for fw_key in coverage:
            assert fw_key in FRAMEWORK_KEYS

    def test_coverage_values_structure(self, crosswalk_db):
        coverage = compute_crosswalk_coverage("proj-cw-1", db_path=crosswalk_db)
        for fw_key, data in coverage.items():
            assert "total" in data
            assert "implemented" in data
            assert "coverage_pct" in data
            assert isinstance(data["coverage_pct"], float)

    def test_coverage_percentage_range(self, crosswalk_db):
        coverage = compute_crosswalk_coverage("proj-cw-1", db_path=crosswalk_db)
        for fw_key, data in coverage.items():
            assert 0.0 <= data["coverage_pct"] <= 100.0


# ---------------------------------------------------------------------------
# Gap Analysis Tests
# ---------------------------------------------------------------------------

class TestGapAnalysis:
    """Verify get_gap_analysis returns unimplemented controls."""

    def test_gap_analysis_returns_list(self, crosswalk_db):
        gaps = get_gap_analysis(
            "proj-cw-1", "fedramp", baseline="moderate", db_path=crosswalk_db,
        )
        assert isinstance(gaps, list)

    def test_gap_analysis_excludes_implemented(self, crosswalk_db):
        gaps = get_gap_analysis(
            "proj-cw-1", "fedramp", baseline="moderate", db_path=crosswalk_db,
        )
        implemented_ids = {"AC-1", "AC-2", "CM-1"}
        gap_nist_ids = {g["nist_id"].upper() for g in gaps}
        # Implemented controls should NOT appear in gaps
        for impl_id in implemented_ids:
            # Only check if the control is in the framework
            frameworks = get_frameworks_for_control(impl_id)
            if "fedramp_moderate" in frameworks:
                assert impl_id not in gap_nist_ids

    def test_gap_analysis_sorted_by_priority(self, crosswalk_db):
        gaps = get_gap_analysis(
            "proj-cw-1", "fedramp", baseline="moderate", db_path=crosswalk_db,
        )
        if len(gaps) >= 2:
            priorities = [g["priority"] for g in gaps]
            priority_order = {"P1": 0, "P2": 1, "P3": 2}
            numeric = [priority_order.get(p, 99) for p in priorities]
            assert numeric == sorted(numeric)

    def test_gap_has_required_fields(self, crosswalk_db):
        gaps = get_gap_analysis(
            "proj-cw-1", "fedramp", baseline="moderate", db_path=crosswalk_db,
        )
        if gaps:
            gap = gaps[0]
            assert "nist_id" in gap
            assert "title" in gap
            assert "priority" in gap
            assert "status" in gap


# ---------------------------------------------------------------------------
# Crosswalk Summary Tests
# ---------------------------------------------------------------------------

class TestCrosswalkSummary:
    """Verify get_crosswalk_summary returns aggregate statistics."""

    def test_summary_has_total_controls(self):
        summary = get_crosswalk_summary()
        assert "total_controls" in summary
        assert summary["total_controls"] > 0

    def test_summary_has_frameworks(self):
        summary = get_crosswalk_summary()
        assert "frameworks" in summary
        assert len(summary["frameworks"]) > 0

    def test_summary_framework_has_count_and_name(self):
        summary = get_crosswalk_summary()
        for fw_key, fw_data in summary["frameworks"].items():
            assert "count" in fw_data
            assert "name" in fw_data

    def test_summary_has_families(self):
        summary = get_crosswalk_summary()
        assert "families" in summary
        assert "AC" in summary["families"]


# ---------------------------------------------------------------------------
# Dual-Hub Bridge Tests (NIST <-> ISO 27001)
# ---------------------------------------------------------------------------

class TestDualHubBridge:
    """Verify the bidirectional NIST <-> ISO 27001 bridge (ADR D111)."""

    def test_load_iso_bridge(self):
        """ISO bridge should load (may be empty if file not present)."""
        bridge = load_iso_bridge()
        assert isinstance(bridge, list)

    def test_iso_for_nist_control(self):
        """If bridge data exists, NIST controls should map to ISO controls."""
        bridge = load_iso_bridge()
        if not bridge:
            pytest.skip("ISO bridge data file not present")
        # Find a NIST control that has an ISO mapping
        nist_id = bridge[0].get("nist_800_53", [None])[0] if bridge else None
        if nist_id:
            results = get_iso_for_nist_control(nist_id)
            assert len(results) > 0
            assert "iso_27001" in results[0]

    def test_nist_for_iso_control(self):
        """If bridge data exists, ISO controls should map to NIST controls."""
        bridge = load_iso_bridge()
        if not bridge:
            pytest.skip("ISO bridge data file not present")
        iso_id = bridge[0].get("iso_27001")
        if iso_id:
            nist_controls = get_nist_for_iso_control(iso_id)
            assert isinstance(nist_controls, list)
            assert len(nist_controls) > 0

    def test_nonexistent_iso_control_returns_empty(self):
        result = get_nist_for_iso_control("A.999.999")
        assert result == []

    def test_nonexistent_nist_for_iso_returns_empty(self):
        result = get_iso_for_nist_control("ZZ-999")
        assert result == []


# ---------------------------------------------------------------------------
# AC-2 ISO 27001 Bridge (if bridge data present)
# ---------------------------------------------------------------------------

class TestAC2ISOBridge:
    """Verify AC-2 maps through the NIST->ISO bridge."""

    def test_ac2_may_have_iso_mapping(self):
        """AC-2 should have ISO 27001 mappings via the bridge if data exists."""
        frameworks = get_frameworks_for_control("AC-2")
        # iso_27001 may or may not be present depending on bridge data
        # Just verify the lookup does not crash
        iso_mappings = get_iso_for_nist_control("AC-2")
        assert isinstance(iso_mappings, list)
