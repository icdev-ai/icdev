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

import os
import subprocess
import sys
import tempfile
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


# --------------------------------------------------------------------------- #
# `run_one(env_extra=...)` and its one consumer, the ungated census (rem-tst-01)
# --------------------------------------------------------------------------- #
# These live HERE rather than in a `tests/test_ungated_test_census.py` of their
# own on purpose. CLAUDE.md requires a NEW test file to be added to
# `args/ci_test_files/core.txt` in the PR that adds it, and rem-tst-01 forbids
# touching either allowlist — the whole point of that task is that it MEASURES
# and promotes nothing. A new file would therefore have been gated by nothing,
# which is the exact debt the census exists to size. This file is already gated,
# and the census is the sole consumer of the `env_extra` hook below, so the
# coverage lands in the one place where CI actually runs it.


def test_env_extra_reaches_the_child_process(repo: Path) -> None:
    """The hook the census needs: per-child environment, or the measurement is noise.

    Concurrent pytest children all writing one `data/icdev.db` produce `database
    is locked` failures that belong to the harness, not the test. The census
    gives each child its own `ICDEV_DB_PATH`; this asserts the value actually
    arrives rather than being silently dropped.
    """
    _write(
        repo / "tests" / "test_reads_env.py",
        "import os\n\n\ndef test_env():\n"
        "    assert os.environ['ICDEV_CENSUS_PROBE'] == 'per-child'\n",
    )
    result = isolation_run.run_one(
        repo, "tests/test_reads_env.py", timeout=180,
        env_extra={"ICDEV_CENSUS_PROBE": "per-child"},
    )
    assert result["status"] == "passed", result["output"]


