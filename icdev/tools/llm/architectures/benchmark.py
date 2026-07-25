"""Cross-architecture benchmark suite for AGX reasoning architectures (agx-bench-01).

Grades the registered architectures (agx-core-01) against one another on
ICDEV-representative tasks, so "which reasoning strategy for this function?" is
answered by *measurement* instead of intuition frozen into ``args/llm_config.yaml``.
agx-bench-02 renders the persisted results as a leaderboard.

Design invariants
------------------
* **Reuse the categorical judge.** Quality is scored by
  ``tools/evolution/fitness.py :: score_full`` (the categorical LLM-as-judge with
  a deterministic small-vocabulary fallback) — this module authors NO new scoring
  stack. Both the runner (which produces an architecture's output) and the judge
  are injectable so tests never need live model access.
* **Never require live models to build/test/merge.** Every cell is executed
  defensively: an unreachable provider, a missing ``.env`` or an air-gapped fresh
  worktree yields a cell with ``status="unmeasured"`` and an honest note rather
  than an exception. A run where nothing is reachable still writes a report with
  overall status ``"unmeasured"`` — "run live to populate".
* **No silent caps.** Anything skipped, dropped or truncated is recorded in the
  report's ``dropped`` list. Architectures with fewer than ``min_samples``
  measured cells are marked ``unmeasured`` rather than ranked on noise.
* **Per-model-family results.** Every architecture runs on >= 2 model families
  (including local Ollama when reachable); aggregation keeps the family axis so a
  frontier-only win cannot hide behind a blended number.
* **Deterministic aggregation.** All scoring composition and aggregation is pure
  Python over the collected cells — reproducible given the same cells.

LLM-agnostic by construction: this module performs no inference itself. It calls
the architecture registry (which routes through ``LLMRouter``) and the fitness
judge (same). Zero vendor-SDK imports; zero hardcoded model IDs.

Adapted from the leaderboard practice in
github.com/FareedKhan-dev/all-agentic-architectures (MIT, Copyright (c) 2025
Fareed Khan). ICDEV adapts the *pattern* and vendors no upstream code.
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

logger = get_logger(__name__)

# Minimum measured cells for an architecture (on a given family) to be ranked
# rather than reported as "unmeasured". Kept low (1) because live model access is
# best-effort; the leaderboard (agx-bench-02) can raise the bar for a headline.
DEFAULT_MIN_SAMPLES = 1

_DEFAULT_TASKS_PATH = "args/agx/benchmark_tasks.yaml"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class BenchmarkTask:
    """One benchmark task drawn from real ICDEV work."""

    id: str
    family: str
    prompt: str
    expected_behavior: str
    function: str = "code_generation"
    golden: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CellResult:
    """Result of one (architecture x model-family x task) cell.

    ``status`` is one of ``measured`` | ``unmeasured`` | ``error``. A cell is
    ``unmeasured`` when the architecture could not run against a live model
    (air-gap / no provider); ``error`` when the harness itself failed. Neither
    fabricates a quality score.
    """

    architecture: str
    model_family: str
    model_id: str
    task_id: str
    task_family: str
    status: str = "unmeasured"
    composite: Optional[float] = None
    correctness: Optional[float] = None
    procedure_following: Optional[float] = None
    conciseness: Optional[float] = None
    cost_usd: float = 0.0
    duration_ms: int = 0
    degraded: bool = False
    stop_reason: str = ""
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AggregateRow:
    """Aggregated view for one (architecture x model-family[, task-family]) group."""

    architecture: str
    model_family: str
    task_family: str = "*"  # "*" = across all task families
    status: str = "unmeasured"  # measured | unmeasured
    n_samples: int = 0
    n_degraded: int = 0
    mean_composite: Optional[float] = None
    mean_correctness: Optional[float] = None
    mean_cost_usd: Optional[float] = None
    mean_duration_ms: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkReport:
    """Full benchmark output — cells + aggregates + honest run metadata."""

    generated_at: str
    status: str  # measured | unmeasured
    architectures: List[str] = field(default_factory=list)
    model_families: List[str] = field(default_factory=list)
    task_families: List[str] = field(default_factory=list)
    n_tasks: int = 0
    cells: List[CellResult] = field(default_factory=list)
    aggregates: List[AggregateRow] = field(default_factory=list)
    dropped: List[Dict[str, Any]] = field(default_factory=list)
    min_samples: int = DEFAULT_MIN_SAMPLES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "status": self.status,
            "architectures": list(self.architectures),
            "model_families": list(self.model_families),
            "task_families": list(self.task_families),
            "n_tasks": self.n_tasks,
            "cells": [c.to_dict() for c in self.cells],
            "aggregates": [a.to_dict() for a in self.aggregates],
            "dropped": list(self.dropped),
            "min_samples": self.min_samples,
        }


# Type aliases for the two injectable seams.
# runner_fn(task, architecture, model_id, family, router) -> ArchitectureResult-like
RunnerFn = Callable[..., Any]
# judge_fn(task, output) -> object with .composite/.correctness/... (FitnessScore)
JudgeFn = Callable[..., Any]


# ---------------------------------------------------------------------------
# Task suite loading
# ---------------------------------------------------------------------------
def load_task_suite(path: Optional[str] = None) -> List[BenchmarkTask]:
    """Load the benchmark task suite from YAML (data, not code).

    Resolves ``path`` relative to the repo root when relative. Returns an empty
    list (never raises) when the file is missing so a bare worktree degrades to a
    "no tasks" report rather than a crash.
    """
    import yaml

    p = Path(path) if path else Path(_DEFAULT_TASKS_PATH)
    if not p.is_absolute():
        p = _repo_root() / p
    if not p.exists():
        logger.warning("[agx-bench] task suite not found at %s", p)
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    tasks: List[BenchmarkTask] = []
    for raw in data.get("tasks", []) or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        tasks.append(
            BenchmarkTask(
                id=str(raw["id"]),
                family=str(raw.get("family", "general")),
                prompt=str(raw.get("prompt", "")).strip(),
                expected_behavior=str(raw.get("expected_behavior", "")).strip(),
                function=str(raw.get("function", "code_generation")),
                golden=str(raw.get("golden", "")).strip(),
            )
        )
    return tasks


def _repo_root() -> Path:
    """Repo root resolved from this file, not cwd (worktree-safe).

    Walks up for the ``.git`` marker so it resolves the same root whether this
    module is loaded from ``tools/`` or the mirrored ``icdev/tools/`` copy (the
    ``tools.* -> icdev.tools.*`` shim means the on-disk file may be under
    ``icdev/``). Falls back to a fixed parent depth when no marker is found.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists() or (parent / "CLAUDE.md").exists():
            return parent
    return here.parents[3]


