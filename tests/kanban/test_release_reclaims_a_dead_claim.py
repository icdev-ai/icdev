# CUI // SP-CTI
"""kpr-dup-08: ``--release`` could never match its own ``--claim``.

``leases.release`` matches on ``holder_session``, and ``get_session_id`` derives
a fresh ``local-<uuid>`` per PROCESS unless CLAUDE_SESSION_ID is exported. The
claim and the release are therefore two different processes with two different
identities, and the match can never succeed — whether the claim came from
``cli.py --claim`` (acquire, exit) or from ``create_tasks(specs, claim=True)``
(acquire inside whatever short-lived process ran the seeder).

So ``--release`` printed "NOT HELD BY THIS SESSION" and the only way out was the
TTL. Observed 2026-08-18 on kpr-fix-03: the work had merged, the row was ``done``,
and the claim was still held by pid 28076 — long exited — withholding a finished
task from the runner for the rest of its 4-hour lease.

It is the SAME defect ``--pause-runner`` had. That one was found and fixed with
``leases.release_stale``; this one was left, while CLAUDE.md documents
``--release`` as the way to hand a task back. The fix is the existing remedy
applied to the second caller, not a new mechanism.

THE LINE THAT MUST NOT MOVE: a claim whose holder is still RUNNING is left
alone. Stealing it is exactly the duplicate-build race the claim exists to
prevent, so the tests assert the negative as hard as the positive.

AND A DEAD PID IS NOT DEAD WORK (autonomy-adm-03). ``release_stale`` reads only
the pid, and the pid on a runner's lease is the dispatcher's, which exits after
handing off while the worker heartbeats on under another pid. The fallback now
asks the shared two-signal verdict (``tools.kanban.lease_liveness``) and
refuses, with the reason, while the task is heartbeating.
"""
from __future__ import annotations

import json

import pytest

import tools.coordination.leases as leases
from tools.kanban import cli
from tools.kanban import lease_liveness as ll


@pytest.fixture
def resource():
    return cli._task_lease_resource("kpr-dup-08")


_DERIVE = object()   # pid_alive follows the holder: ALIVE -> True, DEAD -> False


def _stub(monkeypatch, *, release_ok, stale_ok, holder_before, holder_after,
          pid_alive=_DERIVE, heartbeating=False):
    """Script the lease layer. ``holder`` answers *holder_before* until the
    stale reclaim has run, then *holder_after* — the verdict reads the holder
    too, so a fixed two-read sequence would hand it the AFTER state."""
    calls = {"release": 0, "release_stale": 0}
    state = {"reclaimed": False}

    def _release(r):
        calls["release"] += 1
        return release_ok

    def _release_stale(r):
        calls["release_stale"] += 1
        state["reclaimed"] = True
        return stale_ok

    monkeypatch.setattr(leases, "release", _release)
    monkeypatch.setattr(leases, "release_stale", _release_stale)
    monkeypatch.setattr(leases, "holder",
                        lambda r: holder_after if state["reclaimed"] else holder_before)
    if pid_alive is _DERIVE:
        pid_alive = (holder_before is ALIVE) if holder_before is not None else None
    monkeypatch.setattr(leases, "holder_is_alive", lambda r: pid_alive)
    monkeypatch.setattr(ll, "task_is_heartbeating", lambda tid: heartbeating)
    return calls


DEAD = {"holder_session": "local-4babee2f3e5d", "pid": 28076}
ALIVE = {"holder_session": "local-other", "pid": 999}


def test_a_claim_from_an_exited_process_is_reclaimed(monkeypatch, capsys):
    """The whole bug. Every real claim looks like this, because the claiming
    process has always exited by the time anyone releases."""
    calls = _stub(monkeypatch, release_ok=False, stale_ok=True,
                  holder_before=DEAD, holder_after=None)
    rc = cli.cmd_release("kpr-dup-08", json_out=False)
    assert rc == 0
    assert calls["release_stale"] == 1, "the stale fallback must be attempted"
    out = capsys.readouterr().out
    assert "RELEASED" in out
    assert "28076" in out, "say which dead holder was reclaimed, not just that it worked"


def test_a_LIVE_holder_is_never_stolen(monkeypatch, capsys):
    """The safety property. Another live session is building that task; taking
    its claim would recreate the duplicate-build race the claim prevents."""
    calls = _stub(monkeypatch, release_ok=False, stale_ok=False,
                  holder_before=ALIVE, holder_after=ALIVE)
    rc = cli.cmd_release("kpr-dup-08", json_out=False)
    assert rc == 1, "a live claim must be a refusal, not a success"
    assert calls["release_stale"] == 0, (
        "the verdict refuses BEFORE the lease layer is asked to reap")
    out = capsys.readouterr().out
    assert "STILL CLAIMED" in out and "LIVE" in out
    assert "local-other" in out


