#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for append-only audit trail logic.

Verifies:
- Audit logs are created (audit_trail + cross_agency_transfers)
- Audit logs are immutable (UPDATE / DELETE rejected or ignored)
- Cross-agency transfer data is correctly captured
- INSERT always succeeds with the correct schema
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit.audit_logger import log_event, atomic_log_event  # noqa: E402
from tools.audit.cross_agency_transfer_logger import (  # noqa: E402
    CrossAgencyTransferLogger,
    query_by_transfer_id,
    _VALID_EVENT_TYPES,
)

_MODULE_CAT = "tools.audit.cross_agency_transfer_logger"
_MODULE_AUDIT = "tools.audit.audit_logger"

# Minimal schema for unit-test isolation (matches conftest.py subset)
_UNIT_SCHEMA = """
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

CREATE TABLE IF NOT EXISTS cross_agency_transfers (
    id                  TEXT PRIMARY KEY,
    transfer_id         TEXT NOT NULL,
    event_type          TEXT NOT NULL CHECK(event_type IN (
                            'initiated', 'completed', 'failed', 'rejected')),
    source_agency       TEXT NOT NULL,
    target_agency       TEXT NOT NULL,
    data_type           TEXT,
    data_classification TEXT NOT NULL DEFAULT 'CUI',
    actor               TEXT NOT NULL DEFAULT '',
    project_id          TEXT,
    bytes_transferred   INTEGER,
    checksum            TEXT,
    duration_ms         INTEGER,
    rejection_reason    TEXT,
    error_code          TEXT,
    details             TEXT,
    occurred_at         TEXT NOT NULL
);

-- Enforce append-only immutability at the DB layer for testing
CREATE TRIGGER IF NOT EXISTS trg_audit_trail_no_update
    BEFORE UPDATE ON audit_trail
BEGIN
    SELECT RAISE(ABORT, 'audit_trail is append-only: UPDATE forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_trail_no_delete
    BEFORE DELETE ON audit_trail
BEGIN
    SELECT RAISE(ABORT, 'audit_trail is append-only: DELETE forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_cross_agency_no_update
    BEFORE UPDATE ON cross_agency_transfers
BEGIN
    SELECT RAISE(ABORT, 'cross_agency_transfers is append-only: UPDATE forbidden');
END;

CREATE TRIGGER IF NOT EXISTS trg_cross_agency_no_delete
    BEFORE DELETE ON cross_agency_transfers
BEGIN
    SELECT RAISE(ABORT, 'cross_agency_transfers is append-only: DELETE forbidden');
END;
"""


@pytest.fixture
def db(tmp_path):
    """Temporary SQLite DB with both audit tables and append-only triggers."""
    path = tmp_path / "audit_unit.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_UNIT_SCHEMA)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def audit_conn(db):
    """Connection factory patched into audit_logger."""

    def _make():
        c = sqlite3.connect(str(db))
        c.row_factory = sqlite3.Row
        return c

    with patch(f"{_MODULE_AUDIT}.get_connection", side_effect=_make):
        yield _make


@pytest.fixture
def cat_logger(db):
    """CrossAgencyTransferLogger patched to the temp DB."""

    def _make():
        c = sqlite3.connect(str(db))
        c.row_factory = sqlite3.Row
        return c

    with patch(f"{_MODULE_CAT}.get_connection", side_effect=_make):
        yield CrossAgencyTransferLogger(), _make


# ---------------------------------------------------------------------------
# Audit trail creation
# ---------------------------------------------------------------------------