def test_env_extra_overrides_an_inherited_value(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It must WIN over the inherited environment, not be merged behind it.

    The census overrides PYTHONPATH this way: these sessions run with PYTHONPATH
    pointing at the SHARED checkout, so an unpinned child resolves `import
    tools.x` there and the census would silently measure another worktree's
    source while other sessions edit it.
    """
    monkeypatch.setenv("ICDEV_CENSUS_PROBE", "inherited")
    _write(
        repo / "tests" / "test_reads_env.py",
        "import os\n\n\ndef test_env():\n"
        "    assert os.environ['ICDEV_CENSUS_PROBE'] == 'override'\n",
    )
    result = isolation_run.run_one(
        repo, "tests/test_reads_env.py", timeout=180,
        env_extra={"ICDEV_CENSUS_PROBE": "override"},
    )
    assert result["status"] == "passed", result["output"]


def test_run_one_without_env_extra_is_unchanged(repo: Path) -> None:
    """The default stays None so the changed-test isolation run is untouched."""
    _write(repo / "tests" / "test_plain.py", "def test_plain():\n    assert True\n")
    assert isolation_run.run_one(repo, "tests/test_plain.py", timeout=180)["status"] == "passed"


def test_census_never_reports_no_tests_as_passed() -> None:
    """pytest exit 5 exits 0-ish to anything that only reads "did it fail?".

    A module that collects nothing would look green to a promotion batch and
    widen the allowlist without widening coverage — the same error as counting a
    skip as coverage. `classify` must keep them apart.
    """
    from tools.ci import ungated_test_census as census

    assert census.classify(0, "3 passed in 1.0s") == census.STATUS_PASSED
    assert census.classify(5, "no tests ran in 0.1s") == census.STATUS_NO_TESTS
    assert census.classify(1, "1 failed in 1.0s") == census.STATUS_FAILED


def test_census_keeps_collection_errors_out_of_failed() -> None:
    """A module that does not IMPORT is a different promotion job from a red assert."""
    from tools.ci import ungated_test_census as census

    output = "ERROR collecting tests/test_x.py\nModuleNotFoundError: No module named 'nope'\n"
    assert census.classify(2, output) == census.STATUS_COLLECTION_ERROR
    assert census.classify(1, output) == census.STATUS_COLLECTION_ERROR
    assert census.first_failure_line(output).startswith("ModuleNotFoundError")


def test_census_arithmetic_cannot_claim_more_than_it_measured() -> None:
    """A partial census that looks complete is the defect the whole task is about."""
    from tools.ci import ungated_test_census as census

    honest = {
        "backlog_size": 10, "measured": 4, "not_reached_count": 6,
        "out_of_scope_count": 0,
        "counts": {census.STATUS_PASSED: 4, census.STATUS_NOT_REACHED: 6},
        "results": [{"file": f"tests/test_{i}.py", "status": census.STATUS_PASSED}
                    for i in range(4)],
    }
    assert census.verify(honest) == []

    overclaiming = {**honest, "not_reached_count": 0}
    assert census.verify(overclaiming), "a census missing 6 modules must not verify clean"

    # The most honest partial report there is: the deadline expired before any
    # file finished. measured=0 is a real value, not a missing one.
    nothing_measured = {
        "backlog_size": 10, "measured": 0, "not_reached_count": 10,
        "out_of_scope_count": 0,
        "counts": {census.STATUS_NOT_REACHED: 10},
        "results": [],
    }
    assert census.verify(nothing_measured) == [], (
        "a census that reached nothing and says so must verify clean"
    )


def test_census_run_by_path_imports_siblings_from_this_checkout() -> None:
    """`python tools/ci/ungated_test_census.py` must not measure another checkout.

    The defect this pins, found by running the census for real: invoked as a
    script, `sys.path[0]` is `tools/ci/` and the repo root is not on the path at
    all, so `import tools.ci.isolation_run` resolves through an installed
    `icdev`/`.pth` to the SHARED checkout at `C:\\AI\\ICDev`. The guard it was
    written with — `try: import … except ImportError: sys.path.insert(…)` — never
    fires, because that import SUCCEEDS; it just succeeds against the wrong tree.
    Two consequences, one loud and one silent: `run_one` there has no `env_extra`
    (a TypeError), and `repo_root()` returns the shared checkout, so a census
    would report on this branch while measuring a tree other sessions are editing.

    Asserted in a subprocess with `sys.path[0]` set to the script's own directory,
    because that is the condition the interpreter creates for a script and it
    cannot be reproduced in-process (this test module already puts ROOT on the
    path, which is exactly what masks the bug).
    """
    script = ROOT / "tools" / "ci" / "ungated_test_census.py"

    with tempfile.TemporaryDirectory() as tmp:
        # A DECOY `tools` package standing in for the shared checkout that a
        # `.pth`/installed `icdev` really does make importable on this machine.
        # Injected rather than relied upon, so the test is deterministic on a
        # clean runner where no foreign `tools` happens to be importable.
        decoy = Path(tmp) / "decoy"
        (decoy / "tools" / "ci").mkdir(parents=True)
        (decoy / "tools" / "__init__.py").write_text("", encoding="utf-8")
        (decoy / "tools" / "ci" / "__init__.py").write_text("", encoding="utf-8")
        (decoy / "tools" / "ci" / "isolation_run.py").write_text(
            "def run_one(root, rel, timeout=300, extra=()):\n"
            "    raise AssertionError('decoy checkout was used')\n",
            encoding="utf-8",
        )
        (decoy / "tools" / "ci" / "gated_test_list.py").write_text(
            "from pathlib import Path\n"
            "def repo_root(start=None):\n"
            "    return Path(__file__).resolve().parents[2]\n",
            encoding="utf-8",
        )

        launcher = Path(tmp) / "launcher.py"
        launcher.write_text(
            "import importlib.util, sys\n"
            "from pathlib import Path\n"
            # Exactly what the interpreter gives a script: its OWN directory, and
            # not the repo root. The decoy sits ahead of it, as the shared
            # checkout effectively does. The stdlib entries are kept — only the
            # repo root is withheld, since its presence is what masks the bug.
            f"_root = Path({str(ROOT)!r}).resolve()\n"
            "_rest = [p for p in sys.path if p and Path(p).resolve() != _root]\n"
            f"sys.path[:] = [{str(decoy)!r}, {str(script.parent)!r}] + _rest\n"
            f"spec = importlib.util.spec_from_file_location('census_probe', {str(script)!r})\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(mod)\n"
            "print(sys.modules['tools.ci.isolation_run'].__file__)\n"
            "print(sys.modules['tools.ci.gated_test_list'].__file__)\n",
            encoding="utf-8",
        )

        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        proc = subprocess.run(
            [sys.executable, str(launcher)],
            capture_output=True, text=True, timeout=180, env=env,
        )

    assert proc.returncode == 0, f"census failed to import by path:\n{proc.stderr}"

    printed = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    assert len(printed) == 2, f"expected both sibling paths, got: {proc.stdout!r}"
    for line in printed:
        resolved = Path(line.strip()).resolve()
        assert resolved.is_relative_to(ROOT), (
            f"census imported {resolved}, which is outside this checkout ({ROOT}). "
            "It would measure another worktree's source while reporting on this branch."
        )


def test_census_refuses_a_sibling_already_bound_to_another_checkout() -> None:
    """A path insert loses to `sys.modules`, so the mismatch is checked, not assumed.

    If a parent process or an eagerly-importing `.pth` already bound
    `tools.ci.isolation_run` to a different tree, no `sys.path` change can
    dislodge it. Measuring the wrong tree silently is the one outcome forbidden
    here, so the census must raise rather than proceed.
    """
    import types

    from tools.ci import ungated_test_census as census

    foreign = types.ModuleType("tools.ci.isolation_run")
    foreign.__file__ = str(Path(tempfile.gettempdir()) / "elsewhere" / "isolation_run.py")
    saved = sys.modules.get("tools.ci.isolation_run")
    sys.modules["tools.ci.isolation_run"] = foreign
    try:
        with pytest.raises(census.CensusError, match="refusing to run"):
            census._assert_siblings_are_local()
    finally:
        if saved is not None:
            sys.modules["tools.ci.isolation_run"] = saved
        else:  # pragma: no cover - the module is always imported by now
            del sys.modules["tools.ci.isolation_run"]


def test_census_reads_the_real_backlog_and_measures_nothing_by_default() -> None:
    """The census input is the live file, and `--run` is opt-in."""
    from tools.ci import ungated_test_census as census

    backlog = census.read_backlog(ROOT)
    assert len(backlog) > 1000
    assert all(entry.startswith("tests/") for entry in backlog)
    assert census.main(["--root", str(ROOT)]) == 0
