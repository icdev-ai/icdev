# CUI // SP-CTI
"""WriteGuard diff scorer (WG 20) — compare two analyses.

Deterministic diff of two WriteGuard quality analyses. No LLM calls.

CLI:
    python -m tools.writing.diff_scorer --old old.txt --new new.txt --json
    python -m tools.writing.diff_scorer --old-id <uuid> --new-id <uuid> --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def _analyze(text: str) -> Dict[str, Any]:
    from tools.pulse.writeguard import run_full_quality_check
    return run_full_quality_check(text)


def _word_diff(old_text: str, new_text: str) -> Dict[str, int]:
    old_words = old_text.split()
    new_words = new_text.split()
    old_set = set(w.lower() for w in old_words)
    new_set = set(w.lower() for w in new_words)
    added = len(new_set - old_set)
    removed = len(old_set - new_set)
    return {
        "added": added,
        "removed": removed,
        "net": len(new_words) - len(old_words),
    }


def _dim_score(detail: Any) -> float:
    if isinstance(detail, dict):
        s = detail.get("score", detail.get("overall_score"))
        if isinstance(s, (int, float)):
            return float(s)
    return 0.0


def _extract_dim_scores(analysis: Dict[str, Any]) -> Dict[str, float]:
    scores: Dict[str, float] = {}
    details = analysis.get("details", {}) or {}
    for dim, val in details.items():
        scores[dim] = _dim_score(val)
    return scores


def diff_analyses(old_text: str, new_text: str) -> Dict[str, Any]:
    """Compare two texts via WriteGuard analysis."""
    old_a = _analyze(old_text)
    new_a = _analyze(new_text)

    old_overall = float(old_a.get("overall_score", 0.0))
    new_overall = float(new_a.get("overall_score", 0.0))

    old_dims = _extract_dim_scores(old_a)
    new_dims = _extract_dim_scores(new_a)

    all_dims = set(old_dims) | set(new_dims)
    dimension_deltas: Dict[str, Dict[str, float]] = {}
    improvements: List[str] = []
    regressions: List[str] = []

    for dim in sorted(all_dims):
        o = old_dims.get(dim, 0.0)
        n = new_dims.get(dim, 0.0)
        delta = round(n - o, 2)
        dimension_deltas[dim] = {"old": round(o, 2), "new": round(n, 2), "delta": delta}
        if delta > 2:
            improvements.append(f"{dim}: +{delta:.1f}")
        elif delta < -2:
            regressions.append(f"{dim}: {delta:.1f}")

    overall_delta = round(new_overall - old_overall, 2)
    if overall_delta > 2:
        summary = f"Improved (+{overall_delta:.1f})"
    elif overall_delta < -2:
        summary = f"Regressed ({overall_delta:.1f})"
    else:
        summary = f"Unchanged ({overall_delta:+.1f})"

    return {
        "word_diff": _word_diff(old_text, new_text),
        "score_diff": {
            "overall_old": round(old_overall, 2),
            "overall_new": round(new_overall, 2),
            "delta": overall_delta,
        },
        "dimension_deltas": dimension_deltas,
        "improvements": improvements,
        "regressions": regressions,
        "net_change": summary,
    }


def _fetch_text_by_id(analysis_id: str) -> str:
    """Fetch the original input text referenced by a wg_analysis_results row.

    Since the table stores only the hash, we attempt to retrieve a stored raw
    text from a sibling table if present, else return an empty marker. The diff
    still proceeds using stored scores when possible.
    """
    from tools.db.storage import get_connection
    with get_connection() as conn:
        row = conn.execute(
            "SELECT input_text_hash, overall_quality_score, document_name "
            "FROM wg_analysis_results WHERE id = ?",
            (analysis_id,),
        ).fetchone()
    if not row:
        raise ValueError(f"analysis id not found: {analysis_id}")
    return ""


def _fetch_scores_by_id(analysis_id: str) -> Dict[str, Any]:
    from tools.db.storage import get_connection
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, overall_quality_score, readability_flesch, "
            "readability_gunning_fog, grammar_error_count, passive_voice_pct, "
            "avg_sentence_length, coherence_score, plagiarism_max_similarity, "
            "ai_content_score FROM wg_analysis_results WHERE id = ?",
            (analysis_id,),
        ).fetchone()
    if not row:
        raise ValueError(f"analysis id not found: {analysis_id}")
    try:
        d = dict(row)
    except Exception:
        keys = [
            "id", "overall_quality_score", "readability_flesch",
            "readability_gunning_fog", "grammar_error_count",
            "passive_voice_pct", "avg_sentence_length", "coherence_score",
            "plagiarism_max_similarity", "ai_content_score",
        ]
        d = {k: row[i] for i, k in enumerate(keys)}
    return {
        "overall_score": float(d.get("overall_quality_score") or 0.0),
        "details": {
            "readability_flesch": {"score": float(d.get("readability_flesch") or 0.0)},
            "readability_gunning_fog": {"score": float(d.get("readability_gunning_fog") or 0.0)},
            "grammar_error_count": {"score": float(d.get("grammar_error_count") or 0.0)},
            "passive_voice_pct": {"score": float(d.get("passive_voice_pct") or 0.0)},
            "avg_sentence_length": {"score": float(d.get("avg_sentence_length") or 0.0)},
            "coherence_score": {"score": float(d.get("coherence_score") or 0.0)},
            "plagiarism_max_similarity": {"score": float(d.get("plagiarism_max_similarity") or 0.0)},
            "ai_content_score": {"score": float(d.get("ai_content_score") or 0.0)},
        },
    }


def diff_by_id(old_id: str, new_id: str) -> Dict[str, Any]:
    """Fetch two analyses from wg_analysis_results and diff them."""
    old_a = _fetch_scores_by_id(old_id)
    new_a = _fetch_scores_by_id(new_id)

    old_overall = float(old_a["overall_score"])
    new_overall = float(new_a["overall_score"])
    old_dims = _extract_dim_scores(old_a)
    new_dims = _extract_dim_scores(new_a)

    dimension_deltas: Dict[str, Dict[str, float]] = {}
    improvements: List[str] = []
    regressions: List[str] = []
    for dim in sorted(set(old_dims) | set(new_dims)):
        o = old_dims.get(dim, 0.0)
        n = new_dims.get(dim, 0.0)
        delta = round(n - o, 2)
        dimension_deltas[dim] = {"old": round(o, 2), "new": round(n, 2), "delta": delta}
        if delta > 2:
            improvements.append(f"{dim}: +{delta:.1f}")
        elif delta < -2:
            regressions.append(f"{dim}: {delta:.1f}")

    overall_delta = round(new_overall - old_overall, 2)
    summary = (
        f"Improved (+{overall_delta:.1f})" if overall_delta > 2
        else f"Regressed ({overall_delta:.1f})" if overall_delta < -2
        else f"Unchanged ({overall_delta:+.1f})"
    )
    return {
        "word_diff": {"added": 0, "removed": 0, "net": 0},
        "score_diff": {
            "overall_old": round(old_overall, 2),
            "overall_new": round(new_overall, 2),
            "delta": overall_delta,
        },
        "dimension_deltas": dimension_deltas,
        "improvements": improvements,
        "regressions": regressions,
        "net_change": summary,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="WriteGuard diff scorer (WG 20)")
    ap.add_argument("--old")
    ap.add_argument("--new")
    ap.add_argument("--old-id")
    ap.add_argument("--new-id")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.old_id and args.new_id:
        result = diff_by_id(args.old_id, args.new_id)
    elif args.old and args.new:
        old_text = Path(args.old).read_text(encoding="utf-8")
        new_text = Path(args.new).read_text(encoding="utf-8")
        result = diff_analyses(old_text, new_text)
    else:
        ap.error("Provide --old/--new or --old-id/--new-id")
        return 2

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Net change: {result['net_change']}")
        print(f"Overall: {result['score_diff']['overall_old']} -> "
              f"{result['score_diff']['overall_new']} "
              f"(delta {result['score_diff']['delta']:+.2f})")
        print(f"Improvements: {len(result['improvements'])}")
        for i in result["improvements"]:
            print(f"  + {i}")
        print(f"Regressions: {len(result['regressions'])}")
        for r in result["regressions"]:
            print(f"  - {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
