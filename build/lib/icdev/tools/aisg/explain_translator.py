#!/usr/bin/env python3
# CUI // SP-CTI
"""Rule-based translator: AI audit events → plain-English explanations.

translate(event_type, event_data) -> dict with:
  headline            — one sentence describing what happened
  why_it_matters      — one sentence on operational impact
  confidence_label    — 'High' (>=0.7) | 'Medium' (0.3–0.69) | 'Low' (<0.3)
  alternative_considered  — optional string (present when event_data supplies it)

No LLM calls — deterministic string templates with event_data substitution.
"""
from __future__ import annotations


def _confidence_label(score: float) -> str:
    if score >= 0.7:
        return "High"
    if score >= 0.3:
        return "Medium"
    return "Low"


# ---------------------------------------------------------------------------
# Per-event-type handlers
# ---------------------------------------------------------------------------

def _self_heal_action(d: dict) -> dict:
    component = d.get("component", "a system component")
    issue = d.get("issue", "an anomaly")
    action = d.get("action_taken", d.get("action", "a corrective action"))
    confidence = float(d.get("confidence", 0.75))
    result = {
        "headline": (
            f"The system automatically fixed {issue} in {component} "
            f"by applying {action}."
        ),
        "why_it_matters": (
            "Self-healing prevented manual intervention and kept the system "
            "operational without downtime."
        ),
        "confidence_label": _confidence_label(confidence),
    }
    alt = d.get("alternative_action")
    if alt:
        result["alternative_considered"] = (
            f"'{alt}' was evaluated but not selected due to lower expected reliability."
        )
    return result


def _readiness_score_update(d: dict) -> dict:
    score = float(d.get("score", d.get("readiness_score", 0.5)))
    prev = d.get("previous_score")
    project = d.get("project", d.get("project_id", "the project"))
    direction = ""
    if prev is not None:
        prev_f = float(prev)
        if score > prev_f:
            direction = " (improved)"
        elif score < prev_f:
            direction = " (declined)"
    result = {
        "headline": (
            f"Readiness score for {project} updated to {score:.0%}{direction}."
        ),
        "why_it_matters": (
            "This score tracks ATO submission readiness — higher scores reduce "
            "review risk and accelerate authorization decisions."
        ),
        "confidence_label": _confidence_label(score),
    }
    factors = d.get("top_factors") or d.get("factors")
    if factors:
        result["alternative_considered"] = f"Score is most sensitive to: {factors}."
    return result


def _pattern_recommendation(d: dict) -> dict:
    pattern = d.get("pattern_name", d.get("pattern", "a reusable pattern"))
    use_case = d.get("use_case", d.get("context", "the current context"))
    confidence = float(d.get("confidence", 0.8))
    result = {
        "headline": (
            f"The AI recommended applying the '{pattern}' pattern for {use_case}."
        ),
        "why_it_matters": (
            "Adopting proven patterns accelerates delivery and reduces compliance "
            "risk by reusing vetted, pre-approved solutions."
        ),
        "confidence_label": _confidence_label(confidence),
    }
    alt = d.get("alternative_pattern")
    if alt:
        result["alternative_considered"] = (
            f"'{alt}' was also evaluated but scored lower on fit for this use case."
        )
    return result


def _gap_detection(d: dict) -> dict:
    gap_type = d.get("gap_type", "compliance gap")
    component = d.get("component", d.get("module", "the system"))
    severity = str(d.get("severity", "medium")).lower()
    confidence_map = {
        "critical": 0.95,
        "high": 0.85,
        "medium": 0.65,
        "low": 0.45,
    }
    confidence = confidence_map.get(severity, 0.65)
    result = {
        "headline": (
            f"A {severity}-severity {gap_type} was detected in {component}."
        ),
        "why_it_matters": (
            "Unresolved gaps can block ATO approval or surface as findings "
            "during a compliance assessment."
        ),
        "confidence_label": _confidence_label(confidence),
    }
    remediation = d.get("suggested_remediation") or d.get("remediation")
    if remediation:
        result["alternative_considered"] = f"Suggested remediation: {remediation}."
    return result


def _genesis_reflex_fired(d: dict) -> dict:
    reflex = d.get("reflex_name", d.get("reflex", "an autonomous reflex"))
    outcome = d.get("outcome", "completed successfully")
    confidence = float(d.get("confidence", 0.75))
    result = {
        "headline": f"Genesis reflex '{reflex}' fired and {outcome}.",
        "why_it_matters": (
            "Autonomous reflexes keep the knowledge graph and compliance "
            "evidence current without requiring manual effort."
        ),
        "confidence_label": _confidence_label(confidence),
    }
    artifacts = d.get("artifacts_generated") or d.get("artifacts")
    if artifacts:
        count = len(artifacts) if isinstance(artifacts, list) else int(artifacts)
        result["alternative_considered"] = (
            f"{count} artifact(s) generated and queued for review."
        )
    return result


def _unknown(event_type: str, d: dict) -> dict:
    confidence = float(d.get("confidence", 0.5))
    return {
        "headline": (
            f"An AI decision event of type '{event_type}' was recorded by the system."
        ),
        "why_it_matters": (
            "This event is part of the immutable audit trail tracking autonomous "
            "system behavior for compliance and transparency."
        ),
        "confidence_label": _confidence_label(confidence),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_HANDLERS = {
    "self_heal_action": _self_heal_action,
    "readiness_score_update": _readiness_score_update,
    "pattern_recommendation": _pattern_recommendation,
    "gap_detection": _gap_detection,
    "genesis_reflex_fired": _genesis_reflex_fired,
}


def translate(event_type: str, event_data: dict) -> dict:
    """Translate an AI audit event into a plain-English explanation.

    Args:
        event_type: Canonical event type string (e.g. 'self_heal_action').
        event_data: Parsed event payload (from audit_trail.details JSON).

    Returns:
        dict with keys: headline, why_it_matters, confidence_label,
        and optionally alternative_considered.
    """
    handler = _HANDLERS.get(event_type, lambda d: _unknown(event_type, d))
    return handler(event_data)
