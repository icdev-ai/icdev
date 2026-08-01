# CUI // SP-CTI
"""Divergence-vs-single-shot benchmark on real ICDEV functions (dvg-bench-01).

Adopt upstream's MEASUREMENT, not its numbers. The adhd-agent author reported
1.9x breadth / 2.9x novelty / 5.2x trap detection over 6 of their own problems;
those figures were never independently reproduced on ICDEV. This harness re-runs
the comparison against ICDEV-representative ideation tasks, **holding the model
fixed** so the delta isolates the method (divergent fan-out vs one single-shot
call), and reports **token cost per run** alongside the quality deltas -- a 2x
quality gain at 10x spend is a different decision than at 2x spend, and cost is
the reason divergence ships OFF by default.

Follows the agx-bench-01 pattern (tools/llm/architectures/benchmark.py) so the
two are comparable: injectable runner/judge seams, honest ``unmeasured`` cells
when no model is reachable (a bare/air-gapped worktree NEVER fails the build or
requires live models to merge), no silent caps, deterministic pure aggregation.

Quality is judged by the divergence critic (tools/quality/divergence_critic.py) --
the domain-appropriate categorical judge (novelty/viability/fit + trap flags),
same deterministic-first discipline as tools/evolution/fitness.py. Both methods'
pools are scored by the SAME critic so the only variable is the generator.

RECOMMEND-ONLY: this module writes a recommendation but flips NO default. The
decision to enable a dvg-wire-* branch point, or to promote trap detection from
advisory to gating, is a human one informed by these measurements.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.creative.divergence_benchmark")

_DEFAULT_TASKS_PATH = "args/creative/divergence_benchmark_tasks.yaml"
METHODS = ("single_shot", "divergence")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class DivergenceTask:
    """One ideation task drawn from real ICDEV work (a pain point / capability signal)."""

    id: str
    family: str
    prompt: str
    function: str = "creative_ideation"
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MethodResult:
    """Result of one (task x method) run. ``status`` is measured | unmeasured | error.

    ``unmeasured`` = the generator or judge could not reach a live model (air-gap);
    ``error`` = the harness itself failed. Neither fabricates a quality score.
    """

    task_id: str
    task_family: str
    method: str
    status: str = "unmeasured"
    model_id: str = ""
    breadth_ideas: Optional[int] = None
    breadth_clusters: Optional[int] = None
    mean_novelty: Optional[float] = None
    trap_count: Optional[int] = None
    cost_usd: float = 0.0
    total_tokens: int = 0
    trace_id: str = ""
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskComparison:
    """Divergence-vs-single-shot deltas for one task (measured only if BOTH sides are)."""

    task_id: str
    task_family: str
    status: str = "unmeasured"
    model_fixed: bool = False  # true only when the same model family served both sides
    single_shot: Optional[Dict[str, Any]] = None
    divergence: Optional[Dict[str, Any]] = None
    breadth_ratio: Optional[float] = None
    novelty_ratio: Optional[float] = None
    trap_delta: Optional[int] = None  # absolute: divergence traps - single-shot traps
    cost_ratio: Optional[float] = None
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkReport:
    """Full benchmark output -- comparisons + aggregate deltas + honest metadata + recommendation."""

    generated_at: str
    status: str  # measured | unmeasured
    n_tasks: int = 0
    task_families: List[str] = field(default_factory=list)
    comparisons: List[TaskComparison] = field(default_factory=list)
    aggregate: Dict[str, Any] = field(default_factory=dict)
    recommendation: Dict[str, Any] = field(default_factory=dict)
    dropped: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "status": self.status,
            "n_tasks": self.n_tasks,
            "task_families": list(self.task_families),
            "comparisons": [c.to_dict() for c in self.comparisons],
            "aggregate": dict(self.aggregate),
            "recommendation": dict(self.recommendation),
            "dropped": list(self.dropped),
        }


# Injectable seams.
# generate_fn(method, task, function, router, orchestrator) -> (pool_text, cost_usd, tokens, model_id, trace_id)
GenerateFn = Callable[..., Tuple[str, float, int, str, str]]
# judge_fn(pool_text, function, trace_id, router) -> ScoredPool-like
JudgeFn = Callable[..., Any]


# ---------------------------------------------------------------------------
# Task suite loading
# ---------------------------------------------------------------------------
def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists() or (parent / "CLAUDE.md").exists():
            return parent
    return here.parents[3]


def load_task_suite(path: Optional[str] = None) -> List[DivergenceTask]:
    """Load the ideation task suite from YAML (data, not code). Never raises; a
    missing file yields an empty list so a bare worktree degrades to a scaffold."""
    import yaml

    p = Path(path) if path else Path(_DEFAULT_TASKS_PATH)
    if not p.is_absolute():
        p = _repo_root() / p
    if not p.exists():
        logger.warning("[dvg-bench] task suite not found at %s", p)
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    tasks: List[DivergenceTask] = []
    for raw in data.get("tasks", []) or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        tasks.append(
            DivergenceTask(
                id=str(raw["id"]),
                family=str(raw.get("family", "general")),
                prompt=str(raw.get("prompt", "")).strip(),
                function=str(raw.get("function", "creative_ideation")),
                note=str(raw.get("note", "")).strip(),
            )
        )
    return tasks


# ---------------------------------------------------------------------------
# Default (live) generator + judge -- both injectable for tests
# ---------------------------------------------------------------------------
_SINGLE_SHOT_SYSTEM = (
    "You are an ideation engine. Given a problem, produce a numbered list of "
    "distinct candidate solution directions. Directions only -- do NOT evaluate, "
    "rank, or self-critique. One idea per numbered line."
)


def default_generate(
    method: str, task: DivergenceTask, function: str, router: Any, orchestrator: Any
) -> Tuple[str, float, int, str, str]:
    """Produce an idea pool for ``method``. Raises on unreachable providers -- the
    caller (run_method) turns that into an ``unmeasured`` result.

    single_shot: ONE direct ``router.invoke`` call (the baseline).
    divergence:  isolated fan-out via ``orchestrator.invoke_divergence``.

    Both run under the same ``function`` so routing resolves the same model family
    -- that is how the model is held fixed across the two methods.
    """
    from tools.llm.provider import LLMRequest

    if method == "single_shot":
        resp = router.invoke(
            function,
            LLMRequest(
                messages=[{"role": "user", "content": task.prompt}],
                system_prompt=_SINGLE_SHOT_SYSTEM,
            ),
        )
        content = getattr(resp, "content", "") or ""
        tokens = int(getattr(resp, "input_tokens", 0) or 0) + int(getattr(resp, "output_tokens", 0) or 0)
        cost = _best_effort_cost(router, getattr(resp, "model_id", ""), resp)
        return content, cost, tokens, getattr(resp, "model_id", "") or "", ""

    if method == "divergence":
        request = LLMRequest(messages=[{"role": "user", "content": task.prompt}])
        chain = orchestrator.invoke_divergence(function, request)
        content = getattr(chain, "content", "") or ""
        tokens = int(getattr(chain, "total_input_tokens", 0) or 0) + int(getattr(chain, "total_output_tokens", 0) or 0)
        cost = float(getattr(chain, "total_cost_usd", 0.0) or 0.0)
        models = getattr(chain, "models_used", []) or []
        return content, cost, tokens, (models[0] if models else ""), getattr(chain, "trace_id", "") or ""

    raise ValueError(f"unknown method: {method}")


def _best_effort_cost(router: Any, model_id: str, resp: Any) -> float:
    """Compute single-shot USD cost from the router's pricing table; 0.0 if unknown."""
    try:
        pricing = router.get_model_pricing(model_id) or {}
        in_tok = int(getattr(resp, "input_tokens", 0) or 0)
        out_tok = int(getattr(resp, "output_tokens", 0) or 0)
        return round(
            in_tok / 1_000_000 * float(pricing.get("input_per_1m", 0.0))
            + out_tok / 1_000_000 * float(pricing.get("output_per_1m", 0.0)),
            6,
        )
    except Exception:  # noqa: BLE001 -- cost is best-effort; tokens are the hard signal
        return 0.0


