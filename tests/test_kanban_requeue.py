# CUI // SP-CTI
"""Re-queueing a task must not manufacture a phantom triage queue.

Measured 2026-08-08 (PR #1379). Re-queueing the nine ``sbx`` tasks — closing
their stale PRs so they would rebuild against current main — refreshed
``updated_at`` while the rows still carried the ``last_failure_reason`` from the
build attempts whose PRs had just been closed.

``tools/workflow/failure_triage.py::find_recent_failures`` selects on exactly::

    last_failure_reason IS NOT NULL
      AND updated_at > cutoff
      AND status IN ('backlog','failed','scheduled','needs_decomposition')

so five healthy tasks entered the autofix queue. ``ICDEV_AUTOFIX_ENABLED=true``
at the time; only the absence of ``ICDEV_AUTOFIX_AUTOMERGE`` kept generated
patches off main.

The two halves of the fix, and of this file:
  * a re-queued task must NOT be returned by ``find_recent_failures``; and
  * a task with a REAL current failure must STILL be returned — the fix must
    clear a stale reason, not blind triage.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests._sql_compat import translating  # noqa: E402


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _schema(conn):
    conn.executescript(
        """
        CREATE TABLE kanban_tasks (
            id TEXT PRIMARY KEY,
            title TEXT,
            description TEXT,
            task_type TEXT,
            priority TEXT,
            status TEXT,
            updated_at TEXT,
            scheduled_at TEXT,
            branch_name TEXT,
            depends_on_task_id TEXT,
            failure_count INTEGER DEFAULT 0,
            last_failure_at TEXT,
            last_failure_reason TEXT
        );
        CREATE TABLE kanban_status_transitions (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            from_status TEXT,
            to_status TEXT,
            actor TEXT,
            reason TEXT,
            recorded_at TEXT
        );
        """
    )


def _conn(raw):
    """A placeholder-translating connection over the fixture's sqlite3 handle.

    The code under test writes PostgreSQL-dialect ``%s`` SQL; only
    ``translate_sql`` turns that into sqlite's ``?``. Handing runtime code a
    bare ``sqlite3.connect`` would make every statement raise
    ``near "%": syntax error`` — and since ``find_recent_failures`` swallows
    its query into a ``logger.warning``, it would just return ``[]`` and every
    "the task is NOT in the queue" assertion below would pass for the wrong
    reason. ``_sql_compat.translating`` delegates to the same ``translate_sql``
    the runtime uses, so this cannot drift from production.

    ``unclosable``: one test drives several ``with get_conn() as conn:`` blocks
    over the one fixture connection, and ``__exit__`` closes what it commits.
    """
    return translating(raw, unclosable=True)


@pytest.fixture()
def db(tmp_path):
    raw = sqlite3.connect(str(tmp_path / "k.db"))
    raw.row_factory = sqlite3.Row
    _schema(raw)
    yield raw
    raw.close()


def _insert(raw, tid, *, status, failure_reason, failure_count=3,
            updated_at=None, branch_name=None, priority="high",
            task_type="build"):
    raw.execute(
        "INSERT INTO kanban_tasks (id, title, description, task_type, priority, "
        " status, updated_at, branch_name, failure_count, last_failure_at, "
        " last_failure_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (tid, f"title {tid}", f"desc {tid}", task_type, priority, status,
         updated_at or _utcnow().isoformat(), branch_name, failure_count,
         "2026-08-08T09:00:00+00:00", failure_reason),
    )
    raw.commit()


def _row(raw, tid):
    return dict(raw.execute("SELECT * FROM kanban_tasks WHERE id = ?", (tid,)).fetchone())


@pytest.fixture()
def triage(monkeypatch, db):
    """Point failure_triage's query at the fixture DB.

    ``find_recent_failures`` imports ``get_connection`` *inside* the function,
    so the module attribute is what has to be patched — and there are two
    modules to choose from. ``tools.db`` resolves to ``icdev/tools/db/``, whose
    ``__getattr__`` hands back the ``icdev/`` copy of ``storage``; but
    ``from tools.db.storage import get_connection`` binds
    ``sys.modules['tools.db.storage']``, the root ``tools/db/storage.py``.
    So ``import tools.db.storage as storage`` (which goes through
    ``getattr(tools.db, 'storage')``) patches the WRONG object.

    Getting this wrong is silent: the real connection stays in place,
    ``find_recent_failures`` swallows the resulting "no such table" into a
    ``logger.warning`` and returns ``[]`` — at which point every "the task is
    NOT in the queue" assertion below passes for the wrong reason. That is what
    the positive-control tests in this file exist to catch.
    """
    import sys as _sys

    import tools.db.storage  # noqa: F401 - ensure the root module is loaded
    from tools.workflow import failure_triage

    storage = _sys.modules["tools.db.storage"]
    monkeypatch.setattr(storage, "get_connection", lambda *a, **kw: _conn(db))
    return failure_triage


def _requeue(db, tid, **kw):
    from tools.kanban.requeue import requeue_task

    return requeue_task(tid, get_conn=lambda: _conn(db), **kw)


# --------------------------------------------------------------------------
# The regression: a clean re-queue must not look like a fresh failure
# --------------------------------------------------------------------------

@pytest.mark.parametrize("from_status", ["pr_opened", "failed", "in_progress"])
def test_requeued_task_is_not_returned_by_triage(triage, db, from_status):
    """The sbx case: PR closed, task sent back to rebuild against current main."""
    _insert(db, "sbx-doc-01", status=from_status,
            failure_reason="CI failed on the branch whose PR was just closed",
            branch_name="kanban/sbx-doc-01")

    # Precondition — before the re-queue the row IS a triage candidate whenever
    # its status is one triage scans. This is what the hand-written UPDATE left.
    if from_status in ("failed",):
        assert any(r["id"] == "sbx-doc-01" for r in triage.find_recent_failures(24))

    result = _requeue(db, "sbx-doc-01", status="backlog",
                      reason="closing stale PR; rebuild against current main")
    assert result["requeued"] is True
    assert result["from_status"] == from_status

    found = [r["id"] for r in triage.find_recent_failures(24)]
    assert "sbx-doc-01" not in found, (
        "a re-queued task still matches find_recent_failures — the "
        "fresh-updated_at + stale-last_failure_reason pair is exactly what "
        "put five healthy sbx tasks into the autofix queue on 2026-08-08"
    )


def test_requeue_preserves_failure_count(db):
    """failure_count is the recovery guard's budget and the real history.

    Zeroing it would launder a task that has genuinely failed five times into a
    fresh one, handing it a full retry budget it has already spent.
    """
    _insert(db, "sbx-doc-02", status="pr_opened", failure_reason="boom",
            failure_count=5)

    result = _requeue(db, "sbx-doc-02", status="backlog")

    row = _row(db, "sbx-doc-02")
    assert row["failure_count"] == 5
    assert row["last_failure_at"] == "2026-08-08T09:00:00+00:00", (
        "last_failure_at is history too — only the *reason* describes the "
        "attempt that is no longer current"
    )
    assert result["failure_count"] == 5


def test_requeue_writes_a_status_transition_row(db):
    """A re-queue must be attributable, not an anonymous field edit."""
    _insert(db, "sbx-doc-03", status="pr_opened", failure_reason="boom")

    result = _requeue(db, "sbx-doc-03", status="backlog", actor="cli",
                      reason="closing stale PR")
    assert result["transition_recorded"] is True

    rows = [dict(r) for r in db.execute(
        "SELECT * FROM kanban_status_transitions WHERE task_id = ?",
        ("sbx-doc-03",),
    ).fetchall()]
    assert len(rows) == 1
    assert rows[0]["from_status"] == "pr_opened"
    assert rows[0]["to_status"] == "backlog"
    assert rows[0]["actor"] == "cli"
    assert "closing stale PR" in rows[0]["reason"]


# --------------------------------------------------------------------------
# The other half: the fix must not blind triage
# --------------------------------------------------------------------------

def test_a_real_current_failure_is_still_triaged(triage, db):
    """A task that actually failed just now must still reach the triage queue."""
    _insert(db, "real-fail-01", status="failed",
            failure_reason="AssertionError in tests/test_foo.py::test_bar")

    found = [r["id"] for r in triage.find_recent_failures(24)]
    assert "real-fail-01" in found, (
        "clearing a STALE reason must not stop triage seeing a REAL one"
    )


def test_requeueing_one_task_does_not_hide_another_tasks_failure(triage, db):
    """The two rows coexist: only the re-queued one leaves the queue."""
    _insert(db, "sbx-doc-04", status="failed", failure_reason="stale, PR closed")
    _insert(db, "real-fail-02", status="failed", failure_reason="genuine CI failure")

    _requeue(db, "sbx-doc-04", status="backlog")

    found = [r["id"] for r in triage.find_recent_failures(24)]
    assert "sbx-doc-04" not in found
    assert "real-fail-02" in found


def test_a_task_that_fails_again_after_requeue_is_triaged_again(triage, db):
    """The clear is not sticky — the next real failure re-enters the queue."""
    _insert(db, "sbx-doc-05", status="pr_opened", failure_reason="stale")
    _requeue(db, "sbx-doc-05", status="backlog")
    assert "sbx-doc-05" not in [r["id"] for r in triage.find_recent_failures(24)]

    db.execute(
        "UPDATE kanban_tasks SET status = 'failed', last_failure_reason = ?, "
        "failure_count = failure_count + 1, updated_at = ? WHERE id = ?",
        ("new build blew up", _utcnow().isoformat(), "sbx-doc-05"),
    )
    db.commit()

    assert "sbx-doc-05" in [r["id"] for r in triage.find_recent_failures(24)]
    assert _row(db, "sbx-doc-05")["failure_count"] == 4, (
        "the preserved count is what makes the budget cumulative across re-queues"
    )


# --------------------------------------------------------------------------
# Field semantics
# --------------------------------------------------------------------------

def test_requeue_clears_the_branch(db):
    """The point of a re-queue is to rebuild from current main, not to resume
    the branch whose PR was just closed."""
    _insert(db, "sbx-doc-06", status="pr_opened", failure_reason="stale",
            branch_name="kanban/sbx-doc-06")

    result = _requeue(db, "sbx-doc-06", status="backlog")

    assert _row(db, "sbx-doc-06")["branch_name"] is None
    assert set(result["cleared"]) == {"last_failure_reason", "branch_name"}


def test_requeue_to_scheduled_stamps_scheduled_at(db):
    """_get_due_tasks requires scheduled_at IS NOT NULL; without it the
    re-queued row is invisible to the dispatcher and simply never rebuilds."""
    _insert(db, "sbx-doc-07", status="pr_opened", failure_reason="stale")
    assert _row(db, "sbx-doc-07")["scheduled_at"] is None

    _requeue(db, "sbx-doc-07", status="scheduled")

    row = _row(db, "sbx-doc-07")
    assert row["status"] == "scheduled"
    assert row["scheduled_at"] is not None


def test_requeue_to_scheduled_does_not_push_an_existing_due_time_back(db):
    _insert(db, "sbx-doc-08", status="pr_opened", failure_reason="stale")
    db.execute("UPDATE kanban_tasks SET scheduled_at = ? WHERE id = ?",
               ("2026-07-01T00:00:00+00:00", "sbx-doc-08"))
    db.commit()

    _requeue(db, "sbx-doc-08", status="scheduled")

    assert _row(db, "sbx-doc-08")["scheduled_at"] == "2026-07-01T00:00:00+00:00"


def test_requeue_works_on_a_pipeline_owned_status(db):
    """`pr_opened` is not in cli.VALID_STATUSES and the dashboard move API
    rejects it too, which is why a human re-queue had no clean path back."""
    from tools.kanban import cli

    assert "pr_opened" not in cli.VALID_STATUSES
    _insert(db, "sbx-doc-09", status="pr_opened", failure_reason="stale")

    assert _requeue(db, "sbx-doc-09", status="backlog")["requeued"] is True
    assert _row(db, "sbx-doc-09")["status"] == "backlog"


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------

def test_requeue_rejects_a_non_pickup_target(db):
    """`done`/`failed` are status changes, not re-queues — those stay on
    --set-status, which enforces the merge-verify gate."""
    _insert(db, "sbx-doc-10", status="pr_opened", failure_reason="stale")

    result = _requeue(db, "sbx-doc-10", status="done")

    assert result["requeued"] is False
    assert "invalid re-queue target" in result["error"]
    assert _row(db, "sbx-doc-10")["status"] == "pr_opened"
    assert _row(db, "sbx-doc-10")["last_failure_reason"] == "stale"


def test_requeue_of_an_unknown_task_reports_rather_than_raises(db):
    result = _requeue(db, "no-such-task-99", status="backlog")
    assert result["requeued"] is False
    assert result["error"] == "not found"


def test_requeue_refuses_a_manual_gate_sentinel(db):
    """Gate sentinels are held in_progress on purpose; releasing one dispatches
    every task behind it on a MANUAL-ONLY board."""
    _insert(db, "sbx-gate-00", status="in_progress", failure_reason="stale")

    result = _requeue(db, "sbx-gate-00", status="backlog")

    assert result["requeued"] is False
    assert "manual-mode gate sentinel" in result["error"]
    assert _row(db, "sbx-gate-00")["status"] == "in_progress"

    forced = _requeue(db, "sbx-gate-00", status="backlog", force=True)
    assert forced["requeued"] is True


# --------------------------------------------------------------------------
# The window boundary — proves the tests are actually exercising the predicate
# --------------------------------------------------------------------------

def test_an_old_failure_is_outside_the_window(triage, db):
    _insert(db, "old-fail-01", status="failed", failure_reason="failed last week",
            updated_at=(_utcnow() - timedelta(days=7)).isoformat())
    assert "old-fail-01" not in [r["id"] for r in triage.find_recent_failures(24)]
