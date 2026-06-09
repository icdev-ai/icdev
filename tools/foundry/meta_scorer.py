# CUI // SP-CTI
"""meta_scorer.py — ACF composite-score self-tuning gate (acf-ada-02).

Brings the Genesis ``meta_harness`` adaptive-threshold pattern into the ACF
scorer: instead of the static ``args/foundry_config.yaml -> scoring.min_composite``
gate, the threshold is dynamically tightened (or, when the foundry is shipping
cleanly, loosened) based on a sliding window of recent ``foundry_outcomes``.

Two concerns, mirroring the meta-harness split:

1. ``adjust_threshold()`` — sliding-window *false-approve rate*.  A false-approve
   is a concept that the score gate cleared (``composite_score >= min_composite``)
   but that subsequently ended in ``vv_fail`` or ``abandoned``.  When the rate
   over the window exceeds ``adaptive.false_approve_ceiling`` (default 0.4),
   ``min_composite`` is raised (default +0.05 per pass, bounded above by
   ``adaptive.max_composite``).  A string of clean ``shipped`` / ``vv_pass``
   outcomes lowers it (default -0.05, bounded below by ``adaptive.min_composite_floor``)
   so the gate does not over-tighten into permanent starvation.

2. ``propose_removals()`` — like ``meta_harness._propose_heuristic_retirements``:
   when a *sub-score weight* consistently predicts failure (the failed concept's
   score in that dimension is well below the shipped baseline), propose
   *de-weighting* / retiring that weight.  These are written to the
   shared ``args/meta_harness_proposals.yaml`` so the human-merge review queue
   the Genesis meta-harness already maintains picks them up.

The meta-scorer is the *outer* loop of the ACF scorer: heuristic_learner (acf-ada-07)
proposes weight *bump* amendments from error-case cluster analysis, while this
module proposes threshold *tightening* / heuristic *retirement* from windowed
false-approve analytics.  They are complementary, not duplicative.

Determinism / air-gap: this module is pure statistics over the ``foundry_*``
tables + the local config file — no LLM, no network.  Threshold writes are
persisted to ``args/foundry_config.yaml`` so a fresh ``engine.run_cycle`` call
picks them up on the next pass; proposal writes go to
``args/meta_harness_proposals.yaml`` for human review.

Public API
----------
    load_config(path=None) -> dict
    save_config(cfg, path=None) -> bool
    compute_false_approve_rate(outcomes, window=None) -> float
    adjust_threshold(config=None, outcomes=None, config_path=None) -> dict
    propose_removals(config=None, outcomes=None) -> list[dict]
    write_proposals(proposals, metrics, path=None) -> Path | None
    run_meta_score(dry_run=False) -> dict

CLI
---
    python tools/foundry/meta_scorer.py --run --json
    python tools/foundry/meta_scorer.py --dry-run --json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from tools.logging.icdev_logger import get_logger

LOG = get_logger("icdev.foundry.meta_scorer")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_FILE = BASE_DIR / "args" / "foundry_config.yaml"
PROPOSALS_FILE = BASE_DIR / "args" / "meta_harness_proposals.yaml"

# ── Adaptive-gate defaults (mirror args/foundry_config.yaml -> adaptive) ──────
_DEFAULTS: dict[str, Any] = {
    "window": 5,                   # how many most-recent outcomes to look at
    "false_approve_ceiling": 0.4,  # raise threshold when FA rate > this
    "raise_step": 0.05,            # multiplicative/additive step on raise
    "lower_step": 0.05,            # step on lower (bounded)
    "max_composite": 0.95,         # never raise above this (would starve)
    "min_composite_floor": 0.30,   # never lower below this (open floodgate)
    "min_window_for_action": 3,    # need at least N outcomes to act
}

# Score dimensions we monitor for retirement.  Mirrors the column list the
# ACF scorer emits onto ``foundry_concepts`` (see heuristic_learner._DEFAULT_WEIGHTS).
_SCORE_DIMS: tuple[str, ...] = ("novelty", "feasibility", "strategic_fit", "market_timing")

# Outcomes that count as a "false approve" of the score gate.
_FALSE_APPROVE_OUTCOMES: frozenset[str] = frozenset({"vv_fail", "abandoned"})
_CLEAN_OUTCOMES: frozenset[str] = frozenset({"shipped", "vv_pass"})


# =========================================================================
# CONFIG I/O
# =========================================================================
def load_config(path: Optional[Path] = None) -> dict[str, Any]:
    """Load ``args/foundry_config.yaml`` (or the given override path).

    Returns ``{}`` (NOT a defaulted config) when the file is missing/unreadable
    so callers can distinguish "no config" from "config with defaults".  Use
    ``_adaptive_cfg()`` to merge the ``adaptive`` section with built-in defaults.
    """
    p = Path(path) if path else CONFIG_FILE
    try:
        if p.exists():
            return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - graceful degrade
        LOG.warning("[meta_scorer] could not read %s: %s", p, exc)
    return {}


def save_config(cfg: dict[str, Any], path: Optional[Path] = None) -> bool:
    """Persist the full foundry config back to disk (preserves keys we did not touch).

    Returns True on success, False on any error (so callers can log + continue).
    """
    p = Path(path) if path else CONFIG_FILE
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            yaml.dump(cfg, default_flow_style=False, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        LOG.info("[meta_scorer] wrote config to %s", p)
        return True
    except Exception as exc:  # noqa: BLE001
        LOG.warning("[meta_scorer] save_config failed: %s", exc)
        return False


def _adaptive_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    """Merge ``adaptive`` section over the module defaults (defaults lose)."""
    out = dict(_DEFAULTS)
    adaptive = (cfg.get("adaptive") or {}) if isinstance(cfg, dict) else {}
    if isinstance(adaptive, dict):
        for k, v in adaptive.items():
            if isinstance(v, (int, float)):
                out[k] = v
    return out


def _scoring_min(cfg: dict[str, Any]) -> float:
    """Read ``scoring.min_composite`` (float); fall back to 0.6 if absent."""
    try:
        return float((cfg.get("scoring") or {}).get("min_composite", 0.6))
    except (TypeError, ValueError):
        return 0.6


# =========================================================================
# OUTCOMES
# =========================================================================
def _fetch_outcomes(window: int, conn) -> list[dict[str, Any]]:
    """Return the N most-recent foundry_outcomes rows (id, outcome, metric, ts).

    Defers to ``get_connection()`` when no connection is passed.  Returns ``[]``
    on any DB error so a partially-migrated database never crashes a caller.
    """
    try:
        c = conn
        own = c is None
        if own:
            from tools.db.storage import get_connection
            c = get_connection()
        try:
            rows = c.execute(
                """
                SELECT id, concept_id, outcome, metric, detail, created_at
                  FROM foundry_outcomes
                 ORDER BY created_at DESC, id DESC
                 LIMIT ?
                """,
                (window,),
            ).fetchall()
            out = []
            for r in rows:
                if isinstance(r, dict):
                    out.append(r)
                else:
                    out.append({
                        "id": r[0], "concept_id": r[1], "outcome": r[2],
                        "metric": r[3], "detail": r[4], "created_at": r[5],
                    })
            return out
        finally:
            if own:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001 - graceful degrade
        LOG.debug("[meta_scorer] _fetch_outcomes failed: %s", exc)
        return []


def compute_false_approve_rate(
    outcomes: list[dict[str, Any]],
    window: Optional[int] = None,
) -> float:
    """Fraction of the most-recent N outcomes that are ``vv_fail`` or ``abandoned``.

    A "false approve" is the failure mode we care about: the concept cleared the
    score gate but then failed in build/V&V.  An empty window returns 0.0 (no
    evidence of trouble).  When ``window`` is None, the full list is used.
    """
    if not outcomes:
        return 0.0
    sample = outcomes if window is None else outcomes[: max(1, int(window))]
    if not sample:
        return 0.0
    bad = sum(1 for o in sample if str(o.get("outcome") or "") in _FALSE_APPROVE_OUTCOMES)
    return round(bad / len(sample), 4)


def _consecutive_suffix(outcomes: list[dict[str, Any]], predicate) -> int:
    """Count trailing outcomes (newest-first) matching ``predicate``."""
    n = 0
    for o in outcomes:
        if predicate(str(o.get("outcome") or "")):
            n += 1
        else:
            break
    return n


# =========================================================================
# ADJUST THRESHOLD
# =========================================================================
def _bounded(value: float, lo: float, hi: float) -> float:
    """Clamp a value into [lo, hi] and round to 4 decimals (matches config)."""
    return round(max(lo, min(hi, value)), 4)


def adjust_threshold(
    config: Optional[dict[str, Any]] = None,
    outcomes: Optional[list[dict[str, Any]]] = None,
    config_path: Optional[Path] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Decide whether to raise / lower ``scoring.min_composite`` and persist.

    Returns a dict with the action taken (or skipped), the old + new
    ``min_composite``, the observed false-approve rate, and the
    consecutive-fail / consecutive-clean counters.

    Rules (configurable via ``adaptive`` section in foundry_config.yaml):

    * FA rate > ``false_approve_ceiling`` (and >= ``min_window_for_action``
      samples observed)  → raise min_composite by ``raise_step``, bounded
      above by ``max_composite``.
    * Trailing N outcomes (N = ``min_window_for_action``) are all clean
      (shipped/vv_pass)  → lower min_composite by ``lower_step``, bounded
      below by ``min_composite_floor``.

    Both directions are bounded; both are no-ops at the boundary.  When
    ``config`` is provided, the in-memory dict is mutated and (when
    ``config_path`` is given OR the on-disk config is the same path) the
    updated value is persisted.
    """
    cfg = dict(config) if config is not None else load_config(config_path)
    adaptive = _adaptive_cfg(cfg)
    window = int(adaptive["window"])
    ceiling = float(adaptive["false_approve_ceiling"])
    raise_step = float(adaptive["raise_step"])
    lower_step = float(adaptive["lower_step"])
    hi = float(adaptive["max_composite"])
    lo = float(adaptive["min_composite_floor"])
    min_n = int(adaptive["min_window_for_action"])

    if outcomes is None:
        outcomes = _fetch_outcomes(window, conn=None)

    fa_rate = compute_false_approve_rate(outcomes, window=window)
    old_min = _scoring_min(cfg)
    new_min = old_min
    action = "hold"
    reason = "no_action"

    if len(outcomes) >= min_n and fa_rate > ceiling:
        proposed = old_min + raise_step
        bounded = _bounded(proposed, lo, hi)
        if bounded > old_min:
            new_min = bounded
            action = "raise"
            reason = (
                f"false_approve_rate={fa_rate:.3f} > ceiling {ceiling:.3f} "
                f"over window of {len(outcomes)}"
            )
    elif _consecutive_suffix(outcomes, lambda x: x in _CLEAN_OUTCOMES) >= min_n:
        proposed = old_min - lower_step
        bounded = _bounded(proposed, lo, hi)
        if bounded < old_min:
            new_min = bounded
            action = "lower"
            reason = (
                f"{min_n}+ consecutive clean outcomes "
                f"({_CLEAN_OUTCOMES}); gate can be relaxed"
            )
    else:
        reason = (
            f"fa_rate={fa_rate:.3f} (ceiling {ceiling:.3f}), "
            f"consecutive_clean={_consecutive_suffix(outcomes, lambda x: x in _CLEAN_OUTCOMES)}"
        )

    persisted = False
    if action != "hold" and new_min != old_min:
        # Mutate the in-memory config in place (caller's reference stays in sync).
        scoring = cfg.setdefault("scoring", {}) if isinstance(cfg, dict) else {}
        scoring["min_composite"] = new_min
        # Persist back to disk.  When the caller passed an explicit config_path,
        # write there; otherwise write to the canonical CONFIG_FILE.  We do this
        # even when the caller passed an in-memory ``config`` because the whole
        # point of this module is to tune the gate the engine will read on the
        # next cycle — keeping that update in memory only would surprise callers.
        # ``dry_run=True`` suppresses the on-disk write (the in-memory mutation
        # above is preserved so the caller can inspect the proposed value).
        if not dry_run:
            if config_path is not None:
                persisted = save_config(cfg, config_path)
            elif CONFIG_FILE.exists():
                persisted = save_config(cfg, CONFIG_FILE)

    return {
        "action": action,
        "reason": reason,
        "old_min_composite": old_min,
        "new_min_composite": new_min,
        "delta": round(new_min - old_min, 4),
        "false_approve_rate": fa_rate,
        "window_observed": len(outcomes),
        "ceiling": ceiling,
        "bounded_by": {"min": lo, "max": hi},
        "persisted": persisted,
    }


