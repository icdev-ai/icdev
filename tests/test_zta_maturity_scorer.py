# [TEMPLATE: CUI // SP-CTI]
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.devsecops.zta_maturity_scorer — ZTA 7-pillar maturity scoring."""

import json
from unittest.mock import patch

import pytest

# Translating wrapper — zta_maturity_scorer authors %s for PostgreSQL.
from _sql_compat import connect as _tconnect

from tools.devsecops.zta_maturity_scorer import (
    PILLARS,
    _generate_recommendation,
    _load_config,
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


# ---------------------------------------------------------------------------
# Test config
#
# Weights and maturity bands are pinned so scoring is deterministic, but the
# NIST 800-53 control lists and evidence types are taken from the shipped
# args/zta_config.yaml. An earlier revision of this file replaced them with
# {} — which made _gather_pillar_evidence() skip the nist_controls branch
# entirely, so every assertion below passed against empty control traceability.
# ---------------------------------------------------------------------------
_SHIPPED_PILLARS = (_load_config().get("pillars") or {})

TEST_CONFIG = {
    "pillars": {
        p: {
            "weight": 1.0 / len(PILLARS),
            "nist_800_53_controls": list(
                _SHIPPED_PILLARS.get(p, {}).get("nist_800_53_controls", [])
            ),
            "evidence_types": list(
                _SHIPPED_PILLARS.get(p, {}).get("evidence_types", [])
            ),
        }
        for p in PILLARS
    },
    "maturity_levels": {
        "traditional": {"score_range": [0.0, 0.33]},
        "advanced": {"score_range": [0.34, 0.66]},
        "optimal": {"score_range": [0.67, 1.0]},
    },
}

# Pillar used by the traceability assertions, with two of its controls seeded
# as implemented so the nist_controls check is non-empty and non-zero.
TRACED_PILLAR = "network"
TRACED_CONTROLS = TEST_CONFIG["pillars"][TRACED_PILLAR]["nist_800_53_controls"]
IMPLEMENTED_CONTROLS = TRACED_CONTROLS[:2]


@pytest.fixture
def zta_db(tmp_path):
    """Temporary database with ZTA-related tables and seed project."""
    db_path = tmp_path / "icdev.db"
    conn = _tconnect(db_path)
    conn.executescript(ZTA_SCHEMA)
    conn.execute(
        "INSERT INTO projects (id, name, type, classification, status, directory_path) VALUES (%s, %s, %s, %s, %s, %s)",
        (PROJECT_ID, "ZTA Test", "webapp", "CUI", "active", "/tmp/zta"),
    )
    for control_id in TRACED_CONTROLS:
        conn.execute(
            "INSERT INTO project_controls (project_id, control_id, status) VALUES (%s, %s, %s)",
            (
                PROJECT_ID,
                control_id,
                "implemented" if control_id in IMPLEMENTED_CONTROLS else "planned",
            ),
        )
    conn.commit()
    conn.close()
    return db_path


def _patch_db(db_path):
    """Route the scorer's connections at the temp DB.

    Patching DB_PATH does nothing: zta_maturity_scorer imports get_connection
    from tools.db.storage and never reads DB_PATH for queries, so the earlier
    fixture ran every test against the real data/icdev.db. The scorer opens
    and closes a connection per call, so hand out a fresh one each time.
    """
    return patch(
        "tools.devsecops.zta_maturity_scorer.get_connection",
        lambda *a, **k: _tconnect(db_path),
    )


def _patch_config():
    """Patch _load_config to return the deterministic test config."""
    return patch(
        "tools.devsecops.zta_maturity_scorer._load_config",
        return_value=TEST_CONFIG,
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
        with _patch_db(zta_db), _patch_config():
            result = score_pillar(PROJECT_ID, "nonexistent_pillar")
        assert "error" in result
        assert "valid_pillars" in result

    def test_valid_pillar_returns_score(self, zta_db):
        with _patch_db(zta_db), _patch_config():
            result = score_pillar(PROJECT_ID, "network")
        assert "error" not in result
        assert "score" in result
        assert "maturity_level" in result
        assert "pillar" in result
        assert result["pillar"] == "network"

    def test_score_is_between_0_and_1(self, zta_db):
        with _patch_db(zta_db), _patch_config():
            result = score_pillar(PROJECT_ID, "user_identity")
        assert 0.0 <= result["score"] <= 1.0

    def test_score_persisted_to_db(self, zta_db):
        with _patch_db(zta_db), _patch_config():
            score_pillar(PROJECT_ID, "data")
        conn = _tconnect(zta_db)
        row = conn.execute(
            "SELECT * FROM zta_maturity_scores WHERE project_id = %s AND pillar = %s",
            (PROJECT_ID, "data"),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["pillar"] == "data"
        assert row["score"] is not None


# ---------------------------------------------------------------------------
# TestControlTraceability
# ---------------------------------------------------------------------------


class TestControlTraceability:
    """NIST 800-53 control evidence must be present, not silently empty.

    _gather_pillar_evidence() emits a nist_controls check only when the pillar
    config carries nist_800_53_controls. Drop them and the scorer still returns
    a score — computed from posture evidence alone — and every other assertion
    in this file still passes against zero control traceability. These tests
    pin the traceability itself.
    """

    def test_config_defines_controls_for_every_pillar(self):
        missing = [p for p in PILLARS if not TEST_CONFIG["pillars"][p]["nist_800_53_controls"]]
        assert not missing, f"pillars with no NIST 800-53 controls: {missing}"

    def test_score_pillar_reports_nist_control_evidence(self, zta_db):
        with _patch_db(zta_db), _patch_config():
            result = score_pillar(PROJECT_ID, TRACED_PILLAR)
        checks = [c for c in result["evidence"] if c["type"] == "nist_controls"]
        assert len(checks) == 1, f"no nist_controls evidence in {result['evidence']}"
        check = checks[0]
        assert check["total"] == len(TRACED_CONTROLS)
        assert check["implemented"] == len(IMPLEMENTED_CONTROLS)
        assert check["score"] > 0.0

    def test_persisted_evidence_retains_control_refs(self, zta_db):
        with _patch_db(zta_db), _patch_config():
            score_pillar(PROJECT_ID, TRACED_PILLAR)
        conn = _tconnect(zta_db)
        row = conn.execute(
            "SELECT evidence FROM zta_maturity_scores WHERE project_id = %s AND pillar = %s",
            (PROJECT_ID, TRACED_PILLAR),
        ).fetchone()
        conn.close()
        stored = json.loads(row["evidence"])
        nist = [c for c in stored if c["type"] == "nist_controls"]
        assert nist, f"persisted evidence carries no control traceability: {stored}"
        assert nist[0]["total"] == len(TRACED_CONTROLS)
        assert nist[0]["implemented"] == len(IMPLEMENTED_CONTROLS)

    def test_posture_evidence_check_is_emitted(self, zta_db):
        """The evidence_types branch runs too — it was dead under the old config."""
        with _patch_db(zta_db), _patch_config():
            result = score_pillar(PROJECT_ID, TRACED_PILLAR)
        expected = TEST_CONFIG["pillars"][TRACED_PILLAR]["evidence_types"]
        assert expected, f"pillar {TRACED_PILLAR} declares no evidence types"
        posture = [c for c in result["evidence"] if c["type"] == "posture_evidence"]
        assert len(posture) == 1
        assert posture[0]["total"] == len(expected)


# ---------------------------------------------------------------------------
# TestScoreAllPillars
# ---------------------------------------------------------------------------


class TestScoreAllPillars:
    """score_all_pillars: score all 7 pillars and compute weighted aggregate."""

    def test_returns_all_seven_pillars(self, zta_db):
        with _patch_db(zta_db), _patch_config():
            result = score_all_pillars(PROJECT_ID)
        assert "pillar_scores" in result
        assert len(result["pillar_scores"]) == 7
        for p in PILLARS:
            assert p in result["pillar_scores"]

    def test_overall_score_present(self, zta_db):
        with _patch_db(zta_db), _patch_config():
            result = score_all_pillars(PROJECT_ID)
        assert "overall_score" in result
        assert 0.0 <= result["overall_score"] <= 1.0

    def test_overall_maturity_present(self, zta_db):
        with _patch_db(zta_db), _patch_config():
            result = score_all_pillars(PROJECT_ID)
        assert result["overall_maturity"] in ("traditional", "advanced", "optimal")

    def test_weakest_pillars_identified(self, zta_db):
        with _patch_db(zta_db), _patch_config():
            result = score_all_pillars(PROJECT_ID)
        assert "weakest_pillars" in result
        assert len(result["weakest_pillars"]) <= 2

    def test_recommendation_included(self, zta_db):
        with _patch_db(zta_db), _patch_config():
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
        with _patch_db(zta_db):
            result = get_trend(PROJECT_ID, days=90)
        assert result["project_id"] == PROJECT_ID
        assert result["data_points"] == 0
        assert result["trends"] == {}

    def test_trend_after_scoring(self, zta_db):
        with _patch_db(zta_db), _patch_config():
            score_pillar(PROJECT_ID, "network")
            score_pillar(PROJECT_ID, "data")
        with _patch_db(zta_db):
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
