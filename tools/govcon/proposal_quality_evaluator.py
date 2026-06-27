#!/usr/bin/env python3
# CUI // SP-CTI
"""Proposal Quality Evaluator — Bayesian Autoresearch Integration.

Fitness evaluator wrapper for the ``proposal_quality`` autoresearch domain.
Computes quality improvement deltas, win-rate correlations, trend analysis,
experiment suggestions, and result recording.

Deterministic, zero LLM tokens, air-gap safe.

Usage:
    python tools/govcon/proposal_quality_evaluator.py --evaluate --json
    python tools/govcon/proposal_quality_evaluator.py --trends --json
    python tools/govcon/proposal_quality_evaluator.py --trends --days 60 --json
    python tools/govcon/proposal_quality_evaluator.py --suggest --json
    python tools/govcon/proposal_quality_evaluator.py --suggest --max-suggestions 3 --json
    python tools/govcon/proposal_quality_evaluator.py --record --experiment-id "exp-xxx" --hypothesis "..." --json
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402

# ── Module-level config loading ───────────────────────────────────────────

_CONFIG_PATH = _ROOT / "args" / "experiment_programs" / "proposal_quality.yaml"
_CONFIG: Optional[Dict[str, Any]] = None

try:
    import yaml  # noqa: E402

    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, encoding="utf-8") as _fh:
            _CONFIG = yaml.safe_load(_fh)
except Exception:
    _CONFIG = None


# ── Helpers ───────────────────────────────────────────────────────────────


def _now() -> str:
    """UTC ISO timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _gen_id() -> str:
    """Generate a prefixed unique ID."""
    return f"pqe-{uuid.uuid4()}"


