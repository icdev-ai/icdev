# [CUI // SP-CTI]
"""Tests for tools/security_canvas/artifacts.py — ATO artifact generation.

Covers the four public generators (SSP, SAR, POA&M, and the artifact bundle
orchestrator) that back the Security Design Canvas export routes
(``blueprint.py`` ~:1049-1252).

Design-data source
------------------
``artifacts.py`` is pure: every generator consumes plain ``graph_data`` dicts
(``nodes`` / ``edges`` / ``boundaries``) plus assessment/remediation dicts.
This is byte-for-byte the object the blueprint hands in — it loads the design's
``graph_json`` column, ``json.loads()`` it, and passes the resulting dict
directly (see ``blueprint.py`` lines 1050-1071). The generators never touch the
database, so these tests construct the graph dict in-memory rather than
round-tripping through the ``security_designs`` table; that mirrors exactly what
the module reads at runtime. (No conftest schema change was needed.)

Classification markings
-----------------------
The CUI/SP-CTI marking asserted here is *derived* from
``tools.compliance.classification_manager`` conventions rather than hardcoded,
so these tests validate that the artifact carries the marking the classification
authority defines, not a magic string literal.
"""

import pytest

from tools.security_canvas.artifacts import (
    generate_ssp_artifact,
    generate_sar_artifact,
    generate_poam_artifact,
    generate_artifact_bundle,
)
from tools.security_canvas.security_engine import (
    run_security_assessment,
    compute_nist_coverage,
)
from tools.security_canvas.remediation import generate_remediation_plan
from tools.compliance import classification_manager as cm


# ── Derived-marking helpers ──────────────────────────────────────────────────


def _expected_cui_marking() -> str:
    """Derive the CUI marking token via classification_manager conventions.

    For category ``CTI`` the manager builds ``CUI // SP-CTI``. We extract that
    fragment from the canonical banner instead of hardcoding it, so the
    assertion tracks the classification authority.
    """
    banner = cm.get_marking_banner("CUI", category="CTI")
    for line in banner.splitlines():
        if "CUI // SP-" in line:
            frag = line[line.index("CUI // SP-"):]
            return frag.rstrip(")").strip()
    # Fallback: at minimum the IL4 classification token must appear.
    return cm.get_classification_for_il("IL4")


EXPECTED_MARKING = _expected_cui_marking()
EXPECTED_CLASSIFICATION = cm.get_classification_for_il("IL5")  # -> "CUI"


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def rich_graph():
    """A non-trivial design: assets, controls, boundaries, and mixed flows.

    Chosen so the assessment yields findings across severities (populating SAR
    phases / POA&M rows) while still recording present controls (populating NIST
    coverage families).
    """
    return {
        "nodes": [
            {"id": "inet", "type": "boundary-internet", "label": "Internet"},
            {"id": "client", "type": "asset-client", "label": "User Workstation"},
            {"id": "web", "type": "asset-server", "label": "Web Server"},
            {"id": "db", "type": "asset-database", "label": "Customer DB"},
            {"id": "siem", "type": "ctrl-siem", "label": "Splunk SIEM"},
            {"id": "fw", "type": "ctrl-firewall", "label": "Perimeter Firewall"},
            {"id": "idp", "type": "ctrl-idp", "label": "Okta IdP"},
        ],
        "edges": [
            {"id": "e1", "source": "client", "target": "web", "authenticated": True, "encrypted": True},
            {"id": "e2", "source": "web", "target": "db", "authenticated": False, "encrypted": False},
            {"id": "e3", "source": "fw", "target": "web"},
            {"id": "e4", "source": "siem", "target": "web"},
            {"id": "e5", "source": "idp", "target": "client"},
        ],
        "boundaries": [
            {"id": "b1", "type": "boundary-network", "label": "App Tier", "contained_assets": ["web"]},
            {"id": "b2", "type": "boundary-data", "label": "Data Tier", "contained_assets": ["db"]},
        ],
    }


@pytest.fixture
def empty_graph():
    """A minimal/empty design — no nodes, edges, or boundaries."""
    return {"nodes": [], "edges": [], "boundaries": []}


