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
"""
from __future__ import annotations

import json

import pytest

import tools.coordination.leases as leases
from tools.kanban import cli


@pytest.fixture
def resource():
    return cli._task_lease_resource("kpr-dup-08")


def _stub(monkeypatch, *, release_ok, stale_ok, holder_before, holder_after):
    calls = {"release": 0, "release_stale": 0}
    seq = iter([holder_before, holder_after])

    monkeypatch.setattr(leases, "release", lambda r: (
        calls.__setitem__("release", calls["release"] + 1) or release_ok))
    monkeypatch.setattr(leases, "release_stale", lambda r: (
        calls.__setitem__("release_stale", calls["release_stale"] + 1) or stale_ok))
    monkeypatch.setattr(leases, "holder", lambda r: next(seq, holder_after))
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
    assert calls["release_stale"] == 1
    out = capsys.readouterr().out
    assert "STILL CLAIMED" in out and "LIVE" in out
    assert "local-other" in out


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


def test_the_resource_name_is_the_shared_one():
    """Same seam the seeder and the runner use, or the release frees nothing."""
    assert cli._task_lease_resource("kpr-dup-08") == "kanban:task:kpr-dup-08"


def test_release_stale_refuses_a_live_holder_at_the_lease_layer():
    """Pins the guarantee cmd_release leans on, at the layer that provides it:
    the fallback is only safe because release_stale checks liveness itself."""
    import inspect

    src = inspect.getsource(leases.release_stale)
    assert "holder_is_alive" in src
    assert "is not False" in src, (
        "release_stale must refuse when the holder is alive OR unknowable")
