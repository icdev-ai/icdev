# CUI // SP-CTI
"""Mission Canvas — Plain-English Mission-Ready Outputs wrapper.

Wraps tools.studio.wne.narrative_generator to produce concise,
mission-ready plain-English summaries from structured data.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
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

        ctx = WorkflowContext(
            template_name="mission_canvas",
            audience="leadership",
            org_name="ICDEV",
            program_name=mission_id,
            classification=classification,
            topic=topic,
            sources=sources or [],
        )
        generator = NarrativeGenerator()
        narrative = generator.generate(ctx=ctx)
        return {
            "mission_id": mission_id,
            "topic": topic,
            "narrative": narrative.executive_summary,
            "sources_used": sources or [],
            "classification": classification,
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
