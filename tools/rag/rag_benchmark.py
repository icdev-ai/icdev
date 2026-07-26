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
  - latency_ms        : wall-clock per-query retrieval time (mean / p95); a
                        cost axis, not a quality one — reported alongside so a
                        quality gain that costs 10x latency is visible as such.

Single-toggle isolation (oss-meas-01): five retrieval capabilities are built
here and ship OFF (see ``TOGGLES``). ``run_toggle_matrix`` measures each one
INDIVIDUALLY — the toggle under test is forced on and the other four forced
off — so a delta is attributable to one toggle rather than to a combination.
The all-off control run is measured in the same process, on the same corpus,
so the deltas are internally comparable regardless of corpus drift.

PG-primary; runs unchanged on the SQLite fallback (retrieval backend is chosen
by the existing vector-store factory). Air-gap safe — no new dependencies.

Usage:
    python tools/rag/rag_benchmark.py --json
    python tools/rag/rag_benchmark.py --golden-set args/rag/golden_query_set.yaml --top-k 5 --json
    python tools/rag/rag_benchmark.py --baseline-out data/rag/rce_baseline.json --json
    python tools/rag/rag_benchmark.py --compare data/rag/rce_baseline.json --json
    python tools/rag/rag_benchmark.py --dry-run              # list the toggles under test
    python tools/rag/rag_benchmark.py --toggle-matrix --json # measure each toggle in isolation
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

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
_LATENCY_PRECISION = 2      # decimal places for reported latency (ms)
_P95 = 0.95                 # latency percentile reported alongside the mean
DEFAULT_GOLDEN_SET = BASE_DIR / "args" / "rag" / "golden_query_set.yaml"
DEFAULT_BASELINE = BASE_DIR / "data" / "rag" / "rce_baseline.json"
DEFAULT_RAG_CONFIG = BASE_DIR / "args" / "rag_config.yaml"

# Committed ground truth for the compliance golden set (rce-eval-*). These are
# runs of THIS harness recorded before the toggle work, so a matrix run can be
# read against a historical reference as well as against its own control.
DEFAULT_GROUND_TRUTH = {
    "baseline_compliance": BASE_DIR / "data" / "rag" / "rce_baseline_compliance.json",
    "contextual_compliance": BASE_DIR / "data" / "rag" / "rce_contextual_compliance.json",
}


# ---------------------------------------------------------------------------
# The five retrieval toggles that ship OFF (oss-meas-01)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToggleSpec:
    """One retrieval capability that is built, wired to config, and OFF.

    ``path`` is the dotted key in args/rag_config.yaml whose boolean the
    benchmark flips. ``name`` is that same dotted key — it is what the runner
    prints and what the per-toggle report is keyed by, so the report and the
    config edit that would act on it are never out of step.
    """

    name: str
    path: Tuple[str, ...]
    summary: str


# Order is the order they are measured and printed. Do not reorder casually —
# the writeup in docs/features/ refers to them positionally.
TOGGLES: Tuple[ToggleSpec, ...] = (
    ToggleSpec(
        name="rag.rerank.enabled",
        path=("rag", "rerank", "enabled"),
        summary="Cross-encoder re-ranking (BGE + LLM providers already exist).",
    ),
    ToggleSpec(
        name="rag.reflective_rerank.enabled",
        path=("rag", "reflective_rerank", "enabled"),
        summary="Self-RAG per-document reflective re-ranking (RELEVANT/SUPPORTS/USEFUL).",
    ),
    ToggleSpec(
        name="rag.adaptive_routing.enabled",
        path=("rag", "adaptive_routing", "enabled"),
        summary="Query-complexity pre-routing before retrieval.",
    ),
    ToggleSpec(
        name="rag.quantization.binary_prefilter.enabled",
        path=("rag", "quantization", "binary_prefilter", "enabled"),
        summary="Binary Hamming pre-filter ahead of full-precision cosine (perf).",
    ),
    ToggleSpec(
        name="rag.auto_indexer.enabled",
        path=("rag", "auto_indexer", "enabled"),
        summary="Filesystem auto-indexing of watched corpora.",
    ),
)

