#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Reflex — Claim Verifier (claim_verifier_reflex, autonomy-act-01).

``tools/awareness/claim_verifier.py`` (rem-hyg-17) verifies what a surface
CLAIMS against an INDEPENDENTLY derived fact. Until this reflex existed it was
consumed by NOBODY: no reflex, no scheduler, no daemon imported it (measured
across tools/genesis/, tools/ci/, tools/kanban/ on 2026-08-20). It ran only
when a human typed the command. The immune system was not covered by the
immune system.

WHAT IT DOES
------------
Runs every registered claim through ``verify_all`` on the daemon's cadence and
acts on the verdicts by the claim's OWN action tier:

    report    state the disagreement in the run result. Nothing else.
    propose   seed ONE kanban card carrying BOTH derivations. A human decides.
    restore   NOT ACTED ON HERE. Reported under ``deferred_restore`` — the
              restore tier (a mechanical, individually verifiable act such as
              reaping a provably dead lease) is autonomy-act-03.

No claim is armed to refuse anything. kpr-fix-03 measured that a survey shipped
with a ``--gate`` earns itself a ``|| true``; this reflex reports and proposes.

UNMEASURABLE IS NEVER SUCCESS-SHAPED
------------------------------------
A cycle that verifies ZERO claims — an empty registry, or every claim coming
back ``unmeasurable`` on a database with no operating history — reports
``status: unmeasurable`` and ``metric_value 0``, never ``ok``. A reflex whose
clean run and whose no-op look identical is the defect this card exists to
remove. The daemon-level ``success`` key stays True in that case on purpose:
that key drives the circuit breaker, and a fresh install tripping its breaker
in three cycles would make the verifier *permanently* inert on exactly the
deployments that most need it to start working the moment data arrives. The
distinction is carried on ``status`` and on the per-claim ``verdicts`` the
daemon persists in ``genesis_audit.details`` — which is what the
``verified_claim`` capability class (tools/awareness/capability_consumption.py)
reads, so a verifier that runs and measures nothing reads as INERT there rather
than as clean.

A PARTIAL cycle (some claims measured, some unmeasurable) reports ``ok`` or
``findings`` for the measured ones and NAMES the unmeasurable ones under
``unmeasurable_claims``. A truncated sweep reporting only its successes reads as
full coverage; this one does not.

DEDUPE
------
Card ids are deterministic in the claim id, the same trade
``cache_regression_reflex`` makes: a uuid would refile the same disagreement
every cycle and earn the reflex its own suppression. Once a card for a claim has
been filed, a later recurrence updates nothing and files nothing; the run
result still reports every finding, filed or not.

COOLDOWN: 6h (args/genesis_config.yaml). GREEN tier — reads surfaces and
primary tables, writes kanban rows, edits no claim, threshold or assertion.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import hashlib
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.genesis.claim_verifier_reflex")

REFLEX_NAME = "claim_verifier_reflex"

#: Result statuses. ``unmeasurable`` is a real status, not a failure and not ok.
STATUS_OK = "ok"
STATUS_FINDINGS = "findings"
STATUS_UNMEASURABLE = "unmeasurable"
STATUS_ERROR = "error"

#: Tiers this reflex acts on. ``restore`` is deliberately absent (autonomy-act-03).
ACTING_TIERS = ("report", "propose")

#: The ``success_metric`` name declared in args/genesis_config.yaml. Claims
#: that produced a verdict other than ``unmeasurable``.
METRIC_NAME = "claims_measured"

DEFAULT_CARD: Dict[str, str] = {
    "id_prefix": "claim-verif-",
    "task_type": "fix",
    "priority": "high",
    "status": "backlog",
}


def _card_id(prefix: str, claim_id: str) -> str:
    """Deterministic id so re-detecting the same disagreement never duplicates."""
    digest = hashlib.sha256(claim_id.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}{digest}"


