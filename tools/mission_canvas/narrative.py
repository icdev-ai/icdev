# CUI // SP-CTI
"""Mission Canvas — Plain-English Mission-Ready Outputs wrapper.

Wraps tools.studio.wne.narrative_generator to produce concise,
mission-ready plain-English summaries from structured data.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from typing import Optional

logger = get_logger("icdev.mission_canvas.narrative")


def generate_narrative(
    mission_id: str,
    topic: str,
    sources: Optional[list[dict]] = None,
    classification: str = "CUI",
    max_words: int = 250,
) -> dict:
    """Generate a plain-English mission narrative from structured sources.

    Returns the narrative text and metadata about sources used.
    """
    try:
        from tools.studio.wne.narrative_generator import NarrativeGenerator
        from tools.studio.wne.context_builder import WorkflowContext

        # cnr-mc-03: WorkflowContext is a dataclass with a fixed field set —
        # the previous call passed topic=/sources= (not fields) and omitted
        # required fields, so construction always raised and no narrative was
        # ever produced. Map the mission topic to purpose and carry sources in
        # parameters so grounding has something real to validate.
        ctx = WorkflowContext(
            template_name="mission_canvas",
            audience="leadership",
            org_name="ICDEV",
            program_name=mission_id,
            classification=classification,
            purpose=topic,
            timeframe_months=0,
            parameters={"topic": topic, "sources": sources or []},
            phases=[],
            decision_points=[],
            approval_gates=[],
        )
        generator = NarrativeGenerator()
        narrative = generator.generate(ctx=ctx)
        summary = narrative.executive_summary

        # cnr-mc-03: ground the LLM executive summary against its evidence.
        # Previously the narrative echoed sources but enforced no citation
        # grounding. validate_citations flags any [source: N] tag that references
        # a source not in the evidence (hallucination); citation_gate mirrors the
        # placeholder_guard finding shape so a promote/export surface can gate on
        # it. allowed_sources uses the RAG 1..N injected-source convention.
        from tools.quality.citation_grounding import citation_gate, validate_citations

        allowed = len(sources or [])
        report = validate_citations(summary, allowed)
        findings = citation_gate(
            [{"id": mission_id, "content": summary, "allowed_sources": allowed}],
            require_citations=bool(sources),
        )
        return {
            "mission_id": mission_id,
            "topic": topic,
            "narrative": summary,
            "sources_used": sources or [],
            "classification": classification,
            "grounding": {
                "report": report,
                "findings": findings,
                "grounded": not findings,
            },
            "status": "ok",
        }
    except Exception as exc:
        logger.warning("Narrative generation failed: %s", exc)
        return {
            "mission_id": mission_id,
            "topic": topic,
            "narrative": f"Narrative generation unavailable: {exc}",
            "classification": classification,
            "status": "error",
            "error": str(exc),
        }
