# CUI // SP-CTI
"""Unit tests for the AADC rule-based assessment engine (penta-aadc-06).

``agentic_engine.assess_design`` fans out to one deterministic check per rule
family (NIST AI RMF, OWASP LLM Top 10, OMB M-25-21 HITL paths, MITRE ATLAS,
Phase-4 observability/memory, and autonomy-level classification). Each test
here crafts a minimal design that isolates one family, asserts that family's
finding fires, and (where relevant) that the aggregate score moves relative to
a clean baseline. No LLM calls are involved — the engine is pure rules.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agentic_ai_canvas import agentic_engine as eng  # noqa: E402


# ---------------------------------------------------------------------------
# Tiny graph builders
# ---------------------------------------------------------------------------

def _n(nid: str, ntype: str, label: str = "") -> dict:
    return {"id": nid, "type": ntype, "label": label or ntype, "x": 0, "y": 0}


def _e(src: str, tgt: str) -> dict:
    return {"id": f"{src}-{tgt}", "source": src, "target": tgt}


def _graph(nodes, edges):
    return {"nodes": nodes, "edges": edges}


def _findings(result: dict) -> list[dict]:
    import json
    return json.loads(result["findings_json"])


def _frameworks(result: dict) -> set[str]:
    return {f.get("framework", "") for f in _findings(result)}


# A well-formed, safety-conscious design used as the clean baseline. It has a
# sanitizer upstream of the LLM, an output-validator downstream, a model
# registry, HITL gate, audit logger and confidence gate.
_CLEAN_NODES = [
    _n("in", "inference-input"),
    _n("san", "input-sanitizer"),
    _n("llm", "llm"),
    _n("val", "output-validator"),
    _n("reg", "model-registry"),
    _n("conf", "confidence-threshold"),
    _n("hitl", "hitl-gate"),
    _n("audit", "audit-logger"),
    _n("appr", "approval-workflow"),
]
_CLEAN_EDGES = [
    _e("in", "san"), _e("san", "llm"), _e("llm", "conf"), _e("conf", "val"),
    _e("val", "hitl"), _e("hitl", "appr"), _e("appr", "audit"),
]
_CLEAN = _graph(_CLEAN_NODES, _CLEAN_EDGES)


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------

class TestBaseline:
    def test_clean_design_scores_higher_than_empty(self):
        clean = eng.assess_design("d-clean", _CLEAN, {"domain": "gov"})
        empty = eng.assess_design("d-empty", _graph([], []), {"domain": "gov"})
        assert clean["score"] > empty["score"]

    def test_clean_design_has_no_critical_autonomy_finding(self):
        result = eng.assess_design("d-clean", _CLEAN, {"domain": "gov"})
        titles = [f["title"] for f in _findings(result)]
        assert not any("Unconstrained autonomous agent" in t for t in titles)

    def test_result_shape_is_insert_ready(self):
        result = eng.assess_design("d1", _CLEAN, {"domain": "gov"})
        for key in ("id", "design_id", "score", "nist_rmf_score", "owasp_score",
                    "omb_compliant", "autonomy_max", "safety_impacting",
                    "rights_impacting", "findings_json", "atlas_threats", "created_at"):
            assert key in result


# ---------------------------------------------------------------------------
# NIST AI RMF family
# ---------------------------------------------------------------------------

class TestNistRmf:
    def test_empty_design_fires_rmf_findings(self):
        result = eng.assess_design("d", _graph([], []), {})
        assert "NIST AI RMF" in _frameworks(result)

    def test_rmf_score_is_percentage(self):
        result = eng.assess_design("d", _CLEAN, {"domain": "gov"})
        assert 0.0 <= result["nist_rmf_score"] <= 100.0

    def test_missing_oversight_node_flagged(self):
        # No approval-workflow/hitl-gate → GOVERN oversight check fails.
        g = _graph([_n("in", "inference-input"), _n("llm", "llm"),
                    _n("val", "output-validator")],
                   [_e("in", "llm"), _e("llm", "val")])
        titles = [f["title"] for f in _findings(eng.assess_design("d", g, {}))]
        assert any("Oversight" in t for t in titles)


# ---------------------------------------------------------------------------
# OWASP LLM Top 10 family
# ---------------------------------------------------------------------------

class TestOwaspLlm:
    def test_llm_without_input_sanitizer_fires_llm01(self):
        g = _graph([_n("in", "inference-input"), _n("llm", "llm"),
                    _n("val", "output-validator")],
                   [_e("in", "llm"), _e("llm", "val")])
        ids = [f.get("risk_id") for f in _findings(eng.assess_design("d", g, {}))]
        assert "LLM01" in ids

    def test_llm_without_output_validator_fires_llm02(self):
        g = _graph([_n("in", "inference-input"), _n("san", "input-sanitizer"),
                    _n("llm", "llm")],
                   [_e("in", "san"), _e("san", "llm")])
        ids = [f.get("risk_id") for f in _findings(eng.assess_design("d", g, {}))]
        assert "LLM02" in ids

    def test_sanitized_validated_llm_clears_llm01_and_llm02(self):
        ids = [f.get("risk_id") for f in _findings(eng.assess_design("d", _CLEAN, {}))]
        assert "LLM01" not in ids and "LLM02" not in ids

    def test_design_without_llm_does_not_fire_llm01(self):
        g = _graph([_n("in", "inference-input"), _n("val", "output-validator")],
                   [_e("in", "val")])
        ids = [f.get("risk_id") for f in _findings(eng.assess_design("d", g, {}))]
        assert "LLM01" not in ids


# ---------------------------------------------------------------------------
# OMB M-25-21 HITL path family
# ---------------------------------------------------------------------------

class TestHitlPaths:
    def test_safety_impacting_agent_without_hitl_flagged(self):
        # domain 'medical' is safety-impacting; agent with no HITL downstream.
        g = _graph([_n("in", "inference-input"), _n("agent", "autonomous-agent"),
                    _n("val", "output-validator")],
                   [_e("in", "agent"), _e("agent", "val")])
        result = eng.assess_design("d", g, {"domain": "medical"})
        assert "OMB M-25-21" in _frameworks(result)
        assert result["safety_impacting"] == 1

    def test_non_impacting_domain_skips_hitl_check(self):
        g = _graph([_n("in", "inference-input"), _n("agent", "autonomous-agent"),
                    _n("val", "output-validator")],
                   [_e("in", "agent"), _e("agent", "val")])
        result = eng.assess_design("d", g, {"domain": "gov"})
        assert result["safety_impacting"] == 0
        assert "OMB M-25-21" not in _frameworks(result)

    def test_rights_impacting_domain_sets_flag(self):
        result = eng.assess_design("d", _CLEAN, {"domain": "benefits"})
        assert result["rights_impacting"] == 1


# ---------------------------------------------------------------------------
# MITRE ATLAS family
# ---------------------------------------------------------------------------

class TestAtlas:
    def test_llm_node_maps_atlas_techniques(self):
        import json
        g = _graph([_n("llm", "llm")], [])
        result = eng.assess_design("d", g, {})
        atlas = json.loads(result["atlas_threats"])
        techniques = {t["technique"] for t in atlas}
        assert "AML.T0051" in techniques  # prompt injection

    def test_design_without_mapped_nodes_has_no_threats(self):
        import json
        g = _graph([_n("san", "input-sanitizer")], [])
        result = eng.assess_design("d", g, {})
        assert json.loads(result["atlas_threats"]) == []


# ---------------------------------------------------------------------------
# Phase-4 observability family
# ---------------------------------------------------------------------------

class TestObservability:
    def test_partial_observability_fires_finding_and_scores(self):
        # One observability node present (trace-collector) but span/metrics
        # missing → obs scored (not None) and a Phase-4 finding fires.
        g = _graph([_n("llm", "llm"), _n("tc", "trace-collector"),
                    _n("val", "output-validator")],
                   [_e("llm", "tc"), _e("tc", "val")])
        result = eng.assess_design("d", g, {})
        assert result["obs_score"] is not None
        assert any(f.get("framework", "").startswith("NIST AI RMF (Phase 4)")
                   for f in _findings(result))

    def test_no_observability_nodes_leaves_score_none(self):
        result = eng.assess_design("d", _CLEAN, {})
        assert result["obs_score"] is None


# ---------------------------------------------------------------------------
# Phase-4 memory family
# ---------------------------------------------------------------------------

class TestMemory:
    def test_persistent_memory_without_audit_fires_memory_finding(self):
        # long-term-mem is both a memory node and a persistent-memory type;
        # with no audit-logger the persistent_memory_has_audit check fails.
        g = _graph([_n("agent", "autonomous-agent"), _n("mem", "long-term-mem"),
                    _n("val", "output-validator")],
                   [_e("agent", "mem"), _e("mem", "val")])
        result = eng.assess_design("d", g, {})
        assert result["mem_score"] is not None
        assert "NIST AI RMF (Memory Layer)" in _frameworks(result)

    def test_no_memory_nodes_leaves_score_none(self):
        g = _graph([_n("llm", "llm"), _n("val", "output-validator")],
                   [_e("llm", "val")])
        result = eng.assess_design("d", g, {})
        assert result["mem_score"] is None


# ---------------------------------------------------------------------------
# Autonomy-level classification family
# ---------------------------------------------------------------------------

class TestAutonomy:
    def test_unconstrained_agent_is_l5_critical(self):
        # autonomous-agent with no circuit-breaker / hitl / confidence → L5.
        g = _graph([_n("in", "inference-input"), _n("agent", "autonomous-agent"),
                    _n("api", "external-api")],
                   [_e("in", "agent"), _e("agent", "api")])
        result = eng.assess_design("d", g, {"domain": "gov"})
        assert result["autonomy_max"] == 5
        crit = [f for f in _findings(result) if f.get("severity") == "CRITICAL"]
        assert any("Unconstrained autonomous agent (L5)" in f["title"] for f in crit)

    def test_high_autonomy_without_hitl_fires_cat1(self):
        # autonomous-agent with a circuit-breaker (L3) but no HITL gate on its
        # direct output edge → CAT1 mandatory-HITL finding.
        g = _graph([_n("in", "inference-input"), _n("agent", "autonomous-agent"),
                    _n("cb", "circuit-breaker"), _n("val", "output-validator")],
                   [_e("in", "agent"), _e("agent", "cb"), _e("cb", "val")])
        result = eng.assess_design("d", g, {"domain": "gov"})
        assert result["autonomy_max"] >= 3
        sevs = [(f.get("severity"), f.get("title")) for f in _findings(result)]
        assert any(s == "CAT1" and "mandatory HITL" in t for s, t in sevs)

    def test_hitl_gated_agent_is_low_autonomy(self):
        g = _graph([_n("in", "inference-input"), _n("agent", "autonomous-agent"),
                    _n("hitl", "hitl-gate"), _n("val", "output-validator")],
                   [_e("in", "agent"), _e("agent", "hitl"), _e("hitl", "val")])
        result = eng.assess_design("d", g, {"domain": "gov"})
        assert result["autonomy_max"] <= 1

    def test_classify_autonomy_direct_unit(self):
        nodes = [_n("agent", "autonomous-agent"), _n("api", "external-api")]
        edges = [_e("agent", "api")]
        assert eng.classify_autonomy(nodes[0], nodes, edges) == 5

    def test_classify_impact_direct_unit(self):
        safety, rights = eng.classify_impact({"domain": "medical"})
        assert safety is True and rights is False
        safety2, rights2 = eng.classify_impact({"domain": "housing"})
        assert safety2 is False and rights2 is True


# ---------------------------------------------------------------------------
# Cost/init coverage holes not already covered by test_penta_aadc_cost.py
# ---------------------------------------------------------------------------

class TestCostHoles:
    def test_graph_with_no_llm_nodes_costs_nothing(self):
        from tools.agentic_ai_canvas import cost_estimator as ce
        graph = {"nodes": [{"id": "n1", "type": "vector-db"}], "edges": []}
        result = ce.estimate_design_cost(graph, runs_per_month=1000)
        assert result["total_per_run"] == 0
        assert result["total_monthly"] == 0

    @pytest.mark.parametrize("model", [
        "gpt-4o", "gpt-4o-mini", "claude-sonnet-4", "claude-haiku-4",
    ])
    def test_known_models_return_canonical_schema(self, model):
        from tools.agentic_ai_canvas import cost_estimator as ce
        from tools.agentic_ai_canvas.constants import AADC_MODEL_COSTS
        if model not in AADC_MODEL_COSTS:
            pytest.skip(f"{model} not in AADC_MODEL_COSTS")
        entry = ce._cost_for_model(model)
        assert {"input", "output", "avg_in", "avg_out"} <= set(entry)
