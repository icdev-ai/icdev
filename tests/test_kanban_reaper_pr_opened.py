# CUI // SP-CTI
"""Regression — the stale-reaper must not record a FAILURE against a task that
has already opened its PR.

Observed 2026-08-16, whole sequence in kanban_status_transitions:

    scheduled   -> in_progress  actor=scheduler    'dispatched: agent subprocess launched'
    in_progress -> backlog      actor=stale-reaper 'task was in_progress for 25 min
                                                    with silent-dispatch (no log
                                                    output, no heartbeat)'

Timeline: last_heartbeat_at 16:24:36Z, PR #1744 created 16:27:16Z, reaper fired
16:35:00Z. The worker had finished and opened its PR, then went quiet — which is
what a finished worker looks like. The row still carried
executor_url=https://github.com/icdev-ai/icdev/pull/1744, so the evidence that
the dispatch SUCCEEDED was in the very row the reaper was updating.

Consequences: failure_count incremented against a task that succeeded, a
last_failure_reason that misrepresents it, and status=backlog while an open PR
existed. Enough of those also feed the fc>=5 sweep, which parks a healthy task
in 'suggested'.

This is NOT the duplicate-PR bug — _drop_respawn_guarded already stops the
requeued task from being re-dispatched. The defect is the lying board.

Not a threshold problem either: the silent-dispatch window went 60s -> 600s
precisely because it was 'the single largest source of task failures'. The shape
is that liveness is judged on the heartbeat alone while the strongest evidence
of success sits unread in the same row.

Harness borrowed from tests/test_kanban_stale_reaper_manual_actor.py.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

from tools.db.storage import StorageConnection

_SCHEMA = """
CREATE TABLE kanban_tasks (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    status        TEXT DEFAULT 'backlog',
    priority      TEXT DEFAULT 'medium',
    task_type     TEXT DEFAULT 'build',
    created_at    TEXT,
    updated_at    TEXT,
    completed_at  TEXT,
    failure_count INTEGER DEFAULT 0,
    last_failure_reason TEXT,
    last_failure_at TEXT,
    executor_url  TEXT,
    execution_id  TEXT,
    max_retries   INTEGER DEFAULT 5
);
CREATE TABLE kanban_status_transitions (
    id           TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL,
    from_status  TEXT,
    to_status    TEXT NOT NULL,
    actor        TEXT NOT NULL DEFAULT 'unknown',
    reason       TEXT,
    recorded_at  TEXT NOT NULL
);
"""


@pytest.fixture()
def reaper_ctx(tmp_path, monkeypatch):
    db_path = tmp_path / "k.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()

    def _fake_conn(*_a, **_kw):
        # StorageConnection, not a raw sqlite3 handle: the reaper's SQL is
        # authored in Postgres %s style and only translate_sql makes it run on
        # sqlite. A raw handle raises 'near "%": syntax error' into the reaper's
        # own except-block and the sweep reports "no stale tasks" for a sweep
        # that never ran.
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return StorageConnection(c, "sqlite")

    import tools.genesis.reflexes.kanban as km

    monkeypatch.setattr(km, "get_connection", _fake_conn)
    monkeypatch.setattr(km, "_running", {})
    # The reaper is a no-op while another scheduler owns the runner; without
    # this the assertions below report "not reaped" for a reaper that never ran.
    monkeypatch.setattr(km, "_foreign_scheduler_pid", lambda: 0)
    monkeypatch.setattr(km, "_task_log_is_empty", lambda tid: True)
    monkeypatch.setattr(km, "_get_task_timeout", lambda tid: 900)
    monkeypatch.setattr(km, "_detect_execution_anomaly", lambda age: (False, ""))
    # Never shell out to `gh` from a test. Each case sets its own listing.
    monkeypatch.setattr(km, "_open_pr_head_branches", lambda root: set())
    monkeypatch.setattr(km, "_open_pr_listing_unavailable", lambda root: False)

    return {"km": km, "db": db_path, "monkeypatch": monkeypatch}


def _insert_task(db_path, task_id, status, updated_at, executor_url=None,
                 failure_count=0):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO kanban_tasks "
        "(id, title, status, updated_at, executor_url, failure_count) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, f"Title for {task_id}", status, updated_at, executor_url,
         failure_count),
    )
    conn.execute(
        "INSERT INTO kanban_status_transitions "
        "(id, task_id, from_status, to_status, actor, recorded_at) "
        "VALUES (?, ?, 'scheduled', 'in_progress', 'scheduler', ?)",
        (f"kst-{task_id}", task_id, updated_at),
    )
    conn.commit()
    conn.close()


def _task_row(db_path, task_id):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT status, failure_count, last_failure_reason, last_failure_at "
        "FROM kanban_tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    conn.close()
    return dict(row)


def _stale(km, extra_seconds=30):
    return (
        datetime.now(timezone.utc)
        - timedelta(seconds=km._SILENT_DISPATCH_THRESHOLD + extra_seconds)
    ).isoformat()


class TestReaperSkipsTasksWithAnOpenPR:
    """The reverse-direction case: an in_progress task with an open PR and no
    heartbeat past the threshold must NOT be reaped."""

    def test_open_pr_branch_moves_to_pr_opened_without_a_failure(self, reaper_ctx):
        km = reaper_ctx["km"]
        reaper_ctx["monkeypatch"].setattr(
            km, "_open_pr_head_branches", lambda root: {"kanban/kpr-watch-01"},
        )

        _insert_task(
            reaper_ctx["db"], "kpr-watch-01", "in_progress", _stale(km),
            executor_url="https://github.com/icdev-ai/icdev/pull/1744",
        )

        km._reap_stale_in_progress()

        row = _task_row(reaper_ctx["db"], "kpr-watch-01")
        assert row["status"] == "pr_opened", (
            "a task whose PR is already open has FINISHED — it must not be sent "
            "back to backlog while that PR is open"
        )
        assert row["failure_count"] == 0, (
            "no failure occurred: incrementing failure_count here is what feeds "
            "the fc>=5 quarantine sweep with fictional failures"
        )
        assert row["last_failure_reason"] is None
        assert row["last_failure_at"] is None

    def test_the_skip_is_recorded_as_a_transition(self, reaper_ctx):
        """The move must be attributable, like every other reaper write."""
        km = reaper_ctx["km"]
        reaper_ctx["monkeypatch"].setattr(
            km, "_open_pr_head_branches", lambda root: {"kanban/kpr-watch-01"},
        )
        _insert_task(reaper_ctx["db"], "kpr-watch-01", "in_progress", _stale(km))

        km._reap_stale_in_progress()

        conn = sqlite3.connect(str(reaper_ctx["db"]))
        conn.row_factory = sqlite3.Row
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT to_status, actor, reason FROM kanban_status_transitions "
                "WHERE task_id = ? AND to_status = 'pr_opened'",
                ("kpr-watch-01",),
            ).fetchall()
        ]
        conn.close()
        assert len(rows) == 1
        assert rows[0]["actor"] == "stale-reaper"
        assert "already open" in (rows[0]["reason"] or "")

    def test_task_without_an_open_pr_is_still_reaped(self, reaper_ctx):
        """Guard against over-correcting: a genuinely silent dispatch, with no
        PR anywhere, must still be reaped and still counted as a failure."""
        km = reaper_ctx["km"]
        _insert_task(reaper_ctx["db"], "kpr-dead-01", "in_progress", _stale(km))

        km._reap_stale_in_progress()

        row = _task_row(reaper_ctx["db"], "kpr-dead-01")
        assert row["status"] == "backlog"
        assert row["failure_count"] == 1
        assert "silent-dispatch" in (row["last_failure_reason"] or "")

    def test_executor_url_alone_does_not_hold_a_task_when_gh_is_reachable(
        self, reaper_ctx,
    ):
        """executor_url is never cleared on re-dispatch, so a task whose EARLIER
        PR merged still carries the URL. While the open-PR listing is reachable
        it is authoritative, and the stale URL must not park a dead task in
        pr_opened — that would be the lying board in the other direction."""
        km = reaper_ctx["km"]
        _insert_task(
            reaper_ctx["db"], "kpr-stale-url-01", "in_progress", _stale(km),
            executor_url="https://github.com/icdev-ai/icdev/pull/1400",
        )

        km._reap_stale_in_progress()

        row = _task_row(reaper_ctx["db"], "kpr-stale-url-01")
        assert row["status"] == "backlog"
        assert row["failure_count"] == 1

    def test_executor_url_is_used_when_the_pr_listing_cannot_run(self, reaper_ctx):
        """Air-gapped / no-`gh` runners: the listing answers an empty set for
        'no open PRs' and for 'could not ask' alike, so there executor_url is
        the only record of the PR that exists."""
        km = reaper_ctx["km"]
        reaper_ctx["monkeypatch"].setattr(
            km, "_open_pr_listing_unavailable", lambda root: True,
        )
        _insert_task(
            reaper_ctx["db"], "kpr-airgap-01", "in_progress", _stale(km),
            executor_url="https://github.com/icdev-ai/icdev/pull/1744",
        )

        km._reap_stale_in_progress()

        row = _task_row(reaper_ctx["db"], "kpr-airgap-01")
        assert row["status"] == "pr_opened"
        assert row["failure_count"] == 0


class TestOpenPrListingAvailability:
    """_open_pr_listing_unavailable must distinguish 'no open PRs' from 'could
    not ask' — the two lead to opposite decisions in the reaper, and the cached
    listing returns an empty set for both."""

    def test_unavailable_is_false_before_any_call(self):
        import tools.genesis.reflexes.kanban as km

        km._open_pr_listing_failed_at.pop("/nowhere", None)
        assert km._open_pr_listing_unavailable("/nowhere") is False

    def test_failed_listing_marks_unavailable(self, monkeypatch):
        import subprocess as _sp

        import tools.genesis.reflexes.kanban as km

        km._open_pr_branch_cache.pop("/repo-x", None)
        km._open_pr_listing_failed_at.pop("/repo-x", None)

        def _boom(*_a, **_kw):
            raise OSError("gh: not found")

        monkeypatch.setattr(_sp, "run", _boom)
        assert km._open_pr_head_branches("/repo-x") == set()
        assert km._open_pr_listing_unavailable("/repo-x") is True

    def test_successful_empty_listing_is_available(self, monkeypatch):
        import subprocess as _sp

        import tools.genesis.reflexes.kanban as km

        km._open_pr_branch_cache.pop("/repo-y", None)
        km._open_pr_listing_failed_at["/repo-y"] = 0.0  # a stale prior failure

        class _R:
            returncode = 0
            stdout = "[]"

        monkeypatch.setattr(_sp, "run", lambda *_a, **_kw: _R())
        assert km._open_pr_head_branches("/repo-y") == set()
        assert km._open_pr_listing_unavailable("/repo-y") is False


# ---------------------------------------------------------------------------
# The sweep must not depend on an undeclared package
# ---------------------------------------------------------------------------
class _BlockDateutil:
    """Import blocker mimicking a runner without `python-dateutil`."""

    def find_spec(self, name, path=None, target=None):
        if name == "dateutil" or name.startswith("dateutil."):
            raise ImportError(f"No module named {name!r}")
        return None


@pytest.fixture
def no_dateutil():
    blocker = _BlockDateutil()
    saved = {k: v for k, v in sys.modules.items()
             if k == "dateutil" or k.startswith("dateutil.")}
    for k in saved:
        del sys.modules[k]
    sys.meta_path.insert(0, blocker)
    try:
        yield
    finally:
        sys.meta_path.remove(blocker)
        sys.modules.update(saved)


class TestTheSweepRunsWithoutDateutil:
    """`python-dateutil` is in neither requirements.txt nor pyproject, and is not
    installed on the CI runner.

    The timestamp parse used to be `from dateutil.parser import parse` INSIDE the
    reaper's per-task ``except Exception: continue``. So on every row the import
    raised ImportError, the except swallowed it, and the loop moved on — the
    sweep skipped EVERY task and reported nothing. The reaper has never once run
    on CI, and would not run on an air-gapped install either.

    Measured: with the import blocked, 5 of the 8 tests above fail with the task
    left `in_progress` — exactly the result CI reported on #1754.
    """

    def test_a_stale_task_is_still_reaped(self, reaper_ctx, no_dateutil):
        km = reaper_ctx["km"]
        _insert_task(reaper_ctx["db"], "kpr-nodu-01", "in_progress", _stale(km))
        km._reap_stale_in_progress()
        assert _task_row(reaper_ctx["db"], "kpr-nodu-01")["status"] == "backlog", (
            "a missing optional package must not silently disable the sweep"
        )

    def test_an_open_pr_task_is_still_held(self, reaper_ctx, no_dateutil):
        """The other direction: without dateutil the sweep used to skip every
        task, which LOOKS like this test passing. It has to hold for the right
        reason, so the case above pins that the sweep ran at all."""
        km = reaper_ctx["km"]
        reaper_ctx["monkeypatch"].setattr(
            km, "_open_pr_head_branches", lambda root: {"kanban/kpr-nodu-02"},
        )
        _insert_task(reaper_ctx["db"], "kpr-nodu-02", "in_progress", _stale(km))
        km._reap_stale_in_progress()
        assert _task_row(reaper_ctx["db"], "kpr-nodu-02")["status"] == "pr_opened"


class TestTimestampParsing:
    """`_parse_utc_timestamp` is the seam; these are its edges."""

    def test_an_isoformat_string_is_read_as_utc(self):
        import tools.genesis.reflexes.kanban as km

        got = km._parse_utc_timestamp("2026-08-17T23:41:42.587933+00:00")
        assert got == datetime(2026, 8, 17, 23, 41, 42, 587933, tzinfo=timezone.utc)

    def test_a_trailing_Z_is_accepted(self):
        """fromisoformat rejected `Z` before 3.11, and rows outlive interpreters."""
        import tools.genesis.reflexes.kanban as km

        assert km._parse_utc_timestamp("2026-08-17T23:41:42Z") == datetime(
            2026, 8, 17, 23, 41, 42, tzinfo=timezone.utc)

    def test_a_naive_stamp_is_read_as_utc(self):
        import tools.genesis.reflexes.kanban as km

        got = km._parse_utc_timestamp("2026-08-17T23:41:42")
        assert got.tzinfo is timezone.utc

    def test_a_driver_native_datetime_passes_through(self):
        import tools.genesis.reflexes.kanban as km

        aware = datetime(2026, 8, 17, tzinfo=timezone.utc)
        assert km._parse_utc_timestamp(aware) is aware
        naive = datetime(2026, 8, 17)
        assert km._parse_utc_timestamp(naive).tzinfo is timezone.utc

    def test_junk_is_None_not_an_exception(self):
        """The caller distinguishes 'this row is bad' from 'the sweep is broken',
        and can only do that if a bad row is a value rather than a raise."""
        import tools.genesis.reflexes.kanban as km

        assert km._parse_utc_timestamp("not a timestamp") is None
        assert km._parse_utc_timestamp("") is None
        assert km._parse_utc_timestamp(None) is None

    def test_it_does_not_need_dateutil(self, no_dateutil):
        import tools.genesis.reflexes.kanban as km

        assert km._parse_utc_timestamp("2026-08-17T23:41:42+00:00") is not None
