# CUI // SP-CTI
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.knowledge.pattern_detector — failure pattern detection,
feature extraction, similarity scoring, frequency anomaly detection, and
full project analysis."""

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from tools.knowledge.pattern_detector import (
    _similarity,
    analyze_project,
    detect_frequency_anomaly,
    extract_features,
    match_known_pattern,
)


# ---------------------------------------------------------------------------
# Helpers — extra schema for pattern_detector tables
# ---------------------------------------------------------------------------

PATTERN_DETECTOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_type TEXT NOT NULL,
    pattern_signature TEXT,
    description TEXT,
    root_cause TEXT,
    remediation TEXT,
    confidence REAL DEFAULT 0.5,
    auto_healable INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS failure_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT,
    source TEXT,
    stack_trace TEXT,
    context TEXT,
    resolved INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS deployments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    environment TEXT,
    version TEXT,
    status TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);
"""


@pytest.fixture
def pattern_db(icdev_db):
    """Extend the icdev_db fixture with pattern_detector-specific tables."""
    conn = sqlite3.connect(str(icdev_db))
    conn.row_factory = sqlite3.Row
    conn.executescript(PATTERN_DETECTOR_SCHEMA)
    conn.close()
    return icdev_db


def _seed_patterns(db_path, patterns):
    """Insert rows into knowledge_patterns."""
    conn = sqlite3.connect(str(db_path))
    for p in patterns:
        conn.execute(
            "INSERT INTO knowledge_patterns "
            "(pattern_type, pattern_signature, description, root_cause, remediation, confidence, auto_healable) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            p,
        )
    conn.commit()
    conn.close()