def default_judge(pool_text: str, function: str, trace_id: str, router: Any):
    """Score a pool with the divergence critic (same judge for both methods)."""
    from tools.quality.divergence_critic import score_idea_pool

    return score_idea_pool(pool_text, function=function, trace_id=trace_id or None, router=router, persist=False)


# ---------------------------------------------------------------------------
# Metric extraction (pure, deterministic over a ScoredPool)
# ---------------------------------------------------------------------------
def metrics_from_scored(scored: Any) -> Dict[str, Any]:
    """Extract breadth / novelty / trap metrics from a ScoredPool. Pure.

    breadth_ideas    = number of scored ideas
    breadth_clusters = number of DISTINCT approaches (critic cluster labels)
    mean_novelty     = mean composed novelty-dimension float across ideas
    trap_count       = number of ideas the critic flagged as seductive-but-broken
    """
    from tools.quality.divergence_critic import cluster_pool

    ordered = list(getattr(scored, "ordered", []) or [])
    novelties = [
        float(s.dimension_floats.get("novelty"))
        for s in ordered
        if getattr(s, "dimension_floats", None) and s.dimension_floats.get("novelty") is not None
    ]
    return {
        "breadth_ideas": len(ordered),
        "breadth_clusters": len(cluster_pool(scored)) if ordered else 0,
        "mean_novelty": round(statistics.fmean(novelties), 4) if novelties else None,
        "trap_count": sum(1 for s in ordered if getattr(s, "is_trap", False)),
    }


