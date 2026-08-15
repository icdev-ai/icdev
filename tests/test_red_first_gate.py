# CUI // SP-CTI
"""Red-first proof gate (trust-disc-01).

The acceptance criterion is empirical, so most of this file builds a throwaway
git repository and runs the real gate over it: a deliberately non-discriminating
test must FAIL the gate, and a genuine red-first test must PASS it. Asserting
that on mocks would be the exact defect the gate exists to catch.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.ci import red_first_gate as rfg
from tools.security.reproduction_validator import (
    REPLAY_VOCABULARY,
    DiscriminationVocabulary,
    decide_discrimination,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# The shared decision table
# --------------------------------------------------------------------------- #
VOCAB = DiscriminationVocabulary(
    discriminating="D", tautology="T", inverted="I",
    never_established="N", indecisive="X:{before}/{after}",
)


@pytest.mark.parametrize(
    ("before_fired", "after_fired", "expect_ok", "expect_reason"),
    [
        (True, False, True, "D"),    # fired before, quiet after — the only pass
        (True, True, False, "T"),    # fires in both states: a tautology
        (False, True, False, "I"),   # fires only after: inverted polarity
        (False, False, False, "N"),  # fires in neither: never established
    ],
)
def test_decision_table_is_exhaustive(before_fired, after_fired, expect_ok, expect_reason):
    ok, reason = decide_discrimination(
        before_fired=before_fired, before_decisive=True, before_outcome="x",
        after_fired=after_fired, after_decisive=True, after_outcome="y",
        vocabulary=VOCAB,
    )
    assert (ok, reason) == (expect_ok, expect_reason)


@pytest.mark.parametrize(("before_ok", "after_ok"), [(False, True), (True, False), (False, False)])
def test_indecisive_run_never_yields_discriminating(before_ok, after_ok):
    """Absence of evidence is never evidence of absence, in either direction."""
    ok, reason = decide_discrimination(
        before_fired=True, before_decisive=before_ok, before_outcome="timeout",
        after_fired=False, after_decisive=after_ok, after_outcome="passed",
        vocabulary=VOCAB,
    )
    assert ok is False
    assert reason == "X:timeout/passed"


def test_replay_vocabulary_wording_is_unchanged():
    """The HTTP replay caller must read exactly as it did before the extraction.

    These four strings are asserted verbatim because reproduction_validator's own
    suite and `finding_verify_discrimination` consumers read them; refactoring the
    decision out from under them must be behaviour-preserving prose and all.
    """
    assert REPLAY_VOCABULARY.discriminating == (
        "reproduction fired against the vulnerable target and stopped firing once the fix was applied"
    )
    assert REPLAY_VOCABULARY.tautology == (
        "reproduction fires against the fixed target too — it is a tautology, not a proof"
    )
    assert REPLAY_VOCABULARY.inverted == (
        "reproduction fires only against the FIXED target — the predicate is inverted"
    )
    assert REPLAY_VOCABULARY.never_established == (
        "reproduction fires against neither target — it never established the finding"
    )
    assert REPLAY_VOCABULARY.indecisive.format(before="error", after="refused") == (
        "indecisive replay (vulnerable=error, fixed=refused) — discrimination cannot be established"
    )


# --------------------------------------------------------------------------- #
# pytest exit-code classification
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("code", "outcome", "fired", "decisive"),
    [
        (0, rfg.RunOutcome.passed, False, True),
        (1, rfg.RunOutcome.failed, True, True),
        (2, rfg.RunOutcome.collection_error, True, True),
        (3, rfg.RunOutcome.error, False, False),
        (4, rfg.RunOutcome.error, False, False),
        (5, rfg.RunOutcome.no_tests, False, False),
        (99, rfg.RunOutcome.error, False, False),
    ],
)
def test_exit_code_classification(code, outcome, fired, decisive):
    result = rfg.RunResult(outcome=rfg._classify(code), exit_code=code)
    assert result.outcome == outcome
    assert result.fired is fired
    assert result.decisive is decisive


def test_no_tests_collected_is_not_a_pass():
    """Exit 5 must never read as `passed`.

    Conflating "no tests ran" with "the tests passed" is the same class of error
    as the mandate this gate replaces, and it would let an empty test file clear
    the gate as a tautology-free green.
    """
    assert rfg._classify(5) == rfg.RunOutcome.no_tests
    assert rfg._classify(5) not in rfg._DECISIVE
    assert rfg._classify(5) not in rfg._FIRED


# --------------------------------------------------------------------------- #
# Scope, exemptions, applicability
# --------------------------------------------------------------------------- #
CONFIG = {
    "mode": "enforce",
    "scope": {"roots": ["tests/"], "patterns": ["test_*.py", "*_test.py"]},
    "applicability": {"require_added_test_logic": True, "markers": ["assert", "def test_"]},
    "run": {"timeout_seconds": 180, "pytest_args": ["-q", "--no-header", "-p", "no:cacheprovider"],
            "max_files": 25, "captured_lines": 20},
    "exemptions": [{"pattern": "tests/e2e_selenium/**", "reason": "needs a live server"}],
}


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        ("tests/test_a.py", True),
        ("tests/sub/a_test.py", True),
        ("tests/conftest.py", False),      # collects nothing of its own
        ("tests/helpers.py", False),
        ("tools/ci/red_first_gate.py", False),
    ],
)
def test_scope(rel, expected):
    assert rfg.in_scope(rel, CONFIG) is expected


def test_exemption_requires_a_written_reason():
    """A bare pattern is a bare count with extra steps, so it does not exempt."""
    config = {"exemptions": [{"pattern": "tests/test_x.py"}, {"pattern": "tests/test_y.py", "reason": "why"}]}
    assert rfg.exemption_for("tests/test_x.py", config) is None
    assert rfg.exemption_for("tests/test_y.py", config)["reason"] == "why"


def test_shipped_policy_is_well_formed():
    config = rfg.load_config(REPO_ROOT)
    assert config["mode"] == "enforce", "the shipped gate must be armed, not advisory"
    assert config["applicability"]["require_added_test_logic"] is True
    assert config["exemptions"], "an empty exemption list means the escape hatch is undocumented"
    for rule in config["exemptions"]:
        assert rule.get("pattern"), rule
        assert len(str(rule.get("reason", "")).split()) >= 10, (
            f"exemption {rule.get('pattern')!r} needs a written reason, not a placeholder"
        )


def test_shipped_scope_matches_the_ci_gating_census():
    """Three answers to "what is a test file" would be two too many."""
    import yaml

    mine = rfg.load_config(REPO_ROOT)["scope"]
    theirs = yaml.safe_load(
        (REPO_ROOT / "args" / "test_gating_gate.yaml").read_text(encoding="utf-8")
    )["scope"]
    assert sorted(mine["roots"]) == sorted(theirs["roots"])
    assert sorted(mine["patterns"]) == sorted(theirs["patterns"])


# --------------------------------------------------------------------------- #
# End to end, against a real repository
# --------------------------------------------------------------------------- #
def _git(cwd: Path, *args: str) -> None:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture()
def sample_repo(tmp_path: Path) -> Path:
    """A repo whose base commit has `thing()` returning "old"."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "gate@example.test")
    _git(root, "config", "user.name", "Red First Gate")
    _git(root, "config", "commit.gpgsign", "false")
    _write(root / "pkg" / "__init__.py", "")
    _write(root / "pkg" / "thing.py", "def thing():\n    return 'old'\n")
    _write(root / "tests" / "test_existing.py",
           "from pkg.thing import thing\n\n\ndef test_existing():\n    assert thing() == 'old'\n")
    _write(root / "args" / "red_first_gate.yaml", json.dumps(CONFIG))
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


