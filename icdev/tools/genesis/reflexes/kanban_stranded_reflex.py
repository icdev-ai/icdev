#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Stranded-Branch Reflex (kph-A) — reconciles terminal kanban tasks
(done/validating) against origin/main and files HITL [STRANDED] suggested cards
for any whose branch has unmerged commits (built-but-never-merged).

Delegates the AUDIT to tools/kanban/stranded_audit.py, then ACTS on its
``orphan_validating`` findings through tools/kanban/orphan_requeue.py
(kpr-stale-05): a ``validating`` row with no branch, no worktree, no live lease
and a dispatch-guard park is requeued through ``requeue_task`` — bounded by
``max_requeues_per_run`` in this reflex's config block, the remainder named as
``deferred``; a row parked twice by the same guard within 24h gets a suggested
card instead. The act's outcome rides on ``details.orphan_requeue``; the audit's
own findings are untouched beside it.

GREEN tier — reads git + kanban_tasks, files suggested (quarantined) cards,
requeues proven orphans through the field-set owner.
"""
IMPLEMENTATION_STATUS = "full"

import sys
from pathlib import Path

# sys.path BOOTSTRAP first, so `python tools/genesis/reflexes/kanban_stranded_reflex.py`
# reaches main() (kax-conflict-04); then the ONE root resolver.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402

BASE_DIR = repo_root(__file__)
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("kanban_stranded_reflex")


def _unmeasurable(error: str, config: dict) -> dict:
    from tools.kanban import orphan_requeue as _oq

    return {
        "state": "unmeasurable",
        "candidates": None,
        "requeued": [], "carded": [], "deferred": [], "refused": [], "cards": [],
        "max_requeues_per_run": (config or {}).get(
            "max_requeues_per_run", _oq.DEFAULT_MAX_REQUEUES_PER_RUN),
        "requeue_status": _oq.REQUEUE_STATUS,
        "error": error,
    }


def run(config: dict, state: object) -> dict:
    """Entry point called by the Genesis daemon. ``config`` is this reflex's
    block from args/genesis_config.yaml (``max_requeues_per_run``)."""
    try:
        from tools.kanban import stranded_audit as _sa

        result = _sa.run(config, state)
    except Exception as exc:  # noqa: BLE001
        logger.exception("kanban_stranded_reflex failed: %s", exc)
        return {"success": False, "error": str(exc), "metric_value": 0.0}

    details = result.get("details")
    if not isinstance(details, dict):
        details = {}
        result["details"] = details

    # The audit could not read the board: its findings are an empty list that
    # means "unknown", not "no orphans". Say so rather than act on nothing.
    if not result.get("success", False) or details.get("error"):
        details["orphan_requeue"] = _unmeasurable(
            str(details.get("error") or result.get("error") or "audit failed"), config)
        return result

    try:
        from tools.kanban import orphan_requeue as _oq

        details["orphan_requeue"] = _oq.act_on_orphans(details, config or {})
    except Exception as exc:  # noqa: BLE001
        # The audit's report must not be lost because the act blew up.
        logger.exception("kanban_stranded_reflex: orphan requeue failed: %s", exc)
        details["orphan_requeue"] = _unmeasurable(str(exc), config)
    return result


if __name__ == "__main__":
    import json
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env", override=True)
    except ImportError:
        pass
    print(json.dumps(run({}, None), indent=2, default=str))
