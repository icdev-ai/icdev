# CUI // SP-CTI
"""PR-watcher liveness heartbeat (kax-obs-02).

"Is the PR watcher actually polling?" could only be answered from the log file,
and the log file is exactly what goes missing. Each COMPLETED poll now appends a
row to the EXISTING `heartbeat_checks` table, so the question is answerable from
the database.

The distinction this exists to make: 'no task reached done in N hours' with a
STALE watcher (a broken pipe) versus the same sentence with a POLLING watcher
that took zero actions (a board with nothing mergeable). Both report
``actions_taken == 0``; only the timestamp separates them.

Runs against the isolated SQLite test DB (conftest), never the live board.
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from tools.ci import pr_watcher as pw
from tools.db.storage import get_connection
from tools.kanban import metrics

NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
ROOT = pathlib.Path(__file__).resolve().parents[1]


class _NoCloseConn:
    """Delegate everything but ``close()``.

    ``_record_heartbeat`` and ``_audit`` each close the connection they were
    handed; the test owns this one and needs it to survive both polls.
    """

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def close(self):
        return None


@pytest.fixture()
def conn():
    c = get_connection()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS heartbeat_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            check_type TEXT NOT NULL,
            last_run TEXT NOT NULL,
            next_run TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            result_summary TEXT,
            items_found INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            created_at TEXT,
            classification TEXT DEFAULT 'UNCLASSIFIED'
        )
        """
    )
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
    c.execute("DELETE FROM heartbeat_checks")
    c.execute("DELETE FROM kanban_tasks")
    c.execute("DELETE FROM kanban_status_transitions")
    c.commit()
    yield c
    c.close()


def _watcher(conn, tasks):
    """A PRWatcher wired to the test DB, with every network path stubbed out."""
    shared = _NoCloseConn(conn)

    def _fetch_state(_url):
        raise RuntimeError("stubbed: no gh in tests")

    return pw.PRWatcher(
        config={
            "poll_interval_seconds": 30,
            "link_prs_on_poll": False,
            "sibling_conflict_check": False,
            "auto_merge_enabled": False,
        },
        get_connection=lambda: shared,
        fetch_state=_fetch_state,
    )


def _rows(conn):
    return [
        dict(r)
        for r in conn.execute(
            "SELECT last_run, items_found, result_summary FROM heartbeat_checks "
            "WHERE check_type = %s ORDER BY id",
            (pw.WATCHER_HEARTBEAT_CHECK_TYPE,),
        ).fetchall()
    ]


