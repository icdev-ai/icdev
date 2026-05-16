# CUI // SP-CTI
"""TDD tests for IL5 data ingestion and 30-second display SLA.

Requirement: The system must support IL5 data ingestion and display within
30 seconds of source publication.

NIST 800-53 controls: AU-2 (Audit Events), AU-12 (Audit Record Generation),
SC-28 (Protection of Information at Rest), SI-12 (Information Management).

Run: pytest tests/test_il5_ingestion.py -v
"""

from __future__ import annotations

import sqlite3
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.il5.ingestion import (
    IL5_TABLE,
    IL5_CLASSIFICATION,
    IL5_IMPACT_LEVEL,
    SLA_SECONDS,
    create_il5_schema,
    ingest_il5_event,
    get_il5_events,
    check_sla_compliance,
    get_sla_summary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _UnclosableConn:
    """Wraps in-memory conn so tested code's finally-close() doesn't destroy it."""

    def __init__(self, conn: sqlite3.Connection):
        self._c = conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._c, name)


@pytest.fixture
def mem_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_il5_schema(conn)
    return conn


@pytest.fixture
def mem_db_path(tmp_path):
    db_path = tmp_path / "il5_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    create_il5_schema(conn)
    conn.close()
    return db_path


# ═══════════════════════════════════════════════════════════════════════
# TestIL5Constants
# ═══════════════════════════════════════════════════════════════════════


class TestIL5Constants:
    def test_table_name(self):
        assert IL5_TABLE == "il5_ingestion_log"

    def test_classification_is_cui(self):
        assert IL5_CLASSIFICATION == "CUI"

    def test_impact_level(self):
        assert IL5_IMPACT_LEVEL == "IL5"

    def test_sla_seconds(self):
        assert SLA_SECONDS == 30


# ═══════════════════════════════════════════════════════════════════════
# TestCreateSchema
# ═══════════════════════════════════════════════════════════════════════


class TestCreateSchema:
    def test_table_created(self):
        conn = sqlite3.connect(":memory:")
        create_il5_schema(conn)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (IL5_TABLE,),
        ).fetchall()
        assert len(rows) == 1

    def test_idempotent(self):
        conn = sqlite3.connect(":memory:")
        create_il5_schema(conn)
        create_il5_schema(conn)  # should not raise


# ═══════════════════════════════════════════════════════════════════════
# TestIngestIL5Event
# ═══════════════════════════════════════════════════════════════════════


