# CUI // SP-CTI
"""The CI test-gating ratchet: the gap cannot silently regrow (tsg-policy-01).

`tests/ci/test_gated_test_list.py` pins that the allowlist cannot SHRINK. That
is only half the property, and it was the less important half: the allowlist has
187 of the 2,149 test modules pytest collects, and the other 1,828 have never
gated a merge. A test file CI never runs can be wrong from its first commit and
nothing goes red — which is exactly how `remediation_simulator._run_nqe_layer`
stayed dead for six weeks (tsg-dead-01) and how 87 files accumulated 531 failure
lines nobody saw.

The policy (docs/ci/test-gating-policy.md) is a ratchet, not a sweep: the 1,828
are grandfathered BY NAME so the debt is countable, and anything outside the
allowlist, the documented exclusions and that census fails the `test` job.

These tests pin the four properties that make the ratchet real:

  1. the live tree is compliant, and the census matches what pytest collects;
  2. a NEW ungated test file goes RED, naming the file;
  3. the backlog census is shrink-only — appending to it to silence the gate
     trips the ceiling instead;
  4. the gate is actually WIRED into the `test` job, because a check nobody runs
     is decoration.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest
import yaml

from tools.ci.gated_test_list import (
    GATE_CONFIG,
    AllowlistError,
    census,
    collect_test_files,
    load_gate_config,
    prune_backlog,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "icdev-ci.yml"
POLICY_DOC = ROOT / "docs" / "ci" / "test-gating-policy.md"


# --------------------------------------------------------------------------- #
# 1. The live tree is compliant
# --------------------------------------------------------------------------- #
def test_shipped_tree_has_no_unlisted_test_files():
    """The regression pin. Adding a test file without gating it fails HERE too,
    not only in CI, so the feedback arrives before the push."""
    report = census(ROOT)
    assert report["ran"], report.get("reason")
    assert report["ok"], report["errors"]
    assert report["unlisted"] == []
    assert report["backlog"] <= report["backlog_max"]


def test_census_scope_matches_what_pytest_collects():
    """A census scoped differently from the runner reports coverage over a set
    nobody runs. `testpaths` and the default `python_files` are the contract."""
    config = load_gate_config(ROOT)
    scope = config["scope"]
    assert scope["roots"] == ["tests/"]
    assert sorted(scope["patterns"]) == ["*_test.py", "test_*.py"]

    pyproject = yaml.safe_load  # noqa: F841 - readability; toml parsed below
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'testpaths = ["tests"]' in text, "pytest's roots moved; update scope.roots"
    # `python_files` is unset, so pytest uses its default of both patterns. If a
    # future commit pins it, this catches the divergence rather than letting the
    # census quietly stop describing the runner.
    assert "python_files" not in text


def test_every_exclusion_states_a_reason():
    """An exclusion without a reason is indistinguishable from an oversight, and
    is how a gate gets hollowed out one 'temporary' entry at a time."""
    config = load_gate_config(ROOT)
    assert config["exclusions"], "the exclusion list must not be empty by accident"
    for rule in config["exclusions"]:
        assert rule.get("pattern"), rule
        assert len(str(rule.get("reason", "")).strip()) > 40, (
            f"exclusion {rule.get('pattern')!r} needs a real reason, not a label"
        )


def test_no_stale_exclusions_or_backlog_entries():
    """Warnings in CI, asserted here: an exclusion pattern that has stopped
    matching is a gate shrinking without anyone deciding to shrink it."""
    report = census(ROOT)
    assert report["stale_exclusions"] == []
    assert report["stale_backlog"] == [], (
        "run `python tools/ci/gated_test_list.py --prune-backlog`"
    )


# --------------------------------------------------------------------------- #
# 2. A new ungated test file goes RED
# --------------------------------------------------------------------------- #
def _fake_root(
    tmp_path: pathlib.Path,
    tests: list,
    core: list,
    backlog: list,
    exclusions: list = None,
    backlog_max: int = 100,
) -> pathlib.Path:
    (tmp_path / "args" / "ci_test_files").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    for rel in tests:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_x():\n    assert True\n", encoding="utf-8")
    (tmp_path / "args" / "ci_test_files" / "core.txt").write_text(
        "\n".join(core) + "\n", encoding="utf-8"
    )
    (tmp_path / "args" / "ci_test_files" / "windows.txt").write_text("", encoding="utf-8")
    (tmp_path / "args" / "ci_test_backlog.txt").write_text(
        "# census\n" + "\n".join(backlog) + "\n", encoding="utf-8"
    )
    (tmp_path / "args" / "test_gating_gate.yaml").write_text(
        yaml.safe_dump(
            {
                "scope": {"roots": ["tests/"], "patterns": ["test_*.py", "*_test.py"]},
                "exclusions": exclusions or [],
                "backlog_file": "args/ci_test_backlog.txt",
                "backlog_max": backlog_max,
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_new_ungated_test_file_fails_and_is_named(tmp_path):
    root = _fake_root(
        tmp_path,
        tests=["tests/test_gated.py", "tests/test_debt.py", "tests/test_brand_new.py"],
        core=["tests/test_gated.py"],
        backlog=["tests/test_debt.py"],
    )
    report = census(root)
    assert not report["ok"]
    assert report["unlisted"] == ["tests/test_brand_new.py"]
    # The message must say what to DO. "Coverage violation" sends the reader to
    # the source; naming core.txt sends them to the fix.
    joined = " ".join(report["errors"])
    assert "tests/test_brand_new.py" in joined
    assert "args/ci_test_files/core.txt" in joined


def test_grandfathered_and_gated_files_do_not_fail(tmp_path):
    root = _fake_root(
        tmp_path,
        tests=["tests/test_gated.py", "tests/test_debt.py"],
        core=["tests/test_gated.py"],
        backlog=["tests/test_debt.py"],
    )
    report = census(root)
    assert report["ok"], report["errors"]
    assert report["gated"] == 1
    assert report["backlog"] == 1


def test_directory_allowlist_entry_covers_the_files_beneath_it(tmp_path):
    """`tests/studio/` is one LINE but many FILES. Counting lines would understate
    coverage and push already-gated files into the backlog."""
    root = _fake_root(
        tmp_path,
        tests=["tests/studio/test_a.py", "tests/studio/test_b.py"],
        core=["tests/studio/"],
        backlog=[],
    )
    report = census(root)
    assert report["ok"], report["errors"]
    assert report["gated"] == 2
    assert report["backlog"] == 0


def test_exclusion_pattern_covers_a_subtree(tmp_path):
    root = _fake_root(
        tmp_path,
        tests=["tests/e2e_selenium/test_a.py", "tests/e2e_selenium/deep/test_b.py"],
        core=[],
        backlog=[],
        exclusions=[{"pattern": "tests/e2e_selenium/**", "reason": "x" * 50}],
    )
    report = census(root)
    assert report["ok"], report["errors"]
    assert report["excluded"] == 2


def test_an_allowlisted_file_beats_the_exclusion_covering_it(tmp_path):
    """An exclusion is a DEFAULT, not a veto. tsg-gen-01 gated two files inside
    tests/genesis_auto/ because their failures were real behaviour; those must
    count as gated, not silently drop out of the numbers as excluded."""
    root = _fake_root(
        tmp_path,
        tests=["tests/genesis_auto/test_real.py", "tests/genesis_auto/test_rot.py"],
        core=["tests/genesis_auto/test_real.py"],
        backlog=[],
        exclusions=[{"pattern": "tests/genesis_auto/**", "reason": "z" * 50}],
    )
    report = census(root)
    assert report["ok"], report["errors"]
    assert report["gated"] == 1
    assert report["excluded"] == 1


def test_exclusion_that_matches_nothing_is_reported_stale(tmp_path):
    root = _fake_root(
        tmp_path,
        tests=["tests/test_gated.py"],
        core=["tests/test_gated.py"],
        backlog=[],
        exclusions=[{"pattern": "tests/gone/**", "reason": "y" * 50}],
    )
    report = census(root)
    assert report["stale_exclusions"] == ["tests/gone/**"]
    # Reported, not fatal — a stale pattern is bookkeeping, and failing on it
    # would red-light a PR whose only sin was deleting a directory.
    assert report["ok"], report["errors"]


# --------------------------------------------------------------------------- #
# 3. The backlog census only shrinks
# --------------------------------------------------------------------------- #
def test_appending_to_the_backlog_trips_the_ceiling(tmp_path):
    """The escape hatch this closes: silence the gate by listing the new file as
    'pre-existing debt'. The ceiling is what makes that a visible, arguable edit
    to args/test_gating_gate.yaml rather than a one-line append nobody reads."""
    root = _fake_root(
        tmp_path,
        tests=["tests/test_debt.py", "tests/test_smuggled.py"],
        core=[],
        backlog=["tests/test_debt.py", "tests/test_smuggled.py"],
        backlog_max=1,
    )
    report = census(root)
    assert not report["ok"]
    assert report["unlisted"] == []  # it IS listed — that is the point
    assert any("above the ceiling" in e for e in report["errors"])
    assert any("never raise it" in e for e in report["errors"])


def test_a_bare_count_would_not_catch_the_churn(tmp_path):
    """Why the census is enumerated and not a number: fix one, add one, count
    unchanged. Identity catches it; a count does not."""
    root = _fake_root(
        tmp_path,
        tests=["tests/test_fixed.py", "tests/test_new_debt.py"],
        core=["tests/test_fixed.py"],
        backlog=["tests/test_fixed.py"],  # the old debt, now gated
        backlog_max=1,
    )
    report = census(root)
    assert not report["ok"]
    assert report["unlisted"] == ["tests/test_new_debt.py"]


def test_prune_backlog_drops_fixed_lines_keeps_header_and_writes_lf(tmp_path):
    root = _fake_root(
        tmp_path,
        tests=["tests/test_fixed.py", "tests/test_debt.py"],
        core=["tests/test_fixed.py"],
        backlog=["tests/test_fixed.py", "tests/test_debt.py"],
    )
    result = prune_backlog(root)
    assert result["pruned"] == ["tests/test_fixed.py"]
    raw = (root / "args" / "ci_test_backlog.txt").read_bytes()
    assert b"\r" not in raw, "CRLF here makes every consumer miss a path that exists"
    text = raw.decode("utf-8")
    assert text.startswith("# census")
    assert "tests/test_debt.py" in text
    assert "tests/test_fixed.py" not in text
    assert census(root)["stale_backlog"] == []


def test_missing_backlog_file_is_an_error_not_a_pass(tmp_path):
    """A census that cannot find its own data must not report zero unlisted."""
    root = _fake_root(tmp_path, tests=[], core=[], backlog=[])
    (root / "args" / "ci_test_backlog.txt").unlink()
    with pytest.raises(AllowlistError):
        census(root)


def test_census_reports_not_run_without_a_tests_tree(tmp_path):
    """In an installed wheel args/ is mirrored but the suite is not shipped.
    Flagging all 2,149 files as unlisted there is how a check earns a `|| true`."""
    root = _fake_root(tmp_path, tests=[], core=[], backlog=[])
    (root / "tests").rmdir()
    report = census(root)
    assert report["ran"] is False
    assert report["ok"] is True

    # And with the config absent too. args/ is mirrored to icdev/data/args/ at
    # RELEASE, not by every PR that edits it, so a wheel built in between can
    # legitimately lack the policy files. That must read as "nothing to census",
    # not as a crash — the same reason the existence check reports NOT RUN there.
    (root / "args" / GATE_CONFIG.name).unlink()
    assert census(root)["ran"] is False


# --------------------------------------------------------------------------- #
# 4. The gate is wired, and the policy is written down
# --------------------------------------------------------------------------- #
def test_the_test_job_actually_runs_the_census():
    """A ratchet nobody runs is decoration. Pin the wiring, and pin that nobody
    bolted `|| true` onto it."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "gated_test_list.py --check-coverage" in text
    for line in text.splitlines():
        if "--check-coverage" in line:
            assert "|| true" not in line and "continue-on-error" not in line

    doc = yaml.safe_load(text)
    # The census runs ONCE, in `test-gates` — it is a whole-tree sweep, so
    # running it per shard would pay for it N times for one answer. `test` is
    # now an aggregator, so "is it in the required job" is asked in two parts:
    # the census is in test-gates, AND the required check depends on test-gates.
    steps = doc["jobs"]["test-gates"]["steps"]
    assert any("--check-coverage" in str(s.get("run", "")) for s in steps), (
        "the census must run in `test-gates`, not an advisory job"
    )
    assert "test-gates" in doc["jobs"]["test"]["needs"], (
        "the REQUIRED `test` check must depend on the job carrying the census — "
        "otherwise the ratchet runs but cannot block a merge"
    )


