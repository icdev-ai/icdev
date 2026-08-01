# CUI // SP-CTI
"""Unit tests for the PMA Credential Monitor Genesis reflex.

All tests use an in-memory SQLite DB injected directly into the functions
under test — no live DB or network required.

Scenarios verified:
  1. CRITICAL expiry (≤ 30 days) → pma_credential_alerts row inserted +
     kanban task seeded with correct title.
  2. Alert dedup — a second run for the same person+type+expiry is a no-op.
  3. WATCH expiry (31–90 days) → alert inserted but NO kanban task.
  4. Already-expired credential → ignored (not in the 0–90 day window).
  5. Key person with no backup → cpmp_risks entry with category='staffing'
     created if not already open.
  6. Key person risk dedup — second run for the same person is a no-op.
  7. Key person WITH a backup → not flagged as SPOF.
  8. run() returns success=True and correct metric_value on a happy path.
"""

import os
import sqlite3
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# Repo root on path (conftest.py already does this, but be explicit)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")


# ---------------------------------------------------------------------------
# Helpers — minimal SQLite DB fixture
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pma_personnel (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    role             TEXT,
    contract_id      TEXT,
    poly_expiry      TEXT,
    secret_expiry    TEXT,
    ts_expiry        TEXT,
    backup_person_id TEXT,
    created_at       TEXT,
    updated_at       TEXT
);
CREATE TABLE IF NOT EXISTS pma_credential_alerts (
    id             TEXT PRIMARY KEY,
    person_id      TEXT NOT NULL,
    alert_type     TEXT NOT NULL,
    expiry_date    TEXT NOT NULL,
    days_remaining INTEGER NOT NULL,
    severity       TEXT NOT NULL,
    kanban_task_id TEXT,
    created_at     TEXT NOT NULL,
    UNIQUE (person_id, alert_type, expiry_date)
);
CREATE TABLE IF NOT EXISTS kanban_tasks (
    id            TEXT PRIMARY KEY,
    task_type     TEXT DEFAULT 'chore',
    title         TEXT NOT NULL,
    description   TEXT,
    status        TEXT DEFAULT 'suggested',
    priority      TEXT DEFAULT 'high',
    target_date   TEXT,
    tags          TEXT,
    dispatch_source TEXT,
    created_at    TEXT,
    updated_at    TEXT
);
CREATE TABLE IF NOT EXISTS cpmp_risks (
    id               TEXT PRIMARY KEY,
    contract_id      TEXT NOT NULL,
    title            TEXT NOT NULL,
    category         TEXT NOT NULL,
    probability      INTEGER DEFAULT 3,
    impact           INTEGER DEFAULT 3,
    exposure         INTEGER DEFAULT 9,
    mitigation       TEXT,
    owner            TEXT,
    status           TEXT DEFAULT 'open',
    milestone_id     TEXT,
    negative_event_id TEXT,
    classification   TEXT DEFAULT 'CUI',
    tenant_id        TEXT,
    metadata         TEXT,
    created_at       TEXT,
    updated_at       TEXT
);
CREATE TABLE IF NOT EXISTS audit_trail (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT,
    actor      TEXT,
    action     TEXT,
    details    TEXT,
    session_id TEXT
);
"""


class _FakeConn:
    """Thin wrapper around sqlite3.Connection that mimics the ICDEV conn interface.

    close() is a no-op so the reflex's conn.close() call doesn't destroy the
    shared test connection; the fixture disposes of it after the test.
    """

    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)
        # Route execute() through the REAL storage wrapper. Runtime SQL is
        # authored for PostgreSQL (%s placeholders, per CLAUDE.md); the wrapper
        # is what translates them to SQLite's ?. Passing SQL straight to a raw
        # sqlite3 connection makes every %s a `near "%": syntax error`, which the
        # reflexes swallow -- so they silently seeded nothing.
        from tools.db.storage import StorageConnection

        self._storage = StorageConnection(self._conn, "sqlite")

    def execute(self, sql, params=()):
        return self._storage.execute(sql, params)

    def executescript(self, sql):
        return self._conn.executescript(sql)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        pass  # no-op — fixture manages lifetime

    def _really_close(self):
        """Called by the fixture to actually release the connection."""
        self._conn.close()

    def set_security_context(self, _ctx):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _make_conn():
    return _FakeConn()


def _insert_person(conn, pid, name, role=None, contract_id=None,
                   poly_expiry=None, secret_expiry=None, ts_expiry=None,
                   backup_person_id=None):
    conn.execute(
        """INSERT INTO pma_personnel
           (id, name, role, contract_id, poly_expiry, secret_expiry, ts_expiry,
            backup_person_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
        (pid, name, role, contract_id, poly_expiry, secret_expiry, ts_expiry, backup_person_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Import modules under test
# ---------------------------------------------------------------------------

from tools.pma.credential_monitor import (
    get_expiring_credentials,
    get_key_person_dependencies,
)
import tools.genesis.reflexes.pma_credential_monitor as reflex_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db():
    """Yield a fresh in-memory DB and close it after the test."""
    conn = _make_conn()
    yield conn
    conn._really_close()


# ---------------------------------------------------------------------------
# Tests: get_expiring_credentials
# ---------------------------------------------------------------------------

class TestGetExpiringCredentials:

    def test_critical_poly_expiry_returned(self, db):
        """A poly_expiry 25 days from now is returned with severity=critical."""
        pid = str(uuid.uuid4())
        expiry = (date.today() + timedelta(days=25)).isoformat()
        _insert_person(db, pid, "Alice", poly_expiry=expiry)

        results = get_expiring_credentials(days=90, conn=db)
        assert len(results) == 1
        r = results[0]
        assert r["person_id"] == pid
        assert r["alert_type"] == "poly_expiry"
        assert r["expiry_date"] == expiry
        assert r["days_remaining"] == 25
        assert r["severity"] == "critical"

    def test_watch_secret_expiry_returned(self, db):
        """A secret_expiry 75 days from now is returned with severity=watch."""
        pid = str(uuid.uuid4())
        expiry = (date.today() + timedelta(days=75)).isoformat()
        _insert_person(db, pid, "Bob", secret_expiry=expiry)

        results = get_expiring_credentials(days=90, conn=db)
        assert len(results) == 1
        assert results[0]["severity"] == "watch"

    def test_already_expired_ignored(self, db):
        """A credential that expired yesterday is not returned."""
        pid = str(uuid.uuid4())
        expiry = (date.today() - timedelta(days=1)).isoformat()
        _insert_person(db, pid, "Carol", poly_expiry=expiry)

        results = get_expiring_credentials(days=90, conn=db)
        assert results == []

    def test_far_future_expiry_ignored(self, db):
        """A credential expiring in 200 days is not returned when days=90."""
        pid = str(uuid.uuid4())
        expiry = (date.today() + timedelta(days=200)).isoformat()
        _insert_person(db, pid, "Dave", poly_expiry=expiry)

        results = get_expiring_credentials(days=90, conn=db)
        assert results == []

    def test_multiple_credential_types_same_person(self, db):
        """A person with two expiring credentials generates two entries."""
        pid = str(uuid.uuid4())
        poly = (date.today() + timedelta(days=20)).isoformat()
        secret = (date.today() + timedelta(days=50)).isoformat()
        _insert_person(db, pid, "Eve", poly_expiry=poly, secret_expiry=secret)

        results = get_expiring_credentials(days=90, conn=db)
        assert len(results) == 2
        types = {r["alert_type"] for r in results}
        assert types == {"poly_expiry", "secret_expiry"}


# ---------------------------------------------------------------------------
# Tests: get_key_person_dependencies
# ---------------------------------------------------------------------------

class TestGetKeyPersonDependencies:

    def test_no_backup_is_spof(self, db):
        """A person with no backup_person_id is returned as a SPOF."""
        pid = str(uuid.uuid4())
        _insert_person(db, pid, "Frank", role="Program Manager", backup_person_id=None)

        spofs = get_key_person_dependencies(conn=db)
        assert len(spofs) == 1
        assert spofs[0]["person_id"] == pid
        assert spofs[0]["backup_person_id"] is None

    def test_person_with_backup_not_spof(self, db):
        """A person who has a backup is NOT returned as a SPOF."""
        pid = str(uuid.uuid4())
        backup_id = str(uuid.uuid4())
        _insert_person(db, backup_id, "Grace", role="Deputy PM", backup_person_id=None)
        _insert_person(db, pid, "Henry", role="Program Manager", backup_person_id=backup_id)

        spofs = get_key_person_dependencies(conn=db)
        # Only Grace (no backup) is a SPOF; Henry has Grace as backup
        assert len(spofs) == 1
        assert spofs[0]["person_id"] == backup_id

    def test_empty_table_returns_empty(self, db):
        spofs = get_key_person_dependencies(conn=db)
        assert spofs == []


# ---------------------------------------------------------------------------
# Tests: full reflex run() — end-to-end with mocked get_connection
# ---------------------------------------------------------------------------

class TestReflexRun:
    """Full reflex execution with DB injected via mock."""

    def _patch_get_connection(self, conn):
        """Return a context-manager patcher that stubs get_connection everywhere."""
        def _fake_get_connection(*args, **kwargs):
            return conn
        return _fake_get_connection

    def test_critical_expiry_inserts_alert_and_kanban_task(self, db):
        """
        GIVEN a person with poly_expiry 25 days from now and no existing alert
        WHEN the reflex fires
        THEN a pma_credential_alerts row is inserted AND a kanban task is seeded.
        """
        pid = str(uuid.uuid4())
        backup_id = "backup-001"
        expiry = (date.today() + timedelta(days=25)).isoformat()
        # Give the person a backup so SPOF pass doesn't invoke risk_manager
        _insert_person(db, pid, "Irene", poly_expiry=expiry, backup_person_id=backup_id)

        get_conn_patch = self._patch_get_connection(db)

        with patch("tools.pma.credential_monitor.get_connection", get_conn_patch), \
             patch("tools.genesis.reflexes.pma_credential_monitor.get_connection", get_conn_patch), \
             patch("tools.genesis.reflexes.pma_credential_monitor._write_memory_log"):

            result = reflex_mod.run({}, None)

        assert result["success"] is True
        assert result["details"]["alerts_inserted"] == 1
        assert result["details"]["kanban_tasks_seeded"] == 1

        # Verify the alert row
        alert = db.execute(
            "SELECT * FROM pma_credential_alerts WHERE person_id = ?", (pid,)
        ).fetchone()
        assert alert is not None
        assert dict(alert)["alert_type"] == "poly_expiry"
        assert dict(alert)["severity"] == "critical"

        # Verify the kanban task title
        task = db.execute(
            "SELECT * FROM kanban_tasks WHERE dispatch_source = 'pma_credential_monitor'"
        ).fetchone()
        assert task is not None
        title = dict(task)["title"]
        assert "URGENT: renew" in title
        assert "Irene" in title
        assert expiry in title

    def test_alert_dedup_second_run_is_noop(self, db):
        """Second run for the same expiry does not insert duplicate alert or task."""
        pid = str(uuid.uuid4())
        expiry = (date.today() + timedelta(days=25)).isoformat()
        # Give person a backup so SPOF pass is skipped
        _insert_person(db, pid, "Jack", poly_expiry=expiry, backup_person_id="backup-002")

        get_conn_patch = self._patch_get_connection(db)
        patches = dict(
            pma_gc=patch("tools.pma.credential_monitor.get_connection", get_conn_patch),
            ref_gc=patch("tools.genesis.reflexes.pma_credential_monitor.get_connection", get_conn_patch),
            mem=patch("tools.genesis.reflexes.pma_credential_monitor._write_memory_log"),
        )
        with patches["pma_gc"], patches["ref_gc"], patches["mem"]:
            reflex_mod.run({}, None)

        with patches["pma_gc"], patches["ref_gc"], patches["mem"]:
            result = reflex_mod.run({}, None)

        assert result["success"] is True
        assert result["details"]["alerts_inserted"] == 0
        assert result["details"]["kanban_tasks_seeded"] == 0

        count = db.execute("SELECT COUNT(*) FROM pma_credential_alerts").fetchone()[0]
        assert count == 1

    def test_watch_expiry_no_kanban_task(self, db):
        """Watch-severity (31–90 days) creates an alert but NOT a kanban task."""
        pid = str(uuid.uuid4())
        expiry = (date.today() + timedelta(days=60)).isoformat()
        # Give person a backup so SPOF pass is skipped
        _insert_person(db, pid, "Kate", poly_expiry=expiry, backup_person_id="backup-003")

        get_conn_patch = self._patch_get_connection(db)
        with patch("tools.pma.credential_monitor.get_connection", get_conn_patch), \
             patch("tools.genesis.reflexes.pma_credential_monitor.get_connection", get_conn_patch), \
             patch("tools.genesis.reflexes.pma_credential_monitor._write_memory_log"):

            result = reflex_mod.run({}, None)

        assert result["details"]["alerts_inserted"] == 1
        assert result["details"]["kanban_tasks_seeded"] == 0

    def test_spof_person_seeds_risk_entry(self, db):
        """
        GIVEN a key_person with no backup
        WHEN the reflex fires
        THEN a cpmp_risks entry with category='staffing' is created.
        """
        import tools.govcon.risk_manager as rm_mod

        pid = str(uuid.uuid4())
        _insert_person(db, pid, "Leo", role="CTO", contract_id="CONTRACT-001",
                       backup_person_id=None)

        get_conn_patch = self._patch_get_connection(db)

        def fake_get_db():
            db.execute("PRAGMA journal_mode=WAL")
            return db

        with patch("tools.pma.credential_monitor.get_connection", get_conn_patch), \
             patch("tools.genesis.reflexes.pma_credential_monitor.get_connection", get_conn_patch), \
             patch.object(rm_mod, "_get_db", fake_get_db), \
             patch("tools.genesis.reflexes.pma_credential_monitor._write_memory_log"):

            result = reflex_mod.run({}, None)

        assert result["success"] is True
        assert result["details"]["risk_entries_created"] == 1

        risk = db.execute(
            "SELECT * FROM cpmp_risks WHERE contract_id = ? AND status = 'open'",
            ("CONTRACT-001",),
        ).fetchone()
        assert risk is not None
        r = dict(risk)
        assert r["category"] == "staffing"
        assert "Leo" in r["title"]

    def test_spof_risk_dedup(self, db):
        """Second run for the same SPOF person does not create a duplicate risk."""
        import tools.govcon.risk_manager as rm_mod

        pid = str(uuid.uuid4())
        _insert_person(db, pid, "Mia", role="Lead Architect", contract_id="CONTRACT-002",
                       backup_person_id=None)

        def fake_get_db():
            return db

        get_conn_patch = self._patch_get_connection(db)
        ctx_patches = dict(
            pma_gc=patch("tools.pma.credential_monitor.get_connection", get_conn_patch),
            ref_gc=patch("tools.genesis.reflexes.pma_credential_monitor.get_connection", get_conn_patch),
            rm_db=patch.object(rm_mod, "_get_db", fake_get_db),
            mem=patch("tools.genesis.reflexes.pma_credential_monitor._write_memory_log"),
        )
        with ctx_patches["pma_gc"], ctx_patches["ref_gc"], ctx_patches["rm_db"], ctx_patches["mem"]:
            reflex_mod.run({}, None)

        with ctx_patches["pma_gc"], ctx_patches["ref_gc"], ctx_patches["rm_db"], ctx_patches["mem"]:
            result = reflex_mod.run({}, None)

        assert result["success"] is True
        assert result["details"]["risk_entries_created"] == 0
        count = db.execute(
            "SELECT COUNT(*) FROM cpmp_risks WHERE status='open'"
        ).fetchone()[0]
        assert count == 1

    def test_no_personnel_returns_success(self, db):
        """Empty pma_personnel table → reflex succeeds with zero actions."""
        get_conn_patch = self._patch_get_connection(db)
        with patch("tools.pma.credential_monitor.get_connection", get_conn_patch), \
             patch("tools.genesis.reflexes.pma_credential_monitor.get_connection", get_conn_patch), \
             patch("tools.genesis.reflexes.pma_credential_monitor._write_memory_log"):

            result = reflex_mod.run({}, None)

        assert result["success"] is True
        assert result["metric_value"] == 0
        assert result["details"]["alerts_inserted"] == 0
        assert result["details"]["kanban_tasks_seeded"] == 0
        assert result["details"]["risk_entries_created"] == 0

    def test_pma_credential_monitor_in_reflex_names(self):
        """Verify pma_credential_monitor is registered in REFLEX_NAMES."""
        from tools.genesis.daemon import REFLEX_NAMES
        assert "pma_credential_monitor" in REFLEX_NAMES
