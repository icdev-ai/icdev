# CUI // SP-CTI
"""Mission Canvas — Traceable Source-Attributed Evidence wrapper.

Wraps tools.observability.provenance.prov_recorder to capture
and retrieve provenance records for mission artifacts.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
from typing import Optional

logger = get_logger("icdev.mission_canvas.evidence")


def record_evidence(
    mission_id: str,
    artifact_id: str,
    artifact_type: str,
    source: str,
    actor: Optional[str] = None,
    classification: str = "CUI",
    metadata: Optional[dict] = None,
) -> dict:
    """Write a provenance record linking an artifact to its source."""
    try:
        from tools.observability.provenance.prov_recorder import ProvRecorder

        recorder = ProvRecorder()
        recorder.record_entity(
            entity_type=artifact_type,
            label=artifact_id,
            attributes={
                "mission_id": mission_id,
                "source": source,
                "actor": actor or "mission_canvas",
                "classification": classification,
                **(metadata or {}),
            },
            trace_id=mission_id,
        )
        return {
            "mission_id": mission_id,
            "artifact_id": artifact_id,
            "status": "recorded",
        }
    except Exception as exc:
        logger.warning("Evidence recording failed: %s", exc)
        return {
            "mission_id": mission_id,
            "artifact_id": artifact_id,
            "status": "error",
            "error": str(exc),
        }


def get_evidence_chain(artifact_id: str, depth: int = 3) -> list[dict]:
    """Retrieve the provenance chain for an artifact."""
    try:
        from tools.observability.provenance.prov_recorder import ProvRecorder

        recorder = ProvRecorder()
        return recorder.get_lineage(entity_id=artifact_id, max_depth=depth)
    except Exception as exc:
        logger.warning("Evidence chain retrieval failed: %s", exc)
        return []
