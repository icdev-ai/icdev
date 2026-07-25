#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Failure-Triage Reflex — periodic auto-review of kanban failures.

Wraps ``tools.workflow.failure_triage.triage_once`` so the Genesis daemon
runs it on a schedule. One cycle per call: query recent failed kanban
tasks, LLM-diagnose them, optionally generate + apply a patch in an
isolated worktree (gated on ``ICDEV_AUTOFIX_ENABLED`` — off-by-default).

Risk tier: YELLOW — may edit files and create commits on ``autofix/*``
branches, but never on ``main`` unless ``ICDEV_AUTOFIX_AUTOMERGE=true``
is also set. All edits are confined to a fresh ``.tmp/autofix/<task>__
<sig>/`` worktree and reverted on any verification failure.

Returns: ``{"scanned": N, "applied": N, "suggested": N, "skipped": N}``
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"
from tools.logging.icdev_logger import get_logger

import sys
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = get_logger(__name__)


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute one triage cycle.

    Reads these knobs from ``config`` (all optional, sensible defaults):
      * ``window_hours`` (int, default 1) — look-back window.
      * ``apply`` (bool, default True) — ask triage to attempt patch
        generation. Actual apply still requires ``ICDEV_AUTOFIX_ENABLED``.
      * ``max_failures_per_cycle`` (int, default 10) — hard cap on work
        per cycle so a sudden burst of failures can't starve the daemon.
    """
    try:
        from tools.workflow.failure_triage import triage_once
    except Exception as exc:  # pragma: no cover — defensive import
        logger.warning("failure_triage import failed: %s", exc)
        return {"scanned": 0, "applied": 0, "suggested": 0,
                "skipped": 0, "error": f"import_failed: {exc}"}

    window = int(config.get("window_hours", 1))
    apply_mode = bool(config.get("apply", True))

    summary = triage_once(window_hours=window, apply=apply_mode)

    applied = 0
    suggested = 0
    skipped = 0
    for r in summary.get("results", []):
        outcome = r.get("outcome", "")
        if outcome.startswith("applied_"):
            applied += 1
        elif outcome == "skipped_already_triaged":
            skipped += 1
        elif outcome in (
            "suggested_card_created",
            "patch_gen_failed_fell_through_to_card",
            "apply_failed_fell_through_to_card",
        ):
            suggested += 1

    return {
        "scanned": summary.get("failures_scanned", 0),
        "applied": applied,
        "suggested": suggested,
        "skipped": skipped,
        "autofix_enabled": summary.get("autofix_enabled"),
        "apply_mode": summary.get("apply_mode"),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run({}, None), indent=2, default=str))
