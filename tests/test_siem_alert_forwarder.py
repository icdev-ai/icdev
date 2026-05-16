# CUI // SP-CTI
"""Tests for SIEM alert forwarder.

Validates the 5-second SLA, HTTP delivery logic, and append-only audit
log recording.  Uses mocked urlopen to avoid external network calls.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

from tools.siem_alert_forwarder import SLA_SECONDS, forward_alert


class TestSiemAlertForwarder:
    """RED → GREEN → REFACTOR cycle for SIEM alert delivery."""

    def test_sla_constant_is_five_seconds(self):
        """Requirement: alerts must be delivered within 5 seconds."""
        assert SLA_SECONDS == 5

    def test_forward_alert_success_within_sla(self, tmp_path):
        """Happy path: mocked SIEM accepts alert in < 5 s."""
        alert = {
            "title": "Test Alert",
            "severity": "high",
            "source": "test-suite",
        }
        mock_resp = MagicMock()
        mock_resp.status = 202
        mock_resp.read.return_value = b"{}"
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = False

        db_file = tmp_path / "siem_test.db"
        _init_test_db(db_file)

        with patch("tools.siem_alert_forwarder.urlopen", return_value=mock_resp):
            result = forward_alert(
                alert,
                "http://localhost:9999/siem",
                db_path=str(db_file),
            )

        assert result["delivered"] is True
        assert result["sla_met"] is True
        assert result["error"] is None
        assert 0 <= result["duration_ms"] < 5000

    def test_forward_alert_records_to_db(self, tmp_path):
        """Append-only siem_delivery_log receives a row per delivery."""
        alert = {
            "title": "DB Test",
            "severity": "medium",
            "source": "test-suite",
        }
        mock_resp = MagicMock()
        mock_resp.status = 202
        mock_resp.read.return_value = b"{}"
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = False

        db_file = tmp_path / "siem_test.db"
        _init_test_db(db_file)

        with patch("tools.siem_alert_forwarder.urlopen", return_value=mock_resp):
            result = forward_alert(
                alert,
                "http://localhost:9999/siem",
                db_path=str(db_file),
            )

        conn = sqlite3.connect(str(db_file))
        rows = conn.execute(
            "SELECT alert_title, severity, sla_met, delivered_at "
            "FROM siem_delivery_log WHERE id = ?",
            (result["delivery_id"],),
        ).fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0][0] == "DB Test"
        assert rows[0][1] == "medium"
        assert rows[0][2] == 1  # sla_met
        assert rows[0][3] is not None  # delivered_at

    def test_forward_alert_failure_due_to_timeout(self, tmp_path):
        """Error path: timeout causes delivered=False and sla_met=False."""
        alert = {"title": "Timeout Test", "severity": "low"}
        db_file = tmp_path / "siem_test.db"
        _init_test_db(db_file)

        exc = TimeoutError("Request timed out")
        with patch("tools.siem_alert_forwarder.urlopen", side_effect=exc):
            result = forward_alert(
                alert,
                "http://slow-siem/events",
                db_path=str(db_file),
            )

        assert result["delivered"] is False
        assert result["sla_met"] is False
        assert result["error"] is not None

        # Verify failure also recorded
        conn = sqlite3.connect(str(db_file))
        rows = conn.execute(
            "SELECT error, sla_met FROM siem_delivery_log WHERE id = ?",
            (result["delivery_id"],),
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] is not None
        assert rows[0][1] == 0

    def test_forward_alert_with_auth_token(self, tmp_path):
        """Edge case: Bearer token is injected into the request."""
        alert = {"title": "Auth Test", "severity": "critical"}
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"{}"
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = False

        db_file = tmp_path / "siem_test.db"
        _init_test_db(db_file)

        captured_req = {}

        def _capture_urlopen(req, **kwargs):
            captured_req["headers"] = dict(req.headers)
            return mock_resp

        with patch("tools.siem_alert_forwarder.urlopen", side_effect=_capture_urlopen):
            forward_alert(
                alert,
                "http://siem/events",
                siem_token="secret-token-123",
                db_path=str(db_file),
            )

        assert captured_req["headers"]["Authorization"] == "Bearer secret-token-123"


def _init_test_db(db_path):
    """Create siem_delivery_log on a fresh SQLite file for test isolation."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS siem_delivery_log (
            id TEXT PRIMARY KEY,
            alert_title TEXT,
            severity TEXT,
            siem_endpoint TEXT,
            status_code INTEGER,
            duration_ms REAL,
            sla_met INTEGER,
            error TEXT,
            delivered_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()
