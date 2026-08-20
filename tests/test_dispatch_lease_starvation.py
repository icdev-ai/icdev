# CUI // SP-CTI
"""A held coordination lease must not starve the dispatch queue (rem-hyg-15).

WHAT HAPPENED. On 2026-08-20 the scheduler reported
``idle [review_bound]: 5 task(s) are scheduled and due but every one was
withheld at dispatch`` every 60 seconds for over an hour, with ZERO open PRs,
ZERO tasks in progress and every dispatch slot free.

Three tasks had been seeded with ``claim=True`` from one-shot scripts that
exited seconds later. Their ``kanban:task:<id>`` leases outlived them, and
nothing on the dispatch path ever called ``release_stale`` — so ``acquire()``
kept refusing on behalf of processes that no longer existed. Because those three
were the highest-priority due tasks, ``_get_due_tasks`` selected exactly them,
truncated the candidate list to ``available_slots`` BEFORE the lease was
checked, and the two genuinely dispatchable tasks behind them were never
considered.

That is the same starvation ``_drop_respawn_guarded`` already existed to prevent
— its own docstring describes it for open PRs — with a third cause nobody had
added: a held lease.

THE PART THAT IS EASY TO GET WRONG, and which the first draft of the fix DID get
wrong: the pid recorded on a lease is the pid of the process that TOOK it, which
for a dispatch is the scheduler's short-lived child. It exits as soon as it has
handed off, while the worker runs on for minutes under a different pid. Verified
live: ``rem-hyg-13`` reported ``holder_is_alive() is False`` while heartbeating
four seconds earlier. Reaping on a dead pid ALONE would free a lease guarding
work that is actively running, and two workers would build the same task —
strictly worse than the stall being fixed.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis.reflexes import kanban as k  # noqa: E402


class _FakeLeases:
    """Stand-in for tools.coordination.leases with a scriptable holder table."""

    def __init__(self, holders=None, alive=None):
        self._holders = holders or {}
        self._alive = alive or {}
        self.released = []

    def holder(self, resource):
        return self._holders.get(resource)

    def holder_is_alive(self, resource):
        return self._alive.get(resource)

    def release_stale(self, resource):
        self.released.append(resource)
        self._holders.pop(resource, None)
        return True


def _install_fake_leases(monkeypatch, fake):
    """Make ``from tools.coordination import leases`` resolve to *fake*.

    BOTH bindings, and the second one is the one that matters. ``from PKG import
    NAME`` looks at ``getattr(PKG, NAME)`` FIRST and only falls back to
    ``sys.modules`` when the package carries no such attribute — so patching
    ``sys.modules`` alone works if and only if nothing has already imported the
    real submodule in this process. That made these tests pass ALONE and fail
    IN-SUITE behind any earlier test that touched ``tools.coordination.leases``:
    green in isolation, silently unpatched against the live lease table in a full
    run. Setting the package attribute as well makes the fake win in both orders.
    """
    import types

    import tools.coordination as pkg

    mod = types.ModuleType("tools.coordination.leases")
    mod.holder = fake.holder
    mod.holder_is_alive = fake.holder_is_alive
    mod.release_stale = fake.release_stale
    monkeypatch.setitem(sys.modules, "tools.coordination.leases", mod)
    monkeypatch.setattr(pkg, "leases", mod, raising=False)
    return mod


def _patch(monkeypatch, fake, heartbeating):
    """Route the module's lease import and heartbeat probe at the fakes."""
    _install_fake_leases(monkeypatch, fake)
    monkeypatch.setattr(k, "_task_is_heartbeating", lambda tid: heartbeating.get(tid, False))


# --------------------------------------------------------------------------- #
# 1. The stall, and the reap that ends it
# --------------------------------------------------------------------------- #
def test_a_lease_whose_holder_died_is_reaped_and_the_task_dispatches(monkeypatch):
    """THE defect. A one-shot script claimed the task and exited; nothing
    reclaimed the lease, so the task was blocked permanently."""
    res = "kanban:task:t-1"
    fake = _FakeLeases(holders={res: {"pid": 999}}, alive={res: False})
    _patch(monkeypatch, fake, heartbeating={})

    assert k._lease_blocks_dispatch("t-1") is False, "a dead holder must not block"
    assert fake.released == [res], "the stale lease must be reaped, not merely skipped"


def test_a_live_holder_blocks_and_is_not_reaped(monkeypatch):
    """Another worker owns it — skip, and never reclaim."""
    res = "kanban:task:t-2"
    fake = _FakeLeases(holders={res: {"pid": 1}}, alive={res: True})
    _patch(monkeypatch, fake, heartbeating={})

    assert k._lease_blocks_dispatch("t-2") is True
    assert fake.released == []


def test_an_unheld_task_never_blocks(monkeypatch):
    fake = _FakeLeases()
    _patch(monkeypatch, fake, heartbeating={})
    assert k._lease_blocks_dispatch("t-3") is False
    assert fake.released == []


