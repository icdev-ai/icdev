# CUI // SP-CTI
"""Lease liveness comes from the heartbeat, never a PID alone (autonomy-adm-03).

The ``kanban:task:<id>`` lease records the DISPATCHING pid, which exits as soon
as it has handed off to the worker. Judging liveness from that pid gives both
errors at once: a dead pid reads as dead work (a live worker's lease is reaped
and the task is dispatched twice), and a reused pid reads as live work (a stale
lease is never reaped and starves the queue). rem-hyg-15 fixed the dispatch
window by adding the heartbeat as a second signal; this module is where that
answer now lives, so every reader asks the SAME question.

These tests pin the verdict matrix, the two fail-safes (unknown is alive; an
unreadable heartbeat is alive), and — separately — that every reader of the
lease actually goes through the shared seam rather than keeping an opinion.
"""
from __future__ import annotations

import inspect
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.kanban import lease_liveness as ll  # noqa: E402


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
class _FakeLeases:
    def __init__(self, holder=None, alive=None):
        self._holder, self._alive = holder, alive
        self.released = []

    def holder(self, resource):
        return self._holder

    def holder_is_alive(self, resource):
        return self._alive

    def release_stale(self, resource):
        self.released.append(resource)
        self._holder = None
        return True


def _install(monkeypatch, fake, heartbeating=None):
    """Both bindings — `from PKG import NAME` reads the package attribute first,
    so patching sys.modules alone is ignored in-suite once the real submodule
    has been imported by an earlier test."""
    import tools.coordination as pkg

    mod = types.ModuleType("tools.coordination.leases")
    mod.holder = fake.holder
    mod.holder_is_alive = fake.holder_is_alive
    mod.release_stale = fake.release_stale
    monkeypatch.setitem(sys.modules, "tools.coordination.leases", mod)
    monkeypatch.setattr(pkg, "leases", mod, raising=False)
    probes = {"calls": 0}

    def _hb(tid):
        probes["calls"] += 1
        return bool(heartbeating)

    monkeypatch.setattr(ll, "task_is_heartbeating", _hb)
    return probes


HOLDER = {"holder_session": "local-abc", "pid": 4242}


# --------------------------------------------------------------------------- #
# 1. the verdict matrix
# --------------------------------------------------------------------------- #
def test_no_lease_is_free(monkeypatch):
    _install(monkeypatch, _FakeLeases(holder=None))
    v = ll.task_lease_verdict("t")
    assert v.state == ll.STATE_FREE
    assert v.reapable is False and v.blocks_dispatch is False
    assert v.heartbeating is None, "not consulted — must not read as 'not beating'"


def test_live_pid_is_live_and_the_heartbeat_is_not_consulted(monkeypatch):
    probes = _install(monkeypatch, _FakeLeases(holder=HOLDER, alive=True), heartbeating=False)
    v = ll.task_lease_verdict("t")
    assert v.state == ll.STATE_LIVE
    assert v.blocks_dispatch is True and v.reapable is False
    assert v.pid_alive is True and v.heartbeating is None
    assert probes["calls"] == 0, "a live pid settles it; the board is not queried"


def test_dead_pid_with_heartbeat_is_WORKING_not_litter(monkeypatch):
    """THE defect. The dispatcher's pid died; the worker heartbeats on."""
    _install(monkeypatch, _FakeLeases(holder=HOLDER, alive=False), heartbeating=True)
    v = ll.task_lease_verdict("t")
    assert v.state == ll.STATE_WORKING
    assert v.blocks_dispatch is True
    assert v.reapable is False, "a dead pid is not dead work"
    assert v.pid_alive is False and v.heartbeating is True


def test_dead_pid_without_heartbeat_is_litter(monkeypatch):
    """The one-shot claim whose process exited: the only reapable state."""
    _install(monkeypatch, _FakeLeases(holder=HOLDER, alive=False), heartbeating=False)
    v = ll.task_lease_verdict("t")
    assert v.state == ll.STATE_LITTER
    assert v.reapable is True and v.blocks_dispatch is False


def test_unknown_pid_liveness_is_ALIVE(monkeypatch):
    """holder_is_alive() -> None means CANNOT TELL. Cannot-tell is alive: the
    heartbeat is not even consulted, because no answer from it could make an
    unknown holder reapable."""
    probes = _install(monkeypatch, _FakeLeases(holder=HOLDER, alive=None), heartbeating=False)
    v = ll.task_lease_verdict("t")
    assert v.state == ll.STATE_LIVE
    assert v.reapable is False and v.blocks_dispatch is True
    assert v.pid_alive is None
    assert probes["calls"] == 0


def test_the_state_vocabulary_is_closed():
    assert set(ll.STATES) == {"free", "live", "working", "litter"}
    assert [s for s in ll.STATES if ll.LeaseVerdict("t", "r", s, None, None, None).reapable] == ["litter"]


