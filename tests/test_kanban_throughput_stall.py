# CUI // SP-CTI
"""Tests for the board-throughput stall rule (kax-stall-01).

Nothing watched whether the board was actually completing work: 'done'
transitions ran 63 (Aug 1) -> 121 (Aug 2) -> ZERO for Aug 4, 5 and 6, and the
four-day flatline was found only because a human happened to ask.

Covers two layers:
  * tools/kanban/metrics.py::throughput_stall_check  — the raw signal
  * tools/genesis/reflexes/self_monitor.py::_check_board_throughput — alerting,
    dedupe/cooldown, and auto-resolve.

Runs against the isolated SQLite test DB (conftest), never the live board.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tools.db.storage import get_connection
from tools.genesis.reflexes import self_monitor
from tools.kanban import metrics

NOW = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)


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
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT,
            severity TEXT NOT NULL, source TEXT NOT NULL, title TEXT NOT NULL,
            description TEXT, status TEXT DEFAULT 'firing', acknowledged_by TEXT,
            resolved_at TIMESTAMP, auto_healed BOOLEAN DEFAULT 0,
            healing_event_id INTEGER, watchcon_tier INTEGER DEFAULT 4,
            created_at TIMESTAMP
        )
        """
    )
    c.execute("DELETE FROM kanban_tasks")
    c.execute("DELETE FROM kanban_status_transitions")
    c.execute("DELETE FROM alerts")
    c.commit()
    yield c
    c.close()


def _task(c, tid, status):
    c.execute(
        "INSERT INTO kanban_tasks (id, title, status, created_at) VALUES (%s, %s, %s, %s)",
        [tid, f"task {tid}", status, NOW.isoformat()],
    )


def _trans(c, tid, to_status, at: datetime, from_status="in_progress"):
    c.execute(
        "INSERT INTO kanban_status_transitions "
        "(id, task_id, from_status, to_status, recorded_at) VALUES (%s, %s, %s, %s, %s)",
        [f"kst-{tid}-{to_status}-{at.isoformat()}", tid, from_status, to_status, at.isoformat()],
    )


# ---------------------------------------------------------------------------
# Layer 1: the raw signal
# ---------------------------------------------------------------------------


