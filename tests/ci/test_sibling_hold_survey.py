# CUI // SP-CTI
"""The sibling-hold survey must be right about what union covers.

kpr-watch-07 showed GitHub does not apply `.gitattributes` merge drivers, so the
union-merged paths the sibling guard excludes are real forge collisions. The
obvious response — stop excluding them — is the change `GENERATED_PATH_MARKERS`
already records being burned by: on 2026-08-09 one shared file made every open PR
a sibling of every other and the guard refused all six.

So the widened posture gets measured before it gets armed, and the measurement
has to be trustworthy in one direction above all: it must not UNDER-report how
much serialization widening would cause. Every test here defends that direction.
"""
from __future__ import annotations

import json

import tools.ci.sibling_hold_survey as shs


GITATTRIBUTES = """\
* text=auto eol=lf
*.py text eol=lf

tools/manifest.md merge=union
tools/manifest/*.md merge=union
args/ci_test_files/*.txt merge=union
args/ci_skip_census.txt merge=union
docs/reference/commands.md merge=union
# a commented-out rule is not a rule: tools/nope/*.md merge=union
*.db binary
"""


# ── reading the rules from the source of truth ──────────────────────────────
def test_union_patterns_come_from_gitattributes(tmp_path):
    """Hardcoding a second list is how a claim about union drifts from the merge
    rules it describes — which is the defect this survey exists to measure."""
    (tmp_path / ".gitattributes").write_text(GITATTRIBUTES, encoding="utf-8")
    pats = shs.union_patterns(tmp_path)
    assert "args/ci_test_files/*.txt" in pats
    assert "docs/reference/commands.md" in pats
    assert not any("nope" in p for p in pats), "a commented rule is not a rule"
    assert not any("binary" in p for p in pats)


def test_a_repo_with_no_gitattributes_reports_no_union(tmp_path):
    """`widened` then equals `current`, and the survey says so rather than
    inventing a difference it cannot support."""
    assert shs.union_patterns(tmp_path) == []


# ── what union actually covers ──────────────────────────────────────────────
def test_a_union_path_is_recognised():
    pats = ["args/ci_test_files/*.txt", "tools/manifest/*.md"]
    assert shs.is_union_merged("args/ci_test_files/core.txt", pats)
    assert shs.is_union_merged("tools/manifest/kanban.md", pats)


def test_star_does_not_cross_a_slash():
    """fnmatch's `*` matches `/` and would claim `tools/manifest/a/b.md`, which
    no union rule covers. Over-claiming makes `widened` look milder than it is —
    the one direction this survey must not be wrong in."""
    pats = ["tools/manifest/*.md"]
    assert not shs.is_union_merged("tools/manifest/nested/deep.md", pats)


def test_a_non_union_path_is_not_claimed():
    pats = ["args/ci_test_files/*.txt"]
    assert not shs.is_union_merged("args/test_gating_gate.yaml", pats)
    assert not shs.is_union_merged("tools/ci/pr_watcher.py", pats)


def test_windows_separators_compare_against_posix_rules():
    pats = ["args/ci_test_files/*.txt"]
    assert shs.is_union_merged(r"args\ci_test_files\core.txt", pats)


# ── the two postures ────────────────────────────────────────────────────────
PATS = ["args/ci_test_files/*.txt", "tools/manifest/*.md"]


def test_current_posture_excludes_a_union_path():
    assert shs.is_excluded("args/ci_test_files/core.txt", "current", PATS)


def test_widened_posture_counts_a_union_path_as_a_collision():
    """The whole point: git merges it, the forge does not."""
    assert not shs.is_excluded("args/ci_test_files/core.txt", "widened", PATS)


def test_widening_does_not_disturb_the_other_exclusions():
    """`args/projects.yaml` is excluded as a HEURISTIC about how it is edited,
    not because git resolves it — it has no union rule and never had one. It
    must stay excluded under both postures or the survey is measuring two
    changes at once."""
    assert shs.is_excluded("args/projects.yaml", "current", PATS)
    assert shs.is_excluded("args/projects.yaml", "widened", PATS)


def test_a_generated_file_stays_excluded_under_both():
    """Regenerating resolves it; there is nothing to serialize."""
    p = "docs/research/external-benchmark-map.generated.md"
    assert shs.is_excluded(p, "current", PATS)
    assert shs.is_excluded(p, "widened", PATS)


def test_an_ordinary_source_file_is_never_excluded():
    assert not shs.is_excluded("tools/cortex/blueprint.py", "current", PATS)
    assert not shs.is_excluded("tools/cortex/blueprint.py", "widened", PATS)


# ── the replay ──────────────────────────────────────────────────────────────
def _pr(number, created, merged="", files=(), closed=""):
    return {
        "number": number, "url": f"https://x/pull/{number}", "branch": "b",
        "created": created, "merged": merged, "closed": closed,
        "files": list(files),
    }


