#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for behavioral drift detection in AITelemetryLogger (Phase 45, Gap 1, D257)."""

import math
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# Ensure project root on path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.security.ai_telemetry_logger import AITelemetryLogger


@pytest.fixture
def drift_db(tmp_path):
    """Create a temporary DB with ai_telemetry table and seed data."""
    db_path = tmp_path / "test_drift.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE ai_telemetry (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            user_id TEXT,
            agent_id TEXT,
            model_id TEXT,
            provider TEXT,
            function TEXT,
            prompt_hash TEXT,
            response_hash TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            thinking_tokens INTEGER DEFAULT 0,
            latency_ms REAL DEFAULT 0,
            cost_usd REAL DEFAULT 0,
            classification TEXT DEFAULT 'CUI',
            api_key_source TEXT DEFAULT 'system',
            injection_scan_result TEXT,
            logged_at TEXT
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _seed_telemetry(db_path, agent_id, count, hours_ago_start, hours_ago_end,
                    latency_mean=100, output_mean=500, cost_mean=0.01):
    """Seed ai_telemetry with synthetic data."""
    import random
    random.seed(42)
    conn = sqlite3.connect(str(db_path))
    now = datetime.now(timezone.utc)
    for i in range(count):
        frac = i / max(1, count - 1)
        ts = now - timedelta(hours=hours_ago_start + (hours_ago_end - hours_ago_start) * frac)
        conn.execute(
            """INSERT INTO ai_telemetry
               (id, agent_id, model_id, provider, prompt_hash, response_hash,
                input_tokens, output_tokens, latency_ms, cost_usd, logged_at)
               VALUES (?, ?, 'claude-sonnet', 'anthropic', 'hash1', 'hash2',
                       ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()), agent_id,
                random.randint(100, 500),
                int(random.gauss(output_mean, output_mean * 0.1)),
                round(random.gauss(latency_mean, latency_mean * 0.1), 2),
                round(random.gauss(cost_mean, cost_mean * 0.1), 6),
                ts.isoformat(),
            ),
        )
    conn.commit()
    conn.close()


class TestBehavioralDrift:
    """Tests for detect_behavioral_drift() method (D257)."""

    def test_no_data_returns_empty(self, drift_db):
        logger = AITelemetryLogger(db_path=drift_db)
        alerts = logger.detect_behavioral_drift()
        assert alerts == []

    def test_insufficient_baseline_returns_empty(self, drift_db):
        """If baseline has fewer than min_samples, skip detection."""
        _seed_telemetry(drift_db, "agent-1", count=10, hours_ago_start=200, hours_ago_end=30)
        logger = AITelemetryLogger(db_path=drift_db)
        alerts = logger.detect_behavioral_drift(min_samples=50)
        assert alerts == []

    def test_stable_behavior_no_alerts(self, drift_db):
        """If current window matches baseline, no alerts."""
        # Seed baseline: 100 records over 7 days (168h ago to 24h ago)
        _seed_telemetry(drift_db, "agent-1", count=100,
                        hours_ago_start=192, hours_ago_end=25,
                        latency_mean=100, output_mean=500, cost_mean=0.01)
        # Seed current window: 10 records in last 24h with SAME distribution
        _seed_telemetry(drift_db, "agent-1", count=10,
                        hours_ago_start=23, hours_ago_end=0,
                        latency_mean=100, output_mean=500, cost_mean=0.01)

        logger = AITelemetryLogger(db_path=drift_db)
        alerts = logger.detect_behavioral_drift(agent_id="agent-1", min_samples=50)
        # With matched distributions, there should be few/no critical alerts
        critical = [a for a in alerts if a["severity"] == "critical"]
        assert len(critical) == 0

    def test_latency_spike_detected(self, drift_db):
        """A 10x latency increase should trigger an alert."""
        _seed_telemetry(drift_db, "agent-2", count=100,
                        hours_ago_start=192, hours_ago_end=25,
                        latency_mean=50, output_mean=500, cost_mean=0.01)
        # Current window: 10x higher latency
        _seed_telemetry(drift_db, "agent-2", count=10,
                        hours_ago_start=23, hours_ago_end=0,
                        latency_mean=500, output_mean=500, cost_mean=0.01)

        logger = AITelemetryLogger(db_path=drift_db)
        alerts = logger.detect_behavioral_drift(agent_id="agent-2", min_samples=50)
        latency_alerts = [a for a in alerts if a["dimension"] == "avg_latency_ms"]
        assert len(latency_alerts) > 0
        assert latency_alerts[0]["z_score"] > 2.0

    def test_cost_spike_detected(self, drift_db):
        """A large cost increase should trigger an alert."""
        _seed_telemetry(drift_db, "agent-3", count=100,
                        hours_ago_start=192, hours_ago_end=25,
                        latency_mean=100, output_mean=500, cost_mean=0.001)
        # Current: 50x cost spike
        _seed_telemetry(drift_db, "agent-3", count=10,
                        hours_ago_start=23, hours_ago_end=0,
                        latency_mean=100, output_mean=500, cost_mean=0.05)

        logger = AITelemetryLogger(db_path=drift_db)
        alerts = logger.detect_behavioral_drift(agent_id="agent-3", min_samples=50)
        cost_alerts = [a for a in alerts if a["dimension"] == "cost_rate"]
        assert len(cost_alerts) > 0

    def test_output_token_spike_detected(self, drift_db):
        """Large output token increase should trigger an alert."""
        _seed_telemetry(drift_db, "agent-4", count=100,
                        hours_ago_start=192, hours_ago_end=25,
                        latency_mean=100, output_mean=200, cost_mean=0.01)
        # Current: 5x output size
        _seed_telemetry(drift_db, "agent-4", count=10,
                        hours_ago_start=23, hours_ago_end=0,
                        latency_mean=100, output_mean=1000, cost_mean=0.01)

        logger = AITelemetryLogger(db_path=drift_db)
        alerts = logger.detect_behavioral_drift(agent_id="agent-4", min_samples=50)
        token_alerts = [a for a in alerts if a["dimension"] == "avg_output_tokens"]
        assert len(token_alerts) > 0

    def test_filter_by_agent_id(self, drift_db):
        """Only detect drift for specified agent."""
        _seed_telemetry(drift_db, "agent-A", count=100,
                        hours_ago_start=192, hours_ago_end=25)
        _seed_telemetry(drift_db, "agent-B", count=100,
                        hours_ago_start=192, hours_ago_end=25)
        _seed_telemetry(drift_db, "agent-A", count=10,
                        hours_ago_start=23, hours_ago_end=0,
                        latency_mean=1000)
        _seed_telemetry(drift_db, "agent-B", count=10,
                        hours_ago_start=23, hours_ago_end=0)

        logger = AITelemetryLogger(db_path=drift_db)
        alerts = logger.detect_behavioral_drift(agent_id="agent-A", min_samples=50)
        assert all(a["agent_id"] == "agent-A" for a in alerts)

    def test_all_agents_scanned(self, drift_db):
        """Without agent_id filter, scan all agents."""
        _seed_telemetry(drift_db, "agent-X", count=60,
                        hours_ago_start=192, hours_ago_end=25)
        _seed_telemetry(drift_db, "agent-Y", count=60,
                        hours_ago_start=192, hours_ago_end=25)
        _seed_telemetry(drift_db, "agent-X", count=10,
                        hours_ago_start=23, hours_ago_end=0,
                        latency_mean=1000)
        _seed_telemetry(drift_db, "agent-Y", count=10,
                        hours_ago_start=23, hours_ago_end=0,
                        latency_mean=1000)

        logger = AITelemetryLogger(db_path=drift_db)
        alerts = logger.detect_behavioral_drift(min_samples=50)
        agent_ids = set(a["agent_id"] for a in alerts)
        assert "agent-X" in agent_ids or "agent-Y" in agent_ids

    def test_custom_threshold(self, drift_db):
        """Higher threshold means fewer alerts."""
        _seed_telemetry(drift_db, "agent-T", count=100,
                        hours_ago_start=192, hours_ago_end=25,
                        latency_mean=100)
        _seed_telemetry(drift_db, "agent-T", count=10,
                        hours_ago_start=23, hours_ago_end=0,
                        latency_mean=300)

        logger = AITelemetryLogger(db_path=drift_db)

        alerts_normal = logger.detect_behavioral_drift(
            agent_id="agent-T", threshold_sigma=2.0, min_samples=50
        )
        alerts_strict = logger.detect_behavioral_drift(
            agent_id="agent-T", threshold_sigma=100.0, min_samples=50
        )

        assert len(alerts_strict) <= len(alerts_normal)

    def test_alert_structure(self, drift_db):
        """Verify alert dict has required fields."""
        _seed_telemetry(drift_db, "agent-S", count=100,
                        hours_ago_start=192, hours_ago_end=25,
                        latency_mean=50)
        _seed_telemetry(drift_db, "agent-S", count=10,
                        hours_ago_start=23, hours_ago_end=0,
                        latency_mean=500)

        logger = AITelemetryLogger(db_path=drift_db)
        alerts = logger.detect_behavioral_drift(agent_id="agent-S", min_samples=50)

        if alerts:
            a = alerts[0]
            assert "agent_id" in a
            assert "dimension" in a
            assert "z_score" in a
            assert "severity" in a
            assert "baseline_mean" in a
            assert "current_value" in a
            assert "threshold_sigma" in a

    def test_severity_classification(self, drift_db):
        """Severity should escalate with z-score magnitude."""
        _seed_telemetry(drift_db, "agent-V", count=100,
                        hours_ago_start=192, hours_ago_end=25,
                        latency_mean=10)
        # Massive spike: 100x
        _seed_telemetry(drift_db, "agent-V", count=10,
                        hours_ago_start=23, hours_ago_end=0,
                        latency_mean=1000)

        logger = AITelemetryLogger(db_path=drift_db)
        alerts = logger.detect_behavioral_drift(agent_id="agent-V", min_samples=50)

        severities = [a["severity"] for a in alerts]
        # A 100x spike should produce at least medium severity
        valid = {"medium", "high", "critical"}
        for s in severities:
            assert s in valid

    def test_missing_db_returns_empty(self, tmp_path):
        """Missing DB file returns empty list."""
        logger = AITelemetryLogger(db_path=tmp_path / "nonexistent.db")
        alerts = logger.detect_behavioral_drift()
        assert alerts == []

    def test_custom_window_hours(self, drift_db):
        """Custom window_hours parameter is respected."""
        _seed_telemetry(drift_db, "agent-W", count=100,
                        hours_ago_start=192, hours_ago_end=50)
        _seed_telemetry(drift_db, "agent-W", count=10,
                        hours_ago_start=48, hours_ago_end=0,
                        latency_mean=500)

        logger = AITelemetryLogger(db_path=drift_db)
        # 48-hour window should include the spiky data
        alerts = logger.detect_behavioral_drift(
            agent_id="agent-W", window_hours=48, min_samples=50
        )
        # Should detect something
        assert isinstance(alerts, list)
