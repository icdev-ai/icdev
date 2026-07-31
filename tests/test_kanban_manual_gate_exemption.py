# CUI // SP-CTI
"""Regression — manual-mode gate tasks (e.g. prem-gate-00) must be invisible
to the autonomous kanban pipeline in all three lifecycle sweeps.

A gate task exists solely to block its dependents from auto-dispatch while
implementation happens manually (often in a private repo the runner cannot
reach). It is held in_progress indefinitely by design. Before this fix:

  1. _reap_stale_in_progress bounced the gate back to backlog (silent-dispatch
     fast path after 1 min, or normal 2x-timeout path after 30-80 min),
     incrementing failure_count toward the fc>=5 'suggested' quarantine.
  2. _get_due_tasks then re-dispatched the gate from backlog, burning a full
     agent session on a task whose title says "do not dispatch"
     (observed: prem-gate-00 dispatched twice on 2026-07-12).
  3. _startup_recover_stale_in_progress reset the gate on scheduler restart.

Gate detection (_is_manual_gate): id ends with '-gate-00' or title contains
the 'MANUAL-MODE GATE' marker.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import tools.genesis.reflexes.kanban as km
from tools.db.storage import translate_sql

GATE_ID = "prem-gate-00"
GATE_TITLE = "MANUAL-MODE GATE - do not complete; do not dispatch"


class _TranslatingConn:
    """Translate %s placeholders to ? so kanban.py's PG-style SQL runs on SQLite."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        return self._conn.execute(translate_sql(sql, backend="sqlite"), params)

    def executemany(self, sql, seq):
        return self._conn.executemany(translate_sql(sql, backend="sqlite"), seq)

    def commit(self):
        return self._conn.commit()

    def close(self):
        return self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _patch_km(db_path, monkeypatch):
    def _fake_conn(*_a, **_kw):
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return _TranslatingConn(c)

    monkeypatch.setattr(km, "get_connection", _fake_conn)
    monkeypatch.setattr(km, "_running", {})
    monkeypatch.setattr(km, "_task_log_is_empty", lambda tid: True)
    monkeypatch.setattr(km, "_get_task_timeout", lambda tid: 900)
    monkeypatch.setattr(km, "_detect_execution_anomaly", lambda age: (False, ""))


def _insert_task(db_path, task_id, title, status, updated_at):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO kanban_tasks (id, title, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (task_id, title, status, updated_at, updated_at),
    )
    conn.commit()
    conn.close()


def _task_status(db_path, task_id):
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT status, failure_count FROM kanban_tasks WHERE id = ?", (task_id,)
    ).fetchone()
    conn.close()
    return row


# ── _is_manual_gate ──────────────────────────────────────────────────────


class TestIsManualGate:
    def test_id_suffix_matches(self):
        assert km._is_manual_gate("prem-gate-00", "anything") is True
        assert km._is_manual_gate("docmod-gate-00", None) is True

    def test_title_marker_matches(self):
        assert km._is_manual_gate("some-task", GATE_TITLE) is True

    def test_ordinary_task_does_not_match(self):
        assert km._is_manual_gate("prem-p2-01", "Build licensing UI") is False
        assert km._is_manual_gate("efa-E-gate-1-codelens", "PHASE-EXIT GATE") is False

    def test_none_safe(self):
        assert km._is_manual_gate(None, None) is False


# ── _reap_stale_in_progress ──────────────────────────────────────────────


class TestReaperExemption:
    def test_gate_survives_all_reap_thresholds(self, icdev_db, monkeypatch):
        """30 h stale exceeds every threshold — silent-dispatch (1 min),
        normal 2x budget (30 min here), and the 24 h absolute ceiling.
        The gate must survive all of them; an ordinary task must not."""
        _patch_km(icdev_db, monkeypatch)
        stale = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()

        _insert_task(icdev_db, GATE_ID, GATE_TITLE, "in_progress", stale)
        _insert_task(icdev_db, "prem-p2-01", "Ordinary task", "in_progress", stale)

        km._reap_stale_in_progress()

        gate = _task_status(icdev_db, GATE_ID)
        control = _task_status(icdev_db, "prem-p2-01")
        assert gate[0] == "in_progress", "manual-mode gate must never be reaped"
        assert gate[1] == 0, "gate failure_count must not increment"
        assert control[0] == "backlog", "ordinary stale task must still be reaped"


