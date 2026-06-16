"""Tests for heartbeat kanban stale-scheduler detection (acw-sched-02).

Verifies that check_kanban_stale():
  - Returns ok when the reflex ran recently
  - Returns warning + fires wakeup when last_run_at is stale
  - Returns ok when the reflex is disabled or circuit breaker is open
  - Returns ok when no row exists in genesis_reflex_state
"""
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.monitor.heartbeat_daemon import (  # noqa: E402
    _trigger_kanban_wakeup,
    check_kanban_stale,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_db() -> Path:
    """Create a temp SQLite DB with genesis_reflex_state + heartbeat_checks."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE genesis_reflex_state (
            reflex_name          TEXT PRIMARY KEY,
            enabled              INTEGER NOT NULL DEFAULT 1,
            last_run_at          TEXT,
            last_duration_ms     INTEGER,
            last_status          TEXT,
            failure_count        INTEGER DEFAULT 0,
            circuit_breaker_open INTEGER DEFAULT 0,
            updated_at           TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS heartbeat_checks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            check_type   TEXT    NOT NULL,
            last_run     TEXT    NOT NULL,
            next_run     TEXT    NOT NULL,
            status       TEXT    NOT NULL DEFAULT 'pending',
            result_summary TEXT,
            items_found  INTEGER DEFAULT 0,
            duration_ms  INTEGER DEFAULT 0,
            created_at   TEXT    DEFAULT (datetime('now'))
        )"""
    )
    conn.commit()
    conn.close()
    return db_path


def _insert_kanban_state(
    db_path: Path,
    last_run_at: str,
    enabled: int = 1,
    circuit_open: int = 0,
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO genesis_reflex_state "
        "(reflex_name, last_run_at, enabled, circuit_breaker_open) VALUES (?,?,?,?)",
        ("kanban", last_run_at, enabled, circuit_open),
    )
    conn.commit()
    conn.close()


def _ts(minutes_ago: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


@contextmanager
def _patch_get_conn(db_path: Path):
    """Patch heartbeat_daemon._get_connection to use the test SQLite file.

    This bypasses the storage backend (which may point at PostgreSQL) so tests
    are self-contained.
    """
    def _fake(path=None):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn

    with patch("tools.monitor.heartbeat_daemon._get_connection", side_effect=_fake):
        yield


# ---------------------------------------------------------------------------
# Tests: check_kanban_stale — ok paths
# ---------------------------------------------------------------------------
class TestCheckKanbanStaleOk(unittest.TestCase):
    def test_no_row_returns_ok(self):
        db = _make_db()
        try:
            with _patch_get_conn(db):
                result = check_kanban_stale(config={"stale_threshold_minutes": 5}, db_path=db)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["count"], 0)
        finally:
            db.unlink(missing_ok=True)

    def test_fresh_last_run_returns_ok(self):
        db = _make_db()
        try:
            _insert_kanban_state(db, _ts(2))  # 2 minutes ago — not stale
            with _patch_get_conn(db):
                result = check_kanban_stale(config={"stale_threshold_minutes": 5}, db_path=db)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["count"], 0)
        finally:
            db.unlink(missing_ok=True)

    def test_disabled_reflex_returns_ok(self):
        db = _make_db()
        try:
            _insert_kanban_state(db, _ts(10), enabled=0)
            with _patch_get_conn(db):
                result = check_kanban_stale(config={"stale_threshold_minutes": 5}, db_path=db)
            self.assertEqual(result["status"], "ok")
        finally:
            db.unlink(missing_ok=True)

    def test_circuit_breaker_open_returns_ok(self):
        db = _make_db()
        try:
            _insert_kanban_state(db, _ts(10), circuit_open=1)
            with _patch_get_conn(db):
                result = check_kanban_stale(config={"stale_threshold_minutes": 5}, db_path=db)
            self.assertEqual(result["status"], "ok")
        finally:
            db.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Tests: check_kanban_stale — stale → wakeup