# ---------------------------------------------------------------------------
# Default (live) runner + judge — both injectable for tests
# ---------------------------------------------------------------------------
def default_runner(task: BenchmarkTask, architecture: str, model_id: str, family: str, router: Any):
    """Run ``architecture`` against ``task`` via the registry (routes through LLMRouter).

    The model is chosen by the router's own config for ``task.function``; we do not
    hardcode a model. ``model_id``/``family`` are advisory labels for attribution.
    Returns an ``ArchitectureResult``. Raises on unreachable providers — the caller
    (``run_cell``) turns that into an ``unmeasured`` cell.
    """
    from tools.llm.architectures import run as run_architecture

    return run_architecture(architecture, task.prompt, router=router, function=task.function)


def default_judge(task: BenchmarkTask, output: str):
    """Score ``output`` against the task rubric with the categorical fitness judge."""
    from tools.evolution.fitness import score_full

    return score_full(
        task_input=task.prompt,
        expected_behavior=task.expected_behavior,
        actual_output=output or "",
        skill_text=task.expected_behavior,
    )


def _derive_model_specs(router: Any, *, count: int = 2, role_key: str = "cod_debater_pool") -> List[Tuple[str, str]]:
    """Best-effort discovery of >= 2 (family, model_id) specs from the router.

    family = provider (ollama/anthropic/openai/...). Returns [] when the router
    can't enumerate models (air-gap / no config) — the caller then produces an
    all-unmeasured report.
    """
    specs: List[Tuple[str, str]] = []
    try:
        models = router.get_diverse_models(role_key, count) or []
    except Exception as exc:
        logger.warning("[agx-bench] get_diverse_models failed: %s", exc)
        return []
    for m in models:
        family = "unknown"
        try:
            cfg = router._get_model_config(m) or {}  # noqa: SLF001 - internal tool, best-effort label
            family = str(cfg.get("provider", "unknown"))
        except Exception:
            pass
        specs.append((family, m))
    return specs


