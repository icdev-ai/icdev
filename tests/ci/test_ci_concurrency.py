# [TEMPLATE: CUI // SP-CTI]
"""ICDEV CI runs ONE run per ref and a newer push cancels the older (mfx-ci-02).

MEASURED 2026-09-03 23:30-00:30 UTC: eight ICDEV CI runs queued behind one in
progress, two of them for commits a newer push on the same branch had already
superseded, and an operator cancelled the dead runs by hand to move a green PR
forward ~35 minutes. `.github/workflows/icdev-ci.yml` declared no
`concurrency:` at all.

What this pins, and why each half exists:

* The workflow declares a WORKFLOW-LEVEL concurrency group keyed on
  `github.ref` with `cancel-in-progress: true`. A job-level group would carve
  that job OUT of the workflow's group (the job keeps queueing while the rest
  of the run is cancelled), so no job may declare its own.
* The group is namespaced by workflow name. Two workflows sharing a group
  string cancel EACH OTHER, and nothing goes red when they do -- the shard
  timing refresh would simply never finish.
* Every workflow that runs Playwright carries the same declaration. Today that
  is icdev-ci.yml itself (the E2E shards live there); this keeps a future
  split-out Playwright workflow honest.
* The consumers of a run's outcome keep `cancelled` apart from `failure`:
  shard-timings.yml reads only a SUCCESSFUL default-branch run, and the merge
  ladder reads the PR's HEAD rollup, in which a cancelled check is red for THAT
  sha and cannot make the head sha read green.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
CI = WORKFLOWS / "icdev-ci.yml"
SHARD_TIMINGS = WORKFLOWS / "shard-timings.yml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _workflow_files():
    return sorted(p for p in WORKFLOWS.iterdir()
                  if p.suffix in (".yml", ".yaml") and p.name != "interface_validation_steps.yaml")


# ── The declaration ────────────────────────────────────────────────────────

def test_icdev_ci_declares_a_workflow_level_concurrency_group_per_ref():
    doc = _load(CI)
    conc = doc.get("concurrency")
    assert isinstance(conc, dict), (
        "icdev-ci.yml declares no workflow-level `concurrency:` -- a superseded "
        "push queues a dead run behind the live one (mfx-ci-02)")
    group = str(conc.get("group", ""))
    assert "${{ github.ref }}" in group, (
        f"the group must be keyed on github.ref so each ref is its own queue; got {group!r}")
    # `github.ref`, not `github.head_ref`: head_ref is EMPTY on a push event, so
    # every main push would share one group string and cancel each other only
    # by accident of that emptiness; ref is populated for both event kinds.
    assert "github.head_ref" not in group
    assert conc.get("cancel-in-progress") is True, (
        "cancel-in-progress must be the boolean true -- a queued group without "
        "cancellation still runs the dead sha, it merely runs it later")


def test_the_group_is_namespaced_by_the_workflow():
    group = str(_load(CI)["concurrency"]["group"])
    prefix = group.split("${{")[0].strip("-").strip()
    assert prefix, "the group needs a workflow-specific prefix before the ref"
    assert prefix.startswith("icdev-ci"), prefix


def test_no_job_carves_itself_out_of_the_group():
    doc = _load(CI)
    leaks = [name for name, job in doc["jobs"].items()
             if isinstance(job, dict) and "concurrency" in job]
    assert not leaks, (
        f"job-level concurrency on {leaks}: those jobs leave the workflow group "
        "and keep queueing for a sha the rest of the run has abandoned")


def test_no_two_workflows_share_a_concurrency_group():
    seen = {}
    for path in _workflow_files():
        doc = _load(path)
        conc = doc.get("concurrency") if isinstance(doc, dict) else None
        if not isinstance(conc, dict):
            continue
        group = str(conc.get("group", ""))
        assert group not in seen, (
            f"{path.name} and {seen[group]} share concurrency group {group!r} "
            "and would cancel each other")
        seen[group] = path.name
    assert CI.name in seen.values()


def test_every_workflow_that_runs_playwright_is_in_a_concurrency_group():
    """The card asks for the Playwright workflow too. Today it is not separate
    -- the E2E shards are jobs of icdev-ci.yml -- so this finds every workflow
    that invokes Playwright and requires the declaration on each, which is what
    keeps a later split honest without hard-coding a file name."""
    runners = []
    for path in _workflow_files():
        text = path.read_text(encoding="utf-8")
        if not re.search(r"npx\s+playwright\s+test", text):
            continue
        runners.append(path.name)
        doc = _load(path)
        assert isinstance(doc.get("concurrency"), dict), (
            f"{path.name} runs Playwright and declares no concurrency group")
        assert doc["concurrency"].get("cancel-in-progress") is True, path.name
    assert CI.name in runners, "icdev-ci.yml no longer runs Playwright?"


# ── The consumers ──────────────────────────────────────────────────────────

def test_shard_timings_reads_only_a_successful_run():
    """A cancelled main run is NEITHER a success nor a failure (crx-test-06's
    five outcomes). The weekly refresh must never read one as a measurement:
    its shards aborted mid-suite, so its JUnit is partial exactly the way a
    failed run's is."""
    doc = _load(SHARD_TIMINGS)
    steps = doc["jobs"]["refresh"]["steps"]
    pick = next(s for s in steps if s.get("id") == "pick")
    # Join shell line continuations first so the whole invocation is one line.
    script = pick["run"].replace("\\\n", " ")
    m = re.search(r"gh run list[^\n]*", script)
    assert m, "the pick step no longer calls `gh run list`"
    invocation = m.group(0)
    assert "--status success" in invocation, (
        "the pick step must ask for --status success; a cancelled or failed run "
        "would be folded into the snapshot as a partial measurement")


def test_a_cancelled_check_on_a_sha_never_reads_green_for_that_sha():
    """The merge ladder and the done door read `statusCheckRollup`, which
    GitHub scopes to the PR's HEAD sha: the run cancelled on the OLD sha is
    simply absent from the new head's rollup. For the sha it IS on, a cancelled
    check is not green -- the run never finished -- and it is not `in_progress`
    either, so the ladder cannot wait forever on a run nothing will resume."""
    from tools.ci import error_classifier as ec
    from tools.ci.merge_readiness import CI_FAILED, READY, classify_merge_readiness

    old_sha = {"statusCheckRollup": [
        {"name": "Test", "conclusion": "CANCELLED"},
    ]}
    assert not ec.is_passing(old_sha)
    assert not ec.is_in_progress(old_sha)
    assert ec.is_ci_failed(old_sha)

    base = {"state": "OPEN", "isDraft": False, "mergeable": "MERGEABLE",
            "baseRefName": "main", "labels": [], "reviews": [], "url": "u"}
    verdict = classify_merge_readiness({**base, **old_sha}, default_branch="main")
    assert verdict.state == CI_FAILED
    assert "Test" in verdict.reason

    # The new head's rollup carries the new run only; the cancellation on the
    # superseded sha has no route into it.
    new_sha = {**base, "statusCheckRollup": [
        {"name": "Lint", "conclusion": "SUCCESS"},
        {"name": "Test", "conclusion": "SUCCESS"},
    ]}
    assert classify_merge_readiness(new_sha, default_branch="main").state == READY
