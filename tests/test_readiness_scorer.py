# [TEMPLATE: CUI // SP-CTI]
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import sqlite3
import uuid

import pytest

from tools.requirements.readiness_scorer import (
    _load_weights,
    score_readiness,
    get_score_trend,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_test_db(db_path):
    """Create minimal schema required by readiness_scorer in a temp DB."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS intake_sessions (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            customer_name TEXT NOT NULL,
            customer_org TEXT,
            session_status TEXT DEFAULT 'active',
            classification TEXT DEFAULT 'CUI',
            impact_level TEXT DEFAULT 'IL5',
            readiness_score REAL DEFAULT 0.0,
            readiness_breakdown TEXT,
            gap_count INTEGER DEFAULT 0,
            ambiguity_count INTEGER DEFAULT 0,
            total_requirements INTEGER DEFAULT 0,
            decomposed_count INTEGER DEFAULT 0,
            context_summary TEXT,
            source_documents TEXT,
            resumed_from TEXT,
            created_by TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS intake_requirements (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            project_id TEXT,
            source_turn INTEGER,
            raw_text TEXT NOT NULL,
            refined_text TEXT,
            requirement_type TEXT DEFAULT 'functional',
            priority TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'draft',
            gaps TEXT,
            ambiguities TEXT,
            acceptance_criteria TEXT,
            source_document TEXT,
            source_section TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS intake_conversation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            turn_number INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            content_type TEXT DEFAULT 'text',
            extracted_requirements TEXT,
            metadata TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS readiness_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            turn_number INTEGER,
            overall_score REAL NOT NULL,
            completeness REAL NOT NULL,
            clarity REAL NOT NULL,
            feasibility REAL NOT NULL,
            compliance REAL NOT NULL,
            testability REAL NOT NULL,
            gap_count INTEGER DEFAULT 0,
            ambiguity_count INTEGER DEFAULT 0,
            requirement_count INTEGER DEFAULT 0,
            scored_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()


def _create_session(db_path, session_id, context_summary=None, ambiguity_count=0,
                    gap_count=0):
    """Insert a minimal intake_sessions row."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO intake_sessions (id, customer_name, context_summary, "
        "ambiguity_count, gap_count) VALUES (?, ?, ?, ?, ?)",
        (session_id, "Test Customer", context_summary, ambiguity_count, gap_count),
    )
    conn.commit()
    conn.close()


def _add_requirement(db_path, session_id, req_type="functional", raw_text="Some requirement",
                     acceptance_criteria=None):
    """Insert a requirement row for a session."""
    req_id = f"req-{uuid.uuid4().hex[:8]}"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO intake_requirements (id, session_id, requirement_type, "
        "raw_text, acceptance_criteria) VALUES (?, ?, ?, ?, ?)",
        (req_id, session_id, req_type, raw_text, acceptance_criteria),
    )
    conn.commit()
    conn.close()
    return req_id


