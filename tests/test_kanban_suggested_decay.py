# CUI // SP-CTI
"""The suggested-decay sweep must not manufacture the phantom triage queue.

``tools/genesis/reflexes/kanban.py::_promote_stale_suggested`` re-queues a task
that has sat in ``suggested`` for more than 48 h. Until kax-recover-05 it did so
with a local UPDATE that carried both of the defects
``tools/kanban/requeue.py::requeue_task`` was written to prevent — and unlike
the hand-run UPDATE that motivated ``requeue_task`` (kax-recover-02, PR #1382),
this one runs unattended on every sweep.

(1) Phantom triage. It wrote a *non-failure* string into
    ``last_failure_reason`` while setting ``status='scheduled'`` and a fresh
    ``updated_at``. ``failure_triage.find_recent_failures`` selects on exactly::

        last_failure_reason IS NOT NULL
          AND updated_at > cutoff
          AND status IN ('backlog','failed','scheduled','needs_decomposition')

    so every decay-promoted task entered the autofix queue carrying a "reason"
    that describes a promotion. 114 rows on the live board still carry the
    string ``decay-promoted:`` (measured 2026-08-08).

(2) Quarantine laundering. It set ``failure_count=0`` while the guard directly
    above it uses ``fc >= 5`` as the hard-quarantine test. A task that passed
    through ``suggested`` therefore had its quarantine budget reset every 48 h
    and could never reach hard quarantine — it looped forever.

The two halves of the fix, and of this file:
  * a decay-promoted task must NOT be returned by ``find_recent_failures``, and
    its ``failure_count`` must survive the promotion; and
  * a genuinely hard-quarantined task must STILL be skipped — the fix must stop
    laundering quarantine, not release it.

The rationale moves to ``kanban_status_transitions``, which is an audit surface,
rather than ``last_failure_reason``, which is a triage input.
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


def _ago(**kw) -> str:
    return (_utcnow() - timedelta(**kw)).isoformat()


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

    The sweep, ``requeue_task`` and ``find_recent_failures`` all write
    PostgreSQL-dialect ``%s`` SQL; only ``translate_sql`` turns that into
    sqlite's ``?``. Handing them a bare ``sqlite3`` handle would make every
    statement raise ``near "%": syntax error`` — and because both the sweep and
    ``find_recent_failures`` swallow failures into a ``logger.warning``, the
    sweep would silently do nothing and triage would return ``[]``, so every
    "not in the queue" assertion below would pass for the wrong reason. The
    positive controls in this file exist to catch exactly that.

    ``unclosable``: the sweep opens several ``with get_connection() as conn:``
    blocks over what is here a single fixture connection, and ``__exit__``
    closes what it commits.
    """
    return translating(raw, unclosable=True)


@pytest.fixture()
def db(tmp_path):
    raw = sqlite3.connect(str(tmp_path / "k.db"))
    raw.row_factory = sqlite3.Row
    _schema(raw)
    yield raw
    raw.close()


@pytest.fixture()
def wired(monkeypatch, db):
    """Point the sweep, ``requeue_task`` and ``failure_triage`` at the fixture DB.

    Three call sites resolve ``get_connection`` differently and all three have
    to land on the fixture:

    * the sweep uses the module-global in ``tools.genesis.reflexes.kanban``;
    * ``requeue_task`` and ``_record_status_transition`` import it from
      ``tools.db.storage`` *inside* the function, so the module attribute is
      what must be patched — and ``import tools.db.storage as storage`` would
      patch the WRONG object, because ``tools.db.__getattr__`` hands back the
      ``icdev/`` copy while ``from tools.db.storage import ...`` binds
      ``sys.modules['tools.db.storage']``. ``import_module`` returns that same
      binding, which is why CLAUDE.md prescribes it for shim-aware patching.
    """
    import importlib

    from tools.genesis.reflexes import kanban as reflex
    from tools.workflow import failure_triage

    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **kw: _conn(db))
    monkeypatch.setattr(reflex, "get_connection", lambda *a, **kw: _conn(db))
    return reflex, failure_triage


