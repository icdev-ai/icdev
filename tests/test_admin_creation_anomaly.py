# CUI // SP-CTI
"""Unit tests for admin creation anomaly detection (aiify-rm-a3344-phase-49).

Tests the _detect_admin_creation_anomaly() function added to
tools/dashboard/api/admin.py as the ICDEV analog of the hardcoded_threshold
pattern in paperless-ngx manage_superuser.py.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from tools.dashboard.api.admin import (
    _ADMIN_BURST_MAX,
    _ADMIN_MAX_TOTAL,
    _OFF_HOURS_END_UTC,
    _OFF_HOURS_START_UTC,
    _detect_admin_creation_anomaly,
)


@pytest.fixture()
def tmp_db(icdev_db):
    """Temp dashboard DB for the anomaly tests, standardized on conftest.

    Uses conftest's shared ``icdev_db`` (built from MINIMAL_ICDEV_SCHEMA) rather
    than a private per-file schema, then clears the seeded rows so these
    count-sensitive tests control the admin population exactly.

    Critically, it does NOT mock ``get_connection``: the earlier raw
    ``sqlite3.connect`` mock bypassed StorageConnection.translate_sql, so the
    function's PG-native ``%s`` placeholders raised OperationalError (SQLite
    wants ``?``) and every signal was swallowed to None. Passing this temp DB
    path — which differs from ICDEV_DB_PATH — routes get_connection through the
    no-RLS dedicated-file branch AND translate_sql, exercising the real code
    path. Runtime is PostgreSQL; SQLite is only the conftest-forced test
    backend.
    """
    conn = sqlite3.connect(str(icdev_db))
    conn.execute("DELETE FROM dashboard_users")
    conn.commit()
    conn.close()
    return str(icdev_db)


def _insert_admin(db_path: str, email: str, created_at: str | None = None) -> None:
    conn = sqlite3.connect(db_path)
    ts = created_at or datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO dashboard_users (id, email, display_name, role, status, created_at)"
        " VALUES (?, ?, ?, 'admin', 'active', ?)",
        (email, email, email.split("@")[0], ts),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAdminCreationAnomaly:
    def test_no_anomaly_for_first_admin(self, tmp_db):
        """No signal when DB is empty and request is during business hours."""
        biz_hour = (_OFF_HOURS_START_UTC + _OFF_HOURS_END_UTC) // 2  # noon-ish UTC
        with patch("tools.dashboard.api.admin.datetime") as mock_dt:
            mock_now = datetime(2026, 6, 15, biz_hour, 0, 0, tzinfo=timezone.utc)
            mock_dt.now.return_value = mock_now
            result = _detect_admin_creation_anomaly("new@example.com", "requester@example.com", tmp_db)
        assert result is None

    def test_admin_count_high(self, tmp_db):
        """Returns admin_count_high when active admin count >= _ADMIN_MAX_TOTAL."""
        for i in range(_ADMIN_MAX_TOTAL):
            _insert_admin(tmp_db, f"admin{i}@example.com")

        biz_hour = (_OFF_HOURS_START_UTC + _OFF_HOURS_END_UTC) // 2
        with patch("tools.dashboard.api.admin.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 15, biz_hour, 0, 0, tzinfo=timezone.utc)
            result = _detect_admin_creation_anomaly("new@example.com", "req@example.com", tmp_db)
        assert result == "admin_count_high"

    def test_admin_burst(self, tmp_db):
        """Returns admin_burst when too many admins were created recently."""
        now = datetime.now(timezone.utc)
        for i in range(_ADMIN_BURST_MAX):
            recent_ts = (now - timedelta(seconds=30)).isoformat()
            _insert_admin(tmp_db, f"recent{i}@example.com", created_at=recent_ts)

        biz_hour = (_OFF_HOURS_START_UTC + _OFF_HOURS_END_UTC) // 2
        with patch("tools.dashboard.api.admin.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 15, biz_hour, 0, 0, tzinfo=timezone.utc)
            result = _detect_admin_creation_anomaly("new@example.com", "req@example.com", tmp_db)
        assert result == "admin_burst"

    def test_off_hours_detection(self, tmp_db):
        """Returns off_hours when creation happens outside business hours."""
        off_hour = (_OFF_HOURS_START_UTC + 1) % 24  # 1h into off-hours
        with patch("tools.dashboard.api.admin.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 15, off_hour, 0, 0, tzinfo=timezone.utc)
            result = _detect_admin_creation_anomaly("new@example.com", "req@example.com", tmp_db)
        assert result == "off_hours"

    def test_self_promotion_detection(self, tmp_db):
        """Returns self_promotion when requester grants admin to themselves."""
        biz_hour = (_OFF_HOURS_START_UTC + _OFF_HOURS_END_UTC) // 2
        with patch("tools.dashboard.api.admin.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 15, biz_hour, 0, 0, tzinfo=timezone.utc)
            result = _detect_admin_creation_anomaly("user@example.com", "User@Example.com", tmp_db)
        assert result == "self_promotion"

    def test_no_anomaly_for_non_self_promotion(self, tmp_db):
        """No signal when requester email differs from new admin email."""
        biz_hour = (_OFF_HOURS_START_UTC + _OFF_HOURS_END_UTC) // 2
        with patch("tools.dashboard.api.admin.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 15, biz_hour, 0, 0, tzinfo=timezone.utc)
            result = _detect_admin_creation_anomaly("new@example.com", "boss@example.com", tmp_db)
        assert result is None

    def test_graceful_on_db_error(self, tmp_path):
        """Never raises — returns None if the DB is unreachable."""
        result = _detect_admin_creation_anomaly("x@y.com", None, str(tmp_path / "nonexistent.db"))
        # Missing table → exception caught → None
        assert result is None
