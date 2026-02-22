#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools/security/ai_telemetry_logger.py."""

import sqlite3
from pathlib import Path

import pytest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.security.ai_telemetry_logger import AITelemetryLogger


@pytest.fixture
def logger(tmp_path):
    """Create a telemetry logger with temp DB."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ai_telemetry (
            id TEXT PRIMARY KEY, project_id TEXT, user_id TEXT, agent_id TEXT,
            model_id TEXT NOT NULL, provider TEXT NOT NULL, function TEXT,
            prompt_hash TEXT NOT NULL, response_hash TEXT,
            input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
            thinking_tokens INTEGER DEFAULT 0, latency_ms REAL DEFAULT 0.0,
            cost_usd REAL DEFAULT 0.0, classification TEXT DEFAULT 'CUI',
            api_key_source TEXT DEFAULT 'system', injection_scan_result TEXT,
            logged_at TEXT NOT NULL
        );
    """)
    conn.close()
    return AITelemetryLogger(db_path=db_path)


class TestLogging:
    def test_log_interaction(self, logger):
        entry_id = logger.log_ai_interaction(
            model_id="claude-opus-4-6", provider="bedrock",
            prompt_hash="abc123", response_hash="def456",
            input_tokens=100, output_tokens=50,
            project_id="proj-1", function="code_generation",
        )
        assert entry_id is not None

    def test_log_with_all_fields(self, logger):
        entry_id = logger.log_ai_interaction(
            model_id="gpt-4o", provider="azure_openai",
            prompt_hash="hash1", response_hash="hash2",
            input_tokens=200, output_tokens=100, thinking_tokens=50,
            latency_ms=1500.0, cost_usd=0.05,
            agent_id="builder-agent", user_id="user-1",
            project_id="proj-1", function="nlq_sql",
            classification="CUI", api_key_source="byok",
            injection_scan_result="clean",
        )
        assert entry_id is not None

    def test_log_without_db(self):
        lg = AITelemetryLogger(db_path=Path("/nonexistent/db"))
        entry_id = lg.log_ai_interaction(
            model_id="test", provider="test", prompt_hash="h",
        )
        assert entry_id is None

    def test_append_only(self, logger):
        """Verify entries are append-only (no updates)."""
        id1 = logger.log_ai_interaction(model_id="m1", provider="p1", prompt_hash="h1")
        id2 = logger.log_ai_interaction(model_id="m2", provider="p2", prompt_hash="h2")
        assert id1 != id2

        conn = sqlite3.connect(str(logger._db_path))
        count = conn.execute("SELECT COUNT(*) FROM ai_telemetry").fetchone()[0]
        conn.close()
        assert count == 2


class TestHashText:
    def test_hash_returns_sha256(self):
        h = AITelemetryLogger.hash_text("hello")
        assert len(h) == 64  # SHA-256 hex

    def test_hash_empty(self):
        h = AITelemetryLogger.hash_text("")
        assert h == ""

    def test_hash_deterministic(self):
        h1 = AITelemetryLogger.hash_text("test")
        h2 = AITelemetryLogger.hash_text("test")
        assert h1 == h2


class TestUsageSummary:
    def test_summary_empty(self, logger):
        result = logger.get_usage_summary()
        assert result["total_requests"] == 0

    def test_summary_with_data(self, logger):
        logger.log_ai_interaction(
            model_id="claude-opus-4-6", provider="bedrock",
            prompt_hash="h1", input_tokens=100, output_tokens=50,
            cost_usd=0.01, project_id="proj-1",
        )
        logger.log_ai_interaction(
            model_id="gpt-4o", provider="azure_openai",
            prompt_hash="h2", input_tokens=200, output_tokens=100,
            cost_usd=0.02, project_id="proj-1",
        )
        result = logger.get_usage_summary(project_id="proj-1", hours=1)
        assert result["total_requests"] == 2
        assert result["total_cost_usd"] == 0.03
        assert "bedrock" in result["by_provider"]
        assert "azure_openai" in result["by_provider"]

    def test_summary_filters_by_project(self, logger):
        logger.log_ai_interaction(model_id="m1", provider="p1", prompt_hash="h1", project_id="proj-1")
        logger.log_ai_interaction(model_id="m2", provider="p2", prompt_hash="h2", project_id="proj-2")
        result = logger.get_usage_summary(project_id="proj-1")
        assert result["total_requests"] == 1


class TestAnomalyDetection:
    def test_no_anomalies_empty(self, logger):
        anomalies = logger.detect_anomalies()
        assert len(anomalies) == 0

    def test_injection_anomaly(self, logger):
        conn = sqlite3.connect(str(logger._db_path))
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO ai_telemetry (id, model_id, provider, prompt_hash, injection_scan_result, logged_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("t1", "m1", "p1", "h1", "blocked", now),
        )
        conn.commit()
        conn.close()

        anomalies = logger.detect_anomalies(window_hours=1)
        assert any(a["type"] == "injection_attempts" for a in anomalies)
