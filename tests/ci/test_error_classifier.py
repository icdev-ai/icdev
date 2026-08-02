# CUI // SP-CTI
"""OPT-72: tests for tools/ci/error_classifier.py."""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timedelta, timezone


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ci import error_classifier as ec  # noqa: E402
from tools.kanban.state_machine import KanbanState  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# Per-signal classifiers
# ────────────────────────────────────────────────────────────────────────────


def test_is_ci_failed_detects_failure_conclusion():
    pr = {"statusCheckRollup": [{"conclusion": "FAILURE", "name": "tests"}]}
    assert ec.is_ci_failed(pr)


def test_is_ci_failed_detects_cancelled():
    pr = {"statusCheckRollup": [{"conclusion": "CANCELLED", "name": "lint"}]}
    assert ec.is_ci_failed(pr)


def test_is_ci_failed_ignores_success():
    pr = {"statusCheckRollup": [{"conclusion": "SUCCESS", "name": "tests"}]}
    assert not ec.is_ci_failed(pr)


def test_is_merge_conflict():
    assert ec.is_merge_conflict({"mergeable": "CONFLICTING"})
    assert not ec.is_merge_conflict({"mergeable": "MERGEABLE"})
    assert not ec.is_merge_conflict({})


def test_is_changes_requested():
    pr = {"reviews": [{"state": "CHANGES_REQUESTED", "author": "alice"}]}
    assert ec.is_changes_requested(pr)
    pr_approved = {"reviews": [{"state": "APPROVED", "author": "bob"}]}
    assert not ec.is_changes_requested(pr_approved)


def test_is_approved_and_passing_true_case():
    pr = {
        "reviews": [{"state": "APPROVED", "author": "bob"}],
        "statusCheckRollup": [
            {"conclusion": "SUCCESS", "name": "tests"},
            {"conclusion": "SUCCESS", "name": "lint"},
        ],
    }
    assert ec.is_approved_and_passing(pr)


def test_is_approved_and_passing_fails_without_approval():
    pr = {
        "reviews": [],
        "statusCheckRollup": [{"conclusion": "SUCCESS", "name": "tests"}],
    }
    assert not ec.is_approved_and_passing(pr)


def test_is_approved_and_passing_fails_with_check_failure():
    pr = {
        "reviews": [{"state": "APPROVED", "author": "bob"}],
        "statusCheckRollup": [
            {"conclusion": "SUCCESS", "name": "tests"},
            {"conclusion": "FAILURE", "name": "lint"},
        ],
    }
    assert not ec.is_approved_and_passing(pr)


def test_is_in_progress_detects_pending_check():
    pr = {"statusCheckRollup": [{"state": "PENDING", "name": "tests"}]}
    assert ec.is_in_progress(pr)
    pr_done = {"statusCheckRollup": [{"conclusion": "SUCCESS", "name": "tests"}]}
    assert not ec.is_in_progress(pr_done)


def test_is_stale_true_when_updated_long_ago():
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    assert ec.is_stale({"updatedAt": old}, max_age_hours=24)


def test_is_stale_false_when_recent():
    new = datetime.now(timezone.utc).isoformat()
    assert not ec.is_stale({"updatedAt": new})


def test_is_stale_handles_missing_field():
    assert not ec.is_stale({})


# ────────────────────────────────────────────────────────────────────────────
# Composite classifier
# ────────────────────────────────────────────────────────────────────────────


def test_classify_merged_is_done():
    pr = {"state": "MERGED"}
    assert ec.classify_pr_state(pr) == KanbanState.DONE


def test_classify_merge_conflict():
    pr = {"state": "OPEN", "mergeable": "CONFLICTING"}
    assert ec.classify_pr_state(pr) == KanbanState.MERGE_CONFLICT


def test_classify_changes_requested():
    pr = {
        "state": "OPEN",
        "mergeable": "MERGEABLE",
        "reviews": [{"state": "CHANGES_REQUESTED", "author": "a"}],
    }
    assert ec.classify_pr_state(pr) == KanbanState.CHANGES_REQUESTED


def test_classify_ci_failed():
    pr = {
        "state": "OPEN",
        "mergeable": "MERGEABLE",
        "statusCheckRollup": [{"conclusion": "FAILURE", "name": "tests"}],
    }
    assert ec.classify_pr_state(pr) == KanbanState.CI_FAILED


def test_classify_approved_passing_is_done():
    pr = {
        "state": "OPEN",
        "mergeable": "MERGEABLE",
        "reviews": [{"state": "APPROVED", "author": "b"}],
        "statusCheckRollup": [{"conclusion": "SUCCESS", "name": "tests"}],
    }
    assert ec.classify_pr_state(pr) == KanbanState.DONE


def test_classify_in_progress_is_pr_opened():
    pr = {
        "state": "OPEN",
        "mergeable": "MERGEABLE",
        "statusCheckRollup": [{"state": "IN_PROGRESS", "name": "tests"}],
    }
    assert ec.classify_pr_state(pr) == KanbanState.PR_OPENED


