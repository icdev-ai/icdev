"""
Export Pack Generator — orchestrates all WNE narrative modules into a downloadable zip bundle.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.studio.wne.context_builder import WorkflowContext, WorkflowContextBuilder
from tools.studio.wne.narrative_generator import NarrativeGenerator, NarrativeResult
from tools.studio.wne.coa_builder import COABuilder, COAOption, COAResult
from tools.studio.wne.roi_calculator import ROICalculator, ROIResult
from tools.studio.wne.budget_estimator import BudgetEstimator, BudgetResult

try:
    from tools.canvas.export_utils import _CUI_BANNER
except Exception:
    _CUI_BANNER = "CUI // SP-CTI"


def _safe(fn: Any, label: str) -> tuple[Any, str | None]:
    try:
        return fn(), None
    except Exception as exc:  # pylint: disable=broad-except
        return None, f"*[{label} unavailable: {exc}]*"


def _md_header(title: str, classification: str) -> str:
    return f"<!-- {classification} -->\n# {title}\n\n"


class ExportPackGenerator:
    """Orchestrate all WNE modules and produce a zip narrative pack."""

    def generate(
        self,
        yaml_path: str | Path,
        output_dir: str | Path,
        audience: str | None = None,
    ) -> Path:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # 1. Build context
        ctx, ctx_err = _safe(lambda: WorkflowContextBuilder().build(yaml_path), "context")

        # 2. Override audience if provided
        if ctx is not None and audience:
            ctx = dataclasses.replace(ctx, audience=audience)

        # 3–6. Generate artifacts (all non-fatal)
        _skip = "*[context unavailable; skipping]*"
        narrative, narr_err = (
            _safe(lambda: NarrativeGenerator().generate(ctx), "narrative")
            if ctx is not None else (None, _skip)
        )
        coa, coa_err = (
            _safe(lambda: COABuilder().build(ctx), "coa")
            if ctx is not None else (None, _skip)
        )
        roi, roi_err = (
            _safe(lambda: ROICalculator().calculate(ctx), "roi")
            if ctx is not None else (None, _skip)
        )
        budget, budget_err = (
            _safe(lambda: BudgetEstimator().estimate(ctx), "budget")
            if ctx is not None else (None, _skip)
        )

        classification = getattr(ctx, "classification", _CUI_BANNER) if ctx else _CUI_BANNER
        program_name = getattr(ctx, "program_name", "export") if ctx else "export"

        # 7. Write the 6 output files
        files: list[Path] = [
            self._write_exec_brief(output_dir, narrative, narr_err, classification),
            self._write_coa_comparison(output_dir, coa, coa_err, classification),
            self._write_budget_table(output_dir, budget, budget_err, classification),
            self._write_roi_analysis(output_dir, roi, roi_err, classification),
            self._write_slide_outline(output_dir, narrative, narr_err, classification),
            self._write_workflow_summary(output_dir, ctx, roi),
        ]

        # 8. Zip all 6 files
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in program_name)
        zip_path = output_dir / f"{safe_name}_narrative_pack.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                zf.write(f, f.name)

        # 9. Return zip path
        return zip_path

    # ------------------------------------------------------------------ #
    # File writers                                                         #
    # ------------------------------------------------------------------ #

    def _write_exec_brief(
        self,
        out: Path,
        narrative: NarrativeResult | None,
        err: str | None,
        classification: str,
    ) -> Path:
        path = out / "exec_brief.md"
        lines: list[str] = [_md_header("Executive Brief", classification)]
        if err:
            lines.append(err)
        else:
            assert narrative is not None
            lines.append("## Executive Summary\n")
            lines.append(narrative.executive_summary or "*No summary generated.*")
            lines.append("\n\n## Phase Narratives\n")
            for i, phase_text in enumerate(narrative.phase_narratives, 1):
                lines.append(f"### Phase {i}\n")
                lines.append(phase_text)
                lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8", newline="")
        return path

    def _write_coa_comparison(
        self,
        out: Path,
        coa: COAResult | None,
        err: str | None,
        classification: str,
    ) -> Path:
        path = out / "coa_comparison.md"
        lines: list[str] = [_md_header("Course of Action Comparison", classification)]
        if err:
            lines.append(err)
        else:
            assert coa is not None
            options: list[COAOption] = [coa.coa_a, coa.coa_b, coa.coa_c]
            labels = ["COA A", "COA B", "COA C"]
            lines.append("| Field | " + " | ".join(labels) + " |")
            lines.append("|---|---|---|---|")
            rows: list[tuple[str, list[str]]] = [
                ("Name", [o.name for o in options]),
                ("Approach", [o.approach for o in options]),
                ("Timeline (months)", [str(o.timeline_months) for o in options]),
                ("Cost (USD)", [f"${o.cost_usd:,.0f}" for o in options]),
                ("Risk Level", [o.risk_level for o in options]),
                ("Recommended", ["Yes" if o.recommendation else "No" for o in options]),
                ("Rationale", [o.rationale.replace("|", "\\|") for o in options]),
            ]
            for label, values in rows:
                lines.append("| " + label + " | " + " | ".join(values) + " |")
        path.write_text("\n".join(lines), encoding="utf-8", newline="")
        return path

    def _write_budget_table(
        self,
        out: Path,
        budget: BudgetResult | None,
        err: str | None,
        classification: str,
    ) -> Path:
        path = out / "budget_table.md"
        lines: list[str] = [_md_header("Budget Estimate", classification)]
        if err:
            lines.append(err)
        else:
            assert budget is not None
            lines.append("| Phase | Cost (USD) |")
            lines.append("|---|---|")
            for phase in budget.phases:
                lines.append(f"| {phase.phase_name} | ${phase.cost_usd:,.0f} |")
            lines.append(f"| **Total** | **${budget.total_usd:,.0f}** |")
        path.write_text("\n".join(lines), encoding="utf-8", newline="")
        return path

    def _write_roi_analysis(
        self,
        out: Path,
        roi: ROIResult | None,
        err: str | None,
        classification: str,
    ) -> Path:
        path = out / "roi_analysis.md"
        lines: list[str] = [_md_header("ROI Analysis", classification)]
        if err:
            lines.append(err)
        else:
            assert roi is not None

            def _fmt(val: float | None, fmt: str = ",.0f", prefix: str = "") -> str:
                return f"{prefix}{val:{fmt}}" if val is not None else "N/A"

            lines.append("## Summary\n")
            lines.append("| Metric | Value |")
            lines.append("|---|---|")
            lines.append(f"| Total Investment | ${_fmt(roi.total_investment_usd)} |")
            lines.append(f"| Total Value (3yr) | ${_fmt(roi.total_value_3yr_usd)} |")
            lines.append(f"| ROI | {_fmt(roi.roi_pct, '.1f')}% |")
            lines.append(f"| Payback Period | {_fmt(roi.payback_months, '.1f')} months |")
            lines.append(f"| NPV | ${_fmt(roi.npv_usd)} |")
            if roi.note:
                lines.append(f"\n> {roi.note}")
            if roi.sensitivity_table:
                lines.append("\n## Sensitivity Analysis\n")
                first = roi.sensitivity_table[0]
                if isinstance(first, dict):
                    headers = list(first.keys())
                    lines.append("| " + " | ".join(str(h) for h in headers) + " |")
                    lines.append("|" + "|".join("---" for _ in headers) + "|")
                    for row in roi.sensitivity_table:
                        lines.append("| " + " | ".join(str(row.get(h, "")) for h in headers) + " |")
                else:
                    for row in roi.sensitivity_table:
                        lines.append(f"- {row}")
        path.write_text("\n".join(lines), encoding="utf-8", newline="")
        return path

    def _write_slide_outline(
        self,
        out: Path,
        narrative: NarrativeResult | None,
        err: str | None,
        classification: str,
    ) -> Path:
        path = out / "slide_outline.md"
        lines: list[str] = [_md_header("Slide Outline", classification)]
        if err:
            lines.append(err)
        else:
            assert narrative is not None
            lines.append("*Formatted as slide notes — one bullet per slide.*\n")
            for i, bullet in enumerate(narrative.slide_bullets, 1):
                lines.append(f"**Slide {i}:** {bullet}\n")
        path.write_text("\n".join(lines), encoding="utf-8", newline="")
        return path

    def _write_workflow_summary(
        self,
        out: Path,
        ctx: WorkflowContext | None,
        roi: ROIResult | None,
    ) -> Path:
        path = out / "workflow_summary.json"
        node_count = sum(len(p.nodes) for p in ctx.phases) if ctx else 0
        phases = [p.name for p in ctx.phases] if ctx else []
        summary = {
            "template_name": getattr(ctx, "template_name", "") if ctx else "",
            "audience": getattr(ctx, "audience", "") if ctx else "",
            "node_count": node_count,
            "phases": phases,
            "total_investment_usd": getattr(roi, "total_investment_usd", None) if roi else None,
            "roi_pct": getattr(roi, "roi_pct", None) if roi else None,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        path.write_text(json.dumps(summary, indent=2), encoding="utf-8", newline="")
        return path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate WNE narrative export pack")
    parser.add_argument("--template", required=True, help="Path to workflow YAML")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument("--audience", default=None, help="Audience override (e.g. leadership, technical)")
    args = parser.parse_args(argv)

    zip_path = ExportPackGenerator().generate(args.template, args.output, audience=args.audience)
    print(zip_path)


if __name__ == "__main__":
    main()