# ---------------------------------------------------------------------------
class TestCheckKanbanStaleTriggersWakeup(unittest.TestCase):
    def test_stale_10m_triggers_wakeup_via_api(self):
        """Artificially age last_run_at by 10 minutes → wakeup fires."""
        db = _make_db()
        try:
            _insert_kanban_state(db, _ts(10))  # 10 minutes ago — stale

            with _patch_get_conn(db):
                with patch(
                    "tools.monitor.heartbeat_daemon._trigger_kanban_wakeup",
                    return_value="triggered_via_api",
                ) as mock_wake:
                    result = check_kanban_stale(
                        config={"stale_threshold_minutes": 5}, db_path=db
                    )

            self.assertEqual(result["status"], "warning")
            self.assertEqual(result["count"], 1)
            self.assertEqual(len(result["items"]), 1)
            item = result["items"][0]
            self.assertEqual(item["reflex"], "kanban")
            self.assertGreaterEqual(item["elapsed_minutes"], 9.9)
            self.assertEqual(item["wakeup"], "triggered_via_api")
            mock_wake.assert_called_once()
        finally:
            db.unlink(missing_ok=True)

    def test_stale_exactly_at_threshold_triggers(self):
        """Stale just past the threshold boundary triggers wakeup."""
        db = _make_db()
        try:
            _insert_kanban_state(db, _ts(5.1))
            with _patch_get_conn(db):
                with patch(
                    "tools.monitor.heartbeat_daemon._trigger_kanban_wakeup",
                    return_value="triggered_via_api",
                ):
                    result = check_kanban_stale(
                        config={"stale_threshold_minutes": 5}, db_path=db
                    )
            self.assertEqual(result["status"], "warning")
        finally:
            db.unlink(missing_ok=True)

    def test_configurable_threshold_respected(self):
        """Custom stale_threshold_minutes is respected."""
        db = _make_db()
        try:
            _insert_kanban_state(db, _ts(8))  # 8 minutes ago

            # threshold=10 → not yet stale
            with _patch_get_conn(db):
                result = check_kanban_stale(
                    config={"stale_threshold_minutes": 10}, db_path=db
                )
            self.assertEqual(result["status"], "ok")

            # threshold=5 → stale
            with _patch_get_conn(db):
                with patch(
                    "tools.monitor.heartbeat_daemon._trigger_kanban_wakeup",
                    return_value="triggered_direct",
                ):
                    result2 = check_kanban_stale(
                        config={"stale_threshold_minutes": 5}, db_path=db
                    )
            self.assertEqual(result2["status"], "warning")
        finally:
            db.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Tests: _trigger_kanban_wakeup
# ---------------------------------------------------------------------------
class TestTriggerKanbanWakeup(unittest.TestCase):
    def test_api_success_returns_triggered_via_api(self):
        with patch("urllib.request.urlopen", return_value=MagicMock()):
            result = _trigger_kanban_wakeup()
        self.assertEqual(result, "triggered_via_api")

    def test_api_failure_falls_back_to_direct(self):
        import urllib.error

        mock_kanban = MagicMock()
        mock_kanban.run = MagicMock(return_value=None)

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("conn refused")):
            with patch.dict("sys.modules", {"tools.genesis.reflexes.kanban": mock_kanban}):
                result = _trigger_kanban_wakeup()

        self.assertEqual(result, "triggered_direct")
        mock_kanban.run.assert_called_once_with({}, None)

    def test_api_and_import_both_fail_returns_wakeup_failed(self):
        import urllib.error

        # Setting a module to None in sys.modules causes ImportError on import
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("conn refused")):
            with patch.dict("sys.modules", {"tools.genesis.reflexes.kanban": None}):
                result = _trigger_kanban_wakeup()

        self.assertIn("wakeup_failed", result)


if __name__ == "__main__":
    unittest.main()