DESIGN_NAME = "Acme Customer Portal"
DESIGN_ID = "design-abc-123"


def _assessment(graph):
    return run_security_assessment(DESIGN_ID, graph)


def _remediation(graph):
    return generate_remediation_plan(_assessment(graph), graph)


# ── SSP ──────────────────────────────────────────────────────────────────────


def test_ssp_has_required_sections(rich_graph):
    ssp = generate_ssp_artifact(
        DESIGN_NAME, DESIGN_ID, rich_graph, _assessment(rich_graph), compute_nist_coverage(rich_graph)
    )
    assert isinstance(ssp, str) and ssp.strip()
    assert "# System Security Plan (SSP)" in ssp
    for section in (
        "## 1. System Description",
        "## 2. Authorization Boundary",
        "## 3. Information Types and Impact Level",
        "## 4. Security Control Implementation",
        "## 5. Security Findings Summary",
        "## 6. Continuous Monitoring Strategy",
    ):
        assert section in ssp, f"SSP missing required section: {section}"


def test_ssp_carries_derived_cui_marking(rich_graph):
    ssp = generate_ssp_artifact(
        DESIGN_NAME, DESIGN_ID, rich_graph, _assessment(rich_graph), compute_nist_coverage(rich_graph)
    )
    # Marking presence, derived from classification_manager (not hardcoded).
    assert EXPECTED_MARKING in ssp
    assert EXPECTED_CLASSIFICATION in ssp


def test_ssp_references_design_data(rich_graph):
    ssp = generate_ssp_artifact(
        DESIGN_NAME, DESIGN_ID, rich_graph, _assessment(rich_graph), compute_nist_coverage(rich_graph)
    )
    # System name and id.
    assert DESIGN_NAME in ssp
    assert DESIGN_ID in ssp
    # Component count reflects the seeded graph.
    assert f"{len(rich_graph['nodes'])} components" in ssp
    # A boundary label from the design appears in the Authorization Boundary section.
    assert "App Tier" in ssp
    # At least one present control flowed into NIST coverage: a family the SIEM/
    # firewall/IdP cover shows a non-"Not Implemented" status.
    assert ("Partially Implemented" in ssp) or ("| Implemented |" in ssp)


# ── SAR ──────────────────────────────────────────────────────────────────────


def test_sar_has_required_sections(rich_graph):
    sar = generate_sar_artifact(DESIGN_NAME, _assessment(rich_graph), _remediation(rich_graph))
    assert isinstance(sar, str) and sar.strip()
    assert "# Security Assessment Report (SAR)" in sar
    for section in (
        "## 1. Executive Summary",
        "## 2. Assessment Methodology",
        "## 3. Findings Detail",
        "## 4. Risk Summary",
        "## 5. Recommendations",
    ):
        assert section in sar, f"SAR missing required section: {section}"


def test_sar_carries_derived_cui_marking(rich_graph):
    sar = generate_sar_artifact(DESIGN_NAME, _assessment(rich_graph), _remediation(rich_graph))
    assert EXPECTED_MARKING in sar
    assert EXPECTED_CLASSIFICATION in sar


def test_sar_references_findings_and_design_nodes(rich_graph):
    assessment = _assessment(rich_graph)
    sar = generate_sar_artifact(DESIGN_NAME, assessment, _remediation(rich_graph))
    # System name.
    assert DESIGN_NAME in sar
    # The assessment produced findings; the detail table reports the count.
    assert assessment["findings"], "expected the rich design to yield findings"
    assert f"| Total Findings | {len(assessment['findings'])} |" in sar
    # A design node label surfaces in the findings detail (affected entity).
    assert ("Web Server" in sar) or ("Customer DB" in sar)


# ── POA&M ────────────────────────────────────────────────────────────────────


def test_poam_has_structure(rich_graph):
    poam = generate_poam_artifact(DESIGN_NAME, _remediation(rich_graph))
    assert isinstance(poam, str) and poam.strip()
    assert "# Plan of Action & Milestones (POA&M)" in poam
    # Table header columns present.
    assert "| POAM-ID | Weakness | Severity | Milestone" in poam