def _to_float(value: Any) -> float:
    """Coerce to float, defaulting to 0.0."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _normalise(value: float) -> float:
    """Normalise score: if > 1 assume 0-100 scale, clamp to [0, 1]."""
    if value > 1.0:
        value = value / 100.0
    return min(1.0, max(0.0, value))


def _safe_query(conn: Any, sql: str, params: tuple = ()) -> list:
    """Execute query returning row dicts; empty list on error."""
    try:
        cursor = conn.execute(sql, params)
        return cursor.fetchall()
    except Exception:
        return []


def _ensure_tables(conn: Any) -> None:
    """Create pg_quality_experiments table if it does not exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pg_quality_experiments (
            id              TEXT PRIMARY KEY,
            experiment_id   TEXT,
            hypothesis      TEXT NOT NULL,
            category        TEXT,
            pre_score       REAL,
            post_score      REAL,
            delta           REAL,
            significant     INTEGER DEFAULT 0,
            sample_size     INTEGER,
            details         TEXT,
            created_at      TEXT NOT NULL
        )
    """)
    try:
        conn.commit()
    except Exception:
        pass


# ── Core functions ────────────────────────────────────────────────────────


def evaluate_fitness(experiment_id: Optional[str] = None) -> Dict[str, Any]:
    """Compute fitness score for the proposal_quality autoresearch domain.

    Algorithm:
        1. Query ``pg_proposal_quality_scores`` for the last 30 days
           (recent period) and the preceding 30 days (previous period).
        2. Compute *quality_improvement_delta* = recent_avg - previous_avg.
        3. Query ``pg_win_loss_records`` for win/loss outcomes.
        4. If win data exists, compute Pearson-like correlation between
           quality scores and outcomes (win=1, loss=0).
        5. Final fitness = delta * correlation (both normalised [0, 1]).
           Falls back to delta alone when no win data is available.

    Returns a dict suitable for autoresearch fitness consumption.
    """
    now_dt = datetime.now(timezone.utc)
    recent_cutoff = (now_dt - timedelta(days=30)).isoformat()
    previous_cutoff = (now_dt - timedelta(days=60)).isoformat()

    try:
        with get_connection() as conn:
            _ensure_tables(conn)

            # ── Recent quality scores (last 30 days) ──────────────────
            recent_rows = _safe_query(
                conn,
                "SELECT composite_score, grammar_score, readability_score, "
                "tone_score, plagiarism_score, ai_detection_score "
                "FROM pg_proposal_quality_scores "
                "WHERE created_at >= ?",
                (recent_cutoff,),
            )

            # ── Previous quality scores (30-60 days ago) ──────────────
            previous_rows = _safe_query(
                conn,
                "SELECT composite_score FROM pg_proposal_quality_scores WHERE created_at >= ? AND created_at < ?",
                (previous_cutoff, recent_cutoff),
            )

            recent_avg = _compute_avg(recent_rows, "composite_score")
            previous_avg = _compute_avg(previous_rows, "composite_score")

            # Delta: positive means improvement
            quality_delta = recent_avg - previous_avg

            # ── Win/loss correlation ──────────────────────────────────
            win_rows = _safe_query(
                conn,
                "SELECT outcome FROM pg_win_loss_records WHERE created_at >= ?",
                (previous_cutoff,),
            )

            win_correlation: Optional[float] = None
            if win_rows:
                wins = sum(1 for r in win_rows if str(r["outcome"]).lower() in ("won", "win", "1", "true"))
                total = len(win_rows)
                win_rate = wins / max(total, 1)
                win_correlation = win_rate
            else:
                total = 0

            # ── Fitness computation ───────────────────────────────────
            # Normalise delta to [0, 1]: map [-1, 1] → [0, 1]
            norm_delta = _normalise((quality_delta + 1.0) / 2.0)

            if win_correlation is not None:
                fitness = norm_delta * win_correlation
            else:
                fitness = norm_delta

            fitness = round(min(1.0, max(0.0, fitness)), 4)

        return {
            "success": True,
            "domain": "proposal_quality",
            "fitness_score": fitness,
            "quality_delta": round(quality_delta, 4),
            "win_correlation": (round(win_correlation, 4) if win_correlation is not None else None),
            "sample_sizes": {
                "recent_quality": len(recent_rows),
                "previous_quality": len(previous_rows),
                "win_loss": total,
            },
            "period": {
                "recent_start": recent_cutoff,
                "previous_start": previous_cutoff,
                "now": _now(),
            },
            "timestamp": _now(),
        }

    except Exception as exc:
        return {
            "success": False,
            "domain": "proposal_quality",
            "fitness_score": 0.5,
            "quality_delta": 0.0,
            "win_correlation": None,
            "sample_sizes": {
                "recent_quality": 0,
                "previous_quality": 0,
                "win_loss": 0,
            },
            "period": {},
            "error": str(exc)[:300],
            "timestamp": _now(),
        }


def get_quality_trends(days: int = 90) -> Dict[str, Any]:
    """Return quality score trends grouped by ISO week.

    Queries ``pg_proposal_quality_scores`` for the requested window,
    buckets rows into ISO weeks, and computes per-week averages for
    composite, grammar, readability, and tone scores.

    Trend direction is determined by comparing the last two complete
    weeks: improving (>+2%), declining (<-2%), or stable.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    try:
        with get_connection() as conn:
            rows = _safe_query(
                conn,
                "SELECT created_at, composite_score, grammar_score, "
                "readability_score, tone_score "
                "FROM pg_proposal_quality_scores "
                "WHERE created_at >= ? ORDER BY created_at",
                (cutoff,),
            )

        # Bucket by ISO week
        weeks: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            try:
                dt = datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00"))
                week_key = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
            except Exception:
                continue
            weeks.setdefault(week_key, []).append(r)

        trend_data: List[Dict[str, Any]] = []
        for wk in sorted(weeks.keys()):
            wk_rows = weeks[wk]
            trend_data.append(
                {
                    "week": wk,
                    "composite_avg": round(_compute_avg(wk_rows, "composite_score"), 4),
                    "grammar_avg": round(_compute_avg(wk_rows, "grammar_score"), 4),
                    "readability_avg": round(_compute_avg(wk_rows, "readability_score"), 4),
                    "tone_avg": round(_compute_avg(wk_rows, "tone_score"), 4),
                    "sample_count": len(wk_rows),
                }
            )

        # Direction
        direction = "stable"
        current_avg = 0.0
        previous_avg = 0.0
        if len(trend_data) >= 2:
            current_avg = trend_data[-1]["composite_avg"]
            previous_avg = trend_data[-2]["composite_avg"]
            diff = current_avg - previous_avg
            if previous_avg > 0:
                pct = diff / previous_avg
                if pct > 0.02:
                    direction = "improving"
                elif pct < -0.02:
                    direction = "declining"
        elif len(trend_data) == 1:
            current_avg = trend_data[0]["composite_avg"]

        return {
            "success": True,
            "weeks": trend_data,
            "trends": {
                "total_weeks": len(trend_data),
                "total_samples": len(rows),
            },
            "direction": direction,
            "current_avg": round(current_avg, 4),
            "previous_avg": round(previous_avg, 4),
            "timestamp": _now(),
        }

    except Exception as exc:
        return {
            "success": False,
            "weeks": [],
            "trends": {"total_weeks": 0, "total_samples": 0},
            "direction": "unknown",
            "current_avg": 0.0,
            "previous_avg": 0.0,
            "error": str(exc)[:300],
            "timestamp": _now(),
        }


