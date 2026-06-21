# CUI // SP-CTI
"""Tests for memory_layer.py — Agentic Research Pipeline memory layer checks."""

from tools.agentic_ai_canvas.memory_layer import check_memory_layer, get_memory_layer_map


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _node(nid, ntype, label=None):
    return {"id": nid, "type": ntype, "label": label or nid}


def _edge(src, tgt):
    return {"source": src, "target": tgt}


# ---------------------------------------------------------------------------
# No memory nodes — should return ([], None)
# ---------------------------------------------------------------------------

def test_no_memory_nodes_returns_none_score():
    nodes = [_node("llm1", "llm"), _node("in1", "inference-input")]
    edges = [_edge("in1", "llm1")]
    findings, score = check_memory_layer(nodes, edges)
    assert findings == []
    assert score is None


# ---------------------------------------------------------------------------
# mem-1: vector-db requires embedding-model
# ---------------------------------------------------------------------------

def test_vector_db_without_embedding_model_fails():
    nodes = [
        _node("chunker1", "chunker"),
        _node("vdb1", "vector-db"),
        _node("audit1", "audit-logger"),
        _node("dv1", "data-validator"),
    ]
    edges = [
        _edge("chunker1", "vdb1"),
        _edge("dv1", "chunker1"),
        _edge("vdb1", "audit1"),
    ]
    findings, score = check_memory_layer(nodes, edges)
    assert any(f["id"] == "mem-mem-1" for f in findings)
    assert score < 100


def test_vector_db_with_embedding_model_passes_mem1():
    nodes = [
        _node("dv1", "data-validator"),
        _node("chunker1", "chunker"),
        _node("emb1", "embedding-model"),
        _node("vdb1", "vector-db"),
        _node("audit1", "audit-logger"),
    ]
    edges = [
        _edge("dv1", "chunker1"),
        _edge("chunker1", "emb1"),
        _edge("emb1", "vdb1"),
        _edge("vdb1", "audit1"),
    ]
    findings, score = check_memory_layer(nodes, edges)
    assert not any(f["id"] == "mem-mem-1" for f in findings)


# ---------------------------------------------------------------------------
# mem-2: vector-db requires chunker upstream
# ---------------------------------------------------------------------------

def test_vector_db_without_chunker_fails():
    nodes = [
        _node("emb1", "embedding-model"),
        _node("dv1", "data-validator"),
        _node("vdb1", "vector-db"),
        _node("audit1", "audit-logger"),
    ]
    edges = [
        _edge("dv1", "emb1"),
        _edge("emb1", "vdb1"),
        _edge("vdb1", "audit1"),
    ]
    findings, score = check_memory_layer(nodes, edges)
    assert any(f["id"] == "mem-mem-2" for f in findings)


def test_vector_db_with_chunker_upstream_passes_mem2():
    nodes = [
        _node("dv1", "data-validator"),
        _node("chunker1", "chunker"),
        _node("emb1", "embedding-model"),
        _node("vdb1", "vector-db"),
        _node("audit1", "audit-logger"),
    ]
    edges = [
        _edge("dv1", "chunker1"),
        _edge("chunker1", "emb1"),
        _edge("emb1", "vdb1"),
        _edge("vdb1", "audit1"),
    ]
    findings, score = check_memory_layer(nodes, edges)
    assert not any(f["id"] == "mem-mem-2" for f in findings)


# ---------------------------------------------------------------------------
# mem-3: persistent memory requires audit-logger downstream
# ---------------------------------------------------------------------------

def test_long_term_mem_without_audit_fails():
    nodes = [
        _node("ltm1", "long-term-mem"),
    ]
    edges = []
    findings, score = check_memory_layer(nodes, edges)
    assert any(f["id"] == "mem-mem-3" for f in findings)


def test_vector_db_with_audit_downstream_passes_mem3():
    nodes = [
        _node("dv1", "data-validator"),
        _node("chunker1", "chunker"),
        _node("emb1", "embedding-model"),
        _node("vdb1", "vector-db"),
        _node("audit1", "audit-logger"),
    ]
    edges = [
        _edge("dv1", "chunker1"),
        _edge("chunker1", "emb1"),
        _edge("emb1", "vdb1"),
        _edge("vdb1", "audit1"),
    ]
    findings, score = check_memory_layer(nodes, edges)
    assert not any(f["id"] == "mem-mem-3" for f in findings)


