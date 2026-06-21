#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for the AADC Confidence Gate safety layer.

Covers:
  - evaluate(): proceed / escalate / block decisions
  - get_config() / update_config(): persistence and defaults
  - evaluate_pipeline_node(): convenience wrapper
  - check_confidence_gate_path(): assessment engine integration
  - agentic_engine.assess_design(): end-to-end with confidence gate finding
"""

import json
import sqlite3
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.agentic_ai_canvas.confidence_gate import (
    DEFAULT_THRESHOLD,
    check_confidence_gate_path,
    evaluate,
    evaluate_pipeline_node,
    get_config,
    update_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _design_id():
    return f"aadc-test-{uuid.uuid4().hex[:8]}"


def _node(ntype, label="N", nid=None):
    return {"id": nid or f"n-{uuid.uuid4().hex[:6]}", "type": ntype, "label": label}


def _edge(src, tgt, label=""):
    return {"id": f"e-{uuid.uuid4().hex[:6]}", "source": src, "target": tgt,
            "label": label}


# ---------------------------------------------------------------------------
# Fixtures — patch DB so tests don't need a real canvas DB
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_db(tmp_path):
    """Replace _get_conn with an in-memory SQLite connection."""
    db_path = tmp_path / "gate_test.db"
    conn = sqlite3.connect(str(db_path))

    def _fake_get_conn():
        return sqlite3.connect(str(db_path))

    with patch("tools.agentic_ai_canvas.confidence_gate._get_conn", _fake_get_conn):
        yield conn

    conn.close()


# ---------------------------------------------------------------------------
# evaluate() — core gate logic
# ---------------------------------------------------------------------------

class TestEvaluate:
    def test_high_confidence_proceeds(self):
        did = _design_id()
        result = evaluate(did, "Some research output", score=0.90)
        assert result["decision"] == "proceed"
        assert result["allowed"] is True
        assert result["score"] == 0.90
        assert result["threshold"] == DEFAULT_THRESHOLD

    def test_low_confidence_escalates_by_default(self):
        did = _design_id()
        result = evaluate(did, "Uncertain output", score=0.50)
        assert result["decision"] == "escalate"
        assert result["allowed"] is False

    def test_exact_threshold_proceeds(self):
        did = _design_id()
        result = evaluate(did, "Borderline output", score=DEFAULT_THRESHOLD)
        assert result["decision"] == "proceed"
        assert result["allowed"] is True

    def test_custom_threshold_override(self):
        did = _design_id()
        result = evaluate(did, "Output text", score=0.65, threshold=0.60)
        assert result["decision"] == "proceed"

    def test_custom_threshold_below_score_escalates(self):
        did = _design_id()
        result = evaluate(did, "Output text", score=0.55, threshold=0.60)
        assert result["decision"] == "escalate"
        assert result["allowed"] is False

    def test_score_out_of_range_raises(self):
        did = _design_id()
        with pytest.raises(ValueError, match="score must be"):
            evaluate(did, "text", score=1.5)
        with pytest.raises(ValueError, match="score must be"):
            evaluate(did, "text", score=-0.1)

    def test_result_contains_event_id(self):
        did = _design_id()
        result = evaluate(did, "text", score=0.8)
        assert "event_id" in result
        assert result["event_id"].startswith("cg-")

    def test_output_text_len_reported(self):
        did = _design_id()
        text = "Hello world"
        result = evaluate(did, text, score=0.75)
        assert result["output_text_len"] == len(text)

    def test_node_id_recorded(self):
        did = _design_id()
        result = evaluate(did, "text", score=0.8, node_id="n-gate-001")
        assert result["event_id"].startswith("cg-")  # event written successfully

    def test_detail_contains_score_and_threshold(self):
        did = _design_id()
        result = evaluate(did, "text", score=0.45, threshold=0.70)
        assert "0.45" in result["detail"]
        assert "0.70" in result["detail"]


# ---------------------------------------------------------------------------
# get_config() / update_config()
# ---------------------------------------------------------------------------

class TestConfig:
    def test_defaults_when_no_config(self):
        did = _design_id()
        cfg = get_config(did)
        assert cfg["threshold"] == DEFAULT_THRESHOLD
        assert cfg["low_confidence_action"] == "escalate"

    def test_update_and_retrieve(self):
        did = _design_id()
        update_config(did, threshold=0.85, low_confidence_action="block")
        cfg = get_config(did)
        assert cfg["threshold"] == 0.85
        assert cfg["low_confidence_action"] == "block"

    def test_update_is_idempotent(self):
        did = _design_id()
        update_config(did, 0.75)
        update_config(did, 0.80)
        cfg = get_config(did)
        assert cfg["threshold"] == 0.80

    def test_invalid_threshold_raises(self):
        with pytest.raises(ValueError):
            update_config(_design_id(), threshold=1.5)
        with pytest.raises(ValueError):
            update_config(_design_id(), threshold=-0.1)

    def test_invalid_action_raises(self):
        with pytest.raises(ValueError):
            update_config(_design_id(), threshold=0.7, low_confidence_action="ignore")


# ---------------------------------------------------------------------------
# evaluate_pipeline_node()
# ---------------------------------------------------------------------------

class TestEvaluatePipelineNode:
    def test_extracts_confidence_from_llm_output(self):
        did = _design_id()
        llm_out = {"text": "Research findings...", "confidence": 0.92}
        result = evaluate_pipeline_node(did, llm_out)
        assert result["decision"] == "proceed"
        assert result["score"] == 0.92

    def test_falls_back_to_05_when_no_confidence_key(self):
        did = _design_id()
        llm_out = {"text": "No confidence provided"}
        result = evaluate_pipeline_node(did, llm_out, threshold=0.6)
        # 0.5 < 0.6 → escalate
        assert result["decision"] == "escalate"
        assert result["score"] == 0.5

    def test_raw_llm_output_in_result(self):
        did = _design_id()
        llm_out = {"text": "Test", "confidence": 0.75}
        result = evaluate_pipeline_node(did, llm_out)
        assert result["raw_llm_output"] is llm_out

    def test_low_confidence_in_pipeline_escalates(self):
        did = _design_id()
        llm_out = {"text": "Uncertain", "confidence": 0.40}
        result = evaluate_pipeline_node(did, llm_out)
        assert result["allowed"] is False

    def test_uses_persisted_threshold(self):
        did = _design_id()
        update_config(did, threshold=0.95)
        llm_out = {"confidence": 0.80, "text": "text"}
        result = evaluate_pipeline_node(did, llm_out)
        # 0.80 < 0.95 → escalate
        assert result["decision"] == "escalate"


# ---------------------------------------------------------------------------
# check_confidence_gate_path()
# ---------------------------------------------------------------------------

class TestCheckConfidenceGatePath:
    def test_no_llm_nodes_returns_no_findings(self):
        nodes = [_node("inference-input"), _node("output-validator")]
        edges = []
        assert check_confidence_gate_path(nodes, edges) == []

    def test_llm_with_gate_before_validator_ok(self):
        llm = _node("llm", "Synthesis LLM")
        gate = _node("confidence-threshold", "Gate")
        validator = _node("output-validator", "Validator")
        nodes = [llm, gate, validator]
        edges = [
            _edge(llm["id"], gate["id"], "draft"),
            _edge(gate["id"], validator["id"], "if confident"),
        ]
        findings = check_confidence_gate_path(nodes, edges)
        assert findings == []

    def test_llm_without_gate_before_validator_produces_finding(self):
        llm = _node("llm", "Synthesis LLM")
        validator = _node("output-validator", "Validator")
        nodes = [llm, validator]
        edges = [_edge(llm["id"], validator["id"], "output")]
        findings = check_confidence_gate_path(nodes, edges)
        assert len(findings) == 1
        assert findings[0]["severity"] == "HIGH"
        assert "MEA-1" in findings[0]["category"]
        assert "confidence-threshold" in findings[0]["recommendation"]

    def test_llm_with_no_validator_produces_no_finding(self):
        # Gate check only fires when validator exists but gate is absent
        llm = _node("llm")
        audit = _node("audit-logger")
        nodes = [llm, audit]
        edges = [_edge(llm["id"], audit["id"])]
        findings = check_confidence_gate_path(nodes, edges)
        assert findings == []

    def test_finding_references_llm_label(self):
        llm = _node("llm", "My Research LLM")
        validator = _node("output-validator")
        nodes = [llm, validator]
        edges = [_edge(llm["id"], validator["id"])]
        findings = check_confidence_gate_path(nodes, edges)
        assert "My Research LLM" in findings[0]["detail"]

    def test_multiple_llms_one_missing_gate(self):
        llm1 = _node("llm", "LLM-A")
        llm2 = _node("llm", "LLM-B")
        gate = _node("confidence-threshold")
        validator = _node("output-validator")
        nodes = [llm1, llm2, gate, validator]
        edges = [
            _edge(llm1["id"], gate["id"]),
            _edge(gate["id"], validator["id"]),
            _edge(llm2["id"], validator["id"]),  # no gate
        ]
        findings = check_confidence_gate_path(nodes, edges)
        assert len(findings) == 1
        assert "LLM-B" in findings[0]["detail"]


# ---------------------------------------------------------------------------
# Integration — assess_design() picks up confidence gate findings
# ---------------------------------------------------------------------------

class TestAssessDesignIntegration:
    def test_llm_without_gate_adds_finding_to_assessment(self):
        from tools.agentic_ai_canvas.agentic_engine import assess_design

        llm = _node("llm", "Synthesis LLM")
        validator = _node("output-validator", "Validator")
        audit = _node("audit-logger")
        nodes = [llm, validator, audit]
        edges = [
            _edge(llm["id"], validator["id"]),
            _edge(validator["id"], audit["id"]),
        ]
        graph = {"nodes": nodes, "edges": edges}

        result = assess_design("test-design-001", graph, {"classification": "CUI"})
        findings = json.loads(result["findings_json"])
        gate_findings = [f for f in findings if "MEA-1" in f.get("category", "")
                         or "confidence-threshold" in f.get("recommendation", "")]
        assert len(gate_findings) >= 1

    def test_llm_with_gate_no_confidence_finding(self):
        from tools.agentic_ai_canvas.agentic_engine import assess_design

        inp = _node("inference-input")
        llm = _node("llm", "Synthesis LLM")
        gate = _node("confidence-threshold", "Confidence Gate")
        validator = _node("output-validator")
        audit = _node("audit-logger")
        nodes = [inp, llm, gate, validator, audit]
        edges = [
            _edge(inp["id"], llm["id"]),
            _edge(llm["id"], gate["id"], "draft"),
            _edge(gate["id"], validator["id"], "if confident"),
            _edge(validator["id"], audit["id"]),
        ]
        graph = {"nodes": nodes, "edges": edges}

        result = assess_design("test-design-002", graph, {"classification": "CUI"})
        findings = json.loads(result["findings_json"])
        # cg-missing findings should NOT be present
        cg_findings = [f for f in findings if f.get("id", "").startswith("cg-missing")]
        assert cg_findings == []
