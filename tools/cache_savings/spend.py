# CUI // SP-CTI
"""Spend by kanban task and by outcome, for the /cache-savings page (xrv-cost-04).

WHY A PANEL AND NOT A PAGE. ``tools/llm/cost_intelligence.py`` and
``tools/cost/session_cost.py`` answer "what did this cost" on a CLI and through
MCP, and nothing on any dashboard rendered a dollar attributed to a card.
``/cache-savings`` is the one live cost surface, so this is a SECTION on it --
not a new page, so the 8-point page gate does not apply.

IT COMPUTES NO COST OF ITS OWN. Every figure comes from ONE call to
``task_attribution.survey_by_verdict`` (xrv-cost-02), the seam that already
joins ``agent_token_usage`` rows carrying a ``task_id`` to the outcome through
``landed_check`` and ``pr_linker``. A second reader of that ledger is how a
panel comes to disagree with the CLI about a number neither of them changed, so
this module holds the PRESENTATION vocabulary and the cache and nothing else --
pinned by an AST test that refuses a database cursor here.

FOUR EMPTY STATES, AND ONLY ONE OF THEM IS ABOUT SPENDING NOTHING. Each sends a
reader somewhere different, so none of them is rendered as ``$0.00``:

    unavailable        the survey could not be produced -- git, the board or
                       the ledger raised. NOT a clean bill of health.
    ledger_unreadable  ``agent_token_usage`` could not be read.
    no_attributed_rows nothing was dispatched in the window, or the reap never
                       recorded an envelope. The cards on the board may well
                       have cost money; nothing ATTRIBUTED it.
    measured           rows exist. The only state that carries dollars.

``unpriced`` IS COUNTED APART FROM EVERY VERDICT. A dispatch whose envelope
reported no ``total_cost_usd`` still SHIPPED or was still ABANDONED, so folding
it into ``unmeasurable`` would make the verdict answer a question about the
PRICE instead of about the OUTCOME (``task_attribution.row_is_unpriced``).

Usage::

    python -m tools.cache_savings.spend --json
    python -m tools.cache_savings.spend --window-days 30

API: ``GET /api/cache-savings/spend`` -- GET only, no POST sibling.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from icdev.tools.cost.task_attribution import (
    VERDICT_ABANDONED,
    VERDICT_IN_FLIGHT,
    VERDICT_REVERTED,
    VERDICT_SHIPPED,
    VERDICT_UNMEASURABLE,
)
from icdev.tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: The panel's default window. ``?window_days=N`` overrides it per request.
DEFAULT_WINDOW_DAYS = 7.0
#: One git sweep per TTL, shared by the page render and the API poll.
CACHE_TTL_SECONDS = 120.0
#: The task table is truncated; the count of what was dropped is ALWAYS shown,
#: because a list of twelve over a board of forty reads as the whole board.
MAX_TASK_ROWS = 12

STATE_MEASURED = "measured"
STATE_UNMEASURABLE = "unmeasurable"

REASON_UNAVAILABLE = "unavailable"
REASON_NO_ROWS = "no_attributed_rows"
REASON_LEDGER_UNREADABLE = "ledger_unreadable"

#: Verdicts in ATTENTION order -- spend that did not ship reads first. This is
#: presentation only; it never reorders ``VERDICTS``, which is the closed set.
VERDICT_ORDER = (
    VERDICT_SHIPPED,
    VERDICT_REVERTED,
    VERDICT_ABANDONED,
    VERDICT_IN_FLIGHT,
    VERDICT_UNMEASURABLE,
)

VERDICT_LABELS: Dict[str, str] = {
    VERDICT_SHIPPED: "Shipped",
    VERDICT_REVERTED: "Reverted",
    VERDICT_ABANDONED: "Abandoned",
    VERDICT_IN_FLIGHT: "In flight",
    VERDICT_UNMEASURABLE: "Unmeasurable",
}

#: What each verdict MEANS, in words, on the panel -- a bare label beside a
#: dollar figure invites the reader to invent the definition.
VERDICT_NOTES: Dict[str, str] = {
    VERDICT_SHIPPED: "landed on the default branch and nothing reverts it",
    VERDICT_REVERTED: "landed, then a revert of that commit landed too",
    VERDICT_ABANDONED: "the card is terminal on the board and nothing landed",
    VERDICT_IN_FLIGHT: "still open - this spend has not been judged yet",
    VERDICT_UNMEASURABLE: "git, the board or the forge could not answer - "
                          "not a verdict of no value",
}

#: The one sentence the panel prints when nothing was attributed. Named so the
#: template, the API and the test all quote the same words.
EMPTY_HEADLINE = "no attributed dispatches in the window"


def _empty_verdicts() -> List[Dict[str, Any]]:
    """All five rows, every figure ``None``. A verdict missing from the table
    is indistinguishable from one that measured zero."""
    return [
        {
            "verdict": v,
            "label": VERDICT_LABELS[v],
            "note": VERDICT_NOTES[v],
            "tasks": None,
            "dispatches": None,
            "cost_usd": None,
            "unpriced_dispatches": None,
            "cost_share_pct": None,
        }
        for v in VERDICT_ORDER
    ]


def _share(cost: Optional[float], denominator: float) -> Optional[float]:
    """A verdict's share of the measured spend; ``None``, never 0.0 or 100.0,
    when there is nothing to take a share OF."""
    if cost is None or not denominator:
        return None
    return round(100.0 * cost / denominator, 1)


def shape(survey: Dict[str, Any], *, window_days: float) -> Dict[str, Any]:
    """The panel's view of ONE ``survey_by_verdict`` report. Pure.

    Separated from the fetch so the shaping is testable against a fixture
    survey with no board, no git tree and no clock.
    """
    generated_at = survey.get("generated_at") or datetime.now(timezone.utc).isoformat()
    if survey.get("state") != STATE_MEASURED:
        reason = str(survey.get("reason") or REASON_UNAVAILABLE)
        return {
            "state": STATE_UNMEASURABLE,
            "reason": reason,
            "headline": (EMPTY_HEADLINE if reason == REASON_NO_ROWS
                         else "spend could not be measured (%s)" % reason),
            "window_days": window_days,
            "tasks": survey.get("tasks"),
            "total_cost_usd": None,
            "measured_cost_usd": None,
            "shipped_cost_share_pct": None,
            "unpriced_dispatches": None,
            "unpriced_tasks": None,
            "unmeasurable_tasks": None,
            "by_verdict": _empty_verdicts(),
            "by_task": [],
            "tasks_shown": 0,
            "tasks_total": survey.get("tasks") or 0,
            "generated_at": generated_at,
        }

    measured = float(survey.get("measured_cost_usd") or 0.0)
    by_verdict: List[Dict[str, Any]] = []
    for v in VERDICT_ORDER:
        bucket = (survey.get("by_verdict") or {}).get(v) or {}
        by_verdict.append({
            "verdict": v,
            "label": VERDICT_LABELS[v],
            "note": VERDICT_NOTES[v],
            "tasks": bucket.get("tasks", 0),
            "dispatches": bucket.get("dispatches", 0),
            "cost_usd": bucket.get("cost_usd"),
            "unpriced_dispatches": bucket.get("unpriced_dispatches", 0),
            # The unmeasurable bucket is deliberately OUTSIDE the denominator,
            # exactly as ``shipped_cost_share_pct`` is: a share of a total that
            # includes spend nobody could judge is a share of nothing knowable.
            "cost_share_pct": (None if v == VERDICT_UNMEASURABLE
                               else _share(bucket.get("cost_usd"), measured)),
        })

    rows = list(survey.get("tasks_detail") or [])
    # Costliest first, and an unpriced task (cost None) sorts LAST rather than
    # as $0 -- it is not the cheapest card, it is the one with no price.
    rows.sort(key=lambda r: (r.get("cost_usd") is None, -(r.get("cost_usd") or 0.0)))
    by_task = [
        {
            "task_id": r.get("task_id"),
            "verdict": r.get("verdict"),
            "label": VERDICT_LABELS.get(r.get("verdict"), r.get("verdict")),
            "status": r.get("status"),
            "cost_usd": r.get("cost_usd"),
            "cost_basis": r.get("cost_basis"),
            "dispatches": r.get("dispatches"),
            "unpriced_dispatches": r.get("unpriced_dispatches", 0),
            "models": r.get("models") or [],
            "last_at": r.get("last_at") or "",
        }
        for r in rows[:MAX_TASK_ROWS]
    ]
    total = survey.get("total_cost_usd")
    headline = "%s attributed across %s card(s) in %sd" % (
        "no priced spend" if total is None else "$%.2f" % total,
        survey.get("tasks"), window_days)
    return {
        "state": STATE_MEASURED,
        "reason": "",
        "headline": headline,
        "window_days": window_days,
        "tasks": survey.get("tasks"),
        "total_cost_usd": total,
        "measured_cost_usd": survey.get("measured_cost_usd"),
        "shipped_cost_share_pct": survey.get("shipped_cost_share_pct"),
        "unpriced_dispatches": survey.get("unpriced_dispatches"),
        "unpriced_tasks": survey.get("unpriced_tasks"),
        "unmeasurable_tasks": ((survey.get("by_verdict") or {})
                               .get(VERDICT_UNMEASURABLE, {}).get("tasks", 0)),
        "by_verdict": by_verdict,
        "by_task": by_task,
        "tasks_shown": len(by_task),
        "tasks_total": len(rows),
        "generated_at": generated_at,
    }


def collect(window_days: Optional[float] = None, *, db_path=None,
            survey_fn: Optional[Callable[..., Dict[str, Any]]] = None
            ) -> Dict[str, Any]:
    """Fetch and shape, UNCACHED. Raises whatever the survey raises."""
    window = DEFAULT_WINDOW_DAYS if window_days is None else float(window_days)
    fetch = survey_fn
    if fetch is None:
        from icdev.tools.cost.task_attribution import survey_by_verdict
        fetch = survey_by_verdict
    # The forge is NOT consulted: a PR lookup per task would put a network
    # round-trip on a page render, and the verdict set degrades gracefully
    # without it (``pr_state`` reads ``not_consulted``).
    survey = fetch(db_path=db_path, window_days=window, consult_forge=False)
    return shape(survey, window_days=window)


# -- The 120s cache, shared by the page and the API -------------------------

_CACHE: Dict[float, Dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()


def reset_cache() -> None:
    """Drop every cached window. For tests and for a deliberate refresh."""
    with _CACHE_LOCK:
        _CACHE.clear()


def spend_panel(window_days: Optional[float] = None, *, db_path=None,
                survey_fn: Optional[Callable[..., Dict[str, Any]]] = None,
                ttl: float = CACHE_TTL_SECONDS, now: Optional[float] = None
                ) -> Dict[str, Any]:
    """The panel payload, cached per window for *ttl* seconds.

    A FAILED fetch also stamps the clock, so a broken board is retried once per
    TTL rather than once per tab -- and the LAST GOOD report is then served
    with its age AND the error beside it, because a stale answer presented as
    current is the defect this surface exists to refuse (the ``merge_readiness``
    panel idiom, kpr-watch-03).
    """
    window = DEFAULT_WINDOW_DAYS if window_days is None else float(window_days)
    clock = time.time() if now is None else float(now)
    with _CACHE_LOCK:
        entry = _CACHE.get(window) or {"payload": None, "at": 0.0, "error": None}
        if entry["payload"] is None or (clock - entry["at"]) >= ttl:
            try:
                entry["payload"] = collect(window, db_path=db_path, survey_fn=survey_fn)
                entry["error"] = None
            except Exception as exc:  # noqa: BLE001 -- a panel must not 500
                entry["error"] = str(exc)[:300]
                logger.warning("cache-savings spend panel unavailable: %s", exc)
            entry["at"] = clock
            _CACHE[window] = entry
        payload, error, cached_at = entry["payload"], entry["error"], entry["at"]

    if payload is None:
        payload = shape({"state": STATE_UNMEASURABLE, "reason": REASON_UNAVAILABLE},
                        window_days=window)
    else:
        payload = dict(payload)
    payload["ok"] = error is None
    payload["error"] = error
    payload["cache_age_seconds"] = int(max(0, clock - cached_at))
    payload["cache_ttl_seconds"] = int(ttl)
    return payload


# -- CLI --------------------------------------------------------------------


def human(payload: Dict[str, Any]) -> str:
    lines = [
        "Spend panel (/cache-savings) -- window %sd, %s" % (
            payload["window_days"], payload["headline"]),
        "  state %s%s" % (payload["state"],
                          "" if not payload.get("reason")
                          else " (%s)" % payload["reason"]),
    ]
    if payload.get("error"):
        lines.append("  WARNING: served from cache, last fetch failed: %s"
                     % payload["error"])
    lines.append("  by verdict:")
    for row in payload["by_verdict"]:
        cost = "-" if row["cost_usd"] is None else "$%.4f" % row["cost_usd"]
        lines.append("    %-13s tasks %-5s dispatches %-5s %-12s %s" % (
            row["verdict"], row["tasks"], row["dispatches"], cost, row["note"]))
    lines.append("  unpriced dispatches %s; unmeasurable tasks %s" % (
        payload.get("unpriced_dispatches"), payload.get("unmeasurable_tasks")))
    if payload["by_task"]:
        lines.append("  by task (%s of %s shown):"
                     % (payload["tasks_shown"], payload["tasks_total"]))
        for row in payload["by_task"]:
            cost = "unpriced" if row["cost_usd"] is None else "$%.4f" % row["cost_usd"]
            lines.append("    %-22s %-13s %10s  %s dispatch(es)" % (
                row["task_id"], row["verdict"], cost, row["dispatches"]))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Spend by kanban task and outcome, as the /cache-savings "
                    "panel renders it. Report only.")
    ap.add_argument("--window-days", type=float, default=None,
                    help="attribution window (default %s)" % DEFAULT_WINDOW_DAYS)
    ap.add_argument("--no-cache", action="store_true",
                    help="bypass the 120s panel cache")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.no_cache:
        reset_cache()
    payload = spend_panel(args.window_days)
    print(json.dumps(payload, indent=2, default=str) if args.json else human(payload))
    # Exit 2 when the panel could not be produced -- never the same as a panel
    # that measured nothing.
    return 2 if payload.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
