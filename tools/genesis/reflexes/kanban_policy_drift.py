# CUI // SP-CTI
"""Keep embedded task policy in sync with the card — the scheduled half.

``tools/kanban/policy_drift.py`` is the mechanism; this is the thing that runs
it. A drift checker nobody invokes is how the divergence it detects gets
noticed the same way it did before: by watching the pipeline misbehave (35 hgx
rows kept telling sessions to open ``--draft`` for a day after the card was
corrected, and three separate batches of finished green work needed a human).

What it does each cycle:

* scans EVERY status and reports the count, so a done row that disagrees with
  its card is still visible;
* rewrites only rows a dispatcher could still hand to a session
  (``backlog``/``scheduled``) — that is where the harm is, and those rows have
  no live session whose description edits a rewrite could clobber;
* leaves ``in_progress`` / ``done`` / ``pr_opened`` reported-only.

``dry_run`` in ``args/genesis_config.yaml`` turns the rewrite off and leaves
the report.

Reflex contract:
  - run(ctx, conn) → dict with success / metric_value / details
  - CADENCE_HOURS = 6
  - Idempotent (a rewritten row is in sync and is not rewritten again)
  - Never raises
"""
from __future__ import annotations
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

IMPLEMENTATION_STATUS = "full"

import json  # noqa: E402
import time  # noqa: E402
from typing import Any, Dict  # noqa: E402

logger = get_logger(__name__)

CADENCE_HOURS = 6


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Scan the board for descriptions that disagree with their card.

    ``ctx`` accepts ``dry_run`` (report without rewriting) and ``project``
    (limit to one card key).

    ``metric_value`` is the number of DISPATCHABLE rows still out of sync after
    the pass, not the total drift — a done row that disagrees with its card is
    worth reporting but cannot misdirect anyone, so it must not hold the metric
    permanently red.
    """
    dry_run = bool(ctx.get("dry_run", False))
    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "errors": [],
        "status": "ok",
        "drifted_total": 0,
        "drifted_dispatchable": 0,
        "rewritten": [],
        "reported_only": [],
        "unresolved_dispatchable": 0,
        "dry_run": dry_run,
    }

    started = time.monotonic()
    own_conn = None
    try:
        from tools.kanban import policy_drift as pd

        projects = pd.load_projects()
        ruleset = pd.load_rules(projects=projects)

        if conn is None:
            from tools.db.storage import get_connection
            own_conn = get_connection()
            conn = own_conn

        findings = pd.scan(conn, projects, ruleset,
                           project_filter=ctx.get("project"))
        result["drifted_total"] = len(findings)
        fixable = [f for f in findings if f["fixable"]]
        result["drifted_dispatchable"] = len(fixable)
        result["reported_only"] = [
            {"task_id": f["task_id"], "status": f["status"], "action": f["action"]}
            for f in findings if not f["fixable"]
        ]

        if fixable and not dry_run:
            outcome = pd.apply(conn, fixable)
            result["rewritten"] = outcome["written"]
            result["unresolved_dispatchable"] = len(fixable) - len(outcome["written"])
        else:
            result["unresolved_dispatchable"] = len(fixable)

        if findings:
            logger.warning(
                "kanban_policy_drift: %d row(s) disagree with their card "
                "(%d dispatchable); rewrote %d%s",
                len(findings), len(fixable), len(result["rewritten"]),
                " (dry run)" if dry_run else "",
            )
    except Exception as exc:
        logger.error("kanban_policy_drift reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))
    finally:
        if own_conn is not None:
            try:
                own_conn.close()
            except Exception as exc:  # pragma: no cover - close is best effort
                logger.debug("kanban_policy_drift: connection close failed: %s", exc)

    return _finalise(result, started)


def _finalise(result: Dict[str, Any], started: float) -> Dict[str, Any]:
    """Attach the dispatch contract keys tools/daemon/base.py::run_reflex reads.

    Without success/metric_value/details a healthy cycle scores as a FAILURE
    and, repeated, trips the reflex circuit breaker.
    """
    result["elapsed_sec"] = round(time.monotonic() - started, 1)
    result["success"] = result["status"] != "error"
    result["metric_value"] = float(result.get("unresolved_dispatchable", 0) or 0)
    result["details"] = {
        k: result.get(k)
        for k in (
            "drifted_total", "drifted_dispatchable", "rewritten",
            "reported_only", "unresolved_dispatchable", "elapsed_sec",
            "status", "errors", "dry_run",
        )
    }
    return result


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board as the
    # GenesisDaemon. override=True: a pip-installed ICDEV in site-packages may
    # have already loaded a different checkout's .env at import.
    try:
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_REPO_ROOT / ".env", override=True)
    except ImportError:
        pass
    print(json.dumps(run({"dry_run": True}), indent=2, default=str))
