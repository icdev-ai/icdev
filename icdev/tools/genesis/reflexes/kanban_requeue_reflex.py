#!/usr/bin/env python3
# CUI // SP-CTI
"""The OWNER of a card parked in ``validating`` -- reachable, bounded, on the
cadence a park happens on (mfx-own-03).

THE DEFECT. kpr-stale-05/06 built the requeue act and built it well: prove ->
requeue -> confirm, candidates re-proven from primary data, a shared cap whose
remainder is named, a suggested card instead of a loop for a recurring park. It
was then wired as a downstream consumer of ``kanban_stranded_reflex``, whose
audit walks EVERY terminal task. MEASURED on the live board 2026-09-05:

  * 3,892 ``done`` rows against the 2 ``validating`` rows the act cares about.
    The audit compares each genuinely divergent branch by PATCH-ID, which is
    irreducible work -- so the act, whose whole population is one indexed query,
    was paying a whole-board bill to be allowed to run.
  * ``kanban_stranded_reflex`` recorded durations: median 300.0s, max 1200.2s
    against a 1200s watchdog. 24 ``watchdog_timeout_1200s`` failures.
  * 242 recorded runs of that reflex carry ``orphan_requeue`` ZERO times. Five
    of those runs are since the act landed (2026-09-04 00:28Z); the only one to
    reach a verdict died on the watchdog.
  * three consecutive failures opened its circuit breaker on 2026-09-04
    11:58:56Z, and ``tools/daemon/base.py`` SKIPS a reflex whose breaker is
    open -- so the act stopped being dispatched at all.
  * of 73 lifetime guard parks across 72 distinct tasks, the first exit was a
    HUMAN 63 times (``manual`` 41, ``cli`` 22), ``pr_watcher -> done`` 5 times,
    and 5 are still parked. ``kanban_stranded_reflex`` accounts for EXACTLY
    ZERO. The owner has never once moved a row.

So the repair is not a further proof and not a longer budget. It is a consumer
that a whole-board audit cannot starve. THE ACT IS SEPARATED FROM THE AUDIT --
they answer different questions, cost three orders of magnitude apart, and only
one of them is time-critical: a park blocks every card that depends on the
parked one (2026-09-05 02:06, two parks held seventeen ``backlog`` rows behind
them while the board reported capacity free).

WHAT THIS REFLEX DOES NOT DO, on purpose. It builds no proof of its own -- both
proofs and both acts stay in ``tools/kanban/orphan_requeue.py``, so there is one
statement of the rule and this module cannot drift from it. It does not raise
the guard's 30s worktree timeout and does not retry inside the guard; both are
forbidden by the guard's own comment, and a longer timeout only widens the
window. It does not touch ``kanban_stranded_reflex``, which keeps its audit, its
24h cadence, its 1200s budget and its own breaker -- an audit that legitimately
times out should trip ITS breaker, and the point of this module is that doing so
no longer takes the requeue down with it.

CADENCE. 30 minutes. The parks measured on this board arrive in bursts under
worktree contention (13 in a 35-minute window on 2026-09-03; two at 02:06 and
one more at 04:35 on 2026-09-05), and the cost of a cycle is one indexed query
plus a handful of git probes per candidate -- so the cadence is set by how fast
a park should be answered, not by what the work costs.

UNMEASURABLE, NEVER A CLEAN ZERO, AND NEVER A FAILURE. A board that cannot be
read reports ``state: unmeasurable`` with ``candidates: None`` on both acts,
while ``success`` stays True: marking an unreadable board a reflex FAILURE is
precisely what opened the breaker that made the act unreachable, and repeating
that here would rebuild the defect inside its own fix.

Headless:  python tools/genesis/daemon.py --reflex kanban_requeue_reflex --json
           python -m tools.kanban.orphan_requeue --plan      # proves, acts on nothing
"""
IMPLEMENTATION_STATUS = "full"

import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# sys.path BOOTSTRAP first, so `python tools/genesis/reflexes/kanban_requeue_reflex.py`
# reaches main() (kax-conflict-04); then the ONE root resolver.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402

BASE_DIR = repo_root(__file__)
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

