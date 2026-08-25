# CUI // SP-CTI
"""A NON-required check that fails must not spend resumes, escalate, or hold a merge.

THE DEFECT (task-det-295a9bb95e, surfaced by the recovery_summary detector).
`Test (Windows)` is NON-REQUIRED ON PURPOSE — icdev-ci.yml says so in as many
words: "a Windows-only flake cannot block a merge". Branch protection requires
Lint / Test / Security Scan / Helm Lint and nothing else. Yet
`error_classifier.is_ci_failed` fired on ANY failing check in the rollup, so
pr_watcher classified PR #1859 `ci_failed` with every required check green,
injected five resume contexts into a branch that had no defect in it, escalated
to "manual intervention required", and filed a NEEDED-A-HUMAN card. Surveyed
2026-08-23 over the 11 `ci_failed` escalations since 08-14: two (#1841, #1859)
were this exact shape — ten resumes and two cards for a check designed not to
block.

The CI-hosted sweep (.github/workflows/pr-watcher.yml) merged both anyway, and
only by ACCIDENT: `gh pr checks | awk '{print $2}'` splits on whitespace, so for
any check whose name contains a space `$2` is a fragment of the NAME
(`(Windows)`, `Shard`, `Scan`) and never matches `fail`. 17 of this repo's 19
checks have a space in their name. Its "all checks green" rule was enforced for
`Lint` and `Test` alone.

THE FIX is one rule in both places: the REQUIRED set decides, and it is read from
branch protection — the ONE place it is declared — never from a list in code.
A required check ABSENT from the rollup is "not yet green", never passing; an
unresolvable required set (no protection, 403, an empty set) falls back to the
old all-checks behaviour and says so, because merging on nothing is the
direction with consequences.
"""
from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

from tools.ci import error_classifier as ec
from tools.ci import merge_readiness as mr
import tools.ci.pr_watcher as pw

REQUIRED = frozenset({"Lint", "Test", "Security Scan", "Helm Lint"})
ROOT = pathlib.Path(__file__).resolve().parents[2]


def _check(name, conclusion="SUCCESS"):
    return {"__typename": "CheckRun", "name": name, "conclusion": conclusion,
            "status": "COMPLETED"}


def _pr(checks, **extra):
    pr = {"state": "OPEN", "isDraft": False, "mergeable": "MERGEABLE",
          "mergeStateStatus": "CLEAN", "baseRefName": "main", "labels": [],
          "reviews": [], "statusCheckRollup": checks,
          "url": "https://github.com/icdev-ai/icdev/pull/1859", "number": 1859,
          "headRefName": "kanban/rem-hyg-17", "headRefOid": "abc"}
    pr.update(extra)
    return pr


#: PR #1859 as the forge reported it: every required check green, Test (Windows) red.
PR_1859 = [_check("Lint"), _check("Test"), _check("Security Scan"),
           _check("Helm Lint"), _check("Test (Windows)", "FAILURE"),
           _check("E2E Shard 1 of 4")]


# ── the primitives ──────────────────────────────────────────────────────────
def test_a_non_required_failure_is_not_ci_failed():
    pr = _pr(PR_1859)
    assert ec.is_ci_failed(pr) is True                     # the old, unqualified read
    assert ec.is_ci_failed(pr, required=REQUIRED) is False


def test_a_required_failure_is_still_ci_failed():
    pr = _pr([_check("Lint"), _check("Test", "FAILURE"), _check("Test (Windows)")])
    assert ec.is_ci_failed(pr, required=REQUIRED) is True


def test_required_green_with_a_non_required_red_is_passing():
    pr = _pr(PR_1859)
    assert ec.is_passing(pr) is False
    assert ec.is_passing(pr, required=REQUIRED) is True


def test_a_required_check_absent_from_the_rollup_is_not_passing():
    """Only Lint has reported. Filtering to the required set must not turn a
    partial rollup into a green one — GitHub would refuse the merge."""
    pr = _pr([_check("Lint"), _check("Test (Windows)")])
    assert ec.is_passing(pr, required=REQUIRED) is False
    assert ec.is_ci_failed(pr, required=REQUIRED) is False


def test_an_empty_required_set_means_unresolved_and_reads_every_check():
    pr = _pr(PR_1859)
    assert ec.is_ci_failed(pr, required=frozenset()) is True
    assert ec.is_passing(pr, required=frozenset()) is False


def test_a_status_context_is_matched_by_its_context_name():
    pr = _pr([{"__typename": "StatusContext", "context": "Test", "state": "SUCCESS"},
              {"__typename": "StatusContext", "context": "Lint", "state": "SUCCESS"},
              {"__typename": "StatusContext", "context": "Security Scan", "state": "SUCCESS"},
              {"__typename": "StatusContext", "context": "Helm Lint", "state": "SUCCESS"},
              {"__typename": "StatusContext", "context": "ci/windows", "state": "FAILURE"}])
    assert ec.is_passing(pr, required=REQUIRED) is True


def test_ignored_failures_names_what_was_set_aside():
    assert ec.ignored_failures(_pr(PR_1859), required=REQUIRED) == ["Test (Windows)"]
    assert ec.ignored_failures(_pr(PR_1859), required=None) == []


