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
from typing import Any, Dict, Iterable, List

#: The watcher tried something.
_ATTEMPT_KINDS = ("resume", "rebase")

RECOVERED = "recovered"            # merged, and the watcher never gave up
NEEDED_A_HUMAN = "needed_a_human"  # the watcher escalated — its own verdict
UNRESOLVED = "unresolved"          # attempted, no outcome yet


def summarize_recovery(rows: Iterable[Dict[str, Any]], *, limit: int = 20) -> List[Dict[str, Any]]:
    """Collapse pr_watcher audit rows into ONE entry per task, with an outcome.

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

    out: List[Dict[str, Any]] = []
    for entry in by_task.values():
        if not entry["attempts"]:
            continue
        # Order matters: escalation WINS over a later merge, because that merge
        # is the human the escalation asked for.
        if entry["escalated"]:
            entry["outcome"] = NEEDED_A_HUMAN
        elif entry["merged"]:
            entry["outcome"] = RECOVERED
        else:
            entry["outcome"] = UNRESOLVED
        out.append(entry)

    out.sort(key=lambda r: (r.get("at") or ""), reverse=True)
    return out[:limit]
