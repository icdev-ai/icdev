"""An external-repo PR is judged against ITS repository's required checks.

MEASURED 2026-09-23: icdev_ft PR #417 (task ftl-bz-port-01) ran Test / UI /
E2E (Playwright) / Bootstrap smoke -- every one green -- and pr_watcher judged
it against ICDev's branch protection {Lint, Test, Security Scan, Helm Lint}.
Three of those checks do not exist in icdev_ft, a required check absent from
the rollup is "not yet green", so `is_passing` was False, the PR classified
PR_OPENED on every poll, and the five-task chain behind it stalled for 7h.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import tools.ci.pr_watcher as pw
from tools.ci import error_classifier as ec
from tools.kanban import repo_registry

IT_REQUIRED = ["Helm Lint", "Lint", "Security Scan", "Test"]
FT_PR = "https://github.com/icdev-ai/icdev_ft/pull/417"
IT_PR = "https://github.com/icdev-ai/icdev/pull/2312"


def _check(name):
    return {"__typename": "CheckRun", "name": name, "conclusion": "SUCCESS",
            "status": "COMPLETED"}


def _runner(protection_by_path):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        path = next((c for c in cmd if str(c).startswith("repos/")), "")
        body = protection_by_path.get(path)
        if body is None:
            return SimpleNamespace(returncode=1, stdout='{"message":"Branch not protected"}',
                                   stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")
    run.calls = calls
    return run


def _watcher(runner):
    w = pw.PRWatcher(config={"required_checks_only": True}, get_connection=lambda: None)
    w._gh_runner = runner
    return w


def _external(monkeypatch):
    def resolve(task_id, config_path=None):
        if task_id.startswith("ftl-"):
            return repo_registry.RepoTarget(name="icdev_ft", root=None,
                                            base_branch="main", is_external=True)
        return repo_registry.RepoTarget(name="icdev", root=None,
                                        base_branch="main", is_external=False)
    monkeypatch.setattr(repo_registry, "resolve_task_repo", resolve)


def test_an_external_task_asks_its_own_repository(monkeypatch):
    _external(monkeypatch)
    run = _runner({
        "repos/{owner}/{repo}/branches/main/protection":
            {"required_status_checks": {"contexts": IT_REQUIRED}},
    })
    w = _watcher(run)

    got = w.required_checks_for("ftl-bz-port-01", FT_PR)

    assert ["gh", "api", "repos/icdev-ai/icdev_ft/branches/main/protection"] in [
        list(c) for c in run.calls], run.calls
    # icdev_ft main is unprotected -> unresolved -> every check counts
    assert got is None


def test_an_icdev_task_keeps_the_home_repository_set(monkeypatch):
    _external(monkeypatch)
    run = _runner({
        "repos/{owner}/{repo}/branches/main/protection":
            {"required_status_checks": {"contexts": IT_REQUIRED}},
    })
    w = _watcher(run)
    w._default_branch_cache = "main"

    assert w.required_checks_for("rem-hyg-17", IT_PR) == frozenset(IT_REQUIRED)
    assert all("icdev_ft" not in " ".join(map(str, c)) for c in run.calls)


def test_the_measured_pr_reads_green_under_its_own_set(monkeypatch):
    _external(monkeypatch)
    w = _watcher(_runner({}))
    state = {"state": "OPEN", "isDraft": False, "mergeable": "MERGEABLE",
             "mergeStateStatus": "CLEAN", "reviews": [],
             "statusCheckRollup": [_check("Bootstrap smoke (the easy button, from nothing)"),
                                   _check("E2E (Playwright)"), _check("Test"), _check("UI")]}

    wrong = frozenset(IT_REQUIRED)
    assert ec.classify_pr_state(state, require_approval=False, required=wrong) \
        == ec.KanbanState.PR_OPENED, "the defect: judged against ICDev's set"
    own = w.required_checks_for("ftl-bz-port-01", FT_PR)
    assert ec.classify_pr_state(state, require_approval=False, required=own) \
        == ec.KanbanState.DONE


def test_the_external_answer_is_cached_per_repository(monkeypatch):
    _external(monkeypatch)
    run = _runner({})
    w = _watcher(run)
    w.required_checks_for("ftl-bz-port-01", FT_PR)
    w.required_checks_for("ftl-bz-parity-01", FT_PR)
    assert len(run.calls) == 1
