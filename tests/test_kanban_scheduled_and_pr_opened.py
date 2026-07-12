# CUI // SP-CTI
"""Two board columns that had never, once, held a task.

SCHEDULED: tools/kanban/promote_backlog_to_scheduled.py existed but NOTHING called
it, so backlog went straight to in_progress. The board could not tell "queued, still
blocked" from "ready, waiting for a slot".

AWAITING MERGE (pr_opened): nothing wrote the status. The reflex marked a task `done`
BEFORE its PR was even opened (hence the REFUSED_done_unmerged transitions), and
pr_watcher — the only component that knows a PR actually merged — recorded 'done' in
the audit trail while never touching kanban_tasks.

Both are now wired: backlog -> scheduled -> in_progress -> pr_opened -> done.
"""
from __future__ import annotations

import importlib

import pytest

from tools.db.storage import get_connection

promote_mod = importlib.import_module("tools.kanban.promote_backlog_to_scheduled")
watcher = importlib.import_module("tools.ci.pr_watcher")
kanban = importlib.import_module("tools.genesis.reflexes.kanban")


@pytest.fixture
def db(icdev_db, monkeypatch):
    conn = get_connection(db_path=str(icdev_db))
    monkeypatch.setattr(promote_mod, "get_connection",
                        lambda *a, **k: get_connection(db_path=str(icdev_db)))
    return conn


class TestBacklogPromotesToScheduled:
    def test_a_dep_satisfied_backlog_task_becomes_scheduled(self, db):
        db.execute("INSERT INTO kanban_tasks (id, title, status) "
                   "VALUES ('zs-parent', 'Done parent', 'done')")
        db.execute("INSERT INTO kanban_tasks (id, title, status, depends_on_task_id) "
                   "VALUES ('zs-ready', 'Ready to run', 'backlog', 'zs-parent')")
        db.commit()

        promoted = promote_mod.promote()

        assert "zs-ready" in promoted
        row = dict(db.execute(
            "SELECT status, scheduled_at FROM kanban_tasks WHERE id = 'zs-ready'"
        ).fetchone())
        assert row["status"] == "scheduled"
        # scheduled_at must be set, or _get_due_tasks will never pick it up again.
        assert row["scheduled_at"]

    def test_a_blocked_backlog_task_stays_in_backlog(self, db):
        """This is what keeps a manual gate's dependents off the board's ready lane."""
        db.execute("INSERT INTO kanban_tasks (id, title, status) VALUES "
                   "('zs-gate-00', 'MANUAL-MODE GATE', 'in_progress')")
        db.execute("INSERT INTO kanban_tasks (id, title, status, depends_on_task_id) "
                   "VALUES ('zs-blocked', 'Gated work', 'backlog', 'zs-gate-00')")
        db.commit()

        promoted = promote_mod.promote()

        assert "zs-blocked" not in promoted
        row = dict(db.execute(
            "SELECT status FROM kanban_tasks WHERE id = 'zs-blocked'").fetchone())
        assert row["status"] == "backlog"

    def test_dry_run_writes_nothing(self, db):
        db.execute("INSERT INTO kanban_tasks (id, title, status) "
                   "VALUES ('zs-solo', 'No deps', 'backlog')")
        db.commit()

        promote_mod.promote(dry_run=True)

        row = dict(db.execute(
            "SELECT status FROM kanban_tasks WHERE id = 'zs-solo'").fetchone())
        assert row["status"] == "backlog"

    def test_promote_sql_is_postgres_native(self):
        """This module is RUNTIME, not init/seed: it must not lean on translate_sql's
        `?` -> `%s` rescue, which is an init-only fallback (CLAUDE.md)."""
        import inspect

        src = inspect.getsource(promote_mod)
        sql_lines = [ln for ln in src.splitlines()
                     if ("execute(" in ln or "WHERE" in ln or "SET" in ln)
                     and "?" in ln and not ln.strip().startswith("#")]
        assert not sql_lines, f"bare ? placeholders left: {sql_lines}"


