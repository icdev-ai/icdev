#!/usr/bin/env python3
# CUI // SP-CTI
"""CRAG-inspired RAG evaluation framework (D-RAG-23).

Implements 8 question types, hallucination-penalizing scoring (-1/0/0.5/1),
and entity popularity tiers from the CRAG benchmark (arxiv 2406.04744).

Usage:
    python tools/rag/crag_evaluator.py --benchmark-crag --test-set data/rag/crag_test.json --json
    python tools/rag/crag_evaluator.py --classify-question --query "Compare X and Y" --json
    python tools/rag/crag_evaluator.py --score --answer "42" --ground-truth "42" --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.rag.crag_evaluator")

# ---------------------------------------------------------------------------
# Constants (CRAG benchmark — arxiv 2406.04744)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Module-level fallback constants — all overridable from args/rag_config.yaml
# under crag_evaluator.anomaly_detection.  Change config, not code.
# ---------------------------------------------------------------------------
_ENTITY_HEAD_THRESHOLD      = 100    # corpus mention count → "head" popularity tier
_ENTITY_TORSO_THRESHOLD     = 10     # corpus mention count → "torso" tier (else "tail")
_ACCEPTABLE_WORD_OVERLAP    = 0.80   # minimum Jaccard overlap for "acceptable" match
_ACCEPTABLE_SHORT_GT_WORDS  = 10     # max ground-truth words for reverse containment check
_LLM_CONTEXT_CHARS          = 500    # context chars forwarded to LLM question classifier
_LLM_MAX_TOKENS             = 64     # max tokens for question-type classification
_LLM_TEMPERATURE            = 0.0    # temperature for question-type classification
_DEFAULT_FALLBACK_CONFIDENCE = 0.3   # confidence assigned when no heuristic matches
_GATE_THRESHOLD             = 0.3    # --gate CLI pass/fail threshold for aggregate CRAG score

# Heuristic confidence levels
_HEURISTIC_CONFIDENCE_HIGH   = 0.85  # comparison / aggregation / set
_HEURISTIC_CONFIDENCE_MID    = 0.75  # multi_hop / post_processing / simple_condition
_HEURISTIC_CONFIDENCE_LOW    = 0.70  # false_premise


def _compute_crag_anomaly_thresholds(anomaly_cfg: "dict | None" = None) -> dict:
    """Compute adaptive CRAG scoring thresholds from historical evaluation data.

    Reads rag_evaluation_results to calibrate:
    - entity_head_threshold: based on actual corpus mention distribution
    - acceptable_overlap: tighten if many acceptable answers later revised to incorrect

    Falls back to module-level defaults when fewer than min_samples rows exist.
    """
    cfg = anomaly_cfg or {}
    defaults = {
        "entity_head_threshold":    _ENTITY_HEAD_THRESHOLD,
        "entity_torso_threshold":   _ENTITY_TORSO_THRESHOLD,
        "acceptable_word_overlap":  _ACCEPTABLE_WORD_OVERLAP,
        "computed": False,
    }

    if not cfg.get("enabled", True):
        return {**defaults,
                "entity_head_threshold":   cfg.get("fallback_entity_head", _ENTITY_HEAD_THRESHOLD),
                "entity_torso_threshold":  cfg.get("fallback_entity_torso", _ENTITY_TORSO_THRESHOLD),
                "acceptable_word_overlap": cfg.get("fallback_acceptable_overlap", _ACCEPTABLE_WORD_OVERLAP)}

    min_samples = cfg.get("min_samples", 50)
    bounds      = cfg.get("adaptive_bounds", {})
    overlap_floor = float(bounds.get("overlap_floor", 0.60))
    overlap_ceil  = float(bounds.get("overlap_ceil",  0.95))

    conn = None
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        # How often do "acceptable" labels appear vs "incorrect"?
        # If acceptable_rate is very low, tighten the overlap threshold.
        row = conn.execute(
            "SELECT "
            "SUM(CASE WHEN crag_score = 0.5 THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0) AS acceptable_rate, "
            "SUM(CASE WHEN crag_score < 0.0 THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0) AS incorrect_rate, "
            "COUNT(*) AS n "
            "FROM rag_evaluation_results WHERE crag_score IS NOT NULL"
        ).fetchone()
        if row:
            n = row["n"] if isinstance(row, dict) else row[2]
            if n and n >= min_samples:
                acceptable_rate = float(row["acceptable_rate"] if isinstance(row, dict) else row[0]) or 0.0
                incorrect_rate  = float(row["incorrect_rate"]  if isinstance(row, dict) else row[1]) or 0.0
                # High incorrect rate → overlap threshold was too loose; tighten it
                adjustment = (incorrect_rate - acceptable_rate) * 0.05
                new_overlap = max(overlap_floor, min(overlap_ceil, _ACCEPTABLE_WORD_OVERLAP + adjustment))
                return {
                    "entity_head_threshold":   _ENTITY_HEAD_THRESHOLD,
                    "entity_torso_threshold":  _ENTITY_TORSO_THRESHOLD,
                    "acceptable_word_overlap": round(new_overlap, 3),
                    "computed": True,
                    "n_samples": n,
                    "acceptable_rate": round(acceptable_rate, 3),
                    "incorrect_rate":  round(incorrect_rate, 3),
                }
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return defaults


QUESTION_TYPES: List[str] = [
    "simple",
    "simple_condition",
    "set",
    "comparison",
    "aggregation",
    "multi_hop",
    "post_processing",
    "false_premise",
]

ENTITY_POPULARITY_TIERS: List[str] = ["head", "torso", "tail"]

CRAG_SCORES: Dict[str, float] = {
    "perfect": 1.0,
    "acceptable": 0.5,
    "missing": 0.0,
    "incorrect": -1.0,
}

# Regex heuristics for question-type classification
_COMPARISON_PATTERNS = re.compile(
    r"\b(compare|compar|differ|versus|vs\.?|contrast|distinction|difference between)\b",
    re.IGNORECASE,
)
_AGGREGATION_PATTERNS = re.compile(
    r"\b(how many|how much|count|total|number of|sum of|average|mean|minimum|maximum|min|max)\b",
    re.IGNORECASE,
)
_SET_PATTERNS = re.compile(
    r"\b(list all|list the|enumerate|what are all|what are the|give me all|name all|all the)\b",
    re.IGNORECASE,
)
_MULTI_HOP_PATTERNS = re.compile(
    r"\band then\b|\bafter that\b|\bnext\b.*\bthen\b|\bfollowed by\b|\bsubsequently\b",
    re.IGNORECASE,
)
_CONDITION_PATTERNS = re.compile(
    r"\bif\b|\bwhen\b|\bgiven that\b|\bassuming\b|\bin the case\b|\bprovided that\b",
    re.IGNORECASE,
)
_FALSE_PREMISE_PATTERNS = re.compile(
    r"\bstill\b|\bno longer\b|\bused to\b|\bwhy did .* stop\b|\bwhy is .* not\b",
    re.IGNORECASE,
)
_POST_PROCESSING_PATTERNS = re.compile(
    r"\bsort\b|\brank\b|\border by\b|\bcalculate\b|\bcompute\b|\bformat\b|\btranslate\b|\bconvert\b",
    re.IGNORECASE,
)

# Abstention patterns (answers that admit not knowing)
_ABSTENTION_PATTERNS = re.compile(
    r"^\s*(i (don'?t|do not|cant|can'?t) know"
    r"|i am not sure"
    r"|i'm not sure"
    r"|cannot answer"
    r"|i cannot (find|determine|answer|tell)"
    r"|no (information|data|answer) (available|found)"
    r"|unknown"
    r"|n/a"
    r"|not (available|applicable|known|found)"
    r"|unable to (answer|determine|find)"
    r"|this (question|query) cannot be answered"
    r"|no relevant (information|context|data))\s*$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Safe JSON extraction helper
# ---------------------------------------------------------------------------


def _safe_extract_json(text: str, container: str = "{") -> Any:
    """Extract JSON object or array from raw LLM response text."""
    if not text:
        return None
    open_br = container
    close_br = "}" if container == "{" else "]"
    start = text.find(open_br)
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == open_br:
            depth += 1
        elif ch == close_br:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


# ---------------------------------------------------------------------------
# CRAGScorer
# ---------------------------------------------------------------------------


class CRAGScorer:
    """Score answers using the CRAG hallucination-penalizing rubric (D-RAG-23).

    Scoring rubric:
        perfect   (+1.0) — Answer matches ground truth exactly or semantically
        acceptable (+0.5) — Answer is partially correct or close enough
        missing    (0.0) — Answer is empty or an abstention
        incorrect  (-1.0) — Answer is present but factually wrong (hallucination)

    Special case: abstention on a false-premise question scores as perfect (1.0)
    because recognising a false premise and refusing to answer is the ideal behaviour.
    """

    # ------------------------------------------------------------------
    # Public scoring API
    # ------------------------------------------------------------------

    def score_answer(
        self,
        answer: str,
        ground_truth: str,
        question_type: str = "simple",
        is_false_premise: bool = False,
    ) -> Dict[str, Any]:
        """Score a single answer against its ground truth.

        Args:
            answer: The system-generated answer to evaluate.
            ground_truth: The reference correct answer.
            question_type: One of QUESTION_TYPES (informational only, affects reasoning).
            is_false_premise: Whether the question contains a false premise.

        Returns:
            Dict with keys: score (float), label (str), reasoning (str).
        """
        if not isinstance(answer, str):
            answer = str(answer) if answer is not None else ""

        # --- Step 1: Check for abstention (empty or "I don't know" patterns) ---
        if self._is_abstention(answer):
            if is_false_premise:
                # Refusing to answer a false-premise question is ideal
                return {
                    "score": CRAG_SCORES["perfect"],
                    "label": "perfect",
                    "reasoning": (
                        "System correctly abstained from answering a false-premise question. "
                        "Recognising and refusing to engage with a false premise is ideal behaviour."
                    ),
                }
            return {
                "score": CRAG_SCORES["missing"],
                "label": "missing",
                "reasoning": (
                    "Answer is empty or an explicit abstention. The system did not attempt to answer the question."
                ),
            }

        # --- Step 2: False premise with a substantive (non-abstaining) answer ---
        if is_false_premise:
            # Any non-abstention answer to a false-premise question is wrong
            # unless the answer itself acknowledges the false premise
            if self._acknowledges_false_premise(answer):
                return {
                    "score": CRAG_SCORES["perfect"],
                    "label": "perfect",
                    "reasoning": (
                        "Answer correctly identifies and addresses the false premise rather than accepting it as true."
                    ),
                }
            return {
                "score": CRAG_SCORES["incorrect"],
                "label": "incorrect",
                "reasoning": (
                    "Answer treats a false premise as true and provides a response "
                    "based on an incorrect assumption (hallucination penalty applied)."
                ),
            }

        # --- Step 3: Exact match ---
        if self._is_exact_match(answer, ground_truth):
            return {
                "score": CRAG_SCORES["perfect"],
                "label": "perfect",
                "reasoning": f"Answer exactly matches ground truth for question type '{question_type}'.",
            }

        # --- Step 4: Acceptable (close enough) match ---
        if self._is_acceptable(answer, ground_truth):
            return {
                "score": CRAG_SCORES["acceptable"],
                "label": "acceptable",
                "reasoning": (
                    f"Answer is sufficiently close to ground truth for question type "
                    f"'{question_type}' (containment or high word-overlap)."
                ),
            }

        # --- Step 5: Incorrect — hallucination penalty ---
        return {
            "score": CRAG_SCORES["incorrect"],
            "label": "incorrect",
            "reasoning": (
                f"Answer does not match ground truth for question type '{question_type}'. "
                "Hallucination penalty applied (-1.0)."
            ),
        }

    # ------------------------------------------------------------------
    # Question-type classification
    # ------------------------------------------------------------------

    def classify_question_type(
        self,
        query: str,
        context: str = "",
    ) -> Dict[str, Any]:
        """Classify a query into one of the 8 CRAG question types.

        Strategy:
            1. Fast regex heuristics (air-gap safe, deterministic).
            2. Scanner-tier LLM (qwen3.5) if regex is inconclusive.
            3. Default fallback: "simple".

        Args:
            query: The question text.
            context: Optional supporting context (used by LLM classifier).

        Returns:
            Dict with keys: question_type (str), confidence (float), method (str).
        """
        query_stripped = query.strip()

        # --- Regex heuristics ---
        heuristic_result = self._classify_via_heuristics(query_stripped)
        if heuristic_result is not None:
            qtype, confidence = heuristic_result
            return {
                "question_type": qtype,
                "confidence": confidence,
                "method": "heuristic",
            }

        # --- LLM classifier (scanner tier) ---
        llm_result = self._classify_via_llm(query_stripped, context)
        if llm_result is not None:
            qtype, confidence = llm_result
            return {
                "question_type": qtype,
                "confidence": confidence,
                "method": "llm",
            }

        # --- Default fallback ---
        return {
            "question_type": "simple",
            "confidence": _DEFAULT_FALLBACK_CONFIDENCE,
            "method": "default",
        }

    # ------------------------------------------------------------------
    # Entity popularity classification
    # ------------------------------------------------------------------

    def classify_entity_popularity(
        self,
        entity: str,
        db_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Classify an entity's popularity tier based on corpus frequency.

        Args:
            entity: The entity name/phrase to look up.
            db_path: Optional override for the RAG database path.

        Returns:
            Dict with keys: tier (str), frequency (int).
        """
        frequency = self._get_entity_frequency(entity, db_path)

        if frequency >= _ENTITY_HEAD_THRESHOLD:
            tier = "head"
        elif frequency >= _ENTITY_TORSO_THRESHOLD:
            tier = "torso"
        else:
            tier = "tail"

        return {"tier": tier, "frequency": frequency}

    # ------------------------------------------------------------------
    # Private helpers — scoring
    # ------------------------------------------------------------------

    def _normalize(self, text: str) -> str:
        """Normalize text for comparison: lowercase, collapse whitespace."""
        return re.sub(r"\s+", " ", text.strip().lower())

    def _is_abstention(self, answer: str) -> bool:
        """Return True if the answer is an explicit abstention or empty."""
        if not answer or not answer.strip():
            return True
        return bool(_ABSTENTION_PATTERNS.match(answer.strip()))

    def _acknowledges_false_premise(self, answer: str) -> bool:
        """Return True if the answer explicitly flags the question as false/invalid."""
        patterns = re.compile(
            r"\b(false premise|incorrect premise|invalid assumption"
            r"|does not exist|never happened|not (true|correct|accurate|real)"
            r"|there is no|no such|factually incorrect"
            r"|misconception|this is (wrong|incorrect|false|a myth))\b",
            re.IGNORECASE,
        )
        return bool(patterns.search(answer))

    def _is_exact_match(self, answer: str, ground_truth: str) -> bool:
        """Exact match after normalization."""
        return self._normalize(answer) == self._normalize(ground_truth)

    def _is_acceptable(self, answer: str, ground_truth: str) -> bool:
        """Determine whether an answer is acceptably close to the ground truth.

        Checks:
            1. Case-insensitive containment (ground truth is substring of answer
               or answer is substring of ground truth, for short answers).
            2. >80% word-level Jaccard overlap.
        """
        norm_ans = self._normalize(answer)
        norm_gt = self._normalize(ground_truth)

        # Containment check (works well for short, factual answers)
        if norm_gt in norm_ans:
            return True
        # Reverse containment only for short ground truths
        gt_words = norm_gt.split()
        if len(gt_words) <= _ACCEPTABLE_SHORT_GT_WORDS and norm_ans in norm_gt:
            return True

        # Word overlap (Jaccard-like, ≥ 80%)
        ans_words = set(norm_ans.split())
        gt_words_set = set(gt_words)
        if not gt_words_set:
            return False
        intersection = ans_words & gt_words_set
        union = ans_words | gt_words_set
        overlap = len(intersection) / len(union) if union else 0.0
        return overlap >= _ACCEPTABLE_WORD_OVERLAP

    # ------------------------------------------------------------------
    # Private helpers — question-type classification
    # ------------------------------------------------------------------

    def _classify_via_heuristics(self, query: str) -> Optional[Tuple[str, float]]:
        """Apply regex heuristics. Returns (question_type, confidence) or None."""
        # Order matters — check more specific types first
        if _COMPARISON_PATTERNS.search(query):
            return ("comparison", _HEURISTIC_CONFIDENCE_HIGH)
        if _AGGREGATION_PATTERNS.search(query):
            return ("aggregation", _HEURISTIC_CONFIDENCE_HIGH)
        if _SET_PATTERNS.search(query):
            return ("set", _HEURISTIC_CONFIDENCE_HIGH)
        multi_q = query.count("?") > 1
        if multi_q or _MULTI_HOP_PATTERNS.search(query):
            return ("multi_hop", _HEURISTIC_CONFIDENCE_MID)
        if _POST_PROCESSING_PATTERNS.search(query):
            return ("post_processing", _HEURISTIC_CONFIDENCE_MID)
        if _CONDITION_PATTERNS.search(query):
            return ("simple_condition", _HEURISTIC_CONFIDENCE_MID)
        if _FALSE_PREMISE_PATTERNS.search(query):
            return ("false_premise", _HEURISTIC_CONFIDENCE_LOW)
        # "simple" has no positive heuristic — fall through to LLM
        return None

    def _classify_via_llm(self, query: str, context: str = "") -> Optional[Tuple[str, float]]:
        """Use scanner-tier LLM (qwen3.5) to classify the question type."""
        try:
            from tools.llm.router import LLMRouter
            from tools.llm.provider import LLMRequest

            types_list = ", ".join(QUESTION_TYPES)
            context_block = f"\nContext: {context[:_LLM_CONTEXT_CHARS]}" if context else ""
            prompt = (
                f"Classify the following question into exactly ONE of these types: {types_list}.\n"
                f"Question: {query}{context_block}\n\n"
                f'Return ONLY valid JSON: {{"question_type": "<type>", "confidence": 0.0-1.0}}'
            )
            router = LLMRouter()
            request = LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=(
                    "You are a question-type classifier for the CRAG benchmark. "
                    "Output only valid JSON with 'question_type' and 'confidence' keys."
                ),
                max_tokens=_LLM_MAX_TOKENS,
                temperature=_LLM_TEMPERATURE,
                classification="CUI",
            )
            response = router.invoke("rag_evaluate", request)
            parsed = _safe_extract_json(response.content.strip(), "{")
            if isinstance(parsed, dict):
                qtype = parsed.get("question_type", "").lower().strip()
                confidence = float(parsed.get("confidence", 0.5))
                if qtype in QUESTION_TYPES:
                    return (qtype, confidence)
        except Exception as exc:
            logger.debug("LLM question-type classifier failed: %s", exc)
        return None

    # ------------------------------------------------------------------
    # Private helpers — entity popularity
    # ------------------------------------------------------------------

    def _get_entity_frequency(self, entity: str, db_path: Optional[Path] = None) -> int:
        """Query rag_chunks for entity mention frequency."""
        try:
            from tools.db.storage import get_connection

            target_db = db_path or (BASE_DIR / "data" / "rag" / "rag_vectors.db")
            with get_connection(target_db) as conn:
                rows = conn.execute(
                    "SELECT COUNT(*) FROM rag_chunks WHERE content LIKE %s COLLATE NOCASE",
                    (f"%{entity}%",),
                ).fetchone()
                return int(rows[0]) if rows else 0
        except Exception as exc:
            logger.debug("Entity frequency lookup failed: %s", exc)
            return 0


