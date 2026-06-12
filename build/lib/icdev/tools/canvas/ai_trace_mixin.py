#!/usr/bin/env python3
# CUI // SP-CTI
"""Canvas AI Traceability Mixin — one-import hook for all design canvases.

Every LLM-backed assessment, compliance finding, threat analysis, narrative
generation, or remediation recommendation in any canvas calls
`record_canvas_decision()` after the AI result is received.

This writes to:
  1. canvas_ai_decisions (icdev.db) — cross-canvas AI decision store
  2. audit_trail (icdev.db)         — NIST AU-2 audit record via decision_recorder

It also reads the active OTel span's trace_id/span_id so the decision is
cross-referenced with the distributed trace in otel_spans.

Usage::

    from tools.canvas.ai_trace_mixin import record_canvas_decision

    result = run_my_llm_assessment(...)
    record_canvas_decision(
        canvas_type="ndc",
        record_id=topology_id,
        decision_type="compliance_finding",
        decision=result["summary"],
        rationale=result.get("rationale"),
        model_used=result.get("model"),
        confidence=result.get("confidence"),
        alternatives=result.get("alternatives"),
        project_id=project_id,
    )

canvas_type values: ndc, sdc, pdc, bdc, ddc, odc, idc, aadc, aimc, mc
decision_type values: compliance_finding, threat_assessment, remediation,
                      narrative, classification, anomaly, risk_score,
                      readiness_assessment, boundary_impact, confabulation_flag,
                      chain_of_thought, chain_of_debate
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import uuid
from typing import Any, List, Optional

logger = get_logger("icdev.canvas.ai_trace")

# Lazy singleton — resolved once per process
_tracer = None


def _get_tracer():
    global _tracer
    if _tracer is None:
        try:
            from tools.observability import get_tracer as _gt
            _tracer = _gt()
        except Exception:
            try:
                from tools.observability.tracer import NullTracer
                _tracer = NullTracer()
            except Exception:
                _tracer = _NullFallback()
    return _tracer


class _NullFallback:
    """Minimal fallback when observability module is unavailable."""
    def get_active_span(self):
        return None


def record_canvas_decision(
    canvas_type: str,
    decision_type: str,
    decision: str,
    record_id: Optional[str] = None,
    rationale: Optional[str] = None,
    model_used: Optional[str] = None,
    confidence: Optional[float] = None,
    alternatives: Optional[List[Any]] = None,
    project_id: Optional[str] = None,
    actor: str = "icdev-system",
    classification: str = "CUI",
) -> str:
    """Record an AI-backed decision from a canvas assessment endpoint.

    Args:
        canvas_type:   Canvas identifier (ndc, sdc, pdc, bdc, ddc, odc, idc,
                       aadc, aimc, mc).
        decision_type: Category of decision (compliance_finding,
                       threat_assessment, remediation, narrative,
                       classification, anomaly, risk_score,
                       readiness_assessment, boundary_impact,
                       confabulation_flag).
        decision:      The decision text / finding summary.
        record_id:     Canvas-specific record identifier (topology_id,
                       design_id, dataset_id, etc.).
        rationale:     LLM-generated or rule-based explanation.
        model_used:    Model ID string from LLM router response.
        confidence:    Confidence score 0.0–1.0 if available.
        alternatives:  List of alternative decisions considered.
        project_id:    ICDEV project identifier.
        actor:         Who/what made the decision.
        classification: CUI classification level.

    Returns:
        UUID string of the inserted canvas_ai_decisions row.
    """
    decision_id = str(uuid.uuid4())

    # Resolve active span for cross-reference with otel_spans
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    try:
        active = _get_tracer().get_active_span()
        if active is not None:
            trace_id = active.trace_id
            span_id = active.span_id
    except Exception:
        pass

    # 1. Write to canvas_ai_decisions
    try:
        from tools.db.storage import get_connection
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO canvas_ai_decisions
                    (id, canvas_type, record_id, decision_type, decision,
                     rationale, model_used, confidence, alternatives,
                     trace_id, span_id, actor, project_id, classification)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    canvas_type,
                    record_id,
                    decision_type,
                    decision,
                    rationale,
                    model_used,
                    confidence,
                    json.dumps(alternatives or []),
                    trace_id,
                    span_id,
                    actor,
                    project_id,
                    classification,
                ),
            )
    except Exception as exc:
        logger.warning("record_canvas_decision: DB write failed: %s", exc)

    # 2. Also write to audit_trail via decision_recorder (NIST AU-2)
    try:
        from tools.audit.decision_recorder import record_decision
        record_decision(
            project_id=project_id or "icdev",
            decision=f"[{canvas_type.upper()}] {decision_type}: {decision}",
            rationale=rationale or "",
            alternatives=alternatives or [],
            actor=actor,
            context={
                "canvas": canvas_type,
                "record_id": record_id,
                "trace_id": trace_id,
                "model": model_used,
                "confidence": confidence,
            },
        )
    except Exception as exc:
        logger.warning("record_canvas_decision: audit_trail write failed: %s", exc)

    logger.debug(
        "canvas_decision recorded id=%s canvas=%s type=%s trace=%s",
        decision_id, canvas_type, decision_type, trace_id,
    )
    return decision_id
