#!/usr/bin/env python3
# CUI // SP-CTI
"""Query classifier — 4-label taxonomy for RAG evaluation (D-RAG-24).

Classifies query-context pairs into taxonomy labels from "Know Your RAG"
(arxiv 2411.19710): fact_single, summary, reasoning, unanswerable.

Usage:
    python tools/rag/query_classifier.py --classify --query "What is AC-2?" --json
    python tools/rag/query_classifier.py --classify-batch --input data/rag/queries.json --json
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.rag.query_classifier")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Module-level fallback constants — all overridable from args/rag_config.yaml
# under query_classifier.anomaly_detection.  Change config, not code.
# ---------------------------------------------------------------------------
_UNANSWERABLE_OVERLAP_THRESHOLD = 0.10  # overlap_ratio below this → unanswerable
_MIN_QUERY_WORDS                = 3     # minimum distinct words to check unanswerability
_HEURISTIC_CONFIDENCE_UNANSWERABLE = 0.65
_HEURISTIC_CONFIDENCE_HIGH      = 0.80  # confidence for pattern-matched labels
_HEURISTIC_CONFIDENCE_DEFAULT   = 0.50  # fallback when no pattern matches
_HEURISTIC_PROMOTE_THRESHOLD    = 0.75  # minimum heuristic confidence to skip fallback
_LLM_CONTEXT_CHARS              = 1500  # max context chars forwarded to LLM
_LLM_TEMPERATURE                = 0.10
_LLM_MAX_TOKENS                 = 200
_LLM_CONFIDENCE                 = 0.90
_BATCH_MAX_WORKERS              = 4
_EMPTY_QUERY_CONFIDENCE         = 1.0


def _compute_classification_thresholds(anomaly_cfg: "dict | None" = None) -> dict:
    """Compute adaptive classification thresholds from historical query accuracy data.

    Reads from rag_evaluation_results to calibrate the overlap_ratio threshold:
    - If false-positive rate on 'unanswerable' is high → increase threshold (stricter)
    - If false-negative rate is high → decrease threshold (more sensitive)

    Falls back to module-level defaults when fewer than min_samples rows exist or
    anomaly detection is disabled.
    """
    cfg = anomaly_cfg or {}
    if not cfg.get("enabled", True):
        return {
            "unanswerable_threshold": cfg.get("fallback_overlap_threshold", _UNANSWERABLE_OVERLAP_THRESHOLD),
            "heuristic_promote_threshold": cfg.get("fallback_promote_threshold", _HEURISTIC_PROMOTE_THRESHOLD),
            "computed": False,
        }

    min_samples  = cfg.get("min_samples", 20)
    fallback_ot  = cfg.get("fallback_overlap_threshold", _UNANSWERABLE_OVERLAP_THRESHOLD)
    fallback_pt  = cfg.get("fallback_promote_threshold", _HEURISTIC_PROMOTE_THRESHOLD)
    bounds       = cfg.get("adaptive_bounds", {})
    ot_floor     = float(bounds.get("overlap_floor", 0.05))
    ot_ceil      = float(bounds.get("overlap_ceil", 0.30))

    conn = None
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT "
            "SUM(CASE WHEN predicted_label = 'unanswerable' AND ground_truth_label != 'unanswerable' THEN 1 ELSE 0 END) * 1.0 "
            "  / NULLIF(SUM(CASE WHEN predicted_label = 'unanswerable' THEN 1 ELSE 0 END), 0) AS fp_rate, "
            "SUM(CASE WHEN predicted_label != 'unanswerable' AND ground_truth_label = 'unanswerable' THEN 1 ELSE 0 END) * 1.0 "
            "  / NULLIF(SUM(CASE WHEN ground_truth_label = 'unanswerable' THEN 1 ELSE 0 END), 0) AS fn_rate, "
            "COUNT(*) AS n "
            "FROM rag_evaluation_results "
            "WHERE predicted_label IS NOT NULL AND ground_truth_label IS NOT NULL"
        ).fetchone()
        if row:
            n = row["n"] if isinstance(row, dict) else row[2]
            if n and n >= min_samples:
                fp_rate = float(row["fp_rate"] if isinstance(row, dict) else row[0]) or 0.0
                fn_rate = float(row["fn_rate"] if isinstance(row, dict) else row[1]) or 0.0
                # Too many false positives → tighten threshold (higher = fewer flags)
                # Too many false negatives → loosen threshold (lower = more flags)
                adjustment = (fp_rate - fn_rate) * 0.05
                new_ot = max(ot_floor, min(ot_ceil, fallback_ot + adjustment))
                return {
                    "unanswerable_threshold": round(new_ot, 3),
                    "heuristic_promote_threshold": fallback_pt,
                    "computed": True,
                    "n_samples": n,
                    "fp_rate": round(fp_rate, 3),
                    "fn_rate": round(fn_rate, 3),
                }
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return {
        "unanswerable_threshold": fallback_ot,
        "heuristic_promote_threshold": fallback_pt,
        "computed": False,
    }


TAXONOMY_LABELS: List[str] = ["fact_single", "summary", "reasoning", "unanswerable"]

LABEL_DESCRIPTIONS: Dict[str, str] = {
    "fact_single": (
        "A question that has a single, concise factual answer retrievable from a specific "
        "passage (e.g., 'What is AC-2?', 'Who is responsible for ISSO?', 'When was NIST 800-53 published?'). "
        "Answer is typically a single sentence or short phrase."
    ),
    "summary": (
        "A question requiring synthesis of multiple passages or an overview of a topic "
        "(e.g., 'Summarize the FedRAMP authorization process', 'Describe the key controls for IL5', "
        "'Give an overview of CMMC Level 2'). Answer requires aggregating information."
    ),
    "reasoning": (
        "A question requiring explanation, inference, or causal analysis beyond retrieving facts "
        "(e.g., 'Why does FedRAMP require continuous monitoring?', 'How does zero trust prevent lateral movement?', "
        "'What would happen if MFA is disabled?', 'Explain the rationale behind NIST 800-171'). "
        "Answer requires logical deduction or interpretation."
    ),
    "unanswerable": (
        "A question whose answer cannot be found in the provided context, or whose context contains "
        "no relevant information to ground a response. Indicates a retrieval failure or out-of-scope query."
    ),
}

# Heuristic keyword patterns for air-gap safe classification
_FACT_SINGLE_PATTERNS = re.compile(
    r"^(what is|what are|who is|who are|when did|when was|where is|where are|"
    r"which|how many|how much|what does .+ stand for|what version|what number|"
    r"define |what\s+\w+\s+is\b)",
    re.IGNORECASE,
)

_SUMMARY_PATTERNS = re.compile(
    r"\b(summarize|summary|overview|describe|list|enumerate|outline|explain all|"
    r"what are the (main|key|primary|core)|give an overview|provide an overview|"
    r"what (does|do) .+ cover|explain the (process|framework|approach|system))\b",
    re.IGNORECASE,
)

_REASONING_PATTERNS = re.compile(
    r"\b(why|how does|how do|explain\b|what (would|will) happen|"
    r"what (is|are) the (reason|rationale|purpose|benefit|advantage|disadvantage|risk|impact)|"
    r"what if|if .+ then|how (would|could)|compare|contrast|analyze|evaluate|"
    r"what (causes|caused)|how (can|should)|justify|elaborate|discuss)\b",
    re.IGNORECASE,
)

_LLM_CLASSIFY_SYSTEM_PROMPT = """You are a RAG evaluation expert. Classify query-context pairs into taxonomy labels.