# ---------------------------------------------------------------------------
# Cell execution
# ---------------------------------------------------------------------------
def run_cell(
    task: BenchmarkTask,
    architecture: str,
    family: str,
    model_id: str,
    *,
    runner_fn: RunnerFn,
    judge_fn: JudgeFn,
    router: Any = None,
) -> CellResult:
    """Execute one benchmark cell, never raising.

    Produces a ``measured`` cell on success, ``unmeasured`` when the architecture
    could not reach a model, and ``error`` when the harness/judge itself failed.
    """
    cell = CellResult(
        architecture=architecture,
        model_family=family,
        model_id=model_id,
        task_id=task.id,
        task_family=task.family,
    )
    try:
        result = runner_fn(task, architecture, model_id, family, router)
    except Exception as exc:
        cell.status = "unmeasured"
        cell.note = f"model unreachable: {type(exc).__name__}: {exc}"[:300]
        return cell

    output = getattr(result, "output", None)
    if output is None and isinstance(result, dict):
        output = result.get("output", "")
    cell.cost_usd = float(getattr(result, "cost_usd", 0.0) or 0.0)
    cell.duration_ms = int(getattr(result, "duration_ms", 0) or 0)
    cell.degraded = bool(getattr(result, "degraded", False))
    cell.stop_reason = str(getattr(result, "stop_reason", "") or "")

    if not output:
        # A degraded/empty envelope is honest signal, not a failure of the harness.
        cell.status = "unmeasured"
        cell.note = f"empty output (degraded={cell.degraded}, stop={cell.stop_reason})"
        return cell

    try:
        score = judge_fn(task, output)
    except Exception as exc:
        cell.status = "error"
        cell.note = f"judge failed: {type(exc).__name__}: {exc}"[:300]
        return cell

    cell.status = "measured"
    cell.composite = _round(getattr(score, "composite", None))
    cell.correctness = _round(getattr(score, "correctness", None))
    cell.procedure_following = _round(getattr(score, "procedure_following", None))
    cell.conciseness = _round(getattr(score, "conciseness", None))
    return cell


def _round(v: Any) -> Optional[float]:
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Aggregation (pure, deterministic)
# ---------------------------------------------------------------------------
def aggregate(cells: List[CellResult], *, min_samples: int = DEFAULT_MIN_SAMPLES) -> List[AggregateRow]:
    """Aggregate cells into per-(architecture, model-family) and per-task-family rows.

    Deterministic and pure. Groups with fewer than ``min_samples`` measured cells
    are emitted with ``status="unmeasured"`` and null means — never ranked on noise.
    Rows are sorted for stable output: architecture, model_family, task_family.
    """
    # (architecture, model_family, task_family) -> list of measured cells
    buckets: Dict[Tuple[str, str, str], List[CellResult]] = {}
    for c in cells:
        if c.status != "measured":
            continue
        for tf in (c.task_family, "*"):  # per-task-family row + an across-families row
            buckets.setdefault((c.architecture, c.model_family, tf), []).append(c)

    rows: List[AggregateRow] = []
    for (arch, fam, tf), group in buckets.items():
        n = len(group)
        row = AggregateRow(architecture=arch, model_family=fam, task_family=tf, n_samples=n)
        row.n_degraded = sum(1 for c in group if c.degraded)
        if n >= min_samples:
            row.status = "measured"
            row.mean_composite = _mean([c.composite for c in group])
            row.mean_correctness = _mean([c.correctness for c in group])
            row.mean_cost_usd = _mean([c.cost_usd for c in group])
            row.mean_duration_ms = _mean([c.duration_ms for c in group])
        else:
            row.status = "unmeasured"
        rows.append(row)

    rows.sort(key=lambda r: (r.architecture, r.model_family, r.task_family))
    return rows


