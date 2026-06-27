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
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ── Module-level constants (replaces magic numbers) ───────────────────────

# Scale factor mapping linear slope → [0,1] drift. Slope ≥ 0.10 → full correction.
_SLOPE_NORMALIZER: float = 10.0
# Keyword hits needed per dimension to score full clarity (0/3=none, 3/3=full).
_CLARITY_KEYWORD_DIVISOR: int = 3

# Default convergence thresholds — used as fallback when config is absent and
# AnomalyDetector has insufficient history for adaptive thresholds.
_DEFAULT_CONVERGENCE_SIMILARITY: float = 0.95
_DEFAULT_MAX_ACCEPTABLE_DRIFT: float = 0.30
_DEFAULT_MAX_AMBIGUITY: float = 0.20

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
    """Lowercase, strip non-alpha, remove stopwords. Used as NLP fallback."""
    import re

    words = set(re.findall(r"[a-z_]+", text.lower()))
    return words - _STOPWORDS


class _NLPExtractor:
    """LLM-backed semantic keyword extractor with regex fallback.

    Uses Claude Haiku for richer concept/entity extraction when an LLM is
    available; silently falls back to _tokenize() in air-gap environments so
    the module remains pure-stdlib safe.
    """

    _SYSTEM = (
        "Extract semantically meaningful keywords from the supplied text. "
        "Return ONLY a JSON array of lowercase strings — no explanation, no markdown. "
        "Include technical terms, domain nouns, and action verbs. "
        "Exclude stopwords, articles, and prepositions. At most 30 keywords."
    )
    _FUNCTION = "convergence_nlp_extract"
    _cache: Dict[str, set] = {}

    @classmethod
    def extract(cls, text: str) -> set:
        """Return keyword set. Uses LLM when available, else regex fallback."""
        if not text:
            return set()
        key = hashlib.sha256(text.encode()).hexdigest()[:16]
        if key in cls._cache:
            return cls._cache[key]
        result = cls._llm_extract(text) or _tokenize(text)
        cls._cache[key] = result
        return result

    @classmethod
    def _llm_extract(cls, text: str):
        """Attempt LLM extraction. Returns keyword set or None on any failure."""
        try:
            from icdev.tools.llm.router import LLMRequest, LLMRouter  # noqa: PLC0415

            router = LLMRouter()
            if router.is_no_llm_mode() or not router.has_any_llm():
                return None
            resp = router.invoke(
                cls._FUNCTION,
                LLMRequest(
                    messages=[{"role": "user", "content": text[:2000]}],
                    system_prompt=cls._SYSTEM,
                ),
            )
            keywords = json.loads(resp.content.strip())
            if isinstance(keywords, list):
                return {str(k).lower() for k in keywords if k} - _STOPWORDS
        except Exception:
            return None
        return None


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


class AnomalyDetector:
    """Compute per-reflex adaptive thresholds from historical convergence data.

    Uses Z-score method: threshold = mean + Z * std.
    Falls back to config defaults when fewer than min_samples records exist.
    """

    DEFAULT_Z_SCORE: float = 2.0
    DEFAULT_MIN_SAMPLES: int = 5
    _HISTORY_LIMIT: int = 50

    @staticmethod
    def adaptive_thresholds(
        reflex_name: str,
        config_thresholds: Dict[str, Any],
        min_samples: int = DEFAULT_MIN_SAMPLES,
        z_score: float = DEFAULT_Z_SCORE,
    ) -> Dict[str, Any]:
        """Return thresholds dict with adaptive values when history is sufficient.

        Returns config defaults untouched plus metadata when n_samples < min_samples.
        Otherwise computes mean + z*std for each metric from historical records.
        """
        history = AnomalyDetector._fetch_history(reflex_name)
        n = len(history)

        if n < min_samples:
            return {**config_thresholds, "adaptive": False, "n_samples": n}

        similarities = [r["output_similarity"] for r in history]
        drifts = [r["combined_drift"] for r in history]
        ambiguities = [r["ambiguity_score"] for r in history]

        return {
            "convergence_similarity": AnomalyDetector._upper_bound(similarities, z_score),
            "max_acceptable_drift": AnomalyDetector._upper_bound(drifts, z_score),
            "max_ambiguity": AnomalyDetector._upper_bound(ambiguities, z_score),
            "adaptive": True,
            "n_samples": n,
        }

    @staticmethod
    def _upper_bound(values: List[float], z: float) -> float:
        """mean + z*std, clamped to [0, 1]."""
        if not values:
            return 1.0
        mean = statistics.mean(values)
        std = statistics.stdev(values) if len(values) >= 2 else 0.0
        return min(1.0, max(0.0, mean + z * std))

    @staticmethod
    def _fetch_history(reflex_name: str) -> List[Dict[str, float]]:
        """Fetch recent convergence records from genesis_convergence_log."""
        try:
            from tools.db.storage import get_connection  # noqa: PLC0415

            conn = get_connection()
            try:
                rows = conn.execute(
                    """SELECT output_similarity, combined_drift, ambiguity_score
                       FROM genesis_convergence_log
                       WHERE reflex_name = %s
                       ORDER BY created_at DESC LIMIT %s""",
                    (reflex_name, AnomalyDetector._HISTORY_LIMIT),
                ).fetchall()
            finally:
                conn.close()
        except Exception:
            return []
        return [
            {
                "output_similarity": r[0] or 0.0,
                "combined_drift": r[1] or 0.0,
                "ambiguity_score": r[2] or 0.0,
            }
            for r in rows
        ]