class TestThroughputStallCheck:
    def test_fires_on_four_day_flatline_with_scheduled_work(self, conn):
        """The exact shape of the incident: work queued, nothing finishing."""
        _trans(conn, "old-1", "done", NOW - timedelta(days=4, hours=1))
        _trans(conn, "old-2", "done", NOW - timedelta(days=5))
        _task(conn, "t-a", "scheduled")
        _task(conn, "t-b", "in_progress")
        conn.commit()

        r = metrics.throughput_stall_check(conn=conn, window_hours=24, min_active_tasks=1, now=NOW)

        assert r["stalled"] is True
        assert r["reason"] == "stalled"
        assert r["completed_in_window"] == 0
        assert r["active_tasks"] == 2
        assert r["active_by_status"] == {"scheduled": 1, "in_progress": 1}
        # Reaches past the SQL pre-filter to report the real last completion.
        assert r["last_done_at"].startswith("2026-08-03")
        assert r["hours_since_last_done"] == pytest.approx(97.0, abs=0.1)

    def test_silent_on_idle_but_empty_board(self, conn):
        """Zero throughput with zero active work is idle, not stalled."""
        _trans(conn, "old-1", "done", NOW - timedelta(days=4))
        _task(conn, "t-a", "backlog")
        _task(conn, "t-b", "suggested")
        _task(conn, "t-c", "done")
        conn.commit()

        r = metrics.throughput_stall_check(conn=conn, window_hours=24, min_active_tasks=1, now=NOW)

        assert r["stalled"] is False
        assert r["reason"] == "board_idle"
        assert r["active_tasks"] == 0

    def test_silent_on_completely_empty_board(self, conn):
        r = metrics.throughput_stall_check(conn=conn, window_hours=24, min_active_tasks=1, now=NOW)
        assert r["stalled"] is False
        assert r["reason"] == "board_idle"
        assert r["last_done_at"] is None
        assert r["hours_since_last_done"] is None

    def test_silent_when_throughput_present(self, conn):
        _trans(conn, "t-a", "done", NOW - timedelta(hours=2))
        _task(conn, "t-b", "scheduled")
        conn.commit()

        r = metrics.throughput_stall_check(conn=conn, window_hours=24, min_active_tasks=1, now=NOW)

        assert r["stalled"] is False
        assert r["reason"] == "throughput_present"
        assert r["completed_in_window"] == 1

    # --- mutation checks: each flips exactly one input across a boundary ---

    @pytest.mark.parametrize(
        "hours_ago,expect_stalled",
        [
            (23.5, False),   # inside the 24h window -> throughput present
            (24.5, True),    # outside it -> stalled
        ],
    )
    def test_window_boundary(self, conn, hours_ago, expect_stalled):
        _trans(conn, "t-a", "done", NOW - timedelta(hours=hours_ago))
        _task(conn, "t-b", "scheduled")
        conn.commit()

        r = metrics.throughput_stall_check(conn=conn, window_hours=24, min_active_tasks=1, now=NOW)
        assert r["stalled"] is expect_stalled

    @pytest.mark.parametrize(
        "min_active,expect_stalled", [(1, True), (2, False)]
    )
    def test_min_active_tasks_boundary(self, conn, min_active, expect_stalled):
        _trans(conn, "t-a", "done", NOW - timedelta(days=4))
        _task(conn, "t-b", "in_progress")
        conn.commit()

        r = metrics.throughput_stall_check(
            conn=conn, window_hours=24, min_active_tasks=min_active, now=NOW
        )
        assert r["stalled"] is expect_stalled

    def test_non_done_transitions_do_not_count_as_throughput(self, conn):
        """A busy-looking timeline with no completions is still a stall."""
        for i in range(20):
            _trans(conn, f"t-{i}", "in_progress", NOW - timedelta(minutes=i), from_status="scheduled")
        _task(conn, "t-a", "in_progress")
        conn.commit()

        r = metrics.throughput_stall_check(conn=conn, window_hours=24, min_active_tasks=1, now=NOW)
        assert r["stalled"] is True
        assert r["completed_in_window"] == 0

    def test_window_is_honoured_from_config_not_hardcoded(self, conn):
        """Same data, two windows, two verdicts."""
        _trans(conn, "t-a", "done", NOW - timedelta(hours=40))
        _task(conn, "t-b", "scheduled")
        conn.commit()

        assert metrics.throughput_stall_check(
            conn=conn, window_hours=24, min_active_tasks=1, now=NOW
        )["stalled"] is True
        assert metrics.throughput_stall_check(
            conn=conn, window_hours=72, min_active_tasks=1, now=NOW
        )["stalled"] is False


# ---------------------------------------------------------------------------
# Layer 2: the reflex — alerting, dedupe/cooldown, auto-resolve
# ---------------------------------------------------------------------------


def _stall_alerts(c):
    rows = c.execute(
        "SELECT id, status, title, severity, created_at FROM alerts WHERE source = %s "
        "ORDER BY id",
        [self_monitor.STALL_SOURCE],
    ).fetchall()
    return [dict(r) for r in rows]


def _seed_live_stall(c):
    """A stall as of *real* now — the reflex reads the wall clock."""
    now = datetime.now(timezone.utc)
    c.execute(
        "INSERT INTO kanban_status_transitions "
        "(id, task_id, from_status, to_status, recorded_at) VALUES (%s, %s, %s, %s, %s)",
        ["kst-live-done", "t-old", "in_progress", "done",
         (now - timedelta(days=4)).isoformat()],
    )
    c.execute(
        "INSERT INTO kanban_tasks (id, title, status, created_at) VALUES (%s, %s, %s, %s)",
        ["t-live", "queued work", "scheduled", now.isoformat()],
    )
    c.commit()


CFG = {"board_throughput": {"enabled": True, "window_hours": 24,
                            "min_active_tasks": 1, "cooldown_hours": 12,
                            "severity": "critical"}}


