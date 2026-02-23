# [TEMPLATE: CUI // SP-CTI]
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.devsecops.zta_maturity_scorer — ZTA 7-pillar maturity scoring."""

import json
import sqlite3
from unittest.mock import patch

import pytest

from tools.devsecops.zta_maturity_scorer import (
    PILLARS,
    _generate_recommendation,
    _score_to_maturity,
    get_trend,
    score_all_pillars,
    score_pillar,
)

PROJECT_ID = "proj-zta-001"

# ---------------------------------------------------------------------------
# Schema required by the scorer
# ---------------------------------------------------------------------------
ZTA_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'webapp',
    classification TEXT NOT NULL DEFAULT 'CUI',
    status TEXT NOT NULL DEFAULT 'active',
    directory_path TEXT NOT NULL DEFAULT '/tmp',
    impact_level TEXT DEFAULT 'IL5',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS project_controls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    control_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned',
    UNIQUE(project_id, control_id)
);

CREATE TABLE IF NOT EXISTS zta_posture_evidence (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    evidence_data TEXT,
    status TEXT DEFAULT 'not_collected',
    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS devsecops_profiles (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    maturity_level TEXT,
    active_stages TEXT,
    stage_configs TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS zta_maturity_scores (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    pillar TEXT NOT NULL,
    score REAL,
    maturity_level TEXT,
    evidence TEXT,
    assessed_by TEXT DEFAULT 'icdev-devsecops-agent',
    created_at TEXT
);
"""


@pytest.fixture
def zta_db(tmp_path):
    """Temporary database with ZTA-related tables and seed project."""
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(ZTA_SCHEMA)
    conn.execute(
        "INSERT INTO projects (id, name, type, classification, status, directory_path) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (PROJECT_ID, "ZTA Test", "webapp", "CUI", "active", "/tmp/zta"),
    )
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Fallback config used when YAML is unavailable or missing
# ---------------------------------------------------------------------------
FALLBACK_CONFIG = {
    "pillars": {p: {"weight": 1.0 / len(PILLARS)} for p in PILLARS},
    "maturity_levels": {
        "traditional": {"score_range": [0.0, 0.33]},
        "advanced": {"score_range": [0.34, 0.66]},
        "optimal": {"score_range": [0.67, 1.0]},
    },
}


def _patch_config():
    """Patch _load_config to return the deterministic fallback config."""
    return patch(
        "tools.devsecops.zta_maturity_scorer._load_config",
        return_value=FALLBACK_CONFIG,
    )


# ---------------------------------------------------------------------------
# TestScoreToMaturity
# ---------------------------------------------------------------------------

class TestScoreToMaturity:
    """_score_to_maturity: maps a 0.0-1.0 score to a maturity level string."""

    def test_zero_is_traditional(self):
        with _patch_config():
            assert _score_to_maturity(0.0) == "traditional"

    def test_low_score_is_traditional(self):
        with _patch_config():
            assert _score_to_maturity(0.2) == "traditional"

    def test_boundary_033_is_traditional(self):
        with _patch_config():
            assert _score_to_maturity(0.33) == "traditional"

    def test_mid_score_is_advanced(self):
        with _patch_config():
            assert _score_to_maturity(0.5) == "advanced"

    def test_boundary_066_is_advanced(self):
        with _patch_config():
            assert _score_to_maturity(0.66) == "advanced"

    def test_high_score_is_optimal(self):
        with _patch_config():
            assert _score_to_maturity(0.9) == "optimal"

    def test_perfect_score_is_optimal(self):
        with _patch_config():
            assert _score_to_maturity(1.0) == "optimal"


# ---------------------------------------------------------------------------
# TestScorePillar
# ---------------------------------------------------------------------------

