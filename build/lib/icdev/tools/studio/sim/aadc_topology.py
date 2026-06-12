# CUI // SP-CTI
"""AADC topology builder — agentic AI workflow nodes (agents, LLM endpoints, orchestrators).

Artifact parsing order:
  1. agents.yaml / agent_config.yaml  — agent roles and tool definitions
  2. workflow.json / workflow.yaml     — agent graph / DAG
  3. tools.yaml                        — registered tools per agent
  4. Default — 4-agent ICDEV canvas executor topology
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from tools.studio.sim.base_topology import (
    BaseTopologyBuilder, DockerServiceSpec, LinkSpec, NodeSpec, ProbeSpec, TopologySpec,
)

_AGENT_NODE_TYPE = "vpcs"
_TOOL_NAMES = ["bash", "code", "search", "memory", "read", "write", "test"]


def _parse_agents_yaml(path: Path) -> Optional[list[dict]]:
    """Parse agents.yaml for agent definitions."""
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data[:8]
        if isinstance(data, dict):
            agents = data.get("agents") or data.get("workers") or []
            return agents[:8]
    except Exception:
        pass
    return None


def _parse_workflow_graph(path: Path) -> Optional[list[dict]]:
    """Parse workflow.json for agent graph (edges define connections)."""
    try:
        if path.suffix in (".yaml", ".yml"):
            import yaml
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        edges = data.get("edges") or data.get("connections") or []
        return edges[:16] if edges else None
    except Exception:
        return None


def _build_agent_nodes(agents: list[dict]) -> list[NodeSpec]:
    nodes: list[NodeSpec] = []
    seen: set[str] = set()
    for a in agents:
        name = a.get("name") or a.get("id") or f"agent-{len(nodes)}"
        role = a.get("role") or a.get("type") or "agent"
        tools = a.get("tools") or []
        slug = name.replace(" ", "-").lower()[:20]
        if slug not in seen:
            nodes.append(NodeSpec(
                name=slug,
                node_type=_AGENT_NODE_TYPE,
                meta={"role": role, "tools": tools},
            ))
            seen.add(slug)
    return nodes


class AADCTopologyBuilder(BaseTopologyBuilder):
    canvas = "aadc"

    def build(self, artifacts_dir: Path, run_id: str = "") -> TopologySpec:
        agent_defs: Optional[list] = None
        workflow_edges: Optional[list] = None

        for fname in ("agents.yaml", "agents.yml", "agent_config.yaml"):
            p = artifacts_dir / fname
            if p.exists():
                agent_defs = _parse_agents_yaml(p)
                if agent_defs:
                    break

        for fname in ("workflow.json", "workflow.yaml", "workflow.yml"):
            p = artifacts_dir / fname
            if p.exists():
                workflow_edges = _parse_workflow_graph(p)
                if workflow_edges:
                    break

        if agent_defs:
            agent_nodes = _build_agent_nodes(agent_defs)
            source = "artifact"
        else:
            agent_nodes = [
                NodeSpec(name="agent-planner",  node_type="vpcs", meta={"role": "planner-agent"}),
                NodeSpec(name="agent-coder",    node_type="vpcs", meta={"role": "coder-agent"}),
                NodeSpec(name="agent-tester",   node_type="vpcs", meta={"role": "tester-agent"}),
                NodeSpec(name="agent-reviewer", node_type="vpcs", meta={"role": "reviewer-agent"}),
            ]
            source = "default"

        nodes = [
            NodeSpec(name="orchestrator", node_type="vpcs",            meta={"role": "orchestrator", "tool": "icdev-studio"}),
            NodeSpec(name="llm-gateway",  node_type="vpcs",            meta={"role": "llm-router",   "tool": "ollama"}),
        ] + agent_nodes + [
            NodeSpec(name="tool-sandbox", node_type="ethernet_switch", meta={"role": "tool-executor-sandbox"}),
            NodeSpec(name="memory-store", node_type="vpcs",            meta={"role": "vector-memory", "tool": "chroma"}),
            NodeSpec(name="agent-fabric", node_type="ethernet_switch", meta={"role": "agent-bus"}),
        ]

        # Use workflow edges if available, else star topology
        links: list[LinkSpec] = []
        if workflow_edges:
            node_names = {n.name for n in nodes}
            for edge in workflow_edges:
                src = str(edge.get("from") or edge.get("source") or "")
                dst = str(edge.get("to")   or edge.get("target") or "")
                if src in node_names and dst in node_names:
                    links.append(LinkSpec(src, dst))
            # If no edges matched, fall back to star
            if not links:
                workflow_edges = None

        if not links:
            for node in nodes:
                if node.name not in ("agent-fabric", "tool-sandbox"):
                    links.append(LinkSpec(node.name, "agent-fabric"))
            links.append(LinkSpec("tool-sandbox", "agent-fabric"))

        return TopologySpec(
            project_name="icdev-aadc-sim",
            canvas="aadc",
            nodes=nodes,
            links=links,
            probes=[
                ProbeSpec(name="topology_deployed", type="topology_deployed"),
                ProbeSpec(name="link_count", type="link_count", expected_value=len(links)),
                ProbeSpec(name="llm_gateway_up", type="http_get",
                          target_node="llm-gateway", port=11434, path="/api/tags"),
                ProbeSpec(name="chroma_up", type="http_get",
                          target_node="memory-store", port=8000, path="/api/v1/heartbeat"),
            ],
            docker_services=[
                DockerServiceSpec(
                    name="icdev-ollama-aadc",
                    image="ollama/ollama:latest",
                    ports={11434: 11434},
                    healthcheck_url="http://localhost:11434/api/tags",
                ),
                DockerServiceSpec(
                    name="icdev-chroma-aadc",
                    image="chromadb/chroma:latest",
                    ports={8000: 8000},
                    healthcheck_url="http://localhost:8000/api/v1/heartbeat",
                ),
            ],
            metadata={
                "agent_count": len(agent_nodes),
                "tools": ["ollama", "chroma", "icdev-studio"],
                "source": source,
                "workflow_edges": len(workflow_edges) if workflow_edges else 0,
            },
        )
