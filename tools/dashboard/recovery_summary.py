# CUI // SP-CTI
"""Did pr_watcher actually RECOVER a PR, or just try? (rem-hyg-16)

The Autonomous Recovery panel used to render one line per ``pr_watcher.resume``
/ ``pr_watcher.rebase`` audit row and headline the count as "N auto-recovered
(24h)", under a section titled "Recovered without a human".

Those rows are ATTEMPTS. The resume budget is five, so the overstatement is
structural rather than incidental: a task retried to the cap and then fixed by
hand contributes FIVE rows to a list of recoveries, while a task genuinely fixed
on the first attempt contributes one. The worse the outcome, the bigger the
number.

Measured on the live board 2026-08-20 — the panel read "14 auto-recovered
(24h)":

    qa-fail-e2e-baseurl-01   5 attempts, escalated  -> needed a human
    task-c49fb2727d          5 attempts, escalated  -> needed a human
    cef-ui-01                1 attempt              -> unresolved
    cef-ci-01                1 attempt, merged      -> recovered
    cef-ci-02                1 rebase,  merged      -> recovered
    rem-hyg-10               1 attempt, merged      -> recovered

Six tasks, three recovered. ``task-c49fb2727d`` was resumed five times,
escalated, and then fixed BY HAND — its real causes were a 16-commit-stale
branch and a host-dependent ``as_posix()`` path comparison, neither of which an
LLM resume can address, because the branch it is asked to repair looks fine
locally.

WHY ``escalate`` IS THE DISCRIMINATOR. It is the watcher's OWN verdict — it logs
"resume cap reached, manual intervention required" — so it needs no inference. A
``merge`` recorded AFTER an escalation is a human's merge and must not read as
autonomous, which is why an escalated task can never be classified ``recovered``
no matter what follows it.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

#: The watcher tried something. THIS IS THE WRITER'S VOCABULARY, NOT A GUESS:
#: every value is an ``action=`` literal in tools/ci/pr_watcher.py. The first
#: version listed only ``resume`` and a bare ``rebase`` -- and in the seven days
#: measured on 2026-09-02 the watcher wrote no ``rebase`` row at all (it writes
#: ``rebase_failed``) and its ``ci_retrigger`` rows (closing and reopening a PR
#: to re-fire the workflows, which IS a recovery attempt) were never counted.
#: rmf-disc-01 was rebased twice and read as one attempt; rmf-inert-01's CI
#: re-fire read as none.
_ATTEMPT_KINDS = ("resume", "rebase", "rebase_failed", "ci_retrigger")
#: The watcher withdrew an attempt it had already recorded (nothing to send, or
#: the rebase never ran). Its own accounting says the attempt did not happen;
#: the summary must agree, or a refunded resume reads as a retry loop.
_REFUND_KINDS = ("resume_refund", "rebase_refund")
#: The audit_trail ``action`` values the panel's query must fetch -- exported so
#: the SQL in app.py and the classifier here cannot drift apart again. They did:
#: the query fetched four action names and the classifier knew two of them.
AUDIT_ACTIONS = tuple(
    f"pr_watcher.{k}" for k in (*_ATTEMPT_KINDS, *_REFUND_KINDS, "escalate", "merge")
)
#: A task in one of these states is CLOSED. Mirrors ``_closed_statuses`` in
#: tools/dashboard/app.py::_compute_project_progress EXACTLY (a structural test
#: reads both) -- two hand-maintained copies of "what counts as closed" is the
#: defect the project cards carried until 2026-08-28.
CLOSED_STATUSES = ("done", "decomposed", "cancelled", "merged")

RECOVERED = "recovered"            # closed, and the watcher never gave up
NEEDED_A_HUMAN = "needed_a_human"  # the watcher escalated — its own verdict
UNRESOLVED = "unresolved"          # attempted, no outcome yet


def summarize_recovery(
    rows: Iterable[Dict[str, Any]],
    *,
    limit: int = 20,
    task_status: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Collapse pr_watcher audit rows into ONE entry per task, with an outcome.

    ``task_status`` maps task id to its CURRENT board status and is the second
    outcome signal. The watcher records a merge it performed as ``merge``; a
    merge it merely CONFIRMED ("reconciled: PR is MERGED") lands only as a
    status transition to ``done``. Measured 2026-09-02: rmf-disc-01 read "still
    trying" for an hour after the watcher itself had marked it done, because
    nothing here ever looked at the board. A closed row after an attempt the
    watcher never gave up on is a recovery; ``escalate`` still wins, because a
    row closed after the watcher asked for a human was closed by that human.

    ``rows`` are audit_trail records carrying ``action`` (``pr_watcher.*``), a
    JSON ``d`` payload holding ``task_id``/``reason``, and ``created_at``.

    A task with no attempt in the window is dropped: something that merged
    without the watcher ever trying is not a recovery, and counting it would
    inflate the figure in the opposite direction.
    """
    by_task: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        record = dict(row)
        kind = str(record.get("action") or "").split(".")[-1]
        try:
            payload = json.loads(record.get("d") or "{}")
        except (ValueError, TypeError):
            payload = {}
        task_id = payload.get("task_id")
        if not task_id:
            continue
        entry = by_task.setdefault(task_id, {
            "task_id": task_id, "attempts": 0, "kind": "", "reason": "",
            "at": None, "escalated": False, "merged": False,
            "board_status": None,
        })
        if kind in _ATTEMPT_KINDS:
            entry["attempts"] += 1
            entry["kind"] = kind
            entry["reason"] = str(payload.get("reason") or "")[:160]
            entry["at"] = record.get("created_at")
        elif kind == "escalate":
            entry["escalated"] = True
        elif kind == "merge":
            entry["merged"] = True
        elif kind in _REFUND_KINDS:
            entry["attempts"] = max(0, entry["attempts"] - 1)

    out: List[Dict[str, Any]] = []
    for entry in by_task.values():
        if not entry["attempts"]:
            continue
        # Order matters: escalation WINS over a later merge, because that merge
        # is the human the escalation asked for.
        status = (task_status or {}).get(entry["task_id"])
        entry["board_status"] = status
        if entry["escalated"]:
            entry["outcome"] = NEEDED_A_HUMAN
        elif entry["merged"] or status in CLOSED_STATUSES:
            entry["outcome"] = RECOVERED
        else:
            entry["outcome"] = UNRESOLVED
        out.append(entry)

    out.sort(key=lambda r: (r.get("at") or ""), reverse=True)
    return out[:limit]
