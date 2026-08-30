# CUI // SP-CTI
"""kpr-watch-05: pr_watcher must never auto-merge a change to itself.

THE STANDING HAZARD. The watcher auto-merges any CI-green ``kanban/*`` branch,
including one that edits the watcher. A defect in the merge-eligibility ladder
therefore merges ITSELF into main, and every cycle afterwards runs on the new,
wrong rule with no human in the path.

It is not episodic. It is true whenever any card touches that file, which is why
two separate cards independently invented per-episode manual gates for it
(``kpr-gate-02`` held five tasks for exactly this reason, and the ALRT card
invented a MANUAL-ONLY convention of its own). A per-episode gate cannot fix a
standing hazard — this is its durable replacement.

The reverse-direction test is the one that matters and it is stated first: a
green, MERGEABLE, non-draft PR touching ``tools/ci/pr_watcher.py`` must NOT be
classified ready. Against the pre-change tree there is no ``protected_path``
state at all, so the whole file is the recorded RED.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

import tools.ci.merge_readiness as mr
import tools.ci.pr_watcher as pw

REPO = pathlib.Path(__file__).resolve().parents[1]
GUARDED = ["tools/ci/pr_watcher.py", "args/pr_watcher_config.yaml"]


def _green_pr(url="https://github.com/o/r/pull/1"):
    return {
        "url": url, "state": "OPEN", "isDraft": False, "baseRefName": "main",
        "mergeable": "MERGEABLE", "labels": [], "reviews": [],
        "statusCheckRollup": [{"name": "Test", "conclusion": "SUCCESS"}],
    }


# ── the reverse direction ──────────────────────────────────────────────────
def test_a_green_mergeable_pr_touching_the_watcher_is_not_ready():
    v = mr.classify_merge_readiness(
        _green_pr(), default_branch="main",
        changed_files=["tools/ci/pr_watcher.py"], protected_paths=GUARDED)
    assert v.state == mr.PROTECTED_PATH
    assert v.ready is False
    assert "tools/ci/pr_watcher.py" in v.reason


def test_ownership_does_not_excuse_it():
    """The rung sits AHEAD of `linked` on purpose. Both merge paths auto-merge,
    so an answer that depends on which door the work came through is no guard."""
    url = "https://github.com/o/r/pull/1"
    v = mr.classify_merge_readiness(
        _green_pr(url), default_branch="main", linked_urls=[url],
        changed_files=["tools/ci/pr_watcher.py"], protected_paths=GUARDED)
    assert v.state == mr.PROTECTED_PATH, "a task-linked PR must not be excused"


def test_an_untouched_pr_is_still_ready():
    v = mr.classify_merge_readiness(
        _green_pr(), default_branch="main",
        changed_files=["README.md"], protected_paths=GUARDED)
    assert v.state == mr.READY


def test_protection_is_off_by_default():
    """No configured path must mean byte-for-byte the old behaviour, or every
    existing caller starts refusing everything."""
    assert mr.classify_merge_readiness(
        _green_pr(), default_branch="main").state == mr.READY
    assert mr.protected_hits(["anything.py"], []) is None


# ── matching, and the near-miss the spec calls out ─────────────────────────
def test_a_near_miss_is_not_caught():
    """`tools/ci/pr_watcher.py` must not catch `pr_watcher_helpers.py`. A control
    that stops work it was never meant to stop gets switched off."""
    assert mr.protected_hits(
        ["tools/ci/pr_watcher_helpers.py"], ["tools/ci/pr_watcher.py"]) == []


def test_a_directory_entry_matches_beneath_it_only():
    assert mr.protected_hits(["tools/ci/sub/x.py"], ["tools/ci"]) == ["tools/ci"]
    assert mr.protected_hits(["tools/cix/x.py"], ["tools/ci"]) == []


def test_windows_separators_still_match():
    assert mr.protected_hits(
        [r"tools\ci\pr_watcher.py"], ["tools/ci/pr_watcher.py"]) == \
        ["tools/ci/pr_watcher.py"]


def test_an_unknown_file_list_fails_CLOSED():
    """The opposite default from the sibling-conflict map, deliberately. A
    missed sibling conflict costs a retry; a missed protected path costs the
    control. A merge gate that opens when it cannot see is not a gate."""
    assert mr.protected_hits(None, GUARDED) == sorted(GUARDED)
    v = mr.classify_merge_readiness(
        _green_pr(), default_branch="main",
        changed_files=None, protected_paths=GUARDED)
    assert v.state == mr.PROTECTED_PATH
    assert "could not be determined" in v.reason


# ── the shipped configuration ──────────────────────────────────────────────
def test_the_config_protects_itself():
    """A PR that weakens or empties `protected_paths` must not be able to
    auto-merge itself in. tools/kanban/gates.py already states the principle:
    'A gate that cannot protect itself is not a control.'"""
    cfg = yaml.safe_load((REPO / "args/pr_watcher_config.yaml").read_text(
        encoding="utf-8")) or {}
    paths = cfg.get("protected_paths") or []
    assert "args/pr_watcher_config.yaml" in paths


@pytest.mark.parametrize("path", [
    "tools/ci/pr_watcher.py", "args/pr_watcher_config.yaml",
    ".claude/hooks/pre_tool_use.py", "tools/kanban/task_factory.py",
    "tools/kanban/gates.py",
])
def test_the_seed_list_ships(path):
    cfg = yaml.safe_load((REPO / "args/pr_watcher_config.yaml").read_text(
        encoding="utf-8")) or {}
    assert path in (cfg.get("protected_paths") or [])


def test_every_protected_path_exists():
    """A typo'd entry protects nothing and nothing would ever say so."""
    cfg = yaml.safe_load((REPO / "args/pr_watcher_config.yaml").read_text(
        encoding="utf-8")) or {}
    for path in cfg.get("protected_paths") or []:
        assert (REPO / path).exists(), f"protected path does not exist: {path}"