def _mean(values: List[Any]) -> Optional[float]:
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    return round(statistics.fmean(nums), 4)


# ---------------------------------------------------------------------------
# Top-level run
# ---------------------------------------------------------------------------
def run_benchmark(
    *,
    architectures: Optional[List[str]] = None,
    model_specs: Optional[List[Tuple[str, str]]] = None,
    tasks: Optional[List[BenchmarkTask]] = None,
    runner_fn: Optional[RunnerFn] = None,
    judge_fn: Optional[JudgeFn] = None,
    router: Any = None,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    tasks_path: Optional[str] = None,
) -> BenchmarkReport:
    """Run the full benchmark and return a :class:`BenchmarkReport`.

    Every seam is injectable so the harness runs deterministically in tests with
    no model access:

    * ``architectures`` — names to grade (default: everything registered).
    * ``model_specs``   — list of ``(family, model_id)``; default derives >= 2
      from the router, or yields an all-unmeasured report when none are reachable.
    * ``tasks``         — task objects (default: loaded from the YAML suite).
    * ``runner_fn`` / ``judge_fn`` — default to live registry + fitness judge.

    Never raises for model/provider problems; those surface as ``unmeasured``
    cells and a report ``status`` of ``"unmeasured"``.
    """
    runner_fn = runner_fn or default_runner
    judge_fn = judge_fn or default_judge
    dropped: List[Dict[str, Any]] = []

    if tasks is None:
        tasks = load_task_suite(tasks_path)
    if not tasks:
        dropped.append({"what": "tasks", "reason": "task suite empty or not found"})

    if architectures is None:
        architectures = _discover_architectures(dropped)

    if model_specs is None:
        if router is None:
            router = _try_build_router(dropped)
        model_specs = _derive_model_specs(router) if router is not None else []
    if len(model_specs) < 2:
        dropped.append(
            {
                "what": "model_families",
                "reason": f"only {len(model_specs)} model family(ies) available; "
                "cross-family comparison incomplete — run live with >= 2 families "
                "(incl. local Ollama) to populate",
            }
        )

    cells: List[CellResult] = []
    if model_specs and tasks and architectures:
        for arch in architectures:
            for family, model_id in model_specs:
                for task in tasks:
                    cells.append(
                        run_cell(
                            task,
                            arch,
                            family,
                            model_id,
                            runner_fn=runner_fn,
                            judge_fn=judge_fn,
                            router=router,
                        )
                    )
    else:
        dropped.append(
            {
                "what": "cells",
                "reason": "no cells executed — missing tasks, architectures, or "
                "reachable models; report is a scaffold to be populated live",
            }
        )

    aggregates = aggregate(cells, min_samples=min_samples)
    measured_any = any(c.status == "measured" for c in cells)

    report = BenchmarkReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status="measured" if measured_any else "unmeasured",
        architectures=sorted(architectures or []),
        model_families=sorted({f for f, _ in (model_specs or [])}),
        task_families=sorted({t.family for t in (tasks or [])}),
        n_tasks=len(tasks or []),
        cells=cells,
        aggregates=aggregates,
        dropped=dropped,
        min_samples=min_samples,
    )
    return report


def _discover_architectures(dropped: List[Dict[str, Any]]) -> List[str]:
    try:
        from tools.llm.architectures import list_architectures

        arches = list_architectures()
        if not arches:
            dropped.append({"what": "architectures", "reason": "registry empty"})
        return arches
    except Exception as exc:
        dropped.append({"what": "architectures", "reason": f"import failed: {exc}"})
        return []


