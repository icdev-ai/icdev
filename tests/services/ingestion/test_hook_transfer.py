# CUI // SP-CTI
"""Unit tests for services.ingestion.hook_transfer.

Acceptance criterion:
- Any simulated cross-agency transfer request is automatically logged with the
  correct metadata before proceeding.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from icdev.tools.audit.cross_agency_transfer_logger import (  # noqa: E402
    CrossAgencyTransferLogger,
)
from services.ingestion.hook_transfer import (  # noqa: E402
    TransferValidationError,
    _validate_request,
    complete_transfer,
    fail_transfer,
    intercept_transfer_request,
    run_transfer,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def valid_request():
    """A fully valid cross-agency transfer request."""
    return {
        "transfer_id": "xfer-001",
        "source_agency": "DoD",
        "target_agency": "DHS",
        "actor": "alice",
        "data_type": "intelligence",
        "data_classification": "CUI",
        "project_id": "proj-42",
        "details": {"priority": "high"},
    }


# ---------------------------------------------------------------------------
# Validation helper tests
# ---------------------------------------------------------------------------


class TestValidateRequest:
    def test_passes_with_valid_request(self, valid_request):
        rules = {
            "validation": {
                "required_fields": ["transfer_id", "source_agency", "target_agency", "actor", "data_type"],
                "allowed_agency_pairs": None,
            },
            "classifications": {"default": "CUI", "supported": ["CUI", "SECRET"]},
        }
        _validate_request(valid_request, rules)  # should not raise

    def test_missing_required_field_raises(self, valid_request):
        rules = {
            "validation": {
                "required_fields": ["transfer_id", "source_agency", "target_agency", "actor", "data_type"],
                "allowed_agency_pairs": None,
            },
            "classifications": {"default": "CUI", "supported": ["CUI"]},
        }
        del valid_request["actor"]
        with pytest.raises(TransferValidationError, match="Missing required fields"):
            _validate_request(valid_request, rules)

    def test_unsupported_classification_raises(self, valid_request):
        rules = {
            "validation": {
                "required_fields": ["transfer_id", "source_agency", "target_agency", "actor", "data_type"],
                "allowed_agency_pairs": None,
            },
            "classifications": {"default": "CUI", "supported": ["CUI"]},
        }
        valid_request["data_classification"] = "SECRET"
        with pytest.raises(TransferValidationError, match="Unsupported classification"):
            _validate_request(valid_request, rules)

    def test_restricted_agency_pair_raises(self, valid_request):
        rules = {
            "validation": {
                "required_fields": ["transfer_id", "source_agency", "target_agency", "actor", "data_type"],
                "allowed_agency_pairs": [["DoD", "CIA"]],
            },
            "classifications": {"default": "CUI", "supported": ["CUI", "SECRET", "TOP_SECRET"]},
        }
        with pytest.raises(TransferValidationError, match="Agency pair not allowed"):
            _validate_request(valid_request, rules)

    def test_empty_string_required_field_raises(self, valid_request):
        rules = {
            "validation": {
                "required_fields": ["transfer_id", "source_agency", "target_agency", "actor", "data_type"],
                "allowed_agency_pairs": None,
            },
            "classifications": {"default": "CUI", "supported": ["CUI"]},
        }
        valid_request["actor"] = ""
        with pytest.raises(TransferValidationError, match="Missing required fields"):
            _validate_request(valid_request, rules)


# ---------------------------------------------------------------------------
# Hook interception tests — acceptance criterion
# ---------------------------------------------------------------------------


class TestInterceptTransferRequest:
    """Verify that every simulated transfer request is logged with correct
    metadata before the function returns the proceed verdict."""

    def test_valid_request_logged_before_proceeding(self, valid_request):
        """log_initiated is called with correct metadata; result contains the
        same event_id, proving logging happened before proceeding."""
        with patch("services.ingestion.hook_transfer.CrossAgencyTransferLogger") as MockLogger:
            mock_instance = MockLogger.return_value
            mock_instance.log_initiated.return_value = "evt-uuid-123"

            result = intercept_transfer_request(valid_request)

        assert result["allowed"] is True
        assert result["event_id"] == "evt-uuid-123"
        assert result["transfer_id"] == "xfer-001"
        assert result["reason"] is None

        mock_instance.log_initiated.assert_called_once_with(
            transfer_id="xfer-001",
            source_agency="DoD",
            target_agency="DHS",
            data_type="intelligence",
            actor="alice",
            data_classification="CUI",
            project_id="proj-42",
            details={"priority": "high"},
        )
        mock_instance.log_failed.assert_not_called()

    def test_missing_field_rejected_and_logged_as_failed(self, valid_request):
        """A request missing a required field is rejected AND logged as failed
        with a VALIDATION_ERROR code."""
        del valid_request["actor"]

        with patch("services.ingestion.hook_transfer.CrossAgencyTransferLogger") as MockLogger:
            mock_instance = MockLogger.return_value
            mock_instance.log_failed.return_value = "evt-uuid-456"

            result = intercept_transfer_request(valid_request)

        assert result["allowed"] is False
        assert result["event_id"] == "evt-uuid-456"
        assert result["transfer_id"] == "xfer-001"
        assert "Missing required fields" in result["reason"]

        mock_instance.log_failed.assert_called_once()
        call_kwargs = mock_instance.log_failed.call_args.kwargs
        assert call_kwargs["transfer_id"] == "xfer-001"
        assert call_kwargs["source_agency"] == "DoD"
        assert call_kwargs["target_agency"] == "DHS"
        assert call_kwargs["actor"] == "system"  # fallback when actor missing
        assert call_kwargs["error_code"] == "VALIDATION_ERROR"
        assert "Missing required fields" in call_kwargs["rejection_reason"]
        mock_instance.log_initiated.assert_not_called()

    def test_unsupported_classification_rejected_and_logged(self, valid_request):
        """Unsupported classification triggers rejection and a failed audit log."""
        valid_request["data_classification"] = "INVALID"

        with patch("services.ingestion.hook_transfer.CrossAgencyTransferLogger") as MockLogger:
            mock_instance = MockLogger.return_value
            mock_instance.log_failed.return_value = "evt-uuid-789"

            result = intercept_transfer_request(valid_request)

        assert result["allowed"] is False
        assert "Unsupported classification" in result["reason"]
        mock_instance.log_failed.assert_called_once()
        mock_instance.log_initiated.assert_not_called()

    def test_default_classification_used_when_not_provided(self, valid_request):
        """If data_classification is omitted, the default from rules.yaml is used."""
        del valid_request["data_classification"]

        with patch("services.ingestion.hook_transfer.CrossAgencyTransferLogger") as MockLogger:
            mock_instance = MockLogger.return_value
            mock_instance.log_initiated.return_value = "evt-uuid-abc"

            result = intercept_transfer_request(valid_request)

        assert result["allowed"] is True
        call_kwargs = mock_instance.log_initiated.call_args.kwargs
        assert call_kwargs["data_classification"] == "CUI"

    def test_rejected_when_agency_pair_restricted(self, valid_request):
        """When rules.yaml restricts pairs, an invalid pair is rejected."""
        with patch("services.ingestion.hook_transfer._load_rules") as mock_load:
            mock_load.return_value = {
                "validation": {
                    "required_fields": ["transfer_id", "source_agency", "target_agency", "actor", "data_type"],
                    "allowed_agency_pairs": [["DoD", "CIA"]],
                },
                "classifications": {"default": "CUI", "supported": ["CUI", "SECRET", "TOP_SECRET"]},
            }

            with patch("services.ingestion.hook_transfer.CrossAgencyTransferLogger") as MockLogger:
                mock_instance = MockLogger.return_value
                mock_instance.log_failed.return_value = "evt-uuid-def"

                result = intercept_transfer_request(valid_request)

        assert result["allowed"] is False
        assert "Agency pair not allowed" in result["reason"]
        mock_instance.log_failed.assert_called_once()
        mock_instance.log_initiated.assert_not_called()

    def test_empty_details_is_ok(self, valid_request):
        """A request with no details field is still valid and logged."""
        del valid_request["details"]

        with patch("services.ingestion.hook_transfer.CrossAgencyTransferLogger") as MockLogger:
            mock_instance = MockLogger.return_value
            mock_instance.log_initiated.return_value = "evt-uuid-ghi"

            result = intercept_transfer_request(valid_request)

        assert result["allowed"] is True
        call_kwargs = mock_instance.log_initiated.call_args.kwargs
        assert call_kwargs.get("details") is None


# ---------------------------------------------------------------------------
# Completion / failure helpers
# ---------------------------------------------------------------------------


class TestCompleteTransfer:
    def test_logs_completed_event(self):
        with patch("services.ingestion.hook_transfer.CrossAgencyTransferLogger") as MockLogger:
            mock_instance = MockLogger.return_value
            mock_instance.log_completed.return_value = "evt-uuid-comp"

            eid = complete_transfer(
                transfer_id="xfer-002",
                source_agency="NSA",
                target_agency="CIA",
                actor="bob",
                bytes_transferred=1024,
                checksum="sha256:abc123",
                duration_ms=150,
                data_classification="SECRET",
            )

        assert eid == "evt-uuid-comp"
        mock_instance.log_completed.assert_called_once_with(
            transfer_id="xfer-002",
            source_agency="NSA",
            target_agency="CIA",
            actor="bob",
            bytes_transferred=1024,
            checksum="sha256:abc123",
            duration_ms=150,
            data_classification="SECRET",
        )


class TestFailTransfer:
    def test_logs_failed_event(self):
        with patch("services.ingestion.hook_transfer.CrossAgencyTransferLogger") as MockLogger:
            mock_instance = MockLogger.return_value
            mock_instance.log_failed.return_value = "evt-uuid-fail"

            eid = fail_transfer(
                transfer_id="xfer-003",
                source_agency="FBI",
                target_agency="DEA",
                actor="charlie",
                rejection_reason="auth failure",
                error_code="AUTH_401",
                data_classification="CUI",
            )

        assert eid == "evt-uuid-fail"
        mock_instance.log_failed.assert_called_once_with(
            transfer_id="xfer-003",
            source_agency="FBI",
            target_agency="DEA",
            actor="charlie",
            rejection_reason="auth failure",
            error_code="AUTH_401",
            data_classification="CUI",
        )


# ---------------------------------------------------------------------------
# End-to-end pipeline tests — audit entry created before transfer completes
# ---------------------------------------------------------------------------


class TestRunTransfer:
    """Verify that run_transfer invokes the audit trail writer before the
    transfer completes and that the audit entry is created."""

    def test_valid_request_logs_completed_before_returning(self, valid_request):
        """A valid request runs the pipeline and logs completed before return."""
        with patch("services.ingestion.hook_transfer.CrossAgencyTransferLogger") as MockLogger:
            mock_instance = MockLogger.return_value
            mock_instance.log_initiated.return_value = "evt-init-001"
            mock_instance.log_completed.return_value = "evt-comp-001"

            result = run_transfer(valid_request)

        assert result["success"] is True
        assert result["transfer_id"] == "xfer-001"
        assert result["initiated_event_id"] == "evt-init-001"
        assert result["event_id"] == "evt-comp-001"
        assert result["error"] is None

        # log_initiated called first (validation)
        mock_instance.log_initiated.assert_called_once()
        # log_completed called before returning
        mock_instance.log_completed.assert_called_once_with(
            transfer_id="xfer-001",
            source_agency="DoD",
            target_agency="DHS",
            actor="alice",
            bytes_transferred=None,
            checksum=None,
            duration_ms=None,
            data_classification="CUI",
        )
        mock_instance.log_failed.assert_not_called()

    def test_valid_request_with_custom_transfer_fn(self, valid_request):
        """Custom transfer_fn metrics are forwarded to log_completed."""
        def _fake_transfer(req):
            return {"bytes_transferred": 4096, "checksum": "sha256:deadbeef", "duration_ms": 42}

        with patch("services.ingestion.hook_transfer.CrossAgencyTransferLogger") as MockLogger:
            mock_instance = MockLogger.return_value
            mock_instance.log_initiated.return_value = "evt-init-002"
            mock_instance.log_completed.return_value = "evt-comp-002"

            result = run_transfer(valid_request, transfer_fn=_fake_transfer)

        assert result["success"] is True
        assert result["event_id"] == "evt-comp-002"
        mock_instance.log_completed.assert_called_once_with(
            transfer_id="xfer-001",
            source_agency="DoD",
            target_agency="DHS",
            actor="alice",
            bytes_transferred=4096,
            checksum="sha256:deadbeef",
            duration_ms=42,
            data_classification="CUI",
        )

    def test_invalid_request_rejected_without_running_transfer(self, valid_request):
        """Validation failure aborts before transfer execution and logs failed."""
        del valid_request["actor"]

        with patch("services.ingestion.hook_transfer.CrossAgencyTransferLogger") as MockLogger:
            mock_instance = MockLogger.return_value
            mock_instance.log_failed.return_value = "evt-fail-003"

            result = run_transfer(valid_request)

        assert result["success"] is False
        assert result["transfer_id"] == "xfer-001"
        assert result["event_id"] == "evt-fail-003"
        assert "Missing required fields" in result["error"]
        # log_initiated is NOT called for rejected requests; log_failed is
        mock_instance.log_initiated.assert_not_called()
        mock_instance.log_failed.assert_called_once()
        mock_instance.log_completed.assert_not_called()

    def test_transfer_fn_exception_logs_failed(self, valid_request):
        """When transfer_fn raises, a failed audit entry is created."""
        def _explosive_transfer(req):
            raise RuntimeError("network partition")

        with patch("services.ingestion.hook_transfer.CrossAgencyTransferLogger") as MockLogger:
            mock_instance = MockLogger.return_value
            mock_instance.log_initiated.return_value = "evt-init-004"
            mock_instance.log_failed.return_value = "evt-fail-004"

            result = run_transfer(valid_request, transfer_fn=_explosive_transfer)

        assert result["success"] is False
        assert result["transfer_id"] == "xfer-001"
        assert result["initiated_event_id"] == "evt-init-004"
        assert result["event_id"] == "evt-fail-004"
        assert "network partition" in result["error"]

        mock_instance.log_initiated.assert_called_once()
        mock_instance.log_failed.assert_called_once_with(
            transfer_id="xfer-001",
            source_agency="DoD",
            target_agency="DHS",
            actor="alice",
            rejection_reason="network partition",
            error_code="TRANSFER_EXEC_ERROR",
            data_classification="CUI",
        )
        mock_instance.log_completed.assert_not_called()


# ---------------------------------------------------------------------------
# Real DB integration tests — NIST AU-2 / AU-9 compliance
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
CREATE INDEX IF NOT EXISTS idx_cat_transfer_id ON cross_agency_transfers(transfer_id);
CREATE INDEX IF NOT EXISTS idx_cat_occurred_at ON cross_agency_transfers(occurred_at);

CREATE TABLE IF NOT EXISTS audit_trail (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT,
    affected_files TEXT,
    classification TEXT DEFAULT 'CUI',
    ip_address TEXT,
    session_id TEXT,
    recorded_at TEXT
);
"""


