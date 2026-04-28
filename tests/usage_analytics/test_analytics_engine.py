# CUI // SP-CTI
"""6 tests for usage analytics: EventCollector + AnalyticsEngine."""

import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def analytics_db(icdev_db, monkeypatch):
    """Wrap conftest icdev_db, pointing ICDEV_DB_PATH at the temp DB."""
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    return icdev_db


def _db_rows(db_path, query, params=()):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return rows


def _insert_event(db_path, *, feature_tag, user_session, status_code=200):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO usage_events
           (id, route, method, status_code, duration_ms, user_session, feature_tag, occurred_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), "/api/test", "GET", status_code, 50,
         user_session, feature_tag, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def _insert_aggregate(db_path, *, agg_id, feature_tag, adoption_score, error_count=0):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO usage_aggregates
           (id, feature_tag, date, hit_count, unique_sessions, error_count,
            avg_duration_ms, adoption_score, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (agg_id, feature_tag, today, 1, 1, error_count, 50.0, adoption_score,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_record_inserts_row_with_hashed_ip(analytics_db):
    """EventCollector.record() writes usage_events row; IP stored as sha256 hash."""
    from tools.usage_analytics.event_collector import EventCollector

    ip = "10.0.0.42"
    event_id = EventCollector().record(
        route="/api/projects",
        method="GET",
        status_code=200,
        duration_ms=30,
        ip=ip,
        feature_tag="projects",
    )

    assert event_id, "record() must return a non-empty event ID on success"
    rows = _db_rows(analytics_db, "SELECT * FROM usage_events WHERE id = ?", (event_id,))
    assert len(rows) == 1
    expected_hash = hashlib.sha256(ip.encode()).hexdigest()
    assert rows[0]["ip_hash"] == expected_hash
    assert rows[0]["ip_hash"] != ip  # raw IP must not be stored


def test_excluded_route_not_recorded(analytics_db):
    """Routes in exclude_routes config are silently skipped."""
    from tools.usage_analytics.event_collector import EventCollector

    # /health is excluded per args/usage_analytics_config.yaml
    event_id = EventCollector().record(route="/health", method="GET", status_code=200, duration_ms=2)

    assert event_id == ""
    assert len(_db_rows(analytics_db, "SELECT id FROM usage_events")) == 0


def test_aggregate_produces_correct_hit_count(analytics_db):
    """aggregate() rolls up usage_events into usage_aggregates with correct hit_count."""
    for i in range(3):
        _insert_event(analytics_db, feature_tag="kanban", user_session=f"sess-{i}")

    from tools.usage_analytics.analytics_engine import AnalyticsEngine

    count = AnalyticsEngine().aggregate(days=1)

    assert count >= 1
    rows = _db_rows(analytics_db, "SELECT * FROM usage_aggregates WHERE feature_tag = ?", ("kanban",))
    assert len(rows) == 1
    assert rows[0]["hit_count"] == 3


def test_score_returns_feature_adoption_result(analytics_db):
    """score() returns FeatureAdoptionResult instances that each have an adoption_score."""
    _insert_aggregate(analytics_db, agg_id="agg-genesis", feature_tag="genesis", adoption_score=0.5)

    from tools.usage_analytics.analytics_engine import AnalyticsEngine, FeatureAdoptionResult

    results = AnalyticsEngine().score()

    assert len(results) >= 1
    first = results[0]
    assert isinstance(first, FeatureAdoptionResult)
    assert hasattr(first, "adoption_score")
    assert first.adoption_score == pytest.approx(0.5)


def test_surface_signals_registers_low_adoption_feature(analytics_db):
    """surface_signals() inserts an innovation_signals row for a low-adoption feature."""
    # adoption_score=0.01 < low_adoption_threshold (0.05)
    _insert_aggregate(analytics_db, agg_id="agg-rare", feature_tag="rare_feature", adoption_score=0.01)

    from tools.usage_analytics.analytics_engine import AnalyticsEngine

    inserted = AnalyticsEngine().surface_signals()

    assert inserted >= 1
    rows = _db_rows(
        analytics_db,
        "SELECT * FROM innovation_signals WHERE category = ?",
        ("low_adoption",),
    )
    assert len(rows) >= 1
    assert "rare_feature" in rows[0]["title"]


def test_event_collector_degrades_silently_when_table_missing(tmp_path, monkeypatch):
    """EventCollector.record() returns '' and does not raise if usage_events table is absent."""
    empty_db = tmp_path / "empty.db"
    sqlite3.connect(str(empty_db)).close()  # create valid but empty DB
    monkeypatch.setenv("ICDEV_DB_PATH", str(empty_db))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    from tools.usage_analytics.event_collector import EventCollector

    result = EventCollector().record(route="/api/test", method="GET", status_code=200, duration_ms=10)

    assert result == ""
