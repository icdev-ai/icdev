#!/usr/bin/env python3
# CUI // SP-CTI
"""Fitness Evaluator — Proposal Quality Domain (D-AR-7).

Evaluates proposal quality fitness by querying recent quality scores
and win/loss records from the GovProposal tables.  Deterministic,
zero LLM tokens, air-gap safe.

Score formula:
    fitness = quality_avg * 0.60 + win_rate * 0.40

Where:
    quality_avg = average composite_score (normalised 0-1) from
                  pg_proposal_quality_scores over the last 30 days
    win_rate    = wins / total from pg_win_loss_records over
                  the last 30 days

Returns 0.5 baseline when DB has no data (graceful degradation).

Usage:
    python tools/autoresearch/fitness_proposal_quality.py --json
    python tools/autoresearch/fitness_proposal_quality.py --project-id sparkpilot --json
"""

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.common.helpers import now_iso


def evaluate(project_id: str = None, **kwargs) -> Dict[str, Any]:
    """Evaluate proposal quality fitness.

    Queries pg_proposal_quality_scores for recent composite scores
    and pg_win_loss_records for win/loss correlation.  Returns a
    normalised [0, 1] fitness score.

    Args:
        project_id: Optional project ID filter (unused currently,
                     kept for interface consistency).
        **kwargs: Ignored — interface compatibility with EVALUATORS.

    Returns:
        Dict with score, quality_metrics, win_metrics, sample_size.
    """
    try:
        from tools.db.storage import get_connection
    except Exception as exc:
        return _baseline_result(error=f"storage import failed: {exc}")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    try:
        with get_connection() as conn:
            # ── Quality metrics ──────────────────────────────────────
            quality_rows = _safe_query(
                conn,
                "SELECT composite_score, grammar_score, readability_score, "
                "tone_score FROM pg_proposal_quality_scores "
                "WHERE created_at >= %s",
                (cutoff,),
            )

            if quality_rows:
                composites = [_to_float(r["composite_score"]) for r in quality_rows]
                quality_avg = sum(composites) / len(composites)
                # Normalise: scores may be 0-100 or 0-1
                if quality_avg > 1.0:
                    quality_avg = quality_avg / 100.0
                quality_avg = min(1.0, max(0.0, quality_avg))

                grammar_avg = _avg_field(quality_rows, "grammar_score")
                readability_avg = _avg_field(quality_rows, "readability_score")
                tone_avg = _avg_field(quality_rows, "tone_score")
                quality_sample = len(quality_rows)
            else:
                quality_avg = 0.5  # baseline
                grammar_avg = 0.0
                readability_avg = 0.0
                tone_avg = 0.0
                quality_sample = 0

            # ── Win/loss metrics ─────────────────────────────────────
            win_rows = _safe_query(
                conn,
                "SELECT outcome FROM pg_win_loss_records WHERE created_at >= %s",
                (cutoff,),
            )

            if win_rows:
                wins = sum(1 for r in win_rows if str(r["outcome"]).lower() in ("win", "won", "1", "true"))
                total_wl = len(win_rows)
                win_rate = wins / max(total_wl, 1)
                win_sample = total_wl
            else:
                win_rate = 0.5  # baseline
                wins = 0
                total_wl = 0
                win_sample = 0

        # ── Composite score ──────────────────────────────────────────
        score = quality_avg * 0.60 + win_rate * 0.40
        score = round(min(1.0, max(0.0, score)), 4)

        return {
            "domain": "proposal_quality",
            "metric_name": "proposal_quality_score",
            "metric_value": score,
            "score": score,
            "success": True,
            "quality_metrics": {
                "composite_avg": round(quality_avg, 4),
                "grammar_avg": round(grammar_avg, 4),
                "readability_avg": round(readability_avg, 4),
                "tone_avg": round(tone_avg, 4),
            },
            "win_metrics": {
                "win_rate": round(win_rate, 4),
                "wins": wins,
                "total": total_wl,
            },
            "sample_size": quality_sample + win_sample,
            "timestamp": now_iso(),
        }

    except Exception as exc:
        return _baseline_result(error=str(exc)[:200])


# ── Helpers ──────────────────────────────────────────────────────────────────


def _safe_query(conn, sql: str, params: tuple = ()) -> list:
    """Execute query and return rows, returning [] on any error."""
    try:
        cursor = conn.execute(sql, params)
        return cursor.fetchall()
    except Exception:
        return []


def _to_float(value) -> float:
    """Convert a value to float, defaulting to 0.0."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _avg_field(rows: list, field: str) -> float:
    """Compute average of a field across rows, normalising 0-100 to 0-1."""
    values = [_to_float(r[field]) for r in rows if r.get(field) is not None]
    if not values:
        return 0.0
    avg = sum(values) / len(values)
    if avg > 1.0:
        avg = avg / 100.0
    return min(1.0, max(0.0, avg))


def _baseline_result(error: str = None) -> Dict[str, Any]:
    """Return a 0.5 baseline when data is unavailable."""
    result = {
        "domain": "proposal_quality",
        "metric_name": "proposal_quality_score",
        "metric_value": 0.5,
        "score": 0.5,
        "success": error is None,
        "quality_metrics": {
            "composite_avg": 0.0,
            "grammar_avg": 0.0,
            "readability_avg": 0.0,
            "tone_avg": 0.0,
        },
        "win_metrics": {
            "win_rate": 0.0,
            "wins": 0,
            "total": 0,
        },
        "sample_size": 0,
        "timestamp": now_iso(),
    }
    if error:
        result["error"] = error
    return result


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Fitness Evaluator — Proposal Quality Domain")
    parser.add_argument(
        "--project-id",
        type=str,
        default=None,
        help="Optional project ID filter",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON output",
    )
    args = parser.parse_args()

    result = evaluate(project_id=args.project_id)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
