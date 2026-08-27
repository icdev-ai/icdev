# CUI // SP-CTI
"""Did this task ever get judged against a requirement? ONE decision table (wire-req-01).

THE DEFECT. Measured on the live board 2026-08-27: ``acceptance_criteria`` is populated on 267
of 3,571 tasks (7.5%), 1,362 (38%) completed via bypass, and 1,715 verification rows are
``bypassed`` against 900 ``passed``. Three mechanisms make that silent, and the last two are the
reason a card can read 100% while nothing checked what it was for:

  * ``conformance_reviewer.review_conformance`` returns ``review_passed=None`` when the criterion
    is EMPTY -- indistinguishable, downstream, from "a judge ran and had no opinion";
  * ``pr_watcher._enforced_done_ok`` reads ``None`` as ALLOWED (its own comment: "None = not
    judged, allowed"), so at 7.5% populated **92.5% of tasks clear that rung vacuously**;
  * the same function accepts ``result='bypassed'`` as a pass, so a SKIPPED verification and a
    PASSED one are one bucket.

WHY THIS MODULE EXISTS RATHER THAN AN `if` IN EACH CALLER. The survey that decides whether any
of this may be armed has to replay recorded history through the SAME predicate the gate will
use. ``landed_dispatch_survey`` states the rule it was written under -- "evidence tiers are the
gate's, NOT a second copy... a survey with its own matcher would measure a gate that does not
exist" -- and the merge ladder follows it too (``classify_merge_readiness`` has one
implementation and two consumers). So the decisions live here, and ``task_factory``,
``pr_watcher`` and ``bypass_survey`` all call them.

NOTHING HERE REFUSES ANYTHING BY ITSELF. Every function returns a verdict; the caller decides
what to do with it, and the arming posture is read from config so a survey can run against the
same code that will later enforce. That is deliberate: this repo stands a check down when it
refuses 1.63% of routine work, and the bypass rate is 38%.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

#: Task types whose whole purpose is to change behaviour, so "what would done look like" is
#: answerable when the card is written. `chore`, `research`, `test`, `run` and `deploy` are
#: deliberately absent: a research card's output IS the answer, and demanding a criterion up
#: front would invite one written to be satisfiable rather than one that is true.
REQUIRES_CRITERIA_TYPES = frozenset({"build", "fix"})

#: TWO SWITCHES, TWO DEFAULTS, AND THE SURVEY IS WHY (measured 2026-08-27, 3,576 tasks).
#:
#: ``python -m tools.kanban.bypass_survey`` replayed recorded history through the very
#: predicates below:
#:
#:   seed admission   would refuse 2,367 of 2,588 build/fix cards   91.5%
#:   done gate        would refuse 3,453 of 3,571 done tasks        96.7%  (wrong rate 78.8%)
#:
#: CLAUDE.md stands a check down when it refuses 1.63% of routine work. 96.7% would stop the
#: board dead, so THE DONE GATE SHIPS `report` AND IS NOT ARMED ON THESE NUMBERS. What the
#: verdict breakdown shows is worse than a bad rate and is the reason the rung exists at all:
#: `judged_pass = 1`. Exactly ONE task in 3,571 has ever been judged against a criterion and
#: passed. Arming a gate over a layer that has never functioned would refuse the board, not fix
#: it; the criteria have to exist first.
#:
#: SEED ADMISSION SHIPS `report` TOO, AND MEASURING IS WHAT DECIDED IT. The first reading of
#: this rung looked armable -- 91.5% is a fact about HISTORY, those cards are already seeded,
#: and the forward cost is one sentence. That reasoning was WRONG, because the forward callers
#: are mostly AUTOMATION, not humans. Of 57 modules calling ``create_tasks``, 46 never mention
#: ``acceptance_criteria``, and five of those are LIVE reflexes that seed ``fix`` cards on a
#: 6-hour cadence:
#:
#:     claim_verifier_reflex     coherence_to_kanban_reflex     qa_agent_reflex
#:     ungated_test_drift        route_perf_reflex
#:
#: Arming would have them raise ``ValueError`` on their next cycle and take the autonomous loop
#: down. That is the definition of refusing routine work, and it is what the survey rule exists
#: to catch before it ships rather than after.
#:
#: THE PATH TO ARMING IS NAMED, NOT LEFT VAGUE: drain ``args/kanban_seeder_criteria_census.txt``
#: -- an enumerated, shrink-only list of the modules that seed a build/fix card without a
#: criterion. Each entry is somebody's future fix, which is the shape a census is for. When it
#: reaches zero, flip this default and the gate costs nothing.
#:
#: Both take ``off`` | ``report`` | ``enforce``, the shape of KANBAN_LANDED_CHECK.
SEED_MODE_ENV = "KANBAN_REQUIRE_ACCEPTANCE_CRITERIA"
SEED_DEFAULT_MODE = "report"

DONE_MODE_ENV = "KANBAN_REQUIREMENT_GATE"
DONE_DEFAULT_MODE = "report"

VALID_MODES = ("off", "report", "enforce")


class Verdict:
    """What the requirement layer concluded. Kept apart on purpose."""

    judged_pass = "judged_pass"
    """A criterion existed and the conformance review passed it."""

    judged_fail = "judged_fail"
    """A criterion existed and the review FAILED it. Always refused, in every mode."""

    unjudged_no_criteria = "unjudged_no_criteria"
    """No criterion was ever written, so nothing could be judged. TODAY this reads as a pass.
    It is the 92.5% case and the reason this module exists."""

    unjudged_not_run = "unjudged_not_run"
    """A criterion exists but the review has not run yet. Genuinely pending -- NOT the same as
    having nothing to judge, which is why the two are separate values."""

    skipped_bypass = "skipped_bypass"
    """Verification was skipped (a force-done with an audited reason). An UNVERIFIED change, not
    a failed one -- the distinction is tools/idp/delivery_events.py's and is kept here."""

    no_verification = "no_verification"
    """No verification row at all. 44% of tasks."""


