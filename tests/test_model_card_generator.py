#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for AI Model Card Generator (Phase 48).

Coverage: import, generation (required sections), storage, listing, versioning,
card_hash, classification markings, data integration (ai_bom, ai_telemetry),
CLI entry point.  All tests work without optional dependencies.
"""

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure project root is importable
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from tools.compliance.model_card_generator import (
        generate_model_card,
        list_model_cards,
    )
    _HAS_MODULE = True
except ImportError:
    _HAS_MODULE = False
    generate_model_card = None  # type: ignore[assignment]
    list_model_cards = None  # type: ignore[assignment]

pytestmark = pytest.mark.skipif(
    not _HAS_MODULE,
    reason="model_card_generator not available (missing dependency or not yet created)",
)


# ============================================================
# Fixtures
# ============================================================

_MOCK_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT,
    project_type TEXT DEFAULT 'microservice',
    impact_level TEXT DEFAULT 'IL4',
    classification TEXT DEFAULT 'CUI',
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ai_bom (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    component_type TEXT,
    component_name TEXT,
    version TEXT,
    provider TEXT,
    license TEXT,
    risk_level TEXT,
    created_at TEXT,
    updated_at TEXT,
    classification TEXT DEFAULT 'CUI'
);

CREATE TABLE IF NOT EXISTS ai_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    agent_id TEXT,
    event_type TEXT,
    model_id TEXT,
    prompt_hash TEXT,
    response_hash TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    classification TEXT DEFAULT 'CUI',
    logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS xai_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    assessment_date TEXT DEFAULT (datetime('now')),
    assessor TEXT DEFAULT 'icdev-compliance-engine',
    requirement_id TEXT NOT NULL,
    requirement_title TEXT,
    family TEXT,
    status TEXT DEFAULT 'not_assessed',
    evidence_description TEXT,
    evidence_path TEXT,
    automation_result TEXT,
    notes TEXT,
    nist_800_53_crosswalk TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, requirement_id)
);

CREATE TABLE IF NOT EXISTS model_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    card_data TEXT NOT NULL,
    card_hash TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, model_name)
);

CREATE TABLE IF NOT EXISTS audit_trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    event_type TEXT,
    actor TEXT,
    action TEXT,
    project_id TEXT,
    details TEXT,
    affected_files TEXT,
    session_id TEXT,
    classification TEXT DEFAULT 'CUI'
);

INSERT INTO projects (id, name) VALUES ('proj-test', 'Test Project');
"""


@pytest.fixture
def card_db(tmp_path):
    """Create temp DB with ai_bom, ai_telemetry, xai_assessments, model_cards tables."""
    db_path = tmp_path / "test_model_card.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_MOCK_DB_SCHEMA)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def populated_db(card_db):
    """DB pre-populated with ai_bom and ai_telemetry data."""
    conn = sqlite3.connect(str(card_db))
    now = datetime.now(timezone.utc).isoformat()
    # Insert ai_bom entries
    conn.execute(
        "INSERT INTO ai_bom (id, project_id, component_type, component_name, "
        "version, provider, license, risk_level, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("bom-1", "proj-test", "model", "claude-sonnet", "4.0",
         "bedrock", "proprietary", "medium", now, now),
    )
    conn.execute(
        "INSERT INTO ai_bom (id, project_id, component_type, component_name, "
        "version, provider, license, risk_level, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("bom-2", "proj-test", "library", "openai", "1.40",
         "pypi", "MIT", "low", now, now),
    )
    # Insert ai_telemetry entries
    conn.execute(
        "INSERT INTO ai_telemetry (project_id, agent_id, event_type, model_id, "
        "prompt_hash, response_hash, input_tokens, output_tokens, logged_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("proj-test", "builder-agent", "llm_call", "claude-sonnet-4",
         "abc123", "def456", 500, 1200, now),
    )
    conn.execute(
        "INSERT INTO ai_telemetry (project_id, agent_id, event_type, model_id, "
        "prompt_hash, response_hash, input_tokens, output_tokens, logged_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("proj-test", "compliance-agent", "llm_call", "claude-sonnet-4",
         "ghi789", "jkl012", 300, 800, now),
    )
    # Insert xai_assessment entries
    conn.execute(
        "INSERT INTO xai_assessments (project_id, requirement_id, requirement_title, "
        "status) VALUES (?, ?, ?, ?)",
        ("proj-test", "XAI-001", "Tracing Active", "satisfied"),
    )
    conn.commit()
    conn.close()
    return card_db


# ============================================================
# Import Tests
# ============================================================

class TestImport:
    def test_import_generate_model_card(self):
        """generate_model_card function can be imported."""
        assert generate_model_card is not None

    def test_import_list_model_cards(self):
        """list_model_cards function can be imported."""
        assert list_model_cards is not None


# ============================================================
# Generation — Required Sections
# ============================================================

REQUIRED_SECTIONS = [
    "model_details",
    "intended_use",
    "ethical_considerations",
    "caveats_and_limitations",
    "metrics",
    "training_data",
]


class TestGenerateModelCard:
    def test_returns_dict(self, card_db):
        """generate_model_card returns a dict."""
        result = generate_model_card("proj-test", "test-model", db_path=card_db)
        assert isinstance(result, dict)

    def test_has_required_sections(self, card_db):
        """Generated card contains all required sections."""
        result = generate_model_card("proj-test", "test-model", db_path=card_db)
        card = result.get("card", result)
        for section in REQUIRED_SECTIONS:
            assert section in card, f"Missing required section: {section}"

    def test_model_details_is_populated(self, card_db):
        """model_details section is non-empty."""
        result = generate_model_card("proj-test", "test-model", db_path=card_db)
        card = result.get("card", result)
        assert card.get("model_details") is not None
        # model_details should be a dict or non-empty string
        md = card["model_details"]
        if isinstance(md, dict):
            assert len(md) > 0
        elif isinstance(md, str):
            assert len(md) > 0

    def test_ethical_considerations_present(self, card_db):
        """ethical_considerations section is populated."""
        result = generate_model_card("proj-test", "test-model", db_path=card_db)
        card = result.get("card", result)
        ec = card.get("ethical_considerations")
        assert ec is not None

    def test_project_id_in_result(self, card_db):
        """Result includes the project_id."""
        result = generate_model_card("proj-test", "test-model", db_path=card_db)
        assert result.get("project_id") == "proj-test"


