#!/usr/bin/env python3
# CUI // SP-CTI
"""What a firing HITL alert actually says, and which remediations can work.

`pr_watcher._hitl_alert` writes everything an operator needs into `description`:

    resume cap reached (5/5) after merge_conflict. PR: https://.../pull/1479
    no CI checks ever ran, and a re-trigger did not start them. PR: .../pull/1462

The monitoring page never rendered that field. So the cause, the resume budget
and the PR link — the only facts that decide which of the three buttons can do
anything — were invisible, and on 2026-08-10 they were read out of the database
instead of the page while 12 alerts were cleared by hand.

Parsed here rather than in the template, and rather than as new `alerts` columns:
the row already carries the text, a migration to duplicate it would need
backfilling, and the API has to reach the same verdict as the button or the two
diverge. One parse, two callers.

WHY A CAUSE GATES A VERB
========================
`rebase` runs `rebase_and_push`, which correctly refuses a branch with a true
conflict — it returns pushed=False and the dashboard shows " failed" with no
diagnosis. 10 of the 12 alerts firing on 2026-08-10 were `merge_conflict`, so the
most prominent button on the panel could not have helped any of them. The
template already argues this position for non-HITL rows ("a control that does
nothing teaches people to distrust the ones that work"); this applies it to the
cause.

`requeue` is not offered as a substitute there on purpose: it abandons the branch
for a clean rebuild, and agov-det-07 carried 46 files that existed nowhere else.
Losing that is worse than the alert.
"""
from __future__ import annotations

import re
from typing import Optional

#: The source prefix pr_watcher raises HITL alerts under.
HITL_SOURCE_PREFIX = "pr_watcher:hitl:"

#: `resume cap reached (5/5) after merge_conflict.`
_CAP_RE = re.compile(r"resume cap reached \((?P<cycle>\d+)/(?P<max>\d+)\)"
                     r"(?:\s+after\s+(?P<cause>[a-z_]+))?", re.I)
#: `PR: https://github.com/o/r/pull/1479`
_PR_RE = re.compile(r"PR:\s*(?P<url>\S+)")
_PR_NUMBER_RE = re.compile(r"/pull/(?P<number>\d+)")

CAUSE_CI_NEVER_FIRED = "ci_never_fired"
CAUSE_UNKNOWN = "unknown"

#: Human labels, so the page does not print a bare enum at an operator.
CAUSE_LABELS = {
    "merge_conflict": "merge conflict",
    "ci_failed": "CI failed",
    "changes_requested": "changes requested",
    CAUSE_CI_NEVER_FIRED: "CI never ran",
    CAUSE_UNKNOWN: "unknown",
}

#: Causes a rebase provably cannot clear, and the reason to show instead.
#:
#: Keyed by cause rather than by verb: a new verb should have to state which
#: causes it applies to, rather than defaulting to "all of them" — which is how
#: rebase came to be offered for a conflict it refuses.
REBASE_BLOCKED = {
    "merge_conflict":
        "a rebase cannot clear a real conflict — resolve it on the branch, "
        "then this alert clears itself",
    CAUSE_CI_NEVER_FIRED:
        "CI never ran, so there is nothing for a rebase to re-trigger",
}


def is_hitl_source(source) -> bool:
    return isinstance(source, str) and source.startswith(HITL_SOURCE_PREFIX)


def task_id_from_source(source) -> str:
    """`pr_watcher:hitl:agov-det-06` -> `agov-det-06`; "" for anything else.

    Requires the prefix rather than splitting on it: `split()` on a missing
    delimiter returns the WHOLE string, which invents a task id out of a foreign
    alert source.
    """
    if not is_hitl_source(source):
        return ""
    return source[len(HITL_SOURCE_PREFIX):].strip()


def parse_alert(alert: dict) -> Optional[dict]:
    """Everything the row knows, or None when this is not a HITL alert.

    Never raises on a description it does not recognise: an alert whose text has
    drifted still has to render, and a page that 500s because a string changed
    shape is worse than one that says "unknown".
    """
    source = (alert or {}).get("source")
    task_id = task_id_from_source(source)
    if not task_id:
        return None

    text = str((alert or {}).get("description") or "")
    view = {
        "task_id": task_id,
        "cause": CAUSE_UNKNOWN,
        "cause_label": CAUSE_LABELS[CAUSE_UNKNOWN],
        "cycle": None,
        "max_cycles": None,
        "cycle_display": "",
        "pr_url": "",
        "pr_number": "",
    }

    cap = _CAP_RE.search(text)
    if cap:
        view["cycle"] = int(cap.group("cycle"))
        view["max_cycles"] = int(cap.group("max"))
        view["cycle_display"] = f"{view['cycle']}/{view['max_cycles']}"
        if cap.group("cause"):
            view["cause"] = cap.group("cause").lower()
    elif "no CI checks ever ran" in text:
        view["cause"] = CAUSE_CI_NEVER_FIRED

    view["cause_label"] = CAUSE_LABELS.get(view["cause"], view["cause"].replace("_", " "))

    pr = _PR_RE.search(text)
    if pr:
        view["pr_url"] = pr.group("url").rstrip(".,")
        number = _PR_NUMBER_RE.search(view["pr_url"])
        if number:
            view["pr_number"] = number.group("number")

    return view


def rebase_refusal(cause) -> str:
    """Why a rebase cannot help this cause, or "" when it can.

    The single place the button and the API both ask, so a disabled control and
    a refused request always agree.
    """
    return REBASE_BLOCKED.get(str(cause or "").lower(), "")


def action_is_available(action, cause) -> tuple:
    """(allowed, reason). `dismiss` and `requeue` are always allowed.

    `dismiss` is a human saying "I have handled this", which is true regardless
    of cause. `requeue` stays available because abandoning the branch is
    sometimes right — it is just never the *default* the panel should push
    somebody toward, so the UI de-emphasises it rather than removing it.
    """
    if str(action or "").lower() != "rebase":
        return True, ""
    refusal = rebase_refusal(cause)
    return (not refusal), refusal
