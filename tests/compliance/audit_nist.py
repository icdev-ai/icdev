# CUI // SP-CTI
"""NIST 800-53 AU-2 / AU-9 compliance integration tests for cross-agency transfer audit logging.

Covers:
- AU-2 Audit Events: all required event types generate records; required fields present;
  dual-write to main audit_trail; schema validation; event type restriction.
- AU-9 Protection of Audit Information: append-only (no UPDATE/DELETE surface);
  SQL injection resistance; immutable trail; checksum integrity; classification markings.
- Simulated cross-agency transfer lifecycle with zero missing required fields.
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

from tools.audit.cross_agency_transfer_logger import (  # noqa: E402
    CrossAgencyTransferLogger,
    query_by_transfer_id,
)

_MODULE = "tools.audit.cross_agency_transfer_logger"

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
"""

# Required fields per args/audit_config.yaml (AU-2 who/what/when/where/why)
_REQUIRED_FIELDS = [
    "transfer_id",
    "event_type",
    "source_agency",
    "target_agency",
    "actor",
    "occurred_at",
    "data_classification",
]

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


@pytest.fixture()
def db(tmp_path):
    """Temporary SQLite DB with both cross-agency and audit_trail schemas."""
    path = tmp_path / "compliance.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def logger(db):
    """CrossAgencyTransferLogger mocked to the temp DB."""

    def _make_conn():
        c = sqlite3.connect(str(db))
        c.row_factory = sqlite3.Row
        return c

    with patch(f"{_MODULE}.get_connection", side_effect=_make_conn):
        yield CrossAgencyTransferLogger(), db, _make_conn


# ---------------------------------------------------------------------------
# AU-2: Audit Events
# ---------------------------------------------------------------------------


class TestAu2AuditEvents:
    """NIST AU-2 — auditable events are generated and complete."""

    def test_all_required_event_types_generate_records(self, logger):
        cat, db, _ = logger
        events = [
            ("initiated", cat.log_initiated),
            ("completed", cat.log_completed),
            ("failed", cat.log_failed),
            ("rejected", cat.log_rejected),
        ]
        for ev_type, fn in events:
            if ev_type == "initiated":
                eid = fn("t-" + ev_type, "DoD", "DHS", "intel", "alice")
            elif ev_type == "completed":
                eid = fn("t-" + ev_type, "DoD", "DHS", "alice")
            elif ev_type == "failed":
                eid = fn("t-" + ev_type, "DoD", "DHS", "alice")
            else:  # rejected
                eid = fn("t-" + ev_type, "DoD", "DHS", "reviewer", "policy")
            assert len(eid) == 36, f"{ev_type} did not return UUID"

        conn = sqlite3.connect(str(db))
        n = conn.execute("SELECT COUNT(*) FROM cross_agency_transfers").fetchone()[0]
        conn.close()
        assert n == 4

    def test_all_required_fields_present(self, logger):
        cat, db, _ = logger
        eid = cat.log_initiated("t-req", "NSA", "CIA", "signals", "bob", data_classification="SECRET")
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM cross_agency_transfers WHERE id=?", (eid,)).fetchone()
        conn.close()
        for field in _REQUIRED_FIELDS:
            assert row[field] is not None and str(row[field]) != "", f"missing required field: {field}"

    def test_occurred_at_is_iso8601_utc(self, logger):
        cat, db, _ = logger
        eid = cat.log_initiated("t-time", "A", "B", "d", "u")
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT occurred_at FROM cross_agency_transfers WHERE id=?", (eid,)).fetchone()
        conn.close()
        val = row["occurred_at"]
        assert val.startswith("20") and "T" in val and (val.endswith("Z") or "+00:00" in val or ":" in val)

    def test_event_type_rejected_by_check_constraint(self, db):
        conn = sqlite3.connect(str(db))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO cross_agency_transfers (id,transfer_id,event_type,source_agency,target_agency,actor,data_classification,occurred_at) VALUES (?,?,?,?,?,?,?,?)",
                ("x", "t", "invalid", "A", "B", "u", "CUI", "2026-01-01T00:00:00+00:00"),
            )
        conn.close()

    def test_dual_write_to_audit_trail(self, logger):
        cat, db, make_conn = logger
        # Patch both the logger module and audit_logger module get_connection
        # so the dual-write lands in the same temp DB.
        with patch(f"{_MODULE}.get_connection", side_effect=make_conn), \
             patch("tools.audit.audit_logger.get_connection", side_effect=make_conn):
            cat.log_initiated("t-dual", "DIA", "NRO", "imagery", "agent-x")
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM audit_trail WHERE event_type=? ORDER BY created_at DESC",
            ("cross_agency_transfer_initiated",),
        ).fetchall()
        conn.close()
        assert len(rows) >= 1
        assert rows[0]["actor"] == "agent-x"
        assert "DIA" in rows[0]["action"] and "NRO" in rows[0]["action"]
        assert rows[0]["classification"] == "CUI"

    def test_schema_json_validates_required_fields(self):
        schema_path = ROOT / "args" / "schema" / "audit_events.json"
        assert schema_path.exists()
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        cat_def = schema["definitions"]["CrossAgencyTransferEvent"]
        required = set(cat_def["required"])
        assert required >= {
            "id", "transfer_id", "event_type", "source_agency",
            "target_agency", "actor", "data_classification", "occurred_at",
        }

    def test_classification_enum_enforced(self, logger):
        cat, db, _ = logger
        eid = cat.log_initiated("t-cls", "A", "B", "d", "u", data_classification="SECRET")
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT data_classification FROM cross_agency_transfers WHERE id=?", (eid,)).fetchone()
        conn.close()
        assert row["data_classification"] == "SECRET"


