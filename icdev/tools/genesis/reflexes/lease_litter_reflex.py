#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Reflex — Lease Litter Sweep (lease_litter_reflex, kpr-stale-04).

WHAT IT AUTOMATES. On 2026-09-02 an operator cleared 20 leaked
``kanban:task:<id>`` leases BY HAND to unstarve the board, and 92 more sat on
``done`` tasks afterwards. The actuator for exactly that already existed --
``restore_acts.perform("reap_dead_lease", <id>)`` (autonomy-act-03): prove the
holder pid is dead AND the task is not heartbeating, write the intent row,
release, confirm -- and NOTHING RAN IT ON A CADENCE. claim_verifier_reflex
defers the restore tier by design; the dispatch window reaps only the leases
in front of it; ``--set-status done`` reaps only the lease of the task being
closed. A lease on a task nobody was about to dispatch or close was reaped by
nobody, and its TTL was the only exit.

WHAT IT DOES NOT DO. It carries NO opinion of its own about what is litter.
Every candidate goes through the frozen act, which re-proves both signals and
REFUSES a live pid, a heartbeating task, and a holder it cannot assess;
``release_stale`` refuses again inside the apply, under the file lock. A reflex
with its own reaping rule is the pid-only reader rem-hyg-15 / autonomy-adm-03
removed, and a structural test asserts this module never calls
``leases.release`` or ``release_stale`` itself.

BOUNDED AND REPORTED. ``max_reaps_per_run`` (default 25) caps the acts one
cycle performs; every further candidate is still PROVEN (dry run) and reported
as ``deferred``, never silently dropped. Refusals are reported by reason,
because a sweep that lists only its successes reads as full coverage.

UNMEASURABLE IS NEVER SUCCESS-SHAPED. An unreadable lease store reports
``status: unmeasurable`` with ``metric_value 0`` -- not ``ok``. ``success``
stays True for the daemon's circuit breaker, as claim_verifier_reflex does and
for the same reason: a fresh install must not trip the breaker on an empty
directory and make the sweep permanently inert.

COOLDOWN: 1h (args/genesis_config.yaml) -- the lease TTL is 1h, so this halves
the worst-case starvation of a task stuck behind litter. GREEN tier: reads the
lease store and the board's heartbeat column, writes audit_trail intent and
outcome rows THROUGH the act, releases only what the act has proven dead.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

# sys.path BOOTSTRAP ONLY (kax-conflict-04): resolves the IMPORT root so that
# `python <this file>` can find first-party code at all. It is never used as a
# fact about where the repo is -- that is repo_root()'s job below (xit-decl-03),
# and the two must be in this order or the import of repo_root itself fails.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

#: The ONE root resolver, for the .env lookup in __main__.
BASE_DIR = repo_root(__file__)

logger = get_logger("icdev.genesis.lease_litter_reflex")

REFLEX_NAME = "lease_litter_reflex"
ACT = "reap_dead_lease"

STATUS_OK = "ok"                     # measured; nothing was litter
STATUS_FINDINGS = "findings"         # the act accepted at least one lease
STATUS_UNMEASURABLE = "unmeasurable"  # the lease store could not be read
STATUS_ERROR = "error"

DEFAULT_MAX_REAPS = 25
METRIC_NAME = "leases_reaped"


def _leases():
    """Resolved at call time so a test can substitute the store."""
    from tools.coordination import leases

    return leases


def _restore_acts():
    """The frozen actuator (autonomy-act-03). Resolved at call time."""
    from tools.awareness import restore_acts

    return restore_acts