# ---------------------------------------------------------------------------
# Method + task execution
# ---------------------------------------------------------------------------
def run_method(
    task: DivergenceTask,
    method: str,
    *,
    generate_fn: GenerateFn,
    judge_fn: JudgeFn,
    router: Any = None,
    orchestrator: Any = None,
) -> MethodResult:
    """Run one (task x method) cell, never raising."""
    res = MethodResult(task_id=task.id, task_family=task.family, method=method)
    try:
        pool_text, cost, tokens, model_id, trace_id = generate_fn(method, task, task.function, router, orchestrator)
    except Exception as exc:  # noqa: BLE001 -- unreachable model => unmeasured, not a crash
        res.status = "unmeasured"
        res.note = f"generate unreachable: {type(exc).__name__}: {exc}"[:300]
        return res

    res.cost_usd = float(cost or 0.0)
    res.total_tokens = int(tokens or 0)
    res.model_id = model_id or ""
    res.trace_id = trace_id or ""
    if not pool_text:
        res.status = "unmeasured"
        res.note = "empty pool (no reachable model / degraded generation)"
        return res

    try:
        scored = judge_fn(pool_text, task.function, trace_id, router)
    except Exception as exc:  # noqa: BLE001
        res.status = "error"
        res.note = f"judge failed: {type(exc).__name__}: {exc}"[:300]
        return res

    if not getattr(scored, "ordered", None):
        res.status = "unmeasured"
        res.note = f"critic produced no scores ({getattr(scored, 'stop_reason', 'unknown')})"
        return res

    m = metrics_from_scored(scored)
    res.status = "measured"
    res.breadth_ideas = m["breadth_ideas"]
    res.breadth_clusters = m["breadth_clusters"]
    res.mean_novelty = m["mean_novelty"]
    res.trap_count = m["trap_count"]
    return res


def _same_family(model_a: str, model_b: str) -> bool:
    """Coarse model-family equality for the 'model held fixed' check."""
    def fam(m: str) -> str:
        m = (m or "").lower()
        for key in ("claude", "gpt", "qwen", "llama", "mistral", "gemini", "kimi", "deepseek"):
            if key in m:
                return key
        return m.split("-")[0] if m else ""
    return bool(model_a) and bool(model_b) and fam(model_a) == fam(model_b)


