#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for AI Bill of Materials generator.

Coverage: import, scanning (llm_config, requirements.txt, .mcp.json),
hash computation, risk assessment, database storage, gate evaluation.
All tests work without optional dependencies (graceful degradation).
"""

import json
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root is importable
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from tools.security.ai_bom_generator import AIBOMGenerator, AI_FRAMEWORK_PACKAGES
    _HAS_AI_BOM = True
except ImportError:
    _HAS_AI_BOM = False


pytestmark = pytest.mark.skipif(
    not _HAS_AI_BOM,
    reason="AIBOMGenerator not available (missing dependency)",
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def bom_db(tmp_path):
    """Create temp DB with ai_bom and audit_trail tables."""
    db_path = tmp_path / "test_bom.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE ai_bom (
        id TEXT PRIMARY KEY, project_id TEXT, component_type TEXT,
        component_name TEXT, version TEXT, provider TEXT,
        license TEXT, risk_level TEXT,
        created_at TEXT, updated_at TEXT, classification TEXT)""")
    conn.execute("""CREATE TABLE audit_trail (
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
        event_type TEXT, actor TEXT, action TEXT, project_id TEXT,
        details TEXT, affected_files TEXT, session_id TEXT,
        classification TEXT)""")
    conn.execute("""CREATE TABLE projects (
        id TEXT PRIMARY KEY, name TEXT, type TEXT DEFAULT 'webapp',
        classification TEXT DEFAULT 'CUI', status TEXT DEFAULT 'active',
        directory_path TEXT DEFAULT '/tmp')""")
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def generator(bom_db):
    """AIBOMGenerator with temp DB."""
    return AIBOMGenerator(db_path=bom_db)


# ============================================================
# Import
# ============================================================

class TestImport:
    def test_import(self):
        """AIBOMGenerator can be imported."""
        assert AIBOMGenerator is not None

    def test_ai_framework_packages_exist(self):
        """AI framework package set exists and has known entries."""
        assert "openai" in AI_FRAMEWORK_PACKAGES
        assert "anthropic" in AI_FRAMEWORK_PACKAGES
        assert "boto3" in AI_FRAMEWORK_PACKAGES


# ============================================================
# Scanning
# ============================================================

class TestScanLLMConfig:
    def test_scan_llm_config(self, tmp_path):
        """Scan a mock llm_config.yaml for model components."""
        args_dir = tmp_path / "args"
        args_dir.mkdir()
        config_file = args_dir / "llm_config.yaml"
        config_file.write_text(
            "models:\n"
            "  claude-sonnet:\n"
            "    model_id: claude-sonnet-4-20250514\n"
            "    provider: bedrock\n"
        )

        gen = AIBOMGenerator()
        components = gen._scan_llm_config(tmp_path)
        assert len(components) >= 1
        assert any(c["component_name"] == "claude-sonnet" for c in components)


class TestScanRequirements:
    def test_scan_requirements(self, tmp_path):
        """Scan requirements.txt containing AI framework deps."""
        req_file = tmp_path / "requirements.txt"
        req_file.write_text(
            "flask==3.0\n"
            "openai==1.0\n"
            "anthropic==0.20\n"
            "requests==2.31\n"
        )

        gen = AIBOMGenerator()
        components = gen._scan_requirements(tmp_path)
        names = [c["component_name"] for c in components]
        assert "openai" in names
        assert "anthropic" in names
        # Non-AI packages should be excluded
        assert "flask" not in names
        assert "requests" not in names


