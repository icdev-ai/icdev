# CUI // SP-CTI
"""Tests for tools/ci/isolation_run.py — the changed-file isolation run (trust-disc-02).

The module under test answers two questions, and both are tested here against a
REAL throwaway git repository rather than a mocked `git`:

  1. Which test files did this branch change, and does CI run each one in-suite?
  2. Does each of them still pass when it is the only thing in the process?

The second question is the whole point, so the headline test builds the exact
defect shape that motivated the task — a file that passes with a neighbour and
fails alone — and asserts the runner turns red on it. That is the acceptance
criterion, expressed as a test rather than as a claim.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ci import isolation_run  # noqa: E402


GATE_CONFIG = """\
scope:
  roots:
    - tests/
  patterns:
    - "test_*.py"
exclusions: []
backlog_file: args/ci_test_backlog.txt
backlog_max: 100
"""


def _git(repo: Path, *args: str) -> None:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=False, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" deliberately: a trailing CR in an allowlist makes every
    # consumer report "file not found" for a path that plainly exists.
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repo shaped like ICDEV: args/ policy files plus tests/.

    `main` holds one committed test file; the caller adds more and commits them
    on a branch, which is what `resolve()` is asked to describe.
    """
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    _write(repo / "args" / "test_gating_gate.yaml", GATE_CONFIG)
    _write(repo / "args" / "ci_test_backlog.txt", "# census\n")
    _write(repo / "args" / "ci_test_files" / "core.txt", "tests/test_base.py\n")
    _write(repo / "args" / "ci_test_files" / "windows.txt", "# none\n")
    _write(repo / "tests" / "test_base.py", "def test_base():\n    assert True\n")

    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "ci@example.invalid")
    _git(repo, "config", "user.name", "CI")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    return repo


# --------------------------------------------------------------------------- #
# Resolving the changed set
# --------------------------------------------------------------------------- #
def test_no_changes_resolves_to_no_files(repo: Path) -> None:
    report = isolation_run.resolve(repo, base="main")
    assert report["files"] == []
    assert report["base"] == "main"


def test_only_test_files_are_collected(repo: Path) -> None:
    """A branch touching a source file and a test file yields only the test file."""
    _git(repo, "checkout", "-b", "feature")
    _write(repo / "tools" / "thing.py", "VALUE = 1\n")
    _write(repo / "tests" / "test_thing.py", "def test_thing():\n    assert True\n")
    _write(repo / "tests" / "helpers.py", "X = 1\n")  # not `test_*.py`
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feature")

    report = isolation_run.resolve(repo, base="main")
    assert report["files"] == ["tests/test_thing.py"]


def test_uncommitted_edits_are_included(repo: Path) -> None:
    """The local case: the file you just edited has not been committed yet."""
    _write(repo / "tests" / "test_base.py", "def test_base():\n    assert True\n\n# edit\n")
    report = isolation_run.resolve(repo, base="main")
    assert report["files"] == ["tests/test_base.py"]


def test_untracked_new_test_file_is_included(repo: Path) -> None:
    """`git diff` never reports an untracked file, and a new test is untracked.

    That is precisely when running it alone is most useful, so leaving it out
    would print "this branch changes no test file" to someone who had just
    written one.
    """
    _write(repo / "tests" / "test_brand_new.py", "def test_new():\n    assert True\n")
    report = isolation_run.resolve(repo, base="main")
    assert report["files"] == ["tests/test_brand_new.py"]


def test_an_untracked_file_already_in_core_txt_reads_as_gated(repo: Path) -> None:
    """The census is `git ls-files`; a file added to core.txt but not yet
    `git add`ed must not be reported as one CI never runs."""
    _write(repo / "tests" / "test_brand_new.py", "def test_new():\n    assert True\n")
    _write(repo / "args" / "ci_test_files" / "core.txt",
           "tests/test_base.py\ntests/test_brand_new.py\n")
    report = isolation_run.resolve(repo, base="main")
    assert report["gated"] == ["tests/test_brand_new.py"]
    assert report["ungated"] == []


def test_gitignored_files_are_not_collected(repo: Path) -> None:
    """`--exclude-standard`: a scratch file in an ignored directory is not the PR's."""
    _write(repo / ".gitignore", "tests/scratch/\n")
    _write(repo / "tests" / "scratch" / "test_scratch.py", "def test_s():\n    assert True\n")
    report = isolation_run.resolve(repo, base="main")
    assert report["files"] == []


