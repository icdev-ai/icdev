# CUI // SP-CTI
"""Tests for agentic_ai_canvas.agent_layer — Agent Layer (Agentic Research Pipeline)."""

from __future__ import annotations

import pytest

from tools.agentic_ai_canvas.agent_layer import (
    RESEARCH_STAGES,
    RESEARCHER_DEFAULT_TOOLS,
    AgentConfig,
    AgentLayer,
    AnalystAgent,
    BaseAgent,
    OrchestratorAgent,
    ResearchAgent,
    ReviewerAgent,
    WriterAgent,
    _infer_role,
    _infer_tools,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

RESEARCHER_NODE = {
    "id": "node-ra-1",
    "type": "researcher-agent",
    "label": "Market Research Agent",
    "props": {"memory_policy": "long-term", "max_iterations": 5},
    "metadata": {"classification": "CUI"},
}

ANALYST_NODE = {
    "id": "node-an-1",
    "type": "analyst-agent",
    "label": "Synthesis Analyst",
    "props": {},
}

WRITER_NODE = {
    "id": "node-wr-1",
    "type": "writer-agent",
    "label": "Report Writer",
    "props": {"streaming": True},
}

REVIEWER_NODE = {
    "id": "node-rv-1",
    "type": "reviewer-agent",
    "label": "Quality Reviewer",
    "props": {},
}

ORCHESTRATOR_NODE = {
    "id": "node-oc-1",
    "type": "orchestrator",
    "label": "Pipeline Orchestrator",
    "props": {},
}

MINIMAL_GRAPH = {
    "nodes": [RESEARCHER_NODE],
    "edges": [],
}

FULL_PIPELINE_GRAPH = {
    "nodes": [
        ORCHESTRATOR_NODE,
        RESEARCHER_NODE,
        ANALYST_NODE,
        WRITER_NODE,
        REVIEWER_NODE,
    ],
    "edges": [
        {"source": "node-oc-1", "target": "node-ra-1"},
        {"source": "node-ra-1", "target": "node-an-1"},
        {"source": "node-an-1", "target": "node-wr-1"},
        {"source": "node-wr-1", "target": "node-rv-1"},
    ],
}


# ---------------------------------------------------------------------------
# AgentConfig tests
# ---------------------------------------------------------------------------


def test_agent_config_from_researcher_node():
    cfg = AgentConfig.from_node(RESEARCHER_NODE)
    assert cfg.node_id == "node-ra-1"
    assert cfg.node_type == "researcher-agent"
    assert cfg.label == "Market Research Agent"
    assert cfg.memory_policy == "long-term"
    assert cfg.constraints == {"max_iterations": 5}
    assert cfg.metadata["classification"] == "CUI"


def test_agent_config_default_memory_policy():
    node = {"id": "x", "type": "writer-agent", "label": "W", "props": {}}
    cfg = AgentConfig.from_node(node)
    assert cfg.memory_policy == "short-term"


def test_agent_config_researcher_gets_long_term_memory_default():
    node = {"id": "x", "type": "researcher-agent", "label": "R", "props": {}}
    cfg = AgentConfig.from_node(node)
    assert cfg.memory_policy == "long-term"


def test_agent_config_streaming_from_node():
    node = {"id": "x", "type": "writer-agent", "label": "W",
            "streaming": True, "props": {}}
    cfg = AgentConfig.from_node(node)
    assert cfg.streaming is True


def test_agent_config_streaming_from_props():
    node = {"id": "x", "type": "writer-agent", "label": "W",
            "props": {"streaming": True}}
    cfg = AgentConfig.from_node(node)
    assert cfg.streaming is True


def test_agent_config_tools_from_props():
    node = {"id": "x", "type": "researcher-agent", "label": "R",
            "props": {"tools": ["custom-tool", "web-search"]}}
    cfg = AgentConfig.from_node(node)
    assert "custom-tool" in cfg.tools
    assert "web-search" in cfg.tools


def test_agent_config_unknown_type():
    node = {"id": "x", "type": "unknown-agent", "label": "U", "props": {}}
    cfg = AgentConfig.from_node(node)
    assert cfg.node_type == "unknown-agent"
    assert cfg.tools == []


def test_agent_config_assigns_unique_agent_id():
    cfg1 = AgentConfig.from_node(RESEARCHER_NODE)
    cfg2 = AgentConfig.from_node(RESEARCHER_NODE)
    assert cfg1.agent_id != cfg2.agent_id


# ---------------------------------------------------------------------------
# _infer_role and _infer_tools
# ---------------------------------------------------------------------------


def test_infer_role_researcher():
    role = _infer_role("researcher-agent")
    assert "research" in role.lower()


def test_infer_role_analyst():
    role = _infer_role("analyst-agent")
    assert "analy" in role.lower() or "synthe" in role.lower()


def test_infer_role_unknown():
    role = _infer_role("exotic-agent")
    assert "exotic-agent" in role


def test_infer_tools_researcher_defaults():
    tools = _infer_tools("researcher-agent", {})
    assert set(tools) == set(RESEARCHER_DEFAULT_TOOLS)


def test_infer_tools_reviewer():
    tools = _infer_tools("reviewer-agent", {})
    assert "output-validator" in tools
    assert "audit-logger" in tools


# ---------------------------------------------------------------------------
# ResearchAgent
# ---------------------------------------------------------------------------


def test_research_agent_pipeline_stages():
    cfg = AgentConfig.from_node(RESEARCHER_NODE)
    agent = ResearchAgent(cfg, topic="AI Trends", vertical="defense")
    assert agent.get_pipeline_stages() == RESEARCH_STAGES


def test_research_agent_plan_contains_all_stages():
    cfg = AgentConfig.from_node(RESEARCHER_NODE)
    agent = ResearchAgent(cfg)
    plan = agent.plan()
    assert len(plan) == len(RESEARCH_STAGES)
    for stage in RESEARCH_STAGES:
        assert any(stage in step for step in plan)


def test_research_agent_record_stage():
    cfg = AgentConfig.from_node(RESEARCHER_NODE)
    agent = ResearchAgent(cfg)
    agent.record_stage("SCOPE", {"query": "AI trends", "sources": 5})
    findings = agent.get_findings()
    assert "SCOPE" in findings
    assert findings["SCOPE"]["findings"]["query"] == "AI trends"


def test_research_agent_invalid_stage_raises():
    cfg = AgentConfig.from_node(RESEARCHER_NODE)
    agent = ResearchAgent(cfg)
    with pytest.raises(ValueError, match="Unknown stage"):
        agent.record_stage("INVALID_STAGE", {})


def test_research_agent_is_complete_false_initially():
    cfg = AgentConfig.from_node(RESEARCHER_NODE)
    agent = ResearchAgent(cfg)
    assert agent.is_complete() is False


def test_research_agent_is_complete_after_all_stages():
    cfg = AgentConfig.from_node(RESEARCHER_NODE)
    agent = ResearchAgent(cfg)
    for stage in RESEARCH_STAGES:
        agent.record_stage(stage, {"data": stage})
    assert agent.is_complete() is True


def test_research_agent_describe_includes_pipeline_info():
    cfg = AgentConfig.from_node(RESEARCHER_NODE)
    agent = ResearchAgent(cfg, topic="Defense AI", vertical="dod")
    desc = agent.describe()
    assert desc["topic"] == "Defense AI"
    assert desc["vertical"] == "dod"
    assert desc["pipeline_stages"] == RESEARCH_STAGES
    assert desc["is_complete"] is False


def test_research_agent_topic_defaults_to_label():
    cfg = AgentConfig.from_node(RESEARCHER_NODE)
    agent = ResearchAgent(cfg)
    assert agent.topic == cfg.label


# ---------------------------------------------------------------------------
# Specialised agents
# ---------------------------------------------------------------------------


def test_analyst_agent_plan():
    cfg = AgentConfig.from_node(ANALYST_NODE)
    agent = AnalystAgent(cfg)
    plan = agent.plan()
    assert len(plan) >= 2
    assert any("synthe" in s.lower() or "pattern" in s.lower() for s in plan)


def test_writer_agent_plan():
    cfg = AgentConfig.from_node(WRITER_NODE)
    agent = WriterAgent(cfg)
    plan = agent.plan()
    assert len(plan) >= 3
    assert any("artifact" in s.lower() or "template" in s.lower() for s in plan)


def test_reviewer_agent_plan():
    cfg = AgentConfig.from_node(REVIEWER_NODE)
    agent = ReviewerAgent(cfg)
    plan = agent.plan()
    assert any("review" in s.lower() or "accur" in s.lower() for s in plan)


def test_orchestrator_coordinates_sub_agents():
    oc_cfg = AgentConfig.from_node(ORCHESTRATOR_NODE)
    ra_cfg = AgentConfig.from_node(RESEARCHER_NODE)
    orchestrator = OrchestratorAgent(oc_cfg)
    researcher = ResearchAgent(ra_cfg)
    orchestrator.register_sub_agent(researcher)
    plan = orchestrator.plan()
    assert any("SCOPE" in step for step in plan)
    assert any("Pipeline" in step or "Orchestrat" in step for step in plan)


def test_orchestrator_describe_lists_sub_agents():
    oc_cfg = AgentConfig.from_node(ORCHESTRATOR_NODE)
    ra_cfg = AgentConfig.from_node(RESEARCHER_NODE)
    orchestrator = OrchestratorAgent(oc_cfg, sub_agents=[ResearchAgent(ra_cfg)])
    desc = orchestrator.describe()
    assert len(desc["sub_agents"]) == 1
    assert desc["sub_agents"][0]["type"] == "researcher-agent"


# ---------------------------------------------------------------------------
# AgentLayer
# ---------------------------------------------------------------------------


def test_agent_layer_builds_agents_from_graph():
    layer = AgentLayer(MINIMAL_GRAPH)
    agents = layer.build_agents()
    assert len(agents) == 1
    assert isinstance(agents[0], ResearchAgent)


def test_agent_layer_full_pipeline():
    layer = AgentLayer(FULL_PIPELINE_GRAPH)
    agents = layer.build_agents()
    types = {a.config.node_type for a in agents}
    assert "researcher-agent" in types
    assert "analyst-agent" in types
    assert "writer-agent" in types
    assert "reviewer-agent" in types
    assert "orchestrator" in types


def test_agent_layer_get_research_agents():
    layer = AgentLayer(FULL_PIPELINE_GRAPH)
    layer.build_agents()
    researchers = layer.get_research_agents()
    assert len(researchers) == 1
    assert researchers[0].config.label == "Market Research Agent"


def test_agent_layer_build_pipeline_plan():
    layer = AgentLayer(MINIMAL_GRAPH)
    plan = layer.build_pipeline_plan()
    assert len(plan) == len(RESEARCH_STAGES)


def test_agent_layer_describe():
    layer = AgentLayer(MINIMAL_GRAPH)
    desc = layer.describe()
    assert desc["total_agents"] == 1
    assert "researcher-agent" in desc["agent_types"]
    assert len(desc["pipeline_steps"]) > 0


def test_agent_layer_empty_graph():
    layer = AgentLayer({"nodes": [], "edges": []})
    agents = layer.build_agents()
    assert agents == []
    assert layer.build_pipeline_plan() == []


def test_agent_layer_skips_non_agent_nodes():
    graph = {
        "nodes": [
            {"id": "llm-1", "type": "llm", "label": "Claude"},
            {"id": "ra-1", "type": "researcher-agent", "label": "R"},
            {"id": "mem-1", "type": "vector-db", "label": "VDB"},
        ],
        "edges": [],
    }
    layer = AgentLayer(graph)
    agents = layer.build_agents()
    assert len(agents) == 1
    assert agents[0].config.node_type == "researcher-agent"


def test_agent_layer_from_design_json():
    import json
    design = {"graph": MINIMAL_GRAPH}
    layer = AgentLayer.from_design_json(json.dumps(design))
    agents = layer.build_agents()
    assert len(agents) == 1


def test_agent_layer_from_design_json_dict():
    design = {"graph": MINIMAL_GRAPH}
    layer = AgentLayer.from_design_json(design)
    agents = layer.build_agents()
    assert len(agents) == 1


def test_agent_layer_from_flat_graph():
    layer = AgentLayer.from_design_json(MINIMAL_GRAPH)
    agents = layer.build_agents()
    assert len(agents) == 1


def test_base_agent_describe():
    node = {"id": "x", "type": "sub-agent", "label": "SubA", "props": {}}
    cfg = AgentConfig.from_node(node)
    agent = BaseAgent(cfg)
    desc = agent.describe()
    assert desc["type"] == "sub-agent"
    assert desc["label"] == "SubA"


def test_base_agent_plan():
    node = {"id": "x", "type": "sub-agent", "label": "SubA", "props": {}}
    cfg = AgentConfig.from_node(node)
    agent = BaseAgent(cfg)
    plan = agent.plan()
    assert len(plan) == 1


def test_research_agent_multiple_stage_recordings():
    cfg = AgentConfig.from_node(RESEARCHER_NODE)
    agent = ResearchAgent(cfg)
    agent.record_stage("SCOPE", {"q": "test"})
    agent.record_stage("LANDSCAPE", {"competitors": 12})
    findings = agent.get_findings()
    assert len(findings) == 2
    assert findings["LANDSCAPE"]["findings"]["competitors"] == 12
