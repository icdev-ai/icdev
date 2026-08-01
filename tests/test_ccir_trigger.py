"""Tests for tools.strategos.ccir_trigger."""

import sqlite3
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from tools.strategos.ccir_trigger import (
    _get_historical_scores,
    _validate_baseline,
    evaluate_ccirs,
    _keywords,
    _match_score,
)


# ---------------------------------------------------------------------------
# _keywords
# ---------------------------------------------------------------------------


def test_keywords_extracts_meaningful_words():
    text = "The quick brown fox jumps over the lazy dog"
    result = _keywords(text)
    assert "quick" in result
    assert "brown" in result
    assert "jumps" in result
    assert "the" not in result


def test_keywords_filters_short_and_stopwords():
    text = (
        "a an the of in on at to for and or is are was were be been "
        "by with from that this which what"
    )
    result = _keywords(text)
    assert len(result) == 0


# ---------------------------------------------------------------------------
# _match_score
# ---------------------------------------------------------------------------


def test_match_score_perfect_match():
    keywords = ["invasion", "ukraine", "troop"]
    signal = "Russian invasion of Ukraine with troop movements"
    assert _match_score(keywords, signal) == 1.0


def test_match_score_partial_match():
    keywords = ["invasion", "ukraine", "troop", "tank"]
    signal = "Russian invasion of Ukraine"
    assert _match_score(keywords, signal) == 0.5


def test_match_score_no_match():
    keywords = ["cyber", "attack"]
    signal = "Diplomatic negotiations continue"
    assert _match_score(keywords, signal) == 0.0


# ---------------------------------------------------------------------------
# _get_historical_scores
# ---------------------------------------------------------------------------


def test_get_historical_scores_empty(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE sg_ccir_trigger_events (
            id TEXT PRIMARY KEY,
            ccir_id TEXT,
            match_score REAL,
            created_at TEXT
        );
        """
    )
    scores = _get_historical_scores(conn, "ccir-1")
    assert scores == []
    conn.close()


def test_get_historical_scores_returns_past_scores(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE sg_ccir_trigger_events (
            id TEXT PRIMARY KEY,
            ccir_id TEXT,
            match_score REAL,
            created_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO sg_ccir_trigger_events VALUES ('e1', 'ccir-1', 0.2, '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO sg_ccir_trigger_events VALUES ('e2', 'ccir-1', 0.3, '2026-01-02')"
    )
    conn.execute(
        "INSERT INTO sg_ccir_trigger_events VALUES ('e3', 'ccir-2', 0.8, '2026-01-03')"
    )
    conn.commit()
    scores = _get_historical_scores(conn, "ccir-1")
    assert scores == [0.2, 0.3]
    conn.close()


# ---------------------------------------------------------------------------
# _validate_baseline
# ---------------------------------------------------------------------------


def test_validate_baseline_no_history_returns_true(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE sg_ccir_trigger_events (
            id TEXT PRIMARY KEY,
            ccir_id TEXT,
            match_score REAL,
            created_at TEXT
        );
        """
    )
    assert _validate_baseline(conn, "ccir-1", 0.5) is True
    conn.close()