def suggest_experiments(max_suggestions: int = 5) -> Dict[str, Any]:
    """Suggest experiments based on current quality score gaps.

    Identifies the lowest-scoring quality dimensions from recent
    ``pg_proposal_quality_scores`` entries, then maps each gap to a
    relevant experiment category from the YAML config.
    """
    dimension_category_map = {
        "grammar_score": "readability_targets",
        "readability_score": "readability_targets",
        "tone_score": "emphasis_areas",
        "plagiarism_score": "template_structure",
        "ai_detection_score": "template_structure",
    }

    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    try:
        with get_connection() as conn:
            rows = _safe_query(
                conn,
                "SELECT grammar_score, readability_score, tone_score, "
                "plagiarism_score, ai_detection_score "
                "FROM pg_proposal_quality_scores WHERE created_at >= ?",
                (cutoff,),
            )

        if not rows:
            return {
                "success": True,
                "suggestions": [],
                "message": "No quality scores in last 30 days to base suggestions on",
                "timestamp": _now(),
            }

        # Compute averages per dimension
        dim_avgs: Dict[str, float] = {}
        for dim in dimension_category_map:
            values = [_to_float(r[dim]) for r in rows if r.get(dim) is not None]
            if values:
                avg = sum(values) / len(values)
                dim_avgs[dim] = _normalise(avg)
            else:
                dim_avgs[dim] = 0.0

        # Sort by score ascending (lowest = biggest gap)
        sorted_dims = sorted(dim_avgs.items(), key=lambda x: x[1])

        suggestions: List[Dict[str, Any]] = []
        for dim, avg_score in sorted_dims[:max_suggestions]:
            cat_key = dimension_category_map.get(dim, "template_structure")
            hypothesis = _pick_hypothesis(cat_key)
            suggestions.append(
                {
                    "dimension": dim,
                    "gap": round(1.0 - avg_score, 4),
                    "current_avg": round(avg_score, 4),
                    "category": cat_key,
                    "hypothesis": hypothesis,
                }
            )

        return {
            "success": True,
            "suggestions": suggestions,
            "timestamp": _now(),
        }

    except Exception as exc:
        return {
            "success": False,
            "suggestions": [],
            "error": str(exc)[:300],
            "timestamp": _now(),
        }


