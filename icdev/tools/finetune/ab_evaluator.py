#!/usr/bin/env python3
# CUI // SP-CTI
"""A/B model evaluator — paired comparison with statistical significance (D-FT-15).

Runs the same test set through two models, computes metrics for both,
and uses paired t-test (stdlib statistics) to determine statistical significance.

Usage:
    python tools/finetune/ab_evaluator.py --compare --model-a "mv-xxx" --model-b "mv-yyy" --test-set /path/test.jsonl --json  # noqa: E501
    python tools/finetune/ab_evaluator.py --list --json
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import uuid
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.finetune.evaluator import (
    compute_bleu,
    compute_rouge_l,
    _load_test_data,
    _generate_predictions,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def _get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    conn = get_connection(db_path=str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _paired_t_test(a: List[float], b: List[float]) -> float:
    """Paired t-test p-value (stdlib only, D-FT-15).

    Returns p-value estimate. Lower = more significant.
    Uses two-tailed t-distribution approximation.
    """
    if len(a) != len(b) or len(a) < 2:
        return 1.0

    diffs = [ai - bi for ai, bi in zip(a, b)]
    n = len(diffs)
    mean_diff = statistics.mean(diffs)

    try:
        std_diff = statistics.stdev(diffs)
    except statistics.StatisticsError:
        return 1.0

    if std_diff == 0:
        return 0.0 if mean_diff != 0 else 1.0

    t_stat = mean_diff / (std_diff / math.sqrt(n))
    df = n - 1

    # Approximate p-value using normal distribution for large samples
    # For small samples, use a rough t-distribution approximation
    if df >= 30:
        # Normal approximation
        p = 2 * (1 - _normal_cdf(abs(t_stat)))
    else:
        # Rough t-distribution approximation
        p = 2 * (1 - _t_cdf(abs(t_stat), df))

    return max(0.0, min(1.0, p))


def _normal_cdf(x: float) -> float:
    """Standard normal CDF approximation (Abramowitz & Stegun)."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _t_cdf(t: float, df: int) -> float:
    """Rough t-distribution CDF via normal approximation with df correction."""
    # For df >= 3, approximate t as normal with adjusted variance
    if df < 1:
        return 0.5
    # Cornish-Fisher approximation
    g2 = 1 / (4 * max(df, 1))
    z = t * (1 - g2)
    return _normal_cdf(z)


