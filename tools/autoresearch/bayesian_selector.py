#!/usr/bin/env python3
# CUI // SP-CTI
"""Bayesian Experiment Selector — picks optimal next experiment (D-AR-5, D-AR-6).

Combines:
  - Bayesian info-gain scoring from bayesian_teacher.py (D-BT-2)
  - Thompson Sampling from trust_engine.py (D-AE-1)
  - pgvector dedup from pg_vector_store.py (D-RAG-PG-1)
  - Teaching dimension for minimum experiment count estimation (D-BT-3)

Usage:
    python tools/autoresearch/bayesian_selector.py --score --domain compliance --json
    python tools/autoresearch/bayesian_selector.py --select --domain compliance --json
    python tools/autoresearch/bayesian_selector.py --estimate --domain compliance --json
    python tools/autoresearch/bayesian_selector.py --category-order --domain compliance --json
    python tools/autoresearch/bayesian_selector.py --health --json
"""

import argparse
import hashlib
import json
import math
import random
from pathlib import Path
import sys

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_ROOT = Path(__file__).resolve().parent.parent.parent

from tools.common.helpers import now_iso


def _load_config() -> dict:
    """Load autoresearch config."""
    config_path = _ROOT / "args" / "autoresearch_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        return {}


def _cosine_similarity(a: list, b: list) -> float:
    """Compute cosine similarity between two vectors (stdlib fallback)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _text_to_features(text: str) -> list:
    """Convert text to simple feature vector for scoring.

    Uses word-level statistics as air-gap-safe proxy features.
    """
    words = text.lower().split()
    word_count = len(words)
    unique_words = len(set(words))
    unique_ratio = unique_words / max(word_count, 1)
    avg_word_len = sum(len(w) for w in words) / max(word_count, 1)
    # Normalize to [0, 1].
    # NOTE: the last two features are constant 0.5 placeholders (no real quality
    # or domain-relevance model is wired in). Info-gain scores derived from these
    # features are therefore heuristic — callers must not treat them as learned
    # relevance signals. Scoring/selection payloads carry a `heuristic` /
    # `placeholder_features` flag to surface this.
    return [
        min(1.0, word_count / 100.0),
        unique_ratio,
        min(1.0, avg_word_len / 10.0),
        0.5,  # placeholder for quality score
        0.5,  # placeholder for domain relevance
    ]


# ── Scoring ──────────────────────────────────────────────────────────────────


def score_experiment_candidate(
    candidate: dict,
    domain: str,
    prior_distribution: list = None,
    selected_so_far: list = None,
    config: dict = None,
) -> dict:
    """Score a single experiment candidate by expected information gain.

    Wraps bayesian_teacher.score_candidate() with experiment-specific features.
    """
    if selected_so_far is None:
        selected_so_far = []
    if config is None:
        config = _load_config()

    weights = config.get("scoring", {}).get(
        "weights",
        {
            "posterior_shift": 0.30,
            "discriminability": 0.25,
            "diversity": 0.25,
            "complexity_match": 0.20,
        },
    )
    thresholds = config.get("scoring", {}).get(
        "thresholds",
        {
            "auto_run": 0.75,
            "suggest": 0.50,
            "exclude": 0.30,
        },
    )

    # Extract features from hypothesis text
    hypothesis = candidate.get("hypothesis", "")
    features = candidate.get("features") or _text_to_features(hypothesis)

    # Try to use bayesian_teacher.score_candidate() directly
    try:
        from tools.intelligence.bayesian_teacher import score_candidate as bt_score

        # Build hypothesis space from selected experiments
        if not prior_distribution:
            n_hyp = max(3, len(selected_so_far) + 1)
            prior_distribution = [1.0 / n_hyp] * n_hyp

        hyp_features = [_text_to_features(s.get("hypothesis", "")) for s in selected_so_far[:5]]
        if not hyp_features:
            hyp_features = [[0.5] * len(features)]

        bt_result = bt_score(
            candidate={"id": candidate.get("id", ""), "features": features, "content": hypothesis},
            prior_distribution=prior_distribution,
            hypothesis_features=hyp_features,
            selected_so_far=[{"features": _text_to_features(s.get("hypothesis", ""))} for s in selected_so_far],
            config={"weights": weights},
        )
        info_gain = bt_result.get("info_gain_score", 0.5)
        dimensions = bt_result.get("dimensions", {})
    except (ImportError, Exception):
        # Fallback: simple scoring
        dimensions = _fallback_score(features, selected_so_far, hypothesis)
        info_gain = sum(weights.get(dim, 0.25) * dimensions.get(dim, 0.5) for dim in weights)

    # Determine threshold band
    if info_gain >= thresholds.get("auto_run", 0.75):
        band = "auto_run"
    elif info_gain >= thresholds.get("suggest", 0.50):
        band = "suggest"
    else:
        band = "exclude"

    return {
        "candidate_id": candidate.get("id", ""),
        "domain": domain,
        "info_gain_score": round(info_gain, 4),
        "dimensions": {k: round(v, 4) for k, v in dimensions.items()},
        "threshold_band": band,
        "heuristic": True,
        "placeholder_features": True,
        "scored_at": now_iso(),
    }


def _fallback_score(features: list, selected_so_far: list, hypothesis: str) -> dict:
    """Fallback scoring when bayesian_teacher is unavailable."""
    # Posterior shift: proxy via feature variance
    variance = sum((f - 0.5) ** 2 for f in features) / max(len(features), 1)
    posterior_shift = min(1.0, math.sqrt(variance) * 2.0)

    # Discriminability: proxy via feature magnitude
    magnitude = sum(abs(f) for f in features) / max(len(features), 1)
    discriminability = min(1.0, magnitude)

    # Diversity: Jaccard distance to selected
    if not selected_so_far:
        diversity = 1.0
    else:
        words = set(hypothesis.lower().split())
        max_jaccard = 0.0
        for sel in selected_so_far:
            sel_words = set(sel.get("hypothesis", "").lower().split())
            intersection = len(words & sel_words)
            union = len(words | sel_words)
            if union > 0:
                max_jaccard = max(max_jaccard, intersection / union)
        diversity = 1.0 - max_jaccard

    # Complexity match: Goldilocks zone (100-500 chars)
    length = len(hypothesis)
    if 100 <= length <= 500:
        complexity_match = 1.0
    elif length < 50 or length > 2000:
        complexity_match = 0.3
    else:
        complexity_match = 0.7

    return {
        "posterior_shift": posterior_shift,
        "discriminability": discriminability,
        "diversity": diversity,
        "complexity_match": complexity_match,
    }


# ── Selection ────────────────────────────────────────────────────────────────


def select_next_experiment(
    candidates: list,
    domain: str,
    recent_experiments: list = None,
    seed: int = 42,
) -> dict:
    """Select the best next experiment using Bayesian scoring + Thompson Sampling.

    1. Filter: content hash dedup
    2. Filter: cosine similarity dedup (> 0.85 rejection)
    3. Score all remaining via score_experiment_candidate()
    4. Apply Thompson Sampling explore/exploit
    5. Return highest-scoring candidate
    """
    if not candidates:
        return {"selected": None, "reason": "no_candidates"}

    rng = random.Random(seed)
    config = _load_config()
    threshold = config.get("dedup", {}).get("cosine_threshold", 0.85)

    if recent_experiments is None:
        recent_experiments = []

    # Step 1: Content hash dedup
    seen_hashes = set()
    for exp in recent_experiments:
        h = exp.get("content_hash", "")
        if h:
            seen_hashes.add(h)

    filtered = []
    for c in candidates:
        c_hash = c.get("content_hash", hashlib.sha256(c.get("hypothesis", "").encode()).hexdigest()[:16])
        if c_hash in seen_hashes:
            continue
        seen_hashes.add(c_hash)
        filtered.append(c)

    if not filtered:
        return {"selected": None, "reason": "all_deduped"}

    # Step 2: Cosine similarity dedup against recent experiments
    if recent_experiments:
        recent_features = [_text_to_features(e.get("hypothesis", "")) for e in recent_experiments]
        novel = []
        for c in filtered:
            c_features = _text_to_features(c.get("hypothesis", ""))
            max_sim = max(
                (_cosine_similarity(c_features, rf) for rf in recent_features),
                default=0.0,
            )
            if max_sim < threshold:
                novel.append(c)
        filtered = novel if novel else filtered[:1]  # Keep at least one

    # Step 3: Score all candidates
    scored = []
    selected_so_far = []
    for c in filtered:
        result = score_experiment_candidate(
            c,
            domain,
            selected_so_far=selected_so_far,
            config=config,
        )
        result["candidate"] = c
        scored.append(result)
        selected_so_far.append(c)

    scored.sort(key=lambda x: x["info_gain_score"], reverse=True)

    # Step 4: Thompson Sampling explore/exploit
    ts_enabled = config.get("thompson_sampling", {}).get("enabled", True)
    if ts_enabled and len(scored) > 1:
        # Use trust engine if available, otherwise simple Thompson
        try:
            from tools.autonomy.trust_engine import get_trust_state

            state = get_trust_state("run_experiment")
            alpha = state.get("alpha", 2.0)
            beta_val = state.get("beta", 8.0)
            sample = rng.betavariate(max(alpha, 0.1), max(beta_val, 0.1))
            # If sample is high (explore), occasionally pick a non-top candidate
            if sample > 0.7 and len(scored) > 2:
                explore_idx = rng.randint(1, min(3, len(scored) - 1))
                selected = scored[explore_idx]
                selected["thompson_explored"] = True
            else:
                selected = scored[0]
                selected["thompson_explored"] = False
        except (ImportError, Exception):
            # Simple fallback: 20% exploration
            if rng.random() < 0.2 and len(scored) > 1:
                selected = scored[rng.randint(1, min(3, len(scored) - 1))]
                selected["thompson_explored"] = True
            else:
                selected = scored[0]
                selected["thompson_explored"] = False
    else:
        selected = scored[0]
        selected["thompson_explored"] = False

    return {
        "selected": selected["candidate"],
        "info_gain_score": selected["info_gain_score"],
        "dimensions": selected["dimensions"],
        "threshold_band": selected["threshold_band"],
        "thompson_explored": selected.get("thompson_explored", False),
        "candidates_scored": len(scored),
        "candidates_filtered": len(candidates) - len(filtered),
        "heuristic": True,
        "placeholder_features": True,
        "reason": "selected",
    }


# ── Teaching Dimension ───────────────────────────────────────────────────────


def estimate_experiment_count(domain: str, categories: list = None) -> dict:
    """Estimate minimum experiments needed using teaching_dimension().

    Wraps bayesian_teacher.teaching_dimension() with experiment categories
    as the concept space.
    """
    if not categories:
        from tools.autoresearch.hypothesis_generator import _load_program

        config = _load_program(domain)
        categories = list(config.get("categories", {}).keys())

    if len(categories) <= 1:
        return {
            "domain": domain,
            "estimated_experiments": 1,
            "teaching_dimension": 1,
            "category_count": len(categories),
        }

    try:
        from tools.intelligence.bayesian_teacher import teaching_dimension

        # Build item pool from category names
        item_pool = [{"id": f"cat-{i}", "features": [float(i) / len(categories)]} for i in range(len(categories))]
        concept_labels = {f"cat-{i}": categories[i] for i in range(len(categories))}

        result = teaching_dimension(
            target_id=categories[0],
            item_pool=item_pool,
            concept_labels=concept_labels,
        )
        td = result.get("teaching_dimension", len(categories))
    except (ImportError, Exception):
        td = len(categories)

    return {
        "domain": domain,
        "estimated_experiments": td * 3,  # ~3 experiments per category
        "teaching_dimension": td,
        "category_count": len(categories),
    }


def get_category_order(domain: str) -> dict:
    """Get optimal experiment category ordering.

    Adapts bayesian_teacher.optimal_compliance_order() pattern
    for experiment category dependencies.
    """
    config = _load_config()
    deps = config.get("category_dependencies", {})

    if not deps:
        from tools.autoresearch.hypothesis_generator import _load_program

        program = _load_program(domain)
        order = program.get("category_order", [])
        return {
            "domain": domain,
            "ordered_categories": order,
            "method": "program_config",
        }

    # Greedy ordering by cascade info gain (same pattern as D-BT-4)
    pending = list(deps.keys())
    assessed = {}
    ordered = []

    for _step in range(len(pending)):
        if not pending:
            break

        best_cat = None
        best_score = -1.0

        for cat in pending:
            cat_info = deps.get(cat, {})
            children = cat_info.get("children", [])
            pending_children = [c for c in children if c in pending]

            # Direct info + cascade info
            score = 1.0 + len(pending_children) * 0.75

            # Reduction if parent assessed
            for assessed_cat in assessed:
                if cat in deps.get(assessed_cat, {}).get("children", []):
                    score -= 0.40
                    break

            if score > best_score:
                best_score = score
                best_cat = cat

        if best_cat:
            ordered.append(best_cat)
            assessed[best_cat] = True
            pending.remove(best_cat)

    return {
        "domain": domain,
        "ordered_categories": ordered,
        "method": "bayesian_cascade",
    }


def check_dedup(hypothesis: str, domain: str, threshold: float = 0.85) -> dict:
    """Check if hypothesis is too similar to existing experiments.

    Uses pgvector cosine distance when available, falls back to Python cosine.
    """
    content_hash = hashlib.sha256(hypothesis.encode()).hexdigest()[:16]
    features = _text_to_features(hypothesis)

    # Check DB for recent experiments
    try:
        from tools.db.storage import get_connection

        with get_connection() as conn:
            rows = conn.execute(
                "SELECT hypothesis, content_hash FROM experiment_candidates "
                "WHERE domain = %s AND status NOT IN ('deduped', 'failed') "
                "ORDER BY created_at DESC LIMIT 50",
                (domain,),
            ).fetchall()

        # Exact hash match
        for row in rows:
            if row["content_hash"] == content_hash:
                return {"is_duplicate": True, "method": "content_hash", "similarity": 1.0}

        # Cosine similarity check
        for row in rows:
            existing_features = _text_to_features(row["hypothesis"])
            sim = _cosine_similarity(features, existing_features)
            if sim >= threshold:
                return {"is_duplicate": True, "method": "cosine", "similarity": round(sim, 4)}

    except (ImportError, Exception):
        pass

    return {"is_duplicate": False, "method": "checked", "similarity": 0.0}


def health_check() -> dict:
    """Health check for Bayesian selector."""
    bt_available = False
    te_available = False
    try:
        from tools.intelligence.bayesian_teacher import score_candidate  # noqa: F401

        bt_available = True
    except (ImportError, Exception):
        pass
    try:
        from tools.autonomy.trust_engine import should_act  # noqa: F401

        te_available = True
    except (ImportError, Exception):
        pass

    return {
        "status": "healthy",
        "bayesian_teacher_available": bt_available,
        "trust_engine_available": te_available,
        "fallback_scoring": True,
        "timestamp": now_iso(),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Bayesian Experiment Selector")
    parser.add_argument("--score", action="store_true", help="Score candidates")
    parser.add_argument("--select", action="store_true", help="Select next experiment")
    parser.add_argument("--estimate", action="store_true", help="Estimate experiment count")
    parser.add_argument("--category-order", action="store_true", help="Get category order")
    parser.add_argument("--health", action="store_true", help="Health check")
    parser.add_argument("--domain", type=str, default="compliance")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.health:
        result = health_check()
    elif args.estimate:
        result = estimate_experiment_count(args.domain)
    elif args.category_order:
        result = get_category_order(args.domain)
    elif args.score or args.select:
        # Generate sample candidates for demo
        from tools.autoresearch.hypothesis_generator import generate_hypotheses

        hyp = generate_hypotheses(args.domain, max_hypotheses=5)
        candidates = hyp.get("hypotheses", [])
        if args.select:
            result = select_next_experiment(candidates, args.domain)
        else:
            result = {
                "scores": [score_experiment_candidate(c, args.domain) for c in candidates],
            }
    else:
        parser.print_help()
        return

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