#: Written as ``actor`` on the requeue transition and on the intent audit
#: row. The row must name the reflex that ACTUALLY acted: stamping every
#: requeue with ``kanban_stranded_reflex`` -- which no longer runs the act,
#: and whose breaker is open -- would point a reader at an impossible row.
ACTOR = "kanban_requeue_reflex"

logger = get_logger("kanban_requeue_reflex")


def _unmeasurable(error: str, config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    from tools.kanban import orphan_requeue as _oq

    return {
        "state": "unmeasurable",
        "candidates": None,
        "requeued": [], "carded": [], "deferred": [], "refused": [], "cards": [],
        "acts": [],
        "max_requeues_per_run": (config or {}).get(
            "max_requeues_per_run", _oq.DEFAULT_MAX_REQUEUES_PER_RUN),
        "requeue_status": _oq.REQUEUE_STATUS,
        "error": error,
    }


def run(config: Optional[Dict[str, Any]] = None, state: object = None,
        **overrides: Any) -> Dict[str, Any]:
    """Entry point called by the Genesis daemon.

    ``config`` is this reflex's block from ``args/genesis_config.yaml``
    (``max_requeues_per_run``). ``overrides`` are the injectable seams the acts
    already expose (``get_conn``, ``branch_exists``, ``worktree_exists``,
    ``lease_state``, ``requeue``, ``file_card``, ``ctx``, ``now``) -- used by
    the tests to drive a hermetic board, never by the daemon.

    ``metric_value`` is the number of rows this cycle actually returned to the
    queue, so the reflex's own liveness is measured by what it MOVED and not by
    the fact that it ran.
    """
    config = config or {}
    from tools.kanban import orphan_requeue as _oq

    details: Dict[str, Any] = {}
    result: Dict[str, Any] = {"success": True, "details": details}

    get_conn: Optional[Callable[[], Any]] = overrides.pop("get_conn", None)
    ctx = overrides.pop("ctx", None)
    default_branch = getattr(ctx, "default_branch", None) or "main"

    try:
        findings = _oq.board_findings(get_conn=get_conn, default_branch=default_branch)
    except Exception as exc:  # noqa: BLE001 -- an unreadable board is not a failure
        logger.warning("kanban_requeue_reflex: candidates unmeasurable (%s)", exc)
        details["orphan_requeue"] = _unmeasurable(str(exc), config)
        details["empty_checkout_requeue"] = _unmeasurable(str(exc), config)
        result["metric_value"] = 0.0
        return result

    orphan_seams = {k: v for k, v in overrides.items() if k in (
        "branch_exists", "worktree_exists", "lease_state", "requeue", "file_card", "now")}
    try:
        details["orphan_requeue"] = _oq.act_on_orphans(
            findings, config, get_conn=get_conn, actor=ACTOR, **orphan_seams)
    except Exception as exc:  # noqa: BLE001 -- one act must not take the other down
        logger.exception("kanban_requeue_reflex: orphan requeue failed: %s", exc)
        details["orphan_requeue"] = _unmeasurable(str(exc), config)

    # The SECOND proof shares ONE cap with the first: what the orphan act
    # already requeued this cycle counts against it.
    already = len((details.get("orphan_requeue") or {}).get("requeued") or [])
    empty_seams = {k: v for k, v in overrides.items() if k in (
        "lease_state", "requeue", "file_card", "now", "branch_state",
        "worktree_state", "dry_run")}
    try:
        details["empty_checkout_requeue"] = _oq.act_on_empty_checkouts(
            findings, config, already_requeued=already, get_conn=get_conn,
            ctx=ctx, actor=ACTOR, **empty_seams)
    except Exception as exc:  # noqa: BLE001
        logger.exception("kanban_requeue_reflex: empty-checkout requeue failed: %s", exc)
        details["empty_checkout_requeue"] = _unmeasurable(str(exc), config)

    moved = already + len(
        (details.get("empty_checkout_requeue") or {}).get("requeued") or [])
    result["metric_value"] = float(moved)
    if moved:
        logger.info("kanban_requeue_reflex: returned %d parked row(s) to the queue", moved)
    return result


if __name__ == "__main__":
    import json

    try:
        from dotenv import load_dotenv

        load_dotenv(BASE_DIR / ".env", override=True)
    except ImportError:
        pass
    print(json.dumps(run({}, None), indent=2, default=str))
