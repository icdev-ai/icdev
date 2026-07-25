# CUI // SP-CTI
"""Genesis Reflex — Agent Cron (sag-cron-01).

Ticks the user-facing cron job store: every cadence it asks
:func:`tools.agent_runtime.cron.run_due_jobs` to execute every ``active`` job
whose ``next_run_at`` is due, record an append-only run row, deliver the result,
and reschedule (with exponential backoff on failure).

This reflex adds NO scheduling loop of its own — it is the bridge between the
existing Genesis daemon cadence and the durable ``agent_cron_jobs`` store. The
per-job schedule (interval or 5-field cron) lives in the store; this reflex just
drains what is due each time the daemon runs it. Keeping the reflex cadence small
(default 1 minute in genesis_config.yaml) gives cron near-minute resolution.

NOTE (mem-01 auto-capture): the deferred
:func:`tools.agent_runtime.profile_memory.consolidate_session_facts` post-session
fact consolidation is intentionally NOT hard-wired here. The recommended path is
to schedule it *through* this cron surface as a ``script`` job, e.g.::

    icdev cron create "consolidate-facts" --mode script \\
        --payload "python tools/agent_runtime/consolidate_cli.py --user me" \\
        --cron "0 3 * * *"

so consolidation cadence is user-owned rather than a fixed daemon reflex. See the
sag-xcut-01 phase doc for the rationale.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

from typing import Any, Dict

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.genesis.reflexes.agent_cron")

CADENCE_MINUTES: int = 1


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Drain all due agent-cron jobs. Reflex contract: returns success/metric/details."""
    result: Dict[str, Any] = {
        "cadence_minutes": CADENCE_MINUTES,
        "status": "ok",
    }
    try:
        from tools.agent_runtime.cron import run_due_jobs

        tick = run_due_jobs(conn=conn)
        result["success"] = True
        result["metric_value"] = float(tick.get("ran", 0))
        result["details"] = {
            "due": tick.get("due", 0),
            "ran": tick.get("ran", 0),
            "succeeded": tick.get("succeeded", 0),
            "checked_at": tick.get("checked_at"),
        }
        return result
    except Exception as exc:  # noqa: BLE001 — never wedge the daemon
        logger.warning("agent_cron reflex error: %s", exc)
        result["status"] = "error"
        result["success"] = False
        result["metric_value"] = 0.0
        result["details"] = {"error": str(exc)}
        return result


if __name__ == "__main__":
    import json as _json

    print(_json.dumps(run({}), indent=2))