@pytest.fixture()
def decay_only(wired, monkeypatch):
    """The decay pass alone.

    ``_promote_stale_suggested`` also drives two adjacent passes
    (``_revive_quarantined_suggested``, ``_unblock_dep_chain``) that revive
    ``suggested`` tasks on their own criteria. Silencing them keeps the
    quarantine assertions attributable to the decay pass rather than to whichever
    pass happened to touch the row last. They get their own tests below.
    """
    reflex, triage = wired
    monkeypatch.setattr(reflex, "_revive_quarantined_suggested", lambda conn: None)
    monkeypatch.setattr(reflex, "_unblock_dep_chain", lambda conn: None)
    return reflex, triage


def _insert(raw, tid, *, status="suggested", failure_count=0,
            last_failure_reason=None, updated_at=None,
            depends_on_task_id=None, branch_name=None, priority="high"):
    raw.execute(
        "INSERT INTO kanban_tasks (id, title, description, task_type, priority, "
        " status, updated_at, scheduled_at, branch_name, depends_on_task_id, "
        " failure_count, last_failure_at, last_failure_reason) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, f"title {tid}", f"desc {tid}", "build", priority, status,
         updated_at or _ago(hours=72), None, branch_name, depends_on_task_id,
         failure_count, "2026-08-06T09:00:00+00:00", last_failure_reason),
    )
    raw.commit()


def _row(raw, tid):
    return dict(raw.execute("SELECT * FROM kanban_tasks WHERE id = ?", (tid,)).fetchone())


def _transitions(raw, tid):
    return [dict(r) for r in raw.execute(
        "SELECT * FROM kanban_status_transitions WHERE task_id = ?", (tid,)
    ).fetchall()]


# --------------------------------------------------------------------------
# (1) The phantom triage queue
# --------------------------------------------------------------------------

def test_decay_promoted_task_is_not_returned_by_triage(decay_only, db):
    """The whole point: a promotion is not a failure, so triage must not see it.

    Before the fix the row landed as ``status='scheduled'`` +
    ``updated_at=now`` + ``last_failure_reason='decay-promoted: ...'`` — all
    three legs of ``find_recent_failures``'s WHERE clause at once.
    """
    reflex, triage = decay_only
    _insert(db, "kax-decay-01", failure_count=2,
            last_failure_reason="pytest exited 1 on tests/test_foo.py")

    reflex._promote_stale_suggested()

    row = _row(db, "kax-decay-01")
    assert row["status"] == "scheduled", "the stale task should still be re-queued"
    assert row["last_failure_reason"] is None, (
        "the promotion rationale must not be parked in last_failure_reason — "
        "that column is a triage input, not a note field"
    )

    found = [r["id"] for r in triage.find_recent_failures(24)]
    assert "kax-decay-01" not in found, (
        "a decay-promoted task still matches find_recent_failures; this is the "
        "unattended, every-48-h version of the phantom queue kax-recover-02 closed"
    )


def test_triage_still_sees_a_genuine_failure(decay_only, db):
    """Positive control.

    Without this, a fixture mistake that made ``find_recent_failures`` return
    ``[]`` — a wrong monkeypatch target, an untranslated ``%s`` — would make the
    test above pass while proving nothing.
    """
    _reflex, triage = decay_only
    _insert(db, "kax-decay-real", status="failed", failure_count=2,
            updated_at=_utcnow().isoformat(),
            last_failure_reason="pytest exited 1 on tests/test_foo.py")

    found = [r["id"] for r in triage.find_recent_failures(24)]
    assert "kax-decay-real" in found


# --------------------------------------------------------------------------
# (2) Quarantine laundering
# --------------------------------------------------------------------------

def test_decay_promotion_preserves_failure_count(decay_only, db):
    """``failure_count`` is recovery_guard's budget and the task's real history.

    Zeroing it on every 48 h decay handed a repeatedly-failing task a full fresh
    retry budget, so it could never reach the ``fc >= 5`` hard-quarantine test
    that the very same function applies.
    """
    reflex, _triage = decay_only
    _insert(db, "kax-decay-02", failure_count=4,
            last_failure_reason="executor timed out")

    reflex._promote_stale_suggested()

    row = _row(db, "kax-decay-02")
    assert row["status"] == "scheduled"
    assert row["failure_count"] == 4, (
        "failure_count was reset — the task's quarantine budget has been "
        "laundered and fc>=5 becomes unreachable for anything that cycles "
        "through 'suggested'"
    )
    assert row["last_failure_at"] == "2026-08-06T09:00:00+00:00", (
        "last_failure_at is history too; only the *reason* describes an attempt "
        "that is no longer current"
    )


