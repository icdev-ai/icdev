# CUI // SP-CTI
"""Agentic AI Design Canvas — Agent Layer.

Converts canvas design nodes into agent configurations and manages the
agent execution lifecycle for the Agentic Research Pipeline pattern.

Supported agent types:
  researcher-agent   → ResearchAgent (wraps research pipeline stages)
  analyst-agent      → AnalystAgent  (synthesizes findings)
  writer-agent       → WriterAgent   (produces artifacts)
  reviewer-agent     → ReviewerAgent (validates output quality)
  orchestrator       → OrchestratorAgent (coordinates sub-agents)
  autonomous-agent   → AutonomousAgent
  semi-auto-agent    → SemiAutoAgent

All agents are deterministic configuration objects; LLM calls are deferred
to the execution engine and governed by the Governance Layer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from tools.agentic_ai_canvas.constants import AGENT_NODES

# ---------------------------------------------------------------------------
# Research Pipeline Stage definitions
# ---------------------------------------------------------------------------

RESEARCH_STAGES = [
    "SCOPE",
    "LANDSCAPE",
    "REGULATE",
    "COMMUNITY",
    "ACADEMIC",
    "BUILD_BUY",
    "SYNTHESIZE",
    "DOSSIER",
]

# Tool bindings available to researcher-agent nodes
RESEARCHER_DEFAULT_TOOLS = [
    "web-search",
    "mcp-server",
    "external-api",
    "doc-store",
    "knowledge-graph",
]

# ---------------------------------------------------------------------------
# Agent Configuration
# ---------------------------------------------------------------------------


@dataclass
class AgentConfig:
    """Executable configuration derived from a canvas agent node."""

    agent_id: str
    node_id: str
    node_type: str
    label: str
    role: str
    tools: list[str] = field(default_factory=list)
    memory_policy: str = "short-term"
    streaming: bool = False
    constraints: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_node(cls, node: dict) -> "AgentConfig":
        """Build an AgentConfig from a canvas node dict."""
        node_type = node.get("type", "")
        props = node.get("props", {}) or {}
        return cls(
            agent_id=str(uuid.uuid4()),
            node_id=node.get("id", str(uuid.uuid4())),
            node_type=node_type,
            label=node.get("label", node_type),
            role=_infer_role(node_type),
            tools=_infer_tools(node_type, props),
            memory_policy=props.get("memory_policy", _default_memory_policy(node_type)),
            streaming=bool(node.get("streaming", props.get("streaming", False))),
            constraints=_extract_constraints(props),
            metadata={
                "kanban_task_id": node.get("metadata", {}).get("kanban_task_id"),
                "classification": node.get("metadata", {}).get("classification", "CUI"),
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )


def _infer_role(node_type: str) -> str:
    roles = {
        "researcher-agent": "Conduct structured multi-stage research and produce findings",
        "analyst-agent": "Analyze data and synthesize insights from multiple sources",
        "writer-agent": "Produce structured artifacts from research findings",
        "reviewer-agent": "Validate output quality, accuracy, and compliance",
        "orchestrator": "Coordinate sub-agents and manage pipeline execution",
        "autonomous-agent": "Execute tasks autonomously within defined constraints",
        "semi-auto-agent": "Execute tasks with human-in-the-loop checkpoints",
        "sub-agent": "Execute delegated subtasks from an orchestrator",
        "swarm-agent": "Participate in parallel swarm execution",
        "fan-out-coordinator": "Distribute tasks across parallel agent instances",
        "fan-in-aggregator": "Merge and deduplicate results from parallel agents",
    }
    return roles.get(node_type, f"Execute {node_type} tasks")


def _infer_tools(node_type: str, props: dict) -> list[str]:
    if node_type == "researcher-agent":
        prop_tools = props.get("tools", [])
        return prop_tools if prop_tools else list(RESEARCHER_DEFAULT_TOOLS)
    tool_map = {
        "analyst-agent": ["doc-store", "knowledge-graph", "vector-db"],
        "writer-agent": ["doc-store", "output-validator"],
        "reviewer-agent": ["output-validator", "audit-logger"],
        "orchestrator": ["mcp-gateway", "tool-chain"],
        "autonomous-agent": ["tool-chain", "code-executor", "web-search"],
        "semi-auto-agent": ["tool-chain", "hitl-gate"],
    }
    return list(tool_map.get(node_type, []))


def _default_memory_policy(node_type: str) -> str:
    long_term = {"researcher-agent", "analyst-agent", "orchestrator"}
    return "long-term" if node_type in long_term else "short-term"


def _extract_constraints(props: dict) -> dict[str, Any]:
    keys = ("max_iterations", "timeout_seconds", "confidence_threshold",
            "rate_limit_rpm", "budget_tokens")
    return {k: props[k] for k in keys if k in props}


# ---------------------------------------------------------------------------
# Base Agent
# ---------------------------------------------------------------------------


class BaseAgent:
    """Base class for all AADC agent types."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    def describe(self) -> dict:
        return {
            "agent_id": self.config.agent_id,
            "node_id": self.config.node_id,
            "type": self.config.node_type,
            "label": self.config.label,
            "role": self.config.role,
            "tools": self.config.tools,
            "memory_policy": self.config.memory_policy,
            "streaming": self.config.streaming,
            "constraints": self.config.constraints,
        }

    def plan(self) -> list[str]:
        """Return execution steps for this agent (no LLM calls)."""
        return [f"[{self.config.label}] Execute {self.config.node_type} task"]


