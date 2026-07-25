#!/usr/bin/env python3
# CUI // SP-CTI
"""RAG retrieval-quality baseline harness (rce-eval-01).

A repeatable benchmark so every later RCE (RAG Context Engineering) change —
contextual prefixes, RAPTOR hierarchy, quantization — is *measured*, not assumed.

Reuses the deterministic scoring in ``tools/rag/evaluator.py`` (``mrr``,
``ndcg_at_k``); it does NOT re-implement scoring. It adds ``recall@k`` and
``citation_hit_rate`` on top, which the evaluator does not provide.

Golden query set: ``args/rag/golden_query_set.yaml`` (compliance/NIST-heavy).
Each query declares expected hits by content SUBSTRING (and optionally exact
chunk_id / source_id) so the set survives re-indexing (chunk IDs change on
re-ingest) and stays comparable before/after a change.

Metrics (all in [0, 1], averaged over queries with >=1 target):
  - recall_at_k       : fraction of a query's expected targets found in top-k
  - mrr               : mean reciprocal rank of the first matching result
                        (reuses evaluator.mrr)
  - citation_hit_rate : fraction of queries with >=1 expected target in top-k
  - ndcg_at_k         : ranking quality of matched results (reuses
                        evaluator.ndcg_at_k)

PG-primary; runs unchanged on the SQLite fallback (retrieval backend is chosen
by the existing vector-store factory). Air-gap safe — no new dependencies.

Usage:
    python tools/rag/rag_benchmark.py --json
    python tools/rag/rag_benchmark.py --golden-set args/rag/golden_query_set.yaml --top-k 5 --json
    python tools/rag/rag_benchmark.py --baseline-out data/rag/rce_baseline.json --json
    python tools/rag/rag_benchmark.py --compare data/rag/rce_baseline.json --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.rag.evaluator import mrr as _mrr  # noqa: E402  (reuse — do not re-implement)
from tools.rag.evaluator import ndcg_at_k as _ndcg_at_k  # noqa: E402

# ---------------------------------------------------------------------------
# Module-level fallback constants
# ---------------------------------------------------------------------------
_DEFAULT_TOP_K = 5          # top-k cutoff for recall/ndcg/citation metrics
_SCORE_PRECISION = 4        # decimal places for reported metric scores
DEFAULT_GOLDEN_SET = BASE_DIR / "args" / "rag" / "golden_query_set.yaml"
DEFAULT_BASELINE = BASE_DIR / "data" / "rag" / "rce_baseline.json"


# ---------------------------------------------------------------------------
# Golden-set loading
# ---------------------------------------------------------------------------


def load_golden_set(path: str | Path | None = None) -> Dict[str, Any]:
    """Load and lightly validate a golden query set YAML file."""
    p = Path(path) if path else DEFAULT_GOLDEN_SET
    if not p.exists():
        raise FileNotFoundError(f"Golden query set not found: {p}")
    import yaml

    with open(p, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    queries = data.get("queries", [])
    if not isinstance(queries, list) or not queries:
        raise ValueError(f"Golden query set has no queries: {p}")
    return data


def _query_targets(expect: Dict[str, Any]) -> List[Dict[str, str]]:
    """Expand a query's ``expect`` block into a flat list of targets.

    Each target is ``{"kind": <chunk_id|source_id|substring>, "value": <str>}``.
    ``source_types`` is intentionally NOT a recall target (diagnostic only).
    """
    targets: List[Dict[str, str]] = []
    for cid in expect.get("chunk_ids", []) or []:
        targets.append({"kind": "chunk_id", "value": str(cid)})
    for sid in expect.get("source_ids", []) or []:
        targets.append({"kind": "source_id", "value": str(sid)})
    for sub in expect.get("substrings", []) or []:
        targets.append({"kind": "substring", "value": str(sub)})
    return targets


def _result_matches_target(result: Any, target: Dict[str, str]) -> bool:
    """Return True if a SearchResult (or dict) satisfies a single target."""
    kind, value = target["kind"], target["value"]
    if kind == "chunk_id":
        return _attr(result, "chunk_id") == value
    if kind == "source_id":
        return _attr(result, "source_id") == value
    if kind == "substring":
        content = _attr(result, "content") or ""
        return value.lower() in content.lower()
    return False


def _attr(obj: Any, name: str) -> Any:
    """Read an attribute from a SearchResult dataclass or a plain dict."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