# --------------------------------------------------------------------------- #
# 2. The correction: a dead pid is NOT enough on its own
# --------------------------------------------------------------------------- #
def test_a_dead_pid_does_not_reap_a_task_that_is_still_heartbeating(monkeypatch):
    """The bug the first draft of this fix shipped.

    The lease records the DISPATCHING pid, which exits after handing off. The
    worker keeps running under another pid and keeps heartbeating. Reaping here
    would let a second worker take the same task.
    """
    res = "kanban:task:t-4"
    fake = _FakeLeases(holders={res: {"pid": 999}}, alive={res: False})
    _patch(monkeypatch, fake, heartbeating={"t-4": True})

    assert k._lease_blocks_dispatch("t-4") is True, (
        "a heartbeating task must keep its lease even when the holding pid is gone"
    )
    assert fake.released == [], "reaping a live worker's lease causes duplicate work"


def test_an_unknown_liveness_answer_is_treated_as_alive(monkeypatch):
    """`holder_is_alive` returns None when it cannot tell (no psutil, an
    unreadable process table). Reclaiming on ignorance is the same duplicate-work
    hazard, so None must behave like True."""
    res = "kanban:task:t-5"
    fake = _FakeLeases(holders={res: {"pid": 7}}, alive={res: None})
    _patch(monkeypatch, fake, heartbeating={})

    assert k._lease_blocks_dispatch("t-5") is True
    assert fake.released == []


def test_a_lease_failure_never_wedges_dispatch(monkeypatch):
    """An unreadable lease store must cost the check, not the cycle."""
    import types

    mod = types.ModuleType("tools.coordination.leases")

    def _boom(*_a, **_k):
        raise RuntimeError("lease store unreadable")

    mod.holder = _boom
    mod.holder_is_alive = _boom
    mod.release_stale = _boom
    # Both bindings, for the reason _install_fake_leases documents: with only
    # sys.modules patched this assertion passes in-suite against the REAL lease
    # store, which returns None for an unheld id — the right answer for the
    # wrong reason, and no failure could ever surface it.
    import tools.coordination as pkg

    monkeypatch.setitem(sys.modules, "tools.coordination.leases", mod)
    monkeypatch.setattr(pkg, "leases", mod, raising=False)

    assert k._lease_blocks_dispatch("t-6") is False


# --------------------------------------------------------------------------- #
# 3. The heartbeat probe itself
# --------------------------------------------------------------------------- #
class _Row(dict):
    pass


class _Conn:
    def __init__(self, value, raises=False):
        self._value, self._raises = value, raises

    def execute(self, *_a, **_k):
        if self._raises:
            raise RuntimeError("db down")
        return self

    def fetchone(self):
        return None if self._value is _MISSING else _Row({"last_heartbeat_at": self._value})

    def close(self):
        return None


_MISSING = object()


def _hb(monkeypatch, value, raises=False):
    monkeypatch.setattr(k, "get_connection", lambda *a, **kw: _Conn(value, raises))
    return k._task_is_heartbeating("t")


def test_a_recent_heartbeat_reads_as_running(monkeypatch):
    recent = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    assert _hb(monkeypatch, recent) is True