def _task_leases() -> Optional[List[str]]:
    """Task ids with a HELD lease, or None when the store is unreadable.

    None is the honest answer for an unreadable store; an empty list would be
    indistinguishable from a clean board.
    """
    try:
        leases = _leases()
        prefix = _restore_acts().LEASE_PREFIX
        return sorted(
            str(m["resource"])[len(prefix):]
            for m in leases.list_leases()
            if str(m.get("resource", "")).startswith(prefix)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("lease_litter_reflex: lease store unreadable: %s", exc)
        return None


def _reason_bucket(reason: Any) -> str:
    text = str(reason or "").strip()
    return (text[:60] + "...") if len(text) > 60 else (text or "(no reason)")


def sweep(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One pass. Never raises. Returns the report the daemon persists."""
    cfg = config or {}
    dry_run = bool(cfg.get("dry_run", False))
    try:
        bound = int(cfg.get("max_reaps_per_run") or DEFAULT_MAX_REAPS)
    except (TypeError, ValueError):
        bound = DEFAULT_MAX_REAPS

    report: Dict[str, Any] = {
        "reflex": REFLEX_NAME, "act": ACT, "dry_run": dry_run,
        "max_reaps_per_run": bound, "leases_held": None, "reaped": 0,
        "would_reap": 0, "deferred": 0, "outcomes": {}, "refused_by_reason": {},
        "acted": [], "errors": [],
    }

    targets = _task_leases()
    if targets is None:
        report["status"] = STATUS_UNMEASURABLE
        report["errors"].append("lease store unreadable -- nothing was measured")
        return report
    report["leases_held"] = len(targets)

    acts = _restore_acts()
    outcomes: Counter = Counter()
    refused: Counter = Counter()
    acted: List[Dict[str, Any]] = []
    performed = 0
    for tid in targets:
        over_bound = performed >= bound
        result = acts.perform(ACT, tid, dry_run=(dry_run or over_bound))
        outcome = result.get("outcome")
        if over_bound and outcome == acts.WOULD_APPLY:
            # Proven litter that this cycle will not touch: named, not dropped.
            report["deferred"] += 1
            continue
        outcomes[str(outcome)] += 1
        if outcome == acts.REFUSED:
            refused[_reason_bucket(result.get("reason"))] += 1
            continue
        if outcome == acts.WOULD_APPLY:
            report["would_reap"] += 1
        elif outcome in (acts.APPLIED, acts.APPLIED_UNCONFIRMED):
            report["reaped"] += 1
            performed += 1
        acted.append({
            "target": tid, "outcome": outcome, "reason": result.get("reason"),
            "confirmed": result.get("confirmed"), "audit_id": result.get("audit_id"),
        })

    report["outcomes"] = dict(outcomes)
    report["refused_by_reason"] = dict(refused)
    report["acted"] = acted
    accepted = report["reaped"] + report["would_reap"] + report["deferred"]
    report["status"] = STATUS_FINDINGS if accepted else STATUS_OK
    return report


def run(config: dict, state: object) -> dict:
    """Entry point called by the Genesis daemon (``config`` is this reflex's
    block from args/genesis_config.yaml)."""
    try:
        report = sweep(config or {})
        if report["status"] == STATUS_UNMEASURABLE:
            logger.warning("lease_litter_reflex: %s", "; ".join(report["errors"]))
        elif report["reaped"] or report["deferred"]:
            logger.info(
                "lease_litter_reflex: %d held, %d reaped, %d deferred, refused by reason %s",
                report["leases_held"], report["reaped"], report["deferred"],
                report["refused_by_reason"],
            )
        # `success` drives the daemon's circuit breaker: an unmeasurable cycle
        # must not trip it (see module docstring). The status carries the truth.
        return {
            "success": True,
            "metric_value": float(report["reaped"]),
            "status": report["status"],
            "details": report,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("lease_litter_reflex failed: %s", exc)
        return {"success": False, "error": str(exc), "metric_value": 0.0,
                "status": STATUS_ERROR}


if __name__ == "__main__":
    import json

    try:
        from dotenv import load_dotenv

        load_dotenv(BASE_DIR / ".env", override=True)
    except ImportError:
        pass
    print(json.dumps(run({"dry_run": "--apply" not in sys.argv}, None), indent=2, default=str))