# ---------------------------------------------------------------------------
# mem-4: vector-db requires data-validator upstream (RAG poisoning prevention)
# ---------------------------------------------------------------------------

def test_vector_db_without_data_validator_fails():
    nodes = [
        _node("chunker1", "chunker"),
        _node("emb1", "embedding-model"),
        _node("vdb1", "vector-db"),
        _node("audit1", "audit-logger"),
    ]
    edges = [
        _edge("chunker1", "emb1"),
        _edge("emb1", "vdb1"),
        _edge("vdb1", "audit1"),
    ]
    findings, score = check_memory_layer(nodes, edges)
    assert any(f["id"] == "mem-mem-4" for f in findings)


def test_fully_compliant_research_pipeline_scores_100():
    """Agentic Research Pipeline with all memory checks satisfied scores 100."""
    nodes = [
        _node("dv1", "data-validator"),
        _node("chunker1", "chunker"),
        _node("emb1", "embedding-model"),
        _node("vdb1", "vector-db"),
        _node("audit1", "audit-logger"),
    ]
    edges = [
        _edge("dv1", "chunker1"),
        _edge("chunker1", "emb1"),
        _edge("emb1", "vdb1"),
        _edge("vdb1", "audit1"),
    ]
    findings, score = check_memory_layer(nodes, edges)
    assert findings == []
    assert score == 100.0


# ---------------------------------------------------------------------------
# get_memory_layer_map
# ---------------------------------------------------------------------------

def test_get_memory_layer_map_returns_coverage():
    nodes = [
        _node("dv1", "data-validator"),
        _node("chunker1", "chunker"),
        _node("emb1", "embedding-model"),
        _node("vdb1", "vector-db", "My VectorDB"),
        _node("audit1", "audit-logger"),
    ]
    edges = [
        _edge("dv1", "chunker1"),
        _edge("chunker1", "emb1"),
        _edge("emb1", "vdb1"),
        _edge("vdb1", "audit1"),
    ]
    cmap = get_memory_layer_map(nodes, edges)
    vdb_entry = next(e for e in cmap if e["node_type"] == "vector-db")
    assert vdb_entry["node_label"] == "My VectorDB"
    assert vdb_entry["has_embedding_model"] is True
    assert vdb_entry["has_chunker_upstream"] is True
    assert vdb_entry["has_audit_downstream"] is True
    assert vdb_entry["has_validator_upstream"] is True


def test_get_memory_layer_map_empty_when_no_memory():
    nodes = [_node("llm1", "llm")]
    edges = []
    result = get_memory_layer_map(nodes, edges)
    assert result == []


# ---------------------------------------------------------------------------
# Integration: assess_design includes mem_score
# ---------------------------------------------------------------------------

def test_assess_design_includes_mem_score():
    import json
    from tools.agentic_ai_canvas.agentic_engine import assess_design

    graph = {
        "nodes": [
            _node("in1", "inference-input"),
            _node("san1", "input-sanitizer"),
            _node("llm1", "llm"),
            _node("dv1", "data-validator"),
            _node("chunker1", "chunker"),
            _node("emb1", "embedding-model"),
            _node("vdb1", "vector-db"),
            _node("audit1", "audit-logger"),
            _node("hitl1", "hitl-gate"),
            _node("cb1", "circuit-breaker"),
            _node("ct1", "confidence-threshold"),
            _node("ov1", "output-validator"),
        ],
        "edges": [
            _edge("in1", "san1"),
            _edge("san1", "llm1"),
            _edge("llm1", "ov1"),
            _edge("dv1", "chunker1"),
            _edge("chunker1", "emb1"),
            _edge("emb1", "vdb1"),
            _edge("vdb1", "audit1"),
            _edge("audit1", "hitl1"),
        ],
    }
    result = assess_design("test-design-id", json.dumps(graph), {"classification": "CUI"})
    assert "mem_score" in result
    assert result["mem_score"] == 100.0
