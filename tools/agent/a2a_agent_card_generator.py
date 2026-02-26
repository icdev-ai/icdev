#!/usr/bin/env python3
# CUI // SP-CTI
"""A2A v0.3 Agent Card Generator.

Generates v0.3-compliant Agent Cards from agent_config.yaml for all 15 agents.
Adds capabilities metadata, contextId, and tasks/sendSubscribe support per D344.

Architecture Decisions:
  D344: A2A v0.3 adds `capabilities` to Agent Card and `tasks/sendSubscribe`
        for streaming. Backward compatible — checks `protocolVersion` field.

Usage:
  python tools/agent/a2a_agent_card_generator.py --all --json
  python tools/agent/a2a_agent_card_generator.py --agent-id orchestrator --json
  python tools/agent/a2a_agent_card_generator.py --list --json
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent.parent
AGENT_CONFIG_PATH = BASE_DIR / "args" / "agent_config.yaml"

# A2A protocol version
A2A_PROTOCOL_VERSION = "0.3"

# Default capabilities for all agents
DEFAULT_CAPABILITIES = {
    "streaming": False,
    "pushNotifications": False,
    "taskSubscription": True,
    "contextPreservation": True,
    "asyncNotifications": True,
    "stateTransitionHistory": True,
}

# Skill definitions per agent type
AGENT_SKILLS = {
    "orchestrator": [
        {"id": "task_dispatch", "name": "Task Dispatch", "description": "Route tasks to appropriate agents"},
        {"id": "workflow_manage", "name": "Workflow Management", "description": "Manage multi-agent workflows"},
        {"id": "agent_status", "name": "Agent Status", "description": "Query agent health and availability"},
    ],
    "architect": [
        {"id": "system_design", "name": "System Design", "description": "ATLAS/M-ATLAS architecture design"},
        {"id": "blueprint_generate", "name": "Blueprint Generation", "description": "Generate application blueprints"},
    ],
    "builder": [
        {"id": "scaffold", "name": "Scaffold", "description": "Generate project scaffolding"},
        {"id": "generate_code", "name": "Code Generation", "description": "TDD code generation (RED→GREEN→REFACTOR)"},
        {"id": "write_tests", "name": "Test Writing", "description": "Write unit and BDD tests"},
        {"id": "run_tests", "name": "Test Execution", "description": "Execute test suites"},
    ],
    "compliance": [
        {"id": "ssp_generate", "name": "SSP Generation", "description": "Generate System Security Plans"},
        {"id": "poam_generate", "name": "POAM Generation", "description": "Generate Plans of Action and Milestones"},
        {"id": "sbom_generate", "name": "SBOM Generation", "description": "Generate Software Bills of Materials"},
        {"id": "crosswalk_query", "name": "Crosswalk Query", "description": "Cross-framework control mapping"},
    ],
    "security": [
        {"id": "sast_scan", "name": "SAST Scan", "description": "Static Application Security Testing"},
        {"id": "dependency_audit", "name": "Dependency Audit", "description": "CVE vulnerability scanning"},
        {"id": "secret_detection", "name": "Secret Detection", "description": "Detect hardcoded secrets"},
    ],
    "infrastructure": [
        {"id": "terraform_plan", "name": "Terraform Plan", "description": "Generate Terraform IaC"},
        {"id": "k8s_deploy", "name": "K8s Deploy", "description": "Generate Kubernetes manifests"},
        {"id": "pipeline_generate", "name": "Pipeline Generation", "description": "Generate CI/CD pipelines"},
    ],
    "knowledge": [
        {"id": "search_knowledge", "name": "Knowledge Search", "description": "Search learning knowledge base"},
        {"id": "self_heal", "name": "Self-Heal", "description": "Analyze failures and suggest fixes"},
    ],
    "monitor": [
        {"id": "log_analyze", "name": "Log Analysis", "description": "Analyze application logs"},
        {"id": "health_check", "name": "Health Check", "description": "System health monitoring"},
    ],
    "mbse": [
        {"id": "import_xmi", "name": "XMI Import", "description": "Import SysML XMI models"},
        {"id": "digital_thread", "name": "Digital Thread", "description": "Build digital thread traceability"},
    ],
    "modernization": [
        {"id": "analyze_legacy", "name": "Legacy Analysis", "description": "Analyze legacy applications"},
        {"id": "assess_seven_r", "name": "7R Assessment", "description": "Seven R migration assessment"},
    ],
    "requirements_analyst": [
        {"id": "intake_session", "name": "Requirements Intake", "description": "AI-driven conversational intake"},
        {"id": "gap_detection", "name": "Gap Detection", "description": "Detect requirement gaps"},
        {"id": "readiness_scoring", "name": "Readiness Scoring", "description": "Score requirement readiness"},
    ],
    "supply_chain": [
        {"id": "dependency_graph", "name": "Dependency Graph", "description": "Build supply chain dependency graph"},
        {"id": "scrm_assess", "name": "SCRM Assessment", "description": "Supply chain risk management"},
    ],
    "simulation": [
        {"id": "run_simulation", "name": "Simulation", "description": "Digital Program Twin simulation"},
        {"id": "monte_carlo", "name": "Monte Carlo", "description": "Monte Carlo risk estimation"},
    ],
    "devsecops_zta": [
        {"id": "zta_assess", "name": "ZTA Assessment", "description": "Zero Trust Architecture maturity"},
        {"id": "policy_generate", "name": "Policy Generation", "description": "Policy-as-code generation"},
    ],
    "gateway": [
        {"id": "bind_user", "name": "User Binding", "description": "Bind remote user to ICDEV identity"},
        {"id": "send_command", "name": "Send Command", "description": "Execute remote command"},
    ],
}


def _load_agent_config() -> dict:
    """Load agent configuration from YAML."""
    if not AGENT_CONFIG_PATH.exists():
        return {"agents": {}}
    with open(AGENT_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"agents": {}}


def generate_agent_card(agent_id: str, agent_config: dict = None) -> dict:
    """Generate A2A v0.3 Agent Card for a specific agent.

    Args:
        agent_id: Agent identifier (e.g., 'orchestrator', 'builder').
        agent_config: Optional agent configuration dict override.

    Returns:
        A2A v0.3 compliant Agent Card dict.
    """
    if not agent_config:
        config = _load_agent_config()
        agent_config = config.get("agents", {}).get(agent_id, {})

    port = agent_config.get("port", 8443)
    host = agent_config.get("host", "localhost")
    name = agent_config.get("id", f"{agent_id}-agent")
    description = agent_config.get("description", f"ICDEV {agent_id} agent")

    # Build skills list
    skills = []
    for skill in AGENT_SKILLS.get(agent_id, []):
        skills.append({
            "id": skill["id"],
            "name": skill["name"],
            "description": skill["description"],
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
        })

    # Build v0.3 Agent Card
    card = {
        "name": name,
        "description": description,
        "url": f"https://{host}:{port}",
        "version": "1.0.0",
        "protocolVersion": A2A_PROTOCOL_VERSION,
        "contextId": f"ctx-{agent_id}-v1",
        "capabilities": {
            **DEFAULT_CAPABILITIES,
            "streaming": agent_config.get("streaming", False),
        },
        "authentication": {
            "schemes": ["mutual_tls", "api_key"],
        },
        "skills": skills,
        "tasks": {
            "sendSubscribe": {
                "methods": ["POST"],
                "path": "/tasks/subscribe",
                "description": "Subscribe to task completion notifications via callback",
            }
        },
        "metadata": {
            "tier": _get_agent_tier(agent_id),
            "classification": "CUI // SP-CTI",
            "icdev_version": "1.0",
        },
    }

    return card


def _get_agent_tier(agent_id: str) -> str:
    """Determine agent tier (core/domain/support)."""
    core = {"orchestrator", "architect"}
    support = {"knowledge", "monitor"}
    if agent_id in core:
        return "core"
    if agent_id in support:
        return "support"
    return "domain"


def generate_all_cards() -> dict:
    """Generate Agent Cards for all configured agents.

    Returns:
        Dict mapping agent_id to Agent Card.
    """
    config = _load_agent_config()
    agents = config.get("agents", {})
    cards = {}

    for agent_id in agents:
        cards[agent_id] = generate_agent_card(agent_id, agents[agent_id])

    # Add cards for agents with known skills but possibly not in config
    for agent_id in AGENT_SKILLS:
        if agent_id not in cards:
            cards[agent_id] = generate_agent_card(agent_id)

    return cards


def list_agents() -> list:
    """List all agents with their card summary.

    Returns:
        List of agent summaries.
    """
    cards = generate_all_cards()
    agents = []
    for agent_id, card in sorted(cards.items()):
        agents.append({
            "agent_id": agent_id,
            "name": card["name"],
            "url": card["url"],
            "protocol_version": card["protocolVersion"],
            "skill_count": len(card["skills"]),
            "tier": card["metadata"]["tier"],
            "task_subscription": card["capabilities"].get("taskSubscription", False),
        })
    return agents


def main():
    parser = argparse.ArgumentParser(
        description="A2A v0.3 Agent Card Generator (D344)"
    )
    parser.add_argument("--agent-id", help="Generate card for specific agent", dest="agent_id")
    parser.add_argument("--all", action="store_true", help="Generate cards for all agents")
    parser.add_argument("--list", action="store_true", help="List all agents")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    if args.list:
        agents = list_agents()
        if args.json_output:
            print(json.dumps({"agents": agents, "count": len(agents)}, indent=2))
        else:
            print(f"\n=== ICDEV Agents ({len(agents)}) ===")
            for a in agents:
                print(f"  {a['agent_id']:25s} {a['url']:30s} {a['skill_count']:2d} skills  [{a['tier']}]")
        return

    if args.all:
        cards = generate_all_cards()
        if args.json_output:
            print(json.dumps({"cards": cards, "count": len(cards), "protocol_version": A2A_PROTOCOL_VERSION}, indent=2))
        else:
            print(f"\n=== A2A v{A2A_PROTOCOL_VERSION} Agent Cards ({len(cards)}) ===")
            for aid, card in sorted(cards.items()):
                print(f"  {aid}: {card['name']} ({len(card['skills'])} skills)")
        return

    if args.agent_id:
        card = generate_agent_card(args.agent_id)
        if args.json_output:
            print(json.dumps(card, indent=2))
        else:
            print(f"\n=== Agent Card: {card['name']} ===")
            print(f"  URL: {card['url']}")
            print(f"  Protocol: v{card['protocolVersion']}")
            print(f"  Skills: {len(card['skills'])}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
