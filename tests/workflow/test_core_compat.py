# CUI // SP-CTI
"""The core-compat tier, suite and local runner (xcore-compat-01).

What must hold: the declared tier actually runs, the declared suite is real and derived rather
than hand-picked, and every way of NOT exercising a parent reports something other than success.
That last one is the point of the whole card — a compat matrix that goes green over a parent it
never ran is worse than no matrix, because it retires the question.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

from tools.dev.core_compat_local import (
    COMPAT_LIST,
    LegResult,
    Report,
    State,
    parent_roots,
    read_suite,
    run_leg,
)
from tools.workflow.coherence_checker import (
    CHECK_REGISTRY,
    CORE_TIER_CHECKS,
    TIERS,
    select_checks,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# The tier
# ---------------------------------------------------------------------------


def test_core_is_a_declared_tier():
    assert "core" in TIERS


def test_every_declared_core_check_is_registered():
    """A declared id that is no longer registered would be silently DROPPED.

    `select_checks` intersects with the registry, so a renamed check would shrink the tier
    without any error — six checks running while seven are claimed. That is the shape this
    file exists to refuse, so the mismatch is asserted here rather than discovered later.
    """
    missing = [cid for cid in CORE_TIER_CHECKS if cid not in CHECK_REGISTRY]
    assert missing == [], f"declared in CORE_TIER_CHECKS but not registered: {missing}"


def test_the_core_tier_runs_exactly_what_it_declares():
    assert sorted(select_checks("core")) == sorted(CORE_TIER_CHECKS)


def test_the_core_tier_is_a_strict_subset_of_full():
    """If it were everything, it would not be a tier — it would be `full` with a new name."""
    full = set(select_checks("full"))
    core = set(select_checks("core"))
    assert core < full, "the core tier must be a proper subset of the full run"
    assert core, "an empty core tier would pass over every core change"


def test_the_core_tier_includes_the_check_about_core_itself():
    """`core_api` is the one check whose entire subject is the pinned core surface."""
    assert "core_api" in CORE_TIER_CHECKS


def test_an_unknown_tier_is_rejected_by_the_cli():
    proc = subprocess.run(
        [sys.executable, "tools/workflow/coherence_checker.py", "--tier", "nonsense"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "invalid choice" in (proc.stderr or "").lower()


# ---------------------------------------------------------------------------
# The declared suite
# ---------------------------------------------------------------------------


def test_this_parent_declares_a_compat_suite():
    assert (REPO_ROOT / COMPAT_LIST).is_file(), (
        f"{COMPAT_LIST.as_posix()} is what the core repo's matrix reads; without it that "
        "matrix has nothing to run against this parent"
    )


def test_every_declared_module_exists():
    missing = [m for m in read_suite(REPO_ROOT) if not (REPO_ROOT / m).is_file()]
    assert missing == [], f"declared in the compat suite but absent: {missing[:10]}"


def test_every_declared_module_is_gated():
    """An UNGATED module has never gated a merge, so it may already be red (rem-hyg-14).

    Listing one here would make a core PR fail for a reason that predates it — and the fix
    people would reach for is deleting the line, which is how a suite hollows out.
    """
    proc = subprocess.run(
        [sys.executable, "tools/ci/gated_test_list.py", "--print", "--list", "core"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    # Deliberately NOT a skip. A skipped test satisfies the coverage claim while asserting
    # nothing, and this file is gated; if the gated list cannot be produced then the whole
    # compat suite is unverifiable and that is a finding, not an excuse.
    assert proc.returncode == 0, (
        f"gated_test_list could not be run (exit {proc.returncode}): "
        f"{(proc.stderr or '').strip()[:300]}"
    )
    gated = set(proc.stdout.split())
    assert gated, "the gated list came back empty — nothing could be verified against it"
    ungated = [m for m in read_suite(REPO_ROOT) if m not in gated]
    assert ungated == [], f"declared in the compat suite but NOT gated: {ungated[:10]}"


def test_the_suite_is_not_trivially_small():
    """A floor, so the suite cannot be quietly hollowed out to make a core PR green."""
    assert len(read_suite(REPO_ROOT)) >= 20


def test_comments_and_blanks_are_not_treated_as_modules(tmp_path):
    root = tmp_path / "parent"
    (root / COMPAT_LIST.parent).mkdir(parents=True)
    (root / COMPAT_LIST).write_text(
        "# a comment\n\ntests/a.py\n   \ntests/b.py\n", encoding="utf-8"
    )
    assert read_suite(root) == ["tests/a.py", "tests/b.py"]


def test_a_parent_with_no_declaration_reads_as_empty_not_as_an_error(tmp_path):
    assert read_suite(tmp_path) == []


# ---------------------------------------------------------------------------
# Not exercising a parent is never success
# ---------------------------------------------------------------------------


def test_an_unreachable_parent_is_not_a_pass(tmp_path):
    leg = run_leg(tmp_path / "does_not_exist")
    assert leg.state == State.unreachable
    assert not Report(core="x", legs=[leg]).ok


def test_a_directory_that_is_not_a_checkout_is_not_a_pass(tmp_path):
    (tmp_path / "looks_like_a_repo").mkdir()
    leg = run_leg(tmp_path / "looks_like_a_repo")
    assert leg.state == State.unreachable


def test_a_parent_declaring_nothing_is_undeclared_not_passed(tmp_path):
    """"This parent has nothing to say" and "this parent is happy" are different answers."""
    root = tmp_path / "bare"
    (root / ".git").mkdir(parents=True)
    leg = run_leg(root)
    assert leg.state == State.undeclared
    assert not Report(core="x", legs=[leg]).ok


def test_a_report_over_zero_parents_is_not_ok():
    """The degenerate case: no legs at all must never read as success."""
    assert not Report(core="x", legs=[]).ok


def test_ok_requires_every_leg_to_pass():
    passed = LegResult("a", State.passed)
    assert Report(core="x", legs=[passed]).ok
    for bad in (State.failed, State.undeclared, State.unreachable, State.error):
        assert not Report(core="x", legs=[passed, LegResult("b", bad)]).ok, bad


# ---------------------------------------------------------------------------
# Parent discovery
# ---------------------------------------------------------------------------


def test_parents_are_semicolon_separated(monkeypatch):
    """`;` not `:` — a Windows path contains a colon, and this project's primary dev box is
    Windows. Splitting on `:` would turn `C:/AI/ICDev` into two unusable roots."""
    monkeypatch.setenv("ICDEV_CORE_PARENTS", "C:/AI/ICDev;C:/ai/icdev_ft")
    roots = parent_roots()
    assert [r.as_posix() for r in roots] == ["C:/AI/ICDev", "C:/ai/icdev_ft"]


def test_blank_entries_are_dropped(monkeypatch):
    monkeypatch.setenv("ICDEV_CORE_PARENTS", " ;C:/AI/ICDev; ;")
    assert len(parent_roots()) == 1


def test_an_explicit_parent_overrides_the_environment(monkeypatch):
    monkeypatch.setenv("ICDEV_CORE_PARENTS", "C:/from/env")
    assert [r.as_posix() for r in parent_roots(["C:/explicit"])] == ["C:/explicit"]


def test_no_parents_is_distinguishable_from_a_passing_run(monkeypatch):
    monkeypatch.delenv("ICDEV_CORE_PARENTS", raising=False)
    assert parent_roots() == []
