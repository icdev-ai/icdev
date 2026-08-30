"""The protected-path guard must look in the PR's own repository (kpr-rvfy-09).

`_protected_hits` asked `_open_pr_index()` with no repo, so it listed whichever
repository the process was standing in. An ICDEV[FT] PR is never in that
listing, and the guard's fail-closed rule -- correct and deliberate -- then
reads a PR's own ABSENCE as "protected":

    a PR absent from the index is treated as protected, not as clean

So every external-repo PR was refused forever, by a guard whose entire protected
list is ICDEV[IT] files that such a PR cannot touch. Measured 2026-08-30 on
icdev_ft#323: twelve gates green, `merge_requested` refused, and the reason was
invisible because the refusal is logged rather than returned.

THE FAIL-CLOSED RULE STAYS. It is the one guard between a defective merge ladder
and its own unreviewed merge, and these tests pin it: an unreadable listing is
still protected. What changes is only WHICH repository is asked, so the guard
gets a real answer instead of a structural blank.

Twin of kpr-rvfy-07 (the sibling map), one call site over.
"""
from __future__ import annotations

import json
import subprocess

from tools.ci.pr_watcher import PRWatcher

FT = "https://github.com/icdev-ai/icdev_ft/pull/323"
IT = "https://github.com/icdev-ai/icdev/pull/1988"


class Runner:
    """Answers only for the repo it is told about, like gh does."""

    def __init__(self, by_repo):
        self.by_repo, self.calls = by_repo, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        repo = cmd[cmd.index("--repo") + 1] if "--repo" in cmd else None
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(self.by_repo.get(repo, [])), stderr="")


def _watcher(runner, paths=("tools/ci/pr_watcher.py",)):
    w = PRWatcher.__new__(PRWatcher)          # no __init__: a pure-seam test
    w._pr_list_runner = runner
    w._protected_paths = lambda: list(paths)
    return w


def _entry(url, *files):
    return {"url": url, "files": [{"path": f} for f in files],
            "mergeable": "MERGEABLE", "isDraft": False}


def test_an_external_repo_pr_is_no_longer_blanket_protected():
    """THE DEFECT. icdev_ft#323 touches ui/ and docs/ -- none of the protected
    paths, all of which are ICDEV[IT] files."""
    r = Runner({"icdev-ai/icdev_ft": [_entry(FT, "ui/src/routes/glossary.tsx", "docs/GLOSSARY.md")]})
    assert _watcher(r)._protected_hits(FT) == []
    assert "--repo" in r.calls[0] and r.calls[0][r.calls[0].index("--repo") + 1] == "icdev-ai/icdev_ft"


def test_an_external_repo_pr_that_DOES_touch_a_protected_path_is_still_caught():
    """Repo-awareness must not become blanket permission."""
    r = Runner({"icdev-ai/icdev_ft": [_entry(FT, "tools/ci/pr_watcher.py")]})
    assert _watcher(r)._protected_hits(FT) == ["tools/ci/pr_watcher.py"]


def test_a_local_pr_is_unchanged():
    r = Runner({None: [_entry(IT, "tools/ci/pr_watcher.py")]})
    assert _watcher(r)._protected_hits(IT) == ["tools/ci/pr_watcher.py"]


def test_an_unreadable_listing_is_still_treated_as_protected():
    """The fail-closed rule is the whole point of the guard and must survive:
    a merge gate that opens when it cannot see is not a gate."""
    def boom(cmd, **kw):
        raise OSError("gh missing")

    hits = _watcher(boom)._protected_hits(FT)
    assert hits, "an unreadable listing must NOT read as clean"


def test_no_protected_paths_configured_means_no_guard():
    """Unchanged: an empty list is 'unguarded', not 'everything protected'."""
    r = Runner({"icdev-ai/icdev_ft": [_entry(FT, "anything.py")]})
    assert _watcher(r, paths=())._protected_hits(FT) == []