class TestAuditTrailCreation:
    """Verify that audit entries are created with the correct schema."""

    def test_log_event_creates_audit_entry(self, db, audit_conn):
        entry_id = log_event(
            event_type="security_scan",
            actor="alice",
            action="Ran SAST on project foo",
            project_id="proj-42",
            details={"tool": "bandit", "findings": 3},
            affected_files=["src/main.py"],
            classification="CUI",
            ip_address="10.0.0.1",
            session_id="sess-123",
            db_path=db,
        )
        assert entry_id > 0
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM audit_trail WHERE id=?", (entry_id,)).fetchone()
        conn.close()
        assert row is not None
        assert row["event_type"] == "security_scan"
        assert row["actor"] == "alice"
        assert row["action"] == "Ran SAST on project foo"
        assert row["project_id"] == "proj-42"
        assert json.loads(row["details"])["tool"] == "bandit"
        assert json.loads(row["affected_files"]) == ["src/main.py"]
        assert row["classification"] == "CUI"
        assert row["ip_address"] == "10.0.0.1"
        assert row["session_id"] == "sess-123"

    def test_log_event_with_conn_reuses_connection(self, db, audit_conn):
        """When conn is passed in, log_event must not close it."""
        conn = audit_conn()
        entry_id = log_event(
            event_type="test_executed",
            actor="ci",
            action="pytest run",
            conn=conn,
        )
        assert entry_id > 0
        # Connection should still be usable
        row = conn.execute("SELECT * FROM audit_trail WHERE id=?", (entry_id,)).fetchone()
        assert row is not None
        conn.close()

    def test_atomic_log_event_persists(self, db, audit_conn):
        result = atomic_log_event(
            event_type="deployment_initiated",
            actor="deploy-bot",
            action="Deploy to staging",
            project_id="proj-99",
            db_path=db,
        )
        assert result["status"] == "persisted"
        assert result["entry_id"] > 0

    def test_atomic_log_event_fallback_on_failure(self, tmp_path):
        """If DB is missing, atomic_log_event falls back to JSONL."""
        bad_db = tmp_path / "nonexistent.db"
        fallback = tmp_path / "fallback.jsonl"
        result = atomic_log_event(
            event_type="pir_alert_generated",
            actor="monitor",
            action="PIR alert",
            db_path=bad_db,
            fallback_path=fallback,
        )
        assert result["status"] == "fallback"
        assert fallback.exists()

    def test_invalid_event_type_rejected(self, db):
        with pytest.raises(ValueError, match="Invalid event_type"):
            log_event(
                event_type="not_a_real_event",
                actor="alice",
                action="bad",
                db_path=db,
            )


# ---------------------------------------------------------------------------
# Cross-agency transfer data capture
# ---------------------------------------------------------------------------


class TestCrossAgencyTransferCapture:
    """Verify cross-agency transfer events capture all required data."""

    def test_all_event_types_generate_records(self, cat_logger, db):
        cat, _make = cat_logger
        ids = []
        ids.append(cat.log_initiated("t1", "DoD", "DHS", "intel", "alice"))
        ids.append(cat.log_completed("t1", "DoD", "DHS", "alice", bytes_transferred=1024, checksum="abc", duration_ms=100))
        ids.append(cat.log_failed("t1", "DoD", "DHS", "alice", rejection_reason="policy", error_code="E403"))
        ids.append(cat.log_rejected("t1", "DoD", "DHS", "reviewer", "policy violation"))
        for eid in ids:
            assert len(eid) == 36  # UUID

        conn = sqlite3.connect(str(db))
        n = conn.execute("SELECT COUNT(*) FROM cross_agency_transfers").fetchone()[0]
        conn.close()
        assert n == 4

    def test_fields_correctly_captured(self, cat_logger, db):
        cat, _make = cat_logger
        eid = cat.log_initiated(
            transfer_id="xfer-001",
            source_agency="NSA",
            target_agency="CIA",
            data_type="signals",
            actor="bob",
            data_classification="SECRET",
            project_id="proj-black",
            details={"protocol": "TLS1.3"},
        )
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM cross_agency_transfers WHERE id=?", (eid,)).fetchone()
        conn.close()
        assert row["transfer_id"] == "xfer-001"
        assert row["source_agency"] == "NSA"
        assert row["target_agency"] == "CIA"
        assert row["data_type"] == "signals"
        assert row["actor"] == "bob"
        assert row["data_classification"] == "SECRET"
        assert row["project_id"] == "proj-black"
        assert json.loads(row["details"])["protocol"] == "TLS1.3"
        assert row["event_type"] == "initiated"

    def test_query_by_transfer_id_returns_ordered_rows(self, cat_logger, db):
        cat, _make = cat_logger
        tid = "xfer-002"
        cat.log_initiated(tid, "A", "B", "d", "u")
        cat.log_completed(tid, "A", "B", "u")
        with patch(f"{_MODULE_CAT}.get_connection", side_effect=_make):
            rows = query_by_transfer_id(tid)
        assert len(rows) == 2
        assert rows[0]["event_type"] == "completed"  # newest first
        assert rows[1]["event_type"] == "initiated"

    def test_event_type_constraint_rejects_invalid(self, db):
        conn = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO cross_agency_transfers (id,transfer_id,event_type,source_agency,target_agency,actor,data_classification,occurred_at) VALUES (?,?,?,?,?,?,?,?)",
                ("x", "t", "bogus", "A", "B", "u", "CUI", "2026-01-01T00:00:00+00:00"),
            )
        conn.close()