@pytest.fixture()
def real_db(tmp_path):
    """Temporary SQLite DB with cross_agency_transfers + audit_trail schemas."""
    path = tmp_path / "test_hook_real.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return path


class TestNistAu2Au9RealDb:
    """Integration tests using a real SQLite DB (no mocks).

    NIST 800-53 AU-2: Audit Events — all required fields present; dual-write
    to audit_trail.
    NIST 800-53 AU-9: Protection of Audit Information — append-only, no
    UPDATE/DELETE surface, immutable rows.
    """

    @staticmethod
    def _make_conn(db_path):
        """Connection factory — also stands in for ``get_connection``.

        What this returns is handed to production code (via the
        ``get_connection`` patches below), and that code authors ``%s``
        placeholders for PostgreSQL, relying on the StorageConnection layer to
        rewrite them for SQLite. A bare ``sqlite3.connect`` raises
        ``near "%": syntax error`` on every such statement, which the logger's
        best-effort ``except`` swallows — the row never lands and the test
        reads as a missing feature. See tests/_sql_compat.py.

        The same factory also serves SQL authored in this file, which uses
        ``?`` and is left untouched by the translation.
        """
        from _sql_compat import connect as _tconnect

        return _tconnect(db_path)

    @staticmethod
    def _fetch_cat_row(db_path, event_id):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM cross_agency_transfers WHERE id=?", (event_id,)
        ).fetchone()
        conn.close()
        return row

    def test_intercept_valid_request_appends_audit_row(self, real_db):
        """AU-2: a valid transfer request creates a cross-agency audit row."""
        request = {
            "transfer_id": "xfer-au2-001",
            "source_agency": "DoD",
            "target_agency": "DHS",
            "actor": "alice",
            "data_type": "intelligence",
            "data_classification": "CUI",
            "project_id": "proj-42",
            "details": {"priority": "high"},
        }

        def _conn():
            return self._make_conn(real_db)

        with patch("icdev.tools.audit.cross_agency_transfer_logger.get_connection", side_effect=_conn), \
             patch("tools.audit.audit_logger.get_connection", side_effect=_conn):
            result = intercept_transfer_request(request)

        assert result["allowed"] is True
        assert result["event_id"] != ""

        row = self._fetch_cat_row(real_db, result["event_id"])
        assert row is not None
        assert row["transfer_id"] == "xfer-au2-001"
        assert row["event_type"] == "initiated"
        assert row["source_agency"] == "DoD"
        assert row["target_agency"] == "DHS"
        assert row["actor"] == "alice"
        assert row["data_classification"] == "CUI"
        assert row["data_type"] == "intelligence"
        assert row["project_id"] == "proj-42"
        assert row["occurred_at"]
        import json
        assert json.loads(row["details"]) == {"priority": "high"}

    def test_intercept_invalid_request_appends_failed_audit_row(self, real_db):
        """AU-2: a rejected transfer request still appends a failed audit row."""
        request = {
            "transfer_id": "xfer-au2-002",
            "source_agency": "DoD",
            "target_agency": "DHS",
            "data_type": "intelligence",
            # actor missing — should trigger validation failure
        }

        def _conn():
            return self._make_conn(real_db)

        with patch("icdev.tools.audit.cross_agency_transfer_logger.get_connection", side_effect=_conn), \
             patch("tools.audit.audit_logger.get_connection", side_effect=_conn):
            result = intercept_transfer_request(request)

        assert result["allowed"] is False
        assert result["event_id"] != ""

        row = self._fetch_cat_row(real_db, result["event_id"])
        assert row is not None
        assert row["event_type"] == "failed"
        assert row["error_code"] == "VALIDATION_ERROR"
        assert "Missing required fields" in row["rejection_reason"]

    def test_run_transfer_appends_initiated_and_completed(self, real_db):
        """AU-2: full transfer lifecycle writes initiated + completed rows."""
        request = {
            "transfer_id": "xfer-au2-003",
            "source_agency": "NSA",
            "target_agency": "CIA",
            "actor": "bob",
            "data_type": "signals",
            "data_classification": "SECRET",
            "project_id": "proj-99",
        }

        def _conn():
            return self._make_conn(real_db)

        with patch("icdev.tools.audit.cross_agency_transfer_logger.get_connection", side_effect=_conn), \
             patch("tools.audit.audit_logger.get_connection", side_effect=_conn):
            result = run_transfer(request)

        assert result["success"] is True
        assert result["initiated_event_id"] != ""
        assert result["event_id"] != ""
        assert result["event_id"] != result["initiated_event_id"]

        conn = self._make_conn(real_db)
        rows = conn.execute(
            "SELECT * FROM cross_agency_transfers WHERE transfer_id=? ORDER BY occurred_at ASC",
            ("xfer-au2-003",),
        ).fetchall()
        conn.close()

        assert len(rows) == 2
        assert dict(rows[0])["event_type"] == "initiated"
        assert dict(rows[0])["id"] == result["initiated_event_id"]
        assert dict(rows[1])["event_type"] == "completed"
        assert dict(rows[1])["id"] == result["event_id"]

        # Verify dual-write to audit_trail (AU-2 completeness)
        conn = self._make_conn(real_db)
        audit_rows = conn.execute(
            "SELECT * FROM audit_trail WHERE details LIKE ? ORDER BY recorded_at ASC",
            ("%xfer-au2-003%",),
        ).fetchall()
        conn.close()
        assert len(audit_rows) == 2
        assert dict(audit_rows[0])["event_type"] == "cross_agency_transfer_initiated"
        assert dict(audit_rows[1])["event_type"] == "cross_agency_transfer_completed"

    def test_run_transfer_failure_appends_initiated_and_failed(self, real_db):
        """AU-2: transfer exception writes initiated + failed rows."""
        request = {
            "transfer_id": "xfer-au2-004",
            "source_agency": "FBI",
            "target_agency": "DEA",
            "actor": "charlie",
            "data_type": "case_file",
        }

        def _explode(req):
            raise RuntimeError("network partition")

        def _conn():
            return self._make_conn(real_db)

        with patch("icdev.tools.audit.cross_agency_transfer_logger.get_connection", side_effect=_conn), \
             patch("tools.audit.audit_logger.get_connection", side_effect=_conn):
            result = run_transfer(request, transfer_fn=_explode)

        assert result["success"] is False
        assert result["initiated_event_id"] != ""
        assert result["event_id"] != ""

        conn = self._make_conn(real_db)
        rows = conn.execute(
            "SELECT * FROM cross_agency_transfers WHERE transfer_id=? ORDER BY occurred_at ASC",
            ("xfer-au2-004",),
        ).fetchall()
        conn.close()

        assert len(rows) == 2
        assert dict(rows[0])["event_type"] == "initiated"
        assert dict(rows[1])["event_type"] == "failed"
        assert "network partition" in dict(rows[1])["rejection_reason"]

    def test_append_only_no_public_update_or_delete(self, real_db):
        """AU-9: CrossAgencyTransferLogger exposes no update/delete surface."""
        logger = CrossAgencyTransferLogger()
        for attr in dir(logger):
            if not attr.startswith("_"):
                assert "update" not in attr.lower(), f"AU-9 violation: public method '{attr}'"
                assert "delete" not in attr.lower(), f"AU-9 violation: public method '{attr}'"
                assert "remove" not in attr.lower(), f"AU-9 violation: public method '{attr}'"
                assert "patch" not in attr.lower(), f"AU-9 violation: public method '{attr}'"

    def test_sql_issued_never_updates_or_deletes(self, real_db):
        """AU-9: all SQL executed by the logger is INSERT or SELECT only."""
        request = {
            "transfer_id": "xfer-au9-001",
            "source_agency": "DHS",
            "target_agency": "FBI",
            "actor": "analyst",
            "data_type": "threat_intel",
        }

        executed_sql: list[str] = []

        class _RecordingConn:
            def __init__(self, real):
                self._real = real

            def execute(self, sql, *args, **kwargs):
                executed_sql.append(sql.strip().upper())
                return self._real.execute(sql, *args, **kwargs)

            def cursor(self):
                # ``storage.table_exists`` probes via ``conn.cursor()``. Without
                # this the AttributeError is swallowed by that helper's
                # ``except Exception: return False``, the logger concludes the
                # table is missing and returns before issuing any INSERT — so
                # this test recorded no SQL at all and asserted its own no-op.
                return self._real.cursor()

            def commit(self):
                return self._real.commit()

            def close(self):
                return self._real.close()

        real_conn = self._make_conn(real_db)
        recording = _RecordingConn(real_conn)

        with patch("icdev.tools.audit.cross_agency_transfer_logger.get_connection", return_value=recording), \
             patch("tools.audit.audit_logger.get_connection", return_value=recording):
            intercept_transfer_request(request)

        real_conn.close()

        for sql in executed_sql:
            assert not sql.startswith("UPDATE"), f"AU-9 violation: {sql}"
            assert not sql.startswith("DELETE"), f"AU-9 violation: {sql}"
            assert not sql.startswith("DROP"), f"AU-9 violation: {sql}"
            assert not sql.startswith("ALTER"), f"AU-9 violation: {sql}"

        insert_calls = [s for s in executed_sql if s.startswith("INSERT")]
        assert len(insert_calls) >= 1

    def test_audit_row_contains_all_required_nist_fields(self, real_db):
        """AU-2: every audit row contains the mandatory NIST 800-53 fields."""
        request = {
            "transfer_id": "xfer-au2-req",
            "source_agency": "DoD",
            "target_agency": "DHS",
            "actor": "alice@dod.mil",
            "data_type": "intelligence",
            "data_classification": "SECRET",
            "project_id": "proj-alpha",
            "details": {"protocol": "TLS1.3"},
        }

        def _conn():
            return self._make_conn(real_db)

        with patch("icdev.tools.audit.cross_agency_transfer_logger.get_connection", side_effect=_conn), \
             patch("tools.audit.audit_logger.get_connection", side_effect=_conn):
            result = intercept_transfer_request(request)

        assert result["allowed"] is True
        event_id = result["event_id"]

        row = self._fetch_cat_row(real_db, event_id)
        assert row is not None

        # NIST AU-2 required fields: who, what, when, where, why
        required = [
            "id", "transfer_id", "event_type", "source_agency",
            "target_agency", "actor", "occurred_at", "data_classification",
        ]
        missing = [f for f in required if row[f] is None or str(row[f]) == ""]
        assert missing == [], f"Missing required NIST AU-2 fields: {missing}"

        from datetime import datetime
        datetime.fromisoformat(row["occurred_at"])

        assert row["data_type"] == "intelligence"
        assert row["project_id"] == "proj-alpha"
        assert row["data_classification"] == "SECRET"
        import json
        assert json.loads(row["details"]) == {"protocol": "TLS1.3"}
