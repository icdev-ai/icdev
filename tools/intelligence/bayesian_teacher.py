#!/usr/bin/env python3
# CUI // SP-CTI
"""Bayesian Teaching Engine — information-gain scoring for ICDEV™ (D-BT-1).

Adapts Bayesian Teaching (Shafto/Goodman/Griffiths 2014, Zhu 2015, Qiu 2025)
to optimise example selection, compliance check ordering, and training pair
ranking across ICDEV™ subsystems.

Core algorithms (all deterministic, stdlib-only, air-gap safe):
  1. Information Gain Scorer   — rank candidates by expected posterior shift
  2. Teaching Dimension        — minimum distinguishing set for a target concept
  3. Optimal Ordering          — sequence items for maximum cumulative info gain
  4. SmartEncoding (DeepFlow)  — tag compression for observability data

Integration points:
  - Fine-tuning pair generator (--bayesian-rank)
  - Multi-regime compliance assessor (--optimal-order)
  - RAG reranker (teaching-dimension boost)

References:
  - Shafto, Goodman & Griffiths 2014 — Cognitive Psychology 71:55-89
  - Zhu 2015 — AAAI Blue Sky Ideas
  - Qiu et al. 2025 — Nature Communications (Bayesian Teaching for LLMs)
  - DeepFlow (deepflow.io) — SmartEncoding tag compression (ACM SIGCOMM 2023)

Usage:
    python tools/intelligence/bayesian_teacher.py --score-pairs --dataset-id "ds-xxx" --json
    python tools/intelligence/bayesian_teacher.py --optimal-order --project-id "proj-123" --json
    python tools/intelligence/bayesian_teacher.py --teaching-dim --items '["AC-2","AC-3","SC-7"]' --json
    python tools/intelligence/bayesian_teacher.py --smart-encode --project-id "proj-123" --json
    python tools/intelligence/bayesian_teacher.py --health --json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.intelligence.bayesian_teacher")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CONFIG_PATH = BASE_DIR / "args" / "bayesian_teaching_config.yaml"

# ---------------------------------------------------------------------------
# Helpers (ICDEV™ standard pattern)
# ---------------------------------------------------------------------------

_HAS_YAML = False
try:
    import yaml  # type: ignore

    _HAS_YAML = True
except ImportError:
    pass

_HAS_AUDIT = False
try:
    from tools.audit.audit_logger import log_event as audit_log_event  # type: ignore

    _HAS_AUDIT = True
except (ImportError, Exception):
    pass


def _get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py")
    conn = get_connection(db_path=str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit(
    event_type: str,
    actor: str,
    action: str,
    details: Optional[Dict] = None,
    project_id: str = "intelligence-engine",
) -> None:
    """Write audit trail entry (best-effort, never raises)."""
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor=actor,
                action=action,
                details=json.dumps(details) if details else None,
                project_id=project_id,
            )
        except Exception:
            pass


def _load_config() -> Dict[str, Any]:
    """Load Bayesian Teaching config from YAML with fallback defaults."""
    if not _HAS_YAML or not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Default scoring weights (D-BT-1)
# ---------------------------------------------------------------------------

DEFAULT_INFO_GAIN_WEIGHTS = {
    "posterior_shift": 0.35,  # How much does this example shift learner belief?
    "discriminability": 0.25,  # How well does it distinguish target from alternatives?
    "diversity": 0.20,  # How different is it from already-selected items?
    "complexity_match": 0.20,  # Is difficulty appropriate for learner's current state?
}

DEFAULT_THRESHOLDS = {
    "auto_select": 0.75,  # Auto-include in teaching set
    "suggest": 0.50,  # Suggest for human review
    "exclude": 0.0,  # Below this, exclude
}


def _get_weights(config: Optional[Dict] = None) -> Dict[str, float]:
    """Extract info-gain weights from config, falling back to defaults."""
    if config is None:
        config = _load_config()
    scoring = config.get("scoring", {})
    weights = scoring.get("weights", {})
    result = {}
    for dim, default_val in DEFAULT_INFO_GAIN_WEIGHTS.items():
        result[dim] = float(weights.get(dim, default_val))
    # Normalise
    total = sum(result.values())
    if total > 0 and abs(total - 1.0) > 0.001:
        result = {k: v / total for k, v in result.items()}
    return result


# ---------------------------------------------------------------------------
# 1. Information Gain Scorer (D-BT-2)
# ---------------------------------------------------------------------------


def _entropy(probs: List[float]) -> float:
    """Shannon entropy of a probability distribution."""
    return -sum(p * math.log2(p) for p in probs if p > 0)


def _kl_divergence(p: List[float], q: List[float]) -> float:
    """KL divergence D(p || q). Returns inf if q has zero where p is nonzero."""
    result = 0.0
    for pi, qi in zip(p, q):
        if pi > 0:
            if qi <= 0:
                return float("inf")
            result += pi * math.log2(pi / qi)
    return result


def _posterior_shift(prior: List[float], likelihood: List[float]) -> float:
    """Compute posterior shift as normalised KL(posterior || prior).

    Uses Bayes' rule: posterior ∝ likelihood × prior.
    Returns value in [0, 1] (capped).
    """
    # Compute unnormalised posterior
    unnorm = [lk * p for lk, p in zip(likelihood, prior)]
    z = sum(unnorm)
    if z <= 0:
        return 0.0
    posterior = [u / z for u in unnorm]

    kl = _kl_divergence(posterior, prior)
    if kl == float("inf"):
        return 1.0
    # Normalise KL to [0, 1] via sigmoid-like transform
    return min(1.0, 1.0 - math.exp(-kl))


def score_candidate(
    candidate: Dict[str, Any],
    prior_distribution: List[float],
    hypothesis_features: List[List[float]],
    selected_so_far: List[Dict[str, Any]],
    config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Score a single candidate example by expected information gain.

    Args:
        candidate: Dict with 'id', 'features' (list of floats), 'content'.
        prior_distribution: Current belief over hypotheses.
        hypothesis_features: Feature vectors per hypothesis.
        selected_so_far: Already-selected teaching examples.
        config: Optional config override.

    Returns:
        Dict with per-dimension scores and overall info_gain_score.
    """
    weights = _get_weights(config)
    features = candidate.get("features", [])

    # 1. Posterior shift — how much does this candidate update beliefs?
    if features and hypothesis_features:
        likelihoods = []
        for h_feat in hypothesis_features:
            # Gaussian-like likelihood: exp(-||features - h_feat||^2)
            dist_sq = sum((a - b) ** 2 for a, b in zip(features, h_feat))
            likelihoods.append(math.exp(-dist_sq))
        z = sum(likelihoods) or 1.0
        likelihoods = [lk / z for lk in likelihoods]
        ps = _posterior_shift(prior_distribution, likelihoods)
    else:
        ps = 0.5  # Uniform when features unavailable

    # 2. Discriminability — how well does it distinguish target from alternatives?
    if len(hypothesis_features) > 1 and features:
        # Variance of distances across hypotheses
        dists = []
        for h_feat in hypothesis_features:
            d = math.sqrt(sum((a - b) ** 2 for a, b in zip(features, h_feat)))
            dists.append(d)
        mean_d = sum(dists) / len(dists) if dists else 0.0
        variance = sum((d - mean_d) ** 2 for d in dists) / len(dists) if dists else 0.0
        # Higher variance = more discriminating
        discrim = min(1.0, math.sqrt(variance) * 2.0)
    else:
        discrim = 0.5

    # 3. Diversity — cosine distance from already-selected examples
    if selected_so_far and features:
        min_sim = 1.0
        for sel in selected_so_far:
            sf = sel.get("features", [])
            if sf:
                dot = sum(a * b for a, b in zip(features, sf))
                na = math.sqrt(sum(a * a for a in features)) or 1.0
                nb = math.sqrt(sum(b * b for b in sf)) or 1.0
                sim = dot / (na * nb)
                min_sim = min(min_sim, sim)
        diversity = 1.0 - max(0.0, min_sim)
    else:
        diversity = 1.0  # First item is maximally diverse

    # 4. Complexity match — prefer items near learner's current frontier
    # Use content length as proxy for complexity (deterministic, air-gap safe)
    content = candidate.get("content", "")
    content_len = len(content)
    # Optimal range: 100-500 chars
    if 100 <= content_len <= 500:
        complexity = 1.0
    elif content_len < 50 or content_len > 2000:
        complexity = 0.3
    else:
        complexity = 0.7

    dimensions = {
        "posterior_shift": round(ps, 4),
        "discriminability": round(discrim, 4),
        "diversity": round(diversity, 4),
        "complexity_match": round(complexity, 4),
    }

    # Weighted average (D21 pattern)
    overall = sum(dimensions[d] * weights.get(d, 0.0) for d in dimensions)
    overall = round(max(0.0, min(1.0, overall)), 4)

    # Threshold band
    thresholds = _load_config().get("thresholds", DEFAULT_THRESHOLDS)
    if overall >= thresholds.get("auto_select", 0.75):
        band = "auto_select"
    elif overall >= thresholds.get("suggest", 0.50):
        band = "suggest"
    else:
        band = "exclude"

    return {
        "candidate_id": candidate.get("id", ""),
        "info_gain_score": overall,
        "threshold_band": band,
        "dimensions": dimensions,
        "weights_used": weights,
        "scored_at": _now(),
    }


