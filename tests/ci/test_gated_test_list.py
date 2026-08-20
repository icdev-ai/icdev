# CUI // SP-CTI
"""The CI test allowlists resolve, cannot silently shrink, and stay OUT of the
workflow YAML (kax-conflict-07).

.github/workflows/icdev-ci.yml used to carry the gated test list inline as a
shell line-continuation chain. Every task that added a test appended at the same
offset in the same hand-written file, so on 2026-08-09 five open PRs collided on
it AND deadlocked pr_watcher's sibling-conflict guard — which correctly refuses
to co-merge PRs sharing a non-additive file, and had no way to know that one
REGION of that file was additive.

The list moved to args/ci_test_files/*.txt, which `.gitattributes` marks
`merge=union`. These tests pin the three properties that make that safe:

  1. the list still cannot silently shrink (the reason it was explicit at all);
  2. nothing has re-inlined a list into the workflow;
  3. the wiring — union merge driver, pr_watcher classification — is actually in
     place, and the workflow itself is still NOT treated as additive.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest
import yaml

import tools.ci.pr_watcher as pw
from tools.ci.gated_test_list import (
    FLOORS,
    LISTS,
    AllowlistError,
    check,
    shard,
    extract_chains,
    list_path,
    parse,
    repo_root,
    resolve,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "icdev-ci.yml"
# The jobs that actually RUN pytest. `test` is now an aggregator with no
# checkout and no pytest call (crx-test-05), so asserting against it would
# pass vacuously — every "no inline list" check would hold on an empty body.
GATED_JOBS = ("test-shard", "test-windows")


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 1. The lists resolve, and they resolve to something real
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", sorted(LISTS))
def test_shipped_list_is_healthy(name: str):
    report = check(name, ROOT)
    assert report["ok"], report["errors"]
    assert report["count"] >= report["floor"]
    assert report["existence_checked"], "tests/ must exist at the repo root"
    assert not report["missing"]
    assert not report["duplicates"]


def test_repo_root_is_derived_from_file_not_cwd(monkeypatch, tmp_path):
    """cwd drifts in worktrees and on CI runners; resolution must not depend on it."""
    monkeypatch.chdir(tmp_path)
    assert (repo_root() / "args" / "ci_test_files").is_dir()


def test_print_emits_lf_even_on_windows():
    """`print()` translates "\\n" to "\\r\\n" on Windows and `read -r` keeps the CR.

    The consumer then got "tests/foo.py\\r" and pytest reported "file or directory
    not found" for a file that plainly exists — invisible in a log, and invisible
    to the Linux jobs, which is how it reached the windows-latest runner before
    being caught. Must be a SUBPROCESS: stdout translation only happens on a real
    console/pipe, so an in-process call cannot observe it.
    """
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "ci" / "gated_test_list.py"),
         "--print", "--list", "core"],
        capture_output=True, cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert b"\r" not in proc.stdout
    assert proc.stdout.split(b"\n")[0] == b"tests/test_circuit_breaker.py"


def test_parse_ignores_comments_and_blanks():
    text = "# header\n\ntests/a.py\n  tests/b.py  \n# tests/c.py\ntests/d.py # why\n"
    assert parse(text) == ["tests/a.py", "tests/b.py", "tests/d.py"]


# --------------------------------------------------------------------------- #
# 2. The gate cannot silently shrink
# --------------------------------------------------------------------------- #
def _fake_root(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    (tmp_path / "args" / "ci_test_files").mkdir(parents=True)
    (tmp_path / "args" / "ci_test_files" / "core.txt").write_text(body, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    return tmp_path


def test_empty_list_is_a_failure(tmp_path):
    """The whole point of an explicit list: emptying it must go RED, not green."""
    root = _fake_root(tmp_path, "# every entry removed\n\n")
    report = check("core", root)
    assert not report["ok"]
    assert report["count"] == 0
    assert any("ZERO" in e for e in report["errors"])


def test_truncated_list_trips_the_floor(tmp_path):
    root = _fake_root(tmp_path, "tests/a.py\ntests/b.py\n")
    (root / "tests" / "a.py").touch()
    (root / "tests" / "b.py").touch()
    report = check("core", root)
    assert not report["ok"]
    assert any("below the floor" in e for e in report["errors"])


def test_missing_path_is_a_failure(tmp_path):
    """A renamed-away test must not become a silently skipped test."""
    body = "".join(f"tests/t{i}.py\n" for i in range(FLOORS["core"] + 1))
    root = _fake_root(tmp_path, body)
    for i in range(FLOORS["core"] + 1):
        if i != 3:
            (root / "tests" / f"t{i}.py").touch()
    report = check("core", root)
    assert not report["ok"]
    assert report["missing"] == ["tests/t3.py"]


def test_duplicate_entry_is_a_failure(tmp_path):
    """What a careless union merge leaves behind when both sides add the same row."""
    body = "".join(f"tests/t{i}.py\n" for i in range(FLOORS["core"] + 1)) + "tests/t0.py\n"
    root = _fake_root(tmp_path, body)
    for i in range(FLOORS["core"] + 1):
        (root / "tests" / f"t{i}.py").touch()
    report = check("core", root)
    assert not report["ok"]
    assert report["duplicates"] == ["tests/t0.py"]


def test_missing_list_file_raises_rather_than_resolving_empty(tmp_path):
    """An absent file must never look like "no tests configured"."""
    (tmp_path / "args" / "ci_test_files").mkdir(parents=True)
    with pytest.raises(AllowlistError):
        resolve("core", tmp_path)


def test_floors_cover_every_list():
    assert set(FLOORS) == set(LISTS)
    for name in LISTS:
        assert FLOORS[name] >= 1, "a floor of 0 would let the list empty itself"
        assert list_path(name, ROOT).is_file()


# --------------------------------------------------------------------------- #
# 3. Nothing has re-inlined a list into the workflow
# --------------------------------------------------------------------------- #
def test_no_inline_pytest_list_in_the_workflow(workflow_text: str):
    """A multi-target pytest chain in the YAML is the hot-file problem returning.

    A single-target invocation is fine (the /knowledge-search retry step is one);
    a LIST is what made this file a conflict magnet.
    """
    offenders = {
        job: chain
        for job in GATED_JOBS
        for chain in extract_chains(workflow_text, job)
        if len(chain) > 1
    }
    assert not offenders, (
        "an inline pytest list is back in icdev-ci.yml — append to "
        "args/ci_test_files/*.txt instead: " + repr(offenders)
    )


@pytest.mark.parametrize(
    "job,list_name", [("test-shard", "core"), ("test-windows", "windows")]
)
def test_gated_jobs_resolve_their_list_and_check_it(job: str, list_name: str,
                                                    workflow_text: str):
    """Each job must both --check the list and feed it to pytest.

    --check without a run proves nothing; a run without --check gives up the
    cannot-silently-shrink property that justified the explicit list.
    """
    steps = yaml.safe_load(workflow_text)["jobs"][job]["steps"]
    body = "\n".join(str(s.get("run", "")) for s in steps)
    assert f"--check --list {list_name}" in body
    assert f"--print --list {list_name}" in body
    assert f"args/ci_test_files/{list_name}.txt" in body


def test_workflow_refuses_an_empty_resolved_array(workflow_text: str):
    """`pytest "${EMPTY[@]}" -v` collects the WHOLE suite — guard it in-shell too."""
    for job in GATED_JOBS:
        steps = yaml.safe_load(workflow_text)["jobs"][job]["steps"]
        body = "\n".join(str(s.get("run", "")) for s in steps)
        assert "-eq 0 ]" in body and "refusing to run pytest" in body, job


# --------------------------------------------------------------------------- #
# 4. The wiring that makes co-appends actually mergeable
# --------------------------------------------------------------------------- #
def test_gitattributes_union_merges_the_lists():
    text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "args/ci_test_files/*.txt merge=union" in text


def test_pr_watcher_excludes_the_lists_but_not_the_workflow():
    """The list is additive; the workflow around it is emphatically not.

    Marking the whole workflow additive would let two PRs editing the same job's
    `run:` block merge unserialized — a real collision the guard exists to catch.
    """
    assert pw._is_additive_path("args/ci_test_files/core.txt")
    assert pw._is_additive_path("args/ci_test_files/windows.txt")
    assert not pw._is_additive_path(".github/workflows/icdev-ci.yml")
    assert not pw._is_additive_path(".github/workflows/pr-watcher.yml")


def test_two_prs_sharing_only_the_list_are_not_siblings():
    """The end-to-end shape of the 2026-08-09 deadlock, with the fix in place."""
    w = pw.PRWatcher(config={}, get_connection=lambda: None)
    file_map = {
        "https://x/pull/1": {"args/ci_test_files/core.txt", "tests/a/one.py"},
        "https://x/pull/2": {"args/ci_test_files/core.txt", "tests/b/two.py"},
    }
    assert w._sibling_conflicts("https://x/pull/1", file_map) == {}


def test_two_prs_sharing_the_workflow_are_still_held():
    """The protection this task deliberately kept."""
    w = pw.PRWatcher(config={}, get_connection=lambda: None)
    shared = ".github/workflows/icdev-ci.yml"
    file_map = {
        "https://x/pull/1": {shared, "tools/a/one.py"},
        "https://x/pull/2": {shared, "tools/b/two.py"},
    }
    assert shared in w._sibling_conflicts("https://x/pull/1", file_map).get(
        "https://x/pull/2", set()
    )


# --------------------------------------------------------------------------- #
# 5. The packaged copy exists (refreshed by sync_package_tree, not by hand)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", sorted(LISTS))
def test_packaged_copy_is_present_and_parses(name: str):
    """Byte-identity is deliberately NOT asserted.

    `tools/installer/sync_package_tree.py` refreshes icdev/data/args/ at release.
    Requiring a hand-sync would make every test addition a two-file edit again —
    exactly the cost this task removed. What must hold is that the wheel ships a
    usable list rather than nothing.
    """
    mirrored = ROOT / "icdev" / "data" / "args" / "ci_test_files" / LISTS[name]
    assert mirrored.is_file()
    assert len(parse(mirrored.read_text(encoding="utf-8"))) >= FLOORS[name]


# ── Sharding (crx-test-05) ─────────────────────────────────────────────────
# The `Test` check was 33m43s, 31m15s of it one pytest call over 436 files.
# Sharding splits that across runners. These pin the properties that make the
# split SAFE — a partition bug here silently stops running tests, which is the
# one failure this module exists to prevent.
_SHARD_NS = (1, 2, 3, 4, 6, 8)


def _entries():
    return resolve("core")


@pytest.mark.parametrize("n", _SHARD_NS)
def test_shards_are_a_partition(n):
    """Every file runs exactly once: union == input, and pairwise disjoint.

    Both halves matter and they fail differently. A gap means files silently
    stop being tested — the gate shrinks and nothing says so. An overlap means
    a file runs twice, which wastes time AND breaks the JUnit union argument
    the per-shard skip census rests on.
    """
    entries = _entries()
    parts = [shard(entries, k, n) for k in range(1, n + 1)]
    assert sum(len(p) for p in parts) == len(entries), "a file was dropped or duplicated"
    union = set()
    for p in parts:
        assert not (union & set(p)), "shards overlap — a file would run twice"
        union |= set(p)
    assert union == set(entries)


@pytest.mark.parametrize("n", _SHARD_NS)
def test_shards_are_count_balanced(n):
    """Round-robin balances counts to +/-1. Count is the only duration proxy
    available until per-file timings are recorded, so an imbalance here is an
    imbalance in wall-clock."""
    entries = _entries()
    sizes = [len(shard(entries, k, n)) for k in range(1, n + 1)]
    assert max(sizes) - min(sizes) <= 1


@pytest.mark.parametrize("n", _SHARD_NS)
def test_a_shard_preserves_resolve_order(n):
    """A shard is a SUBSEQUENCE of the resolved list, so within a shard pytest
    still sees the documented order. Reordering would be a second, invisible
    variable on top of the split."""
    entries = _entries()
    for k in range(1, n + 1):
        part = shard(entries, k, n)
        assert part == [e for e in entries if e in set(part)]


def test_sharding_is_stable_across_PROCESSES():
    """THE PYTHONHASHSEED REGRESSION. If the partition ever used the builtin
    `hash()`, two shard jobs in two processes would disagree about which files
    belong to them and files would silently go unrun. Same-process equality
    cannot catch that; two interpreters can."""
    import json
    import os

    code = (
        "import json,sys;"
        "sys.path.insert(0, r'%s');"
        "from tools.ci.gated_test_list import resolve, shard;"
        "e=resolve('core');"
        "print(json.dumps([shard(e,k,4) for k in range(1,5)]))"
    ) % str(repo_root())
    runs = []
    for seed in ("0", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             timeout=300, env=env, check=True).stdout
        runs.append(json.loads(out))
    assert runs[0] == runs[1], "the partition depends on PYTHONHASHSEED"


def test_a_pinned_group_lands_in_one_shard():
    """The escape hatch for order dependence. No pins are needed today — a
    4-shard characterisation over the real list produced ZERO order-dependent
    failures — but the mechanism must work the day one is."""
    entries = ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py"]
    groups = [["a.py", "d.py", "f.py"]]
    parts = [shard(entries, k, 3, groups) for k in range(1, 4)]
    home = [p for p in parts if "a.py" in p][0]
    assert {"a.py", "d.py", "f.py"} <= set(home)
    assert sum(len(p) for p in parts) == len(entries)


def test_an_out_of_range_shard_raises():
    entries = _entries()
    for bad in ((0, 4), (5, 4), (1, 0)):
        with pytest.raises(ValueError):
            shard(entries, *bad)


def test_check_validates_the_FULL_list_not_the_shard():
    """The floor guards against the allowlist silently shrinking. A correct
    73-file shard of a 438-entry list must not be measured against a floor
    meant for 438 — and a DERIVED per-shard floor would be strictly weaker (a
    110-file shard could lose 90 files and still clear a floor of 20)."""
    report = check("core", shard_spec=(1, 6))
    assert report["ok"], report["errors"]
    assert report["shard"] == "1/6"
    assert report["shard_count"] < report["total_count"]
    assert report["floor"] == FLOORS["core"]


def test_an_unsharded_check_is_unchanged():
    """Back-compat: every existing caller passes no shard and must see exactly
    what it saw before, with no shard keys in the report."""
    report = check("core")
    assert "shard" not in report and "shard_count" not in report
    assert report["count"] == len(_entries())


# --------------------------------------------------------------------------- #
# 6. The aggregator wiring (crx-test-05)
#
# `Test` and `E2E (Playwright)` are REQUIRED check names. Sharding split the work
# across matrix jobs and left a job whose only purpose is to carry that name and
# report the shards' verdict. Every failure mode of that pattern is silently
# GREEN, which is why it is pinned here rather than left to review.
# --------------------------------------------------------------------------- #
AGGREGATORS = {
    "test": ("Test", ("test-gates", "test-shard")),
    "e2e": ("E2E (Playwright)", ("e2e-shard",)),
}


@pytest.mark.parametrize("job", sorted(AGGREGATORS))
def test_aggregator_preserves_its_required_check_name(job: str, workflow_text: str):
    """Branch protection matches on the check NAME, not the job id."""
    expected_name, _ = AGGREGATORS[job]
    doc = yaml.safe_load(workflow_text)
    assert doc["jobs"][job]["name"] == expected_name


@pytest.mark.parametrize("job", sorted(AGGREGATORS))
def test_aggregator_runs_even_when_a_dependency_fails(job: str, workflow_text: str):
    """`if: always()` is MANDATORY, and its absence is invisible.

    Without it GitHub SKIPS the aggregator when a dependency fails. A skipped
    check is not a failed one — branch protection never receives a verdict, and
    the PR sits on "Expected — Waiting for status" with no red anywhere.
    """
    doc = yaml.safe_load(workflow_text)
    assert str(doc["jobs"][job].get("if", "")).strip() == "always()"


@pytest.mark.parametrize("job", sorted(AGGREGATORS))
def test_aggregator_needs_every_job_it_reports_on(job: str, workflow_text: str):
    _, required_needs = AGGREGATORS[job]
    doc = yaml.safe_load(workflow_text)
    needs = doc["jobs"][job]["needs"]
    needs = [needs] if isinstance(needs, str) else list(needs)
    assert set(required_needs) <= set(needs)


@pytest.mark.parametrize("job", sorted(AGGREGATORS))
def test_aggregator_asserts_success_rather_than_enumerating_failure(
    job: str, workflow_text: str
):
    """`!= success`, NEVER `== failure`.

    For a matrix job `needs.<job>.result` is a single aggregated value, and a
    CANCELLED shard is not `failure` — so enumerating failure reports GREEN on a
    suite that never finished. Every dependency's result must be checked.
    """
    doc = yaml.safe_load(workflow_text)
    body = "\n".join(str(s.get("run", "")) for s in doc["jobs"][job]["steps"])
    assert '!= "success"' in body, job
    assert '== "failure"' not in body, (
        f"{job} enumerates failure; a cancelled shard would report green"
    )
    _, required_needs = AGGREGATORS[job]
    env = {}
    for step in doc["jobs"][job]["steps"]:
        env.update(step.get("env") or {})
    referenced = " ".join(str(v) for v in env.values())
    for dep in required_needs:
        assert f"needs.{dep}.result" in referenced, (
            f"{job} does not read {dep}'s result — that job could fail unnoticed"
        )


@pytest.mark.parametrize("job", ["test-shard", "e2e-shard"])
def test_shard_matrix_is_contiguous_and_one_based(job: str, workflow_text: str):
    """`strategy.job-total` derives N from this list, so a gap in it silently
    drops test files from the run: shard 3 of 4 would be computed while only
    three jobs exist."""
    doc = yaml.safe_load(workflow_text)
    shards = doc["jobs"][job]["strategy"]["matrix"]["shard"]
    assert shards == list(range(1, len(shards) + 1)), shards


@pytest.mark.parametrize("job", ["test-shard", "e2e-shard"])
def test_a_failing_shard_does_not_cancel_its_siblings(job: str, workflow_text: str):
    """With fail-fast the first failure hides the other shards, so a PR breaking
    tests in three shards costs three round trips instead of one."""
    doc = yaml.safe_load(workflow_text)
    assert doc["jobs"][job]["strategy"]["fail-fast"] is False


def test_e2e_no_longer_waits_for_the_unit_suite(workflow_text: str):
    """The unblock is the single largest wall-clock win in crx-test-05 and is one
    edit away from being silently reverted by a merge."""
    doc = yaml.safe_load(workflow_text)
    needs = doc["jobs"]["e2e-shard"]["needs"]
    needs = [needs] if isinstance(needs, str) else list(needs)
    assert "test" not in needs and "test-shard" not in needs, (
        "e2e-shard is behind the unit suite again — that costs ~17.5 min of "
        "pure wall-clock on every PR"
    )


def test_downstream_jobs_still_gate_on_the_aggregator(workflow_text: str):
    """docker-build and two-tier-build must NOT build from a red tree. They
    declare `needs: [test]`; keeping the aggregator's job id `test` is what
    makes that keep meaning "the whole unit suite passed"."""
    doc = yaml.safe_load(workflow_text)
    for job in ("docker-build", "two-tier-build"):
        needs = doc["jobs"][job]["needs"]
        needs = [needs] if isinstance(needs, str) else list(needs)
        assert "test" in needs, job
