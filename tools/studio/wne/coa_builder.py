"""ICDEV™ Studio — Workflow Narrative Engine (WNE) COA Builder.

Builds a 3-COA comparison (COA A / B / C) from a WorkflowContext.

Primary path — composite node extraction:
  Scans raw YAML steps for composite nodes whose id or sub_steps contain
  'coa_a', 'coa_b', 'coa_c'.  Uses step.name + step.description as labels.

Fallback path — parametric generation:
  COA A  (Speed / Organic):       1.5× timeline, 0.4× cost, high risk
  COA B  (Balanced — recommended): 1.0× timeline, 1.0× cost, medium risk
  COA C  (Comprehensive / Sprint): 0.7× timeline, 2.2× cost, low risk

  Costs are derived from narrative_context.parameters; defaults to $100,000
  when parameters are absent.

No LLM required.  Air-gap safe.

CLI:
    python tools/studio/wne/coa_builder.py --build <yaml_path> --json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Union

import yaml

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ── Dataclasses ────────────────────────────────────────────────────────────────


@dataclass
class COAOption:
    name: str
    approach: str
    timeline_months: float
    cost_usd: float
    risk_level: str  # low | medium | high
    recommendation: bool
    rationale: str


@dataclass
class COAResult:
    coa_a: COAOption
    coa_b: COAOption
    coa_c: COAOption


# ── Constants ──────────────────────────────────────────────────────────────────

_COA_KEYS = ("coa_a", "coa_b", "coa_c")

# Timeline and cost multipliers relative to ctx.timeframe_months and base cost
_MULTIPLIERS: dict[str, dict[str, float]] = {
    "coa_a": {"timeline": 1.5, "cost": 0.4},
    "coa_b": {"timeline": 1.0, "cost": 1.0},
    "coa_c": {"timeline": 0.7, "cost": 2.2},
}

_PARAMETRIC_DEFAULTS: dict[str, dict[str, Any]] = {
    "coa_a": {
        "name": "COA A — Speed / Organic",
        "approach": (
            "Training only; no lab environment; internal upskilling at full timeline. "
            "Lowest near-term cost, relies entirely on organic skill development."
        ),
        "risk_level": "high",
        "recommendation": False,
        "rationale": (
            "Lowest cost but highest execution risk.  No dedicated lab or OJT "
            "infrastructure; schedule slippage likely without accelerants."
        ),
    },
    "coa_b": {
        "name": "COA B — Balanced (Recommended)",
        "approach": (
            "Lab standup + structured training + on-the-job tasks; "
            "balanced speed-vs-cost tradeoff."
        ),
        "risk_level": "medium",
        "recommendation": True,
        "rationale": (
            "Optimal risk-cost-speed tradeoff.  Lab environment accelerates applied "
            "learning while structured training ensures full coverage."
        ),
    },
    "coa_c": {
        "name": "COA C — Comprehensive / Sprint",
        "approach": (
            "All tracks in parallel: ILT, hackathons, dedicated AI exploration time, "
            "and full lab.  Fastest time-to-capability at highest near-term cost."
        ),
        "risk_level": "low",
        "recommendation": False,
        "rationale": (
            "Highest cost but lowest execution risk.  Sprint acquisition model "
            "delivers capability fastest at a 2.2× cost premium."
        ),
    },
}


# ── Builder ────────────────────────────────────────────────────────────────────


class COABuilder:
    """Build a 3-COA comparison from a WorkflowContext or a YAML file."""

    # ── Public API ─────────────────────────────────────────────────────────────

    def build(self, ctx: Any) -> COAResult:
        """Build COAResult from a pre-constructed WorkflowContext.

        Scans ctx.phases for coa_a/b/c node IDs (primary detection via
        WorkflowContext).  Falls back to parametric generation when none are
        found.
        """
        node_info = self._extract_from_phases(ctx)
        if node_info:
            return self._assemble(ctx, node_info)
        return self._build_parametric(ctx)

    def build_from_yaml(self, yaml_path: Union[str, Path]) -> COAResult:
        """Build COAResult directly from a workflow YAML file.

        Provides richer primary-path extraction by scanning the raw YAML steps
        for composite nodes (sub_steps) and direct coa_* steps before falling
        back to WorkflowContext-phase detection and then parametric generation.
        """
        data = self._load_yaml(yaml_path)
        raw_steps: list[dict] = data.get("steps") or []

        # Primary path: raw-step composite/direct coa_* detection
        node_info = self._extract_from_raw_steps(raw_steps)

        # Build WorkflowContext for parameters + timeframe
        from tools.studio.wne.context_builder import WorkflowContextBuilder
        ctx = WorkflowContextBuilder().build(data)

        if node_info:
            return self._assemble(ctx, node_info)

        # Secondary: WorkflowContext phase-based detection
        phase_info = self._extract_from_phases(ctx)
        if phase_info:
            return self._assemble(ctx, phase_info)

        return self._build_parametric(ctx)

    # ── Extraction helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_from_raw_steps(raw_steps: list[dict]) -> dict[str, dict] | None:
        """Scan raw YAML steps for composite or direct coa_* nodes.

        Checks each step's 'id' and 'sub_steps' list.  Returns a dict keyed
        by coa_a/b/c with {name, approach} entries, or None if all three are
        not found.
        """
        found: dict[str, dict] = {}
        for step in raw_steps:
            step_id = step.get("id", "")
            if step_id in _COA_KEYS and step_id not in found:
                found[step_id] = {
                    "name": step.get("name", step_id),
                    "approach": step.get("description", ""),
                }
            # Composite node: scan sub_steps
            for sub in step.get("sub_steps") or []:
                sub_id = sub.get("id", "")
                if sub_id in _COA_KEYS and sub_id not in found:
                    found[sub_id] = {
                        "name": sub.get("name", sub_id),
                        "approach": sub.get("description", ""),
                    }
        return found if len(found) == 3 else None

    @staticmethod
    def _extract_from_phases(ctx: Any) -> dict[str, dict] | None:
        """Scan WorkflowContext phases for coa_a/b/c node IDs.

        Returns a dict keyed by coa_a/b/c with {name, approach} entries
        (name from single-node phase label; approach left empty), or None
        if not all three are found.
        """
        found: dict[str, dict] = {}
        for phase in ctx.phases:
            for node_id in phase.nodes:
                if node_id in _COA_KEYS and node_id not in found:
                    # Single-node phase: phase.name was set to step.name by builder
                    name = phase.name if len(phase.nodes) == 1 else ""
                    found[node_id] = {"name": name, "approach": ""}
        return found if len(found) == 3 else None

    # ── Assembly ───────────────────────────────────────────────────────────────

    def _assemble(self, ctx: Any, node_info: dict[str, dict]) -> COAResult:
        """Assemble COAResult from detected node info + ctx parameters."""
        base_cost = self._base_cost(ctx.parameters)
        timeframe = ctx.timeframe_months or 12
        options: dict[str, COAOption] = {}
        for key in _COA_KEYS:
            mult = _MULTIPLIERS[key]
            defaults = _PARAMETRIC_DEFAULTS[key]
            info = node_info.get(key, {})
            options[key] = COAOption(
                name=info.get("name") or defaults["name"],
                approach=info.get("approach") or defaults["approach"],
                timeline_months=round(timeframe * mult["timeline"], 1),
                cost_usd=round(base_cost * mult["cost"], 2),
                risk_level=defaults["risk_level"],
                recommendation=defaults["recommendation"],
                rationale=defaults["rationale"],
            )
        return COAResult(**options)

    def _build_parametric(self, ctx: Any) -> COAResult:
        """Pure parametric fallback using ctx.parameters and ctx.timeframe_months."""
        base_cost = self._base_cost(ctx.parameters)
        timeframe = ctx.timeframe_months or 12
        options: dict[str, COAOption] = {}
        for key in _COA_KEYS:
            mult = _MULTIPLIERS[key]
            defaults = _PARAMETRIC_DEFAULTS[key]
            options[key] = COAOption(
                name=defaults["name"],
                approach=defaults["approach"],
                timeline_months=round(timeframe * mult["timeline"], 1),
                cost_usd=round(base_cost * mult["cost"], 2),
                risk_level=defaults["risk_level"],
                recommendation=defaults["recommendation"],
                rationale=defaults["rationale"],
            )
        return COAResult(**options)

    # ── Cost derivation ────────────────────────────────────────────────────────

    @staticmethod
    def _base_cost(params: dict) -> float:
        """Derive the 1.0× cost baseline from narrative_context.parameters.

        Balanced COA (B) represents the 1.0× reference:
          base = developers_targeted * training_cost_per_person_usd + lab_standup_cost_usd

        Falls back to $100,000 when parameters are absent.
        """
        devs = int(params.get("developers_targeted") or params.get("workforce_size") or 0)
        training = float(params.get("training_cost_per_person_usd") or 0)
        lab = float(params.get("lab_standup_cost_usd") or 0)
        base = devs * training + lab
        return base if base > 0 else 100_000.0

    # ── YAML loader ────────────────────────────────────────────────────────────

    @staticmethod
    def _load_yaml(path: Union[str, Path]) -> dict:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}


# ── CLI ────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Build a 3-COA comparison from a workflow YAML template."
    )
    parser.add_argument("--build", metavar="YAML_PATH", required=True)
    parser.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args(argv)

    result = COABuilder().build_from_yaml(args.build)

    if args.as_json:
        print(json.dumps(asdict(result), indent=2))
    else:
        for key in _COA_KEYS:
            opt: COAOption = getattr(result, key)
            rec = " [RECOMMENDED]" if opt.recommendation else ""
            print(f"\n{opt.name}{rec}")
            print(f"  Approach:  {opt.approach}")
            print(f"  Timeline:  {opt.timeline_months} months")
            print(f"  Cost:      ${opt.cost_usd:,.0f}")
            print(f"  Risk:      {opt.risk_level.upper()}")
            print(f"  Rationale: {opt.rationale}")


if __name__ == "__main__":
    main()