def test_hard_quarantined_task_is_still_skipped(decay_only, db):
    """The fix must stop laundering quarantine, not release it.

    A task at ``fc >= 5`` is genuinely quarantined and waits for a human. It
    must come out of the sweep untouched: same status, same count, no
    transition row.
    """
    reflex, triage = decay_only
    _insert(db, "kax-decay-03", failure_count=5,
            last_failure_reason="build failed: unresolved import")

    reflex._promote_stale_suggested()

    row = _row(db, "kax-decay-03")
    assert row["status"] == "suggested", "hard quarantine was released"
    assert row["failure_count"] == 5
    assert row["last_failure_reason"] == "build failed: unresolved import", (
        "a skipped task must be left entirely alone — its real failure reason "
        "is what a human reviews"
    )
    assert _transitions(db, "kax-decay-03") == []
    # Its real failure reason is genuine, so triage SHOULD still see it — but
    # 'suggested' is not one of the statuses triage scans.
    assert "kax-decay-03" not in [r["id"] for r in triage.find_recent_failures(24)]


@pytest.mark.parametrize("reason", [
    "hard-quarantine: repeated identical failure",
    "HITL review required before another attempt",
])
def test_explicitly_quarantined_task_is_still_skipped(decay_only, db, reason):
    """The reason-string half of the quarantine guard, at fc below the numeric cap."""
    reflex, _triage = decay_only
    _insert(db, "kax-decay-04", failure_count=1, last_failure_reason=reason)

    reflex._promote_stale_suggested()

    row = _row(db, "kax-decay-04")
    assert row["status"] == "suggested"
    assert row["last_failure_reason"] == reason


def test_preserved_count_lets_a_looping_task_reach_hard_quarantine(decay_only, db):
    """The end-to-end consequence of preserving the count.

    Decay-promote at fc=4, let the next attempt fail (fc -> 5) and re-park the
    task, and the following sweep now refuses it. Under the old ``failure_count=0``
    this second sweep promoted it again, and every sweep after that, forever.
    """
    reflex, _triage = decay_only
    _insert(db, "kax-decay-05", failure_count=4, last_failure_reason="flaky E2E")

    reflex._promote_stale_suggested()
    assert _row(db, "kax-decay-05")["failure_count"] == 4

    # The re-queued attempt fails: the dispatcher bumps the count and re-parks it.
    db.execute(
        "UPDATE kanban_tasks SET status='suggested', failure_count=5, "
        "last_failure_reason='flaky E2E', updated_at=? WHERE id=?",
        (_ago(hours=72), "kax-decay-05"),
    )
    db.commit()

    reflex._promote_stale_suggested()

    assert _row(db, "kax-decay-05")["status"] == "suggested", (
        "the loop is still open: a task cycling through 'suggested' never "
        "reaches hard quarantine"
    )


# --------------------------------------------------------------------------
# The rationale lands on the audit surface instead
# --------------------------------------------------------------------------

def test_promotion_rationale_is_recorded_on_the_transition_row(decay_only, db):
    """"Why" does not disappear — it moves somewhere a human can read it."""
    reflex, _triage = decay_only
    _insert(db, "kax-decay-06", failure_count=2, last_failure_reason="boom")

    reflex._promote_stale_suggested()

    rows = _transitions(db, "kax-decay-06")
    assert len(rows) == 1, f"expected exactly one transition row, got {rows}"
    t = rows[0]
    assert t["from_status"] == "suggested"
    assert t["to_status"] == "scheduled"
    assert t["actor"] == "suggested-decay-sweep"
    assert "suggested-decay" in t["reason"]
    assert str(reflex._SUGGESTED_DECAY_HOURS) in t["reason"], (
        "the rationale should still say how long the task sat in 'suggested'"
    )
    assert t["recorded_at"]