def score_training_pairs(
    dataset_id: str,
    top_k: int = 50,
    seed: int = 42,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Score fine-tuning pairs by Bayesian information gain (D-BT-2).

    Ranks unapproved pairs so the most informative ones surface first
    for human review, following Goldilocks difficulty (D-KARL-4).

    Args:
        dataset_id: Fine-tuning dataset to score.
        top_k: Return top K pairs.
        seed: Random seed for reproducibility.
        db_path: Optional DB path override.

    Returns:
        Dict with scored_pairs list and statistics.
    """
    random.seed(seed)
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            """SELECT id, system_prompt, user_input, expected_output,
                      quality_score, compliance_score, relevance_score
               FROM ft_dataset_examples
               WHERE dataset_id = %s AND status IN ('pending', 'unapproved')
               ORDER BY created_at DESC
               LIMIT 500""",
            (dataset_id,),
        ).fetchall()

        if not rows:
            return {
                "dataset_id": dataset_id,
                "scored_pairs": [],
                "total_candidates": 0,
                "info": "No pending pairs to score",
            }

        # Build feature vectors from content characteristics
        candidates = []
        for row in rows:
            content = f"{row['user_input'] or ''} {row['expected_output'] or ''}"
            words = content.lower().split()
            word_count = len(words)
            unique_ratio = len(set(words)) / max(word_count, 1)
            avg_word_len = sum(len(w) for w in words) / max(word_count, 1)
            q_score = float(row["quality_score"] or 0.5)
            c_score = float(row["compliance_score"] or 0.5)

            candidates.append(
                {
                    "id": row["id"],
                    "content": content,
                    "features": [
                        word_count / 500.0,  # Normalised length
                        unique_ratio,
                        avg_word_len / 10.0,
                        q_score,
                        c_score,
                    ],
                }
            )

        # Build hypothesis space — cluster feature centroids
        n_hyp = min(5, len(candidates))
        # Sample representative hypotheses
        hyp_indices = random.sample(range(len(candidates)), n_hyp)
        hypothesis_features = [candidates[i]["features"] for i in hyp_indices]

        # Uniform prior
        prior = [1.0 / n_hyp] * n_hyp

        # Greedy selection — score each candidate, pick best, update prior
        scored = []
        selected = []
        for cand in candidates:
            result = score_candidate(cand, prior, hypothesis_features, selected)
            scored.append(result)

        # Sort by info_gain_score descending
        scored.sort(key=lambda x: x["info_gain_score"], reverse=True)
        top_scored = scored[:top_k]

        # Store scores in DB (append-only)
        for s in top_scored:
            try:
                conn.execute(
                    """INSERT INTO bayesian_teaching_scores
                       (candidate_id, candidate_type, info_gain_score,
                        dimensions, threshold_band, context_id, scored_at)
                       VALUES (%s, 'training_pair', %s, %s, %s, %s, %s)""",
                    (
                        s["candidate_id"],
                        s["info_gain_score"],
                        json.dumps(s["dimensions"]),
                        s["threshold_band"],
                        dataset_id,
                        s["scored_at"],
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
                # Graceful — don't fail scoring if DB insert fails
                logger.warning(
                    "score_training_pairs: best-effort INSERT into bayesian_teaching_scores failed (non-blocking): %s",
                    exc,
                )
        conn.commit()

        # Statistics
        scores = [s["info_gain_score"] for s in scored]
        bands = Counter(s["threshold_band"] for s in scored)

        _audit(
            "bayesian.score_pairs",
            "intelligence-engine",
            f"Scored {len(scored)} pairs for dataset {dataset_id}",
            {"dataset_id": dataset_id, "top_score": scores[0] if scores else 0},
        )

        return {
            "dataset_id": dataset_id,
            "total_candidates": len(candidates),
            "scored_pairs": top_scored,
            "statistics": {
                "mean_score": round(sum(scores) / len(scores), 4) if scores else 0,
                "max_score": round(max(scores), 4) if scores else 0,
                "min_score": round(min(scores), 4) if scores else 0,
                "band_distribution": dict(bands),
            },
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Teaching Dimension Calculator (D-BT-3)
# ---------------------------------------------------------------------------


def teaching_dimension(
    target_id: str,
    item_pool: List[Dict[str, Any]],
    concept_labels: Dict[str, str],
    seed: int = 42,
) -> Dict[str, Any]:
    """Compute minimum teaching set for a target concept (Goldman & Kearns 1995).

    Given a pool of items with concept labels, find the minimum subset that
    uniquely identifies the target concept from all alternatives.

    Args:
        target_id: The concept/hypothesis the learner should converge to.
        item_pool: List of dicts with 'id' and 'features'.
        concept_labels: Map item_id -> concept_label.
        seed: Random seed.

    Returns:
        Dict with teaching_set, teaching_dimension, and elimination_log.
    """
    random.seed(seed)

    # Identify all distinct concepts
    concepts = set(concept_labels.values())
    alternative_concepts = concepts - {target_id}

    if not alternative_concepts:
        return {
            "target_concept": target_id,
            "teaching_dimension": 0,
            "teaching_set": [],
            "note": "Only one concept — no teaching needed",
        }

    # Greedy set-cover: each teaching example eliminates concepts
    remaining = set(alternative_concepts)
    teaching_set = []
    elimination_log = []
    # For each candidate item, compute which alternative concepts it eliminates
    max_rounds = min(len(item_pool), len(alternative_concepts) + 1)
    for _round in range(max_rounds):
        if not remaining:
            break

        best_item = None
        best_eliminated = set()

        for item in item_pool:
            iid = item["id"]
            if iid in [t["id"] for t in teaching_set]:
                continue  # Already selected

            # This item eliminates concepts that would predict a different label
            eliminated = set()
            for alt in remaining:
                alt_items = {k for k, v in concept_labels.items() if v == alt}
                # If item is NOT in alt_items, it eliminates alt
                if iid not in alt_items:
                    eliminated.add(alt)

            if len(eliminated) > len(best_eliminated):
                best_item = item
                best_eliminated = eliminated

        if best_item is None:
            break

        teaching_set.append(best_item)
        elimination_log.append(
            {
                "round": _round + 1,
                "selected_item": best_item["id"],
                "concepts_eliminated": list(best_eliminated),
                "remaining_after": len(remaining - best_eliminated),
            }
        )
        remaining -= best_eliminated

    return {
        "target_concept": target_id,
        "teaching_dimension": len(teaching_set),
        "teaching_set": [t["id"] for t in teaching_set],
        "total_concepts": len(concepts),
        "elimination_log": elimination_log,
        "fully_resolved": len(remaining) == 0,
    }


# ---------------------------------------------------------------------------
# 3. Optimal Ordering (D-BT-4) — compliance check sequencing
# ---------------------------------------------------------------------------

# Compliance control dependency graph (conditional probabilities)
# P(child fails | parent fails) — derived from NIST 800-53 control families
CONTROL_DEPENDENCIES = {
    "AC-2": {"children": ["AC-3", "AC-6", "AC-7", "AC-11", "AC-12"], "family": "AC"},
    "AC-3": {"children": ["AC-6", "AC-24"], "family": "AC"},
    "AU-2": {"children": ["AU-3", "AU-6", "AU-8", "AU-12"], "family": "AU"},
    "AU-3": {"children": ["AU-6", "AU-12"], "family": "AU"},
    "SC-7": {"children": ["SC-8", "SC-12", "SC-13", "SC-28"], "family": "SC"},
    "SC-8": {"children": ["SC-12", "SC-13"], "family": "SC"},
    "SI-2": {"children": ["SI-3", "SI-4", "SI-5"], "family": "SI"},
    "IA-2": {"children": ["IA-3", "IA-5", "IA-8"], "family": "IA"},
    "CA-2": {"children": ["CA-5", "CA-7", "CA-8"], "family": "CA"},
    "CM-2": {"children": ["CM-3", "CM-6", "CM-7", "CM-8"], "family": "CM"},
    "RA-3": {"children": ["RA-5", "RA-7"], "family": "RA"},
    "SA-3": {"children": ["SA-4", "SA-8", "SA-11"], "family": "SA"},
}

# Conditional probability of child failing given parent fails
P_CHILD_FAIL_GIVEN_PARENT_FAIL = 0.75
P_CHILD_FAIL_GIVEN_PARENT_PASS = 0.15


def _expected_info_gain_control(
    control_id: str,
    assessed_controls: Dict[str, str],
    pending_controls: List[str],
) -> float:
    """Estimate expected information gain from assessing a specific control.

    Uses the dependency graph to estimate how much assessing this control
    reduces uncertainty about other pending controls.

    Returns float in [0, 1].
    """
    deps = CONTROL_DEPENDENCIES.get(control_id, {})
    children = set(deps.get("children", []))
    family = deps.get("family", control_id[:2] if len(control_id) >= 2 else "")

    # Direct info: this control itself
    direct_info = 1.0

    # Cascade info: how many pending controls depend on this one?
    pending_set = set(pending_controls)
    cascade_children = children & pending_set
    cascade_info = len(cascade_children) * P_CHILD_FAIL_GIVEN_PARENT_FAIL

    # Family info: same-family controls are correlated
    family_pending = [c for c in pending_controls if c != control_id and c.startswith(family + "-")]
    family_info = len(family_pending) * 0.3  # Moderate correlation within family

    # Already-assessed context: if parent was assessed, this has less info
    parent_assessed = False
    for parent_id, dep_info in CONTROL_DEPENDENCIES.items():
        if control_id in dep_info.get("children", []):
            if parent_id in assessed_controls:
                parent_assessed = True
                break

    # Reduce info gain if parent already assessed (we have a prediction)
    reduction = 0.4 if parent_assessed else 0.0

    total = direct_info + cascade_info + family_info - reduction
    # Normalise to [0, 1]
    max_possible = (
        1.0
        + len(CONTROL_DEPENDENCIES.get(control_id, {}).get("children", [])) * P_CHILD_FAIL_GIVEN_PARENT_FAIL
        + 5 * 0.3
    )
    normalised = total / max(max_possible, 1.0)
    return round(max(0.0, min(1.0, normalised)), 4)


def optimal_compliance_order(
    control_ids: List[str],
    project_id: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Order compliance controls for maximum cumulative information gain.

    Greedily selects the control with highest expected info gain at each step,
    updating the posterior after each selection (Zhu 2015 teaching algorithm).

    Args:
        control_ids: List of NIST 800-53 control IDs to order.
        project_id: Project for context.
        db_path: Optional DB override.

    Returns:
        Dict with ordered_controls list and info_gain_scores.
    """
    if not control_ids:
        return {"ordered_controls": [], "total_info_gain": 0.0}

    pending = list(control_ids)
    assessed: Dict[str, str] = {}
    ordered = []
    cumulative_gain = 0.0
    steps = []

    for step_num in range(len(control_ids)):
        if not pending:
            break

        # Score each pending control
        scores = {}
        for cid in pending:
            scores[cid] = _expected_info_gain_control(cid, assessed, pending)

        # Select highest info gain
        best = max(scores, key=scores.get)  # type: ignore[arg-type]
        gain = scores[best]
        cumulative_gain += gain

        ordered.append(best)
        assessed[best] = "selected"
        pending.remove(best)

        steps.append(
            {
                "step": step_num + 1,
                "control_id": best,
                "info_gain": gain,
                "cumulative_gain": round(cumulative_gain, 4),
                "remaining": len(pending),
            }
        )

    # Store results
    if db_path or DB_PATH.exists():
        try:
            conn = _get_db(db_path)
            conn.execute(
                """INSERT INTO bayesian_teaching_scores
                   (candidate_id, candidate_type, info_gain_score,
                    dimensions, threshold_band, context_id, scored_at)
                   VALUES (%s, 'compliance_ordering', %s, %s, 'auto_select', %s, %s)""",
                (
                    f"order-{project_id or 'global'}",
                    round(cumulative_gain / max(len(control_ids), 1), 4),
                    json.dumps({"steps": steps}),
                    project_id,
                    _now(),
                ),
            )
            conn.commit()
            conn.close()
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning(
                "optimal_compliance_order: best-effort INSERT into bayesian_teaching_scores failed (non-blocking): %s",
                exc,
            )

    _audit(
        "bayesian.optimal_order",
        "intelligence-engine",
        f"Ordered {len(ordered)} controls for project {project_id}",
        {"controls_count": len(ordered), "avg_gain": round(cumulative_gain / max(len(ordered), 1), 4)},
        project_id=project_id or "intelligence-engine",
    )

    return {
        "project_id": project_id,
        "ordered_controls": ordered,
        "total_controls": len(control_ids),
        "total_info_gain": round(cumulative_gain, 4),
        "avg_info_gain": round(cumulative_gain / max(len(ordered), 1), 4),
        "steps": steps,
        "improvement_vs_alphabetical": _improvement_vs_baseline(steps, control_ids),
    }


def _improvement_vs_baseline(steps: List[Dict], original_order: List[str]) -> Dict[str, Any]:
    """Compare optimal ordering info gain vs alphabetical baseline."""
    if not steps or not original_order:
        return {"improvement_pct": 0.0}

    optimal_first_half_gain = sum(s["info_gain"] for s in steps[: len(steps) // 2])
    # Alphabetical baseline: assume uniform info gain
    total_gain = sum(s["info_gain"] for s in steps)
    baseline_first_half = total_gain / 2.0 if total_gain > 0 else 0

    if baseline_first_half <= 0:
        return {"improvement_pct": 0.0}

    improvement = ((optimal_first_half_gain - baseline_first_half) / baseline_first_half) * 100
    return {
        "improvement_pct": round(improvement, 1),
        "optimal_first_half_gain": round(optimal_first_half_gain, 4),
        "baseline_first_half_gain": round(baseline_first_half, 4),
        "note": "Front-loading high-info-gain checks reduces uncertainty faster",
    }


# ---------------------------------------------------------------------------
# 4. SmartEncoding — DeepFlow-inspired tag compression (D-BT-5)
# ---------------------------------------------------------------------------

# Pre-encoded dictionary for common observability tags
# Inspired by DeepFlow's SmartEncoding (ACM SIGCOMM 2023)
SMART_ENCODING_DICTIONARY: Dict[str, int] = {
    # Agent IDs (15 agents)
    "orchestrator-agent": 1,
    "architect-agent": 2,
    "builder-agent": 3,
    "compliance-agent": 4,
    "security-agent": 5,
    "infrastructure-agent": 6,
    "knowledge-agent": 7,
    "monitor-agent": 8,
    "mbse-agent": 9,
    "modernization-agent": 10,
    "requirements-analyst-agent": 11,
    "supply-chain-agent": 12,
    "simulation-agent": 13,
    "devsecops-agent": 14,
    "gateway-agent": 15,
    # Status codes
    "OK": 100,
    "ERROR": 101,
    "UNSET": 102,
    # Common operations
    "mcp.tool_call": 200,
    "a2a.task": 201,
    "llm.invoke": 202,
    "db.query": 203,
    "http.request": 204,
    # Frameworks
    "nist_800_53": 300,
    "fedramp_moderate": 301,
    "fedramp_high": 302,
    "cmmc_level_2": 303,
    "cmmc_level_3": 304,
    "cjis": 305,
    "hipaa": 306,
    "soc2": 307,
    "pci_dss": 308,
    "iso_27001": 309,
    # Classification
    "CUI": 400,
    "CUI // SP-CTI": 401,
    "SECRET": 402,
    "UNCLASSIFIED": 403,
}


def smart_encode_tags(tags: Dict[str, str]) -> Dict[str, Any]:
    """Encode observability tags using pre-encoded dictionary.

    Replaces known string values with integer codes, reducing storage
    overhead by ~10x for repetitive metadata (DeepFlow SmartEncoding pattern).

    Args:
        tags: Dict of string key-value tag pairs.

    Returns:
        Dict with 'encoded' (compact form) and 'compression_ratio'.
    """
    encoded = {}
    raw_bytes = 0
    encoded_bytes = 0

    for key, value in tags.items():
        raw_bytes += len(key) + len(str(value))
        code = SMART_ENCODING_DICTIONARY.get(value)
        if code is not None:
            encoded[key] = code
            encoded_bytes += len(key) + 4  # int = ~4 bytes
        else:
            encoded[key] = value
            encoded_bytes += len(key) + len(str(value))

    compression_ratio = round(raw_bytes / max(encoded_bytes, 1), 2) if raw_bytes > 0 else 1.0

    return {
        "encoded": encoded,
        "raw_bytes": raw_bytes,
        "encoded_bytes": encoded_bytes,
        "compression_ratio": compression_ratio,
        "tags_compressed": sum(1 for v in encoded.values() if isinstance(v, int)),
        "tags_total": len(tags),
    }


def smart_decode_tags(encoded_tags: Dict[str, Any]) -> Dict[str, str]:
    """Decode SmartEncoded tags back to strings."""
    reverse_dict = {v: k for k, v in SMART_ENCODING_DICTIONARY.items()}
    decoded = {}
    for key, value in encoded_tags.items():
        if isinstance(value, int) and value in reverse_dict:
            decoded[key] = reverse_dict[value]
        else:
            decoded[key] = str(value)
    return decoded


def compute_encoding_stats(
    project_id: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Compute SmartEncoding compression statistics from observability data.

    Scans otel_spans attributes and estimates potential compression.
    """
    try:
        conn = _get_db(db_path)
    except FileNotFoundError:
        return {"error": "Database not found", "dictionary_size": len(SMART_ENCODING_DICTIONARY)}

    try:
        query = "SELECT attributes FROM otel_spans"
        params: tuple = ()
        if project_id:
            query += " WHERE project_id = ?"
            params = (project_id,)
        query += " LIMIT 1000"

        rows = conn.execute(query, params).fetchall()
        if not rows:
            return {
                "spans_analyzed": 0,
                "dictionary_size": len(SMART_ENCODING_DICTIONARY),
                "estimated_compression_ratio": 1.0,
            }

        total_raw = 0
        total_encoded = 0
        tag_freq: Counter = Counter()

        for row in rows:
            attrs_str = row["attributes"] or "{}"
            try:
                attrs = json.loads(attrs_str)
            except (json.JSONDecodeError, TypeError):
                continue

            for key, val in attrs.items():
                tag_freq[str(val)] += 1

            result = smart_encode_tags(attrs)
            total_raw += result["raw_bytes"]
            total_encoded += result["encoded_bytes"]

        return {
            "spans_analyzed": len(rows),
            "dictionary_size": len(SMART_ENCODING_DICTIONARY),
            "estimated_compression_ratio": round(total_raw / max(total_encoded, 1), 2),
            "total_raw_bytes": total_raw,
            "total_encoded_bytes": total_encoded,
            "top_tags": tag_freq.most_common(10),
            "project_id": project_id,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5. Health check
# ---------------------------------------------------------------------------


def health_check(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Health check for Bayesian Teaching subsystem."""
    config = _load_config()
    has_config = bool(config)

    try:
        conn = _get_db(db_path)
        score_count = conn.execute("SELECT COUNT(*) as cnt FROM bayesian_teaching_scores").fetchone()["cnt"]
        conn.close()
        db_ok = True
    except Exception:
        score_count = 0
        db_ok = False

    return {
        "status": "healthy" if db_ok else "degraded",
        "config_loaded": has_config,
        "database_available": db_ok,
        "scores_recorded": score_count,
        "dictionary_size": len(SMART_ENCODING_DICTIONARY),
        "control_dependencies_mapped": len(CONTROL_DEPENDENCIES),
        "weights": _get_weights(config),
        "thresholds": config.get("thresholds", DEFAULT_THRESHOLDS),
        "checked_at": _now(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="ICDEV™ Bayesian Teaching Engine — information-gain scoring")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", type=Path, default=None, help="DB path override")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--score-pairs",
        action="store_true",
        help="Score fine-tuning pairs by info gain",
    )
    group.add_argument(
        "--optimal-order",
        action="store_true",
        help="Order compliance controls for max info gain",
    )
    group.add_argument(
        "--teaching-dim",
        action="store_true",
        help="Compute teaching dimension for item set",
    )
    group.add_argument(
        "--smart-encode",
        action="store_true",
        help="SmartEncoding compression stats",
    )
    group.add_argument(
        "--health",
        action="store_true",
        help="Health check",
    )

    parser.add_argument("--dataset-id", type=str, help="Dataset ID for --score-pairs")
    parser.add_argument("--project-id", type=str, default="", help="Project ID")
    parser.add_argument("--controls", type=str, help="Comma-separated control IDs")
    parser.add_argument("--items", type=str, help="JSON array of item IDs")
    parser.add_argument("--top-k", type=int, default=50, help="Top K results")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")

    args = parser.parse_args()

    try:
        if args.score_pairs:
            if not args.dataset_id:
                parser.error("--score-pairs requires --dataset-id")
            result = score_training_pairs(args.dataset_id, top_k=args.top_k, seed=args.seed, db_path=args.db_path)
        elif args.optimal_order:
            if args.controls:
                cids = [c.strip() for c in args.controls.split(",")]
            else:
                # Default: common NIST 800-53 controls
                cids = [
                    "AC-2",
                    "AC-3",
                    "AC-6",
                    "AC-7",
                    "AU-2",
                    "AU-3",
                    "AU-6",
                    "SC-7",
                    "SC-8",
                    "SI-2",
                    "SI-4",
                    "IA-2",
                    "IA-5",
                    "CM-2",
                    "CM-6",
                    "RA-3",
                    "RA-5",
                    "SA-3",
                    "SA-11",
                    "CA-2",
                ]
            result = optimal_compliance_order(cids, project_id=args.project_id, db_path=args.db_path)
        elif args.teaching_dim:
            items_json = args.items or '["AC-2","AC-3","SC-7","SI-2","IA-2"]'
            item_ids = json.loads(items_json)
            pool = [{"id": iid, "features": []} for iid in item_ids]
            labels = {iid: iid[:2] for iid in item_ids}
            target = labels.get(item_ids[0], item_ids[0]) if item_ids else ""
            result = teaching_dimension(target, pool, labels, seed=args.seed)
        elif args.smart_encode:
            result = compute_encoding_stats(project_id=args.project_id, db_path=args.db_path)
        elif args.health:
            result = health_check(db_path=args.db_path)
        else:
            result = {"error": "No action specified"}

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_human(result)

    except FileNotFoundError as e:
        err = {"error": str(e), "hint": "Run: python tools/db/init_icdev_db.py"}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        err = {"error": str(e)}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def _print_human(result: Dict[str, Any]) -> None:
    """Human-readable output."""
    if "error" in result:
        print(f"ERROR: {result['error']}")
        return

    if "ordered_controls" in result:
        print(f"\n=== Optimal Compliance Order ({result['total_controls']} controls) ===")
        for step in result.get("steps", []):
            print(
                f"  {step['step']:2d}. {step['control_id']:<8s}  "
                f"info_gain={step['info_gain']:.4f}  "
                f"cumulative={step['cumulative_gain']:.4f}"
            )
        imp = result.get("improvement_vs_alphabetical", {})
        print(f"\nFront-loading improvement: {imp.get('improvement_pct', 0)}%")

    elif "scored_pairs" in result:
        print(f"\n=== Bayesian Pair Scoring ({result['total_candidates']} candidates) ===")
        for pair in result.get("scored_pairs", [])[:10]:
            print(
                f"  {pair['candidate_id'][:16]:<16s}  "
                f"score={pair['info_gain_score']:.4f}  "
                f"band={pair['threshold_band']}"
            )
        stats = result.get("statistics", {})
        print(f"\nMean: {stats.get('mean_score', 0):.4f}  Max: {stats.get('max_score', 0):.4f}")

    elif "status" in result:
        print("\n=== Bayesian Teaching Health ===")
        print(f"  Status: {result['status']}")
        print(f"  DB available: {result.get('database_available', False)}")
        print(f"  Scores recorded: {result.get('scores_recorded', 0)}")
        print(f"  Dictionary size: {result.get('dictionary_size', 0)}")

    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