# =========================================================================
# PROPOSE REMOVALS
# =========================================================================
def _f(v: Any) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _fetch_score_profiles(concept_ids: list[int], conn) -> dict[int, dict[str, Any]]:
    """Resolve concept_id -> {novelty_score, market_score, fit_score, ...}.

    Joins ``foundry_concepts`` for the score columns.  Returns ``{}`` on any
    DB error (graceful degrade — ``propose_removals`` then returns ``[]``).
    """
    if not concept_ids:
        return {}
    try:
        own = conn is None
        c = conn
        if own:
            from tools.db.storage import get_connection
            c = get_connection()
        try:
            placeholders = ", ".join("?" for _ in concept_ids)
            rows = c.execute(
                f"""
                SELECT id, novelty_score, market_score, fit_score,
                       effort_estimate, compliance_risk
                  FROM foundry_concepts
                 WHERE id IN ({placeholders})
                """,
                tuple(concept_ids),
            ).fetchall()
            out: dict[int, dict[str, Any]] = {}
            for r in rows:
                if isinstance(r, dict):
                    rid = r["id"]
                    out[rid] = r
                else:
                    rid = r[0]
                    out[rid] = {
                        "id": r[0], "novelty_score": r[1], "market_score": r[2],
                        "fit_score": r[3], "effort_estimate": r[4], "compliance_risk": r[5],
                    }
            return out
        finally:
            if own:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001
        LOG.debug("[meta_scorer] _fetch_score_profiles failed: %s", exc)
        return {}