def test_classify_pr_state_reaches_done_past_a_non_required_red():
    pr = _pr(PR_1859)
    assert ec.classify_pr_state(pr, require_approval=False) == ec.KanbanState.CI_FAILED
    assert ec.classify_pr_state(
        pr, require_approval=False, required=REQUIRED) == ec.KanbanState.DONE


# ── the shared merge ladder (one ladder, not a second copy) ─────────────────
def test_merge_readiness_is_ready_and_names_the_ignored_check():
    v = mr.classify_merge_readiness(
        _pr(PR_1859), default_branch="main", behind_by=0)
    assert v.state == mr.CI_FAILED
    v = mr.classify_merge_readiness(
        _pr(PR_1859), default_branch="main", behind_by=0, required_checks=REQUIRED)
    assert v.state == mr.READY
    assert "Test (Windows)" in v.reason and "non-required" in v.reason


def test_merge_readiness_ci_failed_reason_names_only_required_failures():
    pr = _pr([_check("Lint"), _check("Test", "FAILURE"), _check("Security Scan"),
              _check("Helm Lint"), _check("Test (Windows)", "FAILURE")])
    v = mr.classify_merge_readiness(
        pr, default_branch="main", behind_by=0, required_checks=REQUIRED)
    assert v.state == mr.CI_FAILED
    assert "Test" in v.reason and "Test (Windows)" not in v.reason


# ── resolving the set from branch protection ────────────────────────────────
def _runner_returning(stdout, rc=0):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=rc, stdout=stdout, stderr="")
    run.calls = calls
    return run


def test_required_checks_are_read_from_branch_protection_contexts():
    payload = json.dumps({"required_status_checks": {
        "strict": False, "contexts": ["Lint", "Test", "Security Scan", "Helm Lint"],
        "checks": [{"context": "Lint", "app_id": 15368}]}})
    run = _runner_returning(payload)
    assert mr.fetch_required_checks("main", runner=run) == REQUIRED
    assert any("branches/main/protection" in " ".join(c) for c in run.calls)


def test_required_checks_fall_back_to_the_checks_shape():
    payload = json.dumps({"required_status_checks": {
        "checks": [{"context": "Lint"}, {"context": "Test"}]}})
    assert mr.fetch_required_checks("main", runner=_runner_returning(payload)) \
        == frozenset({"Lint", "Test"})


def test_an_unprotected_or_unreadable_branch_is_unresolved_not_empty():
    assert mr.fetch_required_checks(
        "main", runner=_runner_returning('{"message":"Branch not protected"}', rc=1)) is None
    assert mr.fetch_required_checks(
        "main", runner=_runner_returning("not json")) is None
    assert mr.fetch_required_checks(
        "main", runner=_runner_returning(json.dumps({"required_status_checks": None}))) is None
    assert mr.fetch_required_checks(
        "main", runner=_runner_returning(json.dumps(
            {"required_status_checks": {"contexts": []}}))) is None


# ── the watcher consults it, and the knob turns it off ──────────────────────
def _watcher(protection_stdout, rc=0, **config):
    cfg = {"required_checks_only": True}
    cfg.update(config)
    w = pw.PRWatcher(config=cfg, get_connection=lambda: None)
    w._gh_runner = _runner_returning(protection_stdout, rc=rc)
    return w


PROTECTION = json.dumps({"required_status_checks": {"contexts": sorted(REQUIRED)}})


def test_the_watcher_resolves_and_caches_the_required_set():
    w = _watcher(PROTECTION)
    assert w.required_checks() == REQUIRED
    assert w.required_checks() == REQUIRED
    assert len(w._gh_runner.calls) == 1


def test_the_knob_off_reads_every_check_and_makes_no_forge_call():
    w = _watcher(PROTECTION, required_checks_only=False)
    assert w.required_checks() is None
    assert w._gh_runner.calls == []


def test_an_unresolved_set_is_retried_only_after_the_cache_expires():
    w = _watcher('{"message":"Branch not protected"}', rc=1)
    assert w.required_checks() is None
    assert w.required_checks() is None
    assert len(w._gh_runner.calls) == 1


# ── the CI-hosted sweep parses the STATUS column, for the REQUIRED set ──────
def test_the_ci_sweep_asks_for_required_checks_and_never_splits_on_whitespace():
    text = (ROOT / ".github" / "workflows" / "pr-watcher.yml").read_text(encoding="utf-8")
    assert "awk '{print $2}'" not in text, (
        "awk on whitespace reads a NAME fragment for every check with a space "
        "in its name — 17 of 19 here — so 'fail' is never seen")
    # Code lines only: a comment explaining the old defect may name the command.
    checks_lines = [ln for ln in text.splitlines()
                    if "gh pr checks" in ln and not ln.strip().startswith("#")]
    assert checks_lines, "the sweep must still ask gh which checks passed"
    assert all("--required" in ln for ln in checks_lines)
    assert all("--json" in ln for ln in checks_lines)


def test_the_required_knob_is_declared_on():
    import yaml
    cfg = yaml.safe_load((ROOT / "args" / "pr_watcher_config.yaml").read_text(encoding="utf-8"))
    assert cfg.get("required_checks_only") is True