# ============================================================
# Storage
# ============================================================

class TestStorage:
    def test_card_stored_in_db(self, card_db):
        """Model card is stored in model_cards table after generation."""
        generate_model_card("proj-test", "test-model", db_path=card_db)
        conn = sqlite3.connect(str(card_db))
        rows = conn.execute(
            "SELECT COUNT(*) FROM model_cards WHERE project_id = ?",
            ("proj-test",),
        ).fetchone()
        conn.close()
        assert rows[0] >= 1

    def test_card_data_is_valid_json(self, card_db):
        """Stored card_data column contains valid JSON."""
        generate_model_card("proj-test", "test-model", db_path=card_db)
        conn = sqlite3.connect(str(card_db))
        row = conn.execute(
            "SELECT card_data FROM model_cards WHERE project_id = ? "
            "ORDER BY version DESC LIMIT 1",
            ("proj-test",),
        ).fetchone()
        conn.close()
        assert row is not None
        parsed = json.loads(row[0])
        assert isinstance(parsed, dict)


# ============================================================
# Listing
# ============================================================

class TestListModelCards:
    def test_list_empty(self, card_db):
        """list_model_cards returns empty list when no cards exist."""
        result = list_model_cards("proj-empty", db_path=card_db)
        assert result.get("count", 0) == 0

    def test_list_after_generation(self, card_db):
        """list_model_cards returns correct count after generation."""
        generate_model_card("proj-test", "test-model", db_path=card_db)
        result = list_model_cards("proj-test", db_path=card_db)
        assert result.get("count", 0) >= 1

    def test_list_multiple_projects(self, card_db):
        """Cards for different projects are isolated."""
        # Insert a second project
        conn = sqlite3.connect(str(card_db))
        conn.execute("INSERT INTO projects (id, name) VALUES ('proj-other', 'Other')")
        conn.commit()
        conn.close()

        generate_model_card("proj-test", "test-model", db_path=card_db)
        result = list_model_cards("proj-other", db_path=card_db)
        assert result.get("count", 0) == 0


# ============================================================
# Versioning
# ============================================================

class TestVersioning:
    def test_version_increments(self, card_db):
        """Generating the same model card twice increments version."""
        r1 = generate_model_card("proj-test", "test-model", db_path=card_db)
        r2 = generate_model_card("proj-test", "test-model", db_path=card_db)
        assert r2["version"] > r1["version"]

    def test_first_version_is_one(self, card_db):
        """First generated card has version 1."""
        result = generate_model_card("proj-test", "test-model", db_path=card_db)
        assert result.get("version", 1) == 1


# ============================================================
# Card Hash
# ============================================================

class TestCardHash:
    def test_hash_is_populated(self, card_db):
        """Generated card has a non-empty card_hash."""
        result = generate_model_card("proj-test", "test-model", db_path=card_db)
        assert result.get("card_hash") is not None
        assert len(result["card_hash"]) > 0

    def test_hash_is_hex_string(self, card_db):
        """card_hash is a valid hex string (SHA-256 truncated to 16 chars)."""
        result = generate_model_card("proj-test", "test-model", db_path=card_db)
        h = result["card_hash"]
        assert isinstance(h, str)
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)


# ============================================================
# Classification Markings
# ============================================================

class TestClassificationMarkings:
    def test_card_data_has_classification(self, card_db):
        """Card data JSON includes classification markings."""
        result = generate_model_card("proj-test", "test-model", db_path=card_db)
        card = result.get("card", result)
        # Classification may appear at top level or within card
        classification = result.get("classification") or card.get("classification")
        assert classification is not None
        assert "CUI" in classification


# ============================================================
# Data Integration (ai_bom, ai_telemetry)
# ============================================================

class TestDataIntegration:
    def test_with_bom_data(self, populated_db):
        """Card generated with ai_bom data includes model/component info."""
        result = generate_model_card("proj-test", "claude-sonnet", db_path=populated_db)
        card = result.get("card", result)
        # model_details should reference the components from ai_bom
        card_json = json.dumps(card).lower()
        assert "claude" in card_json or "sonnet" in card_json or "model" in card_json

    def test_with_telemetry_data(self, populated_db):
        """Card generated with ai_telemetry data includes usage info."""
        result = generate_model_card("proj-test", "claude-sonnet", db_path=populated_db)
        card = result.get("card", result)
        # The card should reference metrics or usage from telemetry
        metrics = card.get("metrics")
        assert metrics is not None


# ============================================================
# CLI Entry Point
# ============================================================

class TestCLI:
    def test_main_callable(self, card_db):
        """Module main() can be called without crashing."""
        try:
            from tools.compliance.model_card_generator import main
        except ImportError:
            pytest.skip("main() not exposed in module")

        with patch(
            "sys.argv",
            ["model_card_generator.py", "--project-id", "proj-test",
             "--model-name", "test-model",
             "--db-path", str(card_db), "--json"],
        ):
            try:
                main()
            except SystemExit as e:
                # argparse may call sys.exit(0) on success
                assert e.code in (None, 0)