def compare_task(single: MethodResult, diverge: MethodResult) -> TaskComparison:
    """Deterministic divergence-vs-single-shot deltas for one task. Measured only
    when BOTH sides measured; ratios guard against zero denominators."""
    comp = TaskComparison(
        task_id=single.task_id,
        task_family=single.task_family,
        single_shot=single.to_dict(),
        divergence=diverge.to_dict(),
    )
    if single.status != "measured" or diverge.status != "measured":
        comp.status = "unmeasured"
        comp.note = f"single_shot={single.status}, divergence={diverge.status}"
        return comp

    comp.status = "measured"
    comp.model_fixed = _same_family(single.model_id, diverge.model_id)
    comp.breadth_ratio = _ratio(diverge.breadth_clusters, single.breadth_clusters)
    comp.novelty_ratio = _ratio(diverge.mean_novelty, single.mean_novelty)
    comp.trap_delta = (diverge.trap_count or 0) - (single.trap_count or 0)
    comp.cost_ratio = _ratio(diverge.cost_usd, single.cost_usd) or _ratio(diverge.total_tokens, single.total_tokens)
    if not comp.model_fixed:
        comp.note = "model NOT held fixed across methods -- delta not attributable to method alone"
    return comp


def _ratio(num: Any, den: Any) -> Optional[float]:
    try:
        n, d = float(num), float(den)
    except (TypeError, ValueError):
        return None
    if d == 0:
        return None
    return round(n / d, 4)


# ---------------------------------------------------------------------------
# Aggregation + recommendation (pure)
# ---------------------------------------------------------------------------
def aggregate(comparisons: List[TaskComparison]) -> Dict[str, Any]:
    """Mean of each delta over the measured comparisons. Pure."""
    measured = [c for c in comparisons if c.status == "measured"]
    fixed = [c for c in measured if c.model_fixed]
    return {
        "n_measured": len(measured),
        "n_model_fixed": len(fixed),
        "mean_breadth_ratio": _mean([c.breadth_ratio for c in measured]),
        "mean_novelty_ratio": _mean([c.novelty_ratio for c in measured]),
        "mean_trap_delta": _mean([c.trap_delta for c in measured]),
        "mean_cost_ratio": _mean([c.cost_ratio for c in measured]),
    }


def _mean(values: List[Any]) -> Optional[float]:
    nums = [float(v) for v in values if v is not None]
    return round(statistics.fmean(nums), 4) if nums else None


def recommend(report_status: str, agg: Dict[str, Any]) -> Dict[str, Any]:
    """Advisory recommendation over the aggregate. Flips NO default -- the enable
    decision and the trap-gating promotion remain human calls (dvg-bench-01).

    Rationale thresholds are conservative and explicit: divergence must show a
    real quality gain AND acceptable cost for a *candidate* recommendation, and
    the comparison must have held the model fixed to be credible at all.
    """
    rec: Dict[str, Any] = {
        "enable_divergence_default": False,  # NEVER auto-flip; card ships OFF by default
        "promote_trap_to_gating": False,     # trap stays advisory until measured reliable
        "verdict": "keep_off",
        "rationale": "",
    }
    if report_status != "measured" or not agg.get("n_measured"):
        rec["verdict"] = "unmeasured"
        rec["rationale"] = (
            "No measured comparisons (air-gap / no reachable model). Run live with "
            "the model held fixed before enabling divergence anywhere by default. "
            "Upstream's 1.9x/2.9x/5.2x figures remain unreproduced on ICDEV."
        )
        return rec

    if not agg.get("n_model_fixed"):
        rec["verdict"] = "inconclusive"
        rec["rationale"] = (
            "Measured, but the model was not held fixed on any task, so deltas are "
            "not attributable to the method. Re-run pinning one model family."
        )
        return rec

    breadth = agg.get("mean_breadth_ratio") or 0.0
    novelty = agg.get("mean_novelty_ratio") or 0.0
    cost = agg.get("mean_cost_ratio")
    trap_delta = agg.get("mean_trap_delta") or 0.0

    quality_gain = breadth >= 1.3 or novelty >= 1.3
    cost_acceptable = cost is not None and cost <= 3.0
    if quality_gain and cost_acceptable:
        rec["verdict"] = "candidate_opt_in"
        rec["rationale"] = (
            f"Divergence shows a quality gain (breadth x{breadth:g}, novelty x{novelty:g}) "
            f"at ~x{cost:g} token cost. CANDIDATE for opt-in on high-value functions "
            "via the dvg-wire-* toggles -- still ships OFF by default pending human review."
        )
    else:
        rec["verdict"] = "keep_off"
        rec["rationale"] = (
            f"Quality gain (breadth x{breadth:g}, novelty x{novelty:g}) does not clear "
            f"the bar at ~x{cost if cost is not None else '?'} token cost. Keep divergence OFF."
        )
    rec["trap_delta"] = trap_delta
    rec["trap_note"] = (
        f"Divergence surfaced {trap_delta:+g} traps/task vs single-shot on average; "
        "trap detection stays ADVISORY (promotion to gating is a separate human decision "
        "requiring a larger measured sample)."
    )
    return rec