Labels:
- fact_single: Single concise factual answer from a specific passage
- summary: Requires synthesizing multiple passages or providing an overview
- reasoning: Requires explanation, inference, or causal analysis
- unanswerable: Answer cannot be found in the provided context

Return ONLY valid JSON. No other text.
Output format: {"label": "...", "reasoning": "..."}"""


# ---------------------------------------------------------------------------
# Core classification logic
# ---------------------------------------------------------------------------


def _heuristic_classify(query: str, context: str = "") -> Dict[str, Any]:
    """Classify using deterministic heuristic rules (air-gap safe).

    Strategy:
        1. Match against reasoning patterns first (highest specificity).
        2. Match against summary patterns.
        3. Match against fact_single patterns.
        4. If context provided and no keyword overlap detected → unanswerable.
        5. Default to fact_single.
    """
    query_stripped = query.strip()

    # Check unanswerable first when context is provided
    if context:
        query_words = set(re.findall(r"\b\w{4,}\b", query_stripped.lower()))
        context_words = set(re.findall(r"\b\w{4,}\b", context.lower()))
        overlap = query_words & context_words
        overlap_ratio = len(overlap) / max(len(query_words), 1)
        if overlap_ratio < _UNANSWERABLE_OVERLAP_THRESHOLD and len(query_words) >= _MIN_QUERY_WORDS:
            return {
                "label": "unanswerable",
                "confidence": _HEURISTIC_CONFIDENCE_UNANSWERABLE,
                "method": "heuristic",
                "reasoning": (
                    f"Query keywords overlap with context is {overlap_ratio:.0%}, "
                    "below threshold — likely unanswerable from provided context."
                ),
            }

    if _REASONING_PATTERNS.search(query_stripped):
        return {
            "label": "reasoning",
            "confidence": _HEURISTIC_CONFIDENCE_HIGH,
            "method": "heuristic",
            "reasoning": "Query contains reasoning/causal keywords (why, how does, explain, compare, etc.).",
        }

    if _SUMMARY_PATTERNS.search(query_stripped):
        return {
            "label": "summary",
            "confidence": _HEURISTIC_CONFIDENCE_HIGH,
            "method": "heuristic",
            "reasoning": "Query contains summary/overview keywords (summarize, describe, list, overview, etc.).",
        }

    if _FACT_SINGLE_PATTERNS.match(query_stripped):
        return {
            "label": "fact_single",
            "confidence": _HEURISTIC_CONFIDENCE_HIGH,
            "method": "heuristic",
            "reasoning": "Query starts with factual interrogative pattern (what is, who, when, where, how many, etc.).",
        }

    return {
        "label": "fact_single",
        "confidence": _HEURISTIC_CONFIDENCE_DEFAULT,
        "method": "heuristic",
        "reasoning": "No strong pattern match — defaulting to fact_single.",
    }


def _llm_classify(query: str, context: str = "") -> Optional[Dict[str, Any]]:
    """Classify using scanner-tier LLM (qwen3.5, zero Claude tokens).

    Returns None if LLM is unavailable (graceful degradation).
    """
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        context_section = f"\n\nCONTEXT:\n{context[:_LLM_CONTEXT_CHARS]}" if context else ""
        user_content = (
            f"QUERY: {query}{context_section}\n\n"
            f"Classify into one of: {', '.join(TAXONOMY_LABELS)}.\n"
            'Return JSON: {"label": "...", "reasoning": "..."}'
        )

        router = LLMRouter()
        request = LLMRequest(
            messages=[
                {"role": "system", "content": _LLM_CLASSIFY_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=_LLM_TEMPERATURE,
            max_tokens=_LLM_MAX_TOKENS,
        )

        response = router.invoke("ft_pair_generation", request)
        if not response or not response.content:
            return None

        text = response.content.strip()

        # Strip markdown code fences if present
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.index("```", start) if "```" in text[start:] else len(text)
            text = text[start:end].strip()
        elif "```" in text:
            start = text.index("```") + 3
            end = text.index("```", start) if "```" in text[start:] else len(text)
            text = text[start:end].strip()

        parsed = json.loads(text)
        label = parsed.get("label", "").strip().lower()
        if label not in TAXONOMY_LABELS:
            return None

        return {
            "label": label,
            "confidence": _LLM_CONFIDENCE,
            "method": "llm",
            "reasoning": parsed.get("reasoning", "LLM classification."),
        }

    except (ImportError, json.JSONDecodeError, Exception):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_query(query: str, context: str = "") -> Dict[str, Any]:
    """Classify a single query-context pair into a taxonomy label.

    Tries LLM classification first (scanner-tier qwen3.5); falls back to
    deterministic heuristics for air-gap safe operation.

    Args:
        query: The user query string.
        context: Optional retrieved context text to check for answerability.

    Returns:
        Dict with keys: label, confidence, method, reasoning.
        label is one of TAXONOMY_LABELS.
    """
    if not query or not query.strip():
        return {
            "label": "unanswerable",
            "confidence": _EMPTY_QUERY_CONFIDENCE,
            "method": "default",
            "reasoning": "Empty query — cannot classify.",
        }

    # Strategy 1: LLM (scanner-tier, no Claude tokens)
    llm_result = _llm_classify(query, context)
    if llm_result is not None:
        return llm_result

    # Strategy 2: Heuristic (deterministic, air-gap safe)
    heuristic_result = _heuristic_classify(query, context)
    if heuristic_result["confidence"] >= _HEURISTIC_PROMOTE_THRESHOLD:
        return heuristic_result

    # Strategy 3: Default fallback
    if heuristic_result["confidence"] > 0.0:
        return heuristic_result

    return {
        "label": "fact_single",
        "confidence": 0.50,
        "method": "default",
        "reasoning": "All classification strategies returned low confidence — defaulting to fact_single.",
    }


def classify_batch(
    queries: List[Dict[str, Any]],
    parallel: bool = False,
) -> List[Dict[str, Any]]:
    """Classify a batch of query-context pairs.

    Args:
        queries: List of dicts, each with "query" (required) and optional "context".
        parallel: If True, use ThreadPoolExecutor for concurrent classification.

    Returns:
        List of classify_query results, one per input query dict.
    """
    if not queries:
        return []

    if parallel:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: List[Optional[Dict[str, Any]]] = [None] * len(queries)

        def _classify_indexed(idx: int, item: Dict[str, Any]) -> tuple:
            q = item.get("query", "")
            c = item.get("context", "")
            return idx, classify_query(q, c)

        with ThreadPoolExecutor(max_workers=_BATCH_MAX_WORKERS) as executor:
            futures = {executor.submit(_classify_indexed, i, item): i for i, item in enumerate(queries)}
            for future in as_completed(futures):
                try:
                    idx, result = future.result()
                    results[idx] = result
                except Exception as exc:
                    idx = futures[future]
                    results[idx] = {
                        "label": "unanswerable",
                        "confidence": 0.0,
                        "method": "error",
                        "reasoning": str(exc),
                    }

        return [r for r in results if r is not None]

    return [classify_query(item.get("query", ""), item.get("context", "")) for item in queries]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Query classifier — 4-label taxonomy for RAG evaluation (D-RAG-24).",
    )
    p.add_argument("--classify", action="store_true", help="Classify a single query.")
    p.add_argument("--classify-batch", action="store_true", help="Classify a batch of queries from JSON file.")
    p.add_argument("--query", help="Query string to classify.")
    p.add_argument("--context", default="", help="Optional retrieved context text.")
    p.add_argument("--input", help="Path to JSON file with list of {query, context} objects.")
    p.add_argument("--parallel", action="store_true", help="Use parallel execution for batch classification.")
    p.add_argument("--json", action="store_true", help="Output as JSON.")
    return p


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    parser = _build_parser()
    args = parser.parse_args()

    if args.classify:
        if not args.query:
            parser.error("--classify requires --query")
        result = classify_query(args.query, args.context or "")
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Label      : {result['label']}")
            print(f"Confidence : {result['confidence']:.2f}")
            print(f"Method     : {result['method']}")
            print(f"Reasoning  : {result['reasoning']}")
        return

    if args.classify_batch:
        if not args.input:
            parser.error("--classify-batch requires --input")
        input_path = Path(args.input)
        if not input_path.exists():
            print(json.dumps({"success": False, "error": f"File not found: {args.input}"}))
            sys.exit(1)
        with open(input_path, "r", encoding="utf-8") as fh:
            queries = json.load(fh)
        if not isinstance(queries, list):
            print(json.dumps({"success": False, "error": "Input must be a JSON array."}))
            sys.exit(1)
        results = classify_batch(queries, parallel=args.parallel)
        label_counts: Dict[str, int] = {}
        for r in results:
            label_counts[r["label"]] = label_counts.get(r["label"], 0) + 1
        output = {
            "success": True,
            "total": len(results),
            "label_distribution": label_counts,
            "results": results,
        }
        if args.json:
            print(json.dumps(output, indent=2))
        else:
            print(f"Classified {len(results)} queries:")
            for label, count in label_counts.items():
                print(f"  {label}: {count}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
