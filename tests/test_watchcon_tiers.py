#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for WATCHCON alert tier support.

Covers tools/monitor/watchcon.py and tools/monitor/constants.py.
ADR: WATCHCON three-tier alert system — tiers 4 (routine), 3 (elevated), 2 (high).
"""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.monitor.constants import (
    WATCHCON_2,
    WATCHCON_3,
    WATCHCON_4,
    WATCHCON_TIERS,
    WATCHCON_LABELS,
    WATCHCON_TO_SEVERITY,
    SEVERITY_TO_WATCHCON,
)
from tools.monitor import watchcon as wc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path):
    """Minimal alerts DB for tests."""
    db_path = tmp_path / "test_alerts.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,
            severity TEXT NOT NULL CHECK(severity IN ('critical', 'warning', 'info')),
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'firing',
            acknowledged_by TEXT,
            resolved_at TEXT,
            auto_healed INTEGER DEFAULT 0,
            healing_event_id INTEGER,
            watchcon_tier INTEGER DEFAULT 4 CHECK(watchcon_tier IN (2, 3, 4)),
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def populated_db(tmp_db):
    """DB seeded with one alert per WATCHCON tier."""
    conn = sqlite3.connect(str(tmp_db))
    rows = [
        ("info",     "prometheus", "Disk usage 40%",        "WATCHCON 4 alert", 4),
        ("warning",  "elk",        "CPU spike detected",    "WATCHCON 3 alert", 3),
        ("critical", "splunk",     "Service unreachable",   "WATCHCON 2 alert", 2),
    ]
    conn.executemany(
        "INSERT INTO alerts (severity, source, title, description, watchcon_tier) VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return tmp_db


# ---------------------------------------------------------------------------
# Constants tests
# ---------------------------------------------------------------------------


def test_three_tiers_defined():
    assert WATCHCON_2 == 2
    assert WATCHCON_3 == 3
    assert WATCHCON_4 == 4
    assert set(WATCHCON_TIERS) == {2, 3, 4}


def test_tier_labels():
    assert WATCHCON_LABELS[WATCHCON_4] == "routine"
    assert WATCHCON_LABELS[WATCHCON_3] == "elevated"
    assert WATCHCON_LABELS[WATCHCON_2] == "high"


def test_severity_mapping_roundtrip():
    for tier in WATCHCON_TIERS:
        sev = WATCHCON_TO_SEVERITY[tier]
        assert SEVERITY_TO_WATCHCON[sev] == tier


# ---------------------------------------------------------------------------
# classify_tier tests
# ---------------------------------------------------------------------------


def test_classify_info_gives_watchcon4():
    assert wc.classify_tier("info") == WATCHCON_4


def test_classify_warning_gives_watchcon3():
    assert wc.classify_tier("warning") == WATCHCON_3


def test_classify_critical_gives_watchcon2():
    assert wc.classify_tier("critical") == WATCHCON_2


def test_classify_unknown_defaults_to_watchcon4():
    assert wc.classify_tier("unknown_severity") == WATCHCON_4


def test_classify_case_insensitive():
    assert wc.classify_tier("CRITICAL") == WATCHCON_2
    assert wc.classify_tier("Warning") == WATCHCON_3
    assert wc.classify_tier("INFO") == WATCHCON_4


# ---------------------------------------------------------------------------
# tier_label / tier_severity tests
# ---------------------------------------------------------------------------


def test_tier_label_all_tiers():
    assert wc.tier_label(WATCHCON_4) == "routine"
    assert wc.tier_label(WATCHCON_3) == "elevated"
    assert wc.tier_label(WATCHCON_2) == "high"


def test_tier_severity_all_tiers():
    assert wc.tier_severity(WATCHCON_4) == "info"
    assert wc.tier_severity(WATCHCON_3) == "warning"
    assert wc.tier_severity(WATCHCON_2) == "critical"


# ---------------------------------------------------------------------------
# insert_alert / get_alerts_by_tier tests
# ---------------------------------------------------------------------------


def test_insert_alert_assigns_correct_tier(tmp_db):
    result = wc.insert_alert("critical", "elk", "Test alert", db_path=tmp_db)
    assert result["watchcon_tier"] == WATCHCON_2
    assert result["watchcon_label"] == "high"


def test_insert_all_three_tiers(tmp_db):
    for severity, expected_tier in [("info", 4), ("warning", 3), ("critical", 2)]:
        r = wc.insert_alert(severity, "test", f"{severity} alert", db_path=tmp_db)
        assert r["watchcon_tier"] == expected_tier


def test_get_alerts_by_tier_watchcon4(populated_db):
    alerts = wc.get_alerts_by_tier(WATCHCON_4, db_path=populated_db)
    assert len(alerts) == 1
    assert alerts[0]["watchcon_tier"] == WATCHCON_4
    assert alerts[0]["severity"] == "info"


def test_get_alerts_by_tier_watchcon3(populated_db):
    alerts = wc.get_alerts_by_tier(WATCHCON_3, db_path=populated_db)
    assert len(alerts) == 1
    assert alerts[0]["watchcon_tier"] == WATCHCON_3
    assert alerts[0]["severity"] == "warning"


def test_get_alerts_by_tier_watchcon2(populated_db):
    alerts = wc.get_alerts_by_tier(WATCHCON_2, db_path=populated_db)
    assert len(alerts) == 1
    assert alerts[0]["watchcon_tier"] == WATCHCON_2
    assert alerts[0]["severity"] == "critical"


def test_get_alerts_by_tier_invalid_raises(populated_db):
    with pytest.raises(ValueError, match="Invalid WATCHCON tier"):
        wc.get_alerts_by_tier(1, db_path=populated_db)


# ---------------------------------------------------------------------------
# tier_summary tests
# ---------------------------------------------------------------------------


def test_tier_summary_all_three_tiers(populated_db):
    summary = wc.tier_summary(db_path=populated_db)
    tiers = summary["tiers"]
    assert tiers["4"]["count"] == 1
    assert tiers["4"]["label"] == "routine"
    assert tiers["3"]["count"] == 1
    assert tiers["3"]["label"] == "elevated"
    assert tiers["2"]["count"] == 1
    assert tiers["2"]["label"] == "high"


def test_tier_summary_empty_db(tmp_db):
    summary = wc.tier_summary(db_path=tmp_db)
    tiers = summary["tiers"]
    for tier_key in ("2", "3", "4"):
        assert tiers[tier_key]["count"] == 0


# ---------------------------------------------------------------------------
# backfill tests
# ---------------------------------------------------------------------------


def test_backfill_populates_tier_from_severity(tmp_db):
    conn = sqlite3.connect(str(tmp_db))
    conn.execute(
        "INSERT INTO alerts (severity, source, title, watchcon_tier) VALUES (?, ?, ?, ?)",
        ("critical", "elk", "Untiered critical alert", None),
    )
    conn.commit()
    conn.close()

    updated = wc.backfill_tiers(db_path=tmp_db)
    assert updated >= 1

    alerts = wc.get_alerts_by_tier(WATCHCON_2, db_path=tmp_db)
    assert any(a["title"] == "Untiered critical alert" for a in alerts)


# ---------------------------------------------------------------------------
# Schema migration (_ensure_column) tests
# ---------------------------------------------------------------------------


def test_ensure_column_idempotent(tmp_db):
    """Calling _ensure_column twice must not raise."""
    conn = sqlite3.connect(str(tmp_db))
    wc._ensure_column(conn)
    wc._ensure_column(conn)
    conn.close()


def test_ensure_column_adds_to_legacy_table(tmp_path):
    """alerts table without watchcon_tier gets the column added."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,
            severity TEXT NOT NULL,
            source TEXT NOT NULL,
            title TEXT NOT NULL
        )
    """)
    conn.commit()

    with patch("tools.monitor.watchcon.get_connection", return_value=conn):
        wc._ensure_column(conn)

    cols = {row[1] for row in conn.execute("PRAGMA table_info(alerts)")}
    assert "watchcon_tier" in cols
    conn.close()
