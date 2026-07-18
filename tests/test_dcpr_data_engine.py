# CUI // SP-CTI
"""Behavior tests for tools/data_canvas/data_engine.py (dcpr-qa-01).

data_engine is the largest DDC engine and had zero real coverage. Its public
surface is a set of pure graph functions — no DB, no LLM — so these tests build
small design graphs and assert on the computed compliance / coverage / lineage
output.

Covered public functions:
  assess_data_design, compute_classification_coverage, detect_data_gaps,
  compute_nist_coverage, compute_data_governance, analyze_column_lineage,
  get_column_impact, build_column_lineage_dag, compute_downstream_impact,
  validate_lineage_edge, summarize_lineage, generate_contract_assertions.
"""

import pytest

from tools.data_canvas import data_engine as de

EMPTY_GRAPH = {"nodes": [], "edges": [], "boundaries": []}


# ── assess_data_design ────────────────────────────────────────────────────────

def test_assess_empty_graph_is_clean():
    result = de.assess_data_design("d1", EMPTY_GRAPH)
    assert result["design_id"] == "d1"
    assert result["findings"] == []
    assert result["failed"] == 0
    assert result["passed"] == result["total_rules"]
    # No findings ⇒ no penalty ⇒ perfect risk score / grade A.
    assert result["risk_score"] == 100.0
    assert result["posture_grade"] == "A"


def test_assess_cui_without_encryption_fires_enc001():
    # An entity that owns a CUI column but has no encryption control connected
    # must trip DDC-ENC-001 (CAT1).
    graph = {
        "nodes": [
            {"id": "e1", "type": "ent-table", "label": "Customers"},
            {"id": "c1", "type": "col-cui", "label": "ssn"},
        ],
        "edges": [{"source": "e1", "target": "c1"}],
        "boundaries": [],
    }
    result = de.assess_data_design("d2", graph)
    rule_ids = {f["rule_id"] for f in result["findings"]}
    assert "DDC-ENC-001" in rule_ids
    assert result["failed"] >= 1
    assert result["risk_score"] < 100.0
    # DDC-ENC-001 is CAT1 → penalty ≥ 10.
    assert result["risk_score"] <= 90.0


def test_assess_cui_with_encryption_clears_enc001():
    graph = {
        "nodes": [
            {"id": "e1", "type": "ent-table", "label": "Customers"},
            {"id": "c1", "type": "col-cui", "label": "ssn"},
            {"id": "k1", "type": "ctrl-encryption", "label": "KMS"},
        ],
        "edges": [
            {"source": "e1", "target": "c1"},
            {"source": "e1", "target": "k1"},
        ],
        "boundaries": [],
    }
    result = de.assess_data_design("d3", graph)
    rule_ids = {f["rule_id"] for f in result["findings"]}
    assert "DDC-ENC-001" not in rule_ids


# ── compute_classification_coverage ──────────────────────────────────────────

def test_classification_coverage_partial():
    graph = {
        "nodes": [
            {"id": "e1", "type": "ent-table", "label": "A"},
            {"id": "e2", "type": "ent-table", "label": "B"},
        ],
        "edges": [],
        "boundaries": [
            {
                "id": "z1",
                "type": "bnd-classification",
                "label": "CUI Zone",
                "contained_nodes": ["e1"],
            }
        ],
    }
    cov = de.compute_classification_coverage(graph)
    assert cov["total_entities"] == 2
    assert cov["classified"] == 1
    assert cov["unclassified"] == 1
    assert cov["coverage_pct"] == 50.0
    assert len(cov["zones"]) == 1
    assert cov["zones"][0]["entity_count"] == 1
    assert "B" in cov["unclassified_entities"]


def test_classification_coverage_empty_is_full():
    cov = de.compute_classification_coverage(EMPTY_GRAPH)
    assert cov["total_entities"] == 0
    assert cov["coverage_pct"] == 100.0


# ── detect_data_gaps ─────────────────────────────────────────────────────────

def test_detect_gaps_maps_finding_to_recommendation():
    graph = {
        "nodes": [
            {"id": "e1", "type": "ent-table", "label": "Customers"},
            {"id": "c1", "type": "col-cui", "label": "ssn"},
        ],
        "edges": [{"source": "e1", "target": "c1"}],
        "boundaries": [],
    }
    assessment = de.assess_data_design("d4", graph)
    gaps = de.detect_data_gaps(assessment)
    enc_gaps = [g for g in gaps if g["rule_id"] == "DDC-ENC-001"]
    assert enc_gaps, "expected a gap for DDC-ENC-001"
    gap = enc_gaps[0]
    assert gap["recommended_control"] == "ctrl-encryption"
    assert gap["recommendation"]
    assert gap["severity"] == "CAT1"