class TestScorePillar:
    """score_pillar: score a single ZTA pillar and persist to DB."""

    def test_invalid_pillar_returns_error(self, zta_db):
        with patch("tools.devsecops.zta_maturity_scorer.DB_PATH", zta_db), _patch_config():
            result = score_pillar(PROJECT_ID, "nonexistent_pillar")
        assert "error" in result
        assert "valid_pillars" in result

    def test_valid_pillar_returns_score(self, zta_db):
        with patch("tools.devsecops.zta_maturity_scorer.DB_PATH", zta_db), _patch_config():
            result = score_pillar(PROJECT_ID, "network")
        assert "error" not in result
        assert "score" in result
        assert "maturity_level" in result
        assert "pillar" in result
        assert result["pillar"] == "network"

    def test_score_is_between_0_and_1(self, zta_db):
        with patch("tools.devsecops.zta_maturity_scorer.DB_PATH", zta_db), _patch_config():
            result = score_pillar(PROJECT_ID, "user_identity")
        assert 0.0 <= result["score"] <= 1.0

    def test_score_persisted_to_db(self, zta_db):
        with patch("tools.devsecops.zta_maturity_scorer.DB_PATH", zta_db), _patch_config():
            score_pillar(PROJECT_ID, "data")
        conn = sqlite3.connect(str(zta_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM zta_maturity_scores WHERE project_id = ? AND pillar = ?",
            (PROJECT_ID, "data"),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["pillar"] == "data"
        assert row["score"] is not None


# ---------------------------------------------------------------------------
# TestScoreAllPillars
# ---------------------------------------------------------------------------

class TestScoreAllPillars:
    """score_all_pillars: score all 7 pillars and compute weighted aggregate."""

    def test_returns_all_seven_pillars(self, zta_db):
        with patch("tools.devsecops.zta_maturity_scorer.DB_PATH", zta_db), _patch_config():
            result = score_all_pillars(PROJECT_ID)
        assert "pillar_scores" in result
        assert len(result["pillar_scores"]) == 7
        for p in PILLARS:
            assert p in result["pillar_scores"]

    def test_overall_score_present(self, zta_db):
        with patch("tools.devsecops.zta_maturity_scorer.DB_PATH", zta_db), _patch_config():
            result = score_all_pillars(PROJECT_ID)
        assert "overall_score" in result
        assert 0.0 <= result["overall_score"] <= 1.0

    def test_overall_maturity_present(self, zta_db):
        with patch("tools.devsecops.zta_maturity_scorer.DB_PATH", zta_db), _patch_config():
            result = score_all_pillars(PROJECT_ID)
        assert result["overall_maturity"] in ("traditional", "advanced", "optimal")

    def test_weakest_pillars_identified(self, zta_db):
        with patch("tools.devsecops.zta_maturity_scorer.DB_PATH", zta_db), _patch_config():
            result = score_all_pillars(PROJECT_ID)
        assert "weakest_pillars" in result
        assert len(result["weakest_pillars"]) <= 2

    def test_recommendation_included(self, zta_db):
        with patch("tools.devsecops.zta_maturity_scorer.DB_PATH", zta_db), _patch_config():
            result = score_all_pillars(PROJECT_ID)
        assert "recommendation" in result
        assert isinstance(result["recommendation"], str)
        assert len(result["recommendation"]) > 0


# ---------------------------------------------------------------------------
# TestGetTrend
# ---------------------------------------------------------------------------

class TestGetTrend:
    """get_trend: retrieve historical ZTA maturity scores."""

    def test_empty_trend(self, zta_db):
        with patch("tools.devsecops.zta_maturity_scorer.DB_PATH", zta_db):
            result = get_trend(PROJECT_ID, days=90)
        assert result["project_id"] == PROJECT_ID
        assert result["data_points"] == 0
        assert result["trends"] == {}

    def test_trend_after_scoring(self, zta_db):
        with patch("tools.devsecops.zta_maturity_scorer.DB_PATH", zta_db), _patch_config():
            score_pillar(PROJECT_ID, "network")
            score_pillar(PROJECT_ID, "data")
        with patch("tools.devsecops.zta_maturity_scorer.DB_PATH", zta_db):
            result = get_trend(PROJECT_ID, days=90)
        assert result["data_points"] >= 2
        assert "network" in result["trends"]
        assert "data" in result["trends"]


# ---------------------------------------------------------------------------
# TestRecommendations
# ---------------------------------------------------------------------------

class TestRecommendations:
    """_generate_recommendation: produce improvement guidance."""

    def test_optimal_recommendation(self):
        result = _generate_recommendation("optimal", [])
        assert "optimal" in result.lower()
        assert "maintain" in result.lower()

    def test_advanced_targets_optimal(self):
        weakest = [{"pillar": "network", "score": 0.4}]
        result = _generate_recommendation("advanced", weakest)
        assert "optimal" in result.lower()
        assert "Network" in result

    def test_traditional_targets_advanced(self):
        weakest = [
            {"pillar": "user_identity", "score": 0.1},
            {"pillar": "device", "score": 0.15},
        ]
        result = _generate_recommendation("traditional", weakest)
        assert "advanced" in result.lower()
        assert "User Identity" in result
        assert "Device" in result


# [TEMPLATE: CUI // SP-CTI]