# ---------------------------------------------------------------------------
# Top-level run
# ---------------------------------------------------------------------------
def run_benchmark(
    *,
    tasks: Optional[List[DivergenceTask]] = None,
    generate_fn: Optional[GenerateFn] = None,
    judge_fn: Optional[JudgeFn] = None,
    router: Any = None,
    orchestrator: Any = None,
    tasks_path: Optional[str] = None,
) -> BenchmarkReport:
    """Run the divergence-vs-single-shot benchmark. Never raises for model
    problems -- those surface as ``unmeasured`` comparisons and a report status of
    ``unmeasured``. Every seam is injectable so tests run with no model access."""
    generate_fn = generate_fn or default_generate
    judge_fn = judge_fn or default_judge
    dropped: List[Dict[str, Any]] = []

    if tasks is None:
        tasks = load_task_suite(tasks_path)
    if not tasks:
        dropped.append({"what": "tasks", "reason": "task suite empty or not found"})

    # Build the live router/orchestrator lazily, defensively (air-gap => None).
    if generate_fn is default_generate:
        if router is None:
            router = _try_build_router(dropped)
        if orchestrator is None and router is not None:
            orchestrator = _try_build_orchestrator(router, dropped)

    comparisons: List[TaskComparison] = []
    for task in tasks or []:
        single = run_method(task, "single_shot", generate_fn=generate_fn, judge_fn=judge_fn,
                            router=router, orchestrator=orchestrator)
        diverge = run_method(task, "divergence", generate_fn=generate_fn, judge_fn=judge_fn,
                            router=router, orchestrator=orchestrator)
        comparisons.append(compare_task(single, diverge))

    if not any(c.status == "measured" for c in comparisons):
        dropped.append(
            {"what": "comparisons", "reason": "no task measured on both methods -- "
             "air-gap / no reachable model; report is a scaffold to be populated live"}
        )

    agg = aggregate(comparisons)
    status = "measured" if agg["n_measured"] else "unmeasured"
    report = BenchmarkReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        n_tasks=len(tasks or []),
        task_families=sorted({t.family for t in (tasks or [])}),
        comparisons=comparisons,
        aggregate=agg,
        recommendation=recommend(status, agg),
        dropped=dropped,
    )
    return report


def _try_build_router(dropped: List[Dict[str, Any]]) -> Any:
    try:
        from tools.llm.router import LLMRouter

        return LLMRouter()
    except Exception as exc:  # noqa: BLE001
        dropped.append({"what": "router", "reason": f"LLMRouter unavailable: {exc}"})
        return None


def _try_build_orchestrator(router: Any, dropped: List[Dict[str, Any]]) -> Any:
    try:
        from tools.llm.chain_orchestrator import ChainOrchestrator

        return ChainOrchestrator(router=router)
    except Exception as exc:  # noqa: BLE001
        dropped.append({"what": "orchestrator", "reason": f"ChainOrchestrator unavailable: {exc}"})
        return None


# ---------------------------------------------------------------------------
# Persistence + rendering
# ---------------------------------------------------------------------------
def default_results_dir() -> Path:
    return _repo_root() / "data" / "divergence"


