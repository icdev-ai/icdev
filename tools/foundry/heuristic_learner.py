# CUI // SP-CTI
"""heuristic_learner.py — ACF scorer-weight error-pattern learning (acf-ada-07).

Adapts ``tools/genesis/harness/heuristic_writer.py`` from *oracle_triage
heuristic* co-learning to *ACF scorer-weight* co-learning. Where the harness
writer studies high-confidence wrong triage calls and proposes new
promote/dismiss heuristics, this module studies ACF concepts that were approved,
built, and then **failed V&V** (``foundry_outcomes.outcome = 'vv_fail'``) and
proposes amendments to the composite-score weights in
``args/foundry_config.yaml -> scoring.weights``.

Pipeline (fully automated extraction; human-merge only at the end)::

    foundry_outcomes(vv_fail)  ─┐
                                ├─► extract_error_cases()  → failed score profiles
    foundry_outcomes(shipped/  ─┘                           (+ shipped baseline)
       vv_pass)
                                   propose_amendments()    → weight-bump proposals
                                                              (deterministic diff)
                                   write_proposed_amendments() → YAML for review

A proposal answers: *which score dimension did the scorer systematically
under-weight, such that risky concepts cleared the ``min_composite`` gate and
then failed?* e.g. failed concepts cluster at ``compliance_risk >= 0.6`` while
shipped ones don't → raise the ``compliance_risk`` penalty weight.

Determinism / air-gap: the extraction and the diff are pure statistics over the
``foundry_*`` tables — no LLM, no network. (The harness writer calls an LLM; ACF
weight amendments are a small, bounded numeric space, so a deterministic diff is
both sufficient and reproducible — which is what the tests assert.)

Gate: ``ICDEV_HARNESS_COLEARN=true`` (default off). Even when on, proposals are
written to ``args/foundry_scorer_heuristics_proposed.yaml`` for **human merge**
into ``args/foundry_config.yaml``; nothing is auto-applied.

NOTE on the proposal file: the harness writer owns
``args/oracle_heuristics_proposed.yaml`` (oracle_triage promote/dismiss
heuristics, a different schema and a different producer). To avoid clobbering it,
ACF scorer amendments land in a sibling, foundry-scoped file. Both are gated by
the same ``ICDEV_HARNESS_COLEARN`` flag and both require human merge.

Public API
----------
    extract_error_cases(limit=20, conn=None)      -> list[dict]
    extract_baseline(limit=50, conn=None)         -> list[dict]
    propose_amendments(error_cases, baseline=None, config=None) -> list[dict]
    write_proposed_amendments(proposals, n_err, n_base) -> bool
    run_colearn_pass(dry_run=False, conn=None)    -> dict

CLI
---
    python tools/foundry/heuristic_learner.py --run --json
    python tools/foundry/heuristic_learner.py --dry-run --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from tools.logging.icdev_logger import get_logger

LOG = get_logger("icdev.foundry.heuristic_learner")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_FILE = BASE_DIR / "args" / "foundry_config.yaml"
# Foundry-scoped proposal file — deliberately NOT oracle_heuristics_proposed.yaml
# (that file belongs to the harness oracle_triage co-learner; see module docstring).
PROPOSED_FILE = BASE_DIR / "args" / "foundry_scorer_heuristics_proposed.yaml"

COLEARN_ENV = "ICDEV_HARNESS_COLEARN"

# ── Score dimensions ─────────────────────────────────────────────────────────
# Cost dimensions: higher = worse (subtracted by the scorer). A failure cluster
# at HIGH values means the penalty weight was too small.
COST_DIMS: tuple[str, ...] = ("effort_estimate", "compliance_risk")
# Benefit dimensions: higher = better (added by the scorer). A failure cluster at
# LOW values that still cleared the gate means the weight was too small to drag
# weak concepts below ``min_composite``.
BENEFIT_DIMS: tuple[str, ...] = ("novelty_score", "market_score", "fit_score")

# concept score column -> scoring.weights key (the two name-spaces differ).
DIM_TO_WEIGHT: dict[str, str] = {
    "novelty_score": "novelty",
    "market_score": "market",
    "fit_score": "fit",
    "effort_estimate": "effort",
    "compliance_risk": "compliance_risk",
}

# Default weights mirror args/foundry_config.yaml -> scoring.weights so the diff
# still works when the config can't be read (air-gap / partial checkout).
_DEFAULT_WEIGHTS: dict[str, float] = {
    "novelty": 0.25,
    "market": 0.25,
    "fit": 0.25,
    "effort": 0.15,
    "compliance_risk": 0.10,
}

# Tunables for the diff. Conservative on purpose — we only fire when a dimension
# is genuinely distinguishing, mirroring heuristic_writer's "multiple cases, not
# one-off outliers" rule.
_HIGH_COST = 0.6          # a cost value at/above this is "risky"
_LOW_BENEFIT = 0.45       # a benefit value at/below this is "weak"
_MIN_SUPPORT = 0.5        # >= half the failures must share the trait
_SEP_MARGIN = 0.10        # failed mean must beat the shipped mean by this
_WEIGHT_BUMP = 1.5        # multiplicative bump applied to the under-sized weight
_WEIGHT_CAP = 0.50        # never propose a single weight above this


# =========================================================================
# DB ACCESS
# =========================================================================
def _conn():
    from tools.db.storage import get_connection

    return get_connection()


def _fetch(conn, outcomes: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
    """Return failed/baseline concept score profiles for the given outcomes."""
    placeholders = ", ".join("?" for _ in outcomes)
    rows = conn.execute(
        f"""
        SELECT fo.concept_id, fo.outcome, fo.metric, fo.created_at,
               fc.slug, fc.name,
               fc.novelty_score, fc.market_score, fc.fit_score,
               fc.effort_estimate, fc.compliance_risk, fc.composite_score
          FROM foundry_outcomes fo
          LEFT JOIN foundry_concepts fc ON fo.concept_id = fc.id
         WHERE fo.outcome IN ({placeholders})
         ORDER BY fo.created_at DESC, fo.id DESC
         LIMIT %s
        """,
        (*outcomes, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def extract_error_cases(limit: int = 20, conn=None) -> list[dict[str, Any]]:
    """Return the score profiles of concepts that were built and FAILED V&V.

    "Error case" = ``foundry_outcomes.outcome = 'vv_fail'``: the scorer + CoD gate
    approved the concept, it was seeded and built, and V&V then failed it. Each
    row carries the concept's full score profile so the diff can find which
    dimension the scorer under-weighted.

    Degrades gracefully (returns ``[]``) on any DB error so a partially-migrated
    database never crashes a caller.
    """
    own = conn is None
    try:
        c = conn or _conn()
        try:
            return _fetch(c, ("vv_fail",), limit)
        finally:
            if own:
                c.close()
    except Exception as exc:  # noqa: BLE001 - graceful degrade
        LOG.warning("[heuristic_learner] extract_error_cases failed: %s", exc)
        return []


def extract_baseline(limit: int = 50, conn=None) -> list[dict[str, Any]]:
    """Return score profiles of concepts that SUCCEEDED (shipped / vv_pass).

    This is the contrast set for the diff: a failed-concept trait only justifies a
    weight bump if it does NOT also describe the concepts that worked.
    """
    own = conn is None
    try:
        c = conn or _conn()
        try:
            return _fetch(c, ("shipped", "vv_pass"), limit)
        finally:
            if own:
                c.close()
    except Exception as exc:  # noqa: BLE001 - graceful degrade
        LOG.warning("[heuristic_learner] extract_baseline failed: %s", exc)
        return []


# =========================================================================
# DIFF / PROPOSAL
# =========================================================================
def _f(v: Any) -> float:
    """Coerce a (possibly NULL) score column to a float in [0, 1]."""
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _load_weights(config: Optional[dict[str, Any]]) -> dict[str, float]:
    """Resolve scoring.weights from an explicit config or args/foundry_config.yaml."""
    weights = dict(_DEFAULT_WEIGHTS)
    cfg = config
    if cfg is None:
        try:
            if CONFIG_FILE.exists():
                cfg = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            LOG.warning("[heuristic_learner] could not read %s: %s", CONFIG_FILE, exc)
            cfg = {}
    if isinstance(cfg, dict):
        raw = ((cfg.get("scoring") or {}).get("weights")) or {}
        for k, v in raw.items():
            try:
                weights[k] = float(v)
            except (TypeError, ValueError):
                continue
    return weights


def _trace_id(direction: str, dim: str, ids: list[int]) -> str:
    """Stable, content-addressed trace id (no clock / RNG → reproducible)."""
    digest = hashlib.sha256(f"{direction}|{dim}|{','.join(map(str, ids))}".encode()).hexdigest()
    return f"acf-heur-{digest[:10]}"


def _confidence(support: float, separation: float) -> float:
    """Blend trait prevalence (support) with how cleanly it separates fail vs ship."""
    return round(max(0.0, min(1.0, 0.6 * support + 0.4 * separation)), 4)


def _amendment(
    *,
    direction: str,
    dim: str,
    threshold: float,
    operator: str,
    weights: dict[str, float],
    members: list[dict[str, Any]],
    support: float,
    separation: float,
    fail_mean: float,
    base_mean: Optional[float],
) -> Optional[dict[str, Any]]:
    """Build one weight-bump proposal, or None if the bump would be a no-op."""
    wkey = DIM_TO_WEIGHT[dim]
    current = round(weights.get(wkey, 0.1), 4)
    proposed = round(min(current * _WEIGHT_BUMP, _WEIGHT_CAP), 4)
    if proposed <= current:
        return None  # already at/above the cap — nothing to propose

    ids = sorted(int(m["concept_id"]) for m in members if m.get("concept_id") is not None)
    base_txt = (
        f"vs shipped mean {base_mean:.2f}" if base_mean is not None else "no shipped baseline yet"
    )
    if direction == "penalize_high_cost":
        name = f"raise-{wkey}-penalty"
        rationale = (
            f"{len(members)}/{len(members)} of the matched vv_fail concepts had "
            f"{dim} {operator} {threshold} (mean {fail_mean:.2f}, {base_txt}); the "
            f"scorer under-penalized this cost — raise its weight {current}→{proposed}."
        )
    else:  # penalize_low_benefit
        name = f"raise-{wkey}-weight"
        rationale = (
            f"{len(members)} vv_fail concepts cleared min_composite despite "
            f"{dim} {operator} {threshold} (mean {fail_mean:.2f}, {base_txt}); raise the "
            f"{wkey} weight {current}→{proposed} so weak concepts drop below the gate."
        )

    return {
        "name": name,
        "trace_id": _trace_id(direction, dim, ids),
        "target": dim,
        "weight_key": wkey,
        "direction": direction,
        "condition": f"{dim} {operator} {threshold}",
        "action": "raise_weight",
        "current_weight": current,
        "proposed_weight": proposed,
        "confidence": _confidence(support, separation),
        "support": round(support, 4),
        "rationale": rationale,
        "evidence_concept_ids": ids,
    }


def propose_amendments(
    error_cases: list[dict[str, Any]],
    baseline: Optional[list[dict[str, Any]]] = None,
    config: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Diff failed concepts against the shipped baseline → weight-bump proposals.

    Returns ``[]`` when there are no error cases (so a shipped-only fixture yields
    zero proposals) or when no dimension is distinguishing enough to act on.
    """
    if not error_cases:
        return []

    weights = _load_weights(config)
    baseline = baseline or []
    n = len(error_cases)
    proposals: list[dict[str, Any]] = []

    # ── Cost dimensions: failures clustered at HIGH cost the scorer let through ──
    for dim in COST_DIMS:
        members = [c for c in error_cases if _f(c.get(dim)) >= _HIGH_COST]
        support = len(members) / n
        if support < _MIN_SUPPORT:
            continue
        fail_mean = _mean([_f(c.get(dim)) for c in members])
        base_vals = [_f(c.get(dim)) for c in baseline]
        base_mean = _mean(base_vals) if base_vals else None
        if base_mean is not None:
            if fail_mean <= base_mean + _SEP_MARGIN:
                continue  # shipped concepts look the same → not the cause
            separation = min(1.0, fail_mean - base_mean)
        else:
            # No baseline: separation = how far above the risk threshold the
            # failures sit (0 at the threshold, 1 at the max).
            separation = min(1.0, (fail_mean - _HIGH_COST) / max(1e-6, 1.0 - _HIGH_COST))
        amend = _amendment(
            direction="penalize_high_cost",
            dim=dim,
            threshold=_HIGH_COST,
            operator=">=",
            weights=weights,
            members=members,
            support=support,
            separation=separation,
            fail_mean=fail_mean,
            base_mean=base_mean,
        )
        if amend:
            proposals.append(amend)

    # ── Benefit dimensions: weak concepts that still cleared the gate ────────────
    for dim in BENEFIT_DIMS:
        members = [c for c in error_cases if _f(c.get(dim)) <= _LOW_BENEFIT]
        support = len(members) / n
        if support < _MIN_SUPPORT:
            continue
        fail_mean = _mean([_f(c.get(dim)) for c in members])
        base_vals = [_f(c.get(dim)) for c in baseline]
        base_mean = _mean(base_vals) if base_vals else None
        if base_mean is not None:
            if fail_mean >= base_mean - _SEP_MARGIN:
                continue  # shipped concepts were just as weak here → not the cause
            separation = min(1.0, base_mean - fail_mean)
        else:
            separation = min(1.0, (_LOW_BENEFIT - fail_mean) / max(1e-6, _LOW_BENEFIT))
        amend = _amendment(
            direction="penalize_low_benefit",
            dim=dim,
            threshold=_LOW_BENEFIT,
            operator="<=",
            weights=weights,
            members=members,
            support=support,
            separation=separation,
            fail_mean=fail_mean,
            base_mean=base_mean,
        )
        if amend:
            proposals.append(amend)

    return proposals