def _base_sha(root: Path) -> str:
    proc = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          capture_output=True, text=True, timeout=60)
    return proc.stdout.strip()


def _proof(report: dict, path: str) -> dict:
    matches = [p for p in report["proofs"] if p["path"] == path]
    assert matches, f"{path} missing from {[p['path'] for p in report['proofs']]}"
    return matches[0]


def test_end_to_end_verdicts(sample_repo: Path):
    """The acceptance criterion, measured rather than asserted.

    One branch carrying four test files at once, so a single merge-base worktree
    and one `enforce` call cover every verdict the gate can reach:

      * a genuine red-first test          -> discriminating
      * `assert True`                     -> not_discriminating (never established)
      * an assertion that holds pre-fix   -> not_discriminating (never established)
      * a test for a NEW module           -> discriminating, via collection_error
    """
    base = _base_sha(sample_repo)
    # The fix: thing() now returns "new".
    _write(sample_repo / "pkg" / "thing.py", "def thing():\n    return 'new'\n")
    # ...and a brand-new module the PR also adds.
    _write(sample_repo / "pkg" / "fresh.py", "VALUE = 7\n")
    _write(sample_repo / "tests" / "test_existing.py",
           "from pkg.thing import thing\n\n\ndef test_existing():\n    assert thing() == 'new'\n")
    _write(sample_repo / "tests" / "test_vacuous.py",
           "def test_vacuous():\n    assert True\n")
    _write(sample_repo / "tests" / "test_holds_pre_fix.py",
           "from pkg.thing import thing\n\n\n"
           "def test_holds_pre_fix():\n    assert isinstance(thing(), str)\n")
    _write(sample_repo / "tests" / "test_fresh.py",
           "from pkg.fresh import VALUE\n\n\ndef test_fresh():\n    assert VALUE == 7\n")
    _git(sample_repo, "add", "-A")
    _git(sample_repo, "commit", "-qm", "change")

    report = rfg.enforce(sample_repo, base_ref=base, config=CONFIG)

    assert report["ran"] is True
    assert report["subjects"] == 4

    # A genuine red-first test: the old code returned "old", so it failed there.
    red_first = _proof(report, "tests/test_existing.py")
    assert red_first["status"] == rfg.ProofStatus.discriminating
    assert red_first["before"]["outcome"] == rfg.RunOutcome.failed
    assert red_first["after"]["outcome"] == rfg.RunOutcome.passed
    # The RED is RECORDED, not merely concluded — that is the whole task.
    assert red_first["before"]["summary"], "no pytest output captured for the RED"
    assert any("assert" in line for line in red_first["before"]["tail"])

    # `assert True` — the deliberately non-discriminating test.
    vacuous = _proof(report, "tests/test_vacuous.py")
    assert vacuous["status"] == rfg.ProofStatus.not_discriminating
    assert vacuous["discriminating"] is False
    assert vacuous["before"]["outcome"] == rfg.RunOutcome.passed
    assert "current behaviour" in vacuous["reason"].lower()

    # An assertion that holds pre-fix: correct-looking, reviewed, and worthless.
    holds = _proof(report, "tests/test_holds_pre_fix.py")
    assert holds["status"] == rfg.ProofStatus.not_discriminating
    assert holds["before"]["outcome"] == rfg.RunOutcome.passed

    # A test for a module the PR also adds: red by ImportError. A genuine red,
    # and reported as collection_error so a reader can see it is the weak kind.
    fresh = _proof(report, "tests/test_fresh.py")
    assert fresh["status"] == rfg.ProofStatus.discriminating
    assert fresh["before"]["outcome"] == rfg.RunOutcome.collection_error

    assert report["discriminating"] == 2
    assert report["not_discriminating"] == 2
    assert report["ok"] is False
    assert len(report["errors"]) == 2
    joined = " ".join(report["errors"])
    assert "tests/test_vacuous.py" in joined
    assert "args/red_first_gate.yaml" in joined, "the finding must name the exemption route"