def test_classify_stale_is_failed():
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    pr = {
        "state": "OPEN",
        "mergeable": "MERGEABLE",
        "updatedAt": old,
    }
    assert ec.classify_pr_state(pr) == KanbanState.FAILED


def test_classify_falls_back_to_pr_opened_when_ambiguous():
    pr = {
        "state": "OPEN",
        "mergeable": "MERGEABLE",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    assert ec.classify_pr_state(pr) == KanbanState.PR_OPENED


def test_classify_ci_log_merge_conflict_overrides_unknown():
    pr = {"state": "OPEN", "mergeable": "UNKNOWN"}
    result = ec.classify_pr_state(pr, ci_logs="CONFLICT (content): Merge conflict in foo.py")
    assert result == KanbanState.MERGE_CONFLICT


def test_classify_ci_log_test_failure_sets_ci_failed():
    pr = {"state": "OPEN", "mergeable": "MERGEABLE"}
    result = ec.classify_pr_state(pr, ci_logs="FAILED tests/test_x.py::test_y")
    assert result == KanbanState.CI_FAILED


def test_classify_ci_log_failure_label():
    assert ec.classify_ci_log_failure("ruff check failed with E501") == "lint_failure"
    assert ec.classify_ci_log_failure("bandit issue: B608") == "security_failure"
    assert ec.classify_ci_log_failure("") is None
    assert ec.classify_ci_log_failure("totally clean output") is None


# ────────────────────────────────────────────────────────────────────────────
# Unattended-merge policy (require_approval)
#
# Regression guard: `require_approval=True` was hardcoded into the DONE path via
# is_approved_and_passing(), so a green-but-unapproved PR could never reach DONE
# no matter what the caller's merge config said. The caller's own
# `auto_merge_require_approval: false` was only read *inside* the DONE branch —
# unreachable. Real kanban PRs sat green for 14h+ and then aged into FAILED.
# ────────────────────────────────────────────────────────────────────────────


def _green_unapproved_pr():
    """9 CheckRuns green (one SKIPPED), no reviews — the real #1151 shape."""
    return {
        "state": "OPEN",
        "mergeable": "MERGEABLE",
        "reviews": [],
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "statusCheckRollup": [
            {"name": "Lint", "conclusion": "SUCCESS"},
            {"name": "Test", "conclusion": "SUCCESS"},
            {"name": "Security Scan", "conclusion": "SUCCESS"},
            {"name": "Docker Build", "conclusion": "SKIPPED"},
        ],
    }


def test_green_unapproved_is_not_done_when_approval_required():
    assert ec.classify_pr_state(_green_unapproved_pr()) == KanbanState.PR_OPENED


def test_green_unapproved_is_done_when_approval_not_required():
    assert ec.classify_pr_state(
        _green_unapproved_pr(), require_approval=False
    ) == KanbanState.DONE


def test_require_approval_false_still_respects_ci_failure():
    pr = _green_unapproved_pr()
    pr["statusCheckRollup"].append({"name": "E2E", "conclusion": "FAILURE"})
    assert ec.classify_pr_state(pr, require_approval=False) == KanbanState.CI_FAILED


def test_require_approval_false_still_respects_merge_conflict():
    pr = _green_unapproved_pr()
    pr["mergeable"] = "CONFLICTING"
    assert ec.classify_pr_state(
        pr, require_approval=False
    ) == KanbanState.MERGE_CONFLICT


def test_require_approval_false_still_respects_changes_requested():
    pr = _green_unapproved_pr()
    pr["reviews"] = [{"state": "CHANGES_REQUESTED", "author": "a"}]
    assert ec.classify_pr_state(
        pr, require_approval=False
    ) == KanbanState.CHANGES_REQUESTED


def test_running_ci_is_not_done_even_without_approval_requirement():
    pr = _green_unapproved_pr()
    pr["statusCheckRollup"].append({"name": "E2E", "state": "IN_PROGRESS"})
    assert ec.classify_pr_state(pr, require_approval=False) == KanbanState.PR_OPENED


# ── is_passing ──────────────────────────────────────────────────────────────


def test_is_passing_treats_skipped_and_neutral_as_green():
    assert ec.is_passing({"statusCheckRollup": [
        {"conclusion": "SUCCESS"}, {"conclusion": "SKIPPED"},
        {"conclusion": "NEUTRAL"},
    ]})


def test_is_passing_false_on_empty_rollup():
    # No CI reported yet is "unknown", not "green" — must not auto-merge.
    assert not ec.is_passing({"statusCheckRollup": []})
    assert not ec.is_passing({})


def test_is_passing_handles_status_context_shape():
    # StatusContext entries carry `state` and no `conclusion`.
    assert ec.is_passing({"statusCheckRollup": [{"context": "ci", "state": "SUCCESS"}]})
    assert not ec.is_passing({"statusCheckRollup": [{"context": "ci", "state": "PENDING"}]})


def test_is_approved_and_passing_still_requires_both():
    green = _green_unapproved_pr()
    assert not ec.is_approved_and_passing(green)
    green["reviews"] = [{"state": "APPROVED", "author": "b"}]
    assert ec.is_approved_and_passing(green)