#: Verdicts that mean "a requirement was actually checked and held".
JUDGED = frozenset({Verdict.judged_pass})

#: Verdicts that are refused under `enforce` but recorded under `report`. `judged_fail` is NOT
#: here: a failed conformance review is refused in every mode, by the existing gate, already.
REFUSED_WHEN_ENFORCING = frozenset(
    {Verdict.unjudged_no_criteria, Verdict.skipped_bypass, Verdict.no_verification}
)


def _mode(env_name: str, default: str) -> str:
    """Arming posture. An unknown value falls back to the default rather than raising.

    A typo in an env var must never stop the board, and it must never silently ARM either --
    hence falling back to the declared default rather than to `enforce`.
    """
    raw = (os.environ.get(env_name) or default).strip().lower()
    return raw if raw in VALID_MODES else default


def seed_mode() -> str:
    """Posture for seed admission. Armed by default -- forward-only, see the note above."""
    return _mode(SEED_MODE_ENV, SEED_DEFAULT_MODE)


def done_mode() -> str:
    """Posture for the done gate. `report` by default -- 96.7% refusal, never armed on that."""
    return _mode(DONE_MODE_ENV, DONE_DEFAULT_MODE)


def has_criteria(value: Optional[str]) -> bool:
    """A criterion is present only if it says something.

    Whitespace and the empty string are the same as absent. A placeholder like "TBD" is NOT
    caught here -- that is a review question, and a substring blocklist would be trivially
    routed around while giving the appearance of rigour.
    """
    return bool((value or "").strip())


def requires_criteria(task_type: Optional[str]) -> bool:
    return str(task_type or "").strip().lower() in REQUIRES_CRITERIA_TYPES


# --------------------------------------------------------------------------- #
# Seed-time admission
# --------------------------------------------------------------------------- #
def admit_spec(spec: Dict[str, Any], *, current_mode: Optional[str] = None) -> Tuple[bool, str]:
    """May this task spec be seeded? Returns ``(ok, reason)``; never raises.

    Called by ``task_factory.create_tasks`` BEFORE any INSERT, in the same position as the
    ``VALID_TASK_TYPES`` refusal, so a batch cannot half-land.

    ``current_mode`` is passed explicitly by the SURVEY, which must classify history under
    `enforce` regardless of how the live switch happens to be set. A caller that omits it gets
    the live posture.
    """
    task_type = spec.get("task_type")
    if not requires_criteria(task_type):
        return True, f"task_type={task_type!r} does not require a criterion"
    if has_criteria(spec.get("acceptance_criteria")):
        return True, "criterion present"
    if (current_mode or seed_mode()) != "enforce":
        return True, "no criterion, but seed admission is not armed (reported, not refused)"
    return False, (
        f"{spec.get('id')!r} is a {task_type!r} card with no acceptance_criteria. Nothing "
        "downstream can judge whether it was delivered: an empty criterion makes "
        "review_conformance return review_passed=None, and the done gate reads None as "
        "ALLOWED. Write what done looks like, in terms someone else could check."
    )


# --------------------------------------------------------------------------- #
# Done-time classification
# --------------------------------------------------------------------------- #
def classify_verification(
    *,
    result: Optional[str],
    review_passed: Optional[Any],
    criteria: Optional[str],
) -> Tuple[str, str]:
    """What did the requirement layer actually conclude about this task?

    ``result`` / ``review_passed`` are the latest ``kanban_verifications`` row; ``criteria`` is
    the task's ``acceptance_criteria``. Returns ``(Verdict, reason)``.

    THE WHOLE POINT is separating the three things today's gate merges into "allowed":
    a criterion that PASSED, a criterion that was never written, and a verification that was
    skipped. They justify different actions and must not share a bucket.
    """
    normalised = str(result or "").strip().lower()

    if not normalised:
        return Verdict.no_verification, "no verification row for this task"
    if normalised == "failed" or normalised == "phantom":
        return Verdict.judged_fail, f"verification result={normalised}"
    if normalised == "bypassed":
        return (
            Verdict.skipped_bypass,
            "verification was SKIPPED (force-done). An unverified change, not a failed one -- "
            "but not a judged one either.",
        )
    if review_passed == 0:
        return Verdict.judged_fail, "conformance review_passed=false"
    if review_passed is None:
        if not has_criteria(criteria):
            return (
                Verdict.unjudged_no_criteria,
                "review_passed is NULL because the task has no acceptance_criteria -- nothing "
                "was judged. Today this reads as a pass.",
            )
        return (
            Verdict.unjudged_not_run,
            "a criterion exists but the conformance review has not returned a verdict yet",
        )
    return Verdict.judged_pass, f"conformance passed (result={normalised})"


def refuses(verdict: str, *, current_mode: Optional[str] = None) -> bool:
    """Would the gate refuse ``done`` on this verdict, in this posture?

    ``judged_fail`` is refused in every mode including ``off`` -- that is the EXISTING gate's
    behaviour and this module does not weaken it. Everything else is refused only under
    ``enforce``.
    """
    if verdict == Verdict.judged_fail:
        return True
    if (current_mode or done_mode()) != "enforce":
        return False
    return verdict in REFUSED_WHEN_ENFORCING