def test_only_the_test_file_is_applied_on_top(sample_repo: Path):
    """The restriction IS the experiment.

    If the whole branch were checked out, the fix would be present and the test
    would pass — the gate would prove nothing. Committing the fix and the test
    together and still observing a RED is what shows only the test crossed over.
    """
    base = _base_sha(sample_repo)
    _write(sample_repo / "pkg" / "thing.py", "def thing():\n    return 'new'\n")
    _write(sample_repo / "tests" / "test_existing.py",
           "from pkg.thing import thing\n\n\ndef test_existing():\n    assert thing() == 'new'\n")
    _git(sample_repo, "add", "-A")
    _git(sample_repo, "commit", "-qm", "fix and test together")

    report = rfg.enforce(sample_repo, base_ref=base, config=CONFIG)
    proof = _proof(report, "tests/test_existing.py")
    assert proof["before"]["outcome"] == rfg.RunOutcome.failed, (
        "the merge-base run saw the fix — more than the test file was applied"
    )
    # And the merge-base worktree left nothing behind in the checkout.
    assert (sample_repo / "pkg" / "thing.py").read_text(encoding="utf-8").strip().endswith("'new'")


def test_untracked_test_file_is_still_proven(sample_repo: Path):
    """`git diff` cannot see an untracked file, and a new test is untracked
    right up until `git add` — which is when a developer runs this locally."""
    base = _base_sha(sample_repo)
    _write(sample_repo / "tests" / "test_untracked.py", "def test_u():\n    assert True\n")

    report = rfg.enforce(sample_repo, base_ref=base, config=CONFIG)
    assert _proof(report, "tests/test_untracked.py")["status"] == rfg.ProofStatus.not_discriminating