class TestPrOpenedIsWatched:
    def test_watcher_query_includes_pr_opened(self):
        """The watcher exists to service pr_opened. It was not even looking at it —
        so the instant a task got a PR, the watcher lost sight of it."""
        import inspect

        src = inspect.getsource(watcher.list_pr_tasks)
        assert "'pr_opened'" in src, (
            "pr_watcher does not select pr_opened tasks — a task parked there would "
            "never be followed to merge"
        )

    def test_watcher_can_write_status(self, icdev_db):
        """It classified a merged PR as 'done' in the audit trail and never told the
        board. The merge is the completion; the watcher is the only thing that sees it."""
        conn = get_connection(db_path=str(icdev_db))
        conn.execute("INSERT INTO kanban_tasks (id, title, status) "
                     "VALUES ('zs-pr', 'Awaiting merge', 'pr_opened')")
        conn.commit()

        ok = watcher._set_task_status(
            lambda: get_connection(db_path=str(icdev_db)),
            "zs-pr", "done", reason="PR merged: https://example/pr/1",
        )

        assert ok is True
        row = dict(get_connection(db_path=str(icdev_db)).execute(
            "SELECT status FROM kanban_tasks WHERE id = 'zs-pr'").fetchone())
        assert row["status"] == "done"

    def test_status_write_never_raises_on_a_bad_connection(self):
        """A status-write failure must not stall the watch loop."""
        def _boom():
            raise RuntimeError("db is down")

        assert watcher._set_task_status(_boom, "zs-x", "done") is False


class TestPrFlowGate:
    def test_pr_flow_flag(self, monkeypatch):
        monkeypatch.setenv("ICDEV_KANBAN_PR_FLOW", "true")
        assert kanban._pr_flow_enabled() is True
        monkeypatch.setenv("ICDEV_KANBAN_PR_FLOW", "")
        assert kanban._pr_flow_enabled() is False

    def test_done_is_deferred_only_when_a_pr_will_actually_open(self):
        """A verified task with NO commits (a research/answer task) opens no PR and is
        genuinely done now — it must not be stranded waiting for a merge that will
        never come."""
        import inspect

        src = inspect.getsource(kanban.run.__globals__["_pr_flow_enabled"])
        assert "ICDEV_KANBAN_PR_FLOW" in src

        reflex_src = inspect.getsource(kanban)
        assert "_will_open_pr = (" in reflex_src
        # The guard must require BOTH pr-flow AND real commits — otherwise a
        # commitless task would wait forever for a PR that never opens.
        guard = reflex_src.split("_will_open_pr = (", 1)[1].split("if not _will_open_pr", 1)[0]
        assert "_pr_flow_enabled()" in guard
        assert "_check_worktree_commits(task_id)" in guard


class TestPromoteNeverTouchesAManualGate:
    def test_a_gate_sitting_in_backlog_is_not_promoted(self, db):
        """A gate has NO dependencies, so _deps_satisfied() calls it "ready" and the
        promoter would schedule it straight out of the state that makes it a gate.
        (Observed live: prem-gate-00 went in_progress -> backlog -> scheduled.)"""
        db.execute("INSERT INTO kanban_tasks (id, title, status) VALUES "
                   "('zg-gate-00', 'MANUAL-MODE GATE - do not complete', 'backlog')")
        db.commit()

        promoted = promote_mod.promote()

        assert "zg-gate-00" not in promoted
        row = dict(db.execute(
            "SELECT status FROM kanban_tasks WHERE id = 'zg-gate-00'").fetchone())
        assert row["status"] == "backlog"

    def test_a_gate_identified_only_by_title_is_also_skipped(self, db):
        db.execute("INSERT INTO kanban_tasks (id, title, status) VALUES "
                   "('zg-oddname', 'MANUAL-MODE GATE - hold me', 'backlog')")
        db.commit()

        assert "zg-oddname" not in promote_mod.promote()
