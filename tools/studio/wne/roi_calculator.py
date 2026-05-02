"""ICDEV™ Studio — Workflow Narrative Engine (WNE) ROI Calculator.

Computes ROI, NPV, payback period, and sensitivity table from WorkflowContext
parameters.  No LLM.  No DB.  Air-gap safe.

CLI:
    python tools/studio/wne/roi_calculator.py --ctx <yaml_path> --json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Optional

from tools.studio.wne.context_builder import WorkflowContext, WorkflowContextBuilder

_DISCOUNT_RATE_ANNUAL = 0.08

# Required parameter keys
_REQUIRED = (
    "developers_targeted",
    "training_cost_per_person_usd",
    "lab_standup_cost_usd",
    "workforce_size",
    "avg_annual_salary_usd",
    "ai_productivity_gain_pct",
)


@dataclass
class ROIResult:
    total_investment_usd: Optional[float]
    total_value_3yr_usd: Optional[float]
    roi_pct: Optional[float]
    payback_months: Optional[float]
    npv_usd: Optional[float]
    sensitivity_table: list
    note: str = ""


class ROICalculator:
    """Calculate ROI, NPV, payback, and sensitivity for a WNE initiative."""

    def calculate(self, ctx: WorkflowContext) -> ROIResult:
        p = ctx.parameters
        try:
            developers_targeted = float(p["developers_targeted"])
            training_cost_per_person_usd = float(p["training_cost_per_person_usd"])
            lab_standup_cost_usd = float(p["lab_standup_cost_usd"])
            workforce_size = float(p["workforce_size"])
            avg_annual_salary_usd = float(p["avg_annual_salary_usd"])
            ai_productivity_gain_pct = float(p["ai_productivity_gain_pct"])
            timeframe_months = float(
                p.get("timeframe_months") or ctx.timeframe_months or 36
            )
        except (KeyError, TypeError, ValueError) as exc:
            return ROIResult(
                total_investment_usd=None,
                total_value_3yr_usd=None,
                roi_pct=None,
                payback_months=None,
                npv_usd=None,
                sensitivity_table=[],
                note=f"Missing required parameter: {exc}",
            )

        training_cost = developers_targeted * training_cost_per_person_usd
        total_investment = lab_standup_cost_usd + training_cost
        annual_value = (
            workforce_size * avg_annual_salary_usd * (ai_productivity_gain_pct / 100)
        )
        value_3yr = annual_value * (timeframe_months / 12)
        roi_pct = (
            (value_3yr - total_investment) / total_investment * 100
            if total_investment
            else None
        )
        npv = _npv(total_investment, annual_value, timeframe_months)
        payback_months = (
            total_investment / (annual_value / 12) if annual_value else None
        )

        return ROIResult(
            total_investment_usd=round(total_investment, 2),
            total_value_3yr_usd=round(value_3yr, 2),
            roi_pct=round(roi_pct, 2) if roi_pct is not None else None,
            payback_months=round(payback_months, 1) if payback_months is not None else None,
            npv_usd=round(npv, 2),
            sensitivity_table=_sensitivity(
                ai_productivity_gain_pct,
                workforce_size,
                avg_annual_salary_usd,
                timeframe_months,
                total_investment,
            ),
        )


# ── Pure functions ─────────────────────────────────────────────────────────────


def _npv(total_investment: float, annual_value: float, timeframe_months: float) -> float:
    """NPV at 8% annual discount rate over timeframe_months."""
    monthly_rate = _DISCOUNT_RATE_ANNUAL / 12
    monthly_value = annual_value / 12
    months = int(timeframe_months)
    pv = sum(monthly_value / (1 + monthly_rate) ** t for t in range(1, months + 1))
    return pv - total_investment


def _sensitivity(
    base_gain_pct: float,
    workforce_size: float,
    avg_annual_salary_usd: float,
    timeframe_months: float,
    total_investment: float,
) -> list:
    """Sensitivity table: vary productivity_gain_pct ±10pp in 5pp steps."""
    rows = []
    for delta in (-10, -5, 0, 5, 10):
        gain = base_gain_pct + delta
        annual_val = workforce_size * avg_annual_salary_usd * (gain / 100)
        val_3yr = annual_val * (timeframe_months / 12)
        roi = (
            (val_3yr - total_investment) / total_investment * 100
            if total_investment
            else None
        )
        payback = total_investment / (annual_val / 12) if annual_val else None
        rows.append(
            {
                "productivity_gain_pct": round(gain, 1),
                "annual_value_usd": round(annual_val, 2),
                "value_3yr_usd": round(val_3yr, 2),
                "roi_pct": round(roi, 2) if roi is not None else None,
                "payback_months": round(payback, 1) if payback is not None else None,
            }
        )
    return rows


# ── CLI ────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Calculate ROI from a workflow YAML template."
    )
    parser.add_argument("--ctx", metavar="YAML_PATH", required=True)
    parser.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args(argv)

    ctx = WorkflowContextBuilder().build(args.ctx)
    result = ROICalculator().calculate(ctx)

    if args.as_json:
        print(json.dumps(asdict(result), indent=2, default=str))
    else:
        print(result)


if __name__ == "__main__":
    main()
