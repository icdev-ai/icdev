#!/usr/bin/env python3
# CUI // SP-CTI
"""RAGAS-style RAG evaluation framework (D-RAG-22).

Metrics:
- ndcg_at_k: Normalized Discounted Cumulative Gain (deterministic)
- mrr: Mean Reciprocal Rank (deterministic)
- context_precision: Are retrieved chunks relevant? (LLM-as-judge)
- faithfulness: Is the answer grounded in context? (LLM-as-judge)
- answer_relevancy: Does the answer address the query? (LLM-as-judge)

Deterministic metrics (NDCG, MRR) are always available (air-gap safe).
LLM metrics use qwen3 via scanner_function (no Claude review).

Usage:
    python tools/rag/evaluator.py --benchmark --test-set data/rag/evaluation_set.json --json
    python tools/rag/evaluator.py --evaluate-retrieval --query "AC-2" --expected-ids "chunk-1,chunk-2" --json
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.rag.vector_store_provider import SearchResult

logger = get_logger("icdev.rag.evaluator")

BASE_DIR = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Module-level fallback constants — all overridable from args/rag_config.yaml
# under evaluator.anomaly_detection.  Change config, not code.
# ---------------------------------------------------------------------------
_DEFAULT_K          = 5      # default NDCG@k / top_k retrieval cutoff
_LLM_MAX_TOKENS     = 256    # max tokens for LLM-as-judge calls
_LLM_TEMPERATURE    = 0.0    # temperature for deterministic LLM judging
_LLM_CHUNK_CHARS    = 300    # per-chunk chars sent to context_precision judge
_LLM_CONTEXT_CHARS  = 2000   # context chars sent to faithfulness judge
_LLM_ANSWER_CHARS   = 1000   # answer chars sent to faithfulness/relevancy judges
_SCORE_PRECISION    = 4      # decimal places for rounded metric scores

# Anomaly-detection quality floors — a retrieval run scoring below these is
# flagged as a regression.  When enough history exists they are recomputed
# adaptively as (mean − k·stddev) of past runs; otherwise these are used.
_NDCG_ANOMALY_FLOOR = 0.30   # NDCG@k below this floor is flagged as anomalous
_MRR_ANOMALY_FLOOR  = 0.30   # MRR below this floor is flagged as anomalous
_ANOMALY_STDDEV_K   = 2.0    # flag runs below mean − k·stddev of history


def _compute_eval_anomaly_thresholds(anomaly_cfg: "dict | None" = None) -> dict:
    """Compute adaptive retrieval-quality floors from historical evaluation data.

    Reads ragas_ndcg / ragas_mrr from rag_evaluation_results and derives a
    statistical lower bound (mean − k·stddev) for each metric, so the definition
    of "low quality" tracks the corpus instead of a frozen magic number.

    Falls back to module-level floors when fewer than ``min_samples`` rows exist
    or anomaly detection is disabled.
    """
    cfg = anomaly_cfg or {}
    defaults = {
        "ndcg_floor": cfg.get("fallback_ndcg_floor", _NDCG_ANOMALY_FLOOR),
        "mrr_floor": cfg.get("fallback_mrr_floor", _MRR_ANOMALY_FLOOR),
        "computed": False,
    }
    if not cfg.get("enabled", True):
        return defaults

    min_samples = cfg.get("min_samples", 30)
    stddev_k    = float(cfg.get("stddev_k", _ANOMALY_STDDEV_K))
    bounds      = cfg.get("adaptive_bounds", {})
    floor_min   = float(bounds.get("floor_min", 0.05))
    floor_max   = float(bounds.get("floor_max", 0.80))

    conn = None
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        # Population mean / stddev via E[x²] − E[x]² (SQLite has no STDDEV()).
        row = conn.execute(
            "SELECT "
            "AVG(ragas_ndcg) AS ndcg_mean, AVG(ragas_ndcg * ragas_ndcg) AS ndcg_sq, "
            "AVG(ragas_mrr) AS mrr_mean,  AVG(ragas_mrr * ragas_mrr) AS mrr_sq, "
            "COUNT(*) AS n "
            "FROM rag_evaluation_results "
            "WHERE ragas_ndcg IS NOT NULL AND ragas_mrr IS NOT NULL"
        ).fetchone()
        if row:
            n = row["n"] if isinstance(row, dict) else row[4]
            if n and n >= min_samples:
                def _get(key, idx):
                    return float((row[key] if isinstance(row, dict) else row[idx]) or 0.0)

                ndcg_mean, ndcg_sq = _get("ndcg_mean", 0), _get("ndcg_sq", 1)
                mrr_mean, mrr_sq   = _get("mrr_mean", 2), _get("mrr_sq", 3)
                ndcg_std = math.sqrt(max(0.0, ndcg_sq - ndcg_mean * ndcg_mean))
                mrr_std  = math.sqrt(max(0.0, mrr_sq - mrr_mean * mrr_mean))
                ndcg_floor = max(floor_min, min(floor_max, ndcg_mean - stddev_k * ndcg_std))
                mrr_floor  = max(floor_min, min(floor_max, mrr_mean - stddev_k * mrr_std))
                return {
                    "ndcg_floor": round(ndcg_floor, 3),
                    "mrr_floor": round(mrr_floor, 3),
                    "computed": True,
                    "n_samples": n,
                    "ndcg_mean": round(ndcg_mean, 3),
                    "mrr_mean": round(mrr_mean, 3),
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


# ---------------------------------------------------------------------------
# Deterministic metrics (air-gap safe, no LLM needed)
# ---------------------------------------------------------------------------


def ndcg_at_k(
    retrieved_ids: List[str],
    relevant_ids: List[str],
    k: int = _DEFAULT_K,
) -> float:
    """Normalized Discounted Cumulative Gain at k.

    Args:
        retrieved_ids: Ordered list of retrieved chunk IDs.
        relevant_ids: Set of ground-truth relevant chunk IDs.
        k: Cutoff rank.

    Returns:
        NDCG@k score in [0, 1].
    """
    if not relevant_ids:
        return 0.0

    relevant_set = set(relevant_ids)

    # DCG: sum of 1/log2(rank+1) for relevant items
    dcg = 0.0
    for i, rid in enumerate(retrieved_ids[:k]):
        if rid in relevant_set:
            dcg += 1.0 / math.log2(i + 2)  # rank is 1-indexed

    # Ideal DCG: all relevant items at top positions
    ideal_count = min(len(relevant_ids), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_count))

    return dcg / idcg if idcg > 0 else 0.0


def mrr(
    retrieved_ids: List[str],
    relevant_ids: List[str],
) -> float:
    """Mean Reciprocal Rank.

    Args:
        retrieved_ids: Ordered list of retrieved chunk IDs.
        relevant_ids: Set of ground-truth relevant chunk IDs.

    Returns:
        MRR score (1/rank of first relevant result, or 0).
    """
    relevant_set = set(relevant_ids)
    for i, rid in enumerate(retrieved_ids):
        if rid in relevant_set:
            return 1.0 / (i + 1)
    return 0.0


# ---------------------------------------------------------------------------
# LLM-as-judge metrics (requires Ollama qwen3)
# ---------------------------------------------------------------------------


def _llm_judge(prompt: str, system: str = "") -> str:
    """Invoke qwen3 as LLM judge via scanner_function."""
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system,
            max_tokens=_LLM_MAX_TOKENS,
            temperature=_LLM_TEMPERATURE,
            classification="CUI",
        )
        response = router.invoke("rag_evaluate", request)
        return response.content.strip()
    except Exception as exc:
        logger.debug("LLM judge failed: %s", exc)
        return ""


def _safe_extract_json(text: str, container: str = "{") -> Any:
    """Safely extract JSON from LLM response text.

    SEC: Uses balanced bracket matching instead of fragile rindex() which
    could match wrong brackets in malformed responses.

    Args:
        text: Raw LLM response text.
        container: '{' for objects, '[' for arrays.

    Returns:
        Parsed JSON value, or None on failure.
    """
    if not text:
        return None
    open_br = container
    close_br = "}" if container == "{" else "]"
    start = text.find(open_br)
    if start == -1:
        return None
    # Find matching close bracket by counting nesting
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


def context_precision(
    query: str,
    retrieved_chunks: List[str],
) -> float:
    """Evaluate if retrieved chunks are relevant to the query (LLM judge).

    Returns a score in [0, 1] — fraction of chunks deemed relevant.
    """
    if not retrieved_chunks:
        return 0.0

    system = (
        "You are a relevance evaluator. For each numbered chunk, output 1 if relevant "
        "to the query or 0 if not. Return ONLY a JSON array of 0/1 values."
    )
    chunks_text = "\n".join(f"[{i}] {c[:_LLM_CHUNK_CHARS]}" for i, c in enumerate(retrieved_chunks))
    prompt = f"Query: {query}\n\nChunks:\n{chunks_text}\n\nReturn JSON array of 0/1:"

    result = _llm_judge(prompt, system)
    arr = _safe_extract_json(result, "[")
    if isinstance(arr, list) and arr:
        return sum(1 for v in arr if v == 1) / len(retrieved_chunks)
    return 0.0


def faithfulness(
    query: str,
    context: str,
    answer: str,
) -> float:
    """Evaluate if the answer is grounded in the provided context (LLM judge).

    Returns a score in [0, 1].
    """
    if not answer or not context:
        return 0.0

    system = (
        "You are a faithfulness evaluator. Score how well the answer is grounded "
        'in the provided context. Return ONLY a JSON object: {"score": 0.0-1.0, "reason": "..."}'
    )
    prompt = (
        f"Query: {query}\n\nContext:\n{context[:_LLM_CONTEXT_CHARS]}\n\n"
        f"Answer:\n{answer[:_LLM_ANSWER_CHARS]}\n\nEvaluate faithfulness:"
    )

    result = _llm_judge(prompt, system)
    parsed = _safe_extract_json(result, "{")
    if isinstance(parsed, dict):
        return float(parsed.get("score", 0.0))
    return 0.0


def answer_relevancy(
    query: str,
    answer: str,
) -> float:
    """Evaluate if the answer addresses the query (LLM judge).

    Returns a score in [0, 1].
    """
    if not answer:
        return 0.0

    system = (
        "You are a relevancy evaluator. Score how well the answer addresses the query. "
        'Return ONLY a JSON object: {"score": 0.0-1.0, "reason": "..."}'
    )
    prompt = f"Query: {query}\n\nAnswer:\n{answer[:_LLM_ANSWER_CHARS]}\n\nEvaluate relevancy:"

    result = _llm_judge(prompt, system)
    parsed = _safe_extract_json(result, "{")
    if isinstance(parsed, dict):
        return float(parsed.get("score", 0.0))
    return 0.0


# ---------------------------------------------------------------------------
# RAGEvaluator
# ---------------------------------------------------------------------------


class RAGEvaluator:
    """RAGAS-style evaluation for RAG pipeline quality (D-RAG-22)."""

    def __init__(self, config: Optional[dict] = None):
        self._config = config or {}
        self._anomaly_cfg = self._load_anomaly_config()
        self._anomaly_thresholds = _compute_eval_anomaly_thresholds(self._anomaly_cfg)

    def _load_anomaly_config(self) -> dict:
        """Load evaluator.anomaly_detection settings from rag_config.yaml."""
        if "anomaly_detection" in self._config:
            return self._config["anomaly_detection"] or {}
        config_path = BASE_DIR / "args" / "rag_config.yaml"
        if not config_path.exists():
            return {}
        try:
            import yaml

            with open(config_path, encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
            return raw.get("evaluator", {}).get("anomaly_detection", {})
        except Exception as exc:
            logger.debug("Failed to load rag_config.yaml: %s", exc)
            return {}

    def flag_anomalies(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Flag a metric result as a quality anomaly against adaptive floors.

        Returns ``{"anomalous": bool, "reasons": [...]}`` — empty/clean when the
        relevant deterministic metrics meet or exceed the (adaptive) floors.
        """
        th = self._anomaly_thresholds
        reasons: List[str] = []
        ndcg = metrics.get("ndcg_at_k")
        mrr_score = metrics.get("mrr")
        if ndcg is not None and ndcg < th["ndcg_floor"]:
            reasons.append(f"ndcg_at_k {ndcg} < floor {th['ndcg_floor']}")
        if mrr_score is not None and mrr_score < th["mrr_floor"]:
            reasons.append(f"mrr {mrr_score} < floor {th['mrr_floor']}")
        return {"anomalous": bool(reasons), "reasons": reasons}

    def evaluate_retrieval(
        self,
        query: str,
        results: List[SearchResult],
        ground_truth_ids: Optional[List[str]] = None,
        k: int = _DEFAULT_K,
    ) -> Dict[str, Any]:
        """Evaluate retrieval quality (deterministic + optional LLM metrics).

        Args:
            query: Search query.
            results: Retrieved search results.
            ground_truth_ids: Expected relevant chunk IDs (for NDCG/MRR).
            k: Cutoff rank for NDCG.

        Returns:
            Dict with metric scores.
        """
        retrieved_ids = [r.chunk_id for r in results]
        metrics: Dict[str, Any] = {
            "query": query,
            "retrieved_count": len(results),
        }

        # Deterministic metrics (always available)
        if ground_truth_ids:
            metrics["ndcg_at_k"] = round(ndcg_at_k(retrieved_ids, ground_truth_ids, k=k), _SCORE_PRECISION)
            metrics["mrr"] = round(mrr(retrieved_ids, ground_truth_ids), _SCORE_PRECISION)
            # Anomaly detection: flag retrieval-quality regressions vs adaptive floors
            anomaly = self.flag_anomalies(metrics)
            if anomaly["anomalous"]:
                metrics["anomaly"] = anomaly

        # LLM metrics (optional, require qwen3)
        chunks = [r.content for r in results]
        try:
            metrics["context_precision"] = round(context_precision(query, chunks), _SCORE_PRECISION)
        except Exception:
            pass

        return metrics

    def evaluate_generation(
        self,
        query: str,
        context: str,
        answer: str,
        ground_truth: str = "",
        scoring_mode: str = "ragas",
    ) -> Dict[str, Any]:
        """Evaluate generation quality (LLM-as-judge + optional CRAG metrics).

        Args:
            query: Original query.
            context: Injected RAG context.
            answer: LLM-generated answer.
            ground_truth: Expected answer (required for CRAG scoring).
            scoring_mode: "ragas", "crag", or "both" (D-RAG-23).

        Returns:
            Dict with faithfulness, answer_relevancy, and optional crag_score.
        """
        metrics: Dict[str, Any] = {"query": query}

        if scoring_mode in ("ragas", "both"):
            try:
                metrics["faithfulness"] = round(faithfulness(query, context, answer), _SCORE_PRECISION)
            except Exception:
                pass

            try:
                metrics["answer_relevancy"] = round(answer_relevancy(query, answer), _SCORE_PRECISION)
            except Exception:
                pass

        if scoring_mode in ("crag", "both") and ground_truth:
            try:
                from tools.rag.crag_evaluator import CRAGScorer

                scorer = CRAGScorer()
                crag_result = scorer.score_answer(answer, ground_truth)
                metrics["crag_score"] = crag_result.get("score", 0.0)
                metrics["crag_label"] = crag_result.get("label", "")
            except ImportError:
                pass

        return metrics

    def run_benchmark(self, test_set_path: Optional[str] = None) -> Dict[str, Any]:
        """Run full benchmark against a test set.

        Test set JSON format:
        [
            {
                "query": "...",
                "expected_chunk_ids": ["chunk-1", "chunk-2"],
                "expected_answer": "..." (optional)
            }
        ]

        Returns:
            Aggregate metrics across all test cases.
        """
        path = Path(
            test_set_path
            or self._config.get(
                "test_set_path",
                str(BASE_DIR / "data" / "rag" / "evaluation_set.json"),
            )
        )
        if not path.exists():
            return {"error": f"Test set not found: {path}", "results": []}

        try:
            with open(path, "r", encoding="utf-8") as f:
                test_cases = json.load(f)
        except Exception as exc:
            return {"error": str(exc), "results": []}

        if not isinstance(test_cases, list):
            return {"error": "Test set must be a JSON array", "results": []}

        try:
            from tools.rag.retriever import RAGRetriever

            retriever = RAGRetriever()
        except ImportError:
            return {"error": "RAG subsystem not available", "results": []}

        results_list = []
        agg_ndcg = []
        agg_mrr = []
        agg_precision = []
        anomaly_count = 0

        for tc in test_cases:
            query = tc.get("query", "")
            expected_ids = tc.get("expected_chunk_ids", [])
            if not query:
                continue

            search_results = retriever.search(query=query, top_k=_DEFAULT_K)
            eval_result = self.evaluate_retrieval(
                query=query,
                results=search_results,
                ground_truth_ids=expected_ids,
            )

            results_list.append(eval_result)
            if "ndcg_at_k" in eval_result:
                agg_ndcg.append(eval_result["ndcg_at_k"])
            if "mrr" in eval_result:
                agg_mrr.append(eval_result["mrr"])
            if "context_precision" in eval_result:
                agg_precision.append(eval_result["context_precision"])
            if eval_result.get("anomaly", {}).get("anomalous"):
                anomaly_count += 1

        return {
            "classification": "CUI // SP-CTI",
            "test_cases": len(results_list),
            "aggregate": {
                "avg_ndcg_at_5": round(sum(agg_ndcg) / max(len(agg_ndcg), 1), _SCORE_PRECISION)
                if agg_ndcg
                else None,
                "avg_mrr": round(sum(agg_mrr) / max(len(agg_mrr), 1), _SCORE_PRECISION) if agg_mrr else None,
                "avg_context_precision": round(
                    sum(agg_precision) / max(len(agg_precision), 1), _SCORE_PRECISION
                )
                if agg_precision
                else None,
            },
            "anomaly_detection": {
                "thresholds": self._anomaly_thresholds,
                "anomalous_cases": anomaly_count,
            },
            "results": results_list,
        }


