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
import math
import sys
import time
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


def latency_stats(samples: List[float]) -> Dict[str, Any]:
    """Summarise per-query wall-clock in milliseconds.

    p50/p95 rather than the mean alone: retrieval latency is long-tailed (a cold
    cache or one slow embed dominates an average over ~48 queries), and a toggle
    that helps the median while wrecking the tail is a regression a mean hides.

    Nearest-rank percentiles — no interpolation, so a reported p95 is always an
    observed measurement rather than a synthesised value.
    """
    if not samples:
        return {"n": 0, "p50_ms": None, "p95_ms": None, "mean_ms": None, "total_ms": None}
    ordered = sorted(samples)

    def _pct(p: float) -> float:
        idx = max(0, min(len(ordered) - 1, math.ceil(p / 100.0 * len(ordered)) - 1))
        return round(ordered[idx], 2)

    return {
        "n": len(ordered),
        "p50_ms": _pct(50),
        "p95_ms": _pct(95),
        "mean_ms": round(sum(ordered) / len(ordered), 2),
        "total_ms": round(sum(ordered), 2),
    }


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
        latencies: List[float] = []
        hits = 0
        scored = 0

        for q in self._golden.get("queries", []):
            qid = q.get("id", "")
            query = q.get("query", "")
            expect = q.get("expect", {}) or {}
            if not query or not _query_targets(expect):
                continue
            # Wall-clock per query. Some toggles are SPEED optimisations whose
            # correct outcome is a zero quality delta (binary_prefilter shrinks
            # the candidate set without changing what comes back), so a sweep
            # that reports only quality cannot decide them at all.
            started = time.perf_counter()
            try:
                results = do_search(query, self._top_k) or []
            except Exception as exc:  # a broken retriever must not abort the run
                per_query.append({"id": qid, "query": query, "error": str(exc)})
                continue
            elapsed_ms = (time.perf_counter() - started) * 1000.0

            scores = score_query(results, expect, top_k=self._top_k)
            scores.update({"id": qid, "query": query, "latency_ms": round(elapsed_ms, 2)})
            per_query.append(scores)
            latencies.append(elapsed_ms)

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
            # Kept OUT of `aggregate` on purpose: everything in there is a
            # quality score in [0, 1] that a comparison can subtract blindly.
            # Latency is milliseconds and lower-is-better, so mixing it in would
            # make `compare_to_baseline`'s per-key delta loop report a slowdown
            # as a positive number alongside genuine quality gains.
            "latency": latency_stats(latencies),
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


