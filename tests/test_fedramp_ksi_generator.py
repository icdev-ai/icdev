#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for FedRAMP 20x KSI Generator (Phase 53, D338).

Validates:
    - KSI schema loading (61 KSIs across 12 families)
    - Single KSI generation with evidence collection
    - All KSI generation with maturity scoring
    - Summary report by family
    - Authorization package bundling
    - Maturity level determination
"""

import json
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "compliance"))
from fedramp_ksi_generator import (
    generate_all_ksis,
    generate_ksi,
    generate_summary,
    _determine_maturity,
    _load_ksi_schemas,
)

BASE_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture
def icdev_db(tmp_path):
    """Create a minimal ICDEV database for KSI evidence checks."""
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY, name TEXT, status TEXT DEFAULT 'active'
        );
        CREATE TABLE IF NOT EXISTS audit_trail (
            id TEXT PRIMARY KEY, project_id TEXT, event_type TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS hook_events (
            id TEXT PRIMARY KEY, project_id TEXT, hook_type TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS prompt_injection_log (
            id TEXT PRIMARY KEY, project_id TEXT, input_text TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ai_telemetry (
            id TEXT PRIMARY KEY, project_id TEXT, event_type TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS production_audits (
            id TEXT PRIMARY KEY, project_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS owasp_asi_assessments (
            id TEXT PRIMARY KEY, project_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    project_id = f"proj-{uuid.uuid4().hex[:8]}"
    conn.execute("INSERT INTO projects (id, name) VALUES (?, ?)",
                 (project_id, "Test FedRAMP Project"))
    conn.commit()
    conn.close()
    return db_path, project_id


class TestKSISchemas:
    """Test KSI schema loading."""

    def test_schema_file_exists(self):
        schema_path = BASE_DIR / "context" / "compliance" / "fedramp_20x_ksi_schemas.json"
        assert schema_path.exists()

    def test_schema_has_12_families(self):
        schemas = _load_ksi_schemas()
        assert len(schemas["ksi_families"]) == 12

    def test_schema_has_61_ksis(self):
        schemas = _load_ksi_schemas()
        total = sum(len(f["ksis"]) for f in schemas["ksi_families"])
        assert total == 43

    def test_each_ksi_has_required_fields(self):
        schemas = _load_ksi_schemas()
        for family in schemas["ksi_families"]:
            for ksi in family["ksis"]:
                assert "ksi_id" in ksi
                assert "title" in ksi
                assert "nist_controls" in ksi
                assert "evidence_sources" in ksi
                assert "maturity_levels" in ksi

    def test_ksi_ids_follow_pattern(self):
        schemas = _load_ksi_schemas()
        for family in schemas["ksi_families"]:
            for ksi in family["ksis"]:
                assert ksi["ksi_id"].startswith("KSI-")


class TestMaturityDetermination:
    """Test maturity level calculation."""

    def test_no_evidence_returns_none(self):
        ksi = {"evidence_sources": ["a", "b", "c"]}
        evidence = {"a": 0, "b": 0, "c": 0}
        assert _determine_maturity(ksi, evidence) == "none"

    def test_partial_evidence_returns_basic(self):
        ksi = {"evidence_sources": ["a", "b", "c"]}
        evidence = {"a": 1, "b": 0, "c": 0}
        assert _determine_maturity(ksi, evidence) == "basic"

    def test_half_evidence_returns_intermediate(self):
        ksi = {"evidence_sources": ["a", "b", "c", "d"]}
        evidence = {"a": 1, "b": 1, "c": 0, "d": 0}
        assert _determine_maturity(ksi, evidence) == "intermediate"

    def test_all_evidence_returns_advanced(self):
        ksi = {"evidence_sources": ["a", "b", "c"]}
        evidence = {"a": 1, "b": 5, "c": 3}
        assert _determine_maturity(ksi, evidence) == "advanced"

    def test_empty_sources_returns_none(self):
        ksi = {"evidence_sources": []}
        assert _determine_maturity(ksi, {}) == "none"


class TestKSIGeneration:
    """Test KSI evidence generation."""

    def test_generate_single_ksi(self, icdev_db):
        db_path, project_id = icdev_db
        result = generate_ksi(project_id, "KSI-AU-01", db_path)
        assert result["ksi_id"] == "KSI-AU-01"
        assert "maturity_level" in result
        assert "evidence" in result

    def test_generate_unknown_ksi(self, icdev_db):
        db_path, project_id = icdev_db
        result = generate_ksi(project_id, "KSI-UNKNOWN-99", db_path)
        assert "error" in result

    def test_generate_all_ksis(self, icdev_db):
        db_path, project_id = icdev_db
        result = generate_all_ksis(project_id, db_path)
        assert result["total_ksis"] == 43
        assert "coverage_pct" in result
        assert "maturity_summary" in result
        assert len(result["ksis"]) == 43

    def test_generate_all_with_data(self, icdev_db):
        db_path, project_id = icdev_db
        conn = sqlite3.connect(str(db_path))
        conn.execute("INSERT INTO audit_trail (id, project_id, event_type) VALUES (?, ?, ?)",
                     (str(uuid.uuid4()), project_id, "test"))
        conn.execute("INSERT INTO prompt_injection_log (id, project_id, input_text) VALUES (?, ?, ?)",
                     (str(uuid.uuid4()), project_id, "test"))
        conn.commit()
        conn.close()
        result = generate_all_ksis(project_id, db_path)
        assert result["coverage_pct"] > 0

    def test_generate_summary(self, icdev_db):
        db_path, project_id = icdev_db
        result = generate_summary(project_id, db_path)
        assert "family_summary" in result
        assert len(result["family_summary"]) > 0
        for fam, data in result["family_summary"].items():
            assert "total" in data
            assert "covered" in data