def test_a_dead_pid_with_a_HEARTBEATING_task_is_never_stolen(monkeypatch, capsys):
    """autonomy-adm-03. The dispatcher's pid is gone; the worker is building
    the task right now. The old fallback reclaimed this on request."""
    calls = _stub(monkeypatch, release_ok=False, stale_ok=True,
                  holder_before=DEAD, holder_after=None, heartbeating=True)
    rc = cli.cmd_release("kpr-dup-08", json_out=False)
    assert rc == 1, "a heartbeating task is live work — refusal, not a reclaim"
    assert calls["release_stale"] == 0, "release_stale must not even be attempted"
    out = capsys.readouterr().out
    assert "STILL CLAIMED" in out and "HEARTBEATING" in out
    assert "28076" in out, "say which pid is gone, and why that is not enough"


def test_an_unknowable_pid_is_treated_as_alive(monkeypatch, capsys):
    """holder_is_alive() is None — no psutil, no pid on the lease. Reaping on
    an unknown is how a live worker loses its lease."""
    calls = _stub(monkeypatch, release_ok=False, stale_ok=True,
                  holder_before=DEAD, holder_after=None, pid_alive=None)
    assert cli.cmd_release("kpr-dup-08", json_out=False) == 1
    assert calls["release_stale"] == 0
    assert "STILL CLAIMED" in capsys.readouterr().out


def test_the_own_session_path_still_works_without_the_fallback(monkeypatch):
    """When the claim really was taken by this process, nothing stale is touched."""
    calls = _stub(monkeypatch, release_ok=True, stale_ok=False,
                  holder_before=DEAD, holder_after=None)
    assert cli.cmd_release("kpr-dup-08", json_out=False) == 0
    assert calls["release_stale"] == 0, (
        "release_stale must not run when the ordinary release succeeded")


def test_an_unclaimed_task_says_so(monkeypatch, capsys):
    """Distinct from both other outcomes: nothing to release is not a live hold
    and not a reclaim."""
    _stub(monkeypatch, release_ok=False, stale_ok=False,
          holder_before=None, holder_after=None)
    assert cli.cmd_release("kpr-dup-08", json_out=False) == 1
    assert "NOT CLAIMED" in capsys.readouterr().out


def test_json_reports_which_of_the_two_paths_ran(monkeypatch, capsys):
    """A caller must be able to tell a reclaim from an ordinary release —
    reclaiming means somebody's claim outlived its process."""
    _stub(monkeypatch, release_ok=False, stale_ok=True,
          holder_before=DEAD, holder_after=None)
    cli.cmd_release("kpr-dup-08", json_out=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["released"] is True
    assert payload["reclaimed_from_exited_session"] is True
    assert payload["prior_holder"] == "local-4babee2f3e5d"
    assert payload["still_held_by"] is None
    assert payload["lease_state"] == "litter"
    assert payload["worker_heartbeating"] is False


def test_json_reports_a_heartbeating_refusal_as_such(monkeypatch, capsys):
    _stub(monkeypatch, release_ok=False, stale_ok=True,
          holder_before=DEAD, holder_after=DEAD, heartbeating=True)
    cli.cmd_release("kpr-dup-08", json_out=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["released"] is False
    assert payload["lease_state"] == "working"
    assert payload["worker_heartbeating"] is True


def test_the_resource_name_is_the_shared_one():
    """Same seam the seeder and the runner use, or the release frees nothing."""
    assert cli._task_lease_resource("kpr-dup-08") == "kanban:task:kpr-dup-08"


def test_the_fallback_asks_the_shared_verdict_not_the_pid_alone():
    """Pins the consolidation: cmd_release must reach release_stale ONLY via
    lease_liveness.reap_if_litter, never call it directly."""
    import inspect

    src = inspect.getsource(cli.cmd_release)
    body = src.split('"""')[2]          # after the docstring, which names the old path
    assert "reap_if_litter" in body
    assert "release_stale" not in body


def test_release_stale_refuses_a_live_holder_at_the_lease_layer():
    """Pins the guarantee cmd_release leans on, at the layer that provides it:
    the fallback is only safe because release_stale checks liveness itself."""
    import inspect

    src = inspect.getsource(leases.release_stale)
    assert "holder_is_alive" in src
    assert "is not False" in src, (
        "release_stale must refuse when the holder is alive OR unknowable")