def _beat(conn, at: datetime, tasks_checked: int, actions_taken: int) -> None:
    conn.execute(
        "INSERT INTO heartbeat_checks "
        "(check_type, last_run, next_run, status, result_summary, items_found, "
        " duration_ms) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            pw.WATCHER_HEARTBEAT_CHECK_TYPE, at.isoformat(),
            (at + timedelta(seconds=30)).isoformat(), "ok",
            json.dumps({"tasks_checked": tasks_checked, "actions_taken": actions_taken}),
            tasks_checked, 5,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# The value advances across two poll cycles
# ---------------------------------------------------------------------------


class TestHeartbeatAdvances:
    def test_two_polls_write_two_advancing_rows(self, conn, monkeypatch):
        """Poll twice; the recorded time moves forward and the counts follow."""
        monkeypatch.setattr(pw, "list_pr_tasks", lambda *a, **k: [])
        w = _watcher(conn, [])

        first = w.poll_once()
        # Second cycle sees one task, whose PR fetch fails -> one 'error' action.
        monkeypatch.setattr(
            pw, "list_pr_tasks",
            lambda *a, **k: [{
                "id": "kax-obs-02",
                "pr_url": "https://github.com/icdev-ai/ICDev/pull/1",
                "status": "pr_opened",
            }],
        )
        second = w.poll_once()

        rows = _rows(conn)
        assert len(rows) == 2, "each completed poll writes exactly one row"
        assert rows[1]["last_run"] > rows[0]["last_run"], "the poll time advances"
        assert rows[0]["last_run"] == first.finished_at
        assert rows[1]["last_run"] == second.finished_at

        # Counts track what the poll actually saw, not just that it ran.
        assert rows[0]["items_found"] == 0
        assert rows[1]["items_found"] == 1
        assert json.loads(rows[0]["result_summary"])["actions_taken"] == 0
        assert json.loads(rows[1]["result_summary"])["actions_taken"] == 1

    def test_reader_returns_the_newest_poll(self, conn, monkeypatch):
        monkeypatch.setattr(pw, "list_pr_tasks", lambda *a, **k: [])
        w = _watcher(conn, [])
        w.poll_once()
        second = w.poll_once()

        hb = metrics.watcher_heartbeat(conn=conn)
        assert hb["present"] is True
        assert hb["last_poll_at"].startswith(second.finished_at[:19])
        assert hb["state"] == "polling"
        assert hb["tasks_checked"] == 0
        assert hb["actions_taken"] == 0

    def test_written_row_satisfies_the_live_postgres_constraints(self, conn, monkeypatch):
        """The primary backend is PostgreSQL; SQLite is looser and hides this.

        Live PG `heartbeat_checks` has NO `details` column and its status CHECK
        allows only pending/ok/warning/critical/error — while the SQLite DDL in
        init_icdev_db.py declares `details` and also permits 'healthy'. Either
        divergence makes the INSERT raise, get swallowed by the best-effort
        except, and the heartbeat silently never land in production.
        """
        monkeypatch.setattr(pw, "list_pr_tasks", lambda *a, **k: [])
        _watcher(conn, []).poll_once()

        row = dict(conn.execute(
            "SELECT * FROM heartbeat_checks WHERE check_type = %s",
            (pw.WATCHER_HEARTBEAT_CHECK_TYPE,),
        ).fetchone())
        assert row["status"] in ("pending", "ok", "warning", "critical", "error")
        assert "details" not in row, "live PG has no `details` column"

        src = (ROOT / "tools" / "ci" / "pr_watcher.py").read_text(encoding="utf-8")
        insert = src[src.index("INSERT INTO heartbeat_checks"):]
        insert = insert[:insert.index("VALUES")]
        assert "details" not in insert

    def test_dry_run_writes_nothing(self, conn, monkeypatch):
        """A --dry-run poll must not claim the watcher is live."""
        monkeypatch.setattr(pw, "list_pr_tasks", lambda *a, **k: [])
        w = _watcher(conn, [])
        w.dry_run = True
        w.poll_once()
        assert _rows(conn) == []


# ---------------------------------------------------------------------------
# Stale is distinguishable from zero-action
# ---------------------------------------------------------------------------


class TestStaleVsZeroAction:
    def test_stale_and_zero_action_differ_only_by_time(self, conn):
        """Both took zero actions. Only one of them is a broken pipeline."""
        _beat(conn, NOW - timedelta(hours=4), tasks_checked=0, actions_taken=0)
        stale = metrics.watcher_heartbeat(conn=conn, now=NOW)

        assert stale["state"] == "stale"
        assert stale["stale"] is True
        assert stale["actions_taken"] == 0
        assert "last polled 4.0h ago" in stale["summary"]

        _beat(conn, NOW - timedelta(seconds=30), tasks_checked=3, actions_taken=0)
        fresh = metrics.watcher_heartbeat(conn=conn, now=NOW)

        assert fresh["state"] == "polling"
        assert fresh["stale"] is False
        assert fresh["actions_taken"] == 0, "same zero-action count as the stale one"
        assert "polling" in fresh["summary"] and "3 task(s) checked" in fresh["summary"]

    def test_never_polled_is_not_silently_healthy(self, conn):
        hb = metrics.watcher_heartbeat(conn=conn, now=NOW)
        assert hb["present"] is False
        assert hb["state"] == "never_polled"
        assert hb["stale"] is True

    def test_missing_table_reports_absent_rather_than_raising(self, conn):
        conn.execute("DROP TABLE heartbeat_checks")
        conn.commit()
        hb = metrics.watcher_heartbeat(conn=conn, now=NOW)
        assert hb["present"] is False
        assert hb["state"] == "never_polled"


class TestStallAttribution:
    """The same stalled board reads as two different incidents."""

    def _stalled_board(self, conn):
        conn.execute(
            "INSERT INTO kanban_tasks (id, title, status, created_at) "
            "VALUES (%s, %s, %s, %s)",
            ["t-a", "queued work", "scheduled", NOW.isoformat()],
        )
        conn.execute(
            "INSERT INTO kanban_status_transitions "
            "(id, task_id, from_status, to_status, recorded_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            ["kst-old", "t-old", "in_progress", "done",
             (NOW - timedelta(days=4)).isoformat()],
        )
        conn.commit()

    def test_stale_watcher_is_attributed_to_the_watcher(self, conn):
        self._stalled_board(conn)
        _beat(conn, NOW - timedelta(hours=4), tasks_checked=0, actions_taken=0)

        r = metrics.throughput_stall_check(
            conn=conn, window_hours=24, min_active_tasks=1, now=NOW,
        )
        assert r["stalled"] is True
        assert r["stall_attribution"] == "watcher_not_polling"
        assert r["watcher"]["state"] == "stale"

    def test_live_watcher_is_attributed_to_the_board(self, conn):
        self._stalled_board(conn)
        _beat(conn, NOW - timedelta(seconds=45), tasks_checked=2, actions_taken=0)

        r = metrics.throughput_stall_check(
            conn=conn, window_hours=24, min_active_tasks=1, now=NOW,
        )
        assert r["stalled"] is True
        assert r["stall_attribution"] == "watcher_polling_nothing_mergeable"
        assert r["watcher"]["state"] == "polling"

    def test_reason_field_is_unchanged(self, conn):
        """Attribution is additive — the original three reasons still apply."""
        self._stalled_board(conn)
        _beat(conn, NOW - timedelta(seconds=45), tasks_checked=0, actions_taken=0)
        r = metrics.throughput_stall_check(
            conn=conn, window_hours=24, min_active_tasks=1, now=NOW,
        )
        assert r["reason"] == "stalled"


