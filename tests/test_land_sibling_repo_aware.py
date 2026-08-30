"""The sibling-conflict check must list the PR's OWN repository (kpr-rvfy-07).

`_open_pr_index` ran `gh pr list` with no --repo, so it listed whichever
repository the process was standing in. For an EXTERNAL-repo task -- ICDEV[FT],
compass, idea_lab -- the task's PR is simply not in that listing, and land.py
reads a PR's own absence as "the listing failed":

    if pr_url not in file_map:
        checks.append(_ck("no_sibling_conflict", False, "open-PR listing unavailable"))

which is a correct fail-closed reading of a listing that genuinely could not be
trusted -- and, with `hold_on_sibling_conflict: true`, refused EVERY ICDEV[FT]
task no matter what the PR looked like. Measured 2026-08-30 on icdev_ft#320:
twelve of thirteen gates passed and this one refused, because `gh pr list` in
the ICDev checkout cannot see an icdev_ft PR.

That was the second of two independent defects jamming the sanctioned merge
door for FT work; the first was the pytest gate stripping PYTHONPATH
(kpr-rvfy-06). Between them the door always refused, which is why agents
learned to reach for a raw `gh pr merge` that runs none of the thirteen.
"""
from __future__ import annotations

import json

import pytest

from tools.ci.pr_watcher import PRWatcher, repo_of

FT = "https://github.com/icdev-ai/icdev_ft/pull/320"
IT = "https://github.com/icdev-ai/icdev/pull/1984"


@pytest.mark.parametrize("url,expected", [
    (FT, "icdev-ai/icdev_ft"),
    (IT, "icdev-ai/icdev"),
    ("https://github.com/o/r/pull/1/files", "o/r"),
    ("not a url", None),
    ("", None),
    (None, None),
])
def test_repo_of_reads_the_owner_and_name(url, expected):
    assert repo_of(url) == expected


def _watcher_with(recorder):
    w = PRWatcher.__new__(PRWatcher)          # no __init__: this is a pure-seam test
    w._pr_list_runner = recorder
    return w


class Runner:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)

        class P:
            returncode = 0
            stdout = json.dumps(self.payload)

        return P()


def test_an_external_repo_is_passed_to_gh():
    r = Runner([{"url": FT, "files": [{"path": "a.py"}],
                 "mergeable": "MERGEABLE", "isDraft": False}])
    w = _watcher_with(r)
    files = w._open_pr_files("icdev-ai/icdev_ft")
    assert FT in files, "the external PR must appear in its own repo's listing"
    cmd = r.calls[0]
    assert "--repo" in cmd and cmd[cmd.index("--repo") + 1] == "icdev-ai/icdev_ft"


def test_no_repo_keeps_the_old_local_behaviour():
    """An ICDEV[IT] task lists this checkout, exactly as before -- no --repo, so
    the existing single-repo path and its call count are unchanged."""
    r = Runner([{"url": IT, "files": [{"path": "b.py"}],
                 "mergeable": "MERGEABLE", "isDraft": False}])
    w = _watcher_with(r)
    assert IT in w._open_pr_files()
    assert "--repo" not in r.calls[0]


def test_a_failed_listing_is_still_an_empty_map_not_an_exception():
    """Unchanged: the caller distinguishes {} (could not tell) from a map that
    lacks the PR, and land.py fails CLOSED on the first."""
    def boom(cmd, **kw):
        raise OSError("gh missing")

    w = _watcher_with(boom)
    assert w._open_pr_files("icdev-ai/icdev_ft") == {}