def persist_report(report: BenchmarkReport, out_dir: Optional[str] = None) -> Path:
    """Write the report to ``data/divergence/benchmark_<utc>.json`` + ``latest``."""
    d = Path(out_dir) if out_dir else default_results_dir()
    d.mkdir(parents=True, exist_ok=True)
    stamp = report.generated_at.replace(":", "").replace("-", "").replace(".", "")[:15]
    path = d / f"benchmark_{stamp}.json"
    payload = json.dumps(report.to_dict(), indent=2, sort_keys=True)
    path.write_text(payload, encoding="utf-8")
    (d / "benchmark_latest.json").write_text(payload, encoding="utf-8")
    return path


def load_latest_report(out_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
    d = Path(out_dir) if out_dir else default_results_dir()
    latest = d / "benchmark_latest.json"
    if not latest.exists():
        return None
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def render_markdown(report: BenchmarkReport) -> str:
    a = report.aggregate
    lines = [
        "# Divergence vs Single-Shot Benchmark (dvg-bench-01)",
        "",
        f"- generated: {report.generated_at}",
        f"- status: **{report.status}**",
        f"- tasks: {report.n_tasks} across families: {', '.join(report.task_families) or '(none)'}",
        f"- measured comparisons: {a.get('n_measured', 0)} "
        f"(model held fixed on {a.get('n_model_fixed', 0)})",
        "",
        "## Aggregate deltas (divergence / single-shot)",
        "",
        f"- mean breadth ratio: {_fmt(a.get('mean_breadth_ratio'))}",
        f"- mean novelty ratio: {_fmt(a.get('mean_novelty_ratio'))}",
        f"- mean trap delta (per task): {_fmt(a.get('mean_trap_delta'))}",
        f"- mean cost ratio (token/USD): {_fmt(a.get('mean_cost_ratio'))}",
        "",
        "## Recommendation (advisory -- flips no default)",
        "",
        f"- verdict: **{report.recommendation.get('verdict')}**",
        f"- enable divergence by default: {report.recommendation.get('enable_divergence_default')}",
        f"- promote trap detection to gating: {report.recommendation.get('promote_trap_to_gating')}",
        f"- rationale: {report.recommendation.get('rationale')}",
        "",
    ]
    if report.dropped:
        lines.append("## Dropped / unmeasured (no silent caps)")
        for d in report.dropped:
            lines.append(f"- **{d.get('what')}**: {d.get('reason')}")
        lines.append("")
    lines.append("| task | family | status | model-fixed | breadth | novelty | trap Δ | cost |")
    lines.append("|---|---|---|---|--:|--:|--:|--:|")
    for c in report.comparisons:
        lines.append(
            f"| {c.task_id} | {c.task_family} | {c.status} | {c.model_fixed} | "
            f"{_fmt(c.breadth_ratio)} | {_fmt(c.novelty_ratio)} | {_fmt(c.trap_delta)} | {_fmt(c.cost_ratio)} |"
        )
    return "\n".join(lines) + "\n"


def _fmt(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:g}"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Divergence vs single-shot benchmark (dvg-bench-01)")
    parser.add_argument("--run", action="store_true", help="Run the benchmark (live models if reachable)")
    parser.add_argument("--dry-run", action="store_true", help="List tasks without any model calls")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument("--out", default=None, help="Results directory (default data/divergence)")
    parser.add_argument("--tasks", default=None, help="Path to task suite YAML")
    args = parser.parse_args(argv)

    if args.dry_run:
        tasks = load_task_suite(args.tasks)
        payload = {"tasks": [t.to_dict() for t in tasks], "n_tasks": len(tasks)}
        print(json.dumps(payload, indent=2, sort_keys=True) if args.json else
              "\n".join(f"[{t.family}] {t.id}: {t.prompt[:70]}..." for t in tasks))
        return 0

    report = run_benchmark(tasks_path=args.tasks)
    path = persist_report(report, args.out)
    logger.info("[dvg-bench] wrote %s (status=%s)", path, report.status)
    if args.json:
        out = report.to_dict()
        out["report_path"] = str(path)
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
        print(f"\nwrote: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