# RAPTOR is deliberately NOT in TOGGLES. rce-eval-05-d4/d5 already measured it
# as a regression (0.0 recall@5, 0.0 MRR, -0.0005 ndcg@5) and it stays off; it
# is carried here so the writeup can cite the number rather than re-run it.
MEASURED_REGRESSIONS: Dict[str, Dict[str, Any]] = {
    "rag.raptor.enabled": {
        "verdict": "DROP",
        "source": "rce-eval-05-d4/d5",
        "recall_at_5_delta": 0.0,
        "mrr_delta": 0.0,
        "ndcg_at_5_delta": -0.0005,
    },
}


def toggle_names() -> List[str]:
    """Dotted config keys of the toggles this runner measures, in run order."""
    return [t.name for t in TOGGLES]


def load_rag_config(path: str | Path | None = None) -> Dict[str, Any]:
    """Load args/rag_config.yaml. Empty dict when absent/unreadable."""
    p = Path(path) if path else DEFAULT_RAG_CONFIG
    if not p.exists():
        return {}
    import yaml

    with open(p, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _set_path(cfg: Dict[str, Any], path: Tuple[str, ...], value: Any) -> None:
    """Set a nested key, creating intermediate dicts as needed."""
    node: Dict[str, Any] = cfg
    for key in path[:-1]:
        nxt = node.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            node[key] = nxt
        node = nxt
    node[path[-1]] = value


def build_isolated_config(
    base: Dict[str, Any],
    enable: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a copy of ``base`` with exactly one toggle on and the rest off.

    ``enable=None`` builds the all-off control. Isolation is the point: every
    toggle in ``TOGGLES`` is written explicitly rather than left at whatever
    the file happens to say, so a run is reproducible even if someone flips a
    default in args/rag_config.yaml between runs.
    """
    known = {t.name: t for t in TOGGLES}
    if enable is not None and enable not in known:
        raise ValueError(f"Unknown toggle: {enable}. Known: {', '.join(known)}")
    cfg = copy.deepcopy(base) if base else {}
    for spec in TOGGLES:
        _set_path(cfg, spec.path, spec.name == enable)
    return cfg


@contextmanager
def isolated_toggle_config(cfg: Dict[str, Any]) -> Iterator[None]:
    """Make the in-process retrieval path read ``cfg`` instead of the YAML file.

    The retrieval modules each resolve config lazily through their own loader
    rather than through one shared object, so a single ``RAGRetriever(config=)``
    argument does NOT reach the vector store or the quantization pre-filter.
    This patches each loader for the duration of one toggle run and restores
    them afterwards, so the toggle under test is the only thing that differs.

    Both the ``tools.*`` and ``icdev.tools.*`` module objects are patched when
    already imported — they are distinct modules under the compat shim, and
    patching only one leaves the other serving the on-disk defaults.
    """
    quant = (cfg.get("rag") or {}).get("quantization", {}) or {}
    # (module path, attribute, replacement)
    targets = [
        ("rag.retriever", "_load_rag_config", lambda: copy.deepcopy(cfg)),
        ("rag.vector_store_factory", "_load_rag_config", lambda: copy.deepcopy(cfg)),
        (
            "rag.sqlite_vector_store",
            "_load_quantization_config",
            lambda config=None: copy.deepcopy(quant),
        ),
    ]
    saved: List[Tuple[Any, str, Any]] = []
    try:
        for suffix, attr, replacement in targets:
            for root in ("tools.", "icdev.tools."):
                mod = sys.modules.get(root + suffix)
                if mod is None or not hasattr(mod, attr):
                    continue
                saved.append((mod, attr, getattr(mod, attr)))
                setattr(mod, attr, replacement)
        yield
    finally:
        for mod, attr, original in reversed(saved):
            setattr(mod, attr, original)


def _import_retrieval_modules() -> None:
    """Import the retrieval modules so their loaders exist to be patched.

    Best-effort: a module that cannot import (missing optional backend) simply
    is not patched, and the run degrades to the zeroed-baseline path rather
    than aborting the matrix.
    """
    import importlib

    for name in (
        "tools.rag.retriever",
        "tools.rag.vector_store_factory",
        "tools.rag.sqlite_vector_store",
    ):
        try:
            importlib.import_module(name)
        except Exception:  # optional backend absent — nothing to patch
            continue


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


def _latency_summary(samples: List[float]) -> Dict[str, Optional[float]]:
    """Mean / p95 / max wall-clock latency in ms over the scored queries."""
    if not samples:
        return {"mean_ms": None, "p95_ms": None, "max_ms": None, "samples": 0}
    ordered = sorted(samples)
    # Nearest-rank p95; on small golden sets this is the 2nd-slowest query, and
    # saying "p95" about 33 samples is honest only with the count alongside.
    idx = min(len(ordered) - 1, max(0, int(round(_P95 * len(ordered))) - 1))
    return {
        "mean_ms": round(sum(ordered) / len(ordered), _LATENCY_PRECISION),
        "p95_ms": round(ordered[idx], _LATENCY_PRECISION),
        "max_ms": round(ordered[-1], _LATENCY_PRECISION),
        "samples": len(ordered),
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
        latencies: List[float] = []
        hits = 0
        scored = 0

        for q in self._golden.get("queries", []):
            qid = q.get("id", "")
            query = q.get("query", "")
            expect = q.get("expect", {}) or {}
            if not query or not _query_targets(expect):
                continue
            started = time.perf_counter()
            try:
                results = do_search(query, self._top_k) or []
            except Exception as exc:  # a broken retriever must not abort the run
                per_query.append({"id": qid, "query": query, "error": str(exc)})
                continue
            elapsed_ms = (time.perf_counter() - started) * 1000.0

            scores = score_query(results, expect, top_k=self._top_k)
            scores.update(
                {
                    "id": qid,
                    "query": query,
                    "latency_ms": round(elapsed_ms, _LATENCY_PRECISION),
                }
            )
            per_query.append(scores)

            agg_recall.append(scores["recall_at_k"])
            agg_mrr.append(scores["mrr"])
            agg_ndcg.append(scores["ndcg_at_k"])
            latencies.append(elapsed_ms)
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
            # Cost axis, kept out of "aggregate" so the quality dict stays a
            # dict of [0,1] scores that compare_to_baseline can delta uniformly.
            "latency": _latency_summary(latencies),
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


def load_ground_truth(
    paths: Optional[Dict[str, str | Path]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Load the committed baseline artifacts used as historical reference.

    A missing file is reported as an ``error`` entry rather than raising: the
    matrix's own all-off control is the primary comparison, and a fresh
    worktree without the data/ artifacts must still be able to run.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for label, path in (paths or DEFAULT_GROUND_TRUTH).items():
        p = Path(path)
        if not p.exists():
            out[label] = {"error": f"not found: {p}"}
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            out[label] = {"error": f"unreadable: {exc}"}
            continue
        out[label] = {
            "path": str(p),
            "generated_at": data.get("generated_at"),
            "queries_scored": data.get("queries_scored"),
            "aggregate": data.get("aggregate", {}),
        }
    return out


def _delta_aggregate(control: Dict[str, Any], variant: Dict[str, Any]) -> Dict[str, Any]:
    """Per-metric variant-minus-control deltas over two aggregate dicts."""
    deltas: Dict[str, Any] = {}
    for key in sorted(set(control) | set(variant)):
        c, v = control.get(key), variant.get(key)
        if isinstance(c, (int, float)) and isinstance(v, (int, float)):
            deltas[key] = round(v - c, _SCORE_PRECISION)
        else:
            deltas[key] = None
    return deltas


def run_toggle_matrix(
    golden_set_path: str | Path | None = None,
    top_k: Optional[int] = None,
    rag_config: Optional[Dict[str, Any]] = None,
    search_fn_factory: Optional[Callable[[Dict[str, Any]], Callable[[str, int], List[Any]]]] = None,
    ground_truth: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Measure each toggle in ``TOGGLES`` individually against an all-off control.

    Args:
        golden_set_path: Golden query set YAML (default: the compliance set).
        top_k: Override the golden set's top-k.
        rag_config: Base config to isolate from (default: args/rag_config.yaml).
        search_fn_factory: ``(isolated_config) -> search(query, top_k)``. Tests
            inject this; in production it is None and the real retriever is
            constructed under the patched loaders.
        ground_truth: Pre-loaded reference baselines (default: the committed
            compliance artifacts).

    Returns:
        ``{control, toggles: {name: {aggregate, latency, delta_vs_control}}}``.
    """
    bench = RAGBenchmark(golden_set_path=golden_set_path, top_k=top_k)
    base_cfg = rag_config if rag_config is not None else load_rag_config()
    _import_retrieval_modules()

    def _run_one(enable: Optional[str]) -> Dict[str, Any]:
        cfg = build_isolated_config(base_cfg, enable=enable)
        with isolated_toggle_config(cfg):
            if search_fn_factory is not None:
                return bench.run(search_fn=search_fn_factory(cfg))
            # Construct the retriever INSIDE the patched scope: it snapshots
            # config at __init__, so building it outside would read the
            # on-disk defaults and silently measure the same thing five times.
            return bench.run(search_fn=_live_search_fn(cfg, bench.top_k))

    control = _run_one(None)
    control_agg = control.get("aggregate", {})

    per_toggle: Dict[str, Any] = {}
    for spec in TOGGLES:
        run = _run_one(spec.name)
        per_toggle[spec.name] = {
            "summary": spec.summary,
            "config_path": ".".join(spec.path),
            "queries_scored": run.get("queries_scored"),
            "aggregate": run.get("aggregate", {}),
            "latency": run.get("latency", {}),
            "delta_vs_control": _delta_aggregate(control_agg, run.get("aggregate", {})),
        }

    return {
        "classification": "CUI // SP-CTI",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "top_k": bench.top_k,
        "toggles_tested": toggle_names(),
        "control": {
            "description": "all five toggles forced off",
            "queries_scored": control.get("queries_scored"),
            "aggregate": control_agg,
            "latency": control.get("latency", {}),
        },
        "toggles": per_toggle,
        "ground_truth": ground_truth if ground_truth is not None else load_ground_truth(),
        "previously_measured": MEASURED_REGRESSIONS,
    }


def _live_search_fn(cfg: Dict[str, Any], top_k: int) -> Callable[[str, int], List[Any]]:
    """Build a real-retriever search callable bound to an isolated config."""
    try:
        from tools.rag.retriever import RAGRetriever

        retriever = RAGRetriever(config=copy.deepcopy(cfg))
    except Exception:

        def _empty(query: str, k: int) -> List[Any]:  # noqa: ARG001
            return []

        return _empty

    def _search(query: str, k: int) -> List[Any]:
        return retriever.search(query=query, top_k=k)

    return _search


def dry_run_plan(
    golden_set_path: str | Path | None = None,
    top_k: Optional[int] = None,
) -> Dict[str, Any]:
    """Describe what a matrix run would do, without retrieving anything.

    This is the cheap pre-flight: it proves the golden set parses, the toggle
    registry is intact, and the ground-truth artifacts are where the runner
    expects them — none of which needs a populated vector store.
    """
    plan: Dict[str, Any] = {
        "dry_run": True,
        "classification": "CUI // SP-CTI",
        "toggles_tested": toggle_names(),
        "toggle_count": len(TOGGLES),
        "toggles": [
            {"name": t.name, "config_path": ".".join(t.path), "summary": t.summary}
            for t in TOGGLES
        ],
        "runs_planned": len(TOGGLES) + 1,  # +1 for the all-off control
        "metrics": [
            "recall_at_k",
            "mrr",
            "ndcg_at_k",
            "citation_hit_rate",
            "latency_ms",
        ],
        "ground_truth": load_ground_truth(),
        "previously_measured": MEASURED_REGRESSIONS,
    }
    try:
        bench = RAGBenchmark(golden_set_path=golden_set_path, top_k=top_k)
        golden = bench._golden  # noqa: SLF001 — same module, plan needs the counts
        plan["golden_set"] = str(Path(golden_set_path) if golden_set_path else DEFAULT_GOLDEN_SET)
        plan["top_k"] = bench.top_k
        plan["queries_available"] = sum(
            1
            for q in golden.get("queries", [])
            if q.get("query") and _query_targets(q.get("expect", {}) or {})
        )
    except (FileNotFoundError, ValueError) as exc:
        plan["golden_set_error"] = str(exc)
    return plan


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_dry_run(plan: Dict[str, Any]) -> None:
    """Human-readable dry-run plan (the --dry-run acceptance output)."""
    print(f"Toggle isolation plan — {plan['toggle_count']} toggles under test:")
    for i, t in enumerate(plan["toggles"], 1):
        print(f"  {i}. {t['name']}")
        print(f"     {t['summary']}")
    print(
        f"\nRuns planned: {plan['runs_planned']} "
        f"(1 all-off control + {plan['toggle_count']} single-toggle runs)"
    )
    if "golden_set_error" in plan:
        print(f"Golden set: ERROR — {plan['golden_set_error']}")
    else:
        print(f"Golden set: {plan['golden_set']}")
        print(f"  {plan['queries_available']} scorable queries, top_k={plan['top_k']}")
    print(f"Metrics: {', '.join(plan['metrics'])}")
    print("Ground truth:")
    for label, gt in plan["ground_truth"].items():
        if "error" in gt:
            print(f"  {label:24s}: {gt['error']}")
        else:
            print(f"  {label:24s}: {gt['aggregate']}")


def _print_matrix(result: Dict[str, Any]) -> None:
    """Human-readable toggle matrix."""
    ctrl = result["control"]
    print(f"Control (all toggles off) — {ctrl['queries_scored']} queries, top_k={result['top_k']}")
    for k, v in ctrl["aggregate"].items():
        print(f"  {k:20s}: {v}")
    print(f"  {'latency_mean_ms':20s}: {ctrl.get('latency', {}).get('mean_ms')}")
    for name, t in result["toggles"].items():
        print(f"\n{name} (isolated)")
        for k, v in t["delta_vs_control"].items():
            print(f"  {k:20s}: {t['aggregate'].get(k)} (Δ {v})")
        print(f"  {'latency_mean_ms':20s}: {t.get('latency', {}).get('mean_ms')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG retrieval-quality baseline harness (rce-eval-01).")
    parser.add_argument("--golden-set", help="Path to golden query set YAML.")
    parser.add_argument("--top-k", type=int, help="Override top-k cutoff.")
    parser.add_argument("--baseline-out", help="Write full run to this JSON path (baseline artifact).")
    parser.add_argument("--compare", help="Compare current run against a saved baseline JSON.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the toggles that would be measured and exit. Retrieves nothing.",
    )
    parser.add_argument(
        "--toggle-matrix",
        action="store_true",
        help="Measure each toggle in isolation (others forced off) vs an all-off control.",
    )
    parser.add_argument(
        "--matrix-out",
        help="Write the toggle matrix to this JSON path.",
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output.")
    args = parser.parse_args()

    if args.dry_run:
        plan = dry_run_plan(golden_set_path=args.golden_set, top_k=args.top_k)
        if args.json_output:
            print(json.dumps(plan, indent=2))
        else:
            _print_dry_run(plan)
        return

    if args.toggle_matrix:
        try:
            matrix = run_toggle_matrix(golden_set_path=args.golden_set, top_k=args.top_k)
        except (FileNotFoundError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}) if args.json_output else f"Error: {exc}")
            sys.exit(2)
        if args.matrix_out:
            out = Path(args.matrix_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(matrix, fh, indent=2)
            matrix["matrix_written_to"] = str(out)
        if args.json_output:
            print(json.dumps(matrix, indent=2))
        else:
            _print_matrix(matrix)
        return

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