class TestReflexAlerting:
    def test_opens_one_alert_on_stall(self, conn):
        _seed_live_stall(conn)

        result = self_monitor._check_board_throughput(conn, CFG)

        assert result["action"] == "opened"
        assert result["signal"]["stalled"] is True
        rows = _stall_alerts(conn)
        assert len(rows) == 1
        assert rows[0]["status"] == "firing"
        assert rows[0]["severity"] == "critical"
        assert "stalled" in rows[0]["title"]

    def test_does_not_duplicate_on_consecutive_cycles(self, conn):
        _seed_live_stall(conn)

        actions = [self_monitor._check_board_throughput(conn, CFG)["action"] for _ in range(4)]

        assert actions[0] == "opened"
        assert actions[1:] == ["unchanged", "unchanged", "unchanged"]
        assert len(_stall_alerts(conn)) == 1

    def test_recovery_resolves_the_alert(self, conn):
        _seed_live_stall(conn)
        assert self_monitor._check_board_throughput(conn, CFG)["action"] == "opened"

        # A task completes.
        conn.execute(
            "INSERT INTO kanban_status_transitions "
            "(id, task_id, from_status, to_status, recorded_at) VALUES (%s, %s, %s, %s, %s)",
            ["kst-recover", "t-live", "in_progress", "done",
             datetime.now(timezone.utc).isoformat()],
        )
        conn.commit()

        result = self_monitor._check_board_throughput(conn, CFG)

        assert result["action"] == "resolved"
        rows = _stall_alerts(conn)
        assert len(rows) == 1
        assert rows[0]["status"] == "resolved"

    def test_cooldown_suppresses_reopen_after_manual_resolve(self, conn):
        """A human clearing the alert must not make it re-fire next cycle."""
        _seed_live_stall(conn)
        self_monitor._check_board_throughput(conn, CFG)
        conn.execute("UPDATE alerts SET status = 'resolved' WHERE source = %s",
                     [self_monitor.STALL_SOURCE])
        conn.commit()

        result = self_monitor._check_board_throughput(conn, CFG)

        assert result["action"] == "cooldown"
        assert len(_stall_alerts(conn)) == 1

    def test_reopens_once_cooldown_has_elapsed(self, conn):
        _seed_live_stall(conn)
        self_monitor._check_board_throughput(conn, CFG)
        stale = (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat()
        conn.execute(
            "UPDATE alerts SET status = 'resolved', created_at = %s WHERE source = %s",
            [stale, self_monitor.STALL_SOURCE],
        )
        conn.commit()

        result = self_monitor._check_board_throughput(conn, CFG)

        assert result["action"] == "opened"
        assert len(_stall_alerts(conn)) == 2

    def test_stays_silent_on_idle_board(self, conn):
        # 'done' four days ago, but nothing queued.
        conn.execute(
            "INSERT INTO kanban_status_transitions "
            "(id, task_id, from_status, to_status, recorded_at) VALUES (%s, %s, %s, %s, %s)",
            ["kst-idle", "t-old", "in_progress", "done",
             (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()],
        )
        conn.commit()

        result = self_monitor._check_board_throughput(conn, CFG)

        assert result["action"] == "healthy"
        assert result["signal"]["reason"] == "board_idle"
        assert _stall_alerts(conn) == []

    def test_disabled_flag_silences_the_rule(self, conn):
        _seed_live_stall(conn)

        cfg = {"board_throughput": {**CFG["board_throughput"], "enabled": False}}
        result = self_monitor._check_board_throughput(conn, cfg)

        assert result["action"] == "disabled"
        assert _stall_alerts(conn) == []

    def test_env_overrides_yaml_window(self, conn, monkeypatch):
        """Operator can retune the window without a code or YAML change."""
        _seed_live_stall(conn)  # last done 4 days ago
        monkeypatch.setenv("ICDEV_BOARD_STALL_WINDOW_HOURS", "240")

        result = self_monitor._check_board_throughput(conn, CFG)

        assert result["signal"]["window_hours"] == 240.0
        assert result["action"] == "healthy"

    def test_env_can_disable_the_rule(self, conn, monkeypatch):
        _seed_live_stall(conn)
        monkeypatch.setenv("ICDEV_BOARD_STALL_ENABLED", "false")

        assert self_monitor._check_board_throughput(conn, CFG)["action"] == "disabled"

    def test_probe_rule_does_not_resolve_the_stall_alert(self, conn):
        """Regression: the stall alert lives under its own source prefix.

        _sync_alerts auto-resolves every firing 'self_monitor:*' alert whose
        category is absent from the probe results. Sharing a prefix would have
        resolved a live stall on the very next cycle.
        """
        _seed_live_stall(conn)
        self_monitor._check_board_throughput(conn, CFG)

        self_monitor._sync_alerts(conn, {}, 1)

        rows = _stall_alerts(conn)
        assert len(rows) == 1
        assert rows[0]["status"] == "firing"