# ---------------------------------------------------------------------------
# Append-only immutability
# ---------------------------------------------------------------------------


class TestAppendOnlyImmutability:
    """Verify that UPDATE and DELETE are rejected or ignored on audit tables."""

    def test_audit_trail_update_rejected(self, db, audit_conn):
        entry_id = log_event(
            event_type="config_changed",
            actor="admin",
            action="Changed setting",
            db_path=db,
        )
        conn = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.OperationalError, match="append-only.*UPDATE forbidden"):
            conn.execute("UPDATE audit_trail SET actor='attacker' WHERE id=?", (entry_id,))
        conn.close()

    def test_audit_trail_delete_rejected(self, db, audit_conn):
        entry_id = log_event(
            event_type="config_changed",
            actor="admin",
            action="Changed setting",
            db_path=db,
        )
        conn = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.OperationalError, match="append-only.*DELETE forbidden"):
            conn.execute("DELETE FROM audit_trail WHERE id=?", (entry_id,))
        conn.close()

    def test_cross_agency_update_rejected(self, cat_logger, db):
        cat, _make = cat_logger
        eid = cat.log_initiated("t-up", "A", "B", "d", "u")
        conn = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.OperationalError, match="append-only.*UPDATE forbidden"):
            conn.execute("UPDATE cross_agency_transfers SET actor='attacker' WHERE id=?", (eid,))
        conn.close()

    def test_cross_agency_delete_rejected(self, cat_logger, db):
        cat, _make = cat_logger
        eid = cat.log_initiated("t-del", "A", "B", "d", "u")
        conn = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.OperationalError, match="append-only.*DELETE forbidden"):
            conn.execute("DELETE FROM cross_agency_transfers WHERE id=?", (eid,))
        conn.close()

    def test_logger_has_no_update_or_delete_methods(self):
        cat = CrossAgencyTransferLogger()
        public = {m for m in dir(cat) if not m.startswith("_")}
        for verb in ("update", "delete", "remove", "patch", "modify"):
            assert not any(verb in m.lower() for m in public), f"public method contains '{verb}': {public}"

    def test_insert_always_succeeds_with_valid_schema(self, db, audit_conn):
        """Valid INSERTs must never fail when schema and data are correct."""
        for ev in ("security_scan", "compliance_check", "agent_task_completed"):
            entry_id = log_event(
                event_type=ev,
                actor="test",
                action="test action",
                db_path=db,
            )
            assert entry_id > 0
        conn = sqlite3.connect(str(db))
        n = conn.execute("SELECT COUNT(*) FROM audit_trail").fetchone()[0]
        conn.close()
        assert n == 3


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    """Verify that the tables enforce the expected schema constraints."""

    def test_audit_trail_missing_required_fields_rejected(self, db):
        conn = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO audit_trail (project_id) VALUES ('x')")
        conn.close()

    def test_cross_agency_missing_required_fields_rejected(self, db):
        conn = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO cross_agency_transfers (id) VALUES ('x')")
        conn.close()

    def test_cross_agency_default_classification_is_cui(self, cat_logger, db):
        cat, _make = cat_logger
        eid = cat.log_initiated("t-cls", "A", "B", "d", "u")
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT data_classification FROM cross_agency_transfers WHERE id=?", (eid,)).fetchone()
        conn.close()
        assert row["data_classification"] == "CUI"

    def test_cross_agency_valid_event_types_accepted(self, cat_logger, db):
        cat, _make = cat_logger
        for ev in _VALID_EVENT_TYPES:
            if ev == "initiated":
                eid = cat.log_initiated(f"t-{ev}", "A", "B", "d", "u")
            elif ev == "completed":
                eid = cat.log_completed(f"t-{ev}", "A", "B", "u")
            elif ev == "failed":
                eid = cat.log_failed(f"t-{ev}", "A", "B", "u")
            else:  # rejected
                eid = cat.log_rejected(f"t-{ev}", "A", "B", "r", "reason")
            assert len(eid) == 36