def _concept_id(outcome: dict[str, Any]) -> Optional[int]:
    cid = outcome.get("concept_id")
    try:
        return int(cid) if cid is not None else None
    except (TypeError, ValueError):
        return None


def propose_removals(
    config: Optional[dict[str, Any]] = None,
    outcomes: Optional[list[dict[str, Any]]] = None,
    conn: Any = None,
) -> list[dict[str, Any]]:
    """Propose de-weighting / retiring a sub-score weight that predicts failure.

    Mirrors ``meta_harness._propose_heuristic_retirements``: when the false-approve
    rate is above the ceiling AND a *specific* sub-score dimension on the failed
    concepts diverges from the shipped baseline, propose retiring it (lowering
    its weight to 0) or inverting it.

    Returns ``[]`` when the FA rate is below the ceiling (no structural change
    warranted) or when no single dimension is distinguishing enough to act on.
    """
    cfg = dict(config) if config is not None else load_config()
    adaptive = _adaptive_cfg(cfg)
    window = int(adaptive["window"])
    ceiling = float(adaptive["false_approve_ceiling"])

    if outcomes is None:
        outcomes = _fetch_outcomes(window, conn=conn)

    if not outcomes:
        return []
    fa_rate = compute_false_approve_rate(outcomes, window=window)
    if fa_rate <= ceiling:
        return []  # no structural change warranted

    failed = [o for o in outcomes if str(o.get("outcome") or "") in _FALSE_APPROVE_OUTCOMES]
    shipped = [o for o in outcomes if str(o.get("outcome") or "") in _CLEAN_OUTCOMES]
    if not failed:
        return []

    failed_ids = [cid for cid in (_concept_id(o) for o in failed) if cid is not None]
    shipped_ids = [cid for cid in (_concept_id(o) for o in shipped) if cid is not None]
    failed_profiles = _fetch_score_profiles(failed_ids, conn)
    shipped_profiles = _fetch_score_profiles(shipped_ids, conn)

    # Map: scoring.weights key -> foundry_concepts column name.
    dim_columns: dict[str, str] = {
        "novelty": "novelty_score",
        "feasibility": "fit_score",       # closest proxy on the concepts table
        "strategic_fit": "fit_score",
        "market_timing": "market_score",
    }

    proposals: list[dict[str, Any]] = []
    n_failed = len(failed)
    n_shipped = len(shipped)
    for dim, col in dim_columns.items():
        f_vals = [_f(failed_profiles[cid].get(col)) for cid in failed_profiles]
        if not f_vals:
            continue
        f_mean = _mean(f_vals)
        s_vals = [_f(shipped_profiles[cid].get(col)) for cid in shipped_profiles] if shipped_profiles else []
        s_mean = _mean(s_vals) if s_vals else None
        # Only propose when the failed cohort is markedly LOWER than shipped
        # in this dimension — a low mean on a benefit-style dimension means the
        # weight is over-rewarding weak concepts and should be retired/lowered.
        if s_mean is not None and f_mean >= s_mean - 0.10:
            continue  # not distinguishing
        # Support = fraction of failed concepts that have a profile row at all.
        support = len(f_vals) / n_failed if n_failed else 0.0
        if support < 0.5:
            continue

        current_weight = float(
            ((cfg.get("scoring") or {}).get("weights") or {}).get(dim, 0.0)
        )
        proposed_weight = 0.0  # retire
        proposals.append({
            "heuristic_name": f"acf-weight-{dim}",
            "weight_key": dim,
            "score_column": col,
            "current_weight": round(current_weight, 4),
            "proposed_weight": proposed_weight,
            "proposal": "retire" if current_weight > 0 else "de_weight",
            "support": round(support, 4),
            "fail_mean": round(f_mean, 4),
            "shipped_mean": round(s_mean, 4) if s_mean is not None else None,
            "fail_count": n_failed,
            "shipped_count": n_shipped,
            "false_approve_rate": fa_rate,
            "reason": (
                f"Sub-score '{dim}' (column {col}) averaged {f_mean:.2f} on "
                f"{n_failed} failed concepts"
                + (f" vs {s_mean:.2f} on {n_shipped} shipped" if s_mean is not None else "")
                + f" while FA rate {fa_rate:.2f} > ceiling {ceiling:.2f}. "
                f"Retire (weight {current_weight}→0) so weak concepts no longer "
                f"clear the gate on this dimension alone."
            ),
        })

    return sorted(proposals, key=lambda p: -p.get("fail_count", 0))


