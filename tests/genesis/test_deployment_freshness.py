# CUI // SP-CTI
"""Can this deployment still update itself, and if not WHY? (autonomy-dep-03)

THE INCIDENT. Measured 2026-08-21, `C:/AI/ICDev` was 22 commits behind
origin/main and had stopped updating entirely. 170 files incoming, 11 locally
modified, EXACTLY ONE overlap: `args/projects.yaml`. `pull_if_safe` refused —
correctly, since pulling over a modified file destroys work on a shared
checkout — and nothing read the refusal.

It never cleared because that file is AUTO-MANAGED: `kanban_project_sync.py`
rewrites it in the working tree, and every card registration edits it upstream.
A reflex dirties the local side continuously while merges touch the incoming
side constantly, so a correct, transient refusal became a permanent freeze.

The cost was invisible: autonomy-id-01 recorded nothing even after its migration
was applied, because the RUNNING code had no `boot_identity` call; and
`code_staleness` could not report that, because it needs an identity row only
current code writes. Every board, PR and CI signal stayed green.

THE TWO THINGS PINNED HERE:
  1. `blocked` and `updatable` stay apart. Behind-and-refusing is a stopped
     deployment; behind-and-would-pull is a normal window between poll cycles.
     Merging them makes the alarm fire constantly and get ignored.
  2. `unmeasurable` never reads as `blocked` OR `current`. This module shipped a
     bug in exactly that spot — an unreachable ref made `behind_by` return None,
     `None != 0` fell through, and it raised a FALSE FREEZE ALARM.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis import deployment_freshness as df  # noqa: E402


def _probe(reason, conflicts=None, **extra):
    def _p(_root):
        out = {"pulled": False, "reason": reason}
        if conflicts:
            out["conflicts"] = conflicts
        out.update(extra)
        return out
    return _p


def _behind(n):
    class _R:
        returncode = 0 if n is not None else 1
        stdout = "" if n is None else str(n)

    return lambda *_a, **_k: _R()


# --------------------------------------------------------------------------- #
# 1. The finding
# --------------------------------------------------------------------------- #
def test_behind_and_refusing_is_blocked_and_names_the_file():
    """The live incident. A reason without the offending file cannot be acted
    on — the whole repair is "deal with THIS file"."""
    report = df.freshness(root="/x", runner=_behind(22),
                          probe=_probe("local changes would be lost",
                                       conflicts=["args/projects.yaml"]))
    assert report["state"] == df.BLOCKED
    assert report["behind_by"] == 22
    assert report["conflicts"] == ["args/projects.yaml"]


def test_the_render_refuses_to_recommend_force_pulling():
    """The guard is RIGHT. Trading a stalled update for silent data loss on a
    shared checkout is not a fix, and the advice a human reads must say so."""
    text = df.render(df.freshness(root="/x", runner=_behind(22),
                                  probe=_probe("local changes would be lost",
                                               conflicts=["args/projects.yaml"])))
    assert "Do NOT force-pull" in text
    assert "args/projects.yaml" in text


# --------------------------------------------------------------------------- #
# 2. blocked vs updatable — different repairs, kept apart
# --------------------------------------------------------------------------- #
def test_behind_but_would_pull_is_updatable_not_blocked():
    """A normal window between poll cycles. Calling this blocked would fire the
    alarm on every healthy deployment and teach everyone to ignore it."""
    report = df.freshness(root="/x", runner=_behind(3),
                          probe=_probe("would pull", incoming=3))
    assert report["state"] == df.UPDATABLE


def test_throttled_is_updatable_not_blocked():
    """`throttled` cannot arise through this module (a dry run does not
    throttle), but a caller passing its own probe must still classify it as the
    transient thing it is."""
    report = df.freshness(root="/x", runner=_behind(3), probe=_probe("throttled"))
    assert report["state"] == df.UPDATABLE


def test_nothing_incoming_is_current():
    report = df.freshness(root="/x", runner=_behind(0),
                          probe=_probe("already current"))
    assert report["state"] == df.CURRENT
    assert report["behind_by"] == 0


def test_refusing_with_nothing_behind_is_not_a_freeze():
    """A checkout on a feature branch refuses, and should — somebody is working
    on it deliberately. Nothing is waiting, so nothing is stuck."""
    report = df.freshness(root="/x", runner=_behind(0),
                          probe=_probe("not on main (on feat/x)"))
    assert report["state"] == df.CURRENT


# --------------------------------------------------------------------------- #
# 3. Unmeasurable — the bug this module actually shipped
# --------------------------------------------------------------------------- #
def test_an_unmeasurable_distance_is_not_a_false_freeze_alarm():
    """THE regression. An unreachable ref made `behind_by` return None; `None
    != 0` fell through the ladder and reported BLOCKED — a stopped deployment,
    announced, on a checkout that was fine."""
    report = df.freshness(root="/x", runner=_behind(None),
                          probe=_probe("not on main (on detached)"))
    assert report["state"] == df.UNMEASURABLE, (
        "a distance nobody could measure was reported as a frozen deployment"
    )
    assert report["behind_by"] is None


def test_a_guard_that_cannot_be_asked_is_unmeasurable():
    def _boom(_root):
        raise RuntimeError("code_reload exploded")

    report = df.freshness(root="/x", runner=_behind(5), probe=_boom)
    assert report["state"] == df.UNMEASURABLE


def test_behind_by_is_none_not_zero_when_git_fails():
    """A checkout whose remote cannot be read is not a checkout that is up to
    date, and 0 is exactly that reassurance."""
    assert df.behind_by(runner=_behind(None)) is None


def test_behind_by_handles_garbage_output():
    class _R:
        returncode = 0
        stdout = "not a number"

    assert df.behind_by(runner=lambda *_a, **_k: _R()) is None


# --------------------------------------------------------------------------- #
# 4. It ASKS the guard, it does not re-derive it
# --------------------------------------------------------------------------- #
def test_it_calls_pull_if_safe_rather_than_reimplementing_the_ladder():
    """A reporter with its own copy of the predicate describes an updater the
    deployment does not have — the deps.py lesson. The refusal ladder stays in
    one function; this asks it with dry_run=True."""
    import inspect

    src = inspect.getsource(df.freshness)
    assert "pull_if_safe" in src and "dry_run=True" in src
    # None of the guard's own checks may be re-expressed here.
    whole = inspect.getsource(df)
    for owned_by_the_guard in ("status --porcelain", "merge --ff-only",
                               "diff --name-only"):
        assert owned_by_the_guard not in whole, (
            f"the reporter re-derived {owned_by_the_guard!r}, which belongs to "
            f"pull_if_safe"
        )


def test_it_never_mutates_the_checkout():
    import inspect

    src = inspect.getsource(df)
    for mutating in ("merge", "checkout", "reset", "clean", "stash"):
        assert f'"{mutating}"' not in src, f"the reporter reached for git {mutating}"


# --------------------------------------------------------------------------- #
# 5. The dry run must cost the real updater nothing
# --------------------------------------------------------------------------- #
def test_a_dry_run_does_not_consume_the_pull_throttle():
    """If asking spent the throttle window, the reporter would starve the very
    updater it exists to describe — and the deployment would fall behind
    BECAUSE something was watching it."""
    from tools.genesis import code_reload

    calls = []

    def _runner(args, **_kw):
        calls.append(args)

        class _R:
            returncode = 0
            stdout = "main" if args[:1] == ["rev-parse"] else ""

        return _R()

    before = code_reload._last_pull
    code_reload.pull_if_safe(runner=_runner, dry_run=True)
    assert code_reload._last_pull == before, (
        "a dry run advanced the throttle clock and would delay the next real pull"
    )


def test_a_dry_run_never_merges():
    from tools.genesis import code_reload

    calls = []

    def _runner(args, **_kw):
        calls.append(list(args))

        class _R:
            returncode = 0
            # Report a branch, an incoming file, and a clean tree so the guard
            # reaches the point where it WOULD merge.
            stdout = ("main" if args[:1] == ["rev-parse"]
                      else "tools/x.py" if args[:2] == ["diff", "--name-only"]
                      else "")

        return _R()

    result = code_reload.pull_if_safe(runner=_runner, dry_run=True,
                                      min_interval=0)
    assert result["pulled"] is False
    assert result.get("dry_run") is True
    assert not any(a[:1] == ["merge"] for a in calls), (
        f"a dry run performed a merge: {calls}"
    )


@pytest.mark.parametrize("state,gate,expected", [
    (df.CURRENT, True, 0),
    (df.UPDATABLE, True, 0),
    (df.BLOCKED, True, 1),
    (df.BLOCKED, False, 0),
    (df.UNMEASURABLE, False, 2),
])
def test_exit_codes(monkeypatch, capsys, state, gate, expected):
    monkeypatch.setattr(df, "freshness", lambda **_k: {
        "state": state, "behind_by": 1, "reason": "", "conflicts": [],
        "root": "/x", "ref": "origin/main"})
    assert df.main(["--gate"] if gate else []) == expected
    capsys.readouterr()


# --------------------------------------------------------------------------- #
# The count is measured AFTER the guard has fetched (autonomy-dep-04)
# --------------------------------------------------------------------------- #
def test_behind_by_is_measured_after_the_probe_fetches():
    """The guard fetches before it answers; `rev-list HEAD..origin/main` reads
    the ref on disk. Measured the other way round, a checkout whose ref had not
    been fetched since the last poll read 0 behind while the guard refused on a
    locally-modified incoming file, and 0-and-refusing is reported `current`:
    a frozen deployment with a clean bill of health."""
    fetched = {"done": False}

    class _R:
        returncode = 0

        @property
        def stdout(self):
            return "4" if fetched["done"] else "0"

    def runner(*_a, **_k):
        return _R()

    def probe(_root):
        fetched["done"] = True               # the real guard's `git fetch`
        return {"pulled": False, "reason": "local changes would be lost",
                "conflicts": ["args/projects.yaml"]}

    rep = df.freshness(root="/deploy", runner=runner, probe=probe)
    assert rep["state"] == df.BLOCKED
    assert rep["behind_by"] == 4
    assert rep["conflicts"] == ["args/projects.yaml"]
