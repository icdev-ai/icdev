# CUI // SP-CTI
"""Regression tests for nav-comp-02.

The admin burst-detection query in tools/dashboard/api/admin.py previously used
the SQLite-only ``datetime(%s, %s)`` form under a bare ``except: return None``.
On PostgreSQL (the primary backend) that query raised and the except silently
disabled burst detection — an advisory security control that never ran in
production.

These tests prove:
  1. The burst query now executes against the conftest sqlite backend and
     detects seeded burst data using the Python-computed ISO cutoff form.
  2. No ``datetime(%s`` SQLite-only construct remains in the module (source scan).
  3. The error path logs the failure (caplog) instead of silently returning None.
"""

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import tools.dashboard.api.admin as admin_mod
from tools.dashboard.api.admin import (
    _ADMIN_BURST_MAX,
    _OFF_HOURS_END_UTC,
    _OFF_HOURS_START_UTC,
    _detect_admin_creation_anomaly,
)


@pytest.fixture()
def tmp_db(icdev_db):
    """Conftest-backed dashboard DB with the seeded users cleared.

    Routes get_connection through translate_sql (the real code path) because the
    path differs from ICDEV_DB_PATH — see test_admin_creation_anomaly.py.
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


def test_burst_query_executes_and_detects_seeded_data(tmp_db):
    """Python-cutoff burst query runs on the sqlite backend and returns rows."""
    now = datetime.now(timezone.utc)
    for i in range(_ADMIN_BURST_MAX):
        recent_ts = (now - timedelta(seconds=30)).isoformat()
        _insert_admin(tmp_db, f"recent{i}@example.com", created_at=recent_ts)

    biz_hour = (_OFF_HOURS_START_UTC + _OFF_HOURS_END_UTC) // 2
    with patch("tools.dashboard.api.admin.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 6, 15, biz_hour, 0, 0, tzinfo=timezone.utc)
        result = _detect_admin_creation_anomaly("new@example.com", "req@example.com", tmp_db)
    assert result == "admin_burst"


def test_old_admins_outside_window_do_not_burst(tmp_db):
    """Admins created before the look-back cutoff are not counted as a burst."""
    old_ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    for i in range(_ADMIN_BURST_MAX):
        _insert_admin(tmp_db, f"old{i}@example.com", created_at=old_ts)

    # now must be after the seeded old timestamps so the cutoff excludes them.
    biz_hour = (_OFF_HOURS_START_UTC + _OFF_HOURS_END_UTC) // 2
    with patch("tools.dashboard.api.admin.datetime") as mock_dt:
        mock_dt.now.return_value = datetime.now(timezone.utc).replace(hour=biz_hour)
        result = _detect_admin_creation_anomaly("new@example.com", "boss@example.com", tmp_db)
    assert result is None


def test_no_sqlite_only_datetime_sql_in_module():
    """Source scan: the SQLite-only datetime(%s ... form must be gone."""
    src = Path(admin_mod.__file__).read_text(encoding="utf-8")
    assert "datetime(%s" not in src, "SQLite-only datetime(%s,...) SQL must not return"
    assert "datetime('now'" not in src


def test_error_path_logs_instead_of_silent_none(tmp_path, caplog):
    """A DB failure degrades to None but is logged VISIBLY, not swallowed.

    The module logger is configured with ``propagate = False`` (see
    tools/logging/icdev_logger.get_logger), so caplog's root handler never sees
    its records. Attach caplog's handler to the module logger directly.
    """
    missing_db = str(tmp_path / "nonexistent.db")
    admin_mod.logger.addHandler(caplog.handler)
    admin_mod.logger.setLevel(logging.ERROR)
    try:
        with caplog.at_level(logging.ERROR, logger="icdev.dashboard.api.admin"):
            result = _detect_admin_creation_anomaly("x@y.com", None, missing_db)
    finally:
        admin_mod.logger.removeHandler(caplog.handler)

    assert result is None
    assert any(
        "anomaly detection failed" in rec.getMessage().lower()
        for rec in caplog.records
    ), "error path must log the failure instead of silently returning None"
    # logger.exception records the traceback → exc_info must be present.
    assert any(rec.exc_info for rec in caplog.records), "expected exception traceback in log"