def _short(value: Any, limit: int = 400) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _describe(finding: Dict[str, Any]) -> str:
    claim_id = finding["claim_id"]
    lines = [
        f"The surface claim `{claim_id}` DISAGREES with an independent re-derivation "
        "of the same fact (claim_verifier_reflex, autonomy-act-01).",
        "",
        f"**What the surface reports:** `{_short(finding.get('reported'))}`",
        "",
        f"**What the primary data says:** `{_short(finding.get('derived'))}`",
        "",
    ]
    if finding.get("detail"):
        lines += [f"**The claim:** {finding['detail']}", ""]
    if finding.get("independence") is not None:
        lines += [
            f"Independent observations backing the derivation: "
            f"{finding['independence']} (distinct subject/value pairs — never a "
            "row count; repetition is not corroboration).",
            "",
        ]
    lines += [
        "**Action tier: propose** — seed a card carrying the evidence. A human decides.",
        "",
        "**Re-derive it yourself**",
        "",
        "```",
        f"python tools/awareness/claim_verifier.py --claim {claim_id}",
        "```",
        "",
        "**Do not close this by editing the claim, the threshold or the assertion "
        "so that the surface agrees.** The data is almost never wrong — the "
        "REDUCTION is. A surface talked into agreeing with a broken reduction is "
        "how `0.0%` becomes documented behaviour. Fix the reduction, then watch "
        "the verifier agree on its own.",
        "",
        "Filed automatically by the `claim_verifier_reflex` genesis reflex. Its id "
        "is deterministic in the claim id, so this card is not refiled while it is "
        "open.",
    ]
    return "\n".join(lines)


def run(config: Dict[str, Any], ctx: Any = None) -> Dict[str, Any]:
    """Verify every registered claim; propose on a disagreement; never refuse.

    ``config`` is this reflex's block from args/genesis_config.yaml. Keys read:
        dry_run (bool): verify and report, but file no cards
        card (dict): id_prefix / task_type / priority / status overrides
    """
    started = time.time()
    config = config or {}
    dry_run = bool(config.get("dry_run", False))

    result: Dict[str, Any] = {
        "success": True,   # a missing 'success' key is scored a failure forever
        "status": STATUS_OK,
        "claims": 0,
        "claims_measured": 0,
        "counts": {},
        "verdicts": {},
        "findings": 0,
        "finding_detail": [],
        "cards_filed": 0,
        "card_ids": [],
        "deferred_restore": [],
        "unmeasurable_claims": [],
        "dry_run": dry_run,
        "errors": [],
    }

    try:
        from tools.awareness import claim_verifier as cv
        from tools.awareness.claims import REGISTRY
    except Exception as exc:  # noqa: BLE001
        result.update(success=False, status=STATUS_ERROR,
                      errors=[f"verifier unavailable: {exc}"])
        return _stamp_metric(result, started)

    try:
        report = cv.verify_all(list(REGISTRY))
    except Exception as exc:  # noqa: BLE001
        result.update(success=False, status=STATUS_ERROR, errors=[str(exc)[:300]])
        logger.exception("claim_verifier_reflex: verification failed: %s", exc)
        return _stamp_metric(result, started)

    results: List[Dict[str, Any]] = list(report.get("results") or [])
    counts = dict(report.get("counts") or {})
    result["claims"] = int(report.get("claims") or 0)
    result["counts"] = counts
    result["verdicts"] = {r["claim_id"]: r["verdict"] for r in results}
    result["unmeasurable_claims"] = sorted(
        r["claim_id"] for r in results if r["verdict"] == cv.UNMEASURABLE
    )
    measured = int(counts.get(cv.AGREES, 0)) + int(counts.get(cv.DISAGREES, 0))
    result["claims_measured"] = measured

    if measured == 0:
        # NOT ok, and NOT an error. Nothing was verified: either the registry
        # is empty or every claim was unmeasurable — a fresh worktree, an
        # ephemeral CI database, or every surface unreadable. Reporting `ok`
        # here would be a verifier certifying a board it never looked at.
        result["status"] = STATUS_UNMEASURABLE
        result["reason"] = (
            "no claims are registered" if result["claims"] == 0
            else "every registered claim was unmeasurable on this database"
        )
        result["unmeasurable_detail"] = {
            r["claim_id"]: (r.get("detail") or "")[:200]
            for r in results if r["verdict"] == cv.UNMEASURABLE
        }
        logger.info("claim_verifier_reflex: unmeasurable (%s)", result["reason"])
        return _stamp_metric(result, started)

    disagreements = [r for r in results if r["verdict"] == cv.DISAGREES]
    result["findings"] = len(disagreements)
    result["finding_detail"] = [
        {
            "claim_id": r["claim_id"],
            "tier": r.get("tier", "report"),
            "reported": _short(r.get("reported")),
            "derived": _short(r.get("derived")),
        }
        for r in disagreements
    ]
    # The restore tier is a mechanical act with its own card (autonomy-act-03).
    # Named here so a deferred action is visible, never silently dropped.
    result["deferred_restore"] = sorted(
        r["claim_id"] for r in disagreements if r.get("tier") == "restore"
    )
    if disagreements:
        result["status"] = STATUS_FINDINGS

    proposals = [r for r in disagreements if r.get("tier") == "propose"]
    if proposals and not dry_run:
        filed, ids = _file_cards(proposals, config)
        result["cards_filed"] = filed
        result["card_ids"] = ids

    _stamp_metric(result, started)
    logger.info(
        "claim_verifier_reflex: %d claim(s), %d measured, %d disagreement(s), "
        "%d card(s) — %s",
        result["claims"], measured, result["findings"], result["cards_filed"], counts,
    )
    return result