def main():
    parser = argparse.ArgumentParser(description="RAG Evaluator (D-RAG-22)")
    parser.add_argument("--benchmark", action="store_true", help="Run full benchmark")
    parser.add_argument("--test-set", help="Path to test set JSON")
    parser.add_argument("--evaluate-retrieval", action="store_true", help="Evaluate single retrieval")
    parser.add_argument("--query", help="Query for single evaluation")
    parser.add_argument("--expected-ids", help="Comma-separated expected chunk IDs")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    evaluator = RAGEvaluator()

    if args.benchmark:
        result = evaluator.run_benchmark(test_set_path=args.test_set)
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            print(f"Test cases: {result.get('test_cases', 0)}")
            agg = result.get("aggregate", {})
            for k, v in agg.items():
                if v is not None:
                    print(f"  {k}: {v:.4f}")

    elif args.evaluate_retrieval and args.query:
        from tools.rag.retriever import RAGRetriever

        retriever = RAGRetriever()
        results = retriever.search(query=args.query, top_k=5)
        expected = args.expected_ids.split(",") if args.expected_ids else []
        eval_result = evaluator.evaluate_retrieval(
            query=args.query,
            results=results,
            ground_truth_ids=expected,
        )
        if args.json_output:
            print(json.dumps(eval_result, indent=2))
        else:
            for k, v in eval_result.items():
                print(f"  {k}: {v}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