def test_gated_and_ungated_are_reported_separately(repo: Path) -> None:
    """core.txt membership decides which half of the report a file lands in."""
    _git(repo, "checkout", "-b", "feature")
    _write(repo / "tests" / "test_new.py", "def test_new():\n    assert True\n")
    _write(repo / "tests" / "test_base.py", "def test_base():\n    assert True\n# e\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feature")

    report = isolation_run.resolve(repo, base="main")
    assert report["gated"] == ["tests/test_base.py"]
    assert report["ungated"] == ["tests/test_new.py"]


def test_deleted_file_is_not_offered_to_pytest(repo: Path) -> None:
    """Committed on the branch, deleted in the working tree: pytest cannot run it.

    The two diffs have different endpoints, so the commit still reports the add
    while the working-tree diff no longer mentions the file at all.
    """
    _git(repo, "checkout", "-b", "feature")
    _write(repo / "tests" / "test_ephemeral.py", "def test_x():\n    assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add")
    (repo / "tests" / "test_ephemeral.py").unlink()

    report = isolation_run.resolve(repo, base="main")
    assert report["files"] == []
    assert report["vanished"] == ["tests/test_ephemeral.py"]


def test_a_named_base_that_does_not_resolve_raises(repo: Path) -> None:
    """An explicit --base is authoritative: a typo must not fall back to `main`.

    "no files changed vs main" and "your --base was a typo" read identically, and
    only the second leaves a real regression unrun.
    """
    with pytest.raises(isolation_run.ResolutionError):
        isolation_run.resolve(repo, base="refs/heads/does-not-exist")


def test_cli_exits_2_when_the_changed_set_cannot_be_resolved(repo: Path) -> None:
    """Exit 2, never 0 — a step that ran nothing must not read as green."""
    code = isolation_run.main(
        ["--root", str(repo), "--base", "refs/heads/does-not-exist", "--run"]
    )
    assert code == 2


# --------------------------------------------------------------------------- #
# Running them alone — the acceptance shape
# --------------------------------------------------------------------------- #
#: Two modules with the defect this task exists to catch. `test_follower` reads
#: interpreter state that exists only because `test_leader` was collected into the
#: same process, so the pair passes together and the follower fails when it is the
#: only thing in the run. It is the same failure as registering a blueprint onto
#: the shared `tools.dashboard.app` singleton, reduced to something that runs in a
#: second — and the dependency lives in `sys.modules` rather than on disk, so it
#: cannot leak between the runs this file itself performs.
_LEADER = """\
def test_leader():
    assert True
"""

_FOLLOWER = """\
import sys
def test_follower():
    assert "test_leader" in sys.modules, "only passes when the leader was collected too"
"""


def _seed_order_dependent_pair(repo: Path, *, gated: bool) -> None:
    _git(repo, "checkout", "-b", "feature")
    _write(repo / "tests" / "test_leader.py", _LEADER)
    _write(repo / "tests" / "test_follower.py", _FOLLOWER)
    if gated:
        _write(
            repo / "args" / "ci_test_files" / "core.txt",
            "tests/test_base.py\ntests/test_leader.py\ntests/test_follower.py\n",
        )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "order-dependent pair")


def test_pair_passes_together(repo: Path) -> None:
    """Premise check: in-suite, the pair is green. That is why nothing goes red today."""
    _seed_order_dependent_pair(repo, gated=True)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_leader.py", "tests/test_follower.py",
         "-q", "-p", "no:cacheprovider"],
        cwd=str(repo), capture_output=True, text=True, check=False,
        encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.timeout(300)
def test_gated_order_dependent_file_fails_alone(repo: Path) -> None:
    """THE acceptance criterion: an order-dependent gated file turns the run red."""
    _seed_order_dependent_pair(repo, gated=True)
    report = isolation_run.run(repo, base="main", timeout=180)

    assert report["ok"] is False
    assert report["fatal"] == ["tests/test_follower.py"]
    assert report["advisory"] == []
    by_file = {r["file"]: r for r in report["results"]}
    assert by_file["tests/test_leader.py"]["status"] == "passed"
    assert by_file["tests/test_follower.py"]["status"] == "failed"


