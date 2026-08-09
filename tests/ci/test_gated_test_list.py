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
    extract_chains,
    list_path,
    parse,
    repo_root,
    resolve,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "icdev-ci.yml"
GATED_JOBS = ("test", "test-windows")


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
    "job,list_name", [("test", "core"), ("test-windows", "windows")]
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