def test_detect_gaps_dedups_by_rule():
    # Two CUI-unencrypted entities fire DDC-ENC-001 twice; gaps dedup per rule.
    graph = {
        "nodes": [
            {"id": "e1", "type": "ent-table", "label": "A"},
            {"id": "ca", "type": "col-cui", "label": "ssn"},
            {"id": "e2", "type": "ent-table", "label": "B"},
            {"id": "cb", "type": "col-cui", "label": "dob"},
        ],
        "edges": [
            {"source": "e1", "target": "ca"},
            {"source": "e2", "target": "cb"},
        ],
        "boundaries": [],
    }
    assessment = de.assess_data_design("d5", graph)
    gaps = de.detect_data_gaps(assessment)
    enc_gaps = [g for g in gaps if g["rule_id"] == "DDC-ENC-001"]
    assert len(enc_gaps) == 1


# ── compute_nist_coverage ────────────────────────────────────────────────────

def test_nist_coverage_credits_encryption_control():
    graph = {
        "nodes": [{"id": "k1", "type": "ctrl-encryption", "label": "KMS"}],
        "edges": [],
        "boundaries": [],
    }
    nist = de.compute_nist_coverage(graph)
    assert nist["covered_families"] >= 1
    assert nist["overall_coverage_pct"] > 0
    sc = next(f for f in nist["families"] if f["code"] == "SC")
    assert sc["coverage_pct"] > 0
    assert "ctrl-encryption" in sc["controls"]


def test_nist_coverage_empty_is_zero():
    nist = de.compute_nist_coverage(EMPTY_GRAPH)
    assert nist["covered_families"] == 0
    assert nist["overall_coverage_pct"] == 0


# ── compute_data_governance ──────────────────────────────────────────────────

def test_governance_score_shape_and_pass():
    graph = {
        "nodes": [{"id": "dom1", "type": "ent-domain", "label": "Finance"}],
        "edges": [],
        "boundaries": [],
    }
    gov = de.compute_data_governance(graph)
    assert 0 <= gov["score"] <= 100
    assert gov["grade"] in {"A", "B", "C", "D", "F"}
    assert gov["maturity"]["level"] in {1, 2, 3, 4, 5}
    assert gov["total_checks"] > 0
    assert gov["passed_checks"] >= 1  # GOV-ST-01 satisfied by ent-domain
    # ent-domain is a mesh node type.
    assert gov["has_mesh"] is True
    # GOV-ST-01 should be a PASS.
    st01 = next(c for c in gov["checks"] if c["id"] == "GOV-ST-01")
    assert st01["passed"] is True


def test_governance_bare_graph_flags_recommendations():
    graph = {
        "nodes": [{"id": "t1", "type": "ent-table", "label": "T"}],
        "edges": [],
        "boundaries": [],
    }
    gov = de.compute_data_governance(graph)
    assert gov["has_mesh"] is False
    assert gov["recommendations"], "a bare graph should surface recommendations"


# ── Column lineage ───────────────────────────────────────────────────────────

def _chain_records():
    # amount flows e1 → e2 → e3
    return [
        {"id": "l1", "source_node_id": "e1", "target_node_id": "e2", "column_name": "amount"},
        {"id": "l2", "source_node_id": "e2", "target_node_id": "e3", "column_name": "amount"},
    ]


def test_validate_lineage_edge_valid_and_invalid():
    ok = de.validate_lineage_edge({"source_node_id": "e1", "target_node_id": "e2"})
    assert ok["valid"] is True
    assert ok["errors"] == []

    missing = de.validate_lineage_edge({"source_node_id": "e1"})
    assert missing["valid"] is False
    assert any("target_node_id" in e for e in missing["errors"])

    self_loop = de.validate_lineage_edge({"source_node_id": "e1", "target_node_id": "e1"})
    assert self_loop["valid"] is False


def test_build_dag_counts_nodes_and_edges():
    dag = de.build_column_lineage_dag(_chain_records())
    assert dag["edge_count"] == 2
    assert dag["node_count"] == 3


def test_downstream_impact_reaches_all_descendants():
    impact = de.compute_downstream_impact("e1", "amount", _chain_records())
    assert impact["total_impacted"] == 2
    reached = {n["entity_id"] for n in impact["impacted_nodes"]}
    assert reached == {"e2", "e3"}


def test_get_column_impact_wrapper_adds_metadata():
    result = de.get_column_impact("dz", "e1", "amount", _chain_records())
    assert result["design_id"] == "dz"
    assert result["direction"] == "downstream"
    assert result["total_impacted"] == 2


def test_summarize_lineage_metrics():
    summary = de.summarize_lineage(_chain_records(), EMPTY_GRAPH)
    assert summary["total_edges"] == 2
    assert summary["unique_columns"] == 1
    assert summary["unique_entities"] == 3


def test_analyze_column_lineage_bundles_results():
    graph = {
        "nodes": [
            {"id": "e1", "type": "ent-table"},
            {"id": "e2", "type": "ent-table"},
            {"id": "e3", "type": "ent-table"},
        ],
        "edges": [],
        "boundaries": [],
    }
    out = de.analyze_column_lineage("dbundle", graph, _chain_records())
    assert out["design_id"] == "dbundle"
    assert out["dag"]["edge_count"] == 2
    assert out["summary"]["total_edges"] == 2
    assert isinstance(out["contract_assertions"], list)
    assert out["assertion_count"] == len(out["contract_assertions"])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
