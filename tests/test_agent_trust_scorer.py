#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for AgentTrustScorer (Phase 45, Gap 5, D260)."""

import json
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.security.agent_trust_scorer import AgentTrustScorer


@pytest.fixture
def trust_db(tmp_path):
    """Create temp DB with all required tables."""
    db_path = tmp_path / "test_trust.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE agent_trust_scores (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            project_id TEXT,
            trust_score REAL NOT NULL,
            previous_score REAL,
            score_delta REAL,
            factor_json TEXT NOT NULL,
            trigger_event TEXT NOT NULL,
            trigger_event_id TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE agent_vetoes (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            vetoing_agent_id TEXT,
            vetoed_agent_id TEXT,
            veto_type TEXT DEFAULT 'soft',
            domain TEXT,
            action TEXT,
            rationale TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE tool_chain_events (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            agent_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            tool_sequence_json TEXT NOT NULL,
            rule_matched TEXT,
            severity TEXT DEFAULT 'info',
            action TEXT DEFAULT 'allow',
            context_json TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE agent_output_violations (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            agent_id TEXT NOT NULL,
            tool_name TEXT,
            violation_type TEXT NOT NULL,
            severity TEXT DEFAULT 'medium',
            details_json TEXT,
            output_hash TEXT,
            action_taken TEXT DEFAULT 'logged',
            classification TEXT DEFAULT 'CUI',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def default_config():
    """Default trust scoring config."""
    return {
        "enabled": True,
        "initial_score": 0.85,
        "min_score": 0.0,
        "max_score": 1.0,
        "decay_factors": {
            "veto_hard": -0.15,
            "veto_soft": -0.05,
            "anomaly_detection": -0.10,
            "tool_chain_violation": -0.12,
            "output_violation_critical": -0.15,
            "output_violation_high": -0.08,
            "output_violation_medium": -0.03,
        },
        "recovery": {
            "clean_check_bonus": 0.02,
            "max_recovery_per_day": 0.10,
            "recovery_check_interval_hours": 1,
        },
        "thresholds": {
            "untrusted": 0.30,
            "degraded": 0.50,
            "normal": 0.70,
        },
    }


def _insert_veto(db_path, agent_id, veto_type="hard", hours_ago=1):
    conn = sqlite3.connect(str(db_path))
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    conn.execute(
        "INSERT INTO agent_vetoes (id, vetoed_agent_id, veto_type, created_at) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), agent_id, veto_type, ts),
    )
    conn.commit()
    conn.close()


def _insert_chain_violation(db_path, agent_id, severity="critical", hours_ago=1):
    conn = sqlite3.connect(str(db_path))
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    conn.execute(
        "INSERT INTO tool_chain_events (id, agent_id, session_id, tool_name, tool_sequence_json, severity, created_at) "
        "VALUES (?, ?, 'sess-1', 'test_tool', '[]', ?, ?)",
        (str(uuid.uuid4()), agent_id, severity, ts),
    )
    conn.commit()
    conn.close()


def _insert_output_violation(db_path, agent_id, severity="critical", hours_ago=1):
    conn = sqlite3.connect(str(db_path))
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    conn.execute(
        "INSERT INTO agent_output_violations (id, agent_id, violation_type, severity, created_at) "
        "VALUES (?, ?, 'test_violation', ?, ?)",
        (str(uuid.uuid4()), agent_id, severity, ts),
    )
    conn.commit()
    conn.close()


class TestAgentTrustScorer:
    """Tests for AgentTrustScorer."""

    def test_initial_score_no_events(self, trust_db, default_config):
        scorer = AgentTrustScorer(db_path=trust_db, config=default_config)
        result = scorer.compute_score("agent-1")
        assert result["trust_score"] == 0.85
        assert result["trust_level"] == "normal"

    def test_hard_veto_decay(self, trust_db, default_config):
        _insert_veto(trust_db, "agent-1", veto_type="hard")
        scorer = AgentTrustScorer(db_path=trust_db, config=default_config)
        result = scorer.compute_score("agent-1")
        assert result["trust_score"] < 0.85
        assert "veto_hard" in result["factors"]
        assert result["factors"]["veto_hard"]["count"] == 1

    def test_soft_veto_smaller_decay(self, trust_db, default_config):
        _insert_veto(trust_db, "agent-2", veto_type="soft")
        scorer = AgentTrustScorer(db_path=trust_db, config=default_config)
        result = scorer.compute_score("agent-2")
        assert result["trust_score"] > 0.75  # soft decay is only -0.05

    def test_chain_violation_decay(self, trust_db, default_config):
        _insert_chain_violation(trust_db, "agent-3", severity="critical")
        scorer = AgentTrustScorer(db_path=trust_db, config=default_config)
        result = scorer.compute_score("agent-3")
        assert result["trust_score"] < 0.85
        assert "tool_chain_violation" in result["factors"]

    def test_output_violation_critical_decay(self, trust_db, default_config):
        _insert_output_violation(trust_db, "agent-4", severity="critical")
        scorer = AgentTrustScorer(db_path=trust_db, config=default_config)
        result = scorer.compute_score("agent-4")
        assert result["trust_score"] == pytest.approx(0.85 - 0.15, abs=0.01)

    def test_output_violation_medium_smaller_decay(self, trust_db, default_config):
        _insert_output_violation(trust_db, "agent-5", severity="medium")
        scorer = AgentTrustScorer(db_path=trust_db, config=default_config)
        result = scorer.compute_score("agent-5")
        assert result["trust_score"] == pytest.approx(0.85 - 0.03, abs=0.01)

    def test_multiple_violations_cumulative(self, trust_db, default_config):
        _insert_veto(trust_db, "agent-6", veto_type="hard")
        _insert_chain_violation(trust_db, "agent-6")
        _insert_output_violation(trust_db, "agent-6")
        scorer = AgentTrustScorer(db_path=trust_db, config=default_config)
        result = scorer.compute_score("agent-6")
        # hard veto -0.15, chain -0.12, output critical -0.15 = -0.42
        assert result["trust_score"] < 0.50

    def test_score_clamped_at_min(self, trust_db, default_config):
        # Insert many violations to push below 0
        for _ in range(10):
            _insert_veto(trust_db, "agent-7", veto_type="hard")
        scorer = AgentTrustScorer(db_path=trust_db, config=default_config)
        result = scorer.compute_score("agent-7")
        assert result["trust_score"] >= 0.0

    def test_trust_level_normal(self, trust_db, default_config):
        scorer = AgentTrustScorer(db_path=trust_db, config=default_config)
        assert scorer.get_trust_level(0.85) == "normal"
        assert scorer.get_trust_level(0.70) == "normal"

    def test_trust_level_degraded(self, trust_db, default_config):
        scorer = AgentTrustScorer(db_path=trust_db, config=default_config)
        assert scorer.get_trust_level(0.60) == "degraded"
        assert scorer.get_trust_level(0.50) == "degraded"

    def test_trust_level_untrusted(self, trust_db, default_config):
        scorer = AgentTrustScorer(db_path=trust_db, config=default_config)
        assert scorer.get_trust_level(0.40) == "untrusted"
        assert scorer.get_trust_level(0.30) == "untrusted"

    def test_trust_level_blocked(self, trust_db, default_config):
        scorer = AgentTrustScorer(db_path=trust_db, config=default_config)
        assert scorer.get_trust_level(0.20) == "blocked"
        assert scorer.get_trust_level(0.0) == "blocked"

    def test_evaluate_access_normal_allowed(self, trust_db, default_config):
        scorer = AgentTrustScorer(db_path=trust_db, config=default_config)
        scorer.compute_score("agent-ok")
        result = scorer.evaluate_agent_access("agent-ok")
        assert result["allowed"] is True
        assert result["trust_level"] == "normal"

    def test_evaluate_access_degraded_autonomous_denied(self, trust_db, default_config):
        # Push agent into degraded range
        _insert_veto(trust_db, "agent-deg", veto_type="hard")
        _insert_veto(trust_db, "agent-deg", veto_type="hard")
        scorer = AgentTrustScorer(db_path=trust_db, config=default_config)
        scorer.compute_score("agent-deg")
        result = scorer.evaluate_agent_access("agent-deg", action_type="autonomous")
        assert result["allowed"] is False
        assert result["trust_level"] == "degraded"

    def test_score_stored_in_db(self, trust_db, default_config):
        scorer = AgentTrustScorer(db_path=trust_db, config=default_config)
        scorer.compute_score("agent-store")
        conn = sqlite3.connect(str(trust_db))
        row = conn.execute(
            "SELECT COUNT(*) FROM agent_trust_scores WHERE agent_id = 'agent-store'"
        ).fetchone()
        conn.close()
        assert row[0] > 0

    def test_get_current_score(self, trust_db, default_config):
        scorer = AgentTrustScorer(db_path=trust_db, config=default_config)
        scorer.compute_score("agent-cur")
        score = scorer.get_current_score("agent-cur")
        assert score is not None
        assert isinstance(score, float)

    def test_get_score_history(self, trust_db, default_config):
        scorer = AgentTrustScorer(db_path=trust_db, config=default_config)
        scorer.compute_score("agent-hist")
        scorer.compute_score("agent-hist")
        history = scorer.get_score_history("agent-hist")
        assert len(history) >= 2

    def test_gate_pass_no_untrusted(self, trust_db, default_config):
        scorer = AgentTrustScorer(db_path=trust_db, config=default_config)
        scorer.compute_score("agent-good")
        result = scorer.evaluate_gate()
        assert result["passed"] is True

    def test_gate_fail_untrusted_agent(self, trust_db, default_config):
        # Create agent with very low score
        for _ in range(5):
            _insert_veto(trust_db, "agent-bad", veto_type="hard")
        scorer = AgentTrustScorer(db_path=trust_db, config=default_config)
        scorer.compute_score("agent-bad")
        result = scorer.evaluate_gate()
        assert result["passed"] is False

    def test_missing_db_returns_initial(self, tmp_path, default_config):
        scorer = AgentTrustScorer(db_path=tmp_path / "nonexistent.db", config=default_config)
        result = scorer.compute_score("agent-1")
        assert result["trust_score"] == 0.85

    def test_old_events_not_counted(self, trust_db, default_config):
        """Events older than window_hours should not affect score."""
        _insert_veto(trust_db, "agent-old", veto_type="hard", hours_ago=48)
        scorer = AgentTrustScorer(db_path=trust_db, config=default_config)
        result = scorer.compute_score("agent-old", window_hours=24)
        assert result["trust_score"] == 0.85  # No decay from old event
