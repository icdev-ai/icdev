# CUI // SP-CTI
"""Regression tests for tools.audit.cross_agency_transfer_logger.

Covers:
- Basic insert behaviour for all event types
- SQL injection vectors in every user-controlled field are stored verbatim
  (parameterised queries prevent execution; no error is raised, no extra rows
  appear, and the injected string is retrieved unchanged)
- query_by_transfer_id uses parameterised queries — injection payloads in the
  transfer_id argument do not return unintended rows
- append-only guarantee: no UPDATE/DELETE surface exists
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit.cross_agency_transfer_logger import (  # noqa: E402
    CrossAgencyTransferLogger,
    query_by_transfer_id,
)

# ---------------------------------------------------------------------------
# Shared schema / fixture
# ---------------------------------------------------------------------------

_SCHEMA = """
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
CREATE TABLE IF NOT EXISTS audit_trail (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    project_id TEXT,
    details TEXT,
    classification TEXT DEFAULT 'CUI',
    session_id TEXT,
    source_ip TEXT,
    recorded_at TEXT NOT NULL DEFAULT ''
);
"""

_MODULE = "tools.audit.cross_agency_transfer_logger"


@pytest.fixture()
def db(tmp_path):
    """Temporary SQLite DB with cross_agency_transfers schema."""
    path = tmp_path / "test_cat.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def logger(db):
    """CrossAgencyTransferLogger with get_connection mocked to the temp DB."""

    def _make_conn():
        # Wrapped in StorageConnection so the module's PG-native %s SQL is
        # translated for SQLite (same pattern as tests/test_cato_twin.py).
        from tools.db.storage import StorageConnection

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        return StorageConnection(conn, "sqlite")

    with patch(f"{_MODULE}.get_connection", side_effect=_make_conn):
        yield CrossAgencyTransferLogger(), db, _make_conn


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _row_count(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    n = conn.execute("SELECT COUNT(*) FROM cross_agency_transfers").fetchone()[0]
    conn.close()
    return n


def _fetch_row(db_path: Path, event_id: str) -> sqlite3.Row:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM cross_agency_transfers WHERE id=?", (event_id,)
    ).fetchone()
    conn.close()
    return row


# ---------------------------------------------------------------------------
# Basic functional tests
# ---------------------------------------------------------------------------


class TestBasicInserts:
    def test_log_initiated_returns_uuid(self, logger):
        cat, db, _ = logger
        eid = cat.log_initiated("t-001", "DoD", "DHS", "intelligence", "alice")
        assert len(eid) == 36  # UUID format

    def test_log_initiated_writes_row(self, logger):
        cat, db, _ = logger
        cat.log_initiated("t-002", "NSA", "CIA", "signals", "bob")
        assert _row_count(db) == 1

    def test_log_completed_event_type(self, logger):
        cat, db, _ = logger
        eid = cat.log_completed("t-003", "DIA", "NRO", "agent-x")
        row = _fetch_row(db, eid)
        assert row["event_type"] == "completed"

    def test_log_failed_event_type(self, logger):
        cat, db, _ = logger
        eid = cat.log_failed("t-004", "FBI", "DEA", "agent-y", rejection_reason="auth")
        row = _fetch_row(db, eid)
        assert row["event_type"] == "failed"

    def test_log_rejected_event_type(self, logger):
        cat, db, _ = logger
        eid = cat.log_rejected("t-005", "CISA", "NCSC", "reviewer-z", "policy violation")
        row = _fetch_row(db, eid)
        assert row["event_type"] == "rejected"

    def test_details_stored_as_json(self, logger):
        cat, db, _ = logger
        payload = {"key": "value", "nested": [1, 2]}
        eid = cat.log_initiated(
            "t-006", "A", "B", "dtype", "actor", details=payload
        )
        row = _fetch_row(db, eid)
        assert json.loads(row["details"]) == payload

    def test_default_classification_is_cui(self, logger):
        cat, db, _ = logger
        eid = cat.log_initiated("t-007", "X", "Y", "dtype", "actor")
        row = _fetch_row(db, eid)
        assert row["data_classification"] == "CUI"

    def test_multiple_events_different_ids(self, logger):
        cat, db, _ = logger
        e1 = cat.log_initiated("t-008", "A", "B", "d", "u")
        e2 = cat.log_completed("t-008", "A", "B", "u")
        assert e1 != e2
        assert _row_count(db) == 2


# ---------------------------------------------------------------------------
# SQL injection regression tests
# ---------------------------------------------------------------------------

# Common injection payloads covering UNION-based, stacked-query, comment, and
# boolean-blind vectors.
_INJECTION_PAYLOADS = [
    "'; DROP TABLE cross_agency_transfers; --",
    "' OR '1'='1",
    "' OR 1=1 --",
    "' UNION SELECT 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16 --",
    "admin'--",
    "'; INSERT INTO cross_agency_transfers VALUES('evil','x','initiated','a','b','c','CUI','h',NULL,NULL,NULL,NULL,NULL,NULL,NULL,'2099-01-01') --",
    "' AND SLEEP(5) --",
    "\x00'; --",
    "transfer_id=1 OR 1=1",
    "' OR 'x'='x",
]


class TestSqlInjectionInWriteFields:
    """Injection payloads in user-controlled write fields must be stored
    verbatim and must not alter DB state in any unintended way."""

    @pytest.mark.parametrize("payload", _INJECTION_PAYLOADS)
    def test_transfer_id_injection(self, logger, payload):
        cat, db, _ = logger
        eid = cat.log_initiated(payload, "Agency-A", "Agency-B", "dtype", "actor")
        assert eid != ""  # did not silently fail
        row = _fetch_row(db, eid)
        assert row["transfer_id"] == payload  # stored verbatim
        assert _row_count(db) == 1  # exactly one row — no stacked insert

    @pytest.mark.parametrize("payload", _INJECTION_PAYLOADS)
    def test_source_agency_injection(self, logger, payload):
        cat, db, _ = logger
        eid = cat.log_initiated("t-src", payload, "Agency-B", "dtype", "actor")
        assert eid != ""
        row = _fetch_row(db, eid)
        assert row["source_agency"] == payload
        assert _row_count(db) == 1

    @pytest.mark.parametrize("payload", _INJECTION_PAYLOADS)
    def test_target_agency_injection(self, logger, payload):
        cat, db, _ = logger
        eid = cat.log_initiated("t-tgt", "Agency-A", payload, "dtype", "actor")
        assert eid != ""
        row = _fetch_row(db, eid)
        assert row["target_agency"] == payload
        assert _row_count(db) == 1

    @pytest.mark.parametrize("payload", _INJECTION_PAYLOADS)
    def test_actor_injection(self, logger, payload):
        cat, db, _ = logger
        eid = cat.log_initiated("t-actor", "A", "B", "dtype", payload)
        assert eid != ""
        row = _fetch_row(db, eid)
        assert row["actor"] == payload
        assert _row_count(db) == 1

    @pytest.mark.parametrize("payload", _INJECTION_PAYLOADS)
    def test_rejection_reason_injection(self, logger, payload):
        cat, db, _ = logger
        eid = cat.log_rejected("t-rej", "A", "B", "reviewer", payload)
        assert eid != ""
        row = _fetch_row(db, eid)
        assert row["rejection_reason"] == payload
        assert _row_count(db) == 1

    @pytest.mark.parametrize("payload", _INJECTION_PAYLOADS)
    def test_error_code_injection(self, logger, payload):
        cat, db, _ = logger
        eid = cat.log_failed("t-err", "A", "B", "actor", error_code=payload)
        assert eid != ""
        row = _fetch_row(db, eid)
        assert row["error_code"] == payload
        assert _row_count(db) == 1

    @pytest.mark.parametrize("payload", _INJECTION_PAYLOADS)
    def test_details_json_injection(self, logger, payload):
        """Injection inside the JSON details blob must not escape into SQL."""
        cat, db, _ = logger
        eid = cat.log_initiated(
            "t-detail", "A", "B", "dtype", "actor", details={"evil": payload}
        )
        assert eid != ""
        row = _fetch_row(db, eid)
        stored = json.loads(row["details"])
        assert stored["evil"] == payload
        assert _row_count(db) == 1


class TestSqlInjectionInQueryFunction:
    """query_by_transfer_id must not return unintended rows when the
    transfer_id argument contains injection payloads."""

    @pytest.fixture()
    def seeded(self, db):
        """Pre-seed two known transfers then yield the db path and a conn factory."""

        def _conn():
            from tools.db.storage import StorageConnection

            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            return StorageConnection(conn, "sqlite")

        with patch(f"{_MODULE}.get_connection", side_effect=_conn):
            cat = CrossAgencyTransferLogger()
            cat.log_initiated("legit-transfer-1", "A", "B", "d", "u")
            cat.log_completed("legit-transfer-2", "C", "D", "v")
        return db, _conn

    @pytest.mark.parametrize("payload", _INJECTION_PAYLOADS)
    def test_query_injection_returns_empty(self, seeded, payload):
        """An injection payload as transfer_id should return no rows, not all rows."""
        db, _conn = seeded
        with patch(f"{_MODULE}.get_connection", side_effect=_conn):
            rows = query_by_transfer_id(payload)
        assert rows == []  # no rows leak due to OR-injection

    def test_query_legit_transfer_id_works(self, seeded):
        """A legitimate transfer_id query still returns the correct row."""
        db, _conn = seeded
        with patch(f"{_MODULE}.get_connection", side_effect=_conn):
            rows = query_by_transfer_id("legit-transfer-1")
        assert len(rows) == 1
        assert rows[0]["transfer_id"] == "legit-transfer-1"

    def test_query_or_1_eq_1_returns_empty(self, seeded):
        """Classic OR-true boolean-blind injection returns no rows."""
        db, _conn = seeded
        with patch(f"{_MODULE}.get_connection", side_effect=_conn):
            rows = query_by_transfer_id("' OR '1'='1")
        assert rows == []


# ---------------------------------------------------------------------------
# Append-only guarantee
# ---------------------------------------------------------------------------


class TestAppendOnly:
    def test_no_update_method_on_logger(self):
        """CrossAgencyTransferLogger must expose no update/delete surface."""
        cat = CrossAgencyTransferLogger()
        for attr in dir(cat):
            assert "update" not in attr.lower()
            assert "delete" not in attr.lower()
            assert "remove" not in attr.lower()

    def test_table_missing_returns_empty_string(self, tmp_path):
        """When table is absent, _insert degrades gracefully rather than raising."""
        empty_db = tmp_path / "empty.db"
        conn = sqlite3.connect(str(empty_db))
        conn.close()

        def _conn():
            c = sqlite3.connect(str(empty_db))
            c.row_factory = sqlite3.Row
            return c

        with patch(f"{_MODULE}.get_connection", side_effect=_conn):
            cat = CrossAgencyTransferLogger()
            result = cat.log_initiated("t-x", "A", "B", "d", "u")
        assert result == ""


# ---------------------------------------------------------------------------
# NIST AU-2 compliance — required fields & dual-write
# ---------------------------------------------------------------------------

class TestNistAu2Compliance:
    """NIST 800-53 AU-2 (Audit Events) — verify audit records are comprehensive."""

    def test_initiated_has_all_required_fields(self, logger):
        cat, db, _ = logger
        eid = cat.log_initiated(
            transfer_id="t-au2-001",
            source_agency="DoD",
            target_agency="DHS",
            data_type="intelligence",
            actor="alice@dod.mil",
            data_classification="CUI//SP-CTI",
            project_id="proj-alpha",
            details={"priority": "high"},
        )
        row = _fetch_row(db, eid)
        assert row["id"] and len(row["id"]) == 36
        assert row["transfer_id"] == "t-au2-001"
        assert row["event_type"] == "initiated"
        assert row["source_agency"] == "DoD"
        assert row["target_agency"] == "DHS"
        assert row["actor"] == "alice@dod.mil"
        assert row["data_classification"] == "CUI//SP-CTI"
        assert row["occurred_at"]
        from datetime import datetime
        datetime.fromisoformat(row["occurred_at"])
        assert json.loads(row["details"]) == {"priority": "high"}

    def test_completed_has_all_required_fields(self, logger):
        cat, db, _ = logger
        eid = cat.log_completed(
            transfer_id="t-au2-002",
            source_agency="NSA",
            target_agency="CIA",
            actor="system",
            bytes_transferred=204800,
            checksum="sha256:abc123",
            duration_ms=850,
        )
        row = _fetch_row(db, eid)
        assert row["transfer_id"] == "t-au2-002"
        assert row["event_type"] == "completed"
        assert row["bytes_transferred"] == 204800
        assert row["checksum"] == "sha256:abc123"
        assert row["duration_ms"] == 850
        assert row["occurred_at"]
        assert row["actor"] == "system"
        assert row["data_classification"] == "CUI"

    def test_failed_has_all_required_fields(self, logger):
        cat, db, _ = logger
        eid = cat.log_failed(
            transfer_id="t-au2-003",
            source_agency="FBI",
            target_agency="DEA",
            actor="gateway",
            rejection_reason="auth failure",
            error_code="ERR_401",
        )
        row = _fetch_row(db, eid)
        assert row["event_type"] == "failed"
        assert row["rejection_reason"] == "auth failure"
        assert row["error_code"] == "ERR_401"
        assert row["occurred_at"]
        assert row["actor"] == "gateway"

    def test_rejected_has_all_required_fields(self, logger):
        cat, db, _ = logger
        eid = cat.log_rejected(
            transfer_id="t-au2-004",
            source_agency="CISA",
            target_agency="NCSC",
            reviewed_by="security-officer",
            rejection_reason="policy violation",
            data_classification="SECRET",
        )
        row = _fetch_row(db, eid)
        assert row["event_type"] == "rejected"
        assert row["actor"] == "security-officer"
        assert row["data_classification"] == "SECRET"
        assert row["occurred_at"]
        assert row["rejection_reason"] == "policy violation"

    def test_dual_write_to_audit_trail(self, logger):
        """AU-2 completeness: cross_agency_transfers events are mirrored to audit_trail."""
        cat, db, make_conn = logger
        eid = cat.log_completed(
            transfer_id="t-au2-dual",
            source_agency="DIA",
            target_agency="NRO",
            actor="system",
            bytes_transferred=1024,
            data_classification="CUI",
        )
        conn = make_conn()
        rows = conn.execute(
            "SELECT * FROM audit_trail WHERE details LIKE ?",
            (f"%{eid}%",),
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        audit = dict(rows[0])
        assert audit["event_type"] == "cross_agency_transfer_completed"
        assert audit["actor"] == "system"
        assert "DIA → NRO" in audit["action"]
        assert audit["classification"] == "CUI"
        assert "t-au2-dual" in audit["details"]


# ---------------------------------------------------------------------------
# NIST AU-9 compliance — protection of audit information
# ---------------------------------------------------------------------------

class TestNistAu9Compliance:
    """NIST 800-53 AU-9 (Protection of Audit Information) — append-only guarantee."""

    def test_append_only_no_update_delete_surface(self, logger):
        """The logger must expose no methods that mutate or remove audit rows."""
        cat, db, _ = logger
        for attr in dir(cat):
            assert "update" not in attr.lower()
            assert "delete" not in attr.lower()
            assert "remove" not in attr.lower()
            assert "patch" not in attr.lower()

    def test_sql_executed_never_updates_or_deletes(self, logger):
        """All SQL issued by the logger must be INSERT or SELECT only."""
        cat, db, make_conn = logger
        executed_sql: list[str] = []

        class _RecordingConn:
            def __init__(self, real):
                self._real = real

            def execute(self, sql, *args, **kwargs):
                executed_sql.append(sql.strip().upper())
                return self._real.execute(sql, *args, **kwargs)

            def commit(self):
                return self._real.commit()

            def cursor(self):
                # table_exists() (pgrt-sweep-06) probes via raw.cursor();
                # pass through so the existence check succeeds.
                return self._real.cursor()

            def __getattr__(self, name):
                return getattr(self._real, name)

        conn = make_conn()
        recording = _RecordingConn(conn)
        with patch(f"{_MODULE}.get_connection", return_value=recording):
            cat.log_initiated("t-au9-sql", "A", "B", "d", "u")
            cat.log_completed("t-au9-sql", "A", "B", "u")
        conn.close()

        for sql in executed_sql:
            assert not sql.startswith("UPDATE"), f"AU-9 violation: {sql}"
            assert not sql.startswith("DELETE"), f"AU-9 violation: {sql}"
            assert not sql.startswith("DROP"), f"AU-9 violation: {sql}"
        insert_calls = [s for s in executed_sql if s.startswith("INSERT")]
        assert len(insert_calls) >= 2


# ---------------------------------------------------------------------------
# Cross-agency transfer lifecycle simulation
# ---------------------------------------------------------------------------

class TestTransferLifecycle:
    """End-to-end simulation of a cross-agency data transfer."""

    def test_initiated_to_completed_lifecycle(self, logger):
        """A transfer is initiated then completed; both events are auditable."""
        cat, db, make_conn = logger
        tid = "lifecycle-001"

        eid1 = cat.log_initiated(
            tid, "DHS", "FBI", "threat_intel", "analyst-1", project_id="proj-lifecycle"
        )
        eid2 = cat.log_completed(
            tid, "DHS", "FBI", "gateway",
            bytes_transferred=4096, checksum="sha256:def", duration_ms=1200,
        )

        conn = make_conn()
        rows = conn.execute(
            "SELECT * FROM cross_agency_transfers WHERE transfer_id=? ORDER BY occurred_at ASC",
            (tid,),
        ).fetchall()
        conn.close()

        assert len(rows) == 2
        assert dict(rows[0])["event_type"] == "initiated"
        assert dict(rows[0])["id"] == eid1
        assert dict(rows[1])["event_type"] == "completed"
        assert dict(rows[1])["id"] == eid2

        conn = make_conn()
        audit_rows = conn.execute(
            "SELECT * FROM audit_trail WHERE details LIKE ? ORDER BY recorded_at ASC",
            (f"%{tid}%",),
        ).fetchall()
        conn.close()
        assert len(audit_rows) == 2
        assert dict(audit_rows[0])["event_type"] == "cross_agency_transfer_initiated"
        assert dict(audit_rows[1])["event_type"] == "cross_agency_transfer_completed"

    def test_initiated_to_failed_lifecycle(self, logger):
        """A transfer is initiated then fails; both events are auditable."""
        cat, db, make_conn = logger
        tid = "lifecycle-002"

        cat.log_initiated(tid, "CIA", "DIA", "sigint", "operator-x")
        cat.log_failed(
            tid, "CIA", "DIA", "gateway",
            rejection_reason="Network timeout", error_code="ERR_NET",
        )

        conn = make_conn()
        rows = conn.execute(
            "SELECT * FROM cross_agency_transfers WHERE transfer_id=? ORDER BY occurred_at ASC",
            (tid,),
        ).fetchall()
        conn.close()

        assert len(rows) == 2
        assert dict(rows[0])["event_type"] == "initiated"
        assert dict(rows[1])["event_type"] == "failed"
        assert dict(rows[1])["rejection_reason"] == "Network timeout"

    def test_initiated_to_rejected_lifecycle(self, logger):
        """A transfer is initiated then rejected by a reviewer."""
        cat, db, make_conn = logger
        tid = "lifecycle-003"

        cat.log_initiated(tid, "DEA", "ATF", "case_file", "agent-y")
        cat.log_rejected(
            tid, "DEA", "ATF", "reviewer-z", "Classification mismatch",
        )

        conn = make_conn()
        rows = conn.execute(
            "SELECT * FROM cross_agency_transfers WHERE transfer_id=? ORDER BY occurred_at ASC",
            (tid,),
        ).fetchall()
        conn.close()

        assert len(rows) == 2
        assert dict(rows[0])["event_type"] == "initiated"
        assert dict(rows[1])["event_type"] == "rejected"
        assert dict(rows[1])["actor"] == "reviewer-z"