# ---------------------------------------------------------------------------
# CRAGBenchmarkRunner
# ---------------------------------------------------------------------------


class CRAGBenchmarkRunner:
    """Run CRAG-style benchmark campaigns against a test set (D-RAG-23).

    Loads a JSON test set, scores each case using CRAGScorer, aggregates
    results, and persists to the ICDEV™ database.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._config = config or self._load_config()
        self._scorer = CRAGScorer()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_campaign(
        self,
        test_set_path: str,
        task_type: str = "T1",
        campaign_name: str = "",
    ) -> Dict[str, Any]:
        """Run a full CRAG benchmark campaign.

        Args:
            test_set_path: Path to JSON file containing test cases.
            task_type: CRAG task type — "T1" (open-domain QA), "T2" (multi-hop),
                       "T3" (multi-modal).
            campaign_name: Optional human-readable name for this campaign.

        Returns:
            Summary dict with aggregate scores and per-type breakdowns.
        """
        # Validate task type
        valid_task_types = ["T1", "T2", "T3"]
        if task_type not in valid_task_types:
            task_type = "T1"

        # Generate campaign ID
        campaign_id = f"crag-{uuid.uuid4().hex[:12]}"
        if not campaign_name:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            campaign_name = f"CRAG-{task_type}-{ts}"

        # Load test set
        test_cases = self._load_test_set(test_set_path)
        total_cases = len(test_cases)

        if total_cases == 0:
            return self._empty_campaign_result(campaign_id, campaign_name, task_type, test_set_path)

        # Score each case
        results = []
        for case in test_cases:
            result = self._score_case(case)
            results.append(result)

        # Aggregate scores
        summary = self._aggregate_results(results, campaign_id, campaign_name, task_type)

        # Persist to DB
        try:
            self._persist_campaign(
                campaign_id=campaign_id,
                campaign_name=campaign_name,
                task_type=task_type,
                test_set_path=test_set_path,
                total_cases=total_cases,
                aggregate_score=summary["aggregate_crag_score"],
                results=results,
                summary=summary,
            )
        except Exception as exc:
            logger.warning("Failed to persist campaign to DB: %s", exc)

        return summary

    # ------------------------------------------------------------------
    # Private helpers — campaign logic
    # ------------------------------------------------------------------

    def _load_test_set(self, test_set_path: str) -> List[Dict[str, Any]]:
        """Load and validate a CRAG test set JSON file."""
        path = Path(test_set_path)
        if not path.exists():
            logger.warning("Test set file not found: %s", path)
            return []
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                return data
            logger.warning("Test set must be a JSON array; got %s", type(data).__name__)
            return []
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to load test set: %s", exc)
            return []

    def _score_case(self, case: Dict[str, Any]) -> Dict[str, Any]:
        """Score a single test case."""
        query = case.get("query", "")
        ground_truth = case.get("ground_truth", "")
        answer = case.get("answer", "")
        question_type = case.get("question_type", "simple")
        entity_popularity = case.get("entity_popularity", "head")
        is_false_premise = bool(case.get("is_false_premise", False))

        # Validate question_type
        if question_type not in QUESTION_TYPES:
            question_type = "simple"
        if entity_popularity not in ENTITY_POPULARITY_TIERS:
            entity_popularity = "tail"

        score_result = self._scorer.score_answer(
            answer=answer,
            ground_truth=ground_truth,
            question_type=question_type,
            is_false_premise=is_false_premise,
        )

        result_id = f"res-{hashlib.sha256((query + answer).encode()).hexdigest()[:12]}"

        return {
            "id": result_id,
            "query": query,
            "answer": answer,
            "ground_truth": ground_truth,
            "question_type": question_type,
            "entity_popularity": entity_popularity,
            "is_false_premise": is_false_premise,
            "crag_score": score_result["score"],
            "crag_label": score_result["label"],
            "reasoning": score_result["reasoning"],
        }

    def _aggregate_results(
        self,
        results: List[Dict[str, Any]],
        campaign_id: str,
        campaign_name: str,
        task_type: str,
    ) -> Dict[str, Any]:
        """Aggregate scored results into campaign-level statistics."""
        if not results:
            return self._empty_campaign_result(campaign_id, campaign_name, task_type, "")

        scores = [r["crag_score"] for r in results]
        aggregate_score = sum(scores) / len(scores)

        # Per-question-type breakdown
        type_breakdown: Dict[str, List[float]] = {qt: [] for qt in QUESTION_TYPES}
        for r in results:
            qt = r.get("question_type", "simple")
            type_breakdown.setdefault(qt, []).append(r["crag_score"])

        type_stats = {}
        for qt, qt_scores in type_breakdown.items():
            if qt_scores:
                type_stats[qt] = {
                    "count": len(qt_scores),
                    "mean_score": round(sum(qt_scores) / len(qt_scores), 4),
                    "perfect_rate": round(sum(1 for s in qt_scores if s == 1.0) / len(qt_scores), 4),
                    "incorrect_rate": round(sum(1 for s in qt_scores if s < 0) / len(qt_scores), 4),
                }

        # Per-popularity-tier breakdown
        tier_breakdown: Dict[str, List[float]] = {t: [] for t in ENTITY_POPULARITY_TIERS}
        for r in results:
            tier = r.get("entity_popularity", "tail")
            tier_breakdown.setdefault(tier, []).append(r["crag_score"])

        tier_stats = {}
        for tier, t_scores in tier_breakdown.items():
            if t_scores:
                tier_stats[tier] = {
                    "count": len(t_scores),
                    "mean_score": round(sum(t_scores) / len(t_scores), 4),
                }

        # Label distribution
        label_counts: Dict[str, int] = {
            "perfect": 0,
            "acceptable": 0,
            "missing": 0,
            "incorrect": 0,
        }
        for r in results:
            label = r.get("crag_label", "incorrect")
            label_counts[label] = label_counts.get(label, 0) + 1

        return {
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
            "task_type": task_type,
            "total_cases": len(results),
            "aggregate_crag_score": round(aggregate_score, 4),
            "label_distribution": label_counts,
            "by_question_type": type_stats,
            "by_entity_popularity": tier_stats,
            "results": results,
            "status": "completed",
        }

    def _empty_campaign_result(
        self,
        campaign_id: str,
        campaign_name: str,
        task_type: str,
        test_set_path: str,
    ) -> Dict[str, Any]:
        """Return a valid but empty campaign result (no test cases)."""
        return {
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
            "task_type": task_type,
            "total_cases": 0,
            "aggregate_crag_score": 0.0,
            "label_distribution": {
                "perfect": 0,
                "acceptable": 0,
                "missing": 0,
                "incorrect": 0,
            },
            "by_question_type": {},
            "by_entity_popularity": {},
            "results": [],
            "status": "completed",
        }

    # ------------------------------------------------------------------
    # Private helpers — DB persistence
    # ------------------------------------------------------------------

    def _persist_campaign(
        self,
        campaign_id: str,
        campaign_name: str,
        task_type: str,
        test_set_path: str,
        total_cases: int,
        aggregate_score: float,
        results: List[Dict[str, Any]],
        summary: Dict[str, Any],
    ) -> None:
        """Persist campaign and individual results to ICDEV™ DB."""
        from tools.db.storage import get_connection

        db_path = BASE_DIR / "data" / "icdev.db"
        with get_connection(db_path) as conn:
            _ensure_tables(conn)
            now = datetime.now(timezone.utc).isoformat()

            # Insert campaign record
            details_payload = {
                "label_distribution": summary.get("label_distribution", {}),
                "by_question_type": summary.get("by_question_type", {}),
                "by_entity_popularity": summary.get("by_entity_popularity", {}),
            }
            conn.execute(
                """INSERT OR REPLACE INTO rag_evaluation_campaigns
                   (id, name, scoring_mode, test_set_path, task_type,
                    total_cases, aggregate_crag_score, status, details, completed_at)
                   VALUES (%s, %s, 'crag', %s, %s, %s, %s, 'completed', %s, %s)""",
                (
                    campaign_id,
                    campaign_name,
                    str(test_set_path),
                    task_type,
                    total_cases,
                    aggregate_score,
                    json.dumps(details_payload),
                    now,
                ),
            )

            # Insert individual results
            for r in results:
                conn.execute(
                    """INSERT OR REPLACE INTO rag_evaluation_results
                       (id, campaign_id, query, question_type, entity_popularity,
                        crag_score, answer, ground_truth, is_false_premise, details)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        r["id"],
                        campaign_id,
                        r["query"],
                        r["question_type"],
                        r["entity_popularity"],
                        r["crag_score"],
                        r["answer"],
                        r["ground_truth"],
                        1 if r["is_false_premise"] else 0,
                        json.dumps({"label": r["crag_label"], "reasoning": r["reasoning"]}),
                    ),
                )
            conn.commit()

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------

    def _load_config(self) -> Dict[str, Any]:
        """Load CRAG evaluation settings from rag_config.yaml if it exists."""
        config_path = BASE_DIR / "args" / "rag_config.yaml"
        if not config_path.exists():
            return {}
        try:
            import yaml

            with open(config_path, encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
            return raw.get("evaluation", {}).get("crag", {})
        except Exception as exc:
            logger.debug("Failed to load rag_config.yaml: %s", exc)
            return {}


# ---------------------------------------------------------------------------
# DB schema helpers
# ---------------------------------------------------------------------------


def _ensure_tables(conn) -> None:
    """Create CRAG evaluation tables if they do not exist."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS rag_evaluation_campaigns (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            scoring_mode TEXT DEFAULT 'both'
                CHECK(scoring_mode IN ('ragas','crag','both')),
            test_set_path TEXT,
            task_type TEXT DEFAULT 'T1'
                CHECK(task_type IN ('T1','T2','T3')),
            total_cases INTEGER DEFAULT 0,
            aggregate_crag_score REAL,
            aggregate_ragas_score REAL,
            status TEXT DEFAULT 'running'
                CHECK(status IN ('running','completed','failed')),
            details TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS rag_evaluation_results (
            id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL,
            query TEXT NOT NULL,
            question_type TEXT
                CHECK(question_type IN (
                    'simple','simple_condition','set','comparison',
                    'aggregation','multi_hop','post_processing','false_premise'
                )),
            entity_popularity TEXT
                CHECK(entity_popularity IN ('head','torso','tail')),
            crag_score REAL,
            ragas_ndcg REAL,
            ragas_mrr REAL,
            ragas_faithfulness REAL,
            ragas_relevancy REAL,
            answer TEXT,
            ground_truth TEXT,
            is_false_premise INTEGER DEFAULT 0,
            details TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.commit()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CRAG-inspired RAG evaluation framework (D-RAG-23).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--benchmark-crag",
        action="store_true",
        help="Run a full CRAG benchmark campaign against a test set.",
    )
    mode.add_argument(
        "--classify-question",
        action="store_true",
        help="Classify a single query into one of the 8 CRAG question types.",
    )
    mode.add_argument(
        "--score",
        action="store_true",
        help="Score a single answer against a ground-truth string.",
    )

    # Benchmark options
    parser.add_argument(
        "--test-set",
        metavar="PATH",
        help="Path to JSON test set file (required for --benchmark-crag).",
    )
    parser.add_argument(
        "--task-type",
        default="T1",
        choices=["T1", "T2", "T3"],
        help="CRAG task type (default: T1).",
    )
    parser.add_argument(
        "--campaign-name",
        default="",
        help="Optional human-readable campaign name.",
    )

    # Classify options
    parser.add_argument("--query", help="Query text (for --classify-question).")
    parser.add_argument(
        "--context",
        default="",
        help="Optional context for question-type classification.",
    )

    # Score options
    parser.add_argument("--answer", help="System answer to score (for --score).")
    parser.add_argument("--ground-truth", help="Reference ground-truth answer (for --score).")
    parser.add_argument(
        "--question-type",
        default="simple",
        choices=QUESTION_TYPES,
        help="Question type hint for scoring (default: simple).",
    )
    parser.add_argument(
        "--false-premise",
        action="store_true",
        help="Flag the question as having a false premise.",
    )

    # Output options
    parser.add_argument("--json", action="store_true", help="Output results as JSON.")
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Exit with code 1 if aggregate CRAG score < 0.3 (CI gate).",
    )

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    scorer = CRAGScorer()

    # ------------------------------------------------------------------
    # Mode: --classify-question
    # ------------------------------------------------------------------
    if args.classify_question:
        if not args.query:
            parser.error("--classify-question requires --query")
        result = scorer.classify_question_type(
            query=args.query,
            context=args.context or "",
        )
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(
                f"Question type : {result['question_type']}\n"
                f"Confidence    : {result['confidence']:.2f}\n"
                f"Method        : {result['method']}"
            )
        return

    # ------------------------------------------------------------------
    # Mode: --score
    # ------------------------------------------------------------------
    if args.score:
        if args.answer is None:
            parser.error("--score requires --answer")
        if args.ground_truth is None:
            parser.error("--score requires --ground-truth")
        result = scorer.score_answer(
            answer=args.answer,
            ground_truth=args.ground_truth,
            question_type=args.question_type,
            is_false_premise=args.false_premise,
        )
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Score     : {result['score']}\nLabel     : {result['label']}\nReasoning : {result['reasoning']}")
        return

    # ------------------------------------------------------------------
    # Mode: --benchmark-crag
    # ------------------------------------------------------------------
    if args.benchmark_crag:
        if not args.test_set:
            parser.error("--benchmark-crag requires --test-set")
        runner = CRAGBenchmarkRunner()
        summary = runner.run_campaign(
            test_set_path=args.test_set,
            task_type=args.task_type,
            campaign_name=args.campaign_name,
        )

        if args.json:
            # Exclude per-case results from top-level JSON for brevity
            output = {k: v for k, v in summary.items() if k != "results"}
            output["result_count"] = len(summary.get("results", []))
            print(json.dumps(output, indent=2))
        else:
            score = summary["aggregate_crag_score"]
            print(f"Campaign      : {summary['campaign_name']}")
            print(f"Task Type     : {summary['task_type']}")
            print(f"Total Cases   : {summary['total_cases']}")
            print(f"CRAG Score    : {score:.4f}")
            print(f"Status        : {summary['status']}")
            print("\nLabel Distribution:")
            for label, count in summary.get("label_distribution", {}).items():
                print(f"  {label:12s} : {count}")
            print("\nBy Question Type:")
            for qt, stats in summary.get("by_question_type", {}).items():
                print(
                    f"  {qt:20s} : mean={stats['mean_score']:.3f}"
                    f"  n={stats['count']}"
                    f"  incorrect_rate={stats['incorrect_rate']:.1%}"
                )

        if args.gate:
            threshold = _GATE_THRESHOLD
            agg = summary.get("aggregate_crag_score", 0.0)
            if agg < threshold:
                sys.stderr.write(f"GATE FAIL: aggregate CRAG score {agg:.4f} < {threshold}\n")
                sys.exit(1)
            print(
                f"\nGATE PASS: aggregate CRAG score {agg:.4f} >= {threshold}",
                file=sys.stderr,
            )


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    main()
