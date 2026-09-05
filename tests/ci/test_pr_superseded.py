# CUI // SP-CTI
"""A PR whose head commits ALREADY LANDED through a merged sibling.

FOUR CASES IN TWO DAYS, and every one of them held the queue behind it:
#2015 (its commit merged in #2014, 42 seconds earlier), #1985 (#1983, 82
seconds), #2056 (the kpr-stale-06 branch re-opened after #2053 squashed it) and
#2049 (kpr-stale-05, whose two commits landed INSIDE #2053). Each sat
CONFLICTING or "ahead", each held `no_sibling_conflict` against every open PR
touching the same files -- #2015 blocked #2016 on compliance_server.py -- and
each was diagnosed by a human running `gh pr view <sibling> --json commits` and
closed by hand.

The fixtures below are the REAL forge answers for those four pairs, recorded
2026-09-05. Three of the four are the same branch at the same head sha under two
PR numbers; the fourth is a stacked branch whose commits the survivor absorbed.

FAIL-OPEN IS THE LOAD-BEARING PROPERTY. This module closes pull requests, so an
unreadable forge answer must produce `checked: False` and NEVER a finding --
the same posture `landed_check` takes for the same class of question.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import tools.ci.pr_superseded as ps


# ---------------------------------------------------------------------------
# The four recorded incidents (live forge answers, 2026-09-05)
# ---------------------------------------------------------------------------
def _pr(number, branch, head, commits, *, body="", title=""):
    return {
        "number": number,
        "url": "https://github.com/icdev-ai/icdev/pull/%d" % number,
        "title": title or ("PR %d" % number),
        "body": body,
        "headRefName": branch,
        "headRefOid": head,
        "commits": [{"oid": c} for c in commits],
    }


def _merged(pr, at="2026-09-02T22:29:38Z"):
    out = dict(pr)
    out["mergedAt"] = at
    return out


_C2014 = "7083ea7464081f2b1c2ba1a1d4a9d0f2e7f0a111"
_C1983 = "eec4d789b2a5b0b2a1c3d4e5f60718293a4b5c6d"
_C2053 = "d8a2b6789986a1b2c3d4e5f60718293a4b5c6d7e"
_C2049_A = "9e26d2b793a1b2c3d4e5f60718293a4b5c6d7e8f"
_C2049_B = "52b4b2e8dbcfa1b2c3d4e5f60718293a4b5c6d7e"


INCIDENTS = [
    # (open PR, merged sibling, expected family kind)
    (
        _pr(2015, "kanban/rmf-oscal-01", _C2014, [_C2014]),
        _merged(_pr(2014, "kanban/rmf-oscal-01", _C2014, [_C2014])),
        "same_branch",
    ),
    (
        _pr(1985, "kanban/task-qa-sweep-c17bd3d6", _C1983, [_C1983]),
        _merged(_pr(1983, "kanban/task-qa-sweep-c17bd3d6", _C1983, [_C1983])),
        "same_branch",
    ),
    (
        _pr(2056, "kanban/kpr-stale-06", _C2053, [_C2053]),
        _merged(_pr(2053, "kanban/kpr-stale-06", _C2053, [_C2053])),
        "same_branch",
    ),
    (
        # #2049's own two commits, absorbed by #2053, which NAMES its branch.
        _pr(2049, "kanban/kpr-stale-05", _C2049_B, [_C2049_A, _C2049_B]),
        _merged(_pr(
            2053, "kanban/kpr-stale-06", _C2053,
            [_C2049_A, _C2049_B, _C2053],
            body="Builds on #2049 (kpr-stale-05); this PR contains that branch. "
                 "Once origin/kanban/kpr-stale-05 merges, the diff collapses.")),
        "named_branch",
    ),
]


@pytest.mark.parametrize("open_pr,sibling,fam", INCIDENTS,
                         ids=[str(i[0]["number"]) for i in INCIDENTS])
def test_every_recorded_incident_is_classified_superseded(open_pr, sibling, fam):
    v = ps.decide_superseded(open_pr, [sibling])
    assert v.checked is True
    assert v.superseded is True, v.reason
    assert v.basis == ps.BASIS_SHARED_COMMITS
    assert v.sibling_number == sibling["number"]
    assert v.family == fam
    assert open_pr["headRefOid"] in v.shared_commits


# ---------------------------------------------------------------------------
# The refusals. Each one is a way the check must NOT fire.
# ---------------------------------------------------------------------------
def test_a_commit_the_sibling_does_not_have_is_never_superseded():
    """The whole safety property: work only this branch holds is never closed."""
    open_pr = _pr(3000, "kanban/x-1", "cafe" * 10, ["dead" * 10, "cafe" * 10])
    sib = _merged(_pr(2999, "kanban/x-1", "dead" * 10, ["dead" * 10]))
    v = ps.decide_superseded(open_pr, [sib])
    assert v.checked is True
    assert v.superseded is False


def test_head_commit_absent_from_the_sibling_is_never_superseded():
    """A truncated commit list must not read as containment.

    `gh` caps the commits connection, so OUR list coming back short would make
    the subset test trivially true. Requiring the head sha -- the tip, which is
    what a merge would actually apply -- is what closes that.
    """
    open_pr = _pr(3001, "kanban/x-2", "beef" * 10, ["dead" * 10])
    sib = _merged(_pr(3000, "kanban/x-2", "dead" * 10, ["dead" * 10]))
    v = ps.decide_superseded(open_pr, [sib])
    assert v.superseded is False


def test_an_unrelated_merged_pr_is_not_a_sibling():
    open_pr = _pr(3002, "kanban/a-1", "aaaa" * 10, ["aaaa" * 10])
    sib = _merged(_pr(3001, "kanban/b-9", "aaaa" * 10, ["aaaa" * 10]))
    v = ps.decide_superseded(open_pr, [sib])
    assert v.checked is True
    assert v.superseded is False
    assert v.family == ""


def test_an_open_sibling_is_not_evidence():
    """Only a MERGED sibling proves the work landed."""
    open_pr = _pr(3003, "kanban/c-1", "cccc" * 10, ["cccc" * 10])
    sib = _pr(3002, "kanban/c-1", "cccc" * 10, ["cccc" * 10])   # no mergedAt
    assert ps.decide_superseded(open_pr, [sib]).superseded is False


def test_a_pr_is_never_its_own_sibling():
    pr = _merged(_pr(3004, "kanban/d-1", "dddd" * 10, ["dddd" * 10]))
    assert ps.decide_superseded(pr, [pr]).superseded is False


# ---------------------------------------------------------------------------
# FAIL-OPEN. `checked: False` is never a finding.
# ---------------------------------------------------------------------------
def test_unreadable_merged_listing_is_unchecked_not_clean():
    open_pr = _pr(3005, "kanban/e-1", "eeee" * 10, ["eeee" * 10])
    v = ps.decide_superseded(open_pr, None)
    assert v.checked is False
    assert v.superseded is False


def test_a_pr_with_no_head_sha_is_unchecked():
    v = ps.decide_superseded(_pr(3006, "kanban/f-1", "", ["ffff" * 10]), [])
    assert v.checked is False
    assert v.superseded is False


def test_a_pr_with_no_commit_list_is_unchecked():
    v = ps.decide_superseded(_pr(3007, "kanban/g-1", "gggg" * 10, []), [])
    assert v.checked is False
    assert v.superseded is False


# ---------------------------------------------------------------------------
# fetch_merged_prs -- one gh call, fail-open, and it halves the page on refusal
# ---------------------------------------------------------------------------
def test_fetch_merged_prs_returns_none_on_gh_failure():
    def boom(cmd, **kw):
        return SimpleNamespace(returncode=1, stdout="", stderr="nope")

    assert ps.fetch_merged_prs(runner=boom) is None


def test_fetch_merged_prs_retries_at_half_the_page_on_a_node_limit():
    """The commits connection blows GitHub's node budget at a large --limit.

    Measured 2026-09-05: `--limit 60` returns
    "requesting up to 600,000 possible nodes which exceeds the maximum limit of
    500,000" and `--limit 40` succeeds. A single-shot fetch would leave the
    check permanently unchecked on a busy repo -- silently.
    """
    seen = []

    def runner(cmd, **kw):
        limit = int(cmd[cmd.index("--limit") + 1])
        seen.append(limit)
        if limit > 20:
            return SimpleNamespace(
                returncode=1, stdout="",
                stderr="GraphQL: ... exceeds the maximum limit of 500,000.")
        return SimpleNamespace(returncode=0, stdout=json.dumps([]), stderr="")

    assert ps.fetch_merged_prs(runner=runner, limit=40) == []
    assert seen == [40, 20]


def test_fetch_merged_prs_does_not_retry_a_plain_failure():
    seen = []

    def runner(cmd, **kw):
        seen.append(int(cmd[cmd.index("--limit") + 1]))
        return SimpleNamespace(returncode=1, stdout="", stderr="auth required")

    assert ps.fetch_merged_prs(runner=runner, limit=40) is None
    assert seen == [40]


# ---------------------------------------------------------------------------
# The revert leg. Every commit already upstream BY PATCH ID, and a two-dot diff
# that is not empty -- i.e. merging it would REVERT what main has.
# ---------------------------------------------------------------------------
def _git(*, cherry, shortstat="9 files changed, 1 insertion(+), 900 deletions(-)",
         fetch_rc=0):
    def runner(argv, **kw):
        if argv[1] == "fetch":
            return SimpleNamespace(returncode=fetch_rc, stdout="", stderr="")
        if argv[1] == "cherry":
            return SimpleNamespace(returncode=0, stdout=cherry, stderr="")
        if argv[1] == "diff":
            return SimpleNamespace(returncode=0, stdout=shortstat, stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")
    return runner


def test_revert_evidence_reads_all_upstream_from_git_cherry():
    ev = ps.revert_evidence("kanban/h-1", "main",
                            git_runner=_git(cherry="- aaa\n- bbb\n"))
    assert ev["checked"] is True
    assert ev["all_patches_upstream"] is True
    assert ev["would_revert"] is True
    assert "900 deletions" in ev["two_dot_stat"]


def test_revert_evidence_one_plus_line_means_unique_work():
    ev = ps.revert_evidence("kanban/h-2", "main",
                            git_runner=_git(cherry="- aaa\n+ bbb\n"))
    assert ev["all_patches_upstream"] is False


def test_revert_evidence_is_fail_open_when_git_cannot_answer():
    ev = ps.revert_evidence("kanban/h-3", "main",
                            git_runner=_git(cherry="", fetch_rc=1))
    assert ev["checked"] is False
    assert ev["all_patches_upstream"] is None


def test_revert_evidence_empty_two_dot_diff_reverts_nothing():
    ev = ps.revert_evidence("kanban/h-4", "main",
                            git_runner=_git(cherry="- aaa\n", shortstat=""))
    assert ev["all_patches_upstream"] is True
    assert ev["would_revert"] is False


def test_pure_revert_basis_needs_a_named_sibling_and_git_agreement():
    """Leg B: a CHERRY-PICKED duplicate, whose shas therefore differ."""
    open_pr = _pr(3100, "kanban/i-1", "1111" * 10, ["1111" * 10])
    sib = _merged(_pr(3099, "kanban/i-9", "2222" * 10, ["2222" * 10],
                      body="supersedes kanban/i-1"))
    ev = {"checked": True, "all_patches_upstream": True, "would_revert": True,
          "two_dot_stat": "1 file changed, 40 deletions(-)", "ahead": 1}
    v = ps.decide_superseded(open_pr, [sib], revert=ev)
    assert v.superseded is True
    assert v.basis == ps.BASIS_PURE_REVERT
    assert v.sibling_number == 3099


def test_pure_revert_never_fires_without_a_family_sibling():
    open_pr = _pr(3101, "kanban/i-2", "3333" * 10, ["3333" * 10])
    sib = _merged(_pr(3098, "kanban/zzz", "4444" * 10, ["4444" * 10]))
    ev = {"checked": True, "all_patches_upstream": True, "would_revert": True,
          "two_dot_stat": "x", "ahead": 1}
    assert ps.decide_superseded(open_pr, [sib], revert=ev).superseded is False


def test_pure_revert_never_fires_on_unchecked_git():
    open_pr = _pr(3102, "kanban/i-3", "5555" * 10, ["5555" * 10])
    sib = _merged(_pr(3097, "kanban/i-9", "6666" * 10, ["6666" * 10],
                      body="supersedes kanban/i-3"))
    ev = {"checked": False, "all_patches_upstream": None, "would_revert": None,
          "two_dot_stat": "", "ahead": None}
    assert ps.decide_superseded(open_pr, [sib], revert=ev).superseded is False


# ---------------------------------------------------------------------------
# The comment. Evidence, or it is an unexplained close.
# ---------------------------------------------------------------------------
def test_comment_names_the_sibling_the_shared_sha_and_the_two_dot_stat():
    open_pr, sib, _ = INCIDENTS[3]
    v = ps.decide_superseded(
        open_pr, [sib],
        revert={"checked": True, "all_patches_upstream": True,
                "would_revert": True, "ahead": 2,
                "two_dot_stat": "95 files changed, 11520 deletions(-)"})
    body = ps.comment_body(v)
    assert "#2053" in body
    assert _C2049_B[:12] in body
    assert "11520 deletions" in body
    assert "reopen" in body.lower()
