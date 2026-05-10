#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Convergence Gates — detect phantom improvements and reflex plateau.

Three measurement vectors:
  1. Goal drift:       Jaccard similarity between reflex charter and output keywords
  2. Metric drift:     Statistical slope of last N metric_values
  3. Output similarity: SHA-256 hash comparison of consecutive GKP outputs

Combined drift = w_goal*goal + w_metric*metric + w_output*output  (configurable).
Ambiguity scoring: keyword-based clarity (problem/solution/verification).
Retrospective trigger: every Nth cycle OR drift > threshold.

Pure stdlib — zero LLM cost, air-gap safe.

ADRs: D-GEN-6 (append-only audit), D21 (deterministic scoring), D6 (append-only)
"""

import hashlib
import json
import statistics
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ── Keyword sets for ambiguity scoring ────────────────────────────────────

_PROBLEM_KEYWORDS = frozenset(
    [
        "problem",
        "issue",
        "gap",
        "challenge",
        "failure",
        "risk",
        "bug",
        "vulnerability",
        "deficiency",
        "weakness",
        "error",
        "missing",
    ]
)
_SOLUTION_KEYWORDS = frozenset(
    [
        "solution",
        "approach",
        "implement",
        "fix",
        "resolve",
        "create",
        "build",
        "generate",
        "improve",
        "optimize",
        "refactor",
        "add",
    ]
)
_VERIFICATION_KEYWORDS = frozenset(
    [
        "test",
        "verify",
        "measure",
        "metric",
        "assert",
        "validate",
        "check",
        "confirm",
        "benchmark",
        "coverage",
        "score",
        "gate",
    ]
)
_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "shall",
        "should",
        "may",
        "might",
        "can",
        "could",
        "of",
        "in",
        "to",
        "for",
        "with",
        "on",
        "at",
        "from",
        "by",
        "as",
        "into",
        "and",
        "or",
        "not",
        "this",
        "that",
        "it",
        "its",
        "they",
        "them",
        "their",
        "we",
        "our",
    ]
)


def _tokenize(text: str) -> set:
    """Lowercase, strip non-alpha, remove stopwords."""
    import re

    words = set(re.findall(r"[a-z_]+", text.lower()))
    return words - _STOPWORDS


def _jaccard(a: set, b: set) -> float:
    """Jaccard similarity ∈ [0, 1]. Returns 0 if both empty."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def _linear_slope(values: List[float]) -> float:
    """Compute linear regression slope. Returns 0 if insufficient data."""
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(values) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values))
    den = sum((x - x_mean) ** 2 for x in xs)
    if den == 0:
        return 0.0
    return num / den


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConvergenceGate:
    """Evaluate convergence/drift for a Genesis reflex after execution."""

    def __init__(self, config: Dict[str, Any]):
        self.weights = config.get("weights", {})
        self.thresholds = config.get("thresholds", {})
        self.ambiguity_weights = config.get("ambiguity_weights", {})
        self.retrospective = config.get("retrospective", {})
        self.window_size = config.get("window_size", 10)

    # ── Public API ────────────────────────────────────────────────────

    def evaluate(
        self,
        reflex_name: str,
        current_metric: float,
        current_output: str,
        generation: int,
        reflex_description: str = "",
    ) -> Dict[str, Any]:
        """Run all convergence checks. Returns full result dict."""

        goal = self._compute_goal_drift(reflex_description, current_output)
        metric, metric_detail = self._compute_metric_drift(reflex_name, current_metric)
        output_hash = hashlib.sha256(current_output.encode()).hexdigest()
        similarity = self._compute_output_similarity(reflex_name, output_hash)

        w = self.weights
        combined = (
            w.get("goal_drift", 0.5) * goal
            + w.get("metric_drift", 0.3) * metric
            + w.get("output_similarity", 0.2) * (1.0 - similarity)
        )

        ambiguity = self._compute_ambiguity(current_output)
        converged = similarity >= self.thresholds.get("convergence_similarity", 0.95)
        drift_ok = combined <= self.thresholds.get("max_acceptable_drift", 0.30)
        ambiguity_ok = ambiguity <= self.thresholds.get("max_ambiguity", 0.20)
        retro = self._should_trigger_retrospective(generation, combined)

        if converged:
            rec = "converged_stale"
        elif retro:
            rec = "retrospective_needed"
        else:
            rec = "proceed"

        result = {
            "reflex_name": reflex_name,
            "generation": generation,
            "goal_drift": round(goal, 4),
            "metric_drift": round(metric, 4),
            "output_similarity": round(similarity, 4),
            "combined_drift": round(combined, 4),
            "ambiguity_score": round(ambiguity, 4),
            "converged": converged,
            "drift_acceptable": drift_ok,
            "ambiguity_acceptable": ambiguity_ok,
            "retrospective_triggered": retro,
            "recommendation": rec,
            "metric_detail": metric_detail,
            "evaluated_at": _now(),
        }

        self._store(result)
        return result

    # ── Drift vectors ─────────────────────────────────────────────────

    def _compute_goal_drift(self, charter_text: str, output_text: str) -> float:
        """Jaccard-based drift between charter keywords and output keywords."""
        if not charter_text:
            return 0.0
        charter_kw = _tokenize(charter_text)
        output_kw = _tokenize(output_text)
        sim = _jaccard(charter_kw, output_kw)
        return round(1.0 - sim, 4)  # drift = 1 - similarity

    def _compute_metric_drift(self, reflex_name: str, current: float) -> Tuple[float, Dict]:
        """Slope-based metric drift. Low slope = diminishing returns."""
        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT metric_value FROM genesis_audit
                   WHERE reflex_name = ? AND event_type = 'genesis.reflex.completed'
                     AND metric_value IS NOT NULL
                   ORDER BY created_at DESC LIMIT ?""",
                (reflex_name, self.window_size),
            ).fetchall()
        finally:
            conn.close()

        values = [r[0] for r in reversed(rows)] if rows else []
        values.append(current)

        slope = _linear_slope(values)
        variance = statistics.variance(values) if len(values) >= 2 else 0.0
        # Normalize drift: low abs slope + low variance = high drift (stale)
        drift = max(0.0, 1.0 - min(abs(slope) * 10, 1.0))

        detail = {
            "values": values[-self.window_size :],
            "slope": round(slope, 6),
            "variance": round(variance, 6),
            "n": len(values),
        }
        return round(drift, 4), detail

    def _compute_output_similarity(self, reflex_name: str, current_hash: str) -> float:
        """Compare current output hash to most recent GKP output hash."""
        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            row = conn.execute(
                """SELECT payload_hash FROM genesis_gkp
                   WHERE reflex = ? ORDER BY created_at DESC LIMIT 1""",
                (reflex_name,),
            ).fetchone()
        except Exception:
            # genesis_gkp may not have payload_hash column yet
            return 0.0
        finally:
            conn.close()

        if not row or not row[0]:
            return 0.0
        return 1.0 if row[0] == current_hash else 0.0

    # ── Ambiguity scoring ─────────────────────────────────────────────

    def _compute_ambiguity(self, text: str) -> float:
        """Keyword-presence clarity scoring. Lower = clearer."""
        tokens = _tokenize(text)
        if not tokens:
            return 1.0  # Empty output = fully ambiguous

        w = self.ambiguity_weights
        problem_hits = len(tokens & _PROBLEM_KEYWORDS)
        solution_hits = len(tokens & _SOLUTION_KEYWORDS)
        verify_hits = len(tokens & _VERIFICATION_KEYWORDS)

        # Clarity: each dimension scores 0-1 based on keyword presence
        p_clarity = min(problem_hits / 3, 1.0)
        s_clarity = min(solution_hits / 3, 1.0)
        v_clarity = min(verify_hits / 3, 1.0)

        clarity = (
            w.get("problem_clarity", 0.4) * p_clarity
            + w.get("solution_clarity", 0.3) * s_clarity
            + w.get("verification_clarity", 0.3) * v_clarity
        )
        return round(1.0 - clarity, 4)  # ambiguity = 1 - clarity

    # ── Retrospective trigger ─────────────────────────────────────────

    def _should_trigger_retrospective(self, generation: int, combined_drift: float) -> bool:
        n = self.retrospective.get("trigger_every_n_cycles", 5)
        drift_thresh = self.retrospective.get("trigger_on_drift_above", 0.30)
        return (generation > 0 and generation % n == 0) or combined_drift > drift_thresh

    # ── Storage ───────────────────────────────────────────────────────

    def _store(self, result: Dict) -> str:
        """Append-only INSERT into genesis_convergence_log."""
        from tools.db.storage import get_connection

        entry_id = str(uuid.uuid4())
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO genesis_convergence_log
                   (id, reflex_name, generation, goal_drift, metric_drift,
                    output_similarity, combined_drift, ambiguity_score,
                    converged, retrospective_triggered, details_json,
                    classification, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CUI', ?)""",
                (
                    entry_id,
                    result["reflex_name"],
                    result["generation"],
                    result["goal_drift"],
                    result["metric_drift"],
                    result["output_similarity"],
                    result["combined_drift"],
                    result["ambiguity_score"],
                    1 if result["converged"] else 0,
                    1 if result["retrospective_triggered"] else 0,
                    json.dumps(result.get("metric_detail", {})),
                    result["evaluated_at"],
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return entry_id
