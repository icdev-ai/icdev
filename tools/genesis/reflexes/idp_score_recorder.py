#!/usr/bin/env python3
# CUI // SP-CTI
"""IDP Score Recorder Reflex — accumulate the per-component score series.

GREEN tier. Zero LLM. Every rule is an IQE query over ``idp.components``, and
the only write is an append to ``idp_scorecard_history``.

Cadence is deliberately the awareness reflex's 3h, not the scorecard's window.
Awareness is what refreshes ``awareness_component_health``, which is where the
``probes-healthy`` rule's facts come from — recording on the same beat means a
point is written against fresh probe data rather than a stale snapshot. The
scorecard's own ``evaluation.window`` then decides whether the point is
actually due: with the shipped 24h window a 3h reflex writes one row per
component per day and skips the other seven cycles. Shorten the window in
``args/scorecards/<key>.yaml`` and the granularity follows with no code change.

The failure this exists to prevent is the one the whole IDP card is about: a
scorecard nobody records is a number on a page, and 25 of the platform's 29
score tables are empty precisely because no scheduled writer ever existed.
"""
IMPLEMENTATION_STATUS = "full"

import sys
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

_TAG = "[idp_score_recorder]"


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Record a point for every scorecard whose evaluation window has rolled.

    ``metric_value`` is the number of history rows written, so a cycle that
    correctly skips an already-recorded window reports 0 and still succeeds —
    the success metric is ``gte 0``.
    """
    from tools.idp.score_history import record_all  # noqa: PLC0415

    config = config or {}
    scorecard_dir = config.get("scorecard_dir") or None
    # Config, not code: an operator can force every cycle to write a point
    # (useful while backfilling a fresh install) without editing this file.
    if_due = bool(config.get("if_due", True))

    rows = 0
    recorded, skipped, errors = [], [], []
    try:
        results = record_all(if_due=if_due, directory=scorecard_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"{_TAG} scoring failed: {exc}")
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {"errors": [str(exc)], "rows_written": 0},
        }

    for result in results:
        key = result.get("scorecard")
        if result.get("status") == "skipped":
            skipped.append(key)
            print(f"{_TAG} {key}: window {result.get('window_start')} already recorded")
            continue
        written = int(result.get("rows_written") or 0)
        rows += written
        recorded.append({"scorecard": key, "rows": written})
        print(f"{_TAG} {key}: recorded {written} component(s)")

    print(f"{_TAG} complete — {rows} row(s) across {len(recorded)} scorecard(s)")
    return {
        "success": True,
        "metric_value": float(rows),
        "details": {
            "rows_written": rows,
            "recorded": recorded,
            "skipped": skipped,
            "errors": errors,
        },
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run({}, None), indent=2, default=str))