def _try_build_router(dropped: List[Dict[str, Any]]) -> Any:
    try:
        from tools.llm.router import LLMRouter

        return LLMRouter()
    except Exception as exc:
        dropped.append({"what": "router", "reason": f"LLMRouter unavailable: {exc}"})
        return None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def default_results_dir() -> Path:
    return _repo_root() / "data" / "agx"


def persist_report(report: BenchmarkReport, out_dir: Optional[str] = None) -> Path:
    """Write the report to ``data/agx/benchmark_<utc>.json`` and update ``latest``.

    Deterministic JSON (sorted keys). Returns the timestamped path. bench-02 reads
    ``benchmark_latest.json``.
    """
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
    except Exception:
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def render_markdown(report: BenchmarkReport) -> str:
    lines = [
        "# AGX Cross-Architecture Benchmark",
        "",
        f"- generated: {report.generated_at}",
        f"- status: **{report.status}**",
        f"- tasks: {report.n_tasks} across families: {', '.join(report.task_families) or '(none)'}",
        f"- model families: {', '.join(report.model_families) or '(none reachable)'}",
        f"- architectures: {', '.join(report.architectures) or '(none)'}",
        "",
    ]
    if report.dropped:
        lines.append("## Dropped / unmeasured (no silent caps)")
        for d in report.dropped:
            lines.append(f"- **{d.get('what')}**: {d.get('reason')}")
        lines.append("")
    lines.append("## Aggregates (architecture x model-family, across task families)")
    lines.append("")
    lines.append("| architecture | family | task-family | status | n | mean_composite | mean_cost | mean_ms |")
    lines.append("|---|---|---|---|--:|--:|--:|--:|")
    for r in report.aggregates:
        if r.task_family != "*":
            continue
        lines.append(
            f"| {r.architecture} | {r.model_family} | {r.task_family} | {r.status} | "
            f"{r.n_samples} | {_fmt(r.mean_composite)} | {_fmt(r.mean_cost_usd)} | {_fmt(r.mean_duration_ms)} |"
        )
    return "\n".join(lines) + "\n"


def _fmt(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:g}"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="AGX cross-architecture benchmark (agx-bench-01)")
    parser.add_argument("--run", action="store_true", help="Run the benchmark (live models if reachable)")
    parser.add_argument("--dry-run", action="store_true", help="List tasks/architectures without any model calls")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument("--markdown", action="store_true", help="Emit Markdown")
    parser.add_argument("--out", default=None, help="Results directory (default data/agx)")
    parser.add_argument("--architectures", default=None, help="Comma-separated subset of architectures")
    parser.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES)
    parser.add_argument("--tasks", default=None, help="Path to task suite YAML")
    args = parser.parse_args(argv)

    arch_subset = [a.strip() for a in args.architectures.split(",")] if args.architectures else None

    if args.dry_run:
        tasks = load_task_suite(args.tasks)
        arches = arch_subset or _discover_architectures([])
        payload = {
            "tasks": [t.to_dict() for t in tasks],
            "architectures": sorted(arches),
            "n_tasks": len(tasks),
        }
        print(json.dumps(payload, indent=2, sort_keys=True) if args.json else render_dry(payload))
        return 0

    report = run_benchmark(
        architectures=arch_subset,
        min_samples=args.min_samples,
        tasks_path=args.tasks,
    )
    path = persist_report(report, args.out)
    logger.info("[agx-bench] wrote %s (status=%s)", path, report.status)
    if args.json:
        out = report.to_dict()
        out["report_path"] = str(path)
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
        print(f"\nwrote: {path}")
    return 0


def render_dry(payload: Dict[str, Any]) -> str:
    lines = [f"tasks: {payload['n_tasks']}", "architectures: " + ", ".join(payload["architectures"]), ""]
    for t in payload["tasks"]:
        lines.append(f"  [{t['family']}] {t['id']}: {t['prompt'][:70]}...")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
