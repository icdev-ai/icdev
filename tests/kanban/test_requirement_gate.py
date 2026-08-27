# CUI // SP-CTI
"""The requirement gate: one decision table, and it must actually refuse (wire-req-01).

THE DEFECT. Measured on the live board 2026-08-27 (3,571 tasks): `acceptance_criteria`
populated on 7.5%, 38% completed via bypass, and — the number that matters —
**`judged_pass = 1`**. Exactly one task in the whole history was ever judged against a criterion
and passed. `conformance_reviewer` returns `review_passed=None` for an EMPTY criterion and
`pr_watcher._enforced_done_ok` reads `None` as allowed, so 92.5% of tasks cleared that rung
vacuously.
"""
from __future__ import annotations

import pytest

import pathlib

from tools.kanban import requirement_gate as rg
from tools.kanban.requirement_gate import Verdict

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _census_entries() -> list:
    """The census, comments and blanks dropped."""
    path = REPO_ROOT / "args/kanban_seeder_criteria_census.txt"
    if not path.is_file():
        return []
    return [
        ln.strip()
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


# ---------------------------------------------------------------------------
# The three states today's gate merges into "allowed"
# ---------------------------------------------------------------------------


def test_an_empty_criterion_is_not_the_same_as_a_pass():
    """The 92.5% case. `review_passed IS NULL` because nothing was written."""
    verdict, reason = rg.classify_verification(
        result="passed", review_passed=None, criteria=""
    )
    assert verdict == Verdict.unjudged_no_criteria
    assert "no acceptance_criteria" in reason


def test_an_empty_criterion_is_not_the_same_as_a_pending_review():
    """Two NULLs, two causes, two repairs — write a criterion, or wait for the judge."""
    absent, _ = rg.classify_verification(result="passed", review_passed=None, criteria="  ")
    pending, _ = rg.classify_verification(
        result="passed", review_passed=None, criteria="the endpoint returns 200"
    )
    assert absent == Verdict.unjudged_no_criteria
    assert pending == Verdict.unjudged_not_run
    assert absent != pending


def test_a_bypass_is_unverified_not_passed():
    """`tools/idp/delivery_events.py`'s own distinction, kept: skipped is not failed —
    and it is not passed either, which is what `_enforced_done_ok` currently reads it as."""
    verdict, reason = rg.classify_verification(
        result="bypassed", review_passed=None, criteria="anything"
    )
    assert verdict == Verdict.skipped_bypass
    assert "SKIPPED" in reason


def test_a_judged_pass_is_the_only_verdict_that_means_checked():
    assert rg.JUDGED == {Verdict.judged_pass}
    verdict, _ = rg.classify_verification(
        result="passed", review_passed=1, criteria="the endpoint returns 200"
    )
    assert verdict == Verdict.judged_pass


def test_no_verification_row_is_its_own_verdict():
    """44% of tasks. Not a pass, not a failure — nothing ran."""
    verdict, _ = rg.classify_verification(result=None, review_passed=None, criteria="x")
    assert verdict == Verdict.no_verification


@pytest.mark.parametrize("result", ["failed", "phantom"])
def test_a_real_failure_is_refused_in_every_mode(result):
    """This module must never WEAKEN the existing gate."""
    verdict, _ = rg.classify_verification(result=result, review_passed=None, criteria="x")
    assert verdict == Verdict.judged_fail
    for mode in ("off", "report", "enforce"):
        assert rg.refuses(verdict, current_mode=mode) is True


def test_conformance_failure_is_refused_in_every_mode():
    verdict, _ = rg.classify_verification(result="passed", review_passed=0, criteria="x")
    assert verdict == Verdict.judged_fail
    assert rg.refuses(verdict, current_mode="off") is True


# ---------------------------------------------------------------------------
# Posture: the done gate is NOT armed, and the survey is why
# ---------------------------------------------------------------------------


def test_the_done_gate_ships_unarmed():
    """96.7% refusal measured. CLAUDE.md stands a check down at 1.63%."""
    assert rg.DONE_DEFAULT_MODE == "report"
    for verdict in rg.REFUSED_WHEN_ENFORCING:
        assert rg.refuses(verdict, current_mode="report") is False


def test_the_done_gate_would_refuse_these_if_ever_armed():
    for verdict in (
        Verdict.unjudged_no_criteria,
        Verdict.skipped_bypass,
        Verdict.no_verification,
    ):
        assert rg.refuses(verdict, current_mode="enforce") is True


def test_a_pending_review_is_never_refused():
    """`unjudged_not_run` is genuinely in flight; refusing it would be a race, not a finding."""
    for mode in ("off", "report", "enforce"):
        assert rg.refuses(Verdict.unjudged_not_run, current_mode=mode) is False


def test_seed_admission_also_ships_unarmed_and_the_census_says_why():
    """The first reading of this rung looked armable. Measuring said otherwise.

    Of 57 modules calling `create_tasks`, 46 never mention `acceptance_criteria`, and five of
    those are LIVE reflexes seeding `fix` cards on a 6-hour cadence. Arming would have them
    raise on their next cycle and take the autonomous loop down within hours.
    """
    assert rg.SEED_DEFAULT_MODE == "report"

    census = REPO_ROOT / "args/kanban_seeder_criteria_census.txt"
    assert census.is_file(), (
        "the census is the named path to arming; without it `report` is just a permanent "
        "climbdown"
    )
    entries = _census_entries()
    assert entries, "an empty census means the gate should already be armed — flip the default"


def test_every_census_entry_still_exists():
    """A stale entry makes the file undrainable: nobody can fix a module that is gone."""
    missing = [e for e in _census_entries() if not (REPO_ROOT / e).is_file()]
    assert missing == [], f"census names modules that no longer exist: {missing}"


def test_the_census_names_the_live_reflexes_that_forced_the_posture():
    """Named so a future reader can check the claim rather than take it on trust."""
    entries = set(_census_entries())
    for reflex in (
        "tools/genesis/reflexes/claim_verifier_reflex.py",
        "tools/genesis/reflexes/coherence_to_kanban_reflex.py",
        "tools/genesis/reflexes/qa_agent_reflex.py",
        "tools/genesis/reflexes/ungated_test_drift.py",
        "tools/genesis/reflexes/route_perf_reflex.py",
    ):
        assert reflex in entries, f"{reflex} seeds fix cards with no criterion and must be listed"


def test_a_listed_module_really_would_be_refused():
    """The census is not decorative: each entry names a module whose specs the armed gate
    would refuse. Asserted against the predicate, not against the file's own claim."""
    entries = _census_entries()
    assert entries
    for rel in entries[:5]:
        src = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        assert "acceptance_criteria" not in src, (
            f"{rel} now sets acceptance_criteria — remove its census line"
        )


def test_an_unknown_mode_falls_back_to_the_default_never_to_enforce(monkeypatch):
    """A typo in an env var must not silently ARM a gate."""
    monkeypatch.setenv(rg.DONE_MODE_ENV, "yes-please")
    assert rg.done_mode() == rg.DONE_DEFAULT_MODE
    monkeypatch.setenv(rg.SEED_MODE_ENV, "nonsense")
    assert rg.seed_mode() == rg.SEED_DEFAULT_MODE


# ---------------------------------------------------------------------------
# Seed admission
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task_type", ["build", "fix"])
def test_a_build_or_fix_card_needs_a_criterion(task_type):
    ok, why = rg.admit_spec(
        {"id": "x-y-01", "task_type": task_type, "acceptance_criteria": ""},
        current_mode="enforce",
    )
    assert ok is False
    assert "acceptance_criteria" in why


@pytest.mark.parametrize("task_type", ["chore", "research", "test", "run", "deploy"])
def test_other_types_do_not(task_type):
    """A research card's output IS the answer; demanding a criterion up front would invite
    one written to be satisfiable rather than one that is true."""
    ok, _ = rg.admit_spec(
        {"id": "x-y-01", "task_type": task_type, "acceptance_criteria": ""},
        current_mode="enforce",
    )
    assert ok is True


def test_whitespace_is_not_a_criterion():
    ok, _ = rg.admit_spec(
        {"id": "x-y-01", "task_type": "build", "acceptance_criteria": "   \n  "},
        current_mode="enforce",
    )
    assert ok is False


def test_a_real_criterion_is_admitted():
    ok, _ = rg.admit_spec(
        {
            "id": "x-y-01",
            "task_type": "build",
            "acceptance_criteria": "GET /api/x returns 200 with a non-empty list",
        },
        current_mode="enforce",
    )
    assert ok is True


def test_report_mode_refuses_nothing_at_seed():
    ok, why = rg.admit_spec(
        {"id": "x-y-01", "task_type": "build", "acceptance_criteria": ""},
        current_mode="report",
    )
    assert ok is True
    assert "not armed" in why


# ---------------------------------------------------------------------------
# The seeder actually consumes it
# ---------------------------------------------------------------------------


def test_task_factory_refuses_before_inserting_anything(monkeypatch):
    """BEFORE any INSERT, so a batch cannot half-land — the position the VALID_TASK_TYPES
    refusal already occupies.

    Armed explicitly, because the gate SHIPS `report` (see the census tests above). A gate that
    cannot refuse even when armed is the defect this whole card is about, so it is proved here
    rather than assumed from the default.
    """
    from tools.kanban.task_factory import create_tasks

    monkeypatch.setenv(rg.SEED_MODE_ENV, "enforce")
    with pytest.raises(ValueError, match="nothing can judge"):
        create_tasks(
            [
                {
                    "id": "wire-test-90",
                    "title": "has a criterion",
                    "task_type": "build",
                    "acceptance_criteria": "something checkable",
                },
                {"id": "wire-test-91", "title": "does not", "task_type": "build"},
            ]
        )


def test_task_factory_does_not_refuse_in_the_shipped_posture(monkeypatch):
    """The other half: `report` must not break the five live reflexes it was set for.

    Asserted by REACHING the refusal point with an unjudgeable spec and getting past it — the
    insert then fails for want of a real board, which is fine; what matters is that the
    requirement gate did not raise.
    """
    from tools.kanban.task_factory import create_tasks

    monkeypatch.setenv(rg.SEED_MODE_ENV, "report")
    try:
        create_tasks([{"id": "wire-test-92", "title": "no criterion", "task_type": "fix"}])
    except ValueError as exc:  # pragma: no cover - only on regression
        assert "nothing can judge" not in str(exc), (
            "the requirement gate refused under `report`; that would take the autonomous "
            "loop down"
        )
    except Exception:  # noqa: BLE001 - a DB/env failure here is not what is under test
        pass


def test_the_dead_checker_is_gone():
    """`_check_acceptance_criteria` was defined and called nowhere — the
    declared-but-unconsumed defect inside the acceptance checker itself."""
    from tools.genesis.reflexes import kanban as k

    assert not hasattr(k, "_check_acceptance_criteria")
