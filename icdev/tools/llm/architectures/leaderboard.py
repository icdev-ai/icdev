"""Leaderboard + evidence-based routing recommendation over benchmark results (agx-bench-02).

Consumes the persisted agx-bench-01 report (``data/agx/benchmark_latest.json``) and:

1. **Leaderboard** — ranks architectures per (task-family x model-family) on quality,
   with cost and latency alongside. Per-model-family, never a single blended number
   that could hide a frontier-only win. Insufficient-sample groups are shown as
   ``unmeasured``, not ranked on noise.
2. **Recommendation** — proposes per-function architecture routing defaults, WITH the
   evidence cited inline. It **recommends only; it never writes config.** The
   committed ``args/llm_config.yaml`` ``architectures:`` block stays all-null (current
   behavior). Applying a recommendation is a deliberate human edit (agx-core-03).
3. **Regression guard** — ``check_no_degradation`` flags any architecture that config
   routes a benchmarked function to but that the measurements show performs *below*
   the baseline. ``is_config_noop`` proves the shipped config changes no runtime
   selection.

Honesty requirements (from the task): report where NO architecture beat the current
baseline — the most useful result the bench can produce — and never bury it. The
baseline reference is the ``baseline`` architecture (a single direct model call). When
the baseline is unmeasured, this module refuses to assert any improvement and
recommends keeping current behavior.

Deterministic and pure over the report dict. No inference here; LLM-agnostic.
Pattern adapted from github.com/FareedKhan-dev/all-agentic-architectures
(MIT, (c) 2025 Fareed Khan) — pattern only, no upstream code vendored.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

BASELINE_ARCH = "baseline"

# Thresholds for turning a measured win into a routing recommendation. A quality
# win that costs far more is not a win — see the task's "3% at 5x cost is a loss".
DEFAULT_MIN_MARGIN = 0.05      # min mean_composite improvement over baseline
DEFAULT_MAX_COST_RATIO = 2.0   # reject a winner costing > 2x the baseline
DEFAULT_MIN_SAMPLES = 3        # min measured cells before a group can be ranked/recommended


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------
@dataclass
class LeaderboardEntry:
    task_family: str
    model_family: str
    architecture: str
    rank: Optional[int]  # None when unmeasured
    status: str          # measured | unmeasured
    n_samples: int
    mean_composite: Optional[float]
    mean_cost_usd: Optional[float]
    mean_duration_ms: Optional[float]
    beats_baseline: Optional[bool] = None  # None when baseline unmeasured
    composite_delta_vs_baseline: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _agg_rows(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [r for r in (report.get("aggregates") or []) if isinstance(r, dict)]


def build_leaderboard(report: Dict[str, Any], *, min_samples: int = DEFAULT_MIN_SAMPLES) -> List[LeaderboardEntry]:
    """Build ranked leaderboard entries per (task_family, model_family).

    Ranks measured architectures by ``mean_composite`` descending (cost breaks
    ties toward cheaper). Entries below ``min_samples`` are marked ``unmeasured``
    with ``rank=None``. Deterministic ordering.
    """
    rows = _agg_rows(report)
    # index baseline composite per (task_family, model_family)
    baseline: Dict[tuple, Optional[float]] = {}
    for r in rows:
        if r.get("architecture") == BASELINE_ARCH and r.get("status") == "measured":
            baseline[(r.get("task_family"), r.get("model_family"))] = r.get("mean_composite")

    # group rows by (task_family, model_family)
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault((r.get("task_family"), r.get("model_family")), []).append(r)

    entries: List[LeaderboardEntry] = []
    for (tf, mf), grp in groups.items():
        base = baseline.get((tf, mf))
        measured = [
            r for r in grp
            if r.get("status") == "measured"
            and int(r.get("n_samples", 0)) >= min_samples
            and r.get("mean_composite") is not None
        ]
        # sort: composite desc, then cheaper cost, then name for determinism
        measured.sort(
            key=lambda r: (-float(r["mean_composite"]), float(r.get("mean_cost_usd") or 0.0), r.get("architecture", ""))
        )
        ranked_names = {r["architecture"]: i + 1 for i, r in enumerate(measured)}
        for r in grp:
            arch = r.get("architecture", "")
            is_measured = arch in ranked_names
            comp = r.get("mean_composite")
            delta = round(comp - base, 4) if (comp is not None and base is not None) else None
            entries.append(
                LeaderboardEntry(
                    task_family=tf,
                    model_family=mf,
                    architecture=arch,
                    rank=ranked_names.get(arch),
                    status="measured" if is_measured else "unmeasured",
                    n_samples=int(r.get("n_samples", 0)),
                    mean_composite=comp,
                    mean_cost_usd=r.get("mean_cost_usd"),
                    mean_duration_ms=r.get("mean_duration_ms"),
                    beats_baseline=(delta > 0) if delta is not None else None,
                    composite_delta_vs_baseline=delta,
                )
            )
    entries.sort(key=lambda e: (e.task_family, e.model_family, e.rank if e.rank is not None else 1_000, e.architecture))
    return entries


# ---------------------------------------------------------------------------
# Recommendation (recommend only — never writes config)
# ---------------------------------------------------------------------------
@dataclass
class Recommendation:
    """A per-task-family routing recommendation with inline evidence.

    ``decision`` is one of:
      * ``recommend_change``    — a measured architecture beats baseline by margin
                                  and is not cost-prohibitive.
      * ``keep_current``        — measured, but nothing beats baseline by margin
                                  (the honest, must-not-be-buried result).
      * ``insufficient_evidence`` — baseline or candidates unmeasured / too few
                                  samples; no recommendation.
    """

    task_family: str
    decision: str
    recommended_architecture: Optional[str] = None
    current: str = "current_behavior (config null)"
    evidence: List[str] = field(default_factory=list)
    apply_hint: str = ""  # the exact, human-applied llm_config edit (NOT auto-applied)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def recommend_defaults(
    report: Dict[str, Any],
    *,
    min_margin: float = DEFAULT_MIN_MARGIN,
    max_cost_ratio: float = DEFAULT_MAX_COST_RATIO,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> List[Recommendation]:
    """Derive per-task-family routing recommendations from the report.

    A recommendation is emitted only when, on **every** measured model family for
    that task family, a single architecture beats the baseline by >= ``min_margin``
    and costs <= ``max_cost_ratio`` x baseline. Requiring the win to hold across
    families enforces the LLM-agnostic constraint (no frontier-only wins). When the
    baseline is unmeasured or samples are too few, the decision is
    ``insufficient_evidence`` and current behavior is kept.

    NEVER writes config. The returned ``apply_hint`` is for a human to apply.
    """
    rows = _agg_rows(report)
    task_families = sorted({r.get("task_family") for r in rows if r.get("task_family") not in (None, "*")})
    model_families = sorted({r.get("model_family") for r in rows if r.get("model_family")})

    recs: List[Recommendation] = []
    for tf in task_families:
        # per family, map architecture -> (composite, cost) for measured groups
        per_family: Dict[str, Dict[str, Dict[str, float]]] = {}
        baseline_per_family: Dict[str, Optional[float]] = {}
        baseline_cost_per_family: Dict[str, Optional[float]] = {}
        for mf in model_families:
            grp = [
                r for r in rows
                if r.get("task_family") == tf and r.get("model_family") == mf
                and r.get("status") == "measured" and int(r.get("n_samples", 0)) >= min_samples
                and r.get("mean_composite") is not None
            ]
            fam_map: Dict[str, Dict[str, float]] = {}
            for r in grp:
                fam_map[r["architecture"]] = {
                    "composite": float(r["mean_composite"]),
                    "cost": float(r.get("mean_cost_usd") or 0.0),
                }
            if fam_map:
                per_family[mf] = fam_map
                if BASELINE_ARCH in fam_map:
                    baseline_per_family[mf] = fam_map[BASELINE_ARCH]["composite"]
                    baseline_cost_per_family[mf] = fam_map[BASELINE_ARCH]["cost"]

        measured_families = [mf for mf in per_family if baseline_per_family.get(mf) is not None]
        if not measured_families:
            recs.append(
                Recommendation(
                    task_family=tf,
                    decision="insufficient_evidence",
                    evidence=[
                        f"No measured baseline for '{tf}' on any model family "
                        f"(need >= {min_samples} samples incl. the 'baseline' architecture). "
                        "Run the benchmark live to populate."
                    ],
                )
            )
            continue

        # candidate architectures that beat baseline by margin on EVERY measured family,
        # and are not cost-prohibitive on any.
        candidate_arches = set()
        for mf in measured_families:
            for arch in per_family[mf]:
                if arch != BASELINE_ARCH:
                    candidate_arches.add(arch)

        winners: List[tuple] = []  # (min_margin_across_families, arch)
        evidence_all: List[str] = []
        for arch in sorted(candidate_arches):
            margins = []
            cost_ok = True
            present_on_all = True
            for mf in measured_families:
                fam = per_family[mf]
                if arch not in fam:
                    present_on_all = False
                    break
                base_comp = baseline_per_family[mf]
                base_cost = baseline_cost_per_family[mf] or 0.0
                margin = fam[arch]["composite"] - base_comp
                margins.append(margin)
                if base_cost > 0 and fam[arch]["cost"] > max_cost_ratio * base_cost:
                    cost_ok = False
                evidence_all.append(
                    f"{arch} vs baseline on {mf} ({tf}): "
                    f"Δcomposite={margin:+.4f}, cost={fam[arch]['cost']:.5f} vs {base_cost:.5f}"
                )
            if present_on_all and margins and min(margins) >= min_margin and cost_ok:
                winners.append((min(margins), arch))

        if winners:
            winners.sort(reverse=True)
            best_margin, best_arch = winners[0]
            recs.append(
                Recommendation(
                    task_family=tf,
                    decision="recommend_change",
                    recommended_architecture=best_arch,
                    evidence=[
                        f"'{best_arch}' beats baseline by >= {min_margin} composite on "
                        f"ALL {len(measured_families)} measured model families "
                        f"(worst-family Δ={best_margin:+.4f}), within {max_cost_ratio}x baseline cost."
                    ] + evidence_all,
                    apply_hint=(
                        "Human-apply in args/llm_config.yaml under architectures.functions: "
                        f"map the function(s) served by task-family '{tf}' to '{best_arch}'. "
                        "Do NOT auto-apply; agx-core-03 selection is opt-in."
                    ),
                )
            )
        else:
            recs.append(
                Recommendation(
                    task_family=tf,
                    decision="keep_current",
                    evidence=[
                        f"No architecture beat the baseline by >= {min_margin} composite "
                        f"across all measured model families for '{tf}' (within cost limits). "
                        "Keeping current behavior is the correct, evidence-based choice."
                    ] + evidence_all,
                )
            )
    return recs


# ---------------------------------------------------------------------------
# Regression guard
# ---------------------------------------------------------------------------
def is_config_noop(architectures_block: Optional[Dict[str, Any]]) -> bool:
    """True when the ``architectures:`` config selects nothing (current behavior).

    The shipped config MUST satisfy this — it is the proof that agx-bench-02 did
    not autonomously flip any platform-wide routing default.
    """
    if not architectures_block:
        return True
    if architectures_block.get("default") not in (None, "", "null"):
        return False
    if architectures_block.get("functions"):
        return False
    if architectures_block.get("roles"):
        return False
    return True


def check_no_degradation(
    report: Dict[str, Any],
    architectures_block: Optional[Dict[str, Any]],
    *,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> List[Dict[str, Any]]:
    """Flag any configured architecture measured to perform *below* baseline.

    The regression guard: if a future edit routes a function/role to an
    architecture that the benchmark shows degrades quality vs baseline on any
    measured family, this returns a finding. For the shipped (all-null) config it
    returns [] — nothing is routed, nothing can regress.
    """
    findings: List[Dict[str, Any]] = []
    block = architectures_block or {}
    configured = set()
    for v in (block.get("functions") or {}).values():
        if v:
            configured.add(v)
    for v in (block.get("roles") or {}).values():
        if v:
            configured.add(v)
    default = block.get("default")
    if default:
        configured.add(default)
    if not configured:
        return findings

    rows = _agg_rows(report)
    baseline: Dict[tuple, float] = {}
    for r in rows:
        if r.get("architecture") == BASELINE_ARCH and r.get("status") == "measured" and r.get("mean_composite") is not None:
            baseline[(r.get("task_family"), r.get("model_family"))] = float(r["mean_composite"])
    for r in rows:
        arch = r.get("architecture")
        if arch not in configured or r.get("status") != "measured":
            continue
        if int(r.get("n_samples", 0)) < min_samples or r.get("mean_composite") is None:
            continue
        base = baseline.get((r.get("task_family"), r.get("model_family")))
        if base is not None and float(r["mean_composite"]) < base:
            findings.append(
                {
                    "architecture": arch,
                    "task_family": r.get("task_family"),
                    "model_family": r.get("model_family"),
                    "mean_composite": r.get("mean_composite"),
                    "baseline_composite": base,
                    "regression": round(float(r["mean_composite"]) - base, 4),
                }
            )
    return findings


# ---------------------------------------------------------------------------
# Rendering + CLI
# ---------------------------------------------------------------------------
def render_markdown(report: Dict[str, Any], entries: List[LeaderboardEntry], recs: List[Recommendation]) -> str:
    lines = [
        "# AGX Architecture Leaderboard (agx-bench-02)",
        "",
        f"- source status: **{report.get('status', 'unknown')}**",
        f"- model families: {', '.join(report.get('model_families') or []) or '(none reachable)'}",
        "",
    ]
    if report.get("status") != "measured":
        lines += [
            "> **Unmeasured** — the benchmark has not been run against live models yet.",
            "> Run `python tools/llm/architectures/benchmark.py --run` with >= 2 model",
            "> families (incl. local Ollama) to populate, then re-render.",
            "",
        ]
    lines += ["## Leaderboard (per task-family x model-family)", "",
              "| task-family | model-family | rank | architecture | status | n | composite | Δ vs baseline | cost | ms |",
              "|---|---|--:|---|---|--:|--:|--:|--:|--:|"]
    for e in entries:
        lines.append(
            f"| {e.task_family} | {e.model_family} | {e.rank if e.rank is not None else '-'} | "
            f"{e.architecture} | {e.status} | {e.n_samples} | {_fmt(e.mean_composite)} | "
            f"{_fmt(e.composite_delta_vs_baseline)} | {_fmt(e.mean_cost_usd)} | {_fmt(e.mean_duration_ms)} |"
        )
    lines += ["", "## Routing recommendations (RECOMMEND ONLY — config stays all-null)", ""]
    for rec in recs:
        lines.append(f"### {rec.task_family}: **{rec.decision}**"
                     + (f" -> `{rec.recommended_architecture}`" if rec.recommended_architecture else ""))
        for ev in rec.evidence:
            lines.append(f"- {ev}")
        if rec.apply_hint:
            lines.append(f"- _apply hint (human):_ {rec.apply_hint}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _fmt(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:g}"


def _load_report(path: Optional[str]) -> Optional[Dict[str, Any]]:
    if path:
        p = Path(path)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    from tools.llm.architectures.benchmark import load_latest_report

    return load_latest_report()


def _load_architectures_block() -> Dict[str, Any]:
    try:
        import yaml

        from tools.llm.config_path import resolve_llm_config_path

        cfg = yaml.safe_load(Path(resolve_llm_config_path()).read_text(encoding="utf-8")) or {}
        return cfg.get("architectures") or {}
    except Exception:
        return {}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="AGX leaderboard + routing recommendations (agx-bench-02)")
    parser.add_argument("--report", default=None, help="Path to a benchmark report JSON (default: data/agx/benchmark_latest.json)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--recommend", action="store_true", help="Emit only the routing recommendations")
    parser.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES)
    args = parser.parse_args(argv)

    report = _load_report(args.report)
    if report is None:
        report = {"status": "unmeasured", "aggregates": [], "model_families": [], "dropped": [
            {"what": "report", "reason": "no benchmark report found; run agx-bench-01 first"}]}

    entries = build_leaderboard(report, min_samples=args.min_samples)
    recs = recommend_defaults(report, min_samples=args.min_samples)

    if args.recommend and args.json:
        print(json.dumps([r.to_dict() for r in recs], indent=2, sort_keys=True))
        return 0
    if args.json:
        print(json.dumps(
            {
                "status": report.get("status"),
                "leaderboard": [e.to_dict() for e in entries],
                "recommendations": [r.to_dict() for r in recs],
                "config_is_noop": is_config_noop(_load_architectures_block()),
            },
            indent=2, sort_keys=True,
        ))
        return 0
    print(render_markdown(report, entries, recs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