class TestIngestIL5Event:
    def test_returns_event_id(self, mem_db):
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            event_id = ingest_il5_event("src-001", "payload data")
        assert event_id is not None
        assert len(event_id) > 0

    def test_event_stored_in_db(self, mem_db):
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            event_id = ingest_il5_event("src-002", "some IL5 content")
        row = mem_db.execute(
            f"SELECT * FROM {IL5_TABLE} WHERE id=?", (event_id,)
        ).fetchone()
        assert row is not None
        assert row["source_id"] == "src-002"

    def test_classification_is_cui(self, mem_db):
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            event_id = ingest_il5_event("src-003", "classified payload")
        row = mem_db.execute(
            f"SELECT classification FROM {IL5_TABLE} WHERE id=?", (event_id,)
        ).fetchone()
        assert row["classification"] == "CUI"

    def test_impact_level_is_il5(self, mem_db):
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            event_id = ingest_il5_event("src-004", "il5 payload")
        row = mem_db.execute(
            f"SELECT impact_level FROM {IL5_TABLE} WHERE id=?", (event_id,)
        ).fetchone()
        assert row["impact_level"] == "IL5"

    def test_content_hash_stored(self, mem_db):
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            event_id = ingest_il5_event("src-005", "hashed content")
        row = mem_db.execute(
            f"SELECT content_hash FROM {IL5_TABLE} WHERE id=?", (event_id,)
        ).fetchone()
        assert row["content_hash"] is not None
        assert len(row["content_hash"]) == 64  # SHA-256 hex

    def test_ingested_at_recorded(self, mem_db):
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            event_id = ingest_il5_event("src-006", "timed content")
        row = mem_db.execute(
            f"SELECT ingested_at FROM {IL5_TABLE} WHERE id=?", (event_id,)
        ).fetchone()
        assert row["ingested_at"] is not None
        # Must parse as valid ISO datetime
        dt = datetime.fromisoformat(row["ingested_at"].replace("Z", "+00:00"))
        assert dt is not None

    def test_source_published_at_stored_when_provided(self, mem_db):
        pub_time = datetime.now(timezone.utc) - timedelta(seconds=10)
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            event_id = ingest_il5_event("src-007", "pub content", source_published_at=pub_time)
        row = mem_db.execute(
            f"SELECT source_published_at FROM {IL5_TABLE} WHERE id=?", (event_id,)
        ).fetchone()
        assert row["source_published_at"] is not None

    def test_sla_met_when_within_30s(self, mem_db):
        pub_time = datetime.now(timezone.utc) - timedelta(seconds=5)
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            event_id = ingest_il5_event("src-008", "fast content", source_published_at=pub_time)
        row = mem_db.execute(
            f"SELECT sla_met FROM {IL5_TABLE} WHERE id=?", (event_id,)
        ).fetchone()
        assert row["sla_met"] == 1

    def test_sla_not_met_when_beyond_30s(self, mem_db):
        pub_time = datetime.now(timezone.utc) - timedelta(seconds=35)
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            event_id = ingest_il5_event("src-009", "slow content", source_published_at=pub_time)
        row = mem_db.execute(
            f"SELECT sla_met FROM {IL5_TABLE} WHERE id=?", (event_id,)
        ).fetchone()
        assert row["sla_met"] == 0

    def test_sla_none_when_no_pub_time(self, mem_db):
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            event_id = ingest_il5_event("src-010", "no pub time")
        row = mem_db.execute(
            f"SELECT sla_met, display_latency_s FROM {IL5_TABLE} WHERE id=?",
            (event_id,),
        ).fetchone()
        assert row["sla_met"] is None
        assert row["display_latency_s"] is None

    def test_metadata_stored_as_json(self, mem_db):
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            event_id = ingest_il5_event(
                "src-011", "meta content", metadata={"key": "value", "num": 42}
            )
        import json
        row = mem_db.execute(
            f"SELECT metadata FROM {IL5_TABLE} WHERE id=?", (event_id,)
        ).fetchone()
        parsed = json.loads(row["metadata"])
        assert parsed["key"] == "value"
        assert parsed["num"] == 42

    def test_duplicate_source_gets_unique_id(self, mem_db):
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            id1 = ingest_il5_event("src-dup", "content A")
            id2 = ingest_il5_event("src-dup", "content B")
        assert id1 != id2

    def test_via_db_path(self, mem_db_path):
        event_id = ingest_il5_event("src-path", "path content", db_path=mem_db_path)
        conn = sqlite3.connect(str(mem_db_path))
        row = conn.execute(
            f"SELECT id FROM il5_ingestion_log WHERE id=?", (event_id,)
        ).fetchone()
        conn.close()
        assert row is not None


# ═══════════════════════════════════════════════════════════════════════
# TestGetIL5Events
# ═══════════════════════════════════════════════════════════════════════