def test_two_prs_sharing_only_a_union_path_collide_only_when_widened():
    corpus = [
        _pr(1, "2026-01-01T00:00:00Z", merged="2026-01-03T00:00:00Z",
            files=["args/ci_test_files/core.txt", "tools/a.py"]),
        _pr(2, "2026-01-02T00:00:00Z", merged="2026-01-04T00:00:00Z",
            files=["args/ci_test_files/core.txt", "tools/b.py"]),
    ]
    rep = shs.survey(corpus, patterns=PATS)
    assert rep["current"]["would_be_held"] == 0
    # #2 merges while #1 is... already merged. Held is about OPEN siblings.
    assert rep["widened"]["would_be_held"] == 0, (
        "#1 had merged before #2's moment — an already-merged PR is not a sibling")


def test_a_lower_numbered_OPEN_sibling_holds_the_candidate():
    corpus = [
        _pr(1, "2026-01-01T00:00:00Z", merged="2026-01-09T00:00:00Z",
            files=["args/ci_test_files/core.txt"]),
        _pr(2, "2026-01-02T00:00:00Z", merged="2026-01-05T00:00:00Z",
            files=["args/ci_test_files/core.txt"]),
    ]
    rep = shs.survey(corpus, patterns=PATS)
    assert rep["current"]["would_be_held"] == 0, "union path excluded today"
    assert rep["widened"]["would_be_held"] == 1, (
        "#2 merged at a moment when lower-numbered #1 was still open")
    assert rep["widened"]["examples"][0]["pr"] == 2
    assert rep["widened"]["examples"][0]["held_by"] == [1]


def test_the_lowest_number_in_a_clique_is_never_held():
    """This is what keeps a widened posture a QUEUE rather than a deadlock, and
    it is the difference from 2026-08-09. Assert it rather than assume it."""
    corpus = [
        _pr(1, "2026-01-01T00:00:00Z", merged="2026-01-05T00:00:00Z",
            files=["args/ci_test_files/core.txt"]),
        _pr(2, "2026-01-01T00:00:00Z", merged="2026-01-06T00:00:00Z",
            files=["args/ci_test_files/core.txt"]),
        _pr(3, "2026-01-01T00:00:00Z", merged="2026-01-07T00:00:00Z",
            files=["args/ci_test_files/core.txt"]),
    ]
    rep = shs.survey(corpus, patterns=PATS)
    assert rep["widened"]["moments_with_nobody_free"] == 0
    assert rep["widened"]["max_clique"] == 3


def test_a_closed_unmerged_pr_stops_being_a_sibling():
    corpus = [
        _pr(1, "2026-01-01T00:00:00Z", closed="2026-01-02T00:00:00Z",
            files=["args/ci_test_files/core.txt"]),
        _pr(2, "2026-01-01T00:00:00Z", merged="2026-01-05T00:00:00Z",
            files=["args/ci_test_files/core.txt"]),
    ]
    rep = shs.survey(corpus, patterns=PATS)
    assert rep["widened"]["would_be_held"] == 0


def test_prs_that_share_nothing_never_collide():
    corpus = [
        _pr(1, "2026-01-01T00:00:00Z", merged="2026-01-09T00:00:00Z",
            files=["tools/a.py"]),
        _pr(2, "2026-01-02T00:00:00Z", merged="2026-01-05T00:00:00Z",
            files=["tools/b.py"]),
    ]
    rep = shs.survey(corpus, patterns=PATS)
    assert rep["widened"]["would_be_held"] == 0
    assert rep["widened"]["max_clique"] == 1


# ── an unavailable corpus is not a clean result ─────────────────────────────
def test_a_gh_failure_exits_2_rather_than_reporting_a_clean_survey(monkeypatch):
    """UNMEASURABLE is not zero. A survey nobody could run must not print a
    reassuring report — the same rule red_first_gate encodes as exit 2."""
    def _boom(state, limit, **kw):
        raise RuntimeError("gh not authenticated")
    monkeypatch.setattr(shs, "fetch_prs", _boom)
    assert shs.main(["--json"]) == 2


def test_fetch_prs_raises_rather_than_returning_an_empty_corpus():
    class _P:
        returncode, stdout, stderr = 1, "", "gh: not found"
    try:
        shs.fetch_prs("open", 5, runner=lambda *a, **k: _P())
    except RuntimeError:
        return
    raise AssertionError("a failed gh call must raise, not report zero PRs")


def test_fetch_prs_parses_the_fields_the_replay_needs():
    payload = json.dumps([{
        "number": 7, "url": "https://x/pull/7", "headRefName": "kanban/t",
        "createdAt": "2026-01-01T00:00:00Z", "mergedAt": "2026-01-02T00:00:00Z",
        "closedAt": None, "files": [{"path": "tools/a.py"}, {"path": ""}],
    }])

    class _P:
        returncode, stdout, stderr = 0, payload, ""

    prs = shs.fetch_prs("merged", 5, runner=lambda *a, **k: _P())
    assert prs[0]["number"] == 7
    assert prs[0]["files"] == ["tools/a.py"], "a blank path is dropped"
    assert prs[0]["merged"] == "2026-01-02T00:00:00Z"