# ── _startup_recover_stale_in_progress ───────────────────────────────────


class TestStartupRecoveryExemption:
    def test_gate_survives_scheduler_restart(self, icdev_db, monkeypatch):
        _patch_km(icdev_db, monkeypatch)
        monkeypatch.setattr(km, "_startup_recovery_done", False)
        ts = datetime.now(timezone.utc).isoformat()

        _insert_task(icdev_db, GATE_ID, GATE_TITLE, "in_progress", ts)
        _insert_task(icdev_db, "prem-p2-01", "Ordinary task", "in_progress", ts)

        km._startup_recover_stale_in_progress()

        assert _task_status(icdev_db, GATE_ID)[0] == "in_progress"
        assert _task_status(icdev_db, "prem-p2-01")[0] == "backlog"


# ── _get_due_tasks ───────────────────────────────────────────────────────


class TestDispatchExclusion:
    def test_gate_in_backlog_is_never_promoted(self, icdev_db, monkeypatch):
        """Even if a gate lands in backlog (e.g. reaped by a pre-fix
        scheduler), it must not be dispatched — only a human moves it."""
        _patch_km(icdev_db, monkeypatch)
        monkeypatch.setattr(km, "_count_in_progress", lambda: 0)
        monkeypatch.setattr(km, "_count_pending_prompts", lambda: 0)
        monkeypatch.setattr(km, "_decompose_batch_tasks", lambda tasks, conn: tasks)
        monkeypatch.setattr(km, "_decompose_phase_exit_gates", lambda tasks, conn: tasks)

        # Space-separated format so SQLite's string compare against
        # datetime('now', '-2 minutes') works (ISO 'T' sorts after ' ').
        old = "2026-01-01 00:00:00"
        _insert_task(icdev_db, GATE_ID, GATE_TITLE, "backlog", old)
        _insert_task(icdev_db, "prem-p2-01", "Ordinary task", "backlog", old)

        due_ids = [t["id"] for t in km._get_due_tasks()]

        assert GATE_ID not in due_ids, "manual-mode gate must never be dispatched"
        assert "prem-p2-01" in due_ids, "ordinary backlog task must still promote"


# ── state_machine auto-close exemption ───────────────────────────────────
#
# Regression: startup_backfill's FK-based auto-close sweep closed a
# manual-mode gate (e.g. lpx-gate-00) to 'done' once every dependent task
# finished. A gate is a human-approval sentinel — automation must NEVER
# release it. The guard lives in state_machine.auto_close_* and is exercised
# through the same backfill path the reflex/dashboard call at startup.


def _insert_dep_task(db_path, task_id, title, status, depends_on):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO kanban_tasks (id, title, status, depends_on_task_id) "
        "VALUES (?, ?, ?, ?)",
        (task_id, title, status, depends_on),
    )
    conn.commit()
    conn.close()


class TestStateMachineAutoCloseExemption:
    def _conn(self, db_path):
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return _TranslatingConn(c)

    def test_gate_parent_not_auto_closed_when_all_children_done(self, icdev_db):
        """A -gate-00 parent whose every dependent is done must stay
        in_progress — auto_close_parent_if_all_children_done must return None."""
        import tools.kanban.state_machine as sm

        _insert_task(icdev_db, GATE_ID, GATE_TITLE, "in_progress",
                     datetime.now(timezone.utc).isoformat())
        _insert_dep_task(icdev_db, "lpx-work-01", "Real work", "done", GATE_ID)

        conn = self._conn(icdev_db)
        result = sm.auto_close_parent_if_all_children_done(GATE_ID, conn)
        conn.commit()
        conn.close()

        assert result is None, "manual-mode gate must not be auto-closed"
        assert _task_status(icdev_db, GATE_ID)[0] == "in_progress"

    def test_non_gate_parent_is_still_auto_closed(self, icdev_db):
        """Guard must not over-block: an ordinary parent with all children
        done still closes to done."""
        import tools.kanban.state_machine as sm

        _insert_task(icdev_db, "reg-parent-01", "Ordinary parent",
                     "in_progress", datetime.now(timezone.utc).isoformat())
        _insert_dep_task(icdev_db, "reg-child-01", "child", "done", "reg-parent-01")

        conn = self._conn(icdev_db)
        result = sm.auto_close_parent_if_all_children_done("reg-parent-01", conn)
        conn.commit()
        conn.close()

        assert result is not None and result.applied
        assert _task_status(icdev_db, "reg-parent-01")[0] == "done"

    def test_backfill_sweep_spares_gate_closes_ordinary(self, icdev_db):
        """The full startup backfill sweep must close an eligible ordinary
        parent while leaving a manual-mode gate untouched."""
        import tools.kanban.state_machine as sm

        _insert_task(icdev_db, GATE_ID, GATE_TITLE, "in_progress",
                     datetime.now(timezone.utc).isoformat())
        _insert_dep_task(icdev_db, "lpx-work-01", "Real work", "done", GATE_ID)
        _insert_task(icdev_db, "reg-parent-01", "Ordinary parent",
                     "in_progress", datetime.now(timezone.utc).isoformat())
        _insert_dep_task(icdev_db, "reg-child-01", "child", "done", "reg-parent-01")

        conn = self._conn(icdev_db)
        closed = sm.backfill_auto_close_parents(conn)
        conn.close()

        assert GATE_ID not in closed, "backfill must never close a manual-mode gate"
        assert "reg-parent-01" in closed
        assert _task_status(icdev_db, GATE_ID)[0] == "in_progress"
        assert _task_status(icdev_db, "reg-parent-01")[0] == "done"