def test_cli_check_coverage_exits_nonzero_on_a_violation(tmp_path):
    """Exercised as a subprocess: CI reads the exit CODE, not the report dict."""
    root = _fake_root(
        tmp_path,
        tests=["tests/test_brand_new.py"],
        core=[],
        backlog=[],
    )
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "ci" / "gated_test_list.py"),
         "--check-coverage", "--root", str(root)],
        capture_output=True, cwd=str(ROOT),
    )
    assert proc.returncode == 1
    assert b"tests/test_brand_new.py" in proc.stderr


def test_backlog_is_not_union_merged():
    """Deliberate asymmetry with args/ci_test_files/*.txt. Union resurrects a
    line deleted on one branch — correct for an append-only allowlist, exactly
    backwards for a shrink-only census."""
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "args/ci_test_files/*.txt merge=union" in attrs
    for line in attrs.splitlines():
        if "ci_test_backlog.txt" in line:
            assert "merge=union" not in line


def test_policy_is_documented_and_reachable():
    assert POLICY_DOC.is_file()
    body = POLICY_DOC.read_text(encoding="utf-8")
    for anchor in ("args/ci_test_files/core.txt", "args/ci_test_backlog.txt",
                   str(GATE_CONFIG).replace("\\", "/"), "--check-coverage"):
        assert anchor in body, f"the policy doc must name {anchor}"
    assert "test-gating-policy.md" in (ROOT / "CLAUDE.md").read_text(encoding="utf-8")


def test_collect_test_files_finds_both_naming_conventions(tmp_path):
    root = _fake_root(
        tmp_path,
        tests=["tests/test_leading.py", "tests/trailing_test.py", "tests/helpers.py"],
        core=[],
        backlog=["tests/test_leading.py", "tests/trailing_test.py"],
    )
    found = collect_test_files(root, load_gate_config(root))
    assert found == ["tests/test_leading.py", "tests/trailing_test.py"]