def test_exempt_and_not_applicable_are_reported_but_never_run(sample_repo: Path):
    base = _base_sha(sample_repo)
    config = json.loads(json.dumps(CONFIG))
    config["exemptions"] = [{"pattern": "tests/test_vacuous.py", "reason": "documented on purpose"}]
    _write(sample_repo / "tests" / "test_vacuous.py", "def test_vacuous():\n    assert True\n")
    # A docstring-only edit adds no assertion, so there is no new RED to record.
    _write(sample_repo / "tests" / "test_existing.py",
           '"""Now with a module docstring."""\nfrom pkg.thing import thing\n\n\n'
           "def test_existing():\n    assert thing() == 'old'\n")
    _git(sample_repo, "add", "-A")
    _git(sample_repo, "commit", "-qm", "exempt + docstring")

    report = rfg.enforce(sample_repo, base_ref=base, config=config)
    assert _proof(report, "tests/test_vacuous.py")["status"] == rfg.ProofStatus.exempt
    assert _proof(report, "tests/test_existing.py")["status"] == rfg.ProofStatus.not_applicable
    assert report["ok"] is True, "an exempt or non-applicable file must not block"
    # Neither was run: no pytest ever executed, so no outcome was recorded.
    assert _proof(report, "tests/test_vacuous.py")["before"]["exit_code"] == -1


def test_docstring_only_edit_that_adds_an_assertion_is_still_proven(sample_repo: Path):
    """Applicability decides whether to ASK the question, never what the answer is."""
    base = _base_sha(sample_repo)
    _write(sample_repo / "tests" / "test_existing.py",
           "from pkg.thing import thing\n\n\ndef test_existing():\n"
           "    assert thing() == 'old'\n    assert thing() != ''\n")
    _git(sample_repo, "add", "-A")
    _git(sample_repo, "commit", "-qm", "extra assertion")

    report = rfg.enforce(sample_repo, base_ref=base, config=CONFIG)
    assert _proof(report, "tests/test_existing.py")["status"] == rfg.ProofStatus.not_discriminating


def test_files_above_max_files_are_named_not_dropped(sample_repo: Path):
    """A cap you cannot see reads as "covered everything"."""
    base = _base_sha(sample_repo)
    config = json.loads(json.dumps(CONFIG))
    config["run"]["max_files"] = 1
    for i in range(3):
        _write(sample_repo / "tests" / f"test_bulk_{i}.py", f"def test_b{i}():\n    assert True\n")
    _git(sample_repo, "add", "-A")
    _git(sample_repo, "commit", "-qm", "bulk")

    report = rfg.enforce(sample_repo, base_ref=base, config=config)
    assert len(report["not_proven"]) == 2
    assert report["proven"] == 1
    assert all(_proof(report, p)["status"] == rfg.ProofStatus.not_proven for p in report["not_proven"])


def test_unresolvable_merge_base_raises_rather_than_reporting_zero(tmp_path: Path):
    """A gate that cannot run is NOT a gate that found nothing.

    Reporting "0 changed test files" from a shallow checkout is precisely the
    "cannot distinguish a check that ran from one that did not" failure this
    module exists to close, so it must raise instead.
    """
    root = tmp_path / "empty"
    root.mkdir()
    _git(root, "init", "-q")
    with pytest.raises(rfg.RedFirstError) as excinfo:
        rfg.resolve_merge_base(root)
    assert "merge base" in str(excinfo.value)
    assert "fetch-depth" in str(excinfo.value)


