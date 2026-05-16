# CUI // SP-CTI
"""Functional requirement tests for IL5 ingestion and display flow.

Requirement: The system must support IL5 data ingestion and display within
30 seconds of source publication.

Acceptance criteria:
- Full ingest+display pipeline completes in < 30 seconds for fast data.
- Slow data (>= 30 seconds from publish to display) triggers a TimeoutError.
- Output matches the IL5 display specification (classification, impact_level,
  sla_threshold_s, summary fields).

NIST 800-53: AU-2, AU-12, SC-28, SI-12.

Run: pytest tests/test_il5_requirement.py -v
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.il5.il5_display_service import render_il5_ui
from tools.il5.il5_ingestion_service import fetch_il5_data
from tools.il5.ingestion import (
    IL5_CLASSIFICATION,
    IL5_IMPACT_LEVEL,
    SLA_SECONDS,
    create_il5_schema,
    get_il5_events,
    ingest_il5_event,
)
from tools.pipeline.sla_handler import (
    IL5_PIPELINE_TIMEOUT_SECONDS,
    check_il5_timeout,
    il5_timeout_context,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _UnclosableConn:
    """Wraps a connection so that close() calls from tested code are no-ops."""

    def __init__(self, conn: sqlite3.Connection):
        self._c = conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._c, name)


def _make_feed_response(items: list) -> MagicMock:
    """Build a mock usable as the return value of urllib.request.urlopen."""
    body = json.dumps(items).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _fast_feed_items() -> list:
    """Two IL5 feed items published within the last 10 seconds (SLA = met)."""
    now = datetime.now(timezone.utc)
    return [
        {
            "source_id": "feed-fast-001",
            "content": "IL5/CUI content block A",
            "published_at": (now - timedelta(seconds=5)).isoformat(),
        },
        {
            "source_id": "feed-fast-002",
            "content": "IL5/CUI content block B",
            "published_at": (now - timedelta(seconds=3)).isoformat(),
        },
    ]


def _slow_feed_item() -> list:
    """One IL5 feed item published 45 seconds ago (SLA = violated)."""
    pub = datetime.now(timezone.utc) - timedelta(seconds=45)
    return [
        {
            "source_id": "feed-slow-001",
            "content": "overdue IL5/CUI data",
            "published_at": pub.isoformat(),
        }
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mem_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_il5_schema(conn)
    return conn


@pytest.fixture
def mem_db_path(tmp_path):
    db_path = tmp_path / "il5_req_test.db"
    conn = sqlite3.connect(str(db_path))
    create_il5_schema(conn)
    conn.close()
    return db_path


# ═══════════════════════════════════════════════════════════════════════
# TestFullFlowUnder30Seconds — happy-path functional requirement
# ═══════════════════════════════════════════════════════════════════════


class TestFullFlowUnder30Seconds:
    """Full ingest + render pipeline must complete under 30 seconds."""

    def test_pipeline_completes_under_30_seconds(self, mem_db_path):
        """Wall-clock elapsed time for fetch_il5_data + render_il5_ui must be < 30s."""
        mock_resp = _make_feed_response(_fast_feed_items())
        with patch("urllib.request.urlopen", return_value=mock_resp):
            t0 = time.monotonic()
            records = fetch_il5_data(
                feed_url="http://fake-il5-feed",
                db_path=mem_db_path,
            )
            output = render_il5_ui(records)
            elapsed = time.monotonic() - t0

        assert elapsed < SLA_SECONDS, (
            f"Pipeline took {elapsed:.3f}s — exceeded {SLA_SECONDS}s SLA"
        )
        assert output  # non-empty output

    def test_output_is_valid_json(self, mem_db_path):
        mock_resp = _make_feed_response(_fast_feed_items())
        with patch("urllib.request.urlopen", return_value=mock_resp):
            records = fetch_il5_data(feed_url="http://fake-il5-feed", db_path=mem_db_path)
        payload = json.loads(render_il5_ui(records))
        assert isinstance(payload, dict)

    def test_output_classification_is_cui(self, mem_db_path):
        mock_resp = _make_feed_response(_fast_feed_items())
        with patch("urllib.request.urlopen", return_value=mock_resp):
            records = fetch_il5_data(feed_url="http://fake-il5-feed", db_path=mem_db_path)
        payload = json.loads(render_il5_ui(records))
        assert payload["classification"] == IL5_CLASSIFICATION

    def test_output_impact_level_is_il5(self, mem_db_path):
        mock_resp = _make_feed_response(_fast_feed_items())
        with patch("urllib.request.urlopen", return_value=mock_resp):
            records = fetch_il5_data(feed_url="http://fake-il5-feed", db_path=mem_db_path)
        payload = json.loads(render_il5_ui(records))
        assert payload["impact_level"] == IL5_IMPACT_LEVEL

    def test_output_sla_threshold_is_30(self, mem_db_path):
        mock_resp = _make_feed_response(_fast_feed_items())
        with patch("urllib.request.urlopen", return_value=mock_resp):
            records = fetch_il5_data(feed_url="http://fake-il5-feed", db_path=mem_db_path)
        payload = json.loads(render_il5_ui(records))
        assert payload["sla_threshold_s"] == SLA_SECONDS

    def test_output_contains_summary(self, mem_db_path):
        mock_resp = _make_feed_response(_fast_feed_items())
        with patch("urllib.request.urlopen", return_value=mock_resp):
            records = fetch_il5_data(feed_url="http://fake-il5-feed", db_path=mem_db_path)
        payload = json.loads(render_il5_ui(records))
        assert "summary" in payload

    def test_output_summary_has_required_keys(self, mem_db_path):
        mock_resp = _make_feed_response(_fast_feed_items())
        with patch("urllib.request.urlopen", return_value=mock_resp):
            records = fetch_il5_data(feed_url="http://fake-il5-feed", db_path=mem_db_path)
        summary = json.loads(render_il5_ui(records))["summary"]
        for key in ("total", "sla_met", "sla_violated", "sla_unknown", "compliance_pct"):
            assert key in summary, f"Missing summary key: {key}"

    def test_output_table_has_correct_row_count(self, mem_db_path):
        items = _fast_feed_items()
        mock_resp = _make_feed_response(items)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            records = fetch_il5_data(feed_url="http://fake-il5-feed", db_path=mem_db_path)
        payload = json.loads(render_il5_ui(records, component="table"))
        assert "table" in payload
        assert len(payload["table"]["rows"]) == len(items)

    def test_output_table_columns_present(self, mem_db_path):
        mock_resp = _make_feed_response(_fast_feed_items())
        with patch("urllib.request.urlopen", return_value=mock_resp):
            records = fetch_il5_data(feed_url="http://fake-il5-feed", db_path=mem_db_path)
        payload = json.loads(render_il5_ui(records, component="table"))
        expected_cols = {
            "id",
            "source_id",
            "classification",
            "impact_level",
            "ingested_at",
            "display_latency_s",
            "sla_status",
        }
        actual_cols = set(payload["table"]["columns"])
        assert expected_cols.issubset(actual_cols)

    def test_sla_met_for_fast_data(self, mem_db_path):
        items = _fast_feed_items()
        mock_resp = _make_feed_response(items)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            records = fetch_il5_data(feed_url="http://fake-il5-feed", db_path=mem_db_path)
        summary = json.loads(render_il5_ui(records))["summary"]
        assert summary["sla_met"] == len(items)
        assert summary["sla_violated"] == 0

    def test_chart_component_output(self, mem_db_path):
        mock_resp = _make_feed_response(_fast_feed_items())
        with patch("urllib.request.urlopen", return_value=mock_resp):
            records = fetch_il5_data(feed_url="http://fake-il5-feed", db_path=mem_db_path)
        payload = json.loads(render_il5_ui(records, component="chart"))
        assert payload["component"] == "chart"
        assert "chart" in payload
        assert "datasets" in payload["chart"]

    def test_feed_unreachable_returns_empty_gracefully(self, mem_db_path):
        """When the feed endpoint is down, fetch_il5_data returns [] without raising."""
        import urllib.error

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            records = fetch_il5_data(feed_url="http://unreachable", db_path=mem_db_path)

        assert isinstance(records, list)
        assert records == []

    def test_empty_feed_produces_empty_table(self, mem_db_path):
        mock_resp = _make_feed_response([])
        with patch("urllib.request.urlopen", return_value=mock_resp):
            records = fetch_il5_data(feed_url="http://fake-il5-feed", db_path=mem_db_path)
        assert records == []
        summary = json.loads(render_il5_ui(records))["summary"]
        assert summary["total"] == 0

    def test_il5_timeout_context_passes_for_fast_operation(self):
        """Context manager must not raise when the block is virtually instant."""
        with il5_timeout_context("fast-op"):
            pass  # no-op — must not raise


# ═══════════════════════════════════════════════════════════════════════
# TestSlowDataTimeoutEdgeCases — SLA violation and timeout enforcement
# ═══════════════════════════════════════════════════════════════════════


class TestSlowDataTimeoutEdgeCases:
    """Slow data (>= 30s elapsed) must trigger TimeoutError; SLA must be recorded."""

    def test_check_sla_timeout_raises_at_exactly_30s(self):
        """TimeoutError must be raised when elapsed == SLA_SECONDS."""
        fake_start = time.monotonic() - IL5_PIPELINE_TIMEOUT_SECONDS
        with pytest.raises(TimeoutError):
            check_il5_timeout(fake_start, label="exact-boundary")

    def test_check_sla_timeout_raises_beyond_30s(self):
        """TimeoutError must be raised when elapsed > SLA_SECONDS."""
        fake_start = time.monotonic() - (IL5_PIPELINE_TIMEOUT_SECONDS + 5)
        with pytest.raises(TimeoutError):
            check_il5_timeout(fake_start, label="beyond-boundary")

    def test_check_sla_timeout_error_message_contains_label(self):
        """The TimeoutError message must include the operation label."""
        fake_start = time.monotonic() - (IL5_PIPELINE_TIMEOUT_SECONDS + 1)
        with pytest.raises(TimeoutError, match="my-slow-op"):
            check_il5_timeout(fake_start, label="my-slow-op")

    def test_check_sla_timeout_passes_before_30s(self):
        """check_il5_timeout must NOT raise when elapsed < SLA_SECONDS."""
        fake_start = time.monotonic() - (IL5_PIPELINE_TIMEOUT_SECONDS - 10)
        check_il5_timeout(fake_start, label="within-sla")  # must not raise

    def test_context_manager_raises_on_slow_exit(self):
        """il5_timeout_context raises TimeoutError when block exceeds 30 seconds."""
        with pytest.raises(TimeoutError):
            with patch("tools.pipeline.sla_handler.time") as mock_time:
                fake_start = 1000.0
                mock_time.monotonic.side_effect = [
                    fake_start,
                    fake_start + IL5_PIPELINE_TIMEOUT_SECONDS + 1.0,
                ]
                with il5_timeout_context("slow-pipeline"):
                    pass

    def test_context_manager_does_not_raise_under_30s(self):
        """il5_timeout_context must not raise when block completes in < 30 seconds."""
        with patch("tools.pipeline.sla_handler.time") as mock_time:
            fake_start = 1000.0
            mock_time.monotonic.side_effect = [
                fake_start,
                fake_start + 10.0,
            ]
            with il5_timeout_context("fast-pipeline"):
                pass  # must not raise

    def test_slow_event_sla_violation_recorded_in_db(self, mem_db_path):
        """An event published > 30s ago must persist with sla_met = 0."""
        pub_time = datetime.now(timezone.utc) - timedelta(seconds=35)
        event_id = ingest_il5_event(
            "slow-src",
            "slow IL5 content",
            source_published_at=pub_time,
            db_path=mem_db_path,
        )
        conn = sqlite3.connect(str(mem_db_path))
        row = conn.execute(
            "SELECT sla_met, display_latency_s FROM il5_ingestion_log WHERE id=?",
            (event_id,),
        ).fetchone()
        conn.close()
        assert row[0] == 0, "sla_met must be 0 (violated) for event published 35s ago"
        assert row[1] > SLA_SECONDS

    def test_mixed_fast_and_slow_summary(self, mem_db_path):
        """Summary reflects correct SLA met/violated counts for a mixed event set."""
        pub_fast = datetime.now(timezone.utc) - timedelta(seconds=5)
        pub_slow = datetime.now(timezone.utc) - timedelta(seconds=40)
        ingest_il5_event("mix-fast", "fast payload", source_published_at=pub_fast, db_path=mem_db_path)
        ingest_il5_event("mix-slow", "slow payload", source_published_at=pub_slow, db_path=mem_db_path)

        records = get_il5_events(db_path=mem_db_path)
        summary = json.loads(render_il5_ui(records))["summary"]
        assert summary["sla_met"] == 1
        assert summary["sla_violated"] == 1
        assert summary["total"] == 2

    def test_sla_timeout_constant_is_30(self):
        assert IL5_PIPELINE_TIMEOUT_SECONDS == 30
        assert SLA_SECONDS == 30

    def test_slow_feed_data_rendered_as_violated(self, mem_db_path):
        """Events with publish times > 30s ago must have sla_status = 'VIOLATED'."""
        mock_resp = _make_feed_response(_slow_feed_item())
        with patch("urllib.request.urlopen", return_value=mock_resp):
            records = fetch_il5_data(feed_url="http://fake-slow-feed", db_path=mem_db_path)
        payload = json.loads(render_il5_ui(records, component="table"))
        rows = payload["table"]["rows"]
        assert len(rows) == 1
        assert rows[0]["sla_status"] == "VIOLATED"

    def test_slow_feed_summary_shows_zero_compliance(self, mem_db_path):
        """A batch of only-slow events must have compliance_pct = 0."""
        pub_old = datetime.now(timezone.utc) - timedelta(seconds=60)
        items = [
            {"source_id": f"old-{i}", "content": f"data {i}", "published_at": pub_old.isoformat()}
            for i in range(3)
        ]
        mock_resp = _make_feed_response(items)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            records = fetch_il5_data(feed_url="http://fake-slow-feed", db_path=mem_db_path)
        summary = json.loads(render_il5_ui(records))["summary"]
        assert summary["sla_violated"] == 3
        assert summary["sla_met"] == 0
        assert summary["compliance_pct"] == 0.0