# ---------------------------------------------------------------------------
# No new daemon, no new log file
# ---------------------------------------------------------------------------


class TestNoNewMovingParts:
    def test_reuses_the_existing_heartbeat_checks_table(self):
        """`heartbeat_checks` predates this feature — no new table, no migration."""
        ddl = (ROOT / "tools" / "db" / "init_icdev_db.py").read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS heartbeat_checks" in ddl

    def test_launcher_supervises_no_extra_process(self):
        """The launcher's service set is unchanged — this added no daemon."""
        src = (ROOT / "tools" / "genesis" / "launcher.py").read_text(encoding="utf-8")
        started = sorted(
            line.split("(")[0][len("def "):]
            for line in src.splitlines()
            if line.startswith("def _start_")
        )
        assert started == [
            "_start_daemon",
            "_start_dashboard",
            "_start_kanban_scheduler",
            "_start_pr_watcher",
            "_start_proposal_genesis",
            "_start_trading_dashboard",
        ]

    def test_liveness_is_readable_without_any_log_file(self, conn, monkeypatch):
        """The probe answers from the DB alone — no file path is consulted."""
        def _boom(*a, **k):
            raise AssertionError("watcher liveness must not read the filesystem")

        monkeypatch.setattr(pathlib.Path, "open", _boom)
        monkeypatch.setattr(pathlib.Path, "read_text", _boom)

        _beat(conn, NOW - timedelta(seconds=10), tasks_checked=1, actions_taken=0)
        hb = metrics.watcher_heartbeat(conn=conn, now=NOW)
        assert hb["state"] == "polling"
