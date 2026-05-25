# CUI // SP-CTI
"""Integration test: PIR alert dispatcher writes alerts to secure log store.

Confirms that NotificationGateway.send(event_type="pir_alert") persists
an immutable entry to audit_trail via tools.audit.audit_logger.log_event.

Acceptance: alerts land in the secure log store without invoking silent-cleanup
or stale CLI subprocesses.
"""

import json
import sqlite3
from unittest.mock import patch


from tools.notifications.gateway import NotificationGateway


def _make_test_db(tmp_path):
    """Create a temp SQLite DB with audit_trail matching production schema."""
    db_path = tmp_path / "secure_log.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS audit_trail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            affected_files TEXT,
            classification TEXT DEFAULT 'CUI',
            ip_address TEXT,
            session_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS notification_log (
            id TEXT PRIMARY KEY,
            event_type TEXT,
            adapter TEXT,
            severity TEXT,
            title TEXT,
            delivered INTEGER,
            error TEXT,
            created_at TEXT
        );
        """
    )
    conn.close()
    return db_path


def _connect(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


class TestAlertDispatcherSecureLogIntegration:
    """Verify PIR alerts are persisted to the secure audit trail."""

    def test_pir_alert_persisted_to_audit_trail(self, tmp_path):
        """A pir_alert event must create an audit_trail entry."""
        db_path = _make_test_db(tmp_path)

        def _test_conn():
            return _connect(db_path)

        with patch("tools.audit.audit_logger.get_connection", _test_conn):
            gw = NotificationGateway(
                config={
                    "enabled": True,
                    "adapters": {},
                    "routing": {},
                    "sanitize_before_delivery": False,
                }
            )
            result = gw.send(
                event_type="pir_alert",
                severity="critical",
                title="PIR baseline threshold crossed: Ukraine invasion",
                body="CCIR ccir-ukr-1 triggered with match score 0.85.",
                metadata={"ccir_id": "ccir-ukr-1", "event_id": "evt-001"},
            )

        # Verification: assert exactly N alerts exist in local mock store before cleanup
        conn_verify = _connect(db_path)
        count = conn_verify.execute(
            "SELECT COUNT(*) as cnt FROM audit_trail WHERE event_type = ?", ("pir_alert_generated",)
        ).fetchone()["cnt"]
        conn_verify.close()
        assert count == 1, f"Expected exactly 1 pir_alert in local mock store, got {count}"
        print(f"[VERIFICATION] Local mock store contains exactly {count} alert(s) prior to finalization")

        assert result["event_type"] == "pir_alert"

        conn = _connect(db_path)
        row = conn.execute(
            "SELECT * FROM audit_trail WHERE event_type = ?", ("pir_alert_generated",)
        ).fetchone()
        conn.close()

        assert row is not None, "pir_alert must be written to audit_trail"
        assert row["actor"] == "notification_gateway"
        assert row["action"] == "PIR baseline threshold crossed: Ukraine invasion"
        assert row["classification"] == "CUI"
        details = json.loads(row["details"])
        assert details["severity"] == "critical"
        assert details["ccir_id"] == "ccir-ukr-1"
        assert details["event_id"] == "evt-001"

    def test_non_pir_alert_not_persisted_to_audit_trail(self, tmp_path):
        """Non-PIR events must NOT create audit_trail entries."""
        db_path = _make_test_db(tmp_path)

        def _test_conn():
            return _connect(db_path)

        with patch("tools.audit.audit_logger.get_connection", _test_conn):
            gw = NotificationGateway(
                config={
                    "enabled": True,
                    "adapters": {},
                    "routing": {},
                    "sanitize_before_delivery": False,
                }
            )
            gw.send(
                event_type="compliance_alert",
                severity="warning",
                title="STIG check complete",
                body="No findings.",
            )

        # Verification: assert exactly 0 alerts exist in local mock store before cleanup
        conn_verify = _connect(db_path)
        count = conn_verify.execute(
            "SELECT COUNT(*) as cnt FROM audit_trail WHERE event_type = ?", ("pir_alert_generated",)
        ).fetchone()["cnt"]
        conn_verify.close()
        assert count == 0, f"Expected exactly 0 pir_alert in local mock store, got {count}"
        print(f"[VERIFICATION] Local mock store contains exactly {count} alert(s) prior to finalization")

        conn = _connect(db_path)
        row = conn.execute("SELECT * FROM audit_trail").fetchone()
        conn.close()
        assert row is None, "non-pir_alert must not be written to audit_trail"

    def test_pir_alert_persisted_even_when_notifications_disabled(self, tmp_path):
        """The secure log store must receive PIR alerts regardless of gateway enablement."""
        db_path = _make_test_db(tmp_path)

        def _test_conn():
            return _connect(db_path)

        with patch("tools.audit.audit_logger.get_connection", _test_conn):
            gw = NotificationGateway(config={"enabled": False})
            gw.enabled = False  # override env-var if ICDEV_NOTIFICATIONS_ENABLED is set
            result = gw.send(
                event_type="pir_alert",
                severity="critical",
                title="Test disabled notification",
                body="Body text.",
            )

        # Verification: assert exactly N alerts exist in local mock store before cleanup
        conn_verify = _connect(db_path)
        count = conn_verify.execute(
            "SELECT COUNT(*) as cnt FROM audit_trail WHERE event_type = ?", ("pir_alert_generated",)
        ).fetchone()["cnt"]
        conn_verify.close()
        assert count == 1, f"Expected exactly 1 pir_alert in local mock store, got {count}"
        print(f"[VERIFICATION] Local mock store contains exactly {count} alert(s) prior to finalization")

        assert result["skipped"] == "notifications_disabled"

        conn = _connect(db_path)
        row = conn.execute(
            "SELECT * FROM audit_trail WHERE event_type = ?", ("pir_alert_generated",)
        ).fetchone()
        conn.close()
        assert row is not None, "pir_alert must reach audit_trail even when gateway disabled"
        assert row["action"] == "Test disabled notification"
