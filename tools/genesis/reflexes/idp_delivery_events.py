#!/usr/bin/env python3
# CUI // SP-CTI
"""IDP Delivery Events Reflex — keep the DORA window from emptying again.

GREEN tier. Zero LLM. The only writes are appends to ``audit_trail`` and
``ci_pipeline_runs`` via ``tools.idp.delivery_events.sync_delivery_events``.

``/api/sre/dora`` reads a rolling 30-day window. A one-off backfill therefore
decays: every change it emitted ages out, and in a month the endpoint is back to
``metrics_assessed: 0`` with nobody having changed a line. This reflex is what
makes the metric live rather than a snapshot — it emits events for changes that
have landed since the last cycle.

The sync is incremental and idempotent (already-emitted task ids are read back
out of ``audit_trail``), so a cycle with no new merges writes nothing and still
succeeds. ``metric_value`` is deploy events emitted, and the success metric is
``gte 0`` for exactly that reason: zero new merges in six hours is a quiet
platform, not a failure.

The window default here is 7 days rather than the module's 90 — a steady-state
cycle only needs to reach back far enough to cover the gap since the last run,
and scanning 90 days of ledger every six hours to find nothing is waste. A cold
install still gets its full backfill from the CLI (``--days 90``).
"""
IMPLEMENTATION_STATUS = "full"

import sys
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

CADENCE_HOURS = 6

_TAG = "[idp_delivery_events]"


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Emit delivery events for changes that landed since the last cycle."""
    from tools.idp.delivery_events import dora_input_status, sync_delivery_events  # noqa: PLC0415

    config = config or {}
    # Config, not code: widen the window from an operator's console when a
    # backfill is needed, without editing this file.
    days = int(config.get("days", 7))
    dry_run = bool(config.get("dry_run", False))

    try:
        summary = sync_delivery_events(days=days, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 - reported as a failed cycle, never swallowed
        print(f"{_TAG} sync failed: {exc}")
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {"errors": [str(exc)], "deploy_events": 0},
        }

    emitted = int(summary.get("deploy_events") or 0)
    print(
        f"{_TAG} emitted {emitted} deploy event(s), "
        f"{summary.get('failure_events', 0)} failure event(s), "
        f"{summary.get('pipeline_runs', 0)} pipeline run(s) "
        f"over a {days}d window"
    )

    # Report what the endpoint can now see. A cycle that emitted nothing but
    # left DORA starved is a different situation from one that emitted nothing
    # because there was nothing new, and the difference is visible here.
    try:
        inputs = dora_input_status(days=30)
    except Exception as exc:  # noqa: BLE001 - diagnostic only; never fails the cycle
        inputs = {"error": str(exc)}

    return {
        "success": True,
        "metric_value": float(emitted),
        "details": {"sync": summary, "dora_inputs": inputs, "errors": []},
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run({}, None), indent=2, default=str))