def _add_conversation_turn(db_path, session_id, turn_number, role="customer",
                           content="Some input"):
    """Insert a conversation turn."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO intake_conversation (session_id, turn_number, role, content) "
        "VALUES (?, ?, ?, ?)",
        (session_id, turn_number, role, content),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScoreReadinessCallable:
    """Verify the main scoring function exists and is callable."""

    def test_score_readiness_is_callable(self):
        assert callable(score_readiness)

    def test_get_score_trend_is_callable(self):
        assert callable(get_score_trend)

    def test_load_weights_is_callable(self):
        assert callable(_load_weights)


class TestScoreReadinessMinimal:
    """Test scoring with minimal session data."""

    def test_minimal_session_returns_score_between_0_and_1(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_test_db(db_path)
        sid = "sess-minimal"
        _create_session(db_path, sid)
        _add_requirement(db_path, sid, req_type="functional", raw_text="basic req")

        result = score_readiness(sid, db_path=db_path)
        assert 0.0 <= result["overall_score"] <= 1.0

    def test_scoring_returns_dict_with_expected_keys(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_test_db(db_path)
        sid = "sess-keys"
        _create_session(db_path, sid)
        _add_requirement(db_path, sid)

        result = score_readiness(sid, db_path=db_path)

        expected_keys = {
            "status", "session_id", "overall_score", "dimensions",
            "requirement_count", "types_present", "types_missing",
            "recommendation", "threshold",
        }
        assert expected_keys.issubset(set(result.keys()))

    def test_dimensions_contain_five_keys(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_test_db(db_path)
        sid = "sess-dims"
        _create_session(db_path, sid)
        _add_requirement(db_path, sid)

        result = score_readiness(sid, db_path=db_path)
        dims = result["dimensions"]
        assert set(dims.keys()) == {
            "completeness", "clarity", "feasibility", "compliance", "testability",
        }
        for dim_data in dims.values():
            assert "score" in dim_data
            assert "weight" in dim_data


class TestScoreReadinessComparison:
    """Complete data should yield a higher score than incomplete."""

    def test_complete_data_scores_higher(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_test_db(db_path)

        # Incomplete session -- single requirement, no acceptance criteria
        sid_low = "sess-low"
        _create_session(db_path, sid_low)
        _add_requirement(db_path, sid_low, req_type="functional", raw_text="one thing")
        result_low = score_readiness(sid_low, db_path=db_path)

        # Complete session -- diverse types, acceptance criteria, budget/team/timeline
        sid_high = "sess-high"
        ctx = json.dumps({"selected_frameworks": ["fedramp_moderate"]})
        _create_session(db_path, sid_high, context_summary=ctx)
        for rtype in ("functional", "security", "interface", "data", "performance", "compliance"):
            _add_requirement(
                db_path, sid_high, req_type=rtype,
                raw_text=f"{rtype} with timeline budget team",
                acceptance_criteria="Given X, When Y, Then Z",
            )
        # Add several conversation turns to boost clarity
        for i in range(1, 6):
            _add_conversation_turn(db_path, sid_high, turn_number=i)

        result_high = score_readiness(sid_high, db_path=db_path)

        assert result_high["overall_score"] > result_low["overall_score"]


class TestWeights:
    """Weights should have defaults and sum close to 1.0."""

    def test_default_weights_exist(self):
        weights = _load_weights()
        for dim in ("completeness", "clarity", "feasibility", "compliance", "testability"):
            assert dim in weights

    def test_default_weights_sum_to_one(self):
        weights = _load_weights()
        total = sum(weights.values())
        assert abs(total - 1.0) < 0.01


class TestEdgeCases:
    """Edge cases: missing session, empty requirements."""

    def test_missing_session_raises_value_error(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_test_db(db_path)

        with pytest.raises(ValueError, match="not found"):
            score_readiness("nonexistent-session", db_path=db_path)

    def test_missing_database_raises_file_not_found(self, tmp_path):
        db_path = tmp_path / "does_not_exist.db"
        with pytest.raises(FileNotFoundError):
            score_readiness("any-session", db_path=db_path)


class TestRecommendation:
    """Recommendation string reflects score ranges."""

    def test_low_score_gives_critical_gaps_or_gather_more(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_test_db(db_path)
        sid = "sess-rec"
        _create_session(db_path, sid)
        _add_requirement(db_path, sid, req_type="functional", raw_text="one")

        result = score_readiness(sid, db_path=db_path)
        # With minimal data the score should be low
        assert result["recommendation"] in ("critical_gaps", "gather_more")


class TestScoreTrend:
    """get_score_trend returns trend data."""

    def test_trend_returns_data_points(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_test_db(db_path)
        sid = "sess-trend"
        _create_session(db_path, sid)
        _add_requirement(db_path, sid)

        # Score twice to build history
        score_readiness(sid, db_path=db_path)
        _add_requirement(db_path, sid, req_type="security", raw_text="security req")
        score_readiness(sid, db_path=db_path)

        trend = get_score_trend(sid, db_path=db_path)
        assert trend["status"] == "ok"
        assert trend["data_points"] >= 2
        assert isinstance(trend["trend"], list)