# --------------------------------------------------------------------------- #
# 2. the reaper acts on litter and NOTHING else
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("alive,beating", [(True, False), (None, False), (False, True)])
def test_reap_if_litter_leaves_every_non_litter_lease_alone(monkeypatch, alive, beating):
    fake = _FakeLeases(holder=HOLDER, alive=alive)
    _install(monkeypatch, fake, heartbeating=beating)
    verdict, reaped = ll.reap_if_litter("t")
    assert reaped is False
    assert fake.released == []
    assert verdict.state != ll.STATE_LITTER


def test_reap_if_litter_reaps_litter(monkeypatch):
    fake = _FakeLeases(holder=HOLDER, alive=False)
    _install(monkeypatch, fake, heartbeating=False)
    verdict, reaped = ll.reap_if_litter("t")
    assert reaped is True
    assert fake.released == ["kanban:task:t"]
    assert verdict.state == ll.STATE_LITTER


def test_reap_if_litter_on_a_free_resource_is_a_no_op(monkeypatch):
    fake = _FakeLeases(holder=None)
    _install(monkeypatch, fake)
    verdict, reaped = ll.reap_if_litter("t")
    assert (verdict.state, reaped, fake.released) == (ll.STATE_FREE, False, [])


# --------------------------------------------------------------------------- #
# 3. the heartbeat probe
# --------------------------------------------------------------------------- #
class _Conn:
    def __init__(self, value, raises=False):
        self._value, self._raises = value, raises

    def execute(self, *_a, **_k):
        if self._raises:
            raise RuntimeError("db down")
        return self

    def fetchone(self):
        return None if self._value is _MISSING else {"last_heartbeat_at": self._value}

    def close(self):
        return None


_MISSING = object()


def _hb(monkeypatch, value, raises=False):
    monkeypatch.setattr(ll, "get_connection", lambda *a, **kw: _Conn(value, raises))
    return ll.task_is_heartbeating("t")


def test_recent_heartbeat_is_beating(monkeypatch):
    assert _hb(monkeypatch, (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()) is True


def test_heartbeat_older_than_the_window_is_not(monkeypatch):
    old = datetime.now(timezone.utc) - timedelta(minutes=ll.HEARTBEAT_LIVE_MINUTES + 1)
    assert _hb(monkeypatch, old.isoformat()) is False


def test_a_zulu_suffixed_stamp_is_parsed(monkeypatch):
    """`_refresh_running_heartbeats` writes `_utcnow_iso()`; a `Z` suffix must
    not raise (which would fail-safe to True and mask a real silence)."""
    stamp = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _hb(monkeypatch, stamp) is True
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _hb(monkeypatch, old) is False


def test_no_row_and_null_heartbeat_are_not_beating(monkeypatch):
    assert _hb(monkeypatch, _MISSING) is False
    assert _hb(monkeypatch, None) is False


def test_an_unreadable_heartbeat_is_beating(monkeypatch):
    """Fail-safe: an unknown heartbeat must never license a reap."""
    assert _hb(monkeypatch, None, raises=True) is True


# --------------------------------------------------------------------------- #
# 4. consolidation — every reader goes through the seam
# --------------------------------------------------------------------------- #
def test_the_reflex_consumes_the_shared_probe_rather_than_a_copy():
    from tools.genesis.reflexes import kanban as k

    assert k._task_is_heartbeating is ll.task_is_heartbeating
    assert k._HEARTBEAT_LIVE_MINUTES == ll.HEARTBEAT_LIVE_MINUTES
    src = inspect.getsource(k._lease_blocks_dispatch)
    assert "reap_if_litter" in src
    assert "holder_is_alive" not in src.split('"""')[2], (
        "the reflex must not ask the pid itself outside its docstring")


def test_no_task_lease_reader_asks_holder_is_alive_on_its_own():
    """The pin for the whole card. `holder_is_alive` is a pid-level primitive;
    every TASK-lease reader must go through `lease_liveness`, or a dead pid is
    dead work again somewhere."""
    import importlib

    # Resolved by dotted path so the pin names the exact module objects the
    # consumers live in — `tools.x` and `icdev.tools.x` are distinct objects.
    k = importlib.import_module("tools.genesis.reflexes.kanban")
    cli = importlib.import_module("tools.kanban.cli")
    idle_advisor = importlib.import_module("tools.kanban.idle_advisor")
    startup_recovery = importlib.import_module("tools.kanban.startup_recovery")

    for fn in (k._lease_blocks_dispatch, cli.cmd_release,
               idle_advisor._withhold_cause_clause, startup_recovery._lease_holder_pid):
        body = inspect.getsource(fn)
        code = body.split('"""')[2] if body.count('"""') >= 2 else body
        assert "holder_is_alive" not in code, f"{fn.__qualname__} reads the pid alone"
        assert "lease_liveness" in code, f"{fn.__qualname__} bypasses the shared seam"


def test_describe_names_the_state_and_the_reason():
    v = ll.LeaseVerdict("t", "kanban:task:t", ll.STATE_WORKING, HOLDER, False, True)
    text = ll.describe(v)
    assert "4242" in text and "heartbeat" in text and "not litter" in text
    v = ll.LeaseVerdict("t", "kanban:task:t", ll.STATE_LIVE, HOLDER, None, None)
    assert "cannot tell" in ll.describe(v) and "LIVE" in ll.describe(v)
