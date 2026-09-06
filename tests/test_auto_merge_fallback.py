"""The merge door must be able to merge (kpr-rvfy-08).

`_auto_merge` ran `gh pr merge --squash --auto`. `--auto` asks GitHub to merge
when checks pass, and it requires the REPOSITORY to have auto-merge enabled.

Since mfx-mrg-01 the method is `--merge` (a merge commit), never `--squash`:
a squash leaves the branch AHEAD of main by ancestry and collapses any sibling
stacked on it. `MERGE_METHOD_FLAG` spells the method once; both attempts here
must carry it and neither may carry `--squash`. The git semantics of the flag
are replayed in tests/kanban/test_merge_commit_landing.py.

    MEASURED 2026-08-30:  allow_auto_merge == false on BOTH icdev and icdev_ft.

Auto-merge needs branch protection, which this plan does not offer on a private
repo -- the protection API answers 403. So the call could never succeed on
either parent, `merge_requested` failed on every land, and the sanctioned door
was structurally incapable of merging anything: twelve gates green, the
thirteenth impossible.

That is the whole reason agents and humans alike fell back to a raw
`gh pr merge`, which runs NONE of the thirteen checks. It was never carelessness
-- the door had never once worked. This was the third of three independent
defects in that chain, after the pytest gate stripping PYTHONPATH (kpr-rvfy-06)
and the sibling check listing the wrong repository (kpr-rvfy-07).
"""
from __future__ import annotations

import subprocess

import pytest

from tools.ci.pr_watcher import MERGE_METHOD_FLAG, PRWatcher

PR = "https://github.com/icdev-ai/icdev_ft/pull/322"


class Runner:
    """Records every gh invocation; fails the ones whose argv contains a
    member of ``fail_when``."""

    def __init__(self, fail_when=("--auto",), stderr="auto-merge is not allowed"):
        self.fail_when, self.stderr, self.calls = fail_when, stderr, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        bad = any(f in cmd for f in self.fail_when)
        return subprocess.CompletedProcess(
            cmd, 1 if bad else 0, stdout="", stderr=self.stderr if bad else "")


def _watcher(runner, *, enabled=True):
    w = PRWatcher.__new__(PRWatcher)          # no __init__: a pure-seam test
    w._auto_merge_runner = runner
    w.config = {"auto_merge_enabled": enabled}
    w.dry_run = False                         # a dry run returns True before any gh call
    return w


def test_it_falls_back_to_a_plain_merge_when_auto_is_refused():
    """THE DEFECT. A repo without auto-merge refused the only call there was."""
    r = Runner(fail_when=("--auto",))
    assert _watcher(r)._auto_merge(PR) is True
    assert len(r.calls) == 2, "it must retry without --auto"
    assert "--auto" in r.calls[0] and "--auto" not in r.calls[1]
    assert MERGE_METHOD_FLAG in r.calls[1], "the fallback keeps the merge method"
    assert "--squash" not in r.calls[1]


def test_a_repo_that_allows_auto_merge_still_uses_it():
    """Nothing changes for a deployment where --auto works: one call, done."""
    r = Runner(fail_when=())
    assert _watcher(r)._auto_merge(PR) is True
    assert len(r.calls) == 1 and "--auto" in r.calls[0]
    assert MERGE_METHOD_FLAG in r.calls[0] and "--squash" not in r.calls[0]


def test_a_genuinely_unmergeable_pr_still_fails():
    """The fallback must not launder a real refusal into a merge. Both attempts
    fail -> False, and the reason is logged rather than swallowed."""
    r = Runner(fail_when=(MERGE_METHOD_FLAG,), stderr="not mergeable: CONFLICTING")
    assert _watcher(r)._auto_merge(PR) is False
    assert len(r.calls) == 2


def test_auto_merge_disabled_in_config_makes_no_call_at_all():
    """The operator's own switch is upstream of all of this."""
    r = Runner()
    assert _watcher(r, enabled=False)._auto_merge(PR) is False
    assert r.calls == []


@pytest.mark.parametrize("failing", [("--auto",), ()])
def test_the_url_is_always_passed_explicitly(failing):
    """gh resolves a bare `pr merge` against the CURRENT repo; an external-repo
    PR must be named by url or the merge lands somewhere else entirely."""
    r = Runner(fail_when=failing)
    _watcher(r)._auto_merge(PR)
    for cmd in r.calls:
        assert PR in cmd


def test_the_method_is_a_merge_commit_never_a_squash():
    """mfx-mrg-01. A squash leaves `origin/main..kanban/<id>` non-empty (51 of
    the 60 linked branches surveyed on 2026-09-04) and collapses a stacked
    sibling; a merge commit does neither. Pinned on the constant AND on every
    argv it produces, so a second call site cannot respell it."""
    assert MERGE_METHOD_FLAG == "--merge"
    r = Runner(fail_when=("--auto",))
    _watcher(r)._auto_merge(PR)
    for cmd in r.calls:
        assert "--merge" in cmd and "--squash" not in cmd
