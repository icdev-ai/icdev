# CUI // SP-CTI
"""The firing-alerts panel has to say what the alert already knows.

Measured 2026-08-10, clearing 12 HITL alerts by hand: the page rendered
WATCHCON / Severity / Title / Source / Project / Auto-Healed / Created and never
rendered `description` — where pr_watcher puts the cause, the resume budget and
the PR link. Those are the only facts that decide which remediation can work, so
they were read out of the database instead.
"""
from __future__ import annotations

import pytest

from tools.kanban import hitl_alert_view as view


def _alert(description, source="pr_watcher:hitl:agov-det-06"):
    return {"source": source, "description": description}


CAP = ("resume cap reached (5/5) after merge_conflict. "
       "PR: https://github.com/icdev-ai/icdev/pull/1479")
CI_NEVER = ("no CI checks ever ran, and a re-trigger did not start them. "
            "PR: https://github.com/icdev-ai/icdev/pull/1462")


# ── the parse ───────────────────────────────────────────────────────────────
def test_the_real_description_yields_cause_cycle_and_pr():
    v = view.parse_alert(_alert(CAP))
    assert v["task_id"] == "agov-det-06"
    assert v["cause"] == "merge_conflict"
    assert v["cause_label"] == "merge conflict"
    assert v["cycle_display"] == "5/5"
    assert v["pr_number"] == "1479"
    assert v["pr_url"].endswith("/pull/1479")


def test_the_other_raise_site_is_recognised():
    """CI-never-fired writes a different sentence and has no resume cycle."""
    v = view.parse_alert(_alert(CI_NEVER))
    assert v["cause"] == view.CAUSE_CI_NEVER_FIRED
    assert v["cycle_display"] == ""
    assert v["pr_number"] == "1462"


@pytest.mark.parametrize("cause", ["merge_conflict", "ci_failed", "changes_requested"])
def test_every_resume_class_parses(cause):
    v = view.parse_alert(_alert(f"resume cap reached (5/5) after {cause}. PR: x/pull/9"))
    assert v["cause"] == cause


def test_a_trailing_period_is_not_part_of_the_url():
    v = view.parse_alert(_alert("resume cap reached (5/5) after ci_failed. PR: https://x/pull/7."))
    assert v["pr_url"] == "https://x/pull/7"


# ── it must never be the thing that breaks the page ─────────────────────────
def test_an_unrecognised_description_renders_as_unknown_rather_than_raising():
    """The text is prose written by another module. If it drifts, the page still
    has to render — a 500 because a sentence changed shape is worse than a row
    that says 'unknown'."""
    v = view.parse_alert(_alert("something nobody has seen before"))
    assert v["cause"] == view.CAUSE_UNKNOWN
    assert v["cycle_display"] == "" and v["pr_url"] == ""


def test_a_missing_description_is_not_an_error():
    assert view.parse_alert({"source": "pr_watcher:hitl:t-1"})["cause"] == view.CAUSE_UNKNOWN


def test_a_non_hitl_alert_is_not_parsed_at_all():
    """Other sources keep today's rendering; inventing a task id out of them is
    the bug the sweep's parse already had to fix."""
    assert view.parse_alert(_alert(CAP, source="cpu_monitor:host-7")) is None
    assert view.parse_alert({}) is None
    assert view.task_id_from_source("cpu_monitor:host-7") == ""


# ── which verbs can actually work ───────────────────────────────────────────
def test_rebase_is_refused_for_a_real_conflict():
    """10 of the 12 alerts firing on 2026-08-10 were merge_conflict, and
    rebase_and_push refuses every one of them (pushed=False -> 422). Offering
    the button anyway is what teaches people the controls do not work."""
    allowed, why = view.action_is_available("rebase", "merge_conflict")
    assert allowed is False
    assert "cannot clear a real conflict" in why


def test_rebase_is_refused_when_ci_never_ran():
    allowed, why = view.action_is_available("rebase", view.CAUSE_CI_NEVER_FIRED)
    assert allowed is False and "nothing for a rebase to re-trigger" in why


def test_rebase_is_ALLOWED_for_a_ci_failure():
    """The cheap recovery this button exists for — do not over-block it."""
    assert view.action_is_available("rebase", "ci_failed") == (True, "")


def test_an_unknown_cause_does_not_block_the_operator():
    """Fail open on the VERB, because the alternative is a panel with no working
    control at all the moment the description text drifts. The verb itself is
    still gated by its own runner, which refuses a conflict on its own."""
    assert view.action_is_available("rebase", view.CAUSE_UNKNOWN)[0] is True


@pytest.mark.parametrize("action", ["dismiss", "requeue"])
@pytest.mark.parametrize("cause", ["merge_conflict", "ci_failed", "ci_never_fired"])
def test_dismiss_and_requeue_stay_available_for_every_cause(action, cause):
    """Dismiss is a human saying 'handled', true regardless of cause. Requeue
    abandons the branch, which is sometimes right — it is de-emphasised in the
    UI, not removed, because removing it would strand a task with no verb."""
    assert view.action_is_available(action, cause) == (True, "")
