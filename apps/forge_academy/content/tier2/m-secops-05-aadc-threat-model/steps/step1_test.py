"""Tests for SecOps Mission 5: AADC Threat Modeling."""

import pytest
from step1_starter import (
    DESIGN_GRAPH,
    ATLAS_THREAT_MAP,
    run_stride_analysis,
    map_atlas_techniques,
    generate_threat_report,
)


def test_stride_returns_list():
    result = run_stride_analysis(DESIGN_GRAPH)
    assert isinstance(result, list), "run_stride_analysis must return a list"


def test_stride_finds_minimum_threats():
    result = run_stride_analysis(DESIGN_GRAPH)
    assert len(result) >= 4, f"Expected ≥4 threats, got {len(result)}"


def test_stride_has_critical_or_high():
    result = run_stride_analysis(DESIGN_GRAPH)
    severe = [t for t in result if t.get("severity") in ("CRITICAL", "HIGH")]
    assert len(severe) >= 2, f"Expected ≥2 CRITICAL/HIGH threats, got {len(severe)}"


def test_stride_finding_has_required_fields():
    result = run_stride_analysis(DESIGN_GRAPH)
    assert result, "No threats returned"
    for finding in result:
        for field in ("id", "category", "severity", "title", "mitigation", "affected_nodes"):
            assert field in finding, f"Finding missing field: {field}"


def test_stride_affected_nodes_are_from_graph():
    result = run_stride_analysis(DESIGN_GRAPH)
    valid_ids = {n["id"] for n in DESIGN_GRAPH["nodes"]}
    for finding in result:
        for nid in finding.get("affected_nodes", []):
            assert nid in valid_ids, f"affected_node {nid} not in graph"


def test_atlas_returns_dict():
    result = map_atlas_techniques(DESIGN_GRAPH)
    assert isinstance(result, dict), "map_atlas_techniques must return a dict"


def test_atlas_maps_minimum_node_types():
    result = map_atlas_techniques(DESIGN_GRAPH)
    assert len(result) >= 3, f"Expected ≥3 mapped node types, got {len(result)}"


def test_atlas_values_contain_technique_ids():
    result = map_atlas_techniques(DESIGN_GRAPH)
    for node_type, techniques in result.items():
        assert isinstance(techniques, list), f"Techniques for {node_type} must be a list"
        assert all(t.startswith("AML.") for t in techniques), \
            f"All techniques must start with 'AML.', got: {techniques}"


def test_report_contains_header():
    threats = run_stride_analysis(DESIGN_GRAPH)
    atlas = map_atlas_techniques(DESIGN_GRAPH)
    report = generate_threat_report(threats, atlas)
    assert isinstance(report, str), "generate_threat_report must return a string"
    assert "## Threat Model" in report, "Report must contain '## Threat Model' header"


def test_report_contains_atlas_reference():
    threats = run_stride_analysis(DESIGN_GRAPH)
    atlas = map_atlas_techniques(DESIGN_GRAPH)
    report = generate_threat_report(threats, atlas)
    assert "AML.T" in report, "Report must contain at least one ATLAS technique reference (AML.T...)"


def test_report_contains_mitigation():
    threats = run_stride_analysis(DESIGN_GRAPH)
    atlas = map_atlas_techniques(DESIGN_GRAPH)
    report = generate_threat_report(threats, atlas)
    critical = [t for t in threats if t.get("severity") == "CRITICAL"]
    if critical:
        assert critical[0]["mitigation"][:20] in report, \
            "Report must include mitigation text for CRITICAL findings"
