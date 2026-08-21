# CUI // SP-CTI
"""Quality Design Canvas — Assessment engine.

Pure functions with no Flask dependency.
Computes UQS scores, maps gates to SA-11, emits OSCAL evidence.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from tools.qdc_canvas.constants import (
    QDC_COMPLIANCE_RULES,
    QDC_GATE_TOOLS,
    QDC_SA11_MAP,
    QDC_UQS_DEFAULTS,
)


# ── helpers ────────────────────────────────────────────────────────────────


def _utcnow_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _node_types(graph_data: dict) -> list[str]:
    """Extract the list of node type values from graph data."""
    return [n.get("type", "") for n in graph_data.get("nodes", [])]


def _edge_set(graph_data: dict) -> list[dict]:
    """Return edges list from graph data."""
    return graph_data.get("edges", [])


def _node_ids(graph_data: dict) -> set[str]:
    """Return set of all node IDs."""
    return {n.get("id", "") for n in graph_data.get("nodes", [])}


def _connected_node_ids(graph_data: dict) -> set[str]:
    """Return set of node IDs that appear in at least one edge."""
    connected: set[str] = set()
    for e in _edge_set(graph_data):
        connected.add(e.get("source", ""))
        connected.add(e.get("target", ""))
    return connected


def _targets_of(graph_data: dict, node_id: str) -> set[str]:
    """Return set of node IDs that have an edge pointing TO the given node."""
    return {e.get("source", "") for e in _edge_set(graph_data) if e.get("target") == node_id}


# ── check functions ────────────────────────────────────────────────────────


def _has_sast_gate(graph_data: dict) -> bool:
    return any(t.startswith("gate-sast") for t in _node_types(graph_data))


def _has_unit_gate(graph_data: dict) -> bool:
    return any(t == "gate-unit" for t in _node_types(graph_data))


def _has_review_gate(graph_data: dict) -> bool:
    return any(t == "gate-review" for t in _node_types(graph_data))


def _has_stig_gate(graph_data: dict) -> bool:
    return any(t == "tgt-stig" for t in _node_types(graph_data))


def _has_sbom_node(graph_data: dict) -> bool:
    return any("sbom" in t for t in _node_types(graph_data))


def _has_secret_gate(graph_data: dict) -> bool:
    return any(t == "gate-secret" for t in _node_types(graph_data))


def _has_uqs_consumer(graph_data: dict) -> bool:
    return any(t == "con-uqs" for t in _node_types(graph_data))


def _has_cross_canvas_link(graph_data: dict) -> bool:
    return any(t.startswith("xc-") for t in _node_types(graph_data))


def _has_compliance_target(graph_data: dict) -> bool:
    return any(t.startswith("tgt-") for t in _node_types(graph_data))


def _has_e2e_gate(graph_data: dict) -> bool:
    return any(t == "gate-e2e" for t in _node_types(graph_data))


def _gates_have_sources(graph_data: dict) -> bool:
    """Every gate-* node must have at least one incoming edge."""
    nodes = graph_data.get("nodes", [])
    gate_nodes = [n for n in nodes if n.get("type", "").startswith("gate-")]
    if not gate_nodes:
        return True  # vacuously true when no gates exist
    for gate in gate_nodes:
        if not _targets_of(graph_data, gate.get("id", "")):
            return False
    return True


def _no_orphan_nodes(graph_data: dict) -> bool:
    """Every node must have at least one edge (incoming or outgoing)."""
    all_ids = _node_ids(graph_data)
    connected = _connected_node_ids(graph_data)
    return all_ids.issubset(connected)


_CHECK_REGISTRY: dict[str, Any] = {
    "has_sast_gate": _has_sast_gate,
    "has_unit_gate": _has_unit_gate,
    "has_review_gate": _has_review_gate,
    "has_stig_gate": _has_stig_gate,
    "has_sbom_node": _has_sbom_node,
    "has_secret_gate": _has_secret_gate,
    "has_uqs_consumer": _has_uqs_consumer,
    "has_cross_canvas_link": _has_cross_canvas_link,
    "has_compliance_target": _has_compliance_target,
    "has_e2e_gate": _has_e2e_gate,
    "gates_have_sources": _gates_have_sources,
    "no_orphan_nodes": _no_orphan_nodes,
}


# ── public API ─────────────────────────────────────────────────────────────


def assess_quality_design(graph_data: dict) -> dict:
    """Assess a QDC graph against all compliance rules.

    Parameters
    ----------
    graph_data : dict
        ``{"nodes": [...], "edges": [...]}``

    Returns
    -------
    dict
        Assessment result with findings, summary, and score.
    """
    findings: list[dict] = []
    passed = 0
    rules_checked = len(QDC_COMPLIANCE_RULES)
    by_severity: Counter[str] = Counter()
    by_category: Counter[str] = Counter()

    for rule in QDC_COMPLIANCE_RULES:
        check_fn = _CHECK_REGISTRY.get(rule["check"])
        if check_fn is None:
            continue
        ok = check_fn(graph_data)
        if ok:
            passed += 1
        else:
            by_severity[rule["severity"]] += 1
            by_category[rule["category"]] += 1
            findings.append(
                {
                    "rule_id": rule["id"],
                    "title": rule["title"],
                    "severity": rule["severity"],
                    "category": rule["category"],
                    "detail": f"Check '{rule['check']}' failed — {rule['title']}.",
                }
            )

    # Base score — NOT ASSESSED, never 100.0 (rem-hyg-13). An empty rulebook
    # means no check ran, which is the strongest possible reason NOT to publish
    # a quality score. QDC_COMPLIANCE_RULES is non-empty today so this arm is
    # unreached, and it is corrected anyway: the branch is what a future change
    # filtering the rulebook would land on. Written as a statement rather than a
    # conditional expression because the severity deductions below must not run
    # against None.
    if rules_checked:
        score = 100.0 * (passed / rules_checked)
        # Extra deductions for severity
        score -= by_severity.get("CAT1", 0) * 10
        score -= by_severity.get("CAT2", 0) * 5
        score = max(0.0, min(100.0, score))
    else:
        score = None

    return {
        "assessed_at": _utcnow_iso(),
        "findings": findings,
        "summary": {
            "total": len(findings),
            "rules_checked": rules_checked,
            "passed": passed,
            "by_severity": dict(by_severity),
            "by_category": dict(by_category),
        },
        "score": round(score, 2) if score is not None else None,
    }


def compute_uqs(gate_results: list[dict], weights: dict | None = None) -> dict:
    """Compute Unified Quality Score from individual gate results.

    Parameters
    ----------
    gate_results : list[dict]
        Each entry: ``{"gate_id": str, "status": "pass"|"fail"|"skip",
        "dimension": str, "score": float}``
    weights : dict, optional
        Dimension weights; defaults to ``QDC_UQS_DEFAULTS``.

    Returns
    -------
    dict
        ``{"uqs_score": float, "dimensions": {...}, "grade": str}``
    """
    if weights is None:
        weights = dict(QDC_UQS_DEFAULTS)

    # Group scores by dimension (skip "skip" status)
    dim_scores: dict[str, list[float]] = {}
    for gr in gate_results:
        dim = gr.get("dimension", "")
        status = gr.get("status", "skip")
        if status == "skip":
            continue
        dim_scores.setdefault(dim, []).append(gr.get("score", 0.0))

    # Compute dimension averages
    dimensions: dict[str, float] = {}
    for dim, scores in dim_scores.items():
        dimensions[dim] = round(sum(scores) / len(scores), 2) if scores else 0.0

    # Weighted UQS
    total_weight = 0.0
    weighted_sum = 0.0
    for dim, weight in weights.items():
        if dim in dimensions:
            weighted_sum += dimensions[dim] * weight
            total_weight += weight

    uqs_score = round(weighted_sum / total_weight, 2) if total_weight > 0 else 0.0

    # Grade
    if uqs_score >= 90:
        grade = "A"
    elif uqs_score >= 80:
        grade = "B"
    elif uqs_score >= 70:
        grade = "C"
    elif uqs_score >= 60:
        grade = "D"
    else:
        grade = "F"

    return {
        "uqs_score": uqs_score,
        "dimensions": dimensions,
        "grade": grade,
    }


def map_gate_to_sa11(gate_id: str) -> dict:
    """Return SA-11 control mapping for a given gate ID.

    Returns ``{"control": "Unknown", "title": "Unmapped gate"}`` when
    *gate_id* is not found in ``QDC_SA11_MAP``.
    """
    return QDC_SA11_MAP.get(gate_id, {"control": "Unknown", "title": "Unmapped gate"})


def emit_oscal_evidence(gate_result: dict) -> dict:
    """Generate an OSCAL-compatible evidence dict for a single gate result.

    Parameters
    ----------
    gate_result : dict
        ``{"gate_id": str, "status": "pass"|"fail", ...}``

    Returns
    -------
    dict
        OSCAL finding structure.
    """
    gate_id = gate_result.get("gate_id", "unknown")
    sa11 = map_gate_to_sa11(gate_id)
    tool_path = QDC_GATE_TOOLS.get(gate_id, "N/A")
    status = gate_result.get("status", "fail")

    return {
        "uuid": str(uuid.uuid4()),
        "type": "finding",
        "title": f"SA-11 Gate: {gate_id}",
        "description": sa11.get("title", ""),
        "control_id": sa11.get("control", "Unknown"),
        "status": "satisfied" if status == "pass" else "other_than_satisfied",
        "collected": _utcnow_iso(),
        "props": [
            {"name": "gate_id", "value": gate_id},
            {"name": "tool", "value": str(tool_path) if tool_path else "manual"},
        ],
    }


def detect_quality_gaps(assessment: dict) -> list[dict]:
    """Extract failed findings from an assessment as gap recommendations.

    Parameters
    ----------
    assessment : dict
        Output of :func:`assess_quality_design`.

    Returns
    -------
    list[dict]
        Each entry: ``{"rule_id", "title", "severity", "recommendation"}``.
    """
    gaps: list[dict] = []
    for finding in assessment.get("findings", []):
        gaps.append(
            {
                "rule_id": finding["rule_id"],
                "title": finding["title"],
                "severity": finding["severity"],
                "recommendation": f"Add or connect the required element to satisfy {finding['title']}.",
            }
        )
    return gaps


def aggregate_cross_canvas_quality(canvas_scores: dict[str, float]) -> dict:
    """Aggregate quality scores from multiple design canvases.

    Parameters
    ----------
    canvas_scores : dict
        ``{"idc": 85.0, "sdc": 72.0, ...}``

    Returns
    -------
    dict
        ``{"average", "min_canvas", "max_canvas", "scores"}``
    """
    if not canvas_scores:
        return {"average": 0.0, "min_canvas": "", "max_canvas": "", "scores": {}}

    avg = round(sum(canvas_scores.values()) / len(canvas_scores), 2)
    min_canvas = min(canvas_scores, key=canvas_scores.get)  # type: ignore[arg-type]
    max_canvas = max(canvas_scores, key=canvas_scores.get)  # type: ignore[arg-type]

    return {
        "average": avg,
        "min_canvas": min_canvas,
        "max_canvas": max_canvas,
        "scores": dict(canvas_scores),
    }


def evaluate_runbook_applicability(gate_result: dict, runbooks: list[dict]) -> list[dict]:
    """Match a failed gate result to applicable runbooks.

    Parameters
    ----------
    gate_result : dict
        ``{"gate_id": str, "status": str, ...}``
    runbooks : list[dict]
        Each must have ``trigger_gate`` key.

    Returns
    -------
    list[dict]
        Matching runbook entries.
    """
    gate_id = gate_result.get("gate_id", "")
    status = gate_result.get("status", "pass")
    if status == "pass":
        return []
    return [rb for rb in runbooks if rb.get("trigger_gate") == gate_id]


def compute_uqs_trend(history: list[dict]) -> dict:
    """Compute UQS score trend from historical data.

    Parameters
    ----------
    history : list[dict]
        Each: ``{"uqs_score": float, "computed_at": str}``.
        Must be ordered chronologically (oldest first).

    Returns
    -------
    dict
        ``{"direction", "velocity", "current", "previous", "data_points"}``
    """
    data_points = len(history)
    if data_points == 0:
        return {
            "direction": "stable",
            "velocity": 0.0,
            "current": 0.0,
            "previous": 0.0,
            "data_points": 0,
        }

    current = history[-1].get("uqs_score", 0.0)
    previous = history[-2].get("uqs_score", current) if data_points >= 2 else current
    velocity = round(current - previous, 2)

    if velocity > 1.0:
        direction = "improving"
    elif velocity < -1.0:
        direction = "declining"
    else:
        direction = "stable"

    return {
        "direction": direction,
        "velocity": velocity,
        "current": current,
        "previous": previous,
        "data_points": data_points,
    }


# ── self-test ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sample_graph: dict = {
        "nodes": [
            {"id": "g1", "type": "gate-sast"},
            {"id": "g2", "type": "gate-unit"},
            {"id": "g3", "type": "gate-secret"},
            {"id": "g4", "type": "gate-review"},
            {"id": "g5", "type": "gate-e2e"},
            {"id": "s1", "type": "src-repo"},
            {"id": "s2", "type": "src-pipeline"},
            {"id": "c1", "type": "con-uqs"},
            {"id": "c2", "type": "con-compliance"},
            {"id": "x1", "type": "xc-sdc"},
            {"id": "t1", "type": "tgt-stig"},
            {"id": "t2", "type": "tgt-fedramp-mod"},
        ],
        "edges": [
            {"source": "s1", "target": "g1"},
            {"source": "s1", "target": "g2"},
            {"source": "s1", "target": "g3"},
            {"source": "s1", "target": "g4"},
            {"source": "s2", "target": "g5"},
            {"source": "g1", "target": "c1"},
            {"source": "g2", "target": "c1"},
            {"source": "g3", "target": "c2"},
            {"source": "g4", "target": "c2"},
            {"source": "g5", "target": "c1"},
            {"source": "c1", "target": "x1"},
            {"source": "c2", "target": "t1"},
            {"source": "c2", "target": "t2"},
        ],
    }

    print("=== QDC Assessment ===")
    result = assess_quality_design(sample_graph)
    print(json.dumps(result, indent=2))

    print("\n=== UQS Score ===")
    gate_results = [
        {"gate_id": "sast", "status": "pass", "dimension": "security_posture", "score": 95.0},
        {"gate_id": "dast", "status": "pass", "dimension": "security_posture", "score": 88.0},
        {"gate_id": "coverage", "status": "pass", "dimension": "test_coverage", "score": 82.0},
        {"gate_id": "review", "status": "pass", "dimension": "code_quality", "score": 90.0},
        {"gate_id": "runtime", "status": "pass", "dimension": "runtime_health", "score": 78.0},
        {"gate_id": "assessment", "status": "pass", "dimension": "compliance_readiness", "score": 85.0},
    ]
    uqs = compute_uqs(gate_results)
    print(json.dumps(uqs, indent=2))

    print("\n=== SA-11 Mapping ===")
    print(json.dumps(map_gate_to_sa11("sast"), indent=2))
    print(json.dumps(map_gate_to_sa11("unknown_gate"), indent=2))

    print("\n=== OSCAL Evidence ===")
    evidence = emit_oscal_evidence({"gate_id": "sast", "status": "pass"})
    print(json.dumps(evidence, indent=2))

    print("\n=== Quality Gaps ===")
    gaps = detect_quality_gaps(result)
    print(json.dumps(gaps, indent=2))

    print("\n=== Cross-Canvas Aggregate ===")
    agg = aggregate_cross_canvas_quality({"idc": 85.0, "sdc": 72.0, "ndc": 90.0, "bdc": 78.0})
    print(json.dumps(agg, indent=2))

    print("\n=== UQS Trend ===")
    trend = compute_uqs_trend(
        [
            {"uqs_score": 72.0, "computed_at": "2026-04-01T00:00:00Z"},
            {"uqs_score": 75.5, "computed_at": "2026-04-02T00:00:00Z"},
            {"uqs_score": 78.0, "computed_at": "2026-04-03T00:00:00Z"},
            {"uqs_score": 82.3, "computed_at": "2026-04-04T00:00:00Z"},
        ]
    )
    print(json.dumps(trend, indent=2))

    print("\n=== Runbook Match ===")
    runbooks = [
        {"trigger_gate": "sast", "name": "SAST Remediation", "steps": ["fix", "rescan"]},
        {"trigger_gate": "dast", "name": "DAST Remediation", "steps": ["patch", "retest"]},
    ]
    matched = evaluate_runbook_applicability({"gate_id": "sast", "status": "fail"}, runbooks)
    print(json.dumps(matched, indent=2))

    print("\nSelf-test complete.")