# ---------------------------------------------------------------------------
# Research Agent — Agentic Research Pipeline
# ---------------------------------------------------------------------------


class ResearchAgent(BaseAgent):
    """Agent implementing the Agentic Research Pipeline pattern.

    Maps to the `researcher-agent` canvas node type. Decomposes work into
    the 8-stage research pipeline: SCOPE → LANDSCAPE → … → DOSSIER.
    """

    def __init__(self, config: AgentConfig, topic: str = "", vertical: str = "") -> None:
        super().__init__(config)
        self.topic = topic or config.label
        self.vertical = vertical or "general"
        self._stages_completed: list[str] = []
        self._findings: dict[str, Any] = {}

    def plan(self) -> list[str]:
        return [f"[{self.config.label}] Stage {s}" for s in RESEARCH_STAGES]

    def get_pipeline_stages(self) -> list[str]:
        return list(RESEARCH_STAGES)

    def record_stage(self, stage: str, findings: dict) -> None:
        """Record findings for a completed pipeline stage."""
        if stage not in RESEARCH_STAGES:
            raise ValueError(f"Unknown stage '{stage}'. Valid: {RESEARCH_STAGES}")
        self._stages_completed.append(stage)
        self._findings[stage] = {
            "stage": stage,
            "findings": findings,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_findings(self) -> dict[str, Any]:
        return dict(self._findings)

    def is_complete(self) -> bool:
        return set(RESEARCH_STAGES).issubset(set(self._stages_completed))

    def describe(self) -> dict:
        base = super().describe()
        base.update({
            "topic": self.topic,
            "vertical": self.vertical,
            "pipeline_stages": RESEARCH_STAGES,
            "stages_completed": list(self._stages_completed),
            "is_complete": self.is_complete(),
        })
        return base


# ---------------------------------------------------------------------------
# Specialised agents
# ---------------------------------------------------------------------------


class AnalystAgent(BaseAgent):
    """Synthesizes findings from one or more ResearchAgents."""

    def plan(self) -> list[str]:
        return [
            f"[{self.config.label}] Collect findings from upstream agents",
            f"[{self.config.label}] Identify patterns and contradictions",
            f"[{self.config.label}] Produce synthesis report",
        ]


class WriterAgent(BaseAgent):
    """Produces structured artifacts (reports, briefs) from agent outputs."""

    def plan(self) -> list[str]:
        return [
            f"[{self.config.label}] Gather synthesized findings",
            f"[{self.config.label}] Apply template and style rules",
            f"[{self.config.label}] Produce output artifact",
            f"[{self.config.label}] Validate against output schema",
        ]


class ReviewerAgent(BaseAgent):
    """Validates quality, accuracy, and compliance of agent outputs."""

    def plan(self) -> list[str]:
        return [
            f"[{self.config.label}] Receive artifact for review",
            f"[{self.config.label}] Check factual accuracy",
            f"[{self.config.label}] Verify compliance markings",
            f"[{self.config.label}] Approve or return for revision",
        ]


class OrchestratorAgent(BaseAgent):
    """Coordinates sub-agent execution in the Agentic Research Pipeline."""

    def __init__(self, config: AgentConfig, sub_agents: list[BaseAgent] | None = None) -> None:
        super().__init__(config)
        self.sub_agents: list[BaseAgent] = sub_agents or []

    def register_sub_agent(self, agent: BaseAgent) -> None:
        self.sub_agents.append(agent)

    def plan(self) -> list[str]:
        steps = [f"[{self.config.label}] Initialize pipeline"]
        for sa in self.sub_agents:
            steps.extend(sa.plan())
        steps.append(f"[{self.config.label}] Aggregate results")
        return steps

    def describe(self) -> dict:
        base = super().describe()
        base["sub_agents"] = [sa.describe() for sa in self.sub_agents]
        return base


# ---------------------------------------------------------------------------
# Agent Layer — factory that converts a design graph into agents
# ---------------------------------------------------------------------------

_AGENT_CLASS_MAP: dict[str, type[BaseAgent]] = {
    "researcher-agent": ResearchAgent,
    "analyst-agent": AnalystAgent,
    "writer-agent": WriterAgent,
    "reviewer-agent": ReviewerAgent,
    "orchestrator": OrchestratorAgent,
}


class AgentLayer:
    """Converts an AADC design graph into instantiated agents.

    Usage:
        layer = AgentLayer(design_graph)
        agents = layer.build_agents()
        plan = layer.build_pipeline_plan()
    """

    def __init__(self, design_graph: dict) -> None:
        self.nodes: list[dict] = design_graph.get("nodes", [])
        self.edges: list[dict] = design_graph.get("edges", [])
        self._agents: list[BaseAgent] = []

    def _agent_nodes(self) -> list[dict]:
        return [n for n in self.nodes if n.get("type") in AGENT_NODES]

    def build_agents(self) -> list[BaseAgent]:
        """Instantiate agents for every agent-type node in the design."""
        self._agents = []
        for node in self._agent_nodes():
            config = AgentConfig.from_node(node)
            node_type = node.get("type", "")
            cls = _AGENT_CLASS_MAP.get(node_type, BaseAgent)
            agent = cls(config)
            self._agents.append(agent)
        return list(self._agents)

    def get_research_agents(self) -> list[ResearchAgent]:
        return [a for a in self._agents if isinstance(a, ResearchAgent)]

    def build_pipeline_plan(self) -> list[str]:
        """Return an ordered list of execution steps across all agents."""
        if not self._agents:
            self.build_agents()
        steps: list[str] = []
        for agent in self._agents:
            steps.extend(agent.plan())
        return steps

    def describe(self) -> dict:
        if not self._agents:
            self.build_agents()
        return {
            "total_agents": len(self._agents),
            "agent_types": [a.config.node_type for a in self._agents],
            "agents": [a.describe() for a in self._agents],
            "pipeline_steps": self.build_pipeline_plan(),
        }

    @staticmethod
    def from_design_json(design_json: str | dict) -> "AgentLayer":
        import json as _json
        if isinstance(design_json, str):
            design_json = _json.loads(design_json)
        graph = design_json.get("graph", design_json)
        return AgentLayer(graph)