# ---------------------------------------------------------------------------
# Per-query scoring
# ---------------------------------------------------------------------------


def score_query(
    results: List[Any],
    expect: Dict[str, Any],
    top_k: int = _DEFAULT_TOP_K,
) -> Dict[str, Any]:
    """Score one query's retrieval results against its expected targets.

    Returns a dict with recall_at_k, mrr, ndcg_at_k, hit (0/1), and counts.
    Reuses evaluator.mrr / evaluator.ndcg_at_k for the ranking metrics.
    """
    targets = _query_targets(expect)
    top = results[:top_k]

    # Build an ordered list of retrieved ids + the subset that matched ANY target.
    retrieved_ids: List[str] = []
    relevant_ids: List[str] = []
    for i, r in enumerate(top):
        rid = _attr(r, "chunk_id") or f"idx-{i}"
        retrieved_ids.append(rid)
        if any(_result_matches_target(r, t) for t in targets):
            relevant_ids.append(rid)

    # recall@k: how many distinct expected targets were satisfied in top-k.
    hit_targets = sum(1 for t in targets if any(_result_matches_target(r, t) for r in top))
    n_targets = len(targets)
    recall = hit_targets / n_targets if n_targets else 0.0

    mrr_score = _mrr(retrieved_ids, relevant_ids) if relevant_ids else 0.0
    ndcg_score = _ndcg_at_k(retrieved_ids, relevant_ids, k=top_k) if relevant_ids else 0.0

    return {
        "recall_at_k": round(recall, _SCORE_PRECISION),
        "mrr": round(mrr_score, _SCORE_PRECISION),
        "ndcg_at_k": round(ndcg_score, _SCORE_PRECISION),
        "hit": 1 if hit_targets > 0 else 0,
        "targets": n_targets,
        "targets_hit": hit_targets,
        "retrieved_count": len(results),
    }


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


class RAGBenchmark:
    """Runs the golden query set through a retriever and aggregates metrics."""

    def __init__(
        self,
        golden_set: Optional[Dict[str, Any]] = None,
        golden_set_path: str | Path | None = None,
        top_k: Optional[int] = None,
    ):
        self._golden = golden_set or load_golden_set(golden_set_path)
        self._top_k = top_k or int(self._golden.get("top_k", _DEFAULT_TOP_K))

    @property
    def top_k(self) -> int:
        return self._top_k

    def _default_retriever_search(self) -> Callable[[str, int], List[Any]]:
        """Build a search callable backed by the real RAGRetriever.

        Returns a function ``search(query, top_k) -> list``. If the RAG
        subsystem is unavailable the callable returns an empty list so the
        benchmark still produces a (zeroed) baseline rather than crashing.
        """
        try:
            from tools.rag.retriever import RAGRetriever

            retriever = RAGRetriever()

            def _search(query: str, k: int) -> List[Any]:
                return retriever.search(query=query, top_k=k)

            return _search
        except Exception:

            def _search(query: str, k: int) -> List[Any]:  # noqa: ARG001
                return []

            return _search

    def run(
        self,
        retriever: Any = None,
        search_fn: Optional[Callable[[str, int], List[Any]]] = None,
    ) -> Dict[str, Any]:
        """Run the benchmark.

        Args:
            retriever: Optional object exposing ``.search(query, top_k)``.
            search_fn: Optional callable ``(query, top_k) -> results`` — takes
                precedence over ``retriever``. Used for tests / fixtures.

        Returns:
            Aggregate + per-query metrics dict.
        """
        if search_fn is not None:
            do_search = search_fn
        elif retriever is not None:
            do_search = lambda q, k: retriever.search(query=q, top_k=k)  # noqa: E731
        else:
            do_search = self._default_retriever_search()

        per_query: List[Dict[str, Any]] = []
        agg_recall: List[float] = []
        agg_mrr: List[float] = []
        agg_ndcg: List[float] = []
        hits = 0
        scored = 0

        for q in self._golden.get("queries", []):
            qid = q.get("id", "")
            query = q.get("query", "")
            expect = q.get("expect", {}) or {}
            if not query or not _query_targets(expect):
                continue
            try:
                results = do_search(query, self._top_k) or []
            except Exception as exc:  # a broken retriever must not abort the run
                per_query.append({"id": qid, "query": query, "error": str(exc)})
                continue

            scores = score_query(results, expect, top_k=self._top_k)
            scores.update({"id": qid, "query": query})
            per_query.append(scores)

            agg_recall.append(scores["recall_at_k"])
            agg_mrr.append(scores["mrr"])
            agg_ndcg.append(scores["ndcg_at_k"])
            hits += scores["hit"]
            scored += 1

        def _avg(xs: List[float]) -> Optional[float]:
            return round(sum(xs) / len(xs), _SCORE_PRECISION) if xs else None

        return {
            "classification": "CUI // SP-CTI",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "top_k": self._top_k,
            "golden_set_description": self._golden.get("description", ""),
            "golden_set_version": self._golden.get("version"),
            "queries_scored": scored,
            "aggregate": {
                f"recall_at_{self._top_k}": _avg(agg_recall),
                "mrr": _avg(agg_mrr),
                f"ndcg_at_{self._top_k}": _avg(agg_ndcg),
                "citation_hit_rate": round(hits / scored, _SCORE_PRECISION) if scored else None,
            },
            "results": per_query,
        }