@pytest.mark.timeout(300)
def test_cli_run_exits_1_on_a_gated_isolation_failure(repo: Path) -> None:
    _seed_order_dependent_pair(repo, gated=True)
    assert isolation_run.main(["--root", str(repo), "--base", "main", "--run"]) == 1


@pytest.mark.timeout(300)
def test_ungated_failure_is_advisory_not_fatal(repo: Path) -> None:
    """A file in no allowlist has never gated a merge; it may be red already.

    Failing on those would red-light PRs for pre-existing debt and the step would
    get a `|| true` bolted onto it. It is run and reported, not enforced.
    """
    _seed_order_dependent_pair(repo, gated=False)
    report = isolation_run.run(repo, base="main", timeout=180)

    assert report["ok"] is True
    assert report["fatal"] == []
    assert report["advisory"] == ["tests/test_follower.py"]
    assert isolation_run.main(["--root", str(repo), "--base", "main", "--run"]) == 0


@pytest.mark.timeout(300)
def test_self_sufficient_changed_files_pass(repo: Path) -> None:
    """The ordinary PR: changed tests that stand alone are green and cost one run each."""
    _git(repo, "checkout", "-b", "feature")
    _write(repo / "tests" / "test_solo.py", "def test_solo():\n    assert 1 + 1 == 2\n")
    _write(repo / "args" / "ci_test_files" / "core.txt",
           "tests/test_base.py\ntests/test_solo.py\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "solo")

    report = isolation_run.run(repo, base="main", timeout=180)
    assert report["ok"] is True
    assert report["failures"] == []
    assert [r["file"] for r in report["results"]] == ["tests/test_solo.py"]


@pytest.mark.timeout(300)
def test_a_file_that_collects_nothing_alone_is_a_failure(repo: Path) -> None:
    """pytest exit 5 is "no tests collected" — alone, that is a real finding."""
    _git(repo, "checkout", "-b", "feature")
    _write(repo / "tests" / "test_empty.py", "# no tests here\n")
    _write(repo / "args" / "ci_test_files" / "core.txt",
           "tests/test_base.py\ntests/test_empty.py\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "empty")

    report = isolation_run.run(repo, base="main", timeout=180)
    assert report["ok"] is False
    assert report["fatal"] == ["tests/test_empty.py"]


# --------------------------------------------------------------------------- #
# Base-ref resolution
# --------------------------------------------------------------------------- #
def test_github_base_ref_is_honoured(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """pull_request events set GITHUB_BASE_REF to the TARGET branch."""
    _git(repo, "checkout", "-b", "feature")
    monkeypatch.setenv("GITHUB_BASE_REF", "main")
    assert isolation_run.resolve_base(repo) == "main"


def test_explicit_base_wins_over_the_environment(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _git(repo, "checkout", "-b", "feature")
    monkeypatch.setenv("GITHUB_BASE_REF", "main")
    monkeypatch.setenv("ICDEV_ISOLATION_BASE", "main")
    assert isolation_run.resolve_base(repo, "HEAD") == "HEAD"


# --------------------------------------------------------------------------- #
# The gap this task closes — asserted against the real repo, not described
# --------------------------------------------------------------------------- #
def test_ci_wires_the_isolation_run() -> None:
    """The tool is worthless unmounted; assert the workflow actually calls it.

    Also asserts the absence of a shell neutraliser, for the same reason
    `.claude/settings.json` may not wrap the PreToolUse hook in `|| true`: a gate
    whose exit code the shell discards is decoration.
    """
    workflow = (ROOT / ".github" / "workflows" / "icdev-ci.yml").read_text(encoding="utf-8")
    assert "tools/ci/isolation_run.py --run" in workflow
    for line in workflow.splitlines():
        if "isolation_run.py" in line:
            assert "|| true" not in line, "the isolation run must be able to go red"


def test_isolation_run_job_checks_out_full_history() -> None:
    """A shallow checkout has no merge base, so the runner would exit 2 every time."""
    workflow = (ROOT / ".github" / "workflows" / "icdev-ci.yml").read_text(encoding="utf-8")
    assert "fetch-depth: 0" in workflow