def run_toggle_sweep(
    golden_set: Optional[str] = None,
    top_k: Optional[int] = None,
    only: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Measure each retrieval toggle in isolation against a common baseline.

    One control arm plus one arm per *wired* toggle, every arm through the same
    code path (the baseline also runs under a temp config, so the control is not
    quietly different from the variants).

    Toggles whose consumer is unreachable from the retriever are reported as
    ``NOT-WIRED`` and **not** benchmarked. Emitting a zero delta for dead code
    would read as "measured, no benefit" and produce a DROP decision on a module
    that was simply never connected — the opposite of what the evidence says.
    """
    from tools.rag.toggle_harness import TOGGLES, isolated_config, load_base_config, probe_all

    base_cfg = load_base_config()
    probes = {p.toggle: p for p in probe_all()}
    names = [n for n in (only or list(TOGGLES)) if n in TOGGLES]

    with isolated_config(None, base=base_cfg):
        control = RAGBenchmark(golden_set_path=golden_set, top_k=top_k).run()
    control_agg = control.get("aggregate", {})
    control_lat = control.get("latency", {})

    arms: List[Dict[str, Any]] = []
    for name in names:
        probe = probes[name]
        if not (probe.reachable and probe.retrieval_side):
            arms.append({
                "toggle": name,
                "verdict": probe.verdict,
                "benchmarked": False,
                "reason": probe.reason,
                "recommendation": (
                    "wire it or delete the config key — do NOT record a DROP "
                    "based on an unmeasured zero"
                ),
            })
            continue

        with isolated_config(name, True, base=base_cfg):
            run = RAGBenchmark(golden_set_path=golden_set, top_k=top_k).run()
        agg = run.get("aggregate", {})
        deltas = {
            metric: _delta(control_agg.get(metric), agg.get(metric))
            for metric in sorted(set(control_agg) | set(agg))
        }
        lat = run.get("latency", {})
        arms.append({
            "toggle": name,
            "verdict": probe.verdict,
            "benchmarked": True,
            "path": TOGGLES[name].path,
            "aggregate": agg,
            "deltas": deltas,
            "latency": lat,
            # Reported separately from `deltas` because lower is better here.
            # A toggle can be a KEEP on quality and a DROP on cost; collapsing
            # the two into one signed number hides exactly that trade.
            "latency_delta": {
                stat: _delta(control_lat.get(stat), lat.get(stat))
                for stat in ("p50_ms", "p95_ms", "mean_ms")
            },
        })

    measured = [a for a in arms if a["benchmarked"]]

    def _with(verdict: str) -> List[str]:
        return [a["toggle"] for a in arms if a["verdict"] == verdict]

    return {
        "control": {
            "aggregate": control_agg,
            "latency": control_lat,
            "queries_scored": control.get("queries_scored"),
        },
        "arms": arms,
        "summary": {
            "toggles_considered": len(names),
            "benchmarked": len(measured),
            # Bucketed by WHY, because each state needs a different fix:
            # delete the key, give it a caller, or schedule the CLI.
            "not_wired": _with("NOT-WIRED"),
            "wrapper_unadopted": _with("WRAPPER-UNADOPTED"),
            "cli_unscheduled": _with("CLI-UNSCHEDULED"),
            "ingest_only": _with("WIRED-INGEST-ONLY"),
            "unmeasurable": [a["toggle"] for a in arms if not a["benchmarked"]],
        },
    }


def _delta(baseline: Any, current: Any) -> Dict[str, Any]:
    """Signed delta, tolerant of None metrics (a zeroed or partial run)."""
    if isinstance(baseline, (int, float)) and isinstance(current, (int, float)):
        return {
            "baseline": baseline,
            "current": current,
            "delta": round(current - baseline, _SCORE_PRECISION),
        }
    return {"baseline": baseline, "current": current, "delta": None}


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG retrieval-quality baseline harness (rce-eval-01).")
    parser.add_argument("--golden-set", help="Path to golden query set YAML.")
    parser.add_argument("--top-k", type=int, help="Override top-k cutoff.")
    parser.add_argument("--baseline-out", help="Write full run to this JSON path (baseline artifact).")
    parser.add_argument("--compare", help="Compare current run against a saved baseline JSON.")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output.")
    parser.add_argument(
        "--toggle", metavar="NAME",
        help="Run once with only this retrieval toggle ON (oss-meas-01-d2). "
             "Refuses toggles whose consumer is unreachable from the retriever.",
    )
    parser.add_argument(
        "--sweep", action="store_true",
        help="Baseline + one isolated arm per wired toggle, with per-metric deltas.",
    )
    parser.add_argument(
        "--only", metavar="A,B",
        help="Restrict --sweep to these toggles (comma-separated).",
    )
    args = parser.parse_args()

    if args.sweep:
        only = [s.strip() for s in args.only.split(",")] if args.only else None
        sweep = run_toggle_sweep(args.golden_set, args.top_k, only)
        if args.json_output:
            print(json.dumps(sweep, indent=2))
        else:
            print(f"Control: {sweep['control']['aggregate']}\n")
            for arm in sweep["arms"]:
                if not arm["benchmarked"]:
                    print(f"  {arm['verdict']:18s} {arm['toggle']:18s} NOT BENCHMARKED — {arm['reason'][:90]}")
                    continue
                deltas = ", ".join(
                    f"{m} {d['delta']:+.4f}" for m, d in arm["deltas"].items()
                    if isinstance(d.get("delta"), (int, float))
                )
                print(f"  {arm['verdict']:18s} {arm['toggle']:18s} {deltas or '(no numeric metrics)'}")
                ld = arm.get("latency_delta", {})
                cost = ", ".join(
                    f"{s.replace('_ms','')} {d['delta']:+.1f}ms" for s, d in ld.items()
                    if isinstance(d.get("delta"), (int, float))
                )
                if cost:
                    print(f"  {'':18s} {'':18s} cost: {cost}")
            s = sweep["summary"]
            print(f"\n{s['benchmarked']}/{s['toggles_considered']} benchmarked; "
                  f"not wired: {', '.join(s['not_wired']) or 'none'}")
        sys.exit(0)

    if args.toggle:
        from tools.rag.toggle_harness import TOGGLES, isolated_config, probe_reachability

        if args.toggle not in TOGGLES:
            print(json.dumps({"error": f"unknown toggle {args.toggle!r}",
                              "known": sorted(TOGGLES)}, indent=2))
            sys.exit(2)
        probe = probe_reachability(args.toggle)
        if not (probe.reachable and probe.retrieval_side):
            # Refusing is the point: a number here would be indistinguishable
            # from a real measurement and would licence a false DROP.
            print(json.dumps({"error": "toggle is not measurable by a retrieval benchmark",
                              **probe.to_dict()}, indent=2))
            sys.exit(3)
        with isolated_config(args.toggle, True):
            _emit_single_run(args)
        sys.exit(0)

    _emit_single_run(args)


def _emit_single_run(args: Any) -> None:

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
