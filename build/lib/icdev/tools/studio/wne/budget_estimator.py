"""ICDEV™ Studio — Workflow Narrative Engine (WNE) Budget Estimator.

Estimates phase-by-phase budget from a WorkflowContext using node-type cost
heuristics.  T-shirt sizing reused from tools/simulation/simulation_engine.py.
No LLM.  No DB.  Air-gap safe.

Node cost rules:
    tool     → $0   (automated; already built into the system)
    human    → role_days * (avg_annual_salary_usd / 260)
               or T-shirt hours * hourly_rate if t_shirt_size parameter set
    approval → 0.5 FTE-days * avg_annual_salary_usd / 260 (overhead)
    composite → sum of sub-steps; treated as tool ($0) when sub-steps are
                unavailable in WorkflowContext

CLI:
    python tools/studio/wne/budget_estimator.py --ctx <yaml_path> --json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass

from tools.simulation.simulation_engine import TSHIRT_HOURS
from tools.studio.wne.context_builder import WorkflowContext, WorkflowContextBuilder

_WORKING_DAYS_PER_YEAR = 260
_APPROVAL_FTE_DAYS = 0.5


@dataclass
class PhaseCost:
    phase_name: str
    cost_usd: float
    node_breakdown: list  # list[dict] — node_id, node_type, cost_usd


@dataclass
class BudgetResult:
    phases: list  # list[PhaseCost]
    total_usd: float
    by_type: dict  # {tool: float, human: float, approval: float}


class BudgetEstimator:
    """Estimate workflow budget phase-by-phase from a WorkflowContext."""

    def estimate(self, ctx: WorkflowContext) -> BudgetResult:
        p = ctx.parameters
        avg_salary = float(
            p.get("avg_annual_salary_usd") or p.get("avg_salary_usd") or 100_000
        )
        role_days = float(p.get("role_days") or 5)
        t_shirt = str(p.get("t_shirt_size") or "").upper().strip()

        # Build O(1) lookup from decision_points / approval_gates
        human_node_ids = {dp.node_id for dp in ctx.decision_points}
        approval_node_ids = {ag.node_id for ag in ctx.approval_gates}

        by_type: dict[str, float] = {"tool": 0.0, "human": 0.0, "approval": 0.0}
        phase_costs: list[PhaseCost] = []

        for phase in ctx.phases:
            breakdown: list[dict] = []
            phase_total = 0.0

            for node_id in phase.nodes:
                if node_id in approval_node_ids:
                    node_type = "approval"
                elif node_id in human_node_ids:
                    node_type = "human"
                else:
                    node_type = "tool"

                cost = _node_cost(node_type, avg_salary, role_days, t_shirt)
                breakdown.append(
                    {
                        "node_id": node_id,
                        "node_type": node_type,
                        "cost_usd": round(cost, 2),
                    }
                )
                phase_total += cost
                by_type[node_type] += cost

            phase_costs.append(
                PhaseCost(
                    phase_name=phase.name,
                    cost_usd=round(phase_total, 2),
                    node_breakdown=breakdown,
                )
            )

        total = sum(pc.cost_usd for pc in phase_costs)
        return BudgetResult(
            phases=phase_costs,
            total_usd=round(total, 2),
            by_type={k: round(v, 2) for k, v in by_type.items()},
        )


# ── Pure function ──────────────────────────────────────────────────────────────


def _node_cost(
    node_type: str, avg_salary: float, role_days: float, t_shirt: str
) -> float:
    """Return cost in USD for a single node."""
    if node_type == "tool":
        return 0.0

    daily_rate = avg_salary / _WORKING_DAYS_PER_YEAR

    if node_type == "approval":
        return _APPROVAL_FTE_DAYS * daily_rate

    # human — prefer T-shirt sizing when t_shirt_size parameter is set
    if t_shirt in TSHIRT_HOURS:
        hours = TSHIRT_HOURS[t_shirt]
        hourly_rate = avg_salary / (_WORKING_DAYS_PER_YEAR * 8)
        return hours * hourly_rate

    return role_days * daily_rate


# ── CLI ────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Estimate workflow budget from a workflow YAML template."
    )
    parser.add_argument("--ctx", metavar="YAML_PATH", required=True)
    parser.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args(argv)

    ctx = WorkflowContextBuilder().build(args.ctx)
    result = BudgetEstimator().estimate(ctx)

    if args.as_json:
        print(json.dumps(asdict(result), indent=2, default=str))
    else:
        print(result)


if __name__ == "__main__":
    main()