# ── _count_in_progress (dispatch capacity accounting) ────────────────────


class TestCapacityAccounting:
    """A gate is a sentinel, not work, so it must not consume a dispatch slot.

    Regression (2026-07-31): _count_in_progress() was a raw
    COUNT(*) WHERE status='in_progress', which counted gates. _get_due_tasks
    already excluded gates from dispatch, so the two disagreed: every held gate
    permanently burned one of MAX_IN_PROGRESS (default 3) slots. With two gates
    held the pipeline ran at 1 concurrent task, and a single running task drove
    available_slots to 0 — where _get_due_tasks returns [] and the scheduler
    logs "idle (no due tasks)" while due, dependency-satisfied tasks sit in
    'scheduled'. Three gates would have stopped dispatch entirely.
    """

    def test_gates_do_not_consume_dispatch_slots(self, icdev_db, monkeypatch):
        _patch_km(icdev_db, monkeypatch)
        now = datetime.now(timezone.utc).isoformat()

        # Two held gates (matched by id suffix and by title marker respectively)
        # plus one genuinely running task.
        _insert_task(icdev_db, GATE_ID, GATE_TITLE, "in_progress", now)
        _insert_task(icdev_db, "aca-gate-00", "MANUAL-MODE GATE - held", "in_progress", now)
        _insert_task(icdev_db, "tsr-comp-01", "Real running work", "in_progress", now)

        assert km._count_in_progress() == 1, (
            "only genuinely running work may occupy a dispatch slot; "
            "manual-mode gates are sentinels held in_progress forever"
        )

    def test_all_gates_leaves_full_capacity(self, icdev_db, monkeypatch):
        """Three held gates must leave every slot free, not stop dispatch."""
        _patch_km(icdev_db, monkeypatch)
        now = datetime.now(timezone.utc).isoformat()

        for gid in ("prem-gate-00", "aca-gate-00", "gdx-gate-00"):
            _insert_task(icdev_db, gid, "MANUAL-MODE GATE - held", "in_progress", now)

        count = km._count_in_progress()
        assert count == 0, "gates-only board must report zero occupied slots"
        assert km.MAX_IN_PROGRESS - count == km.MAX_IN_PROGRESS, (
            "available_slots must be untouched by gates; at <= 0 "
            "_get_due_tasks returns [] and the pipeline stalls"
        )

    def test_ordinary_in_progress_still_counted(self, icdev_db, monkeypatch):
        """Guard the other direction — the cap must still bound real work."""
        _patch_km(icdev_db, monkeypatch)
        now = datetime.now(timezone.utc).isoformat()

        for tid in ("tsr-a-01", "tsr-b-01", "tsr-c-01"):
            _insert_task(icdev_db, tid, "Real work", "in_progress", now)

        assert km._count_in_progress() == 3
        assert km.MAX_IN_PROGRESS - km._count_in_progress() <= 0, (
            "genuine work must still saturate capacity"
        )
