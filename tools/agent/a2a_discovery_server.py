#!/usr/bin/env python3
# CUI // SP-CTI
"""A2A v0.3 Agent Discovery Server.

Provides a centralized agent discovery endpoint that returns v0.3 Agent Cards
for all registered ICDEV agents. Supports capability-based filtering and
health-aware routing.

Architecture Decisions:
  D344: A2A v0.3 discovery with capability filtering.
  D2:   Stdio for MCP, HTTPS+mTLS for A2A within K8s.

Usage:
  python tools/agent/a2a_discovery_server.py --help
  python tools/agent/a2a_discovery_server.py --port 8460 --debug
  python tools/agent/a2a_discovery_server.py --list --json
  python tools/agent/a2a_discovery_server.py --find-skill ssp_generate --json
  python tools/agent/a2a_discovery_server.py --find-capability taskSubscription --json
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def _get_connection(db_path=None):
    """Get database connection."""
    path = db_path or DB_PATH
    if path.exists():
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        return conn
    return None


def discover_agents(db_path=None) -> list:
    """Discover all registered agents from the database.

    Returns:
        List of agent records with cards and health status.
    """
    from tools.agent.a2a_agent_card_generator import generate_all_cards

    cards = generate_all_cards()
    agents = []

    conn = _get_connection(db_path)
    health_map = {}

    if conn:
        try:
            rows = conn.execute(
                "SELECT id, name, url, status, last_heartbeat FROM agent_registry"
            ).fetchall()
            for row in rows:
                health_map[row["id"]] = {
                    "status": row["status"],
                    "last_heartbeat": row["last_heartbeat"],
                    "url": row["url"],
                }
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    for agent_id, card in sorted(cards.items()):
        agent_name = card["name"]
        health = health_map.get(agent_name, {})
        agents.append({
            "agent_id": agent_id,
            "card": card,
            "health": {
                "status": health.get("status", "unknown"),
                "last_heartbeat": health.get("last_heartbeat"),
                "registered_url": health.get("url"),
            },
        })

    return agents


def find_agent_for_skill(skill_id: str, db_path=None) -> list:
    """Find agents that provide a specific skill.

    Args:
        skill_id: The skill ID to search for.

    Returns:
        List of matching agents with their cards.
    """
    agents = discover_agents(db_path)
    matches = []
    for agent in agents:
        skills = agent["card"].get("skills", [])
        for skill in skills:
            if skill["id"] == skill_id:
                matches.append({
                    "agent_id": agent["agent_id"],
                    "agent_name": agent["card"]["name"],
                    "url": agent["card"]["url"],
                    "skill": skill,
                    "health_status": agent["health"]["status"],
                })
                break
    return matches


def find_agents_by_capability(capability: str, db_path=None) -> list:
    """Find agents that support a specific capability.

    Args:
        capability: Capability key (e.g., 'taskSubscription', 'streaming').

    Returns:
        List of matching agents.
    """
    agents = discover_agents(db_path)
    matches = []
    for agent in agents:
        caps = agent["card"].get("capabilities", {})
        if caps.get(capability, False):
            matches.append({
                "agent_id": agent["agent_id"],
                "agent_name": agent["card"]["name"],
                "url": agent["card"]["url"],
                "capability_value": caps[capability],
                "health_status": agent["health"]["status"],
            })
    return matches


def get_discovery_summary(db_path=None) -> dict:
    """Get a summary of the agent discovery landscape.

    Returns:
        Summary dict with agent counts, capability coverage, and health stats.
    """
    agents = discover_agents(db_path)

    tier_counts = {"core": 0, "domain": 0, "support": 0}
    health_counts = {"healthy": 0, "unhealthy": 0, "unknown": 0}
    total_skills = 0
    capability_coverage = {}

    for agent in agents:
        card = agent["card"]
        tier = card.get("metadata", {}).get("tier", "domain")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

        health = agent["health"]["status"]
        if health in ("healthy", "active"):
            health_counts["healthy"] += 1
        elif health == "unknown":
            health_counts["unknown"] += 1
        else:
            health_counts["unhealthy"] += 1

        total_skills += len(card.get("skills", []))

        for cap, val in card.get("capabilities", {}).items():
            if cap not in capability_coverage:
                capability_coverage[cap] = {"true": 0, "false": 0}
            if val:
                capability_coverage[cap]["true"] += 1
            else:
                capability_coverage[cap]["false"] += 1

    return {
        "total_agents": len(agents),
        "protocol_version": "0.3",
        "tier_distribution": tier_counts,
        "health_summary": health_counts,
        "total_skills": total_skills,
        "capability_coverage": capability_coverage,
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(
        description="A2A v0.3 Agent Discovery Server (D344)"
    )
    parser.add_argument("--list", action="store_true", help="List all discovered agents")
    parser.add_argument("--find-skill", help="Find agents providing a skill", dest="find_skill")
    parser.add_argument("--find-capability", help="Find agents with a capability", dest="find_capability")
    parser.add_argument("--summary", action="store_true", help="Discovery landscape summary")
    parser.add_argument("--port", type=int, default=8460, help="Server port (default: 8460)")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("--db", help="Database path")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    db = Path(args.db) if args.db else None

    if args.list:
        agents = discover_agents(db)
        if args.json_output:
            print(json.dumps({"agents": [{"agent_id": a["agent_id"], "name": a["card"]["name"], "url": a["card"]["url"], "skills": len(a["card"]["skills"]), "health": a["health"]["status"]} for a in agents], "count": len(agents)}, indent=2))
        else:
            print(f"\n=== Discovered Agents ({len(agents)}) ===")
            for a in agents:
                print(f"  {a['agent_id']:25s} {a['card']['url']:30s} {len(a['card']['skills']):2d} skills  [{a['health']['status']}]")
        return

    if args.find_skill:
        matches = find_agent_for_skill(args.find_skill, db)
        if args.json_output:
            print(json.dumps({"skill_id": args.find_skill, "matches": matches, "count": len(matches)}, indent=2))
        else:
            print(f"\n=== Agents providing '{args.find_skill}' ({len(matches)}) ===")
            for m in matches:
                print(f"  {m['agent_name']:30s} {m['url']:30s} [{m['health_status']}]")
        return

    if args.find_capability:
        matches = find_agents_by_capability(args.find_capability, db)
        if args.json_output:
            print(json.dumps({"capability": args.find_capability, "matches": matches, "count": len(matches)}, indent=2))
        else:
            print(f"\n=== Agents with '{args.find_capability}' ({len(matches)}) ===")
            for m in matches:
                print(f"  {m['agent_name']:30s} {m['url']:30s}")
        return

    if args.summary:
        summary = get_discovery_summary(db)
        if args.json_output:
            print(json.dumps(summary, indent=2))
        else:
            print(f"\n=== Discovery Summary ===")
            print(f"  Total Agents: {summary['total_agents']}")
            print(f"  Protocol: v{summary['protocol_version']}")
            print(f"  Total Skills: {summary['total_skills']}")
            print(f"  Tiers: {summary['tier_distribution']}")
        return

    # Default: show help
    parser.print_help()


if __name__ == "__main__":
    main()