class TestScanMCPConfig:
    def test_scan_mcp_config(self, tmp_path):
        """Scan .mcp.json for MCP server entries."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text(json.dumps({
            "mcpServers": {
                "icdev-core": {
                    "command": "python",
                    "args": ["tools/mcp/core_server.py"],
                },
                "icdev-builder": {
                    "command": "python",
                    "args": ["tools/mcp/builder_server.py"],
                },
            }
        }))

        gen = AIBOMGenerator()
        components = gen._scan_mcp_config(tmp_path)
        assert len(components) == 2
        names = [c["component_name"] for c in components]
        assert "icdev-core" in names
        assert "icdev-builder" in names


# ============================================================
# Utility Methods
# ============================================================

class TestComputeHash:
    def test_compute_hash(self):
        """Hash is a hex string of length 64 (SHA-256)."""
        gen = AIBOMGenerator()
        h = gen._compute_hash({
            "component_type": "model",
            "component_name": "claude-sonnet",
            "version": "4.0",
            "provider": "bedrock",
        })
        assert isinstance(h, str)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


class TestAssessRisk:
    def test_assess_risk_cloud_model(self):
        """Cloud-hosted LLM model gets medium risk."""
        gen = AIBOMGenerator()
        risk = gen._assess_risk({
            "component_type": "model",
            "provider": "bedrock",
        })
        assert risk == "medium"

    def test_assess_risk_local_model(self):
        """Ollama local model gets low risk."""
        gen = AIBOMGenerator()
        risk = gen._assess_risk({
            "component_type": "model",
            "provider": "ollama",
        })
        assert risk == "low"

    def test_assess_risk_unversioned_library(self):
        """Unversioned library gets high risk."""
        gen = AIBOMGenerator()
        risk = gen._assess_risk({
            "component_type": "library",
            "component_name": "some-lib",
            "version": "unspecified",
            "provider": "pypi",
        })
        assert risk == "high"


# ============================================================
# Database Storage
# ============================================================

class TestStoreBOM:
    def test_store_bom(self, generator, bom_db):
        """Store components and verify rows inserted."""
        components = [
            {
                "component_type": "model",
                "component_name": "claude-sonnet",
                "version": "4.0",
                "provider": "bedrock",
                "license": "proprietary",
                "risk_level": "medium",
            },
            {
                "component_type": "library",
                "component_name": "openai",
                "version": "1.0",
                "provider": "pypi",
                "license": "MIT",
                "risk_level": "low",
            },
        ]

        stored = generator.store_bom("proj-test", components)
        assert stored == 2

        conn = sqlite3.connect(str(bom_db))
        rows = conn.execute(
            "SELECT COUNT(*) as cnt FROM ai_bom WHERE project_id = ?",
            ("proj-test",),
        ).fetchone()
        conn.close()
        assert rows[0] == 2


# ============================================================
# Gate Evaluation
# ============================================================

class TestGateEvaluation:
    def test_evaluate_gate_no_bom(self, generator):
        """Gate should fail when no BOM exists."""
        result = generator.evaluate_gate("proj-empty")
        assert result["pass"] is False
        assert any("ai_bom_missing" in issue for issue in result["blocking_issues"])

    def test_evaluate_gate_stale_bom(self, generator, bom_db):
        """Gate should warn when BOM is older than 90 days."""
        old_date = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        conn = sqlite3.connect(str(bom_db))
        conn.execute(
            "INSERT INTO ai_bom (id, project_id, component_type, component_name, "
            "version, provider, risk_level, created_at, updated_at, classification) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("comp-1", "proj-stale", "model", "old-model", "1.0",
             "bedrock", "medium", old_date, old_date, "CUI"),
        )
        conn.commit()
        conn.close()

        result = generator.evaluate_gate("proj-stale")
        assert result["pass"] is True  # Stale is a warning, not a blocker
        assert any("ai_bom_stale" in w for w in result["warnings"])

    def test_evaluate_gate_pass(self, generator, bom_db):
        """Gate should pass when BOM is current and complete."""
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(bom_db))
        conn.execute(
            "INSERT INTO ai_bom (id, project_id, component_type, component_name, "
            "version, provider, risk_level, created_at, updated_at, classification) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("comp-1", "proj-ok", "model", "claude", "4.0",
             "bedrock", "low", now, now, "CUI"),
        )
        conn.commit()
        conn.close()

        result = generator.evaluate_gate("proj-ok")
        assert result["pass"] is True
        assert len(result["blocking_issues"]) == 0


# ============================================================
# Full Scan Integration
# ============================================================

class TestScanProjectIntegration:
    def test_scan_project_integration(self, tmp_path):
        """Full scan with llm_config.yaml + requirements.txt."""
        # Create llm config
        args_dir = tmp_path / "args"
        args_dir.mkdir()
        (args_dir / "llm_config.yaml").write_text(
            "models:\n"
            "  test-model:\n"
            "    model_id: test-v1\n"
            "    provider: ollama\n"
        )

        # Create requirements.txt
        (tmp_path / "requirements.txt").write_text(
            "openai==1.40\n"
            "numpy==1.26\n"
            "flask==3.0\n"
        )

        gen = AIBOMGenerator()
        result = gen.scan_project("proj-int", str(tmp_path))

        assert result["project_id"] == "proj-int"
        assert result["total_components"] >= 2  # At least model + openai
        assert "components" in result

        # Verify components have required fields
        for comp in result["components"]:
            assert "component_type" in comp
            assert "component_name" in comp
            assert "hash" in comp
            assert "risk_level" in comp
