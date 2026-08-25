# CUI // SP-CTI
"""OPT-72: tools/ci/error_classifier.py — PR/CI state classifier.

Adapted from jonwiggins/optio/packages/shared (MIT).
See https://github.com/jonwiggins/optio

Pure-Python classifiers that map `gh pr view --json ...` output + raw
CI log text onto a KanbanState value. No LLM, no network — so the
pr_watcher loop (OPT-70) can decide its next move deterministically.

Consumers provide `pr_json`, which is expected to have at least these
top-level keys (anything missing is treated as "unknown"):

    state:          "OPEN" | "CLOSED" | "MERGED"
    mergeable:      "MERGEABLE" | "CONFLICTING" | "UNKNOWN"
    statusCheckRollup: list of {state, conclusion, name}
    reviews:        list of {state, author}
    updatedAt:      ISO timestamp
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Collection, FrozenSet, List, Optional

from tools.kanban.state_machine import KanbanState


# Patterns the classifier scans for inside CI log text to refine the
# verdict. These are deliberately broad — false positives just mean the
# caller falls back to a coarser state.
_CI_LOG_HINTS = {
    # Order matters — classify_ci_log_failure iterates in insertion order
    # and returns the first hit, so more-specific patterns must come first.
    "merge_conflict": re.compile(
        r"(CONFLICT|Merge conflict|Automatic merge failed)",
        re.IGNORECASE,
    ),
    "security_failure": re.compile(
        r"(bandit.*issue|safety.*vulnerable|trivy.*HIGH)",
        re.IGNORECASE,
    ),
    "lint_failure": re.compile(
        r"(ruff\s+check[^\n]*fail|ruff\s+check[^\n]*E\d+|"
        r"flake8\s+error|pylint\s+.*\serror)",
        re.IGNORECASE,
    ),
    "test_failure": re.compile(
        r"(\d+\s+failed|test_\w+\s+FAILED|pytest\s+.*\sfailed|"
        r"FAILED\s+tests/)",
        re.IGNORECASE,
    ),
}


# ────────────────────────────────────────────────────────────────────────────
# Per-signal classifiers
# ────────────────────────────────────────────────────────────────────────────


def _rollup(pr_json: dict) -> List[dict]:
    return list(pr_json.get("statusCheckRollup") or [])


# ── the REQUIRED set (task-det-295a9bb95e) ─────────────────────────────────
#
# Every predicate below used to read EVERY check in the rollup, and the forge
# does not: branch protection names the checks a merge needs (here Lint / Test
# / Security Scan / Helm Lint) and the rest are advisory. `Test (Windows)` is
# non-required ON PURPOSE — icdev-ci.yml: "a Windows-only flake cannot block a
# merge" — yet one red Windows run made `is_ci_failed` True, so pr_watcher
# spent five resumes on a branch with nothing wrong in it and escalated to
# "manual intervention required" (PR #1859, and #1841 before it). The forge
# then merged both, because the forge only ever asked about the required set.
#
# `required` is that set, resolved by the CALLER from branch protection (see
# merge_readiness.fetch_required_checks) — never a list written here, which
# would drift the moment a check is promoted. Three readings, kept apart:
#   None or empty  UNRESOLVED. Every check counts, exactly as before. An empty
#                  set is treated as unresolved rather than as "nothing is
#                  required" because merging on nothing is the direction with
#                  consequences.
#   a set          only the named checks decide. A failing check outside it is
#                  reported by `ignored_failures`, never silently dropped.
#   absent member  a required check with NO rollup entry yet is "not yet
#                  green", never passing — GitHub would refuse that merge.


def _check_name(check: dict) -> str:
    """CheckRun entries carry `name`; StatusContext entries carry `context`."""
    return str(check.get("name") or check.get("context") or "").strip()


def _required_set(required: Optional[Collection[str]]) -> Optional[FrozenSet[str]]:
    names = frozenset(str(n).strip() for n in (required or ()) if str(n).strip())
    return names or None


def _deciding(pr_json: dict, required: Optional[Collection[str]]) -> List[dict]:
    """The rollup entries that decide CI state: all of them, or the required ones."""
    names = _required_set(required)
    rollup = _rollup(pr_json)
    if names is None:
        return rollup
    return [c for c in rollup if _check_name(c) in names]


def _has_failed(check: dict) -> bool:
    conclusion = (check.get("conclusion") or "").upper()
    state = (check.get("state") or "").upper()
    return (conclusion in ("FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED")
            or state == "FAILURE")


def ignored_failures(
    pr_json: dict, *, required: Optional[Collection[str]] = None,
) -> List[str]:
    """Names of FAILING checks that the required set sets aside.

    Empty when nothing is set aside — including when `required` is unresolved,
    since then nothing is ignored. A caller that ignores a failure must be able
    to say so on its audit row; a silently dropped red check is the same shape
    as the `awk '{print $2}'` that could not see one.
    """
    names = _required_set(required)
    if names is None:
        return []
    return sorted({_check_name(c) or "unnamed check" for c in _rollup(pr_json)
                   if _check_name(c) not in names and _has_failed(c)})


def is_ci_failed(
    pr_json: dict, *, required: Optional[Collection[str]] = None,
) -> bool:
    """True if any DECIDING check in the rollup conclusively failed.

    With `required` resolved, a failure outside that set is not a CI failure —
    it is an advisory red, listed by `ignored_failures`.
    """
    return any(_has_failed(c) for c in _deciding(pr_json, required))


def is_merge_conflict(pr_json: dict) -> bool:
    return (pr_json.get("mergeable") or "").upper() == "CONFLICTING"


def is_changes_requested(pr_json: dict) -> bool:
    for review in pr_json.get("reviews") or []:
        state = (review.get("state") or "").upper()
        if state == "CHANGES_REQUESTED":
            return True
    return False


def is_passing(
    pr_json: dict, *, required: Optional[Collection[str]] = None,
) -> bool:
    """True if every DECIDING check finished green. Approval not considered.

    SKIPPED/NEUTRAL count as green: a skipped conditional job is a *conclusive*
    non-failure, not a pending one (`Docker Build` skips on doc-only PRs). An
    empty rollup is NOT passing — it means no CI has reported yet, which is
    "unknown", not "green".

    With `required` resolved, every required check must be PRESENT and green;
    a required check that has not reported is unknown, not green, so a partial
    rollup can never read as a merge the forge would accept. Checks outside the
    set do not count either way.

    Handles both rollup shapes `gh` emits: CheckRun entries carry `conclusion`
    and no `state`; StatusContext entries carry `state` and no `conclusion`.
    """
    names = _required_set(required)
    rollup = _deciding(pr_json, required)
    if not rollup:
        return False
    if names is not None and not names <= {_check_name(c) for c in rollup}:
        return False
    for check in rollup:
        conclusion = (check.get("conclusion") or "").upper()
        state = (check.get("state") or "").upper()
        if conclusion:
            if conclusion not in ("SUCCESS", "NEUTRAL", "SKIPPED"):
                return False
        elif state != "SUCCESS":
            return False
    return True


def is_approved(pr_json: dict) -> bool:
    """True if at least one review is APPROVED."""
    for review in pr_json.get("reviews") or []:
        if (review.get("state") or "").upper() == "APPROVED":
            return True
    return False


def is_approved_and_passing(
    pr_json: dict, *, required: Optional[Collection[str]] = None,
) -> bool:
    """Approved AND all deciding checks green."""
    return is_approved(pr_json) and is_passing(pr_json, required=required)


def is_in_progress(pr_json: dict) -> bool:
    """True if any check is still pending/running."""
    for check in _rollup(pr_json):
        state = (check.get("state") or "").upper()
        conclusion = (check.get("conclusion") or "").upper()
        if state in ("PENDING", "IN_PROGRESS", "QUEUED") or conclusion == "":
            if conclusion not in ("SUCCESS", "FAILURE"):
                return True
    return False


def is_stale(pr_json: dict, max_age_hours: int = 24) -> bool:
    """True if the PR hasn't been updated in `max_age_hours` hours."""
    updated = pr_json.get("updatedAt") or pr_json.get("updated_at")
    if not updated:
        return False
    try:
        # GitHub ISO timestamps sometimes end in 'Z'
        ts = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    return delta > timedelta(hours=max_age_hours)


