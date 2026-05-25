# CUI // SP-CTI
"""Seed-based unit tests for the RCA (Root Cause Analysis) diagnostics pipeline.

Seeds well-known failure patterns into ``knowledge_patterns`` and validates
that ``_extract_features()``, ``_match_patterns()``, and ``analyze_alert()``
produce correct classifications and decisions.

Covers:
  - Feature extraction: OOM, timeout, connection, disk, auth, database keywords
  - Pattern matching: seeded patterns returned with positive confidence
  - Decision logic: auto_fix (high confidence + auto_healable),
                    suggest (mid confidence), escalate (no match)
  - Matched pattern payload shape
  - Suggestion payload present for suggest decisions
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.monitor.auto_resolver import (
    _extract_features,
    _match_patterns,
    analyze_alert,
    normalize_generic_alert,
)


# ---------------------------------------------------------------------------
# Seed data — 5 canonical RCA patterns
# ---------------------------------------------------------------------------

SEED_PATTERNS = [
    {
        "id": "pat-oom-001",
        "pattern_type": "memory",
        "pattern_signature": "OutOfMemoryError|app-service",
        "description": "JVM heap overflow on app-service",
        "root_cause": "Heap limit too low for current workload",
        "remediation": "Increase JVM -Xmx to 4G and redeploy",
        "confidence": 0.92,
        "auto_healable": 1,
        "occurrence_count": 12,
    },
    {
        "id": "pat-timeout-001",
        "pattern_type": "connectivity",
        "pattern_signature": "TimeoutError|api-gateway",
        "description": "API gateway connection timeout to upstream database",
        "root_cause": "Database connection pool exhausted or slow queries",
        "remediation": "Increase pool_size and add missing query index",
        "confidence": 0.87,
        "auto_healable": 1,
        "occurrence_count": 8,
    },
    {
        "id": "pat-disk-001",
        "pattern_type": "storage",
        "pattern_signature": "DiskSpaceLow|storage-node",
        "description": "Storage node disk usage above 90% threshold",
        "root_cause": "Log accumulation and uncleaned temp files",
        "remediation": "Run log-cleanup cron job and archive old data",
        "confidence": 0.94,
        "auto_healable": 1,
        "occurrence_count": 5,
    },
    {
        "id": "pat-auth-001",
        "pattern_type": "security",
        "pattern_signature": "AuthFailure|auth-service",
        "description": "Auth service 401 errors spike — token expiry or secret rotation",
        "root_cause": "JWT secret misconfigured or expired after rotation",
        "remediation": "Rotate JWT secret and redeploy auth-service",
        "confidence": 0.80,
        "auto_healable": 0,
        "occurrence_count": 3,
    },
    {
        "id": "pat-deadlock-001",
        "pattern_type": "database",
        "pattern_signature": "DatabaseDeadlock|payments-db",
        "description": "Deadlock detected in payments service database",
        "root_cause": "Concurrent transactions with conflicting row locks",
        "remediation": "Add retry logic with exponential backoff; reduce transaction scope",
        "confidence": 0.78,
        "auto_healable": 0,
        "occurrence_count": 7,
    },
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _build_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS knowledge_patterns (
            id TEXT PRIMARY KEY,
            pattern_type TEXT,
            pattern_signature TEXT,
            description TEXT,
            root_cause TEXT,
            remediation TEXT,
            confidence REAL DEFAULT 0.0,
            auto_healable INTEGER DEFAULT 0,
            occurrence_count INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS auto_resolution_log (
            id TEXT PRIMARY KEY,
            alert_source TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            alert_payload TEXT NOT NULL,
            project_id TEXT,
            confidence REAL DEFAULT 0.0,
            decision TEXT NOT NULL,
            resolution_status TEXT NOT NULL DEFAULT 'pending',
            branch_name TEXT,
            pr_url TEXT,
            test_passed BOOLEAN,
            details TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)


def _seed_patterns(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO knowledge_patterns "
        "(id, pattern_type, pattern_signature, description, root_cause, "
        " remediation, confidence, auto_healable, occurrence_count) "
        "VALUES (:id, :pattern_type, :pattern_signature, :description, :root_cause, "
        "        :remediation, :confidence, :auto_healable, :occurrence_count)",
        SEED_PATTERNS,
    )
    conn.commit()


@pytest.fixture()
def seeded_db(tmp_path):
    """Temporary SQLite DB with schema + seed patterns. Patches get_connection."""
    db_file = tmp_path / "rca_seed_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    _build_schema(conn)
    _seed_patterns(conn)
    conn.close()

    def _get_conn(db_path=None, **kw):
        c = sqlite3.connect(str(db_file))
        c.row_factory = sqlite3.Row
        return c

    with patch("tools.monitor.auto_resolver.get_connection", side_effect=_get_conn):
        yield db_file


# ---------------------------------------------------------------------------
# Alert payloads
# ---------------------------------------------------------------------------

OOM_PAYLOAD = {
    "title": "OutOfMemoryError",
    "description": "Java heap space — OOM on app-service pod",
    "service": "app-service",
    "environment": "production",
    "severity": "critical",
}

TIMEOUT_PAYLOAD = {
    "title": "TimeoutError",
    "description": "Connection timed out after 30s to upstream database",
    "service": "api-gateway",
    "environment": "production",
    "severity": "error",
}

DISK_PAYLOAD = {
    "title": "DiskSpaceLow",
    "description": "Disk usage at 93% on storage-node — quota almost reached",
    "service": "storage-node",
    "environment": "staging",
    "severity": "warning",
}

AUTH_PAYLOAD = {
    "title": "AuthFailure",
    "description": "401 authentication failed — permission denied for token",
    "service": "auth-service",
    "environment": "production",
    "severity": "error",
}

DEADLOCK_PAYLOAD = {
    "title": "DatabaseDeadlock",
    "description": "SQL deadlock detected in payments database pool",
    "service": "payments-db",
    "environment": "production",
    "severity": "critical",
}

UNKNOWN_PAYLOAD = {
    "title": "QuantumFluxError",
    "description": "Temporal anomaly in flux capacitor module",
    "service": "flux-capacitor",
    "environment": "dev",
    "severity": "info",
}


# ===========================================================================
# TestFeatureExtraction
# ===========================================================================

class TestFeatureExtraction:
    def test_oom_has_memory_flag(self):
        fd = normalize_generic_alert(OOM_PAYLOAD)
        features = _extract_features(fd)
        assert features["has_memory"] == 1

    def test_timeout_has_timeout_flag(self):
        fd = normalize_generic_alert(TIMEOUT_PAYLOAD)
        features = _extract_features(fd)
        assert features["has_timeout"] == 1

    def test_timeout_has_connection_flag(self):
        fd = normalize_generic_alert(TIMEOUT_PAYLOAD)
        features = _extract_features(fd)
        assert features["has_connection"] == 1

    def test_disk_has_disk_flag(self):
        fd = normalize_generic_alert(DISK_PAYLOAD)
        features = _extract_features(fd)
        assert features["has_disk"] == 1

    def test_auth_has_permission_flag(self):
        fd = normalize_generic_alert(AUTH_PAYLOAD)
        features = _extract_features(fd)
        assert features["has_permission"] == 1

    def test_deadlock_has_database_flag(self):
        fd = normalize_generic_alert(DEADLOCK_PAYLOAD)
        features = _extract_features(fd)
        assert features["has_database"] == 1

    def test_error_type_preserved(self):
        fd = normalize_generic_alert(OOM_PAYLOAD)
        features = _extract_features(fd)
        assert features["error_type"] == "OutOfMemoryError"

    def test_signature_contains_error_type_and_service(self):
        fd = normalize_generic_alert(OOM_PAYLOAD)
        features = _extract_features(fd)
        assert features["signature"].startswith("OutOfMemoryError|app-service")

    def test_error_type_in_features(self):
        fd = normalize_generic_alert(OOM_PAYLOAD)
        features = _extract_features(fd)
        assert features["error_type"] == "OutOfMemoryError"

    def test_service_name_in_features(self):
        fd = normalize_generic_alert(OOM_PAYLOAD)
        features = _extract_features(fd)
        assert features["service_name"] == "app-service"

    def test_unknown_no_special_flags(self):
        fd = normalize_generic_alert(UNKNOWN_PAYLOAD)
        features = _extract_features(fd)
        assert features["has_memory"] == 0
        assert features["has_timeout"] == 0
        assert features["has_disk"] == 0
        assert features["has_database"] == 0


# ===========================================================================
# TestPatternMatching
# ===========================================================================

class TestPatternMatching:
    def test_oom_matches_seeded_pattern(self, seeded_db):
        fd = normalize_generic_alert(OOM_PAYLOAD)
        features = _extract_features(fd)
        confidence, match = _match_patterns(features, seeded_db)
        assert confidence > 0.0
        assert match is not None

    def test_oom_match_identifies_memory_type(self, seeded_db):
        fd = normalize_generic_alert(OOM_PAYLOAD)
        features = _extract_features(fd)
        _, match = _match_patterns(features, seeded_db)
        assert match["pattern_type"] == "memory"

    def test_timeout_matches_connectivity_pattern(self, seeded_db):
        fd = normalize_generic_alert(TIMEOUT_PAYLOAD)
        features = _extract_features(fd)
        _, match = _match_patterns(features, seeded_db)
        assert match is not None
        assert match["pattern_type"] == "connectivity"

    def test_disk_matches_storage_pattern(self, seeded_db):
        fd = normalize_generic_alert(DISK_PAYLOAD)
        features = _extract_features(fd)
        _, match = _match_patterns(features, seeded_db)
        assert match is not None
        assert match["pattern_type"] == "storage"

    def test_auth_matches_security_pattern(self, seeded_db):
        fd = normalize_generic_alert(AUTH_PAYLOAD)
        features = _extract_features(fd)
        _, match = _match_patterns(features, seeded_db)
        assert match is not None
        assert match["pattern_type"] == "security"

    def test_deadlock_matches_database_pattern(self, seeded_db):
        fd = normalize_generic_alert(DEADLOCK_PAYLOAD)
        features = _extract_features(fd)
        _, match = _match_patterns(features, seeded_db)
        assert match is not None
        assert match["pattern_type"] == "database"

    def test_unknown_alert_returns_no_match(self, seeded_db):
        fd = normalize_generic_alert(UNKNOWN_PAYLOAD)
        features = _extract_features(fd)
        confidence, match = _match_patterns(features, seeded_db)
        # Either no match or confidence below actionable threshold
        assert match is None or confidence < 0.3

    def test_match_has_root_cause(self, seeded_db):
        fd = normalize_generic_alert(OOM_PAYLOAD)
        features = _extract_features(fd)
        _, match = _match_patterns(features, seeded_db)
        assert match["root_cause"] != ""

    def test_match_has_remediation(self, seeded_db):
        fd = normalize_generic_alert(OOM_PAYLOAD)
        features = _extract_features(fd)
        _, match = _match_patterns(features, seeded_db)
        assert match["remediation"] != ""

    def test_match_combined_score_in_range(self, seeded_db):
        fd = normalize_generic_alert(OOM_PAYLOAD)
        features = _extract_features(fd)
        confidence, match = _match_patterns(features, seeded_db)
        assert 0.0 < confidence <= 1.0


# ===========================================================================
# TestAnalyzeAlert — decision routing
# ===========================================================================

class TestAnalyzeAlert:
    def test_oom_auto_fix_decision(self, seeded_db):
        """OOM pattern is auto_healable with high confidence → auto_fix."""
        result = analyze_alert(OOM_PAYLOAD, source="generic", db_path=seeded_db)
        assert result["status"] == "ok"
        assert result["decision"] == "auto_fix"

    def test_disk_auto_fix_decision(self, seeded_db):
        """Disk pattern is auto_healable with highest confidence → auto_fix."""
        result = analyze_alert(DISK_PAYLOAD, source="generic", db_path=seeded_db)
        assert result["decision"] == "auto_fix"

    def test_timeout_auto_fix_decision(self, seeded_db):
        result = analyze_alert(TIMEOUT_PAYLOAD, source="generic", db_path=seeded_db)
        assert result["decision"] == "auto_fix"

    def test_auth_not_auto_fix(self, seeded_db):
        """Auth pattern is not auto_healable → suggest or escalate, never auto_fix."""
        result = analyze_alert(AUTH_PAYLOAD, source="generic", db_path=seeded_db)
        assert result["decision"] in ("suggest", "escalate")

    def test_deadlock_not_auto_fix(self, seeded_db):
        """Deadlock pattern is not auto_healable → suggest or escalate."""
        result = analyze_alert(DEADLOCK_PAYLOAD, source="generic", db_path=seeded_db)
        assert result["decision"] in ("suggest", "escalate")

    def test_unknown_escalates(self, seeded_db):
        """No matching pattern → escalate."""
        result = analyze_alert(UNKNOWN_PAYLOAD, source="generic", db_path=seeded_db)
        assert result["decision"] == "escalate"

    def test_result_has_confidence(self, seeded_db):
        result = analyze_alert(OOM_PAYLOAD, source="generic", db_path=seeded_db)
        assert "confidence" in result
        assert isinstance(result["confidence"], float)

    def test_result_has_features(self, seeded_db):
        result = analyze_alert(OOM_PAYLOAD, source="generic", db_path=seeded_db)
        assert "features" in result
        assert result["features"]["has_memory"] == 1

    def test_result_has_alert_normalized(self, seeded_db):
        result = analyze_alert(OOM_PAYLOAD, source="generic", db_path=seeded_db)
        assert "alert_normalized" in result
        assert result["alert_normalized"]["error_type"] == "OutOfMemoryError"

    def test_auto_fix_result_has_matched_pattern(self, seeded_db):
        result = analyze_alert(OOM_PAYLOAD, source="generic", db_path=seeded_db)
        assert "matched_pattern" in result
        assert result["matched_pattern"]["pattern_type"] == "memory"

    def test_suggest_result_has_suggestion_block(self, seeded_db):
        """For suggest decisions, a suggestion dict with root_cause and remediation must be present."""
        result = analyze_alert(AUTH_PAYLOAD, source="generic", db_path=seeded_db)
        if result["decision"] == "suggest":
            assert "suggestion" in result
            assert "root_cause" in result["suggestion"]
            assert "remediation" in result["suggestion"]

    def test_escalate_has_reason(self, seeded_db):
        result = analyze_alert(UNKNOWN_PAYLOAD, source="generic", db_path=seeded_db)
        assert "reason" in result
        assert result["reason"] != ""

    def test_prometheus_source_normalisation(self, seeded_db):
        payload = {
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "OutOfMemoryError",
                        "severity": "critical",
                        "job": "app-service",
                    },
                    "annotations": {
                        "description": "JVM heap OOM — memory limit exceeded",
                    },
                }
            ]
        }
        result = analyze_alert(payload, source="prometheus", db_path=seeded_db)
        assert result["status"] == "ok"
        assert result["alert_normalized"]["source"] == "prometheus"

    def test_sentry_source_normalisation(self, seeded_db):
        payload = {
            "event": {
                "exception": {
                    "values": [
                        {
                            "type": "OutOfMemoryError",
                            "value": "Java heap space",
                            "stacktrace": {"frames": []},
                        }
                    ]
                },
                "tags": [["service", "app-service"], ["environment", "production"]],
            },
            "level": "error",
            "project_id": "proj-java-1",
        }
        result = analyze_alert(payload, source="sentry", db_path=seeded_db)
        assert result["status"] == "ok"
        assert result["alert_normalized"]["source"] == "sentry"
        assert result["alert_normalized"]["error_type"] == "OutOfMemoryError"
