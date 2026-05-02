# [TEMPLATE: CUI // SP-CTI]
#!/usr/bin/env python3
"""ICDEV™ Studio — Workflow Narrative Engine (WNE) Narrative Generator.

Generates audience-driven prose from a WorkflowContext using Jinja2 templates.
Optional LLM refinement via LLMRouter.  Never fails — degrades to Jinja2-only
output when no LLM is reachable (air-gap safe).

CLI:
    python tools/studio/wne/narrative_generator.py --ctx <yaml_path> --audience leadership --json
    python tools/studio/wne/narrative_generator.py --ctx <yaml_path> --audience compliance
    python tools/studio/wne/narrative_generator.py --ctx <yaml_path> --audience technical
    python tools/studio/wne/narrative_generator.py --ctx <yaml_path> --audience board
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
HARDPROMPTS_DIR = BASE_DIR / "hardprompts" / "wne"

try:
    from jinja2 import Template as _Jinja2Template

    def _render(template_text: str, ctx_dict: dict) -> str:
        return _Jinja2Template(template_text).render(**ctx_dict)

except ImportError:
    _Jinja2Template = None  # type: ignore[assignment,misc]

    def _render(template_text: str, ctx_dict: dict) -> str:  # type: ignore[misc]
        return template_text  # bare fallback: return unrendered template


# ── Result dataclass ───────────────────────────────────────────────────────────


@dataclass
class NarrativeResult:
    executive_summary: str = ""
    phase_narratives: list[str] = field(default_factory=list)
    decision_point_rationale: str = ""
    risk_summary: str = ""
    slide_bullets: list[str] = field(default_factory=list)
    audience: str = ""
    llm_refined: bool = False
    writeguard_score: float | None = None
    writeguard_badge: str | None = None
    writeguard_findings: list[str] = field(default_factory=list)
    quality_warning: str | None = None


# ── Generator ─────────────────────────────────────────────────────────────────


class NarrativeGenerator:
    """Generate audience-driven narratives from a WorkflowContext."""

    def generate(self, ctx: "WorkflowContext") -> NarrativeResult:  # type: ignore[name-defined]  # noqa: F821

        ctx_dict = asdict(ctx)

        exec_summary = self._render_exec_brief(ctx_dict)
        phase_narratives = self._build_phase_narratives(ctx)
        dp_rationale = self._build_dp_rationale(ctx)
        risk_summary = self._build_risk_summary(ctx)
        slide_bullets = self._render_slide_bullets(ctx_dict)

        result = NarrativeResult(
            executive_summary=exec_summary,
            phase_narratives=phase_narratives,
            decision_point_rationale=dp_rationale,
            risk_summary=risk_summary,
            slide_bullets=slide_bullets,
            audience=ctx.audience,
            llm_refined=False,
        )

        if self._llm_available():
            result = self._refine_with_llm(result, ctx)

        result = self._apply_writeguard(result)

        return result

    # ── Template rendering ─────────────────────────────────────────────────────

    def _render_exec_brief(self, ctx_dict: dict) -> str:
        template_text = self._load_template("exec_brief.md")
        return _render(template_text, ctx_dict).strip()

    def _render_slide_bullets(self, ctx_dict: dict) -> list[str]:
        template_text = self._load_template("slide_outline.md")
        rendered = _render(template_text, ctx_dict)
        bullets = [
            line.lstrip("- ").strip()
            for line in rendered.splitlines()
            if line.strip().startswith("## Slide")
        ]
        # Guarantee exactly 10 bullets; pad or trim as needed
        while len(bullets) < 10:
            bullets.append(f"Slide {len(bullets) + 1}")
        return bullets[:10]

    @staticmethod
    def _load_template(filename: str) -> str:
        path = HARDPROMPTS_DIR / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    # ── Section builders ───────────────────────────────────────────────────────

    @staticmethod
    def _build_phase_narratives(ctx: "WorkflowContext") -> list[str]:  # type: ignore[name-defined]  # noqa: F821
        narratives: list[str] = []
        for i, phase in enumerate(ctx.phases, start=1):
            step_count = len(phase.nodes)
            narrative = (
                f"Phase {i} — {phase.name} ({phase.phase_type}): "
                f"{step_count} step(s) — {', '.join(phase.nodes)}"
            )
            narratives.append(narrative)
        return narratives

    @staticmethod
    def _build_dp_rationale(ctx: "WorkflowContext") -> str:  # type: ignore[name-defined]  # noqa: F821
        if not ctx.decision_points:
            return "No human decision points; workflow is fully automated."
        lines = [
            f"- {dp.name} (node: {dp.node_id}) — Role: {dp.role}"
            + (f" | Doc: {dp.doc_template}" if dp.doc_template else "")
            for dp in ctx.decision_points
        ]
        return "Decision points requiring human review:\n" + "\n".join(lines)

    @staticmethod
    def _build_risk_summary(ctx: "WorkflowContext") -> str:  # type: ignore[name-defined]  # noqa: F821
        gate_count = len(ctx.approval_gates)
        dp_count = len(ctx.decision_points)
        phase_count = len(ctx.phases)
        parts = [
            f"Classification: {ctx.classification}.",
            f"Workflow spans {phase_count} phase(s) with {gate_count} approval gate(s)"
            f" and {dp_count} human decision point(s).",
        ]
        if ctx.parameters.get("risk_reduction"):
            parts.append(f"Estimated risk reduction: {ctx.parameters['risk_reduction']}.")
        if gate_count == 0 and dp_count == 0:
            parts.append("No manual oversight steps; full automation — review risk acceptance posture.")
        return " ".join(parts)

    # ── LLM refinement ─────────────────────────────────────────────────────────

    @staticmethod
    def _llm_available() -> bool:
        try:
            from tools.llm.router import LLMRouter
            return LLMRouter().has_any_llm()
        except Exception:  # noqa: BLE001
            return False

    def _refine_with_llm(
        self, result: NarrativeResult, ctx: "WorkflowContext"  # type: ignore[name-defined]  # noqa: F821
    ) -> NarrativeResult:
        try:
            from tools.llm.router import LLMRouter

            router = LLMRouter()
            provider = router.get_provider_for_function("narrative_generation")

            prompt = self._build_refinement_prompt(result, ctx)
            refined_text = provider.complete(prompt, max_tokens=1200)

            if refined_text and len(refined_text.strip()) > 50:
                result.executive_summary = refined_text.strip()
                result.llm_refined = True
        except Exception:  # noqa: BLE001
            pass  # LLM refinement is best-effort; Jinja2 output stands
        return result

    @staticmethod
    def _build_refinement_prompt(
        result: NarrativeResult, ctx: "WorkflowContext"  # type: ignore[name-defined]  # noqa: F821
    ) -> str:
        audience_instructions = {
            "leadership": "Lead with Course of Action, ROI, and funding ask. Be concise and action-oriented.",
            "compliance": "Lead with control coverage percentage, risk reduction, and audit evidence trail.",
            "technical": "Lead with toolchain stack, integration steps, and automation gates.",
            "board": "Lead with business value, competitive differentiation, and investment summary.",
        }
        instruction = audience_instructions.get(
            ctx.audience, "Produce a clear executive summary."
        )
        return (
            f"Refine the following workflow executive brief for a {ctx.audience} audience. "
            f"{instruction} Keep it under 300 words. Classification: {ctx.classification}.\n\n"
            f"---\n{result.executive_summary}\n---\n"
            f"Phase count: {len(ctx.phases)}. "
            f"Decision points: {len(ctx.decision_points)}. "
            f"Approval gates: {len(ctx.approval_gates)}."
        )


    # ── WriteGuard integration ─────────────────────────────────────────────────

    @staticmethod
    def _badge_for_score(score: float) -> str:
        if score >= 80:
            return "green"
        if score >= 60:
            return "yellow"
        return "red"

    @staticmethod
    def _run_writeguard(text: str) -> dict | None:
        try:
            from tools.pulse.writeguard import run_full_quality_check  # noqa: PLC0415
            return run_full_quality_check(text)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _extract_top_findings(wg_result: dict) -> list[str]:
        candidates: list[str] = list(wg_result.get("recommendations", []))
        weakest = (wg_result.get("section_scores") or {}).get("weakest") or {}
        candidates.extend(weakest.get("recommendations", []))
        seen: set[str] = set()
        top: list[str] = []
        for f in candidates:
            if f and f not in seen:
                seen.add(f)
                top.append(f)
            if len(top) >= 3:
                break
        return top

    def _writeguard_rewrite(self, text: str, findings: list[str]) -> str:
        try:
            from tools.llm.router import LLMRouter  # noqa: PLC0415
            router = LLMRouter()
            provider = router.get_provider_for_function("narrative_generation")
            guidance = "\n".join(f"- {f}" for f in findings) if findings else "Improve overall clarity and tone."
            prompt = (
                "Rewrite the following executive brief to address these quality issues:\n"
                f"{guidance}\n\n"
                "Keep under 300 words. Be direct, authoritative, and specific.\n\n"
                f"---\n{text}\n---"
            )
            rewritten = provider.complete(prompt, max_tokens=1200)
            if rewritten and len(rewritten.strip()) > 50:
                return rewritten.strip()
        except Exception:  # noqa: BLE001
            pass
        return text

    def _apply_writeguard(self, result: NarrativeResult) -> NarrativeResult:
        wg = self._run_writeguard(result.executive_summary)
        if wg is None:
            return result

        score: float = float(wg.get("overall_score", 0.0))
        badge = self._badge_for_score(score)
        findings = self._extract_top_findings(wg)

        result.writeguard_score = score
        result.writeguard_badge = badge
        result.writeguard_findings = findings

        if score >= 80:
            return result

        if not self._llm_available():
            result.quality_warning = "critical" if score < 60 else f"score {score:.0f} ({badge})"
            return result

        max_passes = 1 if score >= 60 else 2
        for _ in range(max_passes):
            rewritten = self._writeguard_rewrite(result.executive_summary, findings)
            if rewritten == result.executive_summary:
                break

            re_wg = self._run_writeguard(rewritten)
            if re_wg is None:
                break

            new_score = float(re_wg.get("overall_score", score))
            if new_score >= score:
                result.executive_summary = rewritten
                result.llm_refined = True
                score = new_score
                badge = self._badge_for_score(score)
                findings = self._extract_top_findings(re_wg)

            if score >= 80:
                break

        result.writeguard_score = score
        result.writeguard_badge = badge
        result.writeguard_findings = findings

        if score < 80:
            result.quality_warning = f"score {score:.0f} ({badge}) after rewrite"

        return result


# ── CLI ────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate audience-driven narrative from a workflow YAML."
    )
    parser.add_argument("--ctx", metavar="YAML_PATH", required=True,
                        help="Workflow YAML path (passed to WorkflowContextBuilder)")
    parser.add_argument(
        "--audience",
        choices=["leadership", "compliance", "technical", "board"],
        default="leadership",
    )
    parser.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args(argv)

    from tools.studio.wne.context_builder import WorkflowContextBuilder
    from dataclasses import replace as dc_replace

    ctx = WorkflowContextBuilder().build(args.ctx)
    # Override audience from CLI flag
    ctx = dc_replace(ctx, audience=args.audience)

    result = NarrativeGenerator().generate(ctx)

    if args.as_json:
        print(json.dumps(asdict(result), indent=2, default=str))
    else:
        print(result.executive_summary)
        if result.phase_narratives:
            print("\n--- Phase Narratives ---")
            for pn in result.phase_narratives:
                print(f"  {pn}")
        if result.decision_point_rationale:
            print(f"\n--- Decision Points ---\n{result.decision_point_rationale}")
        if result.risk_summary:
            print(f"\n--- Risk Summary ---\n{result.risk_summary}")
        if result.slide_bullets:
            print("\n--- Slide Bullets ---")
            for i, b in enumerate(result.slide_bullets, start=1):
                print(f"  {i}. {b}")


if __name__ == "__main__":
    main()
