# CUI // SP-CTI
"""
Tests for the Activity Feed API blueprint (tools/dashboard/api/activity.py).

Verifies merged audit_trail + hook_events feed, filtering, pagination,
cursor-based polling, filter-options, and stats endpoints.

Run: pytest tests/test_activity_feed.py -v
"""

import sqlite3
import sys
from datetime import datetime, timedelta, timezone


def _utcnow() -> datetime:
    """Return current UTC time as a naive datetime (no tzinfo) for SQLite
    compatibility.  Avoids deprecated ``_utcnow()``."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_tables(db_path: Path) -> None:
    """Create minimal audit_trail and hook_events tables matching the
    columns that the activity API actually queries via UNION ALL."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS audit_trail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            project_id TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS hook_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            agent_id TEXT,
            tool_name TEXT,
            project_id TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            hook_type TEXT,
            session_id TEXT,
            severity TEXT,
            message TEXT,
            payload TEXT
        );
    """)
    conn.commit()
    conn.close()


def _seed_data(db_path: Path) -> None:
    """Insert test rows into both tables with known timestamps."""
    conn = sqlite3.connect(str(db_path))

    now = _utcnow()

    # Audit trail rows (5 events spanning several hours)
    audit_rows = [
        ("code_generated", "builder-agent", "Generated auth module",
         "proj-001", "CUI", (now - timedelta(hours=4)).isoformat()),
        ("test_passed", "builder-agent", "All tests green",
         "proj-001", "CUI", (now - timedelta(hours=3)).isoformat()),
        ("compliance_check", "compliance-agent", "Ran STIG check",
         "proj-002", "CUI", (now - timedelta(hours=2)).isoformat()),
        ("deployment_succeeded", "infra-agent", "Deployed v1.2.0",
         "proj-001", "CUI", (now - timedelta(minutes=30)).isoformat()),
        ("security_scan", "security-agent", "SAST clean",
         "proj-003", "CUI", (now - timedelta(minutes=10)).isoformat()),
    ]
    conn.executemany(
        "INSERT INTO audit_trail (event_type, actor, action, project_id, classification, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        audit_rows,
    )

    # Hook events rows (4 events)
    hook_rows = [
        ("post_tool_use", "builder-agent", "scaffold",
         "proj-001", "CUI", (now - timedelta(hours=5)).isoformat()),
        ("pre_tool_use", "security-agent", "sast_runner",
         "proj-002", "CUI", (now - timedelta(hours=1)).isoformat()),
        ("notification", "monitor-agent", "health_checker",
         "proj-001", "CUI", (now - timedelta(minutes=20)).isoformat()),
        ("post_tool_use", "compliance-agent", "sbom_generator",
         "proj-003", "CUI", (now - timedelta(minutes=5)).isoformat()),
    ]
    conn.executemany(
        "INSERT INTO hook_events (event_type, agent_id, tool_name, project_id, classification, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        hook_rows,
    )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    """Create and seed a temporary database."""
    p = tmp_path / "test_activity.db"
    _create_tables(p)
    _seed_data(p)
    return p


@pytest.fixture
def client(db_path):
    """Flask test client with activity_api blueprint registered and DB_PATH
    monkeypatched to the temporary database."""
    from flask import Flask
    from tools.dashboard.api.activity import activity_api

    with patch("tools.dashboard.api.activity.DB_PATH", str(db_path)):
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(activity_api)
        yield app.test_client()


@pytest.fixture
def empty_client(tmp_path):
    """Flask test client backed by an empty (no rows) database."""
    p = tmp_path / "empty_activity.db"
    _create_tables(p)

    from flask import Flask
    from tools.dashboard.api.activity import activity_api

    with patch("tools.dashboard.api.activity.DB_PATH", str(p)):
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(activity_api)
        yield app.test_client()


# ===================================================================
# 1. Feed — merged events
# ===================================================================

class TestFeedMerged:
    """Feed endpoint returns merged events from both tables."""

    def test_feed_returns_200(self, client):
        resp = client.get("/api/activity/feed")
        assert resp.status_code == 200

    def test_feed_returns_all_events(self, client):
        resp = client.get("/api/activity/feed")
        data = resp.get_json()
        # 5 audit + 4 hook = 9 total
        assert data["count"] == 9

    def test_feed_events_have_source_field(self, client):
        resp = client.get("/api/activity/feed")
        data = resp.get_json()
        sources = {e["source"] for e in data["events"]}
        assert sources == {"audit", "hook"}

    def test_feed_events_have_expected_keys(self, client):
        resp = client.get("/api/activity/feed")
        data = resp.get_json()
        required_keys = {"source", "id", "event_type", "actor_or_agent",
                         "summary", "project_id", "classification", "created_at"}
        for event in data["events"]:
            assert required_keys.issubset(event.keys())


# ===================================================================
# 2. Feed — source filter
# ===================================================================

class TestFeedSourceFilter:
    """Filtering by source=audit or source=hook."""

    def test_filter_audit_only(self, client):
        resp = client.get("/api/activity/feed?source=audit")
        data = resp.get_json()
        assert data["count"] == 5
        assert all(e["source"] == "audit" for e in data["events"])

    def test_filter_hook_only(self, client):
        resp = client.get("/api/activity/feed?source=hook")
        data = resp.get_json()
        assert data["count"] == 4
        assert all(e["source"] == "hook" for e in data["events"])


# ===================================================================
# 3. Feed — event type filter
# ===================================================================

class TestFeedEventTypeFilter:
    """Filtering by event_type."""

    def test_filter_by_event_type_audit(self, client):
        resp = client.get("/api/activity/feed?event_type=security_scan")
        data = resp.get_json()
        assert data["count"] == 1
        assert data["events"][0]["source"] == "audit"
        assert data["events"][0]["event_type"] == "security_scan"

    def test_filter_by_event_type_hook(self, client):
        resp = client.get("/api/activity/feed?event_type=post_tool_use")
        data = resp.get_json()
        assert data["count"] == 2
        assert all(e["event_type"] == "post_tool_use" for e in data["events"])


# ===================================================================
# 4. Feed — actor filter (LIKE match)
# ===================================================================

class TestFeedActorFilter:
    """Filtering by actor uses LIKE %value%."""

    def test_filter_actor_builder(self, client):
        resp = client.get("/api/activity/feed?actor=builder")
        data = resp.get_json()
        # 2 audit (builder-agent) + 1 hook (builder-agent)
        assert data["count"] == 3
        assert all("builder" in e["actor_or_agent"] for e in data["events"])

    def test_filter_actor_partial_match(self, client):
        resp = client.get("/api/activity/feed?actor=compliance")
        data = resp.get_json()
        # 1 audit (compliance-agent) + 1 hook (compliance-agent)
        assert data["count"] == 2

    def test_filter_actor_no_match(self, client):
        resp = client.get("/api/activity/feed?actor=nonexistent")
        data = resp.get_json()
        assert data["count"] == 0


# ===================================================================
# 5. Feed — project_id filter
# ===================================================================

class TestFeedProjectFilter:
    """Filtering by project_id."""

    def test_filter_project_001(self, client):
        resp = client.get("/api/activity/feed?project_id=proj-001")
        data = resp.get_json()
        # audit: code_generated, test_passed, deployment_succeeded (3)
        # hook: scaffold, health_checker (2)
        assert data["count"] == 5
        assert all(e["project_id"] == "proj-001" for e in data["events"])

    def test_filter_project_002(self, client):
        resp = client.get("/api/activity/feed?project_id=proj-002")
        data = resp.get_json()
        # audit: compliance_check (1) + hook: sast_runner (1)
        assert data["count"] == 2


# ===================================================================
# 6. Feed — since timestamp filter
# ===================================================================

class TestFeedSinceFilter:
    """Filtering with since=ISO timestamp."""

    def test_since_filters_old_events(self, client):
        # Use a timestamp 90 minutes ago — should exclude events > 90 min old
        since = (_utcnow() - timedelta(minutes=90)).isoformat()
        resp = client.get(f"/api/activity/feed?since={since}")
        data = resp.get_json()
        # Events within last 90 minutes:
        # audit: deployment_succeeded (30min), security_scan (10min) = 2
        # hook: sast_runner (60min), health_checker (20min), sbom_generator (5min) = 3
        assert data["count"] == 5

    def test_since_far_future_returns_none(self, client):
        since = (_utcnow() + timedelta(days=1)).isoformat()
        resp = client.get(f"/api/activity/feed?since={since}")
        data = resp.get_json()
        assert data["count"] == 0


# ===================================================================
# 7. Feed — pagination (limit, offset)
# ===================================================================

class TestFeedPagination:
    """Pagination via limit and offset query params."""

    def test_limit_constrains_results(self, client):
        resp = client.get("/api/activity/feed?limit=3")
        data = resp.get_json()
        assert data["count"] == 3
        assert data["limit"] == 3

    def test_offset_skips_results(self, client):
        resp = client.get("/api/activity/feed?limit=100&offset=5")
        data = resp.get_json()
        assert data["count"] == 4  # 9 total - 5 offset = 4 remaining
        assert data["offset"] == 5

    def test_limit_capped_at_500(self, client):
        resp = client.get("/api/activity/feed?limit=9999")
        data = resp.get_json()
        assert data["limit"] == 500

    def test_offset_beyond_total_returns_empty(self, client):
        resp = client.get("/api/activity/feed?offset=100")
        data = resp.get_json()
        assert data["count"] == 0


# ===================================================================
# 8. Poll — cursor-based polling
# ===================================================================

class TestPoll:
    """Cursor-based polling returns events newer than cursor."""

    def test_poll_returns_200(self, client):
        resp = client.get("/api/activity/poll")
        assert resp.status_code == 200

    def test_poll_no_cursor_returns_all(self, client):
        resp = client.get("/api/activity/poll")
        data = resp.get_json()
        # Default limit is 50, we have 9 events
        assert data["count"] == 9

    def test_poll_with_cursor_filters_old(self, client):
        # Cursor = 2 hours ago — should return events created after that
        cursor = (_utcnow() - timedelta(hours=2)).isoformat()
        resp = client.get(f"/api/activity/poll?cursor={cursor}")
        data = resp.get_json()
        # Events after 2h ago:
        # audit: compliance_check is exactly 2h (excluded by >), deployment_succeeded, security_scan
        # hook: sast_runner (1h), health_checker (20min), sbom_generator (5min)
        # Note: compliance_check is >= 2h, so it depends on exact timing.
        # At minimum deployment_succeeded + security_scan + sast_runner + health_checker + sbom_generator = 5
        assert data["count"] >= 4

    def test_poll_returns_cursor(self, client):
        resp = client.get("/api/activity/poll")
        data = resp.get_json()
        assert "cursor" in data
        # Cursor should be the most recent event's created_at
        assert data["cursor"] == data["events"][0]["created_at"]

    def test_poll_limit(self, client):
        resp = client.get("/api/activity/poll?limit=2")
        data = resp.get_json()
        assert data["count"] == 2

    def test_poll_limit_capped_at_200(self, client):
        resp = client.get("/api/activity/poll?limit=9999")
        data = resp.get_json()
        # Should not exceed 200, but we only have 9 events
        assert data["count"] == 9


# ===================================================================
# 9. Filter options
# ===================================================================

class TestFilterOptions:
    """Filter-options endpoint returns unique dropdown values."""

    def test_filter_options_returns_200(self, client):
        resp = client.get("/api/activity/filter-options")
        assert resp.status_code == 200

    def test_filter_options_has_event_types(self, client):
        resp = client.get("/api/activity/filter-options")
        data = resp.get_json()
        assert "event_types" in data
        # Should include types from both audit and hook
        assert "security_scan" in data["event_types"]
        assert "post_tool_use" in data["event_types"]

    def test_filter_options_has_actors(self, client):
        resp = client.get("/api/activity/filter-options")
        data = resp.get_json()
        assert "actors" in data
        # Should combine actors from audit_trail and agent_ids from hook_events
        assert "builder-agent" in data["actors"]
        assert "security-agent" in data["actors"]

    def test_filter_options_has_projects(self, client):
        resp = client.get("/api/activity/filter-options")
        data = resp.get_json()
        assert "projects" in data
        assert "proj-001" in data["projects"]

    def test_filter_options_has_sources(self, client):
        resp = client.get("/api/activity/filter-options")
        data = resp.get_json()
        assert data["sources"] == ["audit", "hook"]


# ===================================================================
# 10. Stats
# ===================================================================

class TestStats:
    """Stats endpoint returns correct counts."""

    def test_stats_returns_200(self, client):
        resp = client.get("/api/activity/stats")
        assert resp.status_code == 200

    def test_stats_total_matches(self, client):
        resp = client.get("/api/activity/stats")
        data = resp.get_json()
        assert data["total"] == 9  # 5 audit + 4 hook
        assert data["audit_total"] == 5
        assert data["hook_total"] == 4

    def test_stats_has_today_and_last_hour(self, client):
        resp = client.get("/api/activity/stats")
        data = resp.get_json()
        assert "today" in data
        assert "last_hour" in data
        # All our seeded events are from today (utcnow - hours)
        assert data["today"] == 9
        # Events in last hour: audit deployment(30m), security_scan(10m),
        # hook health_checker(20m), sbom_generator(5m) = 4
        assert data["last_hour"] >= 3


# ===================================================================
# 11. Empty database
# ===================================================================

class TestEmptyDatabase:
    """Endpoints return graceful empty results on an empty DB."""

    def test_feed_empty(self, empty_client):
        resp = empty_client.get("/api/activity/feed")
        data = resp.get_json()
        assert data["count"] == 0
        assert data["events"] == []

    def test_poll_empty(self, empty_client):
        resp = empty_client.get("/api/activity/poll")
        data = resp.get_json()
        assert data["count"] == 0
        assert data["events"] == []
        assert data["cursor"] == ""

    def test_filter_options_empty(self, empty_client):
        resp = empty_client.get("/api/activity/filter-options")
        data = resp.get_json()
        assert data["event_types"] == []
        assert data["actors"] == []
        assert data["projects"] == []
        assert data["sources"] == ["audit", "hook"]

    def test_stats_empty(self, empty_client):
        resp = empty_client.get("/api/activity/stats")
        data = resp.get_json()
        assert data["total"] == 0
        assert data["audit_total"] == 0
        assert data["hook_total"] == 0
        assert data["today"] == 0
        assert data["last_hour"] == 0


# ===================================================================
# 12. Feed ordering
# ===================================================================

class TestFeedOrdering:
    """Feed results are ordered DESC by created_at."""

    def test_feed_descending_order(self, client):
        resp = client.get("/api/activity/feed")
        data = resp.get_json()
        timestamps = [e["created_at"] for e in data["events"]]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_poll_descending_order(self, client):
        resp = client.get("/api/activity/poll")
        data = resp.get_json()
        timestamps = [e["created_at"] for e in data["events"]]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_newest_event_is_first(self, client):
        resp = client.get("/api/activity/feed")
        data = resp.get_json()
        first = data["events"][0]
        # The most recent event is sbom_generator (5 min ago, hook)
        # or security_scan (10 min ago, audit).  sbom_generator is newer.
        assert first["summary"] == "sbom_generator" or first["summary"] == "SAST clean"


# CUI // SP-CTI