class ConvergenceGate:
    """Evaluate convergence/drift for a Genesis reflex after execution."""

    def __init__(self, config: Dict[str, Any]):
        self.weights = config.get("weights", {})
        raw_thresh = config.get("thresholds", {})
        # Merge config thresholds over named defaults so callers always have
        # named, discoverable fallbacks even when config omits a key.
        self.thresholds = {
            "convergence_similarity": _DEFAULT_CONVERGENCE_SIMILARITY,
            "max_acceptable_drift": _DEFAULT_MAX_ACCEPTABLE_DRIFT,
            "max_ambiguity": _DEFAULT_MAX_AMBIGUITY,
            **raw_thresh,
        }
        self.ambiguity_weights = config.get("ambiguity_weights", {})
        self.retrospective = config.get("retrospective", {})
        self.window_size = config.get("window_size", 10)
        anomaly_cfg = config.get("anomaly_detection", {})
        self._anomaly_min_samples = anomaly_cfg.get(
            "min_samples", AnomalyDetector.DEFAULT_MIN_SAMPLES
        )
        self._anomaly_z_score = anomaly_cfg.get(
            "z_score", AnomalyDetector.DEFAULT_Z_SCORE
        )
        self._slope_normalizer = float(
            anomaly_cfg.get("slope_normalizer", _SLOPE_NORMALIZER)
        )
        self._clarity_keyword_divisor = int(
            anomaly_cfg.get("clarity_keyword_divisor", _CLARITY_KEYWORD_DIVISOR)
        )

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

        effective = AnomalyDetector.adaptive_thresholds(
            reflex_name,
            self.thresholds,
            min_samples=self._anomaly_min_samples,
            z_score=self._anomaly_z_score,
        )

        converged = similarity >= effective.get("convergence_similarity", _DEFAULT_CONVERGENCE_SIMILARITY)
        drift_ok = combined <= effective.get("max_acceptable_drift", _DEFAULT_MAX_ACCEPTABLE_DRIFT)
        ambiguity_ok = ambiguity <= effective.get("max_ambiguity", _DEFAULT_MAX_AMBIGUITY)
        retro = self._should_trigger_retrospective(
            generation, combined, effective.get("max_acceptable_drift")
        )

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
            "thresholds_used": {
                k: round(v, 4) if isinstance(v, float) else v
                for k, v in effective.items()
            },
            "evaluated_at": _now(),
        }

        self._store(result)
        return result

    # ── Drift vectors ─────────────────────────────────────────────────

    def _compute_goal_drift(self, charter_text: str, output_text: str) -> float:
        """Jaccard-based drift between charter keywords and output keywords."""
        if not charter_text:
            return 0.0
        charter_kw = _NLPExtractor.extract(charter_text)
        output_kw = _NLPExtractor.extract(output_text)
        sim = _jaccard(charter_kw, output_kw)
        return round(1.0 - sim, 4)  # drift = 1 - similarity

    def _compute_metric_drift(self, reflex_name: str, current: float) -> Tuple[float, Dict]:
        """Slope-based metric drift. Low slope = diminishing returns."""
        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT metric_value FROM genesis_audit
                   WHERE reflex_name = %s AND event_type = 'genesis.reflex.completed'
                     AND metric_value IS NOT NULL
                   ORDER BY created_at DESC LIMIT %s""",
                (reflex_name, self.window_size),
            ).fetchall()
        finally:
            conn.close()

        values = [r[0] for r in reversed(rows)] if rows else []
        values.append(current)

        slope = _linear_slope(values)
        variance = statistics.variance(values) if len(values) >= 2 else 0.0
        # Normalize drift: low abs slope + low variance = high drift (stale)
        drift = max(0.0, 1.0 - min(abs(slope) * self._slope_normalizer, 1.0))

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
                   WHERE reflex = %s ORDER BY created_at DESC LIMIT 1""",
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
        tokens = _NLPExtractor.extract(text)
        if not tokens:
            return 1.0  # Empty output = fully ambiguous

        w = self.ambiguity_weights
        problem_hits = len(tokens & _PROBLEM_KEYWORDS)
        solution_hits = len(tokens & _SOLUTION_KEYWORDS)
        verify_hits = len(tokens & _VERIFICATION_KEYWORDS)

        # Clarity: each dimension scores 0-1 based on keyword presence
        p_clarity = min(problem_hits / self._clarity_keyword_divisor, 1.0)
        s_clarity = min(solution_hits / self._clarity_keyword_divisor, 1.0)
        v_clarity = min(verify_hits / self._clarity_keyword_divisor, 1.0)

        clarity = (
            w.get("problem_clarity", 0.4) * p_clarity
            + w.get("solution_clarity", 0.3) * s_clarity
            + w.get("verification_clarity", 0.3) * v_clarity
        )
        return round(1.0 - clarity, 4)  # ambiguity = 1 - clarity

    # ── Retrospective trigger ─────────────────────────────────────────

    def _should_trigger_retrospective(
        self,
        generation: int,
        combined_drift: float,
        effective_drift_threshold: Optional[float] = None,
    ) -> bool:
        n = self.retrospective.get("trigger_every_n_cycles", 5)
        config_thresh = self.retrospective.get("trigger_on_drift_above", 0.30)
        # Prefer the adaptive threshold so the retrospective trigger tracks anomalies.
        drift_thresh = effective_drift_threshold if effective_drift_threshold is not None else config_thresh
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
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'CUI', %s)""",
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