def test_an_old_heartbeat_reads_as_not_running(monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    assert _hb(monkeypatch, old) is False


def test_a_task_that_never_started_has_no_heartbeat(monkeypatch):
    """The case this whole card is about: seeded, claimed, never dispatched."""
    assert _hb(monkeypatch, None) is False


def test_a_naive_timestamp_is_read_as_utc(monkeypatch):
    """PostgreSQL hands back a naive datetime for a timestamp column; treating
    it as local time would misjudge liveness by the UTC offset."""
    naive = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(tzinfo=None)
    assert _hb(monkeypatch, naive.isoformat()) is True


def test_an_unreadable_heartbeat_assumes_alive(monkeypatch):
    """Fail-safe: an unknown heartbeat must never license a reap."""
    assert _hb(monkeypatch, None, raises=True) is True


# --------------------------------------------------------------------------- #
# 4. The idle diagnosis must state a MEASURED cause, not a guess
# --------------------------------------------------------------------------- #
class _AdvConn:
    def __init__(self, ids):
        self._ids = ids

    def execute(self, *_a, **_k):
        return self

    def fetchall(self):
        return [{"id": i} for i in self._ids]


def _advisor(monkeypatch, fake):
    from tools.kanban import idle_advisor

    _install_fake_leases(monkeypatch, fake)
    return idle_advisor


def test_the_diagnosis_names_a_dead_lease_holder(monkeypatch):
    """The message used to assert "the usual cause is an open PR per task" and
    sent a reader to a merge queue that was already empty. It must say what it
    measured."""
    fake = _FakeLeases(
        holders={"kanban:task:t-dead-1": {"pid": 9}},
        alive={"kanban:task:t-dead-1": False},
    )
    adv = _advisor(monkeypatch, fake)

    # NOT "a"/"b" as ids: `"a" in text` is true of almost any English sentence,
    # so the id assertion passed without the id ever being named.
    text = adv._withhold_cause_clause(_AdvConn(["t-dead-1"]))
    assert "GONE" in text and "t-dead-1" in text
    assert "release_stale" in text, "it must name the remedy, not just the symptom"
    assert "usual cause" not in text


def test_the_diagnosis_names_a_live_claim(monkeypatch):
    fake = _FakeLeases(
        holders={"kanban:task:t-live-1": {"pid": 9}},
        alive={"kanban:task:t-live-1": True},
    )
    adv = _advisor(monkeypatch, fake)

    text = adv._withhold_cause_clause(_AdvConn(["t-live-1"]))
    assert "live session" in text and "t-live-1" in text


def test_with_no_lease_the_open_pr_guess_is_LABELLED_as_inferred(monkeypatch):
    """The open-PR explanation may still be right — it just was not measured
    here, and the old text presented it as fact."""
    adv = _advisor(monkeypatch, _FakeLeases())

    text = adv._withhold_cause_clause(_AdvConn(["c"]))
    assert "inferred rather than measured" in text


def test_an_unreadable_board_says_so_rather_than_guessing(monkeypatch):
    class _Boom:
        def execute(self, *_a, **_k):
            raise RuntimeError("db down")

    adv = _advisor(monkeypatch, _FakeLeases())
    assert "could not be measured" in adv._withhold_cause_clause(_Boom())


# --------------------------------------------------------------------------- #
# 5. The WIRING — the check must run inside the selection window
# --------------------------------------------------------------------------- #
#
# Everything above tests `_lease_blocks_dispatch` in isolation. None of it fails
# if the one line that CALLS it is deleted from `_drop_respawn_guarded`, and a
# guard nothing calls is the exact defect this repository ships most. These two
# pin the call site.
def _guarded(monkeypatch, fake, heartbeating=None):
    """`_drop_respawn_guarded` with its two OTHER guards stubbed to pass.

    The open-PR and recently-completed filters are already covered elsewhere;
    stubbing them isolates the third cause this card added.
    """
    _install_fake_leases(monkeypatch, fake)
    monkeypatch.setattr(k, "_task_is_heartbeating", lambda tid: (heartbeating or {}).get(tid, False))
    monkeypatch.setattr(k, "_tasks_with_recent_success", lambda ids: set())
    monkeypatch.setattr(k, "_open_pr_head_branches", lambda root: set())
    return k._drop_respawn_guarded


def test_the_selection_window_drops_a_lease_held_task(monkeypatch):
    """The call site. Without it every test above passes and the board stalls."""
    res = "kanban:task:t-held"
    fake = _FakeLeases(holders={res: {"pid": 1}}, alive={res: True})
    drop = _guarded(monkeypatch, fake)

    kept = drop([{"id": "t-held"}, {"id": "t-free"}])
    assert [t["id"] for t in kept] == ["t-free"], (
        "a task another session owns must yield its selection slot"
    )


def test_the_starvation_itself_the_filter_runs_BEFORE_the_cap(monkeypatch):
    """2026-08-20, reproduced.

    Three lease-held tasks sort highest, two dispatchable ones sit behind them,
    and there are three slots. Filtering before the truncation is the whole
    fix: `_drop_respawn_guarded` must hand back the two that can RUN, so the
    `[:available_slots]` cap in `_get_due_tasks` fills with real work instead of
    with three tasks that will each be skipped.

    All three holders are dead AND not heartbeating — the one-shot seeding
    scripts — so they are also reaped, which is what makes the block transient
    rather than permanent.
    """
    holders = {f"kanban:task:t-{i}": {"pid": 900 + i} for i in (1, 2, 3)}
    fake = _FakeLeases(holders=dict(holders), alive={r: False for r in holders})
    drop = _guarded(monkeypatch, fake)

    due = [{"id": f"t-{i}"} for i in (1, 2, 3)] + [{"id": "t-4"}, {"id": "t-5"}]
    available_slots = 3

    kept = drop(due)
    dispatched = kept[:available_slots]

    assert [t["id"] for t in dispatched] == ["t-1", "t-2", "t-3"], (
        "dead leases are reaped, so those three become dispatchable again"
    )
    assert sorted(fake.released) == sorted(holders), "all three stale leases reaped"

    # And with the holders ALIVE, the same window yields to the work behind them
    # rather than burning all three slots on tasks that cannot run.
    live = _FakeLeases(holders=dict(holders), alive={r: True for r in holders})
    kept = _guarded(monkeypatch, live)(due)
    assert [t["id"] for t in kept[:available_slots]] == ["t-4", "t-5"]
    assert live.released == []