def test_validate_baseline_insufficient_history_returns_true(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE sg_ccir_trigger_events (
            id TEXT PRIMARY KEY,
            ccir_id TEXT,
            match_score REAL,
            created_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO sg_ccir_trigger_events VALUES ('e1', 'ccir-1', 0.2, '2026-01-01')"
    )
    conn.commit()
    assert _validate_baseline(conn, "ccir-1", 0.5) is True
    conn.close()


def test_validate_baseline_cusum_detects_anomaly(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE sg_ccir_trigger_events (
            id TEXT PRIMARY KEY,
            ccir_id TEXT,
            match_score REAL,
            created_at TEXT
        );
        """
    )
    for i, score in enumerate([0.15, 0.16, 0.14, 0.15, 0.16, 0.15]):
        conn.execute(
            "INSERT INTO sg_ccir_trigger_events VALUES (?, 'ccir-1', ?, ?)",
            (f"e{i}", score, f"2026-01-{i + 1:02d}"),
        )
    conn.commit()
    assert _validate_baseline(conn, "ccir-1", 0.95) is True
    conn.close()


def test_validate_baseline_cusum_normal_returns_false(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE sg_ccir_trigger_events (
            id TEXT PRIMARY KEY,
            ccir_id TEXT,
            match_score REAL,
            created_at TEXT
        );
        """
    )
    for i, score in enumerate([0.15, 0.16, 0.14, 0.15, 0.16, 0.15]):
        conn.execute(
            "INSERT INTO sg_ccir_trigger_events VALUES (?, 'ccir-1', ?, ?)",
            (f"e{i}", score, f"2026-01-{i + 1:02d}"),
        )
    conn.commit()
    assert _validate_baseline(conn, "ccir-1", 0.16) is False
    conn.close()


# ---------------------------------------------------------------------------
# evaluate_ccirs integration
# ---------------------------------------------------------------------------


@pytest.fixture
def icdev_intelligence_db(tmp_path):
    """Temporary DB with intelligence schema."""
    db_path = tmp_path / "intel.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE sg_pir_requirements (
            id TEXT PRIMARY KEY,
            pir_type TEXT,
            topic TEXT,
            description TEXT,
            collection_priority TEXT,
            status TEXT
        );
        CREATE TABLE sg_sigint_events (
            id TEXT PRIMARY KEY,
            description TEXT,
            event_type TEXT,
            collected_at TEXT
        );
        CREATE TABLE sg_eo_signals (
            id TEXT PRIMARY KEY,
            description TEXT,
            signal_type TEXT,
            collected_at TEXT
        );
        CREATE TABLE sg_socmint_signals (
            id TEXT PRIMARY KEY,
            text TEXT,
            platform TEXT,
            posted_at TEXT
        );
        CREATE TABLE sg_ccir_trigger_events (
            id TEXT PRIMARY KEY,
            ccir_id TEXT,
            signal_source TEXT,
            signal_text TEXT,
            match_score REAL,
            resolved INTEGER DEFAULT 0,
            created_at TEXT,
            resolved_at TEXT
        );
        """
    )
    conn.close()
    return db_path


def _seed_ccir_and_signals(conn):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO sg_pir_requirements VALUES (?, 'CCIR', 'Ukraine invasion',"
        " 'Track Russian troop movements', 'high', 'active')",
        ("ccir-ukr-1",),
    )
    conn.execute(
        "INSERT INTO sg_sigint_events VALUES (?, 'Russian armor columns spotted near border',"
        " 'sigint', ?)",
        ("sig-1", now),
    )
    conn.commit()


def test_evaluate_ccirs_trigger_and_alert_when_baseline_true(icdev_intelligence_db):
    with (
        patch("tools.db.storage.get_connection") as mock_get_conn,
        patch("tools.strategos.ccir_trigger.publish") as mock_publish,
        patch("tools.strategos.ccir_trigger.NotificationGateway") as mock_gw,
    ):
        # Translating wrapper — ccir_trigger authors %s for PostgreSQL.
        from _sql_compat import connect as _tconnect

        conn = _tconnect(icdev_intelligence_db)
        mock_get_conn.return_value = conn

        mock_gw_instance = MagicMock()
        mock_gw_instance.send.return_value = {
            "deliveries": {"slack": {"status": "delivered"}}
        }
        mock_gw.return_value = mock_gw_instance

        _seed_ccir_and_signals(conn)

        # No historical scores -> baseline returns True
        result = evaluate_ccirs(lookback_hours=24, threshold=0.1)

        assert result["evaluated"] == 1
        assert len(result["triggered"]) == 1
        assert result["triggered"][0]["ccir_id"] == "ccir-ukr-1"
        assert result["triggered"][0]["baseline_valid"] is True

        # Alert should have been published because baseline validation returned True
        mock_publish.assert_called_once()
        args = mock_publish.call_args
        assert args[0][0] == "strategos"
        assert args[0][1] == "pir.baseline.validated"
        assert args[1]["target_canvas"] == "commander"
        payload = args[0][2]
        assert payload["ccir_id"] == "ccir-ukr-1"
        assert payload["match_score"] > 0.1

        # Notification gateway should have been called
        mock_gw_instance.send.assert_called_once()

        conn.close()


def test_evaluate_ccirs_no_alert_when_baseline_false(icdev_intelligence_db):
    with (
        patch("tools.db.storage.get_connection") as mock_get_conn,
        patch("tools.strategos.ccir_trigger.publish") as mock_publish,
        patch("tools.strategos.ccir_trigger.NotificationGateway") as mock_gw,
        patch("tools.strategos.ccir_trigger._validate_baseline") as mock_val,
    ):
        # Translating wrapper — ccir_trigger authors %s for PostgreSQL.
        from _sql_compat import connect as _tconnect

        conn = _tconnect(icdev_intelligence_db)
        mock_get_conn.return_value = conn

        mock_gw_instance = MagicMock()
        mock_gw.return_value = mock_gw_instance
        mock_val.return_value = False

        _seed_ccir_and_signals(conn)

        result = evaluate_ccirs(lookback_hours=24, threshold=0.1)

        assert result["evaluated"] == 1
        assert len(result["triggered"]) == 1
        assert result["triggered"][0]["ccir_id"] == "ccir-ukr-1"
        assert result["triggered"][0]["baseline_valid"] is False

        # Alert should NOT be published because baseline validation returned False
        mock_publish.assert_not_called()
        mock_gw_instance.send.assert_not_called()

        conn.close()
