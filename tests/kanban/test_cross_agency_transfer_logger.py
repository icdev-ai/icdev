#!/usr/bin/env python3
# CUI // SP-CTI
"""7 unit tests for tools/audit/cross_agency_transfer_logger.py (NIST AU-2, AU-9)."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path):
    """Return a sqlite3 connection backed by a temp file with the CAT table."""
    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()
    return db_path, conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_log_initiated_inserts_initiated_row(tmp_path):
    """log_initiated() must insert a row with event_type='initiated'."""
    db_path, conn = _make_db(tmp_path)
    conn.close()

    from tools.audit.cross_agency_transfer_logger import CrossAgencyTransferLogger

    logger = CrossAgencyTransferLogger()
    with patch(
        "tools.audit.cross_agency_transfer_logger.get_connection",
        return_value=sqlite3.connect(str(db_path)),
    ):
        event_id = logger.log_initiated(
            transfer_id="xfr-001",
            source_agency="DHS",
            target_agency="DOD",
            data_type="threat_intel",
            actor="analyst@dhs.gov",
            data_classification="CUI",
            project_id="proj-alpha",
        )

    assert event_id != ""
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    row = conn2.execute(
        "SELECT * FROM cross_agency_transfers WHERE id=?", (event_id,)
    ).fetchone()
    assert row is not None
    assert row["event_type"] == "initiated"
    assert row["transfer_id"] == "xfr-001"
    assert row["source_agency"] == "DHS"
    assert row["target_agency"] == "DOD"
    assert row["data_classification"] == "CUI"
    conn2.close()


def test_log_completed_inserts_completed_row(tmp_path):
    """log_completed() must insert a row with event_type='completed'."""
    db_path, conn = _make_db(tmp_path)
    conn.close()

    from tools.audit.cross_agency_transfer_logger import CrossAgencyTransferLogger

    logger = CrossAgencyTransferLogger()
    with patch(
        "tools.audit.cross_agency_transfer_logger.get_connection",
        return_value=sqlite3.connect(str(db_path)),
    ):
        event_id = logger.log_completed(
            transfer_id="xfr-002",
            source_agency="FBI",
            target_agency="NSA",
            actor="system",
            bytes_transferred=204800,
            checksum="sha256:abc123",
            duration_ms=850,
        )

    assert event_id != ""
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    row = conn2.execute(
        "SELECT * FROM cross_agency_transfers WHERE id=?", (event_id,)
    ).fetchone()
    assert row is not None
    assert row["event_type"] == "completed"
    assert row["bytes_transferred"] == 204800
    assert row["checksum"] == "sha256:abc123"
    assert row["duration_ms"] == 850
    conn2.close()


def test_log_failed_inserts_failed_row(tmp_path):
    """log_failed() must insert a row with event_type='failed'."""
    db_path, conn = _make_db(tmp_path)
    conn.close()

    from tools.audit.cross_agency_transfer_logger import CrossAgencyTransferLogger

    logger = CrossAgencyTransferLogger()
    with patch(
        "tools.audit.cross_agency_transfer_logger.get_connection",
        return_value=sqlite3.connect(str(db_path)),
    ):
        event_id = logger.log_failed(
            transfer_id="xfr-003",
            source_agency="CIA",
            target_agency="DIA",
            actor="gateway",
            rejection_reason="Connection timeout",
            error_code="ERR_TIMEOUT",
        )

    assert event_id != ""
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    row = conn2.execute(
        "SELECT * FROM cross_agency_transfers WHERE id=?", (event_id,)
    ).fetchone()
    assert row is not None
    assert row["event_type"] == "failed"
    assert row["rejection_reason"] == "Connection timeout"
    assert row["error_code"] == "ERR_TIMEOUT"
    conn2.close()


def test_log_rejected_inserts_rejected_row(tmp_path):
    """log_rejected() must insert a row with event_type='rejected'."""
    db_path, conn = _make_db(tmp_path)
    conn.close()

    from tools.audit.cross_agency_transfer_logger import CrossAgencyTransferLogger

    logger = CrossAgencyTransferLogger()
    with patch(
        "tools.audit.cross_agency_transfer_logger.get_connection",
        return_value=sqlite3.connect(str(db_path)),
    ):
        event_id = logger.log_rejected(
            transfer_id="xfr-004",
            source_agency="DEA",
            target_agency="ATF",
            reviewed_by="security-officer",
            rejection_reason="Classification mismatch — recipient not cleared",
            data_classification="CUI//SP-CTI",
        )

    assert event_id != ""
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    row = conn2.execute(
        "SELECT * FROM cross_agency_transfers WHERE id=?", (event_id,)
    ).fetchone()
    assert row is not None
    assert row["event_type"] == "rejected"
    assert row["actor"] == "security-officer"
    assert "Classification mismatch" in row["rejection_reason"]
    conn2.close()


def test_no_update_or_delete_issued(tmp_path):
    """_insert() must only call INSERT, never UPDATE or DELETE (NIST AU-9)."""
    db_path, conn = _make_db(tmp_path)
    conn.close()

    from tools.audit.cross_agency_transfer_logger import CrossAgencyTransferLogger

    executed_sql: list[str] = []

    class _RecordingConn:
        def __init__(self, real):
            self._real = real

        def execute(self, sql, *args, **kwargs):
            executed_sql.append(sql.strip().upper())
            return self._real.execute(sql, *args, **kwargs)

        def commit(self):
            return self._real.commit()

        def close(self):
            return self._real.close()

    real_conn = sqlite3.connect(str(db_path))
    real_conn.row_factory = sqlite3.Row
    recording_conn = _RecordingConn(real_conn)

    logger = CrossAgencyTransferLogger()
    with patch(
        "tools.audit.cross_agency_transfer_logger.get_connection",
        return_value=recording_conn,
    ):
        logger.log_initiated(
            transfer_id="xfr-005",
            source_agency="DHS",
            target_agency="DOD",
            data_type="sigint",
            actor="test-actor",
        )

    real_conn.close()

    for sql in executed_sql:
        assert not sql.startswith("UPDATE"), f"Unexpected UPDATE: {sql}"
        assert not sql.startswith("DELETE"), f"Unexpected DELETE: {sql}"

    insert_calls = [s for s in executed_sql if s.startswith("INSERT")]
    assert len(insert_calls) >= 1


def test_degrades_gracefully_when_table_missing(tmp_path):
    """When cross_agency_transfers table is absent, returns '' without raising."""
    db_path = tmp_path / "empty.db"
    empty_conn = sqlite3.connect(str(db_path))
    empty_conn.close()

    from tools.audit.cross_agency_transfer_logger import CrossAgencyTransferLogger

    logger = CrossAgencyTransferLogger()
    empty_conn2 = sqlite3.connect(str(db_path))
    empty_conn2.row_factory = sqlite3.Row

    with patch(
        "tools.audit.cross_agency_transfer_logger.get_connection",
        return_value=empty_conn2,
    ):
        result = logger.log_initiated(
            transfer_id="xfr-006",
            source_agency="AGENCY_A",
            target_agency="AGENCY_B",
            data_type="test",
            actor="test",
        )

    empty_conn2.close()
    assert result == ""


def test_cli_query_returns_valid_json(tmp_path):
    """CLI --query --transfer-id returns a valid JSON list."""
    db_path, conn = _make_db(tmp_path)

    conn.execute(
        """
        INSERT INTO cross_agency_transfers
            (id, transfer_id, event_type, source_agency, target_agency,
             data_classification, actor, occurred_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "evt-cli-001", "xfr-cli", "initiated",
            "AGENCY_A", "AGENCY_B", "CUI", "test", "2026-05-16T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    env = os.environ.copy()
    env["ICDEV_DB_PATH"] = str(db_path)
    env["ICDEV_STORAGE_BACKEND"] = "sqlite"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "audit" / "cross_agency_transfer_logger.py"),
            "--query",
            "--transfer-id", "xfr-cli",
            "--json",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["transfer_id"] == "xfr-cli"