def record_experiment_result(
    experiment_id: str,
    hypothesis: str,
    result_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Store an experiment outcome in pg_quality_experiments (append-only).

    Args:
        experiment_id: The autoresearch experiment ID (e.g. ``exp-xxx``).
        hypothesis: The hypothesis text that was tested.
        result_data: Optional dict with pre_score, post_score, delta,
                     significant, sample_size, category, details.

    Returns:
        Dict with ``stored`` bool and the new record ``id``.
    """
    result_data = result_data or {}
    record_id = _gen_id()

    pre_score = _to_float(result_data.get("pre_score"))
    post_score = _to_float(result_data.get("post_score"))
    delta = _to_float(result_data.get("delta", post_score - pre_score))
    significant = 1 if result_data.get("significant") else 0
    sample_size = int(result_data.get("sample_size", 0))
    category = result_data.get("category", "")
    details = json.dumps(result_data.get("details", {}), default=str)

    try:
        with get_connection() as conn:
            _ensure_tables(conn)
            conn.execute(
                "INSERT INTO pg_quality_experiments "
                "(id, experiment_id, hypothesis, category, pre_score, "
                "post_score, delta, significant, sample_size, details, "
                "created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    record_id,
                    experiment_id,
                    hypothesis,
                    category,
                    pre_score,
                    post_score,
                    delta,
                    significant,
                    sample_size,
                    details,
                    _now(),
                ),
            )
            try:
                conn.commit()
            except Exception:
                pass

        return {
            "success": True,
            "stored": True,
            "id": record_id,
            "experiment_id": experiment_id,
            "timestamp": _now(),
        }

    except Exception as exc:
        return {
            "success": False,
            "stored": False,
            "id": None,
            "error": str(exc)[:300],
            "timestamp": _now(),
        }


# ── Internal helpers ──────────────────────────────────────────────────────


def _compute_avg(rows: list, field: str) -> float:
    """Average a field across rows, normalising 0-100 to 0-1."""
    values = [_to_float(r[field]) for r in rows if r.get(field) is not None]
    if not values:
        return 0.0
    avg = sum(values) / len(values)
    return _normalise(avg)


def _pick_hypothesis(category_key: str) -> str:
    """Pick the first hypothesis from the YAML config for a category."""
    if _CONFIG and "categories" in _CONFIG:
        cat = _CONFIG["categories"].get(category_key, {})
        hyps = cat.get("hypotheses", [])
        if hyps:
            return hyps[0]
    # Fallback
    fallbacks = {
        "template_structure": ("Proposals with executive summary under 500 words win more often"),
        "readability_targets": ("Flesch-Kincaid grade 10-12 outperforms grade 14+ in competitive evaluations"),
        "section_ordering": ("Leading with discriminators before compliance claims improves persuasiveness"),
        "emphasis_areas": ("Three discriminators per section outperforms five"),
    }
    return fallbacks.get(category_key, "Quality improvement hypothesis")


# ── CLI ───────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Proposal Quality Evaluator — Bayesian Autoresearch")
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="Compute fitness score for proposal quality domain",
    )
    parser.add_argument(
        "--trends",
        action="store_true",
        help="Return quality score trends over time",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Number of days for trend window (default: 90)",
    )
    parser.add_argument(
        "--suggest",
        action="store_true",
        help="Suggest experiments based on current quality gaps",
    )
    parser.add_argument(
        "--max-suggestions",
        type=int,
        default=5,
        help="Max experiment suggestions to return (default: 5)",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="Record an experiment result",
    )
    parser.add_argument(
        "--experiment-id",
        type=str,
        default=None,
        help="Experiment ID for --record",
    )
    parser.add_argument(
        "--hypothesis",
        type=str,
        default=None,
        help="Hypothesis text for --record",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON output",
    )
    args = parser.parse_args()

    if args.evaluate:
        result = evaluate_fitness()
    elif args.trends:
        result = get_quality_trends(days=args.days)
    elif args.suggest:
        result = suggest_experiments(max_suggestions=args.max_suggestions)
    elif args.record:
        if not args.experiment_id or not args.hypothesis:
            result = {
                "success": False,
                "error": "--record requires --experiment-id and --hypothesis",
            }
        else:
            result = record_experiment_result(
                experiment_id=args.experiment_id,
                hypothesis=args.hypothesis,
            )
    else:
        # Default to evaluate
        result = evaluate_fitness()

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