def test_missing_policy_raises_rather_than_defaulting(tmp_path: Path):
    with pytest.raises(rfg.RedFirstError) as excinfo:
        rfg.load_config(tmp_path)
    assert "red_first_gate.yaml" in str(excinfo.value)


def test_cli_gate_exit_codes(sample_repo: Path, capsys):
    """0 clean, 1 finding, 2 could-not-run — three states, three codes."""
    base = _base_sha(sample_repo)
    _write(sample_repo / "args" / "red_first_gate.yaml", json.dumps(CONFIG))
    _write(sample_repo / "tests" / "test_vacuous.py", "def test_vacuous():\n    assert True\n")
    _git(sample_repo, "add", "-A")
    _git(sample_repo, "commit", "-qm", "vacuous")

    out = sample_repo / "proof.json"
    code = rfg.main(["--root", str(sample_repo), "--base", base, "--gate", "--out", str(out)])
    assert code == 1
    captured = capsys.readouterr()
    assert "not_discriminating" in captured.out
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["not_discriminating"] == 1

    # Advisory mode runs everything and still exits 0 — measured, not armed.
    advisory = json.loads(json.dumps(CONFIG))
    advisory["mode"] = "advisory"
    _write(sample_repo / "args" / "red_first_gate.yaml", json.dumps(advisory))
    assert rfg.main(["--root", str(sample_repo), "--base", base, "--gate"]) == 0
    assert "ADVISORY:" in capsys.readouterr().out


def test_cli_reports_exit_2_when_it_cannot_run(tmp_path: Path, capsys):
    root = tmp_path / "bare"
    root.mkdir()
    _git(root, "init", "-q")
    assert rfg.main(["--root", str(root), "--gate"]) == 2
    assert "could not run" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #
def test_ci_workflow_runs_the_gate_without_a_neutraliser():
    """The D394 lesson, applied to this gate's own wiring.

    A gate wired as `... || true` prints its verdict and blocks nothing, and it
    reads as enforcing to everyone who never checks the exit status. It also
    needs full history: a shallow checkout cannot resolve a merge base.
    """
    workflow = (REPO_ROOT / ".github" / "workflows" / "icdev-ci.yml").read_text(encoding="utf-8")
    assert "tools/ci/red_first_gate.py --gate" in workflow
    line = next(ln for ln in workflow.splitlines() if "red_first_gate.py --gate" in ln)
    assert "|| true" not in line
    assert "continue-on-error" not in line
    assert "fetch-depth: 0" in workflow, "the merge base needs unshallowed history"


def test_module_does_not_reimplement_the_decision_table():
    """The prior art states the rule; this module must import it, not copy it."""
    source = (REPO_ROOT / "tools" / "ci" / "red_first_gate.py").read_text(encoding="utf-8")
    assert "from tools.security.reproduction_validator import" in source
    assert "decide_discrimination" in source
    # A local copy would need its own branch on both fired flags.
    assert "before_fired and not after_fired" not in source


def test_mirrored_to_the_icdev_package():
    """Two copies of a module are two module objects; a stale one fails silently."""
    left = REPO_ROOT / "tools" / "ci" / "red_first_gate.py"
    right = REPO_ROOT / "icdev" / "tools" / "ci" / "red_first_gate.py"
    assert right.is_file(), "icdev/tools/ci/red_first_gate.py is missing"
    assert left.read_bytes() == right.read_bytes()


def test_gate_is_self_hosting():
    """This suite is itself a red-first test, and the gate proves it in CI.

    `tests/test_red_first_gate.py` imports `tools.ci.red_first_gate`, which does
    not exist at the merge base, so the merge-base run is a collection_error and
    the file clears its own gate. Recorded here so that if the module is ever
    moved, the reason this file was never exempted is not lost.
    """
    assert (REPO_ROOT / "tools" / "ci" / "red_first_gate.py").is_file()
    config = rfg.load_config(REPO_ROOT)
    assert rfg.in_scope("tests/test_red_first_gate.py", config) is True
    assert rfg.exemption_for("tests/test_red_first_gate.py", config) is None


def test_registered_in_the_ci_allowlist():
    """A test file CI never runs has never gated a merge (tsg-policy-01)."""
    listed = (REPO_ROOT / "args" / "ci_test_files" / "core.txt").read_text(encoding="utf-8")
    assert "tests/test_red_first_gate.py" in listed


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
