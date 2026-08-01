# CUI // SP-CTI
"""Unit tests validating WATCHCON 4/3/2 alert tier support.

Covers:
  - Constants: tier values, labels, and severity mappings
  - classify_tier(): severity string → tier integer
  - tier_label() / tier_severity(): round-trip helpers
  - insert_alert(): auto-assigns correct tier on write
  - get_alerts_by_tier(): retrieves alerts by tier
  - tier_summary(): returns counts for all three tiers
  - backfill_tiers(): re-classifies existing rows
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

from tools.monitor.constants import (
    WATCHCON_2,
    WATCHCON_3,
    WATCHCON_4,
    WATCHCON_LABELS,
    WATCHCON_TIERS,
    WATCHCON_TO_SEVERITY,
    SEVERITY_TO_WATCHCON,
)
from tools.monitor.watchcon import (
    classify_tier,
    tier_label,
    tier_severity,
    insert_alert,
    get_alerts_by_tier,
    tier_summary,
    backfill_tiers,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _create_alerts_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,
            severity TEXT,
            source TEXT,
            title TEXT,
            description TEXT,
            status TEXT DEFAULT 'open',
            acknowledged_by TEXT,
            resolved_at TEXT,
            auto_healed INTEGER DEFAULT 0,
            healing_event_id TEXT,
            watchcon_tier INTEGER DEFAULT 4,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


@pytest.fixture()
def tmp_db(tmp_path):
    """Temporary SQLite DB with the alerts table pre-created."""
    db_file = tmp_path / "test_icdev.db"
    conn = sqlite3.connect(str(db_file))
    _create_alerts_table(conn)
    conn.close()

    with patch("tools.monitor.watchcon.get_connection") as mock_conn:
        # Return a fresh connection on every call so tests are isolated.
        # Wrapped in StorageConnection so the module's PG-native %s SQL is
        # translated for SQLite (same pattern as tests/test_cato_twin.py).
        from tools.db.storage import StorageConnection

        def _get_conn(db_path=None, **kw):
            return StorageConnection(sqlite3.connect(str(db_file)), "sqlite")

        mock_conn.side_effect = _get_conn
        yield db_file


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestWatchconConstants:
    def test_tier_values(self):
        assert WATCHCON_4 == 4
        assert WATCHCON_3 == 3
        assert WATCHCON_2 == 2

    def test_tiers_tuple_contains_all(self):
        assert set(WATCHCON_TIERS) == {2, 3, 4}

    def test_labels(self):
        assert WATCHCON_LABELS[4] == "routine"
        assert WATCHCON_LABELS[3] == "elevated"
        assert WATCHCON_LABELS[2] == "high"

    def test_severity_mapping_round_trip(self):
        for tier, sev in WATCHCON_TO_SEVERITY.items():
            assert SEVERITY_TO_WATCHCON[sev] == tier


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

class TestClassifyTier:
    @pytest.mark.parametrize("severity,expected_tier", [
        ("info", 4),
        ("warning", 3),
        ("critical", 2),
    ])
    def test_known_severities(self, severity, expected_tier):
        assert classify_tier(severity) == expected_tier

    def test_case_insensitive(self):
        assert classify_tier("INFO") == 4
        assert classify_tier("WARNING") == 3
        assert classify_tier("CRITICAL") == 2

    def test_unknown_defaults_to_watchcon4(self):
        assert classify_tier("unknown") == 4
        assert classify_tier("") == 4


class TestTierHelpers:
    @pytest.mark.parametrize("tier,expected_label", [
        (4, "routine"),
        (3, "elevated"),
        (2, "high"),
    ])
    def test_tier_label(self, tier, expected_label):
        assert tier_label(tier) == expected_label

    def test_tier_label_unknown(self):
        assert tier_label(99) == "unknown"

    @pytest.mark.parametrize("tier,expected_severity", [
        (4, "info"),
        (3, "warning"),
        (2, "critical"),
    ])
    def test_tier_severity(self, tier, expected_severity):
        assert tier_severity(tier) == expected_severity

    def test_tier_severity_unknown_defaults_info(self):
        assert tier_severity(99) == "info"


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

class TestInsertAlert:
    def test_info_gets_watchcon4(self, tmp_db):
        result = insert_alert("info", "test", "Routine alert", db_path=tmp_db)
        assert result["watchcon_tier"] == 4
        assert result["watchcon_label"] == "routine"

    def test_warning_gets_watchcon3(self, tmp_db):
        result = insert_alert("warning", "test", "Elevated alert", db_path=tmp_db)
        assert result["watchcon_tier"] == 3
        assert result["watchcon_label"] == "elevated"

    def test_critical_gets_watchcon2(self, tmp_db):
        result = insert_alert("critical", "test", "High alert", db_path=tmp_db)
        assert result["watchcon_tier"] == 2
        assert result["watchcon_label"] == "high"

    def test_returns_inserted_id(self, tmp_db):
        result = insert_alert("info", "src", "Test", db_path=tmp_db)
        assert isinstance(result["id"], int)
        assert result["id"] > 0


class TestGetAlertsByTier:
    def test_returns_correct_tier(self, tmp_db):
        insert_alert("info", "src", "R1", db_path=tmp_db)
        insert_alert("warning", "src", "E1", db_path=tmp_db)
        insert_alert("critical", "src", "H1", db_path=tmp_db)

        routine = get_alerts_by_tier(4, db_path=tmp_db)
        elevated = get_alerts_by_tier(3, db_path=tmp_db)
        high = get_alerts_by_tier(2, db_path=tmp_db)

        assert len(routine) == 1
        assert routine[0]["title"] == "R1"
        assert len(elevated) == 1
        assert elevated[0]["title"] == "E1"
        assert len(high) == 1
        assert high[0]["title"] == "H1"

    def test_invalid_tier_raises(self, tmp_db):
        with pytest.raises(ValueError):
            get_alerts_by_tier(1, db_path=tmp_db)

    def test_limit_respected(self, tmp_db):
        for i in range(5):
            insert_alert("info", "src", f"Alert {i}", db_path=tmp_db)
        results = get_alerts_by_tier(4, db_path=tmp_db, limit=3)
        assert len(results) == 3


class TestTierSummary:
    def test_all_tiers_present_in_summary(self, tmp_db):
        insert_alert("info", "src", "R", db_path=tmp_db)
        insert_alert("warning", "src", "E", db_path=tmp_db)
        insert_alert("critical", "src", "H", db_path=tmp_db)

        summary = tier_summary(db_path=tmp_db)
        tiers = summary["tiers"]

        assert "2" in tiers
        assert "3" in tiers
        assert "4" in tiers
        assert tiers["4"]["count"] == 1
        assert tiers["3"]["count"] == 1
        assert tiers["2"]["count"] == 1

    def test_empty_db_returns_zero_counts(self, tmp_db):
        summary = tier_summary(db_path=tmp_db)
        for tier_key in ("2", "3", "4"):
            assert summary["tiers"][tier_key]["count"] == 0

    def test_summary_includes_labels_and_severities(self, tmp_db):
        summary = tier_summary(db_path=tmp_db)
        assert summary["tiers"]["4"]["label"] == "routine"
        assert summary["tiers"]["3"]["label"] == "elevated"
        assert summary["tiers"]["2"]["label"] == "high"
        assert summary["tiers"]["4"]["severity"] == "info"
        assert summary["tiers"]["3"]["severity"] == "warning"
        assert summary["tiers"]["2"]["severity"] == "critical"


class TestBackfillTiers:
    def test_backfill_sets_correct_tiers(self, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        conn.execute(
            "INSERT INTO alerts (severity, source, title, watchcon_tier) VALUES (?, ?, ?, NULL)",
            ("critical", "src", "Unclassified"),
        )
        conn.commit()
        conn.close()

        updated = backfill_tiers(db_path=tmp_db)
        assert updated >= 1

        alerts = get_alerts_by_tier(2, db_path=tmp_db)
        titles = [a["title"] for a in alerts]
        assert "Unclassified" in titles