# ────────────────────────────────────────────────────────────────────────────
# Composite classifier
# ────────────────────────────────────────────────────────────────────────────


def classify_pr_state(
    pr_json: dict,
    ci_logs: str = "",
    *,
    max_age_hours: int = 24,
    require_approval: bool = True,
    required: Optional[Collection[str]] = None,
) -> KanbanState:
    """Return the KanbanState that best describes the PR.

    ``required`` is the branch-protection required-check set (see the note
    above `ignored_failures`). Unresolved (None/empty) reads every check, which
    is the pre-task-det-295a9bb95e behaviour.

    Precedence:
        1. merged  → DONE
        2. explicit merge-conflict → MERGE_CONFLICT
        3. reviewer requested changes → CHANGES_REQUESTED
        4. CI failed → CI_FAILED
        5. green (+ approved, when ``require_approval``) → DONE
        6. still running checks → PR_OPENED
        7. stale → FAILED
        8. default → PR_OPENED

    ``require_approval`` mirrors the caller's merge policy. It defaults to True
    (unchanged behavior), but a caller configured for unattended merge must pass
    False: without it a green, unapproved PR can never reach DONE, so the caller
    never evaluates its own approval config and the PR waits until it ages into
    ``is_stale`` → FAILED and gets pointlessly retried. That is exactly what
    stranded kanban PR #1151 for 14h with 9/9 checks green.
    """
    top_state = (pr_json.get("state") or "").upper()
    if top_state == "MERGED":
        return KanbanState.DONE

    if is_merge_conflict(pr_json):
        return KanbanState.MERGE_CONFLICT

    # CI log hint: a loud "Merge conflict" in logs still trumps an
    # unknown mergeable flag.
    if ci_logs and _CI_LOG_HINTS["merge_conflict"].search(ci_logs):
        return KanbanState.MERGE_CONFLICT

    if is_changes_requested(pr_json):
        return KanbanState.CHANGES_REQUESTED

    if is_ci_failed(pr_json, required=required):
        return KanbanState.CI_FAILED

    # A log hint is a SECOND opinion on a failure the rollup already shows. The
    # logs the watcher fetches are the FAILING jobs' logs, so when the only red
    # check is one the required set sets aside, these are exactly the lines
    # that would match — and the hint must not overrule the set. It still
    # counts when NO check in the rollup has failed (the case it was for).
    if (ci_logs and not is_ci_failed(pr_json)
            and _CI_LOG_HINTS["test_failure"].search(ci_logs)):
        return KanbanState.CI_FAILED

    if (is_approved_and_passing(pr_json, required=required) if require_approval
            else is_passing(pr_json, required=required)):
        return KanbanState.DONE

    if is_in_progress(pr_json):
        return KanbanState.PR_OPENED

    if is_stale(pr_json, max_age_hours=max_age_hours):
        return KanbanState.FAILED

    return KanbanState.PR_OPENED


def classify_ci_log_failure(ci_logs: str) -> Optional[str]:
    """Return a short label identifying the dominant failure category."""
    if not ci_logs:
        return None
    for label, pattern in _CI_LOG_HINTS.items():
        if pattern.search(ci_logs):
            return label
    return None