def test_decay_promotion_clears_the_stale_branch(decay_only, db):
    """A re-queue rebuilds against current main; it does not resume the old branch.

    Inherited from ``requeue_task`` — asserted here so routing the sweep back
    through a local UPDATE would fail loudly.
    """
    reflex, _triage = decay_only
    _insert(db, "kax-decay-07", failure_count=1, branch_name="kanban/kax-decay-07")

    reflex._promote_stale_suggested()

    row = _row(db, "kax-decay-07")
    assert row["branch_name"] is None
    assert row["scheduled_at"] is not None, (
        "_get_due_tasks requires a non-NULL scheduled_at; without it the "
        "promoted row is invisible to the dispatcher"
    )


# --------------------------------------------------------------------------
# The two adjacent passes carried the same phantom-reason defect
# --------------------------------------------------------------------------

def test_auto_revive_does_not_write_a_phantom_failure_reason(wired, db):
    """``_revive_quarantined_suggested`` wrote "auto-revive N/M: ..." into
    ``last_failure_reason`` while moving the task to ``backlog`` — the same three
    legs of the triage WHERE clause.

    It still resets ``failure_count``, and that is deliberate: this pass only
    acts on ``fc >= 5`` tasks and the dispatcher's circuit breaker blocks at
    ``fc >= max_retries`` (default 5), so preserving the count would re-park the
    task the instant it was revived. The budget bounding this path is
    ``revive_count`` in ``kanban_task_revivals``, which survives re-quarantine.
    """
    reflex, triage = wired
    _insert(db, "kax-revive-01", failure_count=6,
            last_failure_reason="hard-quarantine: five identical failures")

    reflex._promote_stale_suggested()

    row = _row(db, "kax-revive-01")
    assert row["status"] == "backlog", "the bounded auto-revive should still fire"
    assert row["last_failure_reason"] is None
    assert "kax-revive-01" not in [r["id"] for r in triage.find_recent_failures(24)]

    rows = _transitions(db, "kax-revive-01")
    assert len(rows) == 1
    assert rows[0]["actor"] == "auto-revive"
    assert "auto-revive" in rows[0]["reason"]
    assert "deps satisfied" in rows[0]["reason"]


def test_dep_chain_unblock_does_not_write_a_phantom_failure_reason(wired, db):
    """``_unblock_dep_chain`` had the same defect.

    The parent is updated recently so the decay pass skips it and only the
    critical-path unblock can act — otherwise the assertion could not tell which
    pass moved the row.
    """
    reflex, triage = wired
    _insert(db, "kax-parent-01", failure_count=1,
            updated_at=_utcnow().isoformat(),
            last_failure_reason="no executor available")
    _insert(db, "kax-child-01", status="backlog", failure_count=0,
            updated_at=_utcnow().isoformat(),
            depends_on_task_id="kax-parent-01")

    reflex._promote_stale_suggested()

    row = _row(db, "kax-parent-01")
    assert row["status"] == "backlog"
    assert row["last_failure_reason"] is None
    assert "kax-parent-01" not in [r["id"] for r in triage.find_recent_failures(24)]

    rows = _transitions(db, "kax-parent-01")
    assert len(rows) == 1
    assert rows[0]["actor"] == "dep-chain-unblock"
    assert "dep-chain-unblock" in rows[0]["reason"]
    assert "fc was 1" in rows[0]["reason"], (
        "the count at unblock time is the detail worth keeping; it is no longer "
        "recoverable from the task row once failure_count is reset"
    )


def test_full_sweep_leaves_no_phantom_reason_anywhere(wired, db):
    """Belt-and-braces: after one full sweep, nothing the sweep touched is in
    the triage queue and no row carries a sweep-authored 'reason'."""
    reflex, triage = wired
    _insert(db, "kax-sweep-01", failure_count=2, last_failure_reason="boom")
    _insert(db, "kax-sweep-02", failure_count=7,
            last_failure_reason="hard-quarantine: giving up")
    _insert(db, "kax-sweep-03", failure_count=0, last_failure_reason=None)

    reflex._promote_stale_suggested()

    reasons = [
        (dict(r)["id"], dict(r)["last_failure_reason"] or "")
        for r in db.execute("SELECT id, last_failure_reason FROM kanban_tasks").fetchall()
    ]
    for tid, reason in reasons:
        for phantom in ("decay-promoted", "auto-revive", "dep-chain-unblock"):
            assert phantom not in reason, (
                f"{tid} carries a sweep-authored string in last_failure_reason: {reason!r}"
            )
    assert triage.find_recent_failures(24) == []
