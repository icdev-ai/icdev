# CUI // SP-CTI
"""Unit tests for the PMA INT Gap Monitor Genesis reflex.

All tests use an in-memory SQLite DB — no live DB or network required.

Scenarios verified:
  1. Persistent gap > 14 days → collection requirement generated + kanban task seeded.
  2. Duplicate prevention: second reflex firing on same gap skips requirement + task.
  3. High-severity gap: requirement inserted + kanban task seeded.
  4. Medium-severity gap: requirement inserted but NO kanban task seeded.
  5. Critical gap > 30 days with no 'tasked' requirement → compliance risk created.
  6. Critical gap > 30 days WITH an existing 'tasked' requirement → no risk created.
  7. Critical gap < 30 days → no compliance risk (not old enough).
  8. Gap younger than 14 days → ignored by reflex.
  9. Empty pma_int_gaps table → reflex succeeds with zero actions.
 10. pma_int_gap_monitor registered in REFLEX_NAMES.
 11. pma_int_gap_monitor registered in reflex_registry.REGISTRY with weekly interval.
"""

import os
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")


# ---------------------------------------------------------------------------
# In-memory DB schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pma_int_gaps (
    id                    TEXT PRIMARY KEY,
    exploitation_line_id  TEXT NOT NULL,
    exploitation_line_title TEXT NOT NULL,
    discipline            TEXT NOT NULL DEFAULT 'ALL',
    gap_description       TEXT NOT NULL,
    severity              TEXT NOT NULL DEFAULT 'medium',
    status                TEXT NOT NULL DEFAULT 'open',
    contract_id           TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pma_collection_requirements (
    id               TEXT PRIMARY KEY,
    gap_id           TEXT NOT NULL,
    coverage_id      TEXT NOT NULL,
    discipline       TEXT NOT NULL,
    priority         TEXT NOT NULL DEFAULT 'medium',
    requirement_text TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'open',
    kanban_task_id   TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kanban_tasks (
    id              TEXT PRIMARY KEY,
    task_type       TEXT DEFAULT 'feature',
    title           TEXT NOT NULL,
    description     TEXT,
    status          TEXT DEFAULT 'suggested',
    priority        TEXT DEFAULT 'medium',
    target_date     TEXT,
    tags            TEXT,
    dispatch_source TEXT,
    created_at      TEXT,
    updated_at      TEXT
);
CREATE TABLE IF NOT EXISTS cpmp_risks (
    id                TEXT PRIMARY KEY,
    contract_id       TEXT NOT NULL,
    title             TEXT NOT NULL,
    category          TEXT NOT NULL,
    probability       INTEGER DEFAULT 3,
    impact            INTEGER DEFAULT 3,
    exposure          INTEGER DEFAULT 9,
    mitigation        TEXT,
    owner             TEXT,
    status            TEXT DEFAULT 'open',
    milestone_id      TEXT,
    negative_event_id TEXT,
    classification    TEXT DEFAULT 'CUI',
    tenant_id         TEXT,
    metadata          TEXT,
    created_at        TEXT,
    updated_at        TEXT
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
    """SQLite in-memory connection wrapper that mimics the ICDEV storage interface."""

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
        pass  # no-op; fixture manages lifetime

    def _really_close(self):
        self._conn.close()

    def set_security_context(self, _ctx):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _insert_gap(
    conn,
    *,
    gap_id: str = None,
    exploitation_line_id: str = "line-001",
    exploitation_line_title: str = "Alpha Target Network",
    discipline: str = "SIGINT",
    gap_description: str = "Signal collection gap on primary node.",
    severity: str = "high",
    status: str = "open",
    contract_id: str = "CONTRACT-TEST",
    created_at: str = None,
) -> str:
    gap_id = gap_id or f"gap-{uuid.uuid4().hex[:8]}"
    created_at = created_at or _ago(20)
    conn.execute(
        """INSERT INTO pma_int_gaps
           (id, exploitation_line_id, exploitation_line_title, discipline,
            gap_description, severity, status, contract_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (gap_id, exploitation_line_id, exploitation_line_title, discipline,
         gap_description, severity, status, contract_id, created_at, created_at),
    )
    conn.commit()
    return gap_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db():
    conn = _FakeConn()
    yield conn
    conn._really_close()


# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------

from tools.pma.int_gap_monitor import (
    generate_collection_requirements,
    get_persistent_gaps,
    get_untasked_critical_gaps_over_30_days,
)
import tools.genesis.reflexes.pma_int_gap_monitor as reflex_mod


# ---------------------------------------------------------------------------
# Unit tests: data-access layer
# ---------------------------------------------------------------------------

class TestGetPersistentGaps:

    def test_gap_older_than_14_days_returned(self, db):
        _insert_gap(db, created_at=_ago(20))
        gaps = get_persistent_gaps(days=14, conn=db)
        assert len(gaps) == 1

    def test_gap_younger_than_14_days_excluded(self, db):
        _insert_gap(db, created_at=_ago(5))
        gaps = get_persistent_gaps(days=14, conn=db)
        assert gaps == []

    def test_resolved_gap_excluded(self, db):
        _insert_gap(db, created_at=_ago(20), status="resolved")
        gaps = get_persistent_gaps(days=14, conn=db)
        assert gaps == []

    def test_cancelled_gap_excluded(self, db):
        _insert_gap(db, created_at=_ago(20), status="cancelled")
        gaps = get_persistent_gaps(days=14, conn=db)
        assert gaps == []

    def test_in_progress_gap_returned(self, db):
        _insert_gap(db, created_at=_ago(20), status="in_progress")
        gaps = get_persistent_gaps(days=14, conn=db)
        assert len(gaps) == 1


class TestGenerateCollectionRequirements:

    def test_new_gap_inserts_requirement(self, db):
        _insert_gap(db, exploitation_line_id="line-001", discipline="SIGINT")
        gaps = get_persistent_gaps(days=14, conn=db)
        result = generate_collection_requirements(gaps[0], db)
        assert result["inserted"] is True
        count = db.execute("SELECT COUNT(*) FROM pma_collection_requirements").fetchone()[0]
        assert count == 1

    def test_duplicate_skipped(self, db):
        """Second call for same (coverage_id, discipline) is a no-op."""
        _insert_gap(db, exploitation_line_id="line-001", discipline="SIGINT")
        gaps = get_persistent_gaps(days=14, conn=db)
        generate_collection_requirements(gaps[0], db)
        db.commit()
        result2 = generate_collection_requirements(gaps[0], db)
        assert result2["inserted"] is False
        count = db.execute("SELECT COUNT(*) FROM pma_collection_requirements").fetchone()[0]
        assert count == 1

    def test_cancelled_requirement_allows_reinsertion(self, db):
        """If the existing requirement is 'cancelled', a new one may be inserted."""
        _insert_gap(db, exploitation_line_id="line-002", discipline="HUMINT")
        gaps = get_persistent_gaps(days=14, conn=db)
        res1 = generate_collection_requirements(gaps[0], db)
        db.commit()
        # Cancel it
        db.execute(
            "UPDATE pma_collection_requirements SET status = 'cancelled' WHERE id = ?",
            (res1["requirement_id"],),
        )
        db.commit()
        res2 = generate_collection_requirements(gaps[0], db)
        assert res2["inserted"] is True
        count = db.execute("SELECT COUNT(*) FROM pma_collection_requirements").fetchone()[0]
        assert count == 2

    def test_different_discipline_inserts_separate_requirement(self, db):
        """Same exploitation line but different discipline → two requirements."""
        _insert_gap(db, gap_id="g1", exploitation_line_id="line-003", discipline="SIGINT")
        _insert_gap(db, gap_id="g2", exploitation_line_id="line-003", discipline="HUMINT")
        gaps = get_persistent_gaps(days=14, conn=db)
        for gap in gaps:
            generate_collection_requirements(gap, db)
        db.commit()
        count = db.execute("SELECT COUNT(*) FROM pma_collection_requirements").fetchone()[0]
        assert count == 2


class TestGetUntaskedCriticalGaps:

    def test_critical_gap_30_days_no_tasked_req_returned(self, db):
        gid = _insert_gap(db, severity="critical", created_at=_ago(35))
        gaps = get_persistent_gaps(days=14, conn=db)
        result = get_untasked_critical_gaps_over_30_days(db, gaps=gaps)
        assert len(result) == 1
        assert result[0]["id"] == gid

    def test_critical_gap_with_tasked_req_excluded(self, db):
        gid = _insert_gap(db, severity="critical", created_at=_ago(35),
                          exploitation_line_id="line-t")
        req_id = f"req-{uuid.uuid4().hex[:8]}"
        now = _now_iso()
        db.execute(
            "INSERT INTO pma_collection_requirements "
            "(id, gap_id, coverage_id, discipline, priority, requirement_text, "
            " status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'tasked', ?, ?)",
            (req_id, gid, "line-t", "SIGINT", "critical", "Requirement text.", now, now),
        )
        db.commit()
        gaps = get_persistent_gaps(days=14, conn=db)
        result = get_untasked_critical_gaps_over_30_days(db, gaps=gaps)
        assert result == []

    def test_high_severity_gap_30_days_excluded(self, db):
        _insert_gap(db, severity="high", created_at=_ago(35))
        gaps = get_persistent_gaps(days=14, conn=db)
        result = get_untasked_critical_gaps_over_30_days(db, gaps=gaps)
        assert result == []

    def test_critical_gap_under_30_days_excluded(self, db):
        _insert_gap(db, severity="critical", created_at=_ago(20))
        gaps = get_persistent_gaps(days=14, conn=db)
        result = get_untasked_critical_gaps_over_30_days(db, gaps=gaps)
        assert result == []


# ---------------------------------------------------------------------------
# Integration tests: full reflex run()
# ---------------------------------------------------------------------------

class TestReflexRun:

    def _gc(self, conn):
        def _fake(*a, **kw):
            return conn
        return _fake

    # -- Scenario 1: new gap → requirement + kanban task ----------------------

    def test_high_gap_inserts_requirement_and_seeds_kanban(self, db):
        """GIVEN persistent gap > 14 days (severity=high) WHEN reflex fires
        THEN one requirement inserted AND one kanban task seeded."""
        _insert_gap(db, severity="high", created_at=_ago(20))

        gc = self._gc(db)
        with patch("tools.genesis.reflexes.pma_int_gap_monitor.get_connection", gc), \
             patch("tools.genesis.reflexes.pma_int_gap_monitor._write_memory_log"):
            result = reflex_mod.run({}, None)

        assert result["success"] is True
        assert result["details"]["gaps_found"] == 1
        assert result["details"]["requirements_inserted"] == 1
        assert result["details"]["kanban_tasks_seeded"] == 1

        req = db.execute("SELECT * FROM pma_collection_requirements").fetchone()
        assert req is not None
        assert dict(req)["priority"] == "high"

        task = db.execute(
            "SELECT * FROM kanban_tasks WHERE dispatch_source = 'pma_int_gap_monitor'"
        ).fetchone()
        assert task is not None
        assert "Alpha Target Network" in dict(task)["title"]

    # -- Scenario 2: dedup — second firing is a no-op -------------------------

    def test_dedup_second_firing_is_noop(self, db):
        """GIVEN same gap on a second reflex firing WHEN reflex fires
        THEN no duplicate requirement row inserted (dedup by coverage_id + discipline)."""
        _insert_gap(db, severity="high", created_at=_ago(20))

        gc = self._gc(db)
        patches = dict(
            rgc=patch("tools.genesis.reflexes.pma_int_gap_monitor.get_connection", gc),
            mem=patch("tools.genesis.reflexes.pma_int_gap_monitor._write_memory_log"),
        )

        with patches["rgc"], patches["mem"]:
            reflex_mod.run({}, None)

        # Second firing
        with patches["rgc"], patches["mem"]:
            result = reflex_mod.run({}, None)

        assert result["success"] is True
        assert result["details"]["requirements_inserted"] == 0
        assert result["details"]["kanban_tasks_seeded"] == 0

        req_count = db.execute("SELECT COUNT(*) FROM pma_collection_requirements").fetchone()[0]
        assert req_count == 1

        task_count = db.execute(
            "SELECT COUNT(*) FROM kanban_tasks WHERE dispatch_source = 'pma_int_gap_monitor'"
        ).fetchone()[0]
        assert task_count == 1

    # -- Scenario 3: critical gap > 30 days → compliance risk -----------------

    def test_critical_gap_over_30_days_creates_compliance_risk(self, db):
        """GIVEN a critical gap older than 30 days with no tasked requirement
        WHEN the reflex fires THEN a compliance risk is created."""
        import tools.govcon.risk_manager as rm_mod

        _insert_gap(db, severity="critical", created_at=_ago(35),
                    exploitation_line_id="line-crit")

        gc = self._gc(db)

        def fake_get_db():
            db.execute("PRAGMA journal_mode=WAL")
            return db

        with patch("tools.genesis.reflexes.pma_int_gap_monitor.get_connection", gc), \
             patch.object(rm_mod, "_get_db", fake_get_db), \
             patch("tools.genesis.reflexes.pma_int_gap_monitor._write_memory_log"):
            result = reflex_mod.run({}, None)

        assert result["success"] is True
        assert result["details"]["compliance_risks_created"] == 1

        risk = db.execute(
            "SELECT * FROM cpmp_risks WHERE category = 'compliance' AND status = 'open'"
        ).fetchone()
        assert risk is not None
        r = dict(risk)
        assert r["category"] == "compliance"
        assert "Alpha Target Network" in r["title"]

    # -- Scenario 4: critical gap > 30 days WITH tasked req → no risk ---------

    def test_critical_gap_with_tasked_req_no_compliance_risk(self, db):
        """GIVEN a critical gap > 30 days WITH an existing 'tasked' requirement
        WHEN the reflex fires THEN no compliance risk is created."""
        import tools.govcon.risk_manager as rm_mod

        gid = _insert_gap(db, severity="critical", created_at=_ago(35),
                          exploitation_line_id="line-tasked")
        req_id = f"req-{uuid.uuid4().hex[:8]}"
        now = _now_iso()
        db.execute(
            "INSERT INTO pma_collection_requirements "
            "(id, gap_id, coverage_id, discipline, priority, requirement_text, "
            " status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'tasked', ?, ?)",
            (req_id, gid, "line-tasked", "SIGINT", "critical", "Requirement text.", now, now),
        )
        db.commit()

        gc = self._gc(db)

        def fake_get_db():
            return db

        with patch("tools.genesis.reflexes.pma_int_gap_monitor.get_connection", gc), \
             patch.object(rm_mod, "_get_db", fake_get_db), \
             patch("tools.genesis.reflexes.pma_int_gap_monitor._write_memory_log"):
            result = reflex_mod.run({}, None)

        assert result["success"] is True
        assert result["details"]["compliance_risks_created"] == 0

    # -- Scenario 5: medium-severity gap → requirement inserted but no task ---

    def test_medium_gap_inserts_requirement_no_kanban(self, db):
        """Medium-severity gap: requirement inserted but no kanban task."""
        _insert_gap(db, severity="medium", created_at=_ago(20))

        gc = self._gc(db)
        with patch("tools.genesis.reflexes.pma_int_gap_monitor.get_connection", gc), \
             patch("tools.genesis.reflexes.pma_int_gap_monitor._write_memory_log"):
            result = reflex_mod.run({}, None)

        assert result["details"]["requirements_inserted"] == 1
        assert result["details"]["kanban_tasks_seeded"] == 0

    # -- Scenario 6: gap younger than 14 days → ignored -----------------------

    def test_young_gap_ignored(self, db):
        """Gap created 5 days ago is below the 14-day threshold; reflex ignores it."""
        _insert_gap(db, severity="critical", created_at=_ago(5))

        gc = self._gc(db)
        with patch("tools.genesis.reflexes.pma_int_gap_monitor.get_connection", gc), \
             patch("tools.genesis.reflexes.pma_int_gap_monitor._write_memory_log"):
            result = reflex_mod.run({}, None)

        assert result["success"] is True
        assert result["details"]["gaps_found"] == 0
        assert result["details"]["requirements_inserted"] == 0

    # -- Scenario 7: empty table → success with zero actions ------------------

    def test_empty_table_returns_success(self, db):
        gc = self._gc(db)
        with patch("tools.genesis.reflexes.pma_int_gap_monitor.get_connection", gc), \
             patch("tools.genesis.reflexes.pma_int_gap_monitor._write_memory_log"):
            result = reflex_mod.run({}, None)

        assert result["success"] is True
        assert result["metric_value"] == 0
        assert result["details"]["gaps_found"] == 0
        assert result["details"]["requirements_inserted"] == 0
        assert result["details"]["kanban_tasks_seeded"] == 0
        assert result["details"]["compliance_risks_created"] == 0


# ---------------------------------------------------------------------------
# Registry / daemon registration checks
# ---------------------------------------------------------------------------

class TestRegistration:

    def test_pma_int_gap_monitor_in_reflex_names(self):
        """pma_int_gap_monitor must be listed in daemon.REFLEX_NAMES."""
        from tools.genesis.daemon import REFLEX_NAMES
        assert "pma_int_gap_monitor" in REFLEX_NAMES

    def test_pma_int_gap_monitor_in_reflex_registry(self):
        """pma_int_gap_monitor must be registered in reflex_registry.REGISTRY."""
        from tools.genesis.reflex_registry import REGISTRY
        names = [e.name for e in REGISTRY]
        assert "pma_int_gap_monitor" in names

    def test_pma_int_gap_monitor_weekly_interval(self):
        """pma_int_gap_monitor should fire weekly (interval_h = 168)."""
        from tools.genesis.reflex_registry import get
        entry = get("pma_int_gap_monitor")
        assert entry.interval_h == 168.0

    def test_pma_int_gap_monitor_implementation_status(self):
        """Reflex module must declare IMPLEMENTATION_STATUS = 'full'."""
        assert reflex_mod.IMPLEMENTATION_STATUS == "full"