def compare_models(
    model_a_id: str,
    model_b_id: str,
    test_set_path: str = "",
    dataset_id: str = "",
    test_split_pct: float = 0.2,
    classification: str = "CUI",
    tenant_id: str = "",
    project_id: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run A/B comparison between two model versions.

    Returns per-metric scores, paired t-test significance, and winner.
    """
    conn = _get_db(db_path)
    try:
        mv_a = conn.execute("SELECT * FROM ft_model_versions WHERE id = %s", (model_a_id,)).fetchone()
        mv_b = conn.execute("SELECT * FROM ft_model_versions WHERE id = %s", (model_b_id,)).fetchone()

        if not mv_a:
            return {"success": False, "error": f"Model A not found: {model_a_id}"}
        if not mv_b:
            return {"success": False, "error": f"Model B not found: {model_b_id}"}

        model_a_name = mv_a["ollama_model_name"] or mv_a["model_name"]
        model_b_name = mv_b["ollama_model_name"] or mv_b["model_name"]
    finally:
        conn.close()

    # Load test data
    test_pairs = _load_test_data(test_set_path, dataset_id, test_split_pct, db_path)
    if not test_pairs:
        return {"success": False, "error": "No test data available"}

    # Generate predictions from both models
    preds_a = _generate_predictions(model_a_name, test_pairs)
    preds_b = _generate_predictions(model_b_name, test_pairs)

    # Compute per-example metrics
    bleu_a, bleu_b = [], []
    rouge_a, rouge_b = [], []

    for pair, pa, pb in zip(test_pairs, preds_a, preds_b):
        ref = pair["expected_output"]
        bleu_a.append(compute_bleu(ref, pa or ""))
        bleu_b.append(compute_bleu(ref, pb or ""))
        rouge_a.append(compute_rouge_l(ref, pa or ""))
        rouge_b.append(compute_rouge_l(ref, pb or ""))

    # Compute averages
    avg_bleu_a = statistics.mean(bleu_a) if bleu_a else 0.0
    avg_bleu_b = statistics.mean(bleu_b) if bleu_b else 0.0
    avg_rouge_a = statistics.mean(rouge_a) if rouge_a else 0.0
    avg_rouge_b = statistics.mean(rouge_b) if rouge_b else 0.0

    # Paired t-tests
    bleu_p = _paired_t_test(bleu_a, bleu_b)
    rouge_p = _paired_t_test(rouge_a, rouge_b)

    # Determine winner
    a_wins = 0
    b_wins = 0
    if avg_bleu_a > avg_bleu_b:
        a_wins += 1
    elif avg_bleu_b > avg_bleu_a:
        b_wins += 1
    if avg_rouge_a > avg_rouge_b:
        a_wins += 1
    elif avg_rouge_b > avg_rouge_a:
        b_wins += 1

    if a_wins > b_wins:
        winner = "model_a"
    elif b_wins > a_wins:
        winner = "model_b"
    else:
        winner = "tie"

    significant = bleu_p < 0.05 or rouge_p < 0.05

    # Store comparison evaluation (append-only)
    eval_id = f"eval-{uuid.uuid4().hex[:12]}"
    conn = _get_db(db_path)
    try:
        comparison_scores = {
            "model_a": {"bleu": round(avg_bleu_a, 4), "rouge_l": round(avg_rouge_a, 4)},
            "model_b": {"bleu": round(avg_bleu_b, 4), "rouge_l": round(avg_rouge_b, 4)},
        }
        conn.execute(
            """INSERT INTO ft_evaluations
               (id, model_version_id, eval_type, test_set_size,
                bleu_score, rouge_l_score,
                comparison_model, comparison_scores,
                statistical_significance, pass_threshold,
                classification, tenant_id, project_id, evaluated_at)
               VALUES (%s, %s, 'ab_comparison', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                eval_id,
                model_a_id,
                len(test_pairs),
                avg_bleu_a,
                avg_rouge_a,
                model_b_id,
                json.dumps(comparison_scores),
                min(bleu_p, rouge_p),
                1 if significant else 0,
                classification,
                tenant_id,
                project_id,
                _now(),
            ),
        )
        conn.commit()
    except Exception as e:
        return {"success": False, "error": f"Failed to store comparison: {e}"}
    finally:
        conn.close()

    return {
        "success": True,
        "eval_id": eval_id,
        "test_set_size": len(test_pairs),
        "model_a": {
            "id": model_a_id,
            "name": model_a_name,
            "avg_bleu": round(avg_bleu_a, 4),
            "avg_rouge_l": round(avg_rouge_a, 4),
        },
        "model_b": {
            "id": model_b_id,
            "name": model_b_name,
            "avg_bleu": round(avg_bleu_b, 4),
            "avg_rouge_l": round(avg_rouge_b, 4),
        },
        "statistical_significance": {
            "bleu_p_value": round(bleu_p, 4),
            "rouge_l_p_value": round(rouge_p, 4),
            "significant_at_005": significant,
        },
        "winner": winner,
    }


# -- CLI -----------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Fine-tuning A/B model evaluator")
    parser.add_argument("--compare", action="store_true", help="Compare two models")
    parser.add_argument("--model-a", type=str, default="")
    parser.add_argument("--model-b", type=str, default="")
    parser.add_argument("--test-set", type=str, default="")
    parser.add_argument("--dataset-id", type=str, default="")
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.compare and args.model_a and args.model_b:
        result = compare_models(
            model_a_id=args.model_a,
            model_b_id=args.model_b,
            test_set_path=args.test_set,
            dataset_id=args.dataset_id,
        )
    else:
        result = {"success": False, "error": "Specify --compare --model-a <id> --model-b <id>"}

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