def test_poam_carries_derived_cui_marking(rich_graph):
    poam = generate_poam_artifact(DESIGN_NAME, _remediation(rich_graph))
    assert EXPECTED_MARKING in poam
    assert EXPECTED_CLASSIFICATION in poam


def test_poam_references_design_data(rich_graph):
    remediation = _remediation(rich_graph)
    poam = generate_poam_artifact(DESIGN_NAME, remediation)
    # System name.
    assert DESIGN_NAME in poam
    # At least one sequenced POA&M entry when remediation actions exist.
    assert remediation["total_actions"] > 0
    assert "POAM-0001" in poam
    # A remediation weakness title appears as a table row.
    first_action = remediation["phases"][0]["actions"][0]
    assert first_action["title"] in poam


# ── Bundle orchestrator ──────────────────────────────────────────────────────


def test_bundle_returns_all_artifacts_and_metadata(rich_graph):
    bundle = generate_artifact_bundle(DESIGN_ID, DESIGN_NAME, rich_graph)
    assert set(bundle.keys()) == {"ssp", "sar", "poam", "metadata"}
    for key in ("ssp", "sar", "poam"):
        assert isinstance(bundle[key], str) and bundle[key].strip()
    meta = bundle["metadata"]
    for field in ("design_name", "generated_at", "risk_score", "posture_grade"):
        assert field in meta, f"bundle metadata missing {field}"
    assert meta["design_name"] == DESIGN_NAME


def test_bundle_artifacts_are_complete_documents(rich_graph):
    bundle = generate_artifact_bundle(DESIGN_ID, DESIGN_NAME, rich_graph)
    # Each artifact is a titled document carrying the derived CUI marking.
    assert "# System Security Plan (SSP)" in bundle["ssp"]
    assert "# Security Assessment Report (SAR)" in bundle["sar"]
    assert "# Plan of Action & Milestones (POA&M)" in bundle["poam"]
    for key in ("ssp", "sar", "poam"):
        assert EXPECTED_MARKING in bundle[key], f"{key} missing CUI marking"
    # Referenced design name appears across the bundle.
    assert DESIGN_NAME in bundle["ssp"]
    assert DESIGN_NAME in bundle["poam"]


# ── Empty / minimal design (graceful, no traceback) ──────────────────────────


def test_empty_design_ssp_is_well_formed(empty_graph):
    ssp = generate_ssp_artifact(
        "Empty Design", "design-empty", empty_graph, _assessment(empty_graph), compute_nist_coverage(empty_graph)
    )
    assert isinstance(ssp, str)
    assert "# System Security Plan (SSP)" in ssp
    assert EXPECTED_MARKING in ssp
    # Graceful messaging for the missing authorization boundary.
    assert "No authorization boundaries defined" in ssp
    assert "0 components" in ssp


def test_empty_design_sar_is_well_formed(empty_graph):
    sar = generate_sar_artifact("Empty Design", _assessment(empty_graph), _remediation(empty_graph))
    assert isinstance(sar, str)
    assert "# Security Assessment Report (SAR)" in sar
    assert EXPECTED_MARKING in sar


def test_empty_design_poam_is_well_formed(empty_graph):
    poam = generate_poam_artifact("Empty Design", _remediation(empty_graph))
    assert isinstance(poam, str)
    assert "# Plan of Action & Milestones (POA&M)" in poam
    assert EXPECTED_MARKING in poam


def test_empty_design_bundle_no_traceback(empty_graph):
    # Must not raise on an empty design.
    bundle = generate_artifact_bundle("design-empty", "Empty Design", empty_graph)
    assert set(bundle.keys()) == {"ssp", "sar", "poam", "metadata"}
    assert bundle["metadata"]["design_name"] == "Empty Design"
    # Posture grade is still a valid letter grade.
    assert bundle["metadata"]["posture_grade"] in {"A", "B", "C", "D", "F"}
