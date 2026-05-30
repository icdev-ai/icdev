#!/usr/bin/env python3
# CUI // SP-CTI
"""Central canvas provenance helper — register design + assessment hashes.

Called from each canvas blueprint after design save and assessment complete.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import hashlib
import json
from typing import Any

logger = get_logger("canvas.provenance")


def register_canvas_provenance(
    canvas_key: str,
    design_id: str,
    graph_json: dict[str, Any] | None = None,
    assessment_data: dict[str, Any] | None = None,
    project_id: str = "",
) -> dict:
    """Register design and assessment provenance for a canvas.

    Returns {"design_reg_id": str|None, "assessment_reg_id": str|None}.
    """
    try:
        from tools.provenance.registry import register_citation
        from tools.blockchain.chain_anchor import ChainAnchor
    except Exception as exc:
        logger.debug("Provenance deps unavailable: %s", exc)
        return {"design_reg_id": None, "assessment_reg_id": None}

    results: dict[str, str | None] = {"design_reg_id": None, "assessment_reg_id": None}
    anchor_ids: list[str] = []

    # Register design graph
    if graph_json:
        try:
            design_canon = json.dumps(graph_json, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            design_hash = hashlib.sha256(design_canon.encode("utf-8")).hexdigest()
            reg_id = register_citation(
                citation_type="prov_entity",
                source_table=f"{canvas_key}_designs",
                source_record_id=str(design_id),
                source_hash=design_hash,
                source_doc=f"{canvas_key.upper()} design",
                project_id=project_id,
            )
            if reg_id:
                results["design_reg_id"] = reg_id
                anchor_ids.append(reg_id)
                logger.info("[%s] Design provenance registered: %s", canvas_key, reg_id)
        except Exception as exc:
            logger.debug("[%s] Design provenance skipped: %s", canvas_key, exc)

    # Register assessment
    if assessment_data:
        try:
            assess_canon = json.dumps(assessment_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            assess_hash = hashlib.sha256(assess_canon.encode("utf-8")).hexdigest()
            reg_id = register_citation(
                citation_type="compliance_evidence",
                source_table=f"{canvas_key}_assessments",
                source_record_id=f"{design_id}-assessment",
                source_hash=assess_hash,
                source_doc=f"{canvas_key.upper()} assessment",
                project_id=project_id,
            )
            if reg_id:
                results["assessment_reg_id"] = reg_id
                anchor_ids.append(reg_id)
                logger.info("[%s] Assessment provenance registered: %s", canvas_key, reg_id)
        except Exception as exc:
            logger.debug("[%s] Assessment provenance skipped: %s", canvas_key, exc)

    # Anchor batch
    if anchor_ids:
        try:
            ChainAnchor().anchor_provenance(anchor_ids)
        except Exception as exc:
            logger.debug("[%s] Anchor batch skipped: %s", canvas_key, exc)

    return results
