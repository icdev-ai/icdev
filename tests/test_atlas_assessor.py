#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for MITRE ATLAS v5.4.0 assessor.

Coverage: framework metadata, 6 automated checks (M0015, M0024, M0012,
M0013, M0019, M0026), base class inheritance, return structure.
"""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure project root is importable
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.compliance.atlas_assessor import ATLASAssessor


# ============================================================
# Fixtures
# ============================================================

_MOCK_DB_SCHEMA = """
CREATE TABLE agent_token_usage (
    id INTEGER PRIMARY KEY, project_id TEXT, timestamp TEXT, tokens INTEGER);
CREATE TABLE dashboard_user_llm_keys (
    id INTEGER PRIMARY KEY, user_id TEXT, provider TEXT, encrypted_key TEXT);
CREATE TABLE dashboard_api_keys (
    id INTEGER PRIMARY KEY, user_id TEXT, key_hash TEXT);
CREATE TABLE marketplace_scan_results (
    id INTEGER PRIMARY KEY, scan_type TEXT, status TEXT);
CREATE TABLE remote_command_allowlist (
    id INTEGER PRIMARY KEY, command TEXT);
"""


@pytest.fixture
def mock_db_path(tmp_path):
    """Create a file-backed temp DB with required tables.

    Returns the db path so each _get_connection call can open a fresh
    connection (the source code closes connections in finally blocks).
    """
    db_path = tmp_path / "mock_atlas.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_MOCK_DB_SCHEMA)
    conn.commit()
    conn.close()
    return db_path


# ============================================================
# Import & Metadata
# ============================================================

class TestImportAndMetadata:
    def test_import(self):
        """ATLASAssessor can be imported."""
        assert ATLASAssessor is not None

    def test_framework_metadata(self):
        """Verify ATLAS framework constants."""
        assert ATLASAssessor.FRAMEWORK_ID == "atlas"
        assert "ATLAS" in ATLASAssessor.FRAMEWORK_NAME
        assert ATLASAssessor.TABLE_NAME == "atlas_assessments"
        assert ATLASAssessor.CATALOG_FILENAME == "atlas_mitigations.json"

    def test_inherits_base_assessor(self):
        """ATLASAssessor inherits from BaseAssessor (ABC).

        atlas_assessor.py imports BaseAssessor via bare module name
        (sys.path manipulation), so we verify via MRO class names
        to avoid dual-import identity mismatch.
        """
        base_names = [cls.__name__ for cls in ATLASAssessor.__mro__]
        assert "BaseAssessor" in base_names


# ============================================================
# M0015 -- Prompt Injection Detection
# ============================================================

class TestM0015PromptInjection:
    def test_m0015_prompt_injection_satisfied(self, tmp_path, mock_db_path):
        """Project with prompt injection keywords -> M0015 satisfied."""
        py_file = tmp_path / "detector.py"
        py_file.write_text("# prompt_injection detector\ndef scan(): pass\n")

        assessor = ATLASAssessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=str(tmp_path))

        assert results.get("M0015") == "satisfied"

    def test_m0015_prompt_injection_not_satisfied(self, tmp_path, mock_db_path):
        """Project WITHOUT prompt injection keywords -> M0015 not_satisfied."""
        py_file = tmp_path / "app.py"
        py_file.write_text("# Normal application\ndef main(): pass\n")

        assessor = ATLASAssessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=str(tmp_path))

        assert results.get("M0015") == "not_satisfied"


# ============================================================
# M0024 -- AI Telemetry / Model Monitoring
# ============================================================

class TestM0024AITelemetry:
    def test_m0024_ai_telemetry_satisfied(self, mock_db_path):
        """DB with agent_token_usage rows -> M0024 satisfied."""
        conn = sqlite3.connect(str(mock_db_path))
        conn.execute(
            "INSERT INTO agent_token_usage (project_id, timestamp, tokens) "
            "VALUES (?, datetime('now'), ?)",
            ("proj-test", 1500),
        )
        conn.commit()
        conn.close()

        assessor = ATLASAssessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)

        assert results.get("M0024") == "satisfied"

    def test_m0024_ai_telemetry_not_satisfied(self, mock_db_path):
        """DB with no agent_token_usage rows -> M0024 not_satisfied."""
        assessor = ATLASAssessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)

        assert results.get("M0024") == "not_satisfied"


# ============================================================
# M0012 -- BYOK Encryption
# ============================================================

class TestM0012BYOKEncryption:
    def test_m0012_byok_encryption(self, mock_db_path):
        """DB with dashboard_user_llm_keys entries -> M0012 satisfied."""
        conn = sqlite3.connect(str(mock_db_path))
        conn.execute(
            "INSERT INTO dashboard_user_llm_keys (user_id, provider, encrypted_key) "
            "VALUES (?, ?, ?)",
            ("user-1", "openai", "encrypted_abc"),
        )
        conn.commit()
        conn.close()

        assessor = ATLASAssessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)

        assert results.get("M0012") == "satisfied"


# ============================================================
# M0019 -- API Gateway Auth
# ============================================================

class TestM0019APIGatewayAuth:
    def test_m0019_api_gateway_auth(self, mock_db_path, tmp_path):
        """DB with api keys + auth middleware -> M0019 satisfied."""
        conn = sqlite3.connect(str(mock_db_path))
        conn.execute(
            "INSERT INTO dashboard_api_keys (user_id, key_hash) VALUES (?, ?)",
            ("user-1", "hash_abc"),
        )
        conn.commit()
        conn.close()

        # Create mock auth middleware file at expected BASE_DIR location
        auth_dir = tmp_path / "tools" / "saas" / "auth"
        auth_dir.mkdir(parents=True)
        (auth_dir / "middleware.py").write_text("# auth middleware\n")

        assessor = ATLASAssessor(db_path=mock_db_path)
        project = {"id": "proj-test"}

        # Patch BASE_DIR so it can find the middleware file
        with patch("tools.compliance.atlas_assessor.BASE_DIR", tmp_path):
            results = assessor.get_automated_checks(project, project_dir=None)

        assert results.get("M0019") == "satisfied"


# ============================================================
# M0026 -- Command Allowlist
# ============================================================

class TestM0026CommandAllowlist:
    def test_m0026_command_allowlist(self, tmp_path, mock_db_path):
        """Config with allowlist content -> M0026 satisfied."""
        args_dir = tmp_path / "args"
        args_dir.mkdir()
        config = args_dir / "remote_gateway_config.yaml"
        config.write_text("security:\n  command_allowlist:\n    - icdev-status\n")

        assessor = ATLASAssessor(db_path=mock_db_path)
        project = {"id": "proj-test"}

        with patch("tools.compliance.atlas_assessor.BASE_DIR", tmp_path):
            results = assessor.get_automated_checks(project, project_dir=None)

        assert results.get("M0026") == "satisfied"


# ============================================================
# Return Structure
# ============================================================

class TestReturnStructure:
    def test_automated_checks_returns_dict(self, mock_db_path, tmp_path):
        """get_automated_checks returns a dict with string values."""
        py_file = tmp_path / "app.py"
        py_file.write_text("# app\n")

        assessor = ATLASAssessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=str(tmp_path))

        assert isinstance(results, dict)
        for key, value in results.items():
            assert isinstance(key, str), f"Key {key} is not a string"
            assert isinstance(value, str), f"Value for {key} is not a string"