class TestGetIL5Events:
    def _seed(self, mem_db, count=3):
        ids = []
        for i in range(count):
            with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
                ids.append(ingest_il5_event(f"seed-{i}", f"content {i}"))
        return ids

    def test_returns_list(self, mem_db):
        self._seed(mem_db, 2)
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            events = get_il5_events()
        assert isinstance(events, list)

    def test_returns_all_when_no_filter(self, mem_db):
        self._seed(mem_db, 3)
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            events = get_il5_events()
        assert len(events) == 3

    def test_since_filter(self, mem_db):
        self._seed(mem_db, 2)
        cursor = datetime.now(timezone.utc).isoformat()
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            ingest_il5_event("after-cursor", "new content")
            events = get_il5_events(since=cursor)
        assert len(events) == 1
        assert events[0]["source_id"] == "after-cursor"

    def test_limit_respected(self, mem_db):
        self._seed(mem_db, 5)
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            events = get_il5_events(limit=2)
        assert len(events) == 2

    def test_events_have_required_fields(self, mem_db):
        self._seed(mem_db, 1)
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            events = get_il5_events()
        e = events[0]
        for field in ("id", "source_id", "classification", "impact_level", "ingested_at"):
            assert field in e, f"Missing field: {field}"

    def test_empty_when_no_events(self, mem_db):
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            events = get_il5_events()
        assert events == []


# ═══════════════════════════════════════════════════════════════════════
# TestCheckSLACompliance
# ═══════════════════════════════════════════════════════════════════════


class TestCheckSLACompliance:
    def test_sla_met_returns_true(self, mem_db):
        pub = datetime.now(timezone.utc) - timedelta(seconds=5)
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            eid = ingest_il5_event("sla-ok", "ok", source_published_at=pub)
            result = check_sla_compliance(eid)
        assert result["sla_met"] is True
        assert result["latency_s"] <= 30

    def test_sla_violated_returns_false(self, mem_db):
        pub = datetime.now(timezone.utc) - timedelta(seconds=40)
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            eid = ingest_il5_event("sla-fail", "fail", source_published_at=pub)
            result = check_sla_compliance(eid)
        assert result["sla_met"] is False
        assert result["latency_s"] > 30

    def test_sla_unknown_when_no_pub_time(self, mem_db):
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            eid = ingest_il5_event("sla-none", "none")
            result = check_sla_compliance(eid)
        assert result["sla_met"] is None
        assert result["latency_s"] is None

    def test_nonexistent_event_returns_none(self, mem_db):
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            result = check_sla_compliance("does-not-exist")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# TestGetSLASummary
# ═══════════════════════════════════════════════════════════════════════


class TestGetSLASummary:
    def _seed_mixed(self, mem_db):
        pub_ok = datetime.now(timezone.utc) - timedelta(seconds=5)
        pub_fail = datetime.now(timezone.utc) - timedelta(seconds=40)
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            ingest_il5_event("s1", "c1", source_published_at=pub_ok)
            ingest_il5_event("s2", "c2", source_published_at=pub_ok)
            ingest_il5_event("s3", "c3", source_published_at=pub_fail)
            ingest_il5_event("s4", "c4")  # no pub time

    def test_summary_structure(self, mem_db):
        self._seed_mixed(mem_db)
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            summary = get_sla_summary()
        for key in ("total", "sla_met", "sla_violated", "sla_unknown", "compliance_pct"):
            assert key in summary, f"Missing key: {key}"

    def test_total_count(self, mem_db):
        self._seed_mixed(mem_db)
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            summary = get_sla_summary()
        assert summary["total"] == 4

    def test_sla_met_count(self, mem_db):
        self._seed_mixed(mem_db)
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            summary = get_sla_summary()
        assert summary["sla_met"] == 2

    def test_sla_violated_count(self, mem_db):
        self._seed_mixed(mem_db)
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            summary = get_sla_summary()
        assert summary["sla_violated"] == 1

    def test_sla_unknown_count(self, mem_db):
        self._seed_mixed(mem_db)
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            summary = get_sla_summary()
        assert summary["sla_unknown"] == 1

    def test_compliance_pct_calculation(self, mem_db):
        self._seed_mixed(mem_db)
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            summary = get_sla_summary()
        # 2 met out of 3 with known pub time → 66.67%
        assert abs(summary["compliance_pct"] - 66.67) < 1.0

    def test_empty_db_summary(self, mem_db):
        with patch("tools.il5.ingestion.get_connection", return_value=_UnclosableConn(mem_db)):
            summary = get_sla_summary()
        assert summary["total"] == 0
        assert summary["compliance_pct"] == 100.0