# =========================================================================
# PROPOSAL WRITER — shared with meta_harness, keyed by source
# =========================================================================
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_proposals(
    proposals: list[dict[str, Any]],
    metrics: dict[str, Any],
    path: Optional[Path] = None,
) -> Optional[Path]:
    """Append ACF scorer-retirement proposals into ``args/meta_harness_proposals.yaml``.

    Schema mirrors ``meta_harness._write_meta_proposals`` so a single human-merge
    review queue can drain both producers.  We key our block by ``source:
    "acf_meta_scorer"`` and keep the existing ``oracle_heuristic_retirements`` /
    ``heal_constitution_tightening`` keys untouched.  An existing file is read
    and amended; an absent one is created with a header.
    """
    if not proposals:
        return None
    p = Path(path) if path else PROPOSALS_FILE
    try:
        existing: dict[str, Any] = {}
        if p.exists():
            try:
                existing = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001
                existing = {}
        if not isinstance(existing, dict):
            existing = {}

        existing["generated_at"] = _now()
        existing.setdefault("metrics_snapshot", {})
        existing["metrics_snapshot"]["acf_meta_scorer"] = metrics
        existing.setdefault("acf_scorer_retirements", [])
        existing["acf_scorer_retirements"] = (
            existing.get("acf_scorer_retirements") or []
        ) + proposals

        header = (
            "# Meta-Harness / ACF Meta-Scorer Proposals — generated automatically\n"
            "# Review and apply manually:\n"
            "#   Oracle retirements: edit args/oracle_heuristics.yaml\n"
            "#   Heal tightening:    edit args/heal_constitution.yaml\n"
            "#   ACF weight retirements: edit args/foundry_config.yaml -> scoring.weights\n"
            "# Delete this file after review.\n\n"
        )
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            header + yaml.dump(existing, default_flow_style=False, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        LOG.info("[meta_scorer] wrote %d ACF weight-retirement proposal(s) to %s", len(proposals), p)
        return p
    except Exception as exc:  # noqa: BLE001
        LOG.warning("[meta_scorer] write_proposals failed: %s", exc)
        return None


# =========================================================================
# DRIVER
# =========================================================================
def run_meta_score(
    dry_run: bool = False,
    conn: Any = None,
) -> dict[str, Any]:
    """Run one ACF meta-score pass: adjust threshold + write retirement proposals.

    Always read-only when ``dry_run=True`` — the config is *not* persisted and
    proposals are *not* written.  On a real pass the threshold is persisted
    (via ``adjust_threshold``) and the retirement proposals are written to
    ``args/meta_harness_proposals.yaml`` (via ``write_proposals``).
    """
    cfg = load_config()
    adaptive = _adaptive_cfg(cfg)
    window = int(adaptive["window"])
    outcomes = _fetch_outcomes(window, conn=conn)

    adjust = adjust_threshold(config=cfg, outcomes=outcomes, dry_run=dry_run)

    proposals = propose_removals(config=cfg, outcomes=outcomes, conn=conn)
    metrics = {
        "window": window,
        "false_approve_rate": adjust["false_approve_rate"],
        "ceiling": adjust["ceiling"],
        "old_min_composite": adjust["old_min_composite"],
        "new_min_composite": adjust["new_min_composite"],
    }

    proposals_path: Optional[str] = None
    if proposals and not dry_run:
        written = write_proposals(proposals, metrics)
        proposals_path = str(written) if written else None

    return {
        "ran": True,
        "dry_run": dry_run,
        "adjust": adjust,
        "proposals": proposals,
        "proposals_written": bool(proposals and not dry_run),
        "proposals_path": proposals_path,
        "outcomes_observed": len(outcomes),
    }


# =========================================================================
# CLI
# =========================================================================
def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ACF meta-scorer — adaptive composite threshold (acf-ada-02)"
    )
    parser.add_argument("--run", action="store_true", help="Run a pass and persist changes")
    parser.add_argument("--dry-run", action="store_true", help="Compute but do not persist")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)

    result = run_meta_score(dry_run=args.dry_run or not args.run)
    if args.json:
        print(json.dumps(result, default=str))
    else:
        a = result["adjust"]
        print(
            f"action={a['action']}  fa_rate={a['false_approve_rate']:.3f}  "
            f"min_composite {a['old_min_composite']:.4f}→{a['new_min_composite']:.4f}  "
            f"proposals={len(result['proposals'])}  "
            f"path={result['proposals_path'] or '-'}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(_main())