# ---------------------------------------------------------------------------
# Baseline compare
# ---------------------------------------------------------------------------


def compare_to_baseline(current: Dict[str, Any], baseline_path: str | Path) -> Dict[str, Any]:
    """Compute deltas of the current run's aggregate vs a saved baseline."""
    p = Path(baseline_path)
    if not p.exists():
        return {"error": f"Baseline not found: {p}"}
    with open(p, encoding="utf-8") as fh:
        baseline = json.load(fh)
    base_agg = baseline.get("aggregate", {})
    cur_agg = current.get("aggregate", {})
    deltas: Dict[str, Any] = {}
    for key in sorted(set(base_agg) | set(cur_agg)):
        b = base_agg.get(key)
        c = cur_agg.get(key)
        if isinstance(b, (int, float)) and isinstance(c, (int, float)):
            deltas[key] = {
                "baseline": b,
                "current": c,
                "delta": round(c - b, _SCORE_PRECISION),
            }
        else:
            deltas[key] = {"baseline": b, "current": c, "delta": None}
    return {
        "baseline_generated_at": baseline.get("generated_at"),
        "current_generated_at": current.get("generated_at"),
        "deltas": deltas,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG retrieval-quality baseline harness (rce-eval-01).")
    parser.add_argument("--golden-set", help="Path to golden query set YAML.")
    parser.add_argument("--top-k", type=int, help="Override top-k cutoff.")
    parser.add_argument("--baseline-out", help="Write full run to this JSON path (baseline artifact).")
    parser.add_argument("--compare", help="Compare current run against a saved baseline JSON.")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output.")
    args = parser.parse_args()

    try:
        bench = RAGBenchmark(golden_set_path=args.golden_set, top_k=args.top_k)
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}) if args.json_output else f"Error: {exc}")
        sys.exit(2)

    result = bench.run()

    if args.baseline_out:
        out = Path(args.baseline_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
        result["baseline_written_to"] = str(out)

    if args.compare:
        result["comparison"] = compare_to_baseline(result, args.compare)

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        agg = result["aggregate"]
        print(f"Golden query set: {result['queries_scored']} queries scored (top_k={result['top_k']})")
        for k, v in agg.items():
            print(f"  {k:20s}: {v}")
        if "comparison" in result and "deltas" in result["comparison"]:
            print("\nvs baseline:")
            for k, d in result["comparison"]["deltas"].items():
                print(f"  {k:20s}: {d['baseline']} -> {d['current']} (Δ {d['delta']})")


if __name__ == "__main__":
    main()
