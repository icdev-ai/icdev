# CUI // SP-CTI
"""Verify what a surface CLAIMS against an independently derived FACT (rem-hyg-17).

WHY THIS EXISTS. On 2026-08-20 eight defects were found by hand in one session,
and they were one defect wearing eight faces:

    the cache card    said "0 Entries" beside "every stored entry is past its TTL"
    the cache card    reported `unlogged=True` for a table pg_class calls 'p'
    the cache card    showed "0.0% Hit Rate" over zero traffic
    the governance card merged "structurally free" with "never instrumented"
    the posture card  scored 100.0 for canvases never assessed
    the posture trend subtracted one design's score from another's
    the project card  invented an epic from a hex id fragment
    the recovery panel called 331 retry ATTEMPTS "auto-recovered" (real: 46)
    the idle log      asserted "the usual cause is an open PR" with zero open PRs

THE DATA WAS ALMOST NEVER WRONG. The REDUCTION was. Every one of these is a
surface asserting a claim whose supporting evidence nothing ever re-derived.

WHY A LEARNER THAT WATCHES OUTPUTS CANNOT FIND THESE — the trap this module is
named for. A bad reduction produces a beautifully stable series. Measured on
this board: ``odc_gap_scores`` holds 91 rows spanning 2026-07-18..2026-08-20
carrying exactly ONE distinct value for ONE subject. Anything that gains
confidence from row count would rate that as extremely well corroborated; it is
a single stuck writer. Likewise five ``pr_watcher.resume`` rows for one task are
ONE failure repeated, not five recoveries.

    REPETITION IS NOT CORROBORATION.

So this module never learns from the system's own reported history. Each claim
carries TWO callables that must not share code: what the system REPORTS, and an
INDEPENDENT derivation of the same fact from primary data. The finding is their
disagreement. That is exactly the procedure that found all eight defects by hand
— panel said 14, `audit_trail` said 3; field said unlogged, `pg_class` said no.

WHAT IT DOES NOT DO. It never edits a claim to match reality: a surface talked
into agreeing with a broken reduction is how "0.0%" becomes documented
behaviour, and it is the same move as a test quietly weakened to match code that
broke. Corrective action is tiered by reversibility and stops well short of that
— see :data:`TIER`.

REPORT ONLY, no ``--gate``. This repo has learned twice (kpr-fix-03) that a
survey shipped with a gate earns itself a ``|| true``. Arming any claim requires
its own false-positive survey first.

WHERE A CLAIM COMES FROM (autonomy-lrn-01). Every claim cites an
:class:`Incident` — the defect that actually happened, by kanban task id and
date. That is the LEARNING rule: a claim is seeded from a VERIFIED FACT (the fix
is ``done`` on the board and landed on the default branch), never from a
pattern in the system's own reported history. ``tools/awareness/incident_claims``
is the path from one to the other and measures how many of a window's fixed
incidents have a standing claim — the conversion was manual, and mostly did not
happen: 4 claims against 58 done fixes in 7 days, measured 2026-08-21.

Usage:
    python tools/awareness/claim_verifier.py --json
    python tools/awareness/claim_verifier.py --claim posture_score_needs_evidence
    python tools/awareness/claim_verifier.py --list
    python tools/awareness/claim_verifier.py --incidents [--window-days 7]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)

# ── Verdicts ────────────────────────────────────────────────────────────────
#: The reported claim and the independent derivation agree.
AGREES = "agrees"
#: They disagree. THE finding — a surface is stating something untrue.
DISAGREES = "disagrees"
#: One side could not be derived. NEVER folded into `agrees`: "nobody checked"
#: and "checked and fine" justify opposite actions, and collapsing them is the
#: same defect this module exists to catch, committed by the checker itself.
UNMEASURABLE = "unmeasurable"

#: Corrective action, tiered by REVERSIBILITY. A verifier that edits the thing
#: it verifies can always make itself green.
TIER = {
    "report": "state the disagreement, showing BOTH derivations",
    "restore": "re-establish an invariant by a mechanical, individually "
               "verifiable act (reap a provably dead lease; drop a census entry "
               "whose file is gone). Never a change of meaning.",
    "propose": "seed a card carrying the evidence. A human decides.",
}
#: Deliberately absent: anything that edits the claim, the threshold or the
#: assertion so that the surface agrees with a reduction nobody re-derived.


@dataclass
class ClaimResult:
    claim_id: str
    verdict: str
    reported: Any = None
    derived: Any = None
    detail: str = ""
    #: How many INDEPENDENT observations back the derivation — see
    #: :func:`independent_observations`. Never a row count.
    independence: Optional[int] = None
    tier: str = "report"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id, "verdict": self.verdict,
            "reported": self.reported, "derived": self.derived,
            "detail": self.detail, "independence": self.independence,
            "tier": self.tier,
        }


@dataclass
class Incident:
    """The defect a claim was learned from — a FACT, cited by task id.

    A claim must be seeded from something that actually happened, or the
    registry becomes one more capability that reports clean because it does
    nothing. ``task_ids`` names every card that fixed the defect: two when the
    same defect was fixed at two sites weeks apart (hgx-park-01, rem-hyg-19),
    which is the whole reason a LIVE claim over the data beats a structural test
    over one function. ``tools.awareness.incident_claims.verify_incident``
    checks the citation against the board and the default branch.
    """

    task_ids: List[str]
    #: When the defect was OBSERVED (ISO date), not when the claim was written.
    observed_on: str
    #: One line: what the fix changed. Not a re-statement of the claim.
    fixed_by: str = ""


@dataclass
class Claim:
    """One checkable assertion a surface makes.

    ``reported`` and ``derived`` MUST NOT share an implementation. If the
    verifier calls the same function the surface calls, it proves only that the
    function is deterministic — which was never in doubt. Every defect above
    survived precisely because one computation was trusted twice.
    """

    claim_id: str
    description: str
    reported: Callable[[], Any]
    derived: Callable[[], Any]
    #: True when the pair is acceptable. Defaults to equality.
    agree: Callable[[Any, Any], bool] = lambda a, b: a == b
    tier: str = "report"
    tags: List[str] = field(default_factory=list)
    #: The defect this claim was learned from. Every registered claim cites one
    #: (asserted by tests/awareness/test_incident_claims.py); the default exists
    #: only so an ad-hoc Claim built in a test needs no provenance.
    incident: Optional[Incident] = None


def independent_observations(rows, subject_key: str, value_key: str) -> int:
    """Count DISTINCT (subject, value) pairs — never rows.

    The whole point. ``odc_gap_scores`` holds 91 rows carrying one value for one
    subject; five ``pr_watcher.resume`` rows for one task are one failure. A
    count of rows reads both as strong evidence, which is how a stuck writer
    comes to look like a stable measurement and a retry loop like a success
    streak.
    """
    seen = set()
    for row in rows or []:
        record = dict(row)
        seen.add((record.get(subject_key), record.get(value_key)))
    return len(seen)


def _is_empty(value: Any) -> bool:
    """An empty COLLECTION covered nothing. A scalar (0, False) did not.

    The distinction matters: `False` for `unlogged` is a real measured answer,
    while `[]` for "canvases holding evidence" means the query reached nothing.
    """
    return isinstance(value, (list, tuple, set, dict, str)) and len(value) == 0


def verify(claim: Claim) -> ClaimResult:
    """Run one claim. Never raises: a claim that cannot run is UNMEASURABLE."""
    try:
        reported = claim.reported()
    except Exception as exc:  # noqa: BLE001
        return ClaimResult(claim.claim_id, UNMEASURABLE, tier=claim.tier,
                           detail=f"the reported side could not be read: {exc}")
    try:
        derived = claim.derived()
    except Exception as exc:  # noqa: BLE001
        return ClaimResult(claim.claim_id, UNMEASURABLE, reported=reported,
                           tier=claim.tier,
                           detail=f"the independent side could not be derived: {exc}")

    if reported is None or derived is None:
        return ClaimResult(
            claim.claim_id, UNMEASURABLE, reported=reported, derived=derived,
            tier=claim.tier,
            detail="one side is None — nothing was measured, which is not agreement",
        )

    # A VACUOUS AGREEMENT IS NOT AN AGREEMENT, and this module very nearly
    # shipped committing the defect it exists to catch. Run against an empty
    # database both sides came back `[]` / `{}`, `[] == []` was True, and two
    # claims reported `agrees` having compared nothing at all. That is exactly
    # "no data rendered as a clean bill of health".
    if _is_empty(reported) and _is_empty(derived):
        return ClaimResult(
            claim.claim_id, UNMEASURABLE, reported=reported, derived=derived,
            tier=claim.tier,
            detail=("both sides are empty — the comparison is vacuously true and "
                    "covered nothing. A database with no operating history "
                    "reaches this branch, and must not read as verified"),
        )
    try:
        ok = bool(claim.agree(reported, derived))
    except Exception as exc:  # noqa: BLE001
        return ClaimResult(claim.claim_id, UNMEASURABLE, reported=reported,
                           derived=derived, tier=claim.tier,
                           detail=f"the comparison itself failed: {exc}")
    return ClaimResult(
        claim.claim_id, AGREES if ok else DISAGREES,
        reported=reported, derived=derived, tier=claim.tier,
        detail=claim.description if not ok else "",
    )


def verify_all(claims: List[Claim]) -> Dict[str, Any]:
    """Run every claim and summarise. `unmeasurable` is reported, never hidden."""
    results = [verify(c) for c in claims]
    counts = {AGREES: 0, DISAGREES: 0, UNMEASURABLE: 0}
    for r in results:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    return {
        "claims": len(results),
        "counts": counts,
        # The headline a human acts on. `unmeasurable` deliberately does NOT
        # make this False: a claim nobody could check is not a claim that failed.
        "any_disagreement": counts[DISAGREES] > 0,
        "results": [r.to_dict() for r in results],
    }


def render(report: Dict[str, Any]) -> str:
    out = [f"Claim verification — {report['claims']} claim(s)"]
    c = report["counts"]
    out.append(f"  agrees {c[AGREES]} · DISAGREES {c[DISAGREES]} · "
               f"unmeasurable {c[UNMEASURABLE]}")
    out.append("")
    for r in report["results"]:
        mark = {AGREES: "  ok  ", DISAGREES: " FAIL ", UNMEASURABLE: "  ??  "}[r["verdict"]]
        out.append(f"{mark} {r['claim_id']}")
        if r["verdict"] != AGREES:
            out.append(f"        reported: {r['reported']!r}")
            out.append(f"        derived : {r['derived']!r}")
            if r["detail"]:
                out.append(f"        {r['detail']}")
            out.append(f"        action tier: {r['tier']} — {TIER.get(r['tier'], '')}")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--claim", help="verify one claim by id")
    parser.add_argument("--list", action="store_true", help="list registered claims")
    parser.add_argument("--incidents", action="store_true",
                        help="which of the window's FIXED incidents have a standing "
                             "claim, and which claims cite an unverified incident")
    parser.add_argument("--window-days", type=int, default=7)
    args = parser.parse_args(argv)

    from tools.awareness.claims import REGISTRY

    if args.list:
        for c in REGISTRY:
            cited = ",".join(c.incident.task_ids) if c.incident else "NO INCIDENT"
            print(f"  {c.claim_id:34} [{c.tier}] <- {cited}")
            print(f"  {'':34} {c.description[:70]}")
        return 0

    if args.incidents:
        from tools.awareness import incident_claims

        report = incident_claims.coverage_report(REGISTRY, window_days=args.window_days)
        print(json.dumps(report, indent=2, default=str) if args.json
              else incident_claims.render(report))
        # Report only: a survey with a gate earns itself a `|| true` (kpr-fix-03).
        return 0 if report["state"] != "error" else 2

    claims = [c for c in REGISTRY if not args.claim or c.claim_id == args.claim]
    if args.claim and not claims:
        parser.error(f"no such claim: {args.claim}")

    report = verify_all(claims)
    print(json.dumps(report, indent=2, default=str) if args.json else render(report))
    # Report only — see the module docstring. Exit 2 only when NOTHING ran.
    return 0 if report["claims"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
