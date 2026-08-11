# CUI // SP-CTI
"""Genesis Reflex — Agent Cron + Wake tick (sag-cron-01, agov-wake-03).

Ticks the user-facing cron job store: every cadence it asks
:func:`tools.agent_runtime.cron.run_due_jobs` to execute every ``active`` job
whose ``next_run_at`` is due, record an append-only run row, deliver the result,
and reschedule (with exponential backoff on failure).

Since agov-wake-03 it drains a second store on the same cadence:
:func:`tools.agent_runtime.wake_tick.run_due_wakes` fires every ``due``
``agent_wakes`` row and resumes the session that suspended itself against it.
The two stores are siblings — ``agent_cron_jobs`` is operator-declared and
recurring, ``agent_wakes`` is agent-declared and single-shot — and they share
this tick deliberately: ICDEV already runs the Genesis daemon, the kanban
scheduler and pr_watcher, and a fourth long-lived process would be a fourth
thing that can die unnoticed. (An unnoticed dead watcher is the failure that
motivated the epic: a stalled board went four days before anyone saw it.)

The two drains are independently guarded. A cron failure must not cost the wakes
their delivery, and vice versa — one wedged store taking the other down would
re-create, inside one reflex, exactly the single-point-of-failure that not
adding a daemon was meant to avoid.

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


def _tick_cron(conn=None) -> Dict[str, Any]:
    """Drain due ``agent_cron_jobs``. Returns the tick summary, or an error dict."""
    try:
        from tools.agent_runtime.cron import run_due_jobs

        tick = run_due_jobs(conn=conn)
        return {
            "due": tick.get("due", 0),
            "ran": tick.get("ran", 0),
            "succeeded": tick.get("succeeded", 0),
            "checked_at": tick.get("checked_at"),
        }
    except Exception as exc:  # noqa: BLE001 — never wedge the daemon
        logger.warning("agent_cron reflex: cron drain error: %s", exc)
        return {"due": 0, "ran": 0, "succeeded": 0, "error": str(exc)}


def _tick_wakes(conn=None) -> Dict[str, Any]:
    """Drain due ``agent_wakes``. Returns the tick summary, or an error dict."""
    try:
        from tools.agent_runtime.wake_tick import run_due_wakes

        tick = run_due_wakes(conn=conn)
        return {
            "due": tick.get("due", 0),
            "fired": tick.get("fired", 0),
            "resumed": tick.get("resumed", 0),
            "failed": tick.get("failed", 0),
            "skipped": tick.get("skipped", 0),
            "checked_at": tick.get("checked_at"),
            **({"error": tick["error"]} if tick.get("error") else {}),
        }
    except Exception as exc:  # noqa: BLE001 — never wedge the daemon
        logger.warning("agent_cron reflex: wake drain error: %s", exc)
        return {"due": 0, "fired": 0, "resumed": 0, "failed": 0, "error": str(exc)}


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Drain all due agent-cron jobs and all due wakes.

    Reflex contract: returns success/metric/details. The metric is work actually
    done this tick — cron jobs run plus wakes fired — so a tick that finds
    nothing due reports 0 and is not a failure.
    """
    result: Dict[str, Any] = {
        "cadence_minutes": CADENCE_MINUTES,
        "status": "ok",
    }
    cron = _tick_cron(conn=conn)
    wakes = _tick_wakes(conn=conn)

    errors = [d["error"] for d in (cron, wakes) if d.get("error")]
    result["success"] = not errors
    result["status"] = "error" if errors else "ok"
    result["metric_value"] = float(cron.get("ran", 0)) + float(wakes.get("fired", 0))
    result["details"] = {
        # Flattened cron keys are kept at the top level: genesis_config.yaml reads
        # `ran` as this reflex's success metric, and reshaping the details would
        # break every dashboard and reflex-health row already reading it.
        "due": cron.get("due", 0),
        "ran": cron.get("ran", 0),
        "succeeded": cron.get("succeeded", 0),
        "checked_at": cron.get("checked_at"),
        "cron": cron,
        "wakes": wakes,
    }
    if errors:
        result["details"]["error"] = "; ".join(errors)
    return result


if __name__ == "__main__":
    import json as _json

    print(_json.dumps(run({}), indent=2))