# ── both merge paths ───────────────────────────────────────────────────────
class _Watcher(pw.PRWatcher):
    """Watcher with the forge stubbed: one open PR, with a known file set."""

    def __init__(self, files, **kw):
        super().__init__(config={"auto_merge_enabled": True,
                                 "protected_paths": GUARDED}, **kw)
        self._files = files
        self.merged = []
        self.audits = []

    def _open_pr_index(self, repo=None):  # repo: the PR's own repository (kpr-rvfy-09) --
        # optional on the real watcher, so only a double that hard-codes the
        # old arity needs touching. No assertion changes.
        if self._files is None:
            return {}                      # PR absent from the listing
        return {"https://github.com/o/r/pull/1":
                {"files": set(self._files), "mergeable": "MERGEABLE",
                 "draft": False}}

    def _audit(self, action):
        self.audits.append(action)


def test_auto_merge_refuses_a_protected_pr():
    """The chokepoint. BOTH merge paths call `_auto_merge`, so a future caller
    cannot route around the guard."""
    w = _Watcher(["tools/ci/pr_watcher.py"])
    assert w._auto_merge("https://github.com/o/r/pull/1") is False


def test_auto_merge_refuses_when_the_pr_is_not_in_the_listing():
    w = _Watcher(None)
    assert w._auto_merge("https://github.com/o/r/pull/1") is False


def test_auto_merge_still_merges_an_unprotected_pr():
    calls = []

    class _Proc:
        returncode = 0
        stderr = ""

    w = _Watcher(["README.md"],
                 auto_merge_runner=lambda *a, **k: calls.append(a) or _Proc())
    assert w._auto_merge("https://github.com/o/r/pull/1") is True
    assert calls, "an unprotected PR must still merge"


def test_a_refusal_is_never_silent():
    """'Not a silent continue' is the requirement: a human has to be able to see
    what is waiting and why."""
    w = _Watcher(["tools/ci/pr_watcher.py"])
    hits = w._refuse_protected("https://github.com/o/r/pull/1", "kpr-watch-05")
    assert hits == ["tools/ci/pr_watcher.py"]
    assert len(w.audits) == 1
    assert w.audits[0].action == "protected_path_hold"
    assert w.audits[0].task_id == "kpr-watch-05"
    assert "tools/ci/pr_watcher.py" in w.audits[0].reason


def test_the_guard_runs_before_the_un_draft():
    """ORDERING IS THE SAFETY PROPERTY. `_auto_merge` refuses a protected PR
    anyway, but by then `_mark_ready` has cleared the draft — and the draft is
    exactly the brake the per-episode manual gates relied on. Un-drafting is
    visible and hard to walk back, so it must not happen for a PR that was never
    going to merge."""
    import inspect

    src = inspect.getsource(pw)
    guard = src.index("_refuse_protected(pr_url, task[")
    undraft = src.index("approved_ok = self._mark_ready(")
    assert guard < undraft, (
        "the protected-path refusal must precede the un-draft on the linked path")


def test_dry_run_does_not_excuse_a_protected_pr():
    """`dry_run` returns True from `_auto_merge` early. The guard has to sit
    ahead of that, or a dry run reports a merge the real run would refuse."""
    w = _Watcher(["tools/ci/pr_watcher.py"], dry_run=True)
    assert w._auto_merge("https://github.com/o/r/pull/1") is False


def test_the_refusal_LOGS_as_well_as_audits(caplog):
    """`tests/ci/test_pr_watcher_stale_conflict_recovery.py` defends one
    principle — "a refusal must leave a trace" — after eleven PRs went unmerged
    for a day with no evidence a merge had been attempted. This guard adds a
    THIRD way `_auto_merge` can return False, so it owes that file's principle
    the same debt: refuse loudly, and name the path that caused it.
    """
    w = _Watcher(["tools/ci/pr_watcher.py"])
    pw.logger.propagate = True
    with caplog.at_level("WARNING", logger=pw.logger.name):
        assert w._auto_merge("https://github.com/o/r/pull/1") is False
    assert any("REFUSING to merge" in r.getMessage() for r in caplog.records), \
        "a protected-path refusal must leave a trace, like every other refusal"
    assert any("tools/ci/pr_watcher.py" in r.getMessage() for r in caplog.records), \
        "name the path — 'refused' without the reason is the silence this fixes"