def _seed_failures(db_path, failures):
    """Insert rows into failure_log."""
    conn = sqlite3.connect(str(db_path))
    for f in failures:
        conn.execute(
            "INSERT INTO failure_log "
            "(project_id, error_type, error_message, source, stack_trace, context, resolved, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            f,
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# TestExtractFeatures
# ---------------------------------------------------------------------------

class TestExtractFeatures:
    """extract_features: pure dict-to-dict feature extraction."""

    def test_minimal_input_returns_defaults(self):
        features = extract_features({})
        assert features["error_type"] == "unknown"
        assert features["service_name"] == "unknown"
        assert features["time_of_day"] == -1
        assert features["day_of_week"] == -1
        assert features["is_business_hours"] == -1
        assert features["message_length"] == 0
        assert features["has_stack_trace"] == 0
        assert "signature" in features

    def test_timestamp_extracts_time_features(self):
        # Wednesday 2025-06-11 at 14:30 is business hours
        features = extract_features({
            "timestamp": "2025-06-11T14:30:00",
        })
        assert features["time_of_day"] == 14
        assert features["day_of_week"] == 2  # Wednesday
        assert features["is_business_hours"] == 1

    def test_weekend_timestamp_not_business_hours(self):
        # Saturday 2025-06-14 at 10:00
        features = extract_features({
            "timestamp": "2025-06-14T10:00:00",
        })
        assert features["is_business_hours"] == 0

    def test_message_keyword_flags(self):
        features = extract_features({
            "error_message": "Connection timed out to database pool",
        })
        assert features["has_timeout"] == 1
        assert features["has_connection"] == 1
        assert features["has_database"] == 1
        assert features["has_memory"] == 0
        assert features["has_permission"] == 0

    def test_stack_trace_features(self):
        stack = "Traceback:\n  File a.py\n  File b.py\n  File c.py"
        features = extract_features({"stack_trace": stack})
        assert features["has_stack_trace"] == 1
        assert features["stack_depth"] == 3

    def test_context_as_json_string(self):
        features = extract_features({
            "context": '{"environment": "staging", "recent_deployment": true}',
        })
        assert features["environment"] == "staging"
        assert features["recent_deployment"] == 1

    def test_signature_built_from_features(self):
        features = extract_features({
            "error_type": "TimeoutError",
            "service_name": "api-gw",
            "error_message": "connection timeout to postgres database",
        })
        sig = features["signature"]
        assert "TimeoutError" in sig
        assert "api-gw" in sig
        assert "timeout" in sig
        assert "connection" in sig
        assert "database" in sig


# ---------------------------------------------------------------------------
# TestSimilarity
# ---------------------------------------------------------------------------

class TestSimilarity:
    """_similarity: Jaccard character-trigram similarity."""

    def test_identical_strings_return_one(self):
        assert _similarity("hello", "hello") == 1.0

    def test_empty_string_returns_zero(self):
        assert _similarity("", "hello") == 0.0
        assert _similarity("hello", "") == 0.0

    def test_completely_different_returns_low(self):
        score = _similarity("aaaaaa", "zzzzzz")
        assert score < 0.1

    def test_similar_strings_return_high(self):
        score = _similarity("timeout|api-gw", "timeout|api-gateway")
        assert score > 0.5


# ---------------------------------------------------------------------------
# TestMatchKnownPattern
# ---------------------------------------------------------------------------

class TestMatchKnownPattern:
    """match_known_pattern: match features against knowledge_patterns table."""

    def test_no_patterns_returns_empty(self, pattern_db):
        features = extract_features({"error_type": "ValueError"})
        matches = match_known_pattern(features, db_path=pattern_db)
        assert matches == []

    def test_matching_pattern_returned(self, pattern_db):
        _seed_patterns(pattern_db, [
            ("timeout", "TimeoutError|api-gw|timeout",
             "API gateway timeout on upstream service",
             "Upstream service overloaded", "Scale upstream replicas",
             0.9, 1),
        ])
        features = extract_features({
            "error_type": "TimeoutError",
            "service_name": "api-gw",
            "error_message": "Request timed out after 30s",
        })
        matches = match_known_pattern(features, db_path=pattern_db)
        assert len(matches) >= 1
        top = matches[0]
        assert top["pattern_type"] == "timeout"
        assert top["auto_healable"] is True
        assert top["combined_score"] > 0.1

    def test_low_similarity_filtered_out(self, pattern_db):
        _seed_patterns(pattern_db, [
            ("disk", "DiskError|storage|disk",
             "Disk space exhaustion on node",
             "Log rotation disabled", "Enable log rotation",
             0.3, 0),
        ])
        # Features that are completely unrelated to disk errors
        features = extract_features({
            "error_type": "ImportError",
            "service_name": "auth-svc",
            "error_message": "No module named foo",
        })
        matches = match_known_pattern(features, db_path=pattern_db)
        # Either empty or all scores below threshold
        for m in matches:
            assert m["combined_score"] > 0.1  # only kept if above threshold


# ---------------------------------------------------------------------------
# TestDetectFrequencyAnomaly
# ---------------------------------------------------------------------------

class TestDetectFrequencyAnomaly:
    """detect_frequency_anomaly: find repeated error types in a time window."""

    def test_no_failures_returns_empty(self, pattern_db):
        anomalies = detect_frequency_anomaly(
            "proj-test-001", window_hours=1, threshold=3, db_path=pattern_db,
        )
        assert anomalies == []

    def test_below_threshold_returns_empty(self, pattern_db):
        now = datetime.now(timezone.utc).isoformat()
        _seed_failures(pattern_db, [
            ("proj-test-001", "ValueError", "bad value", "svc", "", "{}", 0, now),
            ("proj-test-001", "ValueError", "bad value", "svc", "", "{}", 0, now),
        ])
        anomalies = detect_frequency_anomaly(
            "proj-test-001", window_hours=1, threshold=3, db_path=pattern_db,
        )
        assert anomalies == []

    def test_above_threshold_detected(self, pattern_db):
        now = datetime.now(timezone.utc).isoformat()
        failures = [
            ("proj-test-001", "TimeoutError", "timed out", "api", "", "{}", 0, now)
            for _ in range(5)
        ]
        _seed_failures(pattern_db, failures)
        anomalies = detect_frequency_anomaly(
            "proj-test-001", window_hours=1, threshold=3, db_path=pattern_db,
        )
        assert len(anomalies) == 1
        assert anomalies[0]["error_type"] == "TimeoutError"
        assert anomalies[0]["count"] == 5
        assert anomalies[0]["severity"] in ("warning", "high", "critical")

    def test_severity_critical_for_high_count(self, pattern_db):
        now = datetime.now(timezone.utc).isoformat()
        # threshold=3, critical when count > threshold * 3 = 9
        failures = [
            ("proj-test-001", "OOMError", "out of memory", "worker", "", "{}", 0, now)
            for _ in range(12)
        ]
        _seed_failures(pattern_db, failures)
        anomalies = detect_frequency_anomaly(
            "proj-test-001", window_hours=1, threshold=3, db_path=pattern_db,
        )
        assert len(anomalies) == 1
        assert anomalies[0]["severity"] == "critical"


# ---------------------------------------------------------------------------
# TestAnalyzeProject
# ---------------------------------------------------------------------------

class TestAnalyzeProject:
    """analyze_project: full pattern analysis pipeline."""

    def test_empty_project_returns_zero_summary(self, pattern_db):
        result = analyze_project("proj-test-001", db_path=pattern_db)
        assert result["project_id"] == "proj-test-001"
        assert "analyzed_at" in result
        assert result["summary"]["total_anomalies"] == 0
        assert result["summary"]["deployment_related"] == 0
        assert result["summary"]["pattern_matches_found"] == 0
        assert result["summary"]["auto_healable"] == 0

    def test_unresolved_failures_matched_against_patterns(self, pattern_db):
        _seed_patterns(pattern_db, [
            ("timeout", "TimeoutError|api-gw|timeout",
             "API gateway timeout",
             "Upstream overload", "Scale replicas",
             0.9, 1),
        ])
        now = datetime.now(timezone.utc).isoformat()
        _seed_failures(pattern_db, [
            ("proj-test-001", "TimeoutError", "Request timed out",
             "api-gw", "", "{}", 0, now),
        ])
        result = analyze_project("proj-test-001", db_path=pattern_db)
        assert result["summary"]["pattern_matches_found"] >= 1
        match = result["pattern_matches"][0]
        assert match["error_type"] == "TimeoutError"
        assert "top_match" in match


# CUI // SP-CTI