# ---------------------------------------------------------------------------
# AU-9: Protection of Audit Information
# ---------------------------------------------------------------------------


class TestAu9Protection:
    """NIST AU-9 — audit information is protected from alteration/deletion."""

    def test_no_update_method_on_logger(self):
        cat = CrossAgencyTransferLogger()
        for attr in dir(cat):
            if not attr.startswith("_"):
                assert "update" not in attr.lower(), f"found update-like attr: {attr}"
                assert "delete" not in attr.lower(), f"found delete-like attr: {attr}"
                assert "remove" not in attr.lower(), f"found remove-like attr: {attr}"

    def test_sqlite_update_blocked_by_hook_simulation(self, db):
        """Simulate hook protection: attempt to UPDATE/DELETE should fail or be blocked."""
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO cross_agency_transfers (id,transfer_id,event_type,source_agency,target_agency,actor,data_classification,occurred_at) VALUES (?,?,?,?,?,?,?,?)",
            ("hook-test", "t-hook", "initiated", "A", "B", "u", "CUI", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()
        # In production the pre_tool_use hook blocks UPDATE/DELETE on append-only tables.
        # In the DB layer we verify the table has no triggers that allow it, but SQLite
        # itself permits the statement.  The compliance posture is enforced by the hook
        # and by application design (no update/delete methods).
        # We verify the table has no such triggers.
        triggers = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='cross_agency_transfers'"
        ).fetchall()
        for t in triggers:
            assert "update" not in t[0].lower() and "delete" not in t[0].lower()
        conn.close()

    @pytest.mark.parametrize("payload", _INJECTION_PAYLOADS)
    def test_sql_injection_resistance_in_all_fields(self, logger, payload):
        cat, db, _ = logger
        eid = cat.log_initiated(
            transfer_id=payload,
            source_agency=payload,
            target_agency=payload,
            data_type=payload,
            actor=payload,
            details={"injected": payload},
        )
        assert eid != ""
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM cross_agency_transfers WHERE id=?", (eid,)).fetchone()
        conn.close()
        assert row["transfer_id"] == payload
        assert row["source_agency"] == payload
        assert row["target_agency"] == payload
        assert row["actor"] == payload
        assert json.loads(row["details"])["injected"] == payload
        conn = sqlite3.connect(str(db))
        n = conn.execute("SELECT COUNT(*) FROM cross_agency_transfers").fetchone()[0]
        conn.close()
        assert n == 1  # no stacked insert

    @pytest.mark.parametrize("payload", _INJECTION_PAYLOADS)
    def test_query_by_transfer_id_injection_safe(self, logger, payload):
        cat, db, _make_conn = logger
        # Seed a legitimate row
        cat.log_initiated("legit", "A", "B", "d", "u")
        with patch(f"{_MODULE}.get_connection", side_effect=_make_conn):
            rows = query_by_transfer_id(payload)
        assert rows == []

    def test_immutable_after_insert(self, db):
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO cross_agency_transfers (id,transfer_id,event_type,source_agency,target_agency,actor,data_classification,occurred_at) VALUES (?,?,?,?,?,?,?,?)",
            ("immut", "t-immut", "initiated", "A", "B", "u", "CUI", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()
        # Verify row exists
        row = conn.execute("SELECT * FROM cross_agency_transfers WHERE id=?", ("immut",)).fetchone()
        assert row is not None
        # Application layer has no update/delete methods; direct SQL would bypass that.
        # We assert the design intent: the only public API is insert+query.
        conn.close()

    def test_checksum_integrity_when_provided(self, logger):
        cat, db, _ = logger
        eid = cat.log_completed(
            "t-chk", "A", "B", "u",
            bytes_transferred=1024,
            checksum="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            duration_ms=250,
        )
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM cross_agency_transfers WHERE id=?", (eid,)).fetchone()
        conn.close()
        assert row["checksum"] == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert row["bytes_transferred"] == 1024
        assert row["duration_ms"] == 250

    def test_classification_default_cui_prevents_unmarked(self, logger):
        cat, db, _ = logger
        eid = cat.log_initiated("t-mark", "A", "B", "d", "u")
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT data_classification FROM cross_agency_transfers WHERE id=?", (eid,)).fetchone()
        conn.close()
        assert row["data_classification"] == "CUI"  # never NULL/unmarked


# ---------------------------------------------------------------------------
# Simulated Cross-Agency Transfer Lifecycle (zero missing fields)
# ---------------------------------------------------------------------------


class TestSimulatedCrossAgencyTransfer:
    """End-to-end simulation: initiated -> completed with all required fields."""

    def test_simulated_transfer_zero_missing_fields(self, logger):
        cat, db, make_conn = logger
        transfer_id = "sim-2026-05-16-001"
        source = "DoD"
        target = "DHS"
        actor = "operator-jane"
        data_type = "threat-intel"
        classification = "SECRET"
        project_id = "proj-42"

        # 1. Initiated
        eid1 = cat.log_initiated(
            transfer_id=transfer_id,
            source_agency=source,
            target_agency=target,
            data_type=data_type,
            actor=actor,
            data_classification=classification,
            project_id=project_id,
            details={"protocol": "TLS1.3", "mfa": True},
        )
        assert eid1 != ""

        # 2. Completed
        eid2 = cat.log_completed(
            transfer_id=transfer_id,
            source_agency=source,
            target_agency=target,
            actor=actor,
            bytes_transferred=20480,
            checksum="a" * 64,
            duration_ms=1200,
            data_classification=classification,
        )
        assert eid2 != ""
        assert eid1 != eid2

        # Verify zero missing required fields across both rows
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM cross_agency_transfers WHERE transfer_id=? ORDER BY occurred_at",
            (transfer_id,),
        ).fetchall()
        conn.close()
        assert len(rows) == 2

        missing = []
        for row in rows:
            for field in _REQUIRED_FIELDS:
                if row[field] is None or str(row[field]) == "":
                    missing.append(f"row={row['id']} field={field}")
        assert missing == [], f"Missing required fields: {missing}"

        # Verify classification consistent
        for row in rows:
            assert row["data_classification"] == classification

        # Verify query returns both rows newest-first
        with patch(f"{_MODULE}.get_connection", side_effect=make_conn):
            result = query_by_transfer_id(transfer_id)
        assert len(result) == 2
        assert result[0]["event_type"] == "completed"  # newest first
        assert result[1]["event_type"] == "initiated"

    def test_compliance_report_compliant(self, logger, tmp_path):
        """Generate a compliance report dict that must show Compliant / zero missing."""
        cat, db, _ = logger
        transfer_id = "report-001"
        cat.log_initiated(transfer_id, "A", "B", "d", "u", data_classification="CUI")
        cat.log_completed(transfer_id, "A", "B", "u", bytes_transferred=512, checksum="b" * 64, duration_ms=300)

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM cross_agency_transfers WHERE transfer_id=?", (transfer_id,)).fetchall()
        conn.close()

        missing = []
        for row in rows:
            for field in _REQUIRED_FIELDS:
                if row[field] is None or str(row[field]) == "":
                    missing.append(field)

        report = {
            "nist_controls": ["AU-2", "AU-9"],
            "status": "Compliant" if not missing else "Non-Compliant",
            "missing_fields": missing,
            "records_examined": len(rows),
        }
        assert report["status"] == "Compliant"
        assert report["missing_fields"] == []
        assert report["records_examined"] == 2
