# CUI // SP-CTI
"""Tests for kanban SLA + cycle-time metrics (crx-kan-01).

Runs against the isolated SQLite test DB (conftest), never the live board.
Covers tools/kanban/metrics.py::sla_snapshot and ::cycle_time_metrics.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tools.db.storage import get_connection
from tools.kanban import metrics


def _iso(dt: datetime) -> str:
    return dt.isoformat()


NOW = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def conn():
    c = get_connection()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS kanban_tasks (
            id TEXT PRIMARY KEY, title TEXT, description TEXT, task_type TEXT,
            priority TEXT DEFAULT 'high', status TEXT DEFAULT 'backlog',
            created_at TEXT, updated_at TEXT, completed_at TEXT,
            due_date TEXT, sla_hours INTEGER
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS kanban_status_transitions (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL, from_status TEXT,
            to_status TEXT NOT NULL, actor TEXT, reason TEXT, recorded_at TEXT NOT NULL
        )
        """
    )
    c.execute("DELETE FROM kanban_tasks")
    c.execute("DELETE FROM kanban_status_transitions")
    c.commit()
    yield c
    c.close()


def _task(c, tid, status="in_progress", created=None, due_date=None, sla_hours=None, title="t"):
    c.execute(
        "INSERT INTO kanban_tasks (id, title, status, created_at, due_date, sla_hours) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        [tid, title, status, created, due_date, sla_hours],
    )


def _trans(c, tid, to_status, recorded_at, from_status=None):
    c.execute(
        "INSERT INTO kanban_status_transitions (id, task_id, from_status, to_status, recorded_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        [f"kst-{tid}-{to_status}-{recorded_at}", tid, from_status, to_status, recorded_at],
    )


class TestSlaSnapshot:
    def test_overdue_and_at_risk_and_ok(self, conn):
        # Overdue: due_date in the past.
        _task(conn, "od-1", due_date=_iso(NOW - timedelta(hours=5)))
        # At-risk: created 90h ago, due in 2h → <20% of 92h window remains.
        _task(conn, "ar-1", created=_iso(NOW - timedelta(hours=90)),
              due_date=_iso(NOW + timedelta(hours=2)))
        # OK: due far in the future relative to its window.
        _task(conn, "ok-1", created=_iso(NOW - timedelta(hours=1)),
              due_date=_iso(NOW + timedelta(hours=100)))
        # No deadline → ignored entirely.
        _task(conn, "none-1")
        # Terminal task with a past due_date → ignored.
        _task(conn, "done-1", status="done", due_date=_iso(NOW - timedelta(hours=5)))
        conn.commit()

        snap = metrics.sla_snapshot(conn=conn, now=NOW)
        assert snap["tracked"] == 3  # od-1, ar-1, ok-1
        assert snap["overdue_count"] == 1
        assert snap["at_risk_count"] == 1
        assert snap["overdue"][0]["id"] == "od-1"
        assert snap["at_risk"][0]["id"] == "ar-1"

    def test_sla_hours_from_created(self, conn):
        # No due_date; sla_hours=10 from created 12h ago → overdue.
        _task(conn, "sla-od", created=_iso(NOW - timedelta(hours=12)), sla_hours=10)
        conn.commit()
        snap = metrics.sla_snapshot(conn=conn, now=NOW)
        assert snap["overdue_count"] == 1
        assert snap["overdue"][0]["id"] == "sla-od"


class TestCycleTimeMetrics:
    def test_p50_p90_and_throughput(self, conn):
        # Three completed tasks with known cycle times (hours): 10, 20, 60.
        specs = [("c1", 10), ("c2", 20), ("c3", 60)]
        done_at = NOW - timedelta(days=1)
        for tid, hours in specs:
            _task(conn, tid, status="done", created=_iso(done_at - timedelta(hours=hours)))
            _trans(conn, tid, "in_progress", _iso(done_at - timedelta(hours=hours)), from_status="backlog")
            _trans(conn, tid, "done", _iso(done_at), from_status="in_progress")
        conn.commit()

        m = metrics.cycle_time_metrics(conn=conn, weeks=4)
        assert m["completed"] == 3
        # p50 of [10,20,60] = 20; p90 ≈ 52 (interpolated).
        assert m["p50_cycle_hours"] == 20.0
        assert m["p90_cycle_hours"] == pytest.approx(52.0, abs=0.5)
        # All completed in the same ISO week.
        assert sum(m["throughput_by_week"].values()) == 3

    def test_excludes_outside_window(self, conn):
        old_done = NOW - timedelta(weeks=10)
        _task(conn, "old", status="done", created=_iso(old_done - timedelta(hours=5)))
        _trans(conn, "old", "in_progress", _iso(old_done - timedelta(hours=5)), from_status="backlog")
        _trans(conn, "old", "done", _iso(old_done), from_status="in_progress")
        conn.commit()
        m = metrics.cycle_time_metrics(conn=conn, weeks=4)
        assert m["completed"] == 0

    def test_board_metrics_combines(self, conn):
        # board_metrics uses real wall-clock now, so use a clearly-past deadline.
        _task(conn, "od", due_date="2020-01-01T00:00:00+00:00")
        conn.commit()
        bm = metrics.board_metrics(conn=conn, weeks=4)
        assert "sla" in bm and "cycle_time" in bm
        assert bm["sla"]["overdue_count"] == 1