# =========================================================================
# WRITE
# =========================================================================
def write_proposed_amendments(
    proposals: list[dict[str, Any]], error_case_count: int, baseline_count: int
) -> bool:
    """Write the proposals to the foundry-scoped proposal YAML for human merge."""
    if not proposals:
        return False
    try:
        output = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "gated_by": COLEARN_ENV,
            "target_config": "args/foundry_config.yaml -> scoring.weights",
            "error_cases_analyzed": error_case_count,
            "shipped_baseline": baseline_count,
            "proposed": proposals,
        }
        PROPOSED_FILE.write_text(
            yaml.dump(output, default_flow_style=False, allow_unicode=True, sort_keys=False),
            encoding="utf-8", newline="",
        )
        LOG.info(
            "[heuristic_learner] wrote %d weight proposal(s) to %s",
            len(proposals),
            PROPOSED_FILE,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        LOG.warning("[heuristic_learner] write_proposed_amendments failed: %s", exc)
        return False


# =========================================================================
# DRIVER
# =========================================================================
def run_colearn_pass(dry_run: bool = False, conn=None) -> dict[str, Any]:
    """Full pass: extract vv_fail errors + baseline → propose → write.

    Gated by ``ICDEV_HARNESS_COLEARN``. Extraction is automated; the written
    proposals require a human to merge them into ``args/foundry_config.yaml``.
    """
    if os.getenv(COLEARN_ENV, "").lower() not in ("true", "1"):
        return {"skipped": True, "reason": f"{COLEARN_ENV} not enabled"}

    error_cases = extract_error_cases(conn=conn)
    if not error_cases:
        return {"skipped": True, "reason": "no vv_fail outcomes recorded"}

    baseline = extract_baseline(conn=conn)
    proposals = propose_amendments(error_cases, baseline)
    if not proposals:
        return {
            "skipped": True,
            "reason": "no distinguishing score dimension found",
            "error_cases_analyzed": len(error_cases),
        }

    if dry_run:
        return {
            "dry_run": True,
            "error_cases_analyzed": len(error_cases),
            "shipped_baseline": len(baseline),
            "proposals": proposals,
        }

    written = write_proposed_amendments(proposals, len(error_cases), len(baseline))
    return {
        "error_cases_analyzed": len(error_cases),
        "shipped_baseline": len(baseline),
        "proposals": len(proposals),
        "proposals_written": written,
        "proposed_file": str(PROPOSED_FILE) if written else None,
    }


# =========================================================================
# CLI
# =========================================================================
def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ACF scorer-weight error-pattern learner (acf-ada-07)"
    )
    parser.add_argument("--run", action="store_true", help="Run the co-learning pass and write proposals")
    parser.add_argument("--dry-run", action="store_true", help="Compute proposals but do not write")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)

    result = run_colearn_pass(dry_run=args.dry_run or not args.run)
    if args.json:
        print(json.dumps(result, default=str))
    else:
        if result.get("skipped"):
            print(f"skipped: {result.get('reason')}")
        else:
            n = result.get("proposals", len(result.get("proposals", []) or []))
            print(f"error cases: {result.get('error_cases_analyzed')}  proposals: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
