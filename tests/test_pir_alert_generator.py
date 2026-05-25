# CUI // SP-CTI
"""Unit tests for PIR alert generator baseline comparison logic.

Verifies that ``evaluate_indicator_and_alert``:
- generates a PIR alert when the score exceeds the baseline,
- returns ``None`` for the alert when the score is within bounds,
- returns ``None`` when no baseline exists (unbounded),
- maps severity bands to the correct collection priority.
"""
import pytest

from tools.db.storage import get_connection
from tools.intelligence.pir_alert_generator import evaluate_indicator_and_alert
from tools.threat_analysis.service import create_baseline


@pytest.fixture
def db_conn():
    """Yield a fresh connection with both required tables guaranteed to exist."""
    conn = get_connection()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS indicator_baselines (
            id TEXT PRIMARY KEY,
            indicator_name TEXT NOT NULL,
            indicator_category TEXT DEFAULT 'general',
            scope TEXT NOT NULL DEFAULT 'project'
                CHECK(scope IN ('global', 'platform', 'tenant', 'project', 'user')),
            scope_id TEXT,
            threshold_score REAL NOT NULL,
            severity_band TEXT DEFAULT 'medium'
                CHECK(severity_band IN ('low', 'medium', 'high', 'critical')),
            operator_id TEXT NOT NULL,
            rationale TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sg_pir_requirements (
            id                  TEXT PRIMARY KEY,
            pir_type            TEXT NOT NULL DEFAULT 'PIR'
                                    CHECK(pir_type IN ('PIR','CCIR','EEI')),
            topic               TEXT NOT NULL,
            description         TEXT,
            collection_priority INTEGER NOT NULL DEFAULT 3
                                    CHECK(collection_priority BETWEEN 1 AND 5),
            status              TEXT NOT NULL DEFAULT 'active'
                                    CHECK(status IN ('active','satisfied','cancelled')),
            tasked_to           TEXT,
            due_by              TEXT,
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL
        );
        """
    )
    conn.execute("DELETE FROM indicator_baselines")
    conn.execute("DELETE FROM sg_pir_requirements")
    conn.commit()
    yield conn
    conn.execute("DELETE FROM indicator_baselines")
    conn.execute("DELETE FROM sg_pir_requirements")
    conn.commit()
    conn.close()


class TestPirAlertGenerator:
    def test_exceeded_score_generates_alert(self, db_conn):
        """A score above the threshold must produce a PIR alert."""
        indicator = "network_anomaly"
        operator_id = "op-test-001"
        scope = "project"
        scope_id = "proj-test-001"
        threshold = 30.0
        severity_band = "medium"
        injected_score = 75.0

        create_baseline(
            indicator_name=indicator,
            threshold_score=threshold,
            scope=scope,
            scope_id=scope_id,
            severity_band=severity_band,
            operator_id=operator_id,
            rationale="Low threshold for unit testing",
        )

        result = evaluate_indicator_and_alert(
            indicator_name=indicator,
            score=injected_score,
            scope_id=scope_id,
            scope=scope,
            operator_id=operator_id,
            pir_type="PIR",
        )

        validation = result["validation"]
        alert = result["alert"]

        assert validation["exceeded"] is True
        assert validation["score"] == injected_score
        assert validation["threshold"] == threshold
        assert validation["delta"] == pytest.approx(injected_score - threshold, abs=0.001)
        assert validation["severity_band"] == severity_band

        assert alert is not None
        assert alert["pir_type"] == "PIR"
        assert alert["topic"] == f"{indicator} threshold exceeded"
        assert alert["status"] == "active"
        assert alert["collection_priority"] == 3  # medium → 3
        assert alert["tasked_to"] == operator_id
        assert indicator in alert["description"]
        assert str(injected_score) in alert["description"]
        assert alert.get("id") is not None

    def test_within_baseline_no_alert(self, db_conn):
        """A score below the threshold must NOT trigger an alert."""
        indicator = "cpu_usage"
        operator_id = "op-test-002"
        scope = "global"
        threshold = 90.0
        injected_score = 45.0

        create_baseline(
            indicator_name=indicator,
            threshold_score=threshold,
            scope=scope,
            operator_id=operator_id,
        )

        result = evaluate_indicator_and_alert(
            indicator_name=indicator,
            score=injected_score,
            scope=scope,
            operator_id=operator_id,
        )

        assert result["validation"]["exceeded"] is False
        assert result["validation"]["valid"] is True
        assert result["validation"]["delta"] == 0.0
        assert result["alert"] is None

    def test_unbounded_no_baseline_no_alert(self, db_conn):
        """When no baseline exists the score is unbounded and no alert is generated."""
        indicator = "unknown_indicator"

        result = evaluate_indicator_and_alert(
            indicator_name=indicator,
            score=99.0,
        )

        assert result["validation"]["unbounded"] is True
        assert result["validation"]["exceeded"] is False
        assert result["validation"]["valid"] is True
        assert result["alert"] is None

    def test_critical_severity_priority_1(self, db_conn):
        """A critical severity baseline must produce a priority-1 alert."""
        indicator = "ransomware_likelihood"
        operator_id = "op-test-003"
        threshold = 10.0
        severity_band = "critical"
        injected_score = 95.0

        create_baseline(
            indicator_name=indicator,
            threshold_score=threshold,
            scope="tenant",
            scope_id="tenant-test-001",
            severity_band=severity_band,
            operator_id=operator_id,
        )

        result = evaluate_indicator_and_alert(
            indicator_name=indicator,
            score=injected_score,
            scope="tenant",
            scope_id="tenant-test-001",
            operator_id=operator_id,
            pir_type="CCIR",
        )

        alert = result["alert"]
        assert alert is not None
        assert alert["pir_type"] == "CCIR"
        assert alert["collection_priority"] == 1

    def test_high_severity_priority_2(self, db_conn):
        """A high severity baseline must produce a priority-2 alert."""
        indicator = "phishing_volume"
        threshold = 50.0
        severity_band = "high"
        injected_score = 80.0

        create_baseline(
            indicator_name=indicator,
            threshold_score=threshold,
            scope="global",
            severity_band=severity_band,
            operator_id="op-test-004",
        )

        result = evaluate_indicator_and_alert(
            indicator_name=indicator,
            score=injected_score,
            scope="global",
        )

        alert = result["alert"]
        assert alert is not None
        assert alert["collection_priority"] == 2

    def test_low_severity_priority_4(self, db_conn):
        """A low severity baseline must produce a priority-4 alert."""
        indicator = "disk_io"
        threshold = 100.0
        severity_band = "low"
        injected_score = 150.0

        create_baseline(
            indicator_name=indicator,
            threshold_score=threshold,
            scope="global",
            severity_band=severity_band,
            operator_id="op-test-005",
        )

        result = evaluate_indicator_and_alert(
            indicator_name=indicator,
            score=injected_score,
            scope="global",
        )

        alert = result["alert"]
        assert alert is not None
        assert alert["collection_priority"] == 4

    def test_exact_threshold_no_alert(self, db_conn):
        """A score exactly equal to the threshold must NOT trigger an alert."""
        indicator = "latency_ms"
        threshold = 100.0
        injected_score = 100.0

        create_baseline(
            indicator_name=indicator,
            threshold_score=threshold,
            scope="global",
            operator_id="op-test-006",
        )

        result = evaluate_indicator_and_alert(
            indicator_name=indicator,
            score=injected_score,
            scope="global",
        )

        assert result["validation"]["exceeded"] is False
        assert result["validation"]["delta"] == 0.0
        assert result["alert"] is None