def _stamp_metric(result: Dict[str, Any], started: float) -> Dict[str, Any]:
    """Populate the keys the daemon actually reads off a reflex result.

    ``daemon._run_reflex_impl_inner`` records ``result["metric_value"]`` and
    ``result["details"]`` and defaults both, so a reflex that never sets them
    records 0.0 forever while looking like it reported something. The declared
    metric is ``claims_measured``. ``details.verdicts`` is the per-claim record
    the ``verified_claim`` consumption probe reads back out of
    ``genesis_audit`` — keep it a flat ``{claim_id: verdict}`` map.
    """
    result["elapsed_seconds"] = round(time.time() - started, 2)
    result["metric_value"] = float(result.get("claims_measured", 0) or 0)
    result["details"] = {
        "status": result.get("status"),
        "claims": result.get("claims", 0),
        "claims_measured": result.get("claims_measured", 0),
        "counts": result.get("counts", {}),
        "verdicts": result.get("verdicts", {}),
        "findings": result.get("findings", 0),
        "finding_detail": result.get("finding_detail", []),
        "cards_filed": result.get("cards_filed", 0),
        "card_ids": result.get("card_ids", []),
        "deferred_restore": result.get("deferred_restore", []),
        "unmeasurable_claims": result.get("unmeasurable_claims", []),
        "reason": result.get("reason"),
        "dry_run": result.get("dry_run", False),
        "errors": result.get("errors", []),
    }
    return result


def _file_cards(findings: List[Dict[str, Any]], config: Dict[str, Any]) -> Tuple[int, List[str]]:
    """One card per propose-tier disagreement, deterministic id. ``(count, ids)``."""
    try:
        from tools.kanban.task_factory import create_tasks
    except Exception as exc:  # noqa: BLE001
        logger.warning("claim_verifier_reflex: task_factory unavailable: %s", exc)
        return 0, []

    card = dict(DEFAULT_CARD)
    card.update({k: str(v) for k, v in (config.get("card") or {}).items()})
    specs = []
    for f in findings:
        specs.append({
            "id": _card_id(card["id_prefix"], f["claim_id"]),
            "title": f"Surface claim disagrees with its evidence: {f['claim_id']}",
            "task_type": card["task_type"],
            "priority": card["priority"],
            "status": card["status"],
            "description": _describe(f),
        })
    try:
        created = create_tasks(specs)
        return len(created), list(created)
    except Exception as exc:  # noqa: BLE001 -- a card write must never break the daemon
        logger.warning("claim_verifier_reflex: card write failed: %s", exc)
        return 0, []


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv as _load_dotenv

        _load_dotenv(BASE_DIR / ".env", override=True)
    except ImportError:
        pass
    import json

    print(json.dumps(run({"dry_run": "--dry-run" in sys.argv}), indent=2, default=str))
