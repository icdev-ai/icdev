# CUI // SP-CTI
"""Tests for tools/monitor/auto_resolver.py — webhook-triggered auto-resolution pipeline."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from tools.monitor.auto_resolver import (
    _check_rate_limit,
    _ensure_table,
    analyze_alert,
    get_resolution_history,
    normalize_alert,
    normalize_generic_alert,
    normalize_prometheus_alert,
    normalize_sentry_alert,
    resolve_alert,
)


# ---------------------------------------------------------------------------
# Sample payloads
# ---------------------------------------------------------------------------
SENTRY_PAYLOAD = {
    "event": {
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": "invalid input",
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "app/main.py",
                                "lineno": 42,
                                "function": "process",
                            }
                        ]
                    },
                }
            ]
        },
        "tags": [
            ["service", "auth-api"],
            ["environment", "production"],
            ["server_name", "web-01"],
        ],
    },
    "project_slug": "auth-service",
    "level": "error",
    "project_id": "proj-sentry-1",
}

PROMETHEUS_PAYLOAD = {
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "HighErrorRate",
                "severity": "critical",
                "instance": "web-01",
                "job": "api-gateway",
                "namespace": "production",
            },
            "annotations": {
                "description": "Error rate above 5%",
                "summary": "API Gateway error rate elevated",
            },
        }
    ]
}

GENERIC_PAYLOAD = {
    "title": "DiskSpaceLow",
    "description": "Disk usage at 95%",
    "service": "storage-node",
    "environment": "staging",
    "severity": "warning",
    "project_id": "proj-generic-1",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _minutes_ago(n: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(minutes=n)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _init_test_db(db_path: Path) -> None:
    """Create the minimal tables that analyze_alert / _match_patterns query."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS knowledge_patterns (
            id TEXT PRIMARY KEY,
            pattern_type TEXT,
            pattern_signature TEXT,
            description TEXT,
            root_cause TEXT,
            remediation TEXT,
            confidence REAL DEFAULT 0.0,
            auto_healable INTEGER DEFAULT 0
        );
    """)
    conn.commit()
    conn.close()


# ===================================================================
# TestEnsureTable
# ===================================================================
class TestEnsureTable:
    def test_creates_table(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        _ensure_table(db)
        conn = sqlite3.connect(str(db))
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        conn.close()
        assert "auto_resolution_log" in tables

    def test_idempotent(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        _ensure_table(db)
        _ensure_table(db)  # no error on second call


# ===================================================================
# TestNormalization
# ===================================================================
class TestNormalization:
    def test_sentry_alert(self) -> None:
        result = normalize_sentry_alert(SENTRY_PAYLOAD)
        assert result["error_type"] == "ValueError"
        assert result["error_message"] == "invalid input"
        assert result["source"] == "sentry"
        assert result["service_name"] == "web-01"
        assert result["environment"] == "production"
        assert result["severity"] == "error"
        assert result["project_id"] == "proj-sentry-1"

    def test_prometheus_alert(self) -> None:
        result = normalize_prometheus_alert(PROMETHEUS_PAYLOAD)
        assert result["error_type"] == "HighErrorRate"
        assert result["severity"] == "critical"
        assert result["source"] == "prometheus"
        assert result["service_name"] == "api-gateway"
        assert "Error rate above 5%" in result["error_message"]

    def test_generic_alert(self) -> None:
        result = normalize_generic_alert(GENERIC_PAYLOAD)
        assert result["error_type"] == "DiskSpaceLow"
        assert result["error_message"] == "Disk usage at 95%"
        assert result["service_name"] == "storage-node"
        assert result["environment"] == "staging"
        assert result["severity"] == "warning"

    def test_normalize_routes_correctly(self) -> None:
        result = normalize_alert(SENTRY_PAYLOAD, source="sentry")
        assert result["source"] == "sentry"
        assert result["error_type"] == "ValueError"
        # Should also add a timestamp
        assert "timestamp" in result

    def test_normalize_prometheus_routes(self) -> None:
        result = normalize_alert(PROMETHEUS_PAYLOAD, source="prometheus")
        assert result["source"] == "prometheus"
        assert result["error_type"] == "HighErrorRate"

    def test_normalize_unknown_source_falls_back(self) -> None:
        payload = {"title": "CustomAlert", "description": "Something happened"}
        result = normalize_alert(payload, source="unknown_system")
        assert result["error_type"] == "CustomAlert"
        assert "timestamp" in result

    def test_sentry_no_exception_values(self) -> None:
        """Sentry payload with empty exception values should not crash."""
        payload = {"event": {"exception": {"values": []}}, "level": "warning"}
        result = normalize_sentry_alert(payload)
        assert result["error_type"] == "UnknownError"
        assert result["source"] == "sentry"

    def test_prometheus_no_alerts(self) -> None:
        """Prometheus payload with empty alerts list should not crash."""
        payload = {"alerts": []}
        result = normalize_prometheus_alert(payload)
        assert result["error_type"] == "UnknownAlert"
        assert result["source"] == "prometheus"

    def test_generic_alert_minimal(self) -> None:
        """Generic alert with empty payload fills in defaults."""
        result = normalize_generic_alert({})
        assert result["error_type"] == "GenericAlert"
        assert result["service_name"] == "unknown"
        assert result["environment"] == "unknown"
        assert result["severity"] == "warning"


# ===================================================================
# TestAnalyzeAlert
# ===================================================================
class TestAnalyzeAlert:
    def test_analyze_returns_expected_keys(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _init_test_db(db)
        result = analyze_alert(GENERIC_PAYLOAD, source="generic", db_path=db)
        assert isinstance(result, dict)
        assert "status" in result
        assert "confidence" in result
        assert "decision" in result
        assert "reason" in result

    def test_generic_alert_low_confidence(self, tmp_path: Path) -> None:
        """A generic alert with no matching patterns should yield low confidence."""
        db = tmp_path / "icdev.db"
        _init_test_db(db)
        result = analyze_alert(GENERIC_PAYLOAD, source="generic", db_path=db)
        assert result["confidence"] <= 0.3
        assert result["decision"] == "escalate"

    def test_decision_values_valid(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _init_test_db(db)
        result = analyze_alert(SENTRY_PAYLOAD, source="sentry", db_path=db)
        assert result["decision"] in ("auto_fix", "suggest", "escalate")

    def test_analyze_sentry_returns_normalized(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _init_test_db(db)
        result = analyze_alert(SENTRY_PAYLOAD, source="sentry", db_path=db)
        assert "alert_normalized" in result
        assert result["alert_normalized"]["error_type"] == "ValueError"

    def test_analyze_prometheus_returns_features(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _init_test_db(db)
        result = analyze_alert(PROMETHEUS_PAYLOAD, source="prometheus", db_path=db)
        assert "features" in result
        features = result["features"]
        assert "error_type" in features
        assert "signature" in features


# ===================================================================
# TestResolveAlert
# ===================================================================
class TestResolveAlert:
    def test_resolve_dry_run(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _init_test_db(db)
        result = resolve_alert(
            GENERIC_PAYLOAD, source="generic", dry_run=True, db_path=db
        )
        assert isinstance(result, dict)
        assert "resolution_id" in result
        assert result["dry_run"] is True
        # Should not have a PR URL since it is dry_run or escalated
        assert result.get("pr_url") is None

    def test_resolve_records_in_db(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _init_test_db(db)
        result = resolve_alert(
            GENERIC_PAYLOAD, source="generic", dry_run=False, db_path=db
        )
        rid = result["resolution_id"]
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM auto_resolution_log WHERE id = ?", (rid,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["alert_source"] == "generic"
        assert row["decision"] in ("auto_fix", "suggest", "escalate")

    def test_resolve_escalation(self, tmp_path: Path) -> None:
        """Low-confidence generic alert should result in escalation."""
        db = tmp_path / "icdev.db"
        _init_test_db(db)
        result = resolve_alert(
            GENERIC_PAYLOAD, source="generic", dry_run=False, db_path=db
        )
        # No matching patterns => escalated
        assert result["resolution_status"] in ("escalated", "suggested")

    def test_resolve_sentry_has_analysis(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _init_test_db(db)
        result = resolve_alert(
            SENTRY_PAYLOAD, source="sentry", dry_run=True, db_path=db
        )
        assert "analysis" in result
        analysis = result["analysis"]
        assert "decision" in analysis
        assert "confidence" in analysis


# ===================================================================
# TestRateLimit
# ===================================================================
class TestRateLimit:
    def test_under_limit(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _ensure_table(db)
        config = {"max_auto_fixes_per_hour": 5}
        assert _check_rate_limit(config, db_path=db) is True

    def test_over_limit(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _ensure_table(db)
        conn = sqlite3.connect(str(db))
        for i in range(6):
            conn.execute(
                "INSERT INTO auto_resolution_log "
                "(id, alert_source, alert_type, alert_payload, confidence, "
                "decision, resolution_status, created_at) "
                "VALUES (?, 'test', 'TestError', '{}', 0.9, 'auto_fix', "
                "'completed', datetime('now'))",
                (f"rate-{i}",),
            )
        conn.commit()
        conn.close()
        config = {"max_auto_fixes_per_hour": 5}
        assert _check_rate_limit(config, db_path=db) is False

    def test_old_entries_dont_count(self, tmp_path: Path) -> None:
        """Entries older than 1 hour should not count toward the rate limit."""
        db = tmp_path / "icdev.db"
        _ensure_table(db)
        conn = sqlite3.connect(str(db))
        for i in range(6):
            conn.execute(
                "INSERT INTO auto_resolution_log "
                "(id, alert_source, alert_type, alert_payload, confidence, "
                "decision, resolution_status, created_at) "
                "VALUES (?, 'test', 'TestError', '{}', 0.9, 'auto_fix', "
                "'completed', datetime('now', '-2 hours'))",
                (f"old-{i}",),
            )
        conn.commit()
        conn.close()
        config = {"max_auto_fixes_per_hour": 5}
        assert _check_rate_limit(config, db_path=db) is True


# ===================================================================
# TestResolutionHistory
# ===================================================================
class TestResolutionHistory:
    def test_empty_history(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        history = get_resolution_history(db_path=db)
        assert history == []

    def test_returns_entries(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _ensure_table(db)
        conn = sqlite3.connect(str(db))
        for i in range(3):
            conn.execute(
                "INSERT INTO auto_resolution_log "
                "(id, alert_source, alert_type, alert_payload, confidence, "
                "decision, resolution_status, created_at) "
                "VALUES (?, 'sentry', 'ValueError', ?, 0.5, 'suggest', "
                "'suggested', ?)",
                (
                    f"hist-{i}",
                    json.dumps({"event": {}}),
                    _minutes_ago(i * 10),
                ),
            )
        conn.commit()
        conn.close()
        history = get_resolution_history(db_path=db)
        assert len(history) == 3
        # Should be in descending order by created_at
        assert history[0]["id"] == "hist-0"  # most recent
        assert history[-1]["id"] == "hist-2"  # oldest

    def test_filter_by_project_id(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _ensure_table(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO auto_resolution_log "
            "(id, alert_source, alert_type, alert_payload, project_id, "
            "confidence, decision, resolution_status) "
            "VALUES ('p1', 'generic', 'Err', '{}', 'proj-A', 0.1, 'escalate', 'escalated')"
        )
        conn.execute(
            "INSERT INTO auto_resolution_log "
            "(id, alert_source, alert_type, alert_payload, project_id, "
            "confidence, decision, resolution_status) "
            "VALUES ('p2', 'generic', 'Err', '{}', 'proj-B', 0.1, 'escalate', 'escalated')"
        )
        conn.commit()
        conn.close()
        history = get_resolution_history(project_id="proj-A", db_path=db)
        assert len(history) == 1
        assert history[0]["project_id"] == "proj-A"

    def test_limit_parameter(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _ensure_table(db)
        conn = sqlite3.connect(str(db))
        for i in range(10):
            conn.execute(
                "INSERT INTO auto_resolution_log "
                "(id, alert_source, alert_type, alert_payload, confidence, "
                "decision, resolution_status) "
                "VALUES (?, 'generic', 'Err', '{}', 0.1, 'escalate', 'escalated')",
                (f"lim-{i}",),
            )
        conn.commit()
        conn.close()
        history = get_resolution_history(limit=3, db_path=db)
        assert len(history) == 3

    def test_json_fields_deserialized(self, tmp_path: Path) -> None:
        """alert_payload and details JSON columns should be deserialized."""
        db = tmp_path / "icdev.db"
        _ensure_table(db)
        payload = {"event": {"exception": {}}}
        details = {"confidence": 0.5, "reason": "test"}
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO auto_resolution_log "
            "(id, alert_source, alert_type, alert_payload, confidence, "
            "decision, resolution_status, details) "
            "VALUES ('json-1', 'sentry', 'ValueError', ?, 0.5, 'suggest', "
            "'suggested', ?)",
            (json.dumps(payload), json.dumps(details)),
        )
        conn.commit()
        conn.close()
        history = get_resolution_history(db_path=db)
        assert len(history) == 1
        entry = history[0]
        assert isinstance(entry["alert_payload"], dict)
        assert isinstance(entry["details"], dict)
        assert entry["details"]["reason"] == "test"
