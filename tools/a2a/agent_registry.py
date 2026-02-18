#!/usr/bin/env python3
"""A2A Agent Registry — agent discovery and registration.

Provides:
- register_agent(agent_id, name, description, url, capabilities) -> insert into agents table
- deregister_agent(agent_id) -> set status inactive
- discover_agents() -> list all active agents
- get_agent(agent_id) -> get specific agent
- heartbeat(agent_id) -> update last_heartbeat
- find_agent_for_skill(skill_id) -> find agent that handles a given skill
"""

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
AGENT_CARDS_DIR = Path(__file__).resolve().parent / "agent_cards"


def _get_db(db_path: Path = None) -> sqlite3.Connection:
    """Get a database connection with row factory."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    if row is None:
        return None
    d = dict(row)
    # Parse capabilities JSON if present
    if d.get("capabilities") and isinstance(d["capabilities"], str):
        try:
            d["capabilities"] = json.loads(d["capabilities"])
        except json.JSONDecodeError:
            pass
    return d


def register_agent(
    agent_id: str,
    name: str,
    description: str,
    url: str,
    capabilities: Optional[Dict] = None,
    db_path: Path = None,
) -> dict:
    """Register a new agent or update an existing one.

    Args:
        agent_id: Unique agent identifier.
        name: Human-readable agent name.
        description: What the agent does.
        url: Base URL of the agent (e.g. https://localhost:8443).
        capabilities: Dict of agent capabilities (skills, streaming, etc.).
        db_path: Optional database path override.

    Returns:
        The registered agent record as a dict.
    """
    conn = _get_db(db_path)
    try:
        c = conn.cursor()
        caps_json = json.dumps(capabilities) if capabilities else None

        c.execute(
            """INSERT INTO agents (id, name, description, url, status, capabilities, last_heartbeat)
               VALUES (?, ?, ?, ?, 'active', ?, CURRENT_TIMESTAMP)
               ON CONFLICT(id) DO UPDATE SET
               name = excluded.name,
               description = excluded.description,
               url = excluded.url,
               status = 'active',
               capabilities = excluded.capabilities,
               last_heartbeat = CURRENT_TIMESTAMP""",
            (agent_id, name, description, url, caps_json),
        )
        conn.commit()

        c.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        row = c.fetchone()
        result = _row_to_dict(row)
        print(f"Agent registered: {agent_id} ({name}) at {url}")
        return result
    finally:
        conn.close()


def deregister_agent(agent_id: str, db_path: Path = None) -> bool:
    """Set an agent's status to inactive.

    Args:
        agent_id: The agent to deregister.
        db_path: Optional database path override.

    Returns:
        True if the agent was found and deregistered.
    """
    conn = _get_db(db_path)
    try:
        c = conn.cursor()
        c.execute("UPDATE agents SET status = 'inactive' WHERE id = ?", (agent_id,))
        conn.commit()
        changed = c.rowcount > 0
        if changed:
            print(f"Agent deregistered: {agent_id}")
        else:
            print(f"Agent not found: {agent_id}")
        return changed
    finally:
        conn.close()


def discover_agents(db_path: Path = None) -> List[dict]:
    """List all active agents.

    Returns:
        List of agent dicts with id, name, description, url, capabilities.
    """
    conn = _get_db(db_path)
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM agents WHERE status = 'active' ORDER BY name")
        rows = c.fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_agent(agent_id: str, db_path: Path = None) -> Optional[dict]:
    """Get a specific agent by ID.

    Args:
        agent_id: The agent to look up.

    Returns:
        Agent dict or None if not found.
    """
    conn = _get_db(db_path)
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        row = c.fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def heartbeat(agent_id: str, db_path: Path = None) -> bool:
    """Update an agent's heartbeat timestamp.

    Args:
        agent_id: The agent sending the heartbeat.

    Returns:
        True if the agent was found and updated.
    """
    conn = _get_db(db_path)
    try:
        c = conn.cursor()
        c.execute(
            "UPDATE agents SET last_heartbeat = CURRENT_TIMESTAMP WHERE id = ?",
            (agent_id,),
        )
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()


def find_agent_for_skill(skill_id: str, db_path: Path = None) -> Optional[dict]:
    """Find an active agent that handles the given skill.

    Searches the capabilities JSON of each active agent for a matching skill ID.
    Also checks agent card files in the agent_cards/ directory as a fallback.

    Args:
        skill_id: The skill to search for.

    Returns:
        Agent dict or None if no agent handles the skill.
    """
    conn = _get_db(db_path)
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM agents WHERE status = 'active'")
        rows = c.fetchall()

        for row in rows:
            agent = _row_to_dict(row)
            caps = agent.get("capabilities")
            if isinstance(caps, dict):
                skills = caps.get("skills", [])
                for skill in skills:
                    sid = skill.get("id", "") if isinstance(skill, dict) else skill
                    if sid == skill_id:
                        return agent
    finally:
        conn.close()

    # Fallback: check agent card JSON files
    if AGENT_CARDS_DIR.exists():
        for card_file in AGENT_CARDS_DIR.glob("*.json"):
            try:
                with open(card_file, "r") as f:
                    card = json.load(f)
                for skill in card.get("skills", []):
                    if skill.get("id") == skill_id:
                        return {
                            "id": card_file.stem + "-agent",
                            "name": card.get("name", ""),
                            "description": card.get("description", ""),
                            "url": card.get("url", ""),
                            "capabilities": card,
                            "status": "active",
                            "source": "agent_card_file",
                        }
            except (json.JSONDecodeError, IOError):
                continue

    return None


def discover_agents_healthy(staleness_seconds: int = 120, db_path: Path = None) -> List[dict]:
    """List active agents whose last_heartbeat is within the staleness window.

    Args:
        staleness_seconds: Max seconds since last heartbeat to consider healthy.
        db_path: Optional database path override.

    Returns:
        List of healthy agent dicts.
    """
    conn = _get_db(db_path)
    try:
        c = conn.cursor()
        c.execute(
            """SELECT * FROM agents
               WHERE status = 'active'
                 AND last_heartbeat IS NOT NULL
                 AND (julianday('now') - julianday(last_heartbeat)) * 86400 <= ?
               ORDER BY name""",
            (staleness_seconds,),
        )
        rows = c.fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_agent_load(agent_id: str, db_path: Path = None) -> dict:
    """Get current task load for an agent (count of in-progress tasks).

    Args:
        agent_id: The agent to check.
        db_path: Optional database path override.

    Returns:
        Dict with agent_id, active_tasks count, and recent_completed count.
    """
    conn = _get_db(db_path)
    try:
        c = conn.cursor()
        # Count non-terminal tasks assigned to this agent
        c.execute(
            """SELECT COUNT(*) as cnt FROM a2a_tasks
               WHERE target_agent_id = ?
                 AND status IN ('submitted', 'working')""",
            (agent_id,),
        )
        active = c.fetchone()["cnt"]

        # Count recently completed tasks (last hour)
        c.execute(
            """SELECT COUNT(*) as cnt FROM a2a_tasks
               WHERE target_agent_id = ?
                 AND status = 'completed'
                 AND completed_at IS NOT NULL
                 AND (julianday('now') - julianday(completed_at)) * 86400 <= 3600""",
            (agent_id,),
        )
        recent = c.fetchone()["cnt"]

        return {
            "agent_id": agent_id,
            "active_tasks": active,
            "recent_completed": recent,
        }
    finally:
        conn.close()


def register_all_from_cards(db_path: Path = None) -> List[dict]:
    """Register all agents from their agent card JSON files.

    Reads each JSON file in agent_cards/ and registers the agent.

    Returns:
        List of registered agent dicts.
    """
    registered = []
    if not AGENT_CARDS_DIR.exists():
        print(f"Agent cards directory not found: {AGENT_CARDS_DIR}")
        return registered

    for card_file in sorted(AGENT_CARDS_DIR.glob("*.json")):
        try:
            with open(card_file, "r") as f:
                card = json.load(f)

            agent_id = card_file.stem + "-agent"
            agent = register_agent(
                agent_id=agent_id,
                name=card.get("name", agent_id),
                description=card.get("description", ""),
                url=card.get("url", ""),
                capabilities=card,
                db_path=db_path,
            )
            registered.append(agent)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error loading {card_file}: {e}")

    return registered


def main():
    parser = argparse.ArgumentParser(description="A2A Agent Registry")
    sub = parser.add_subparsers(dest="command", help="Command")

    # register
    p_reg = sub.add_parser("register", help="Register an agent")
    p_reg.add_argument("--agent-id", required=True, help="Agent ID")
    p_reg.add_argument("--name", required=True, help="Agent name")
    p_reg.add_argument("--description", default="", help="Description")
    p_reg.add_argument("--url", required=True, help="Agent URL")
    p_reg.add_argument("--capabilities", help="JSON capabilities string")

    # deregister
    p_dereg = sub.add_parser("deregister", help="Deregister an agent")
    p_dereg.add_argument("--agent-id", required=True, help="Agent ID")

    # discover
    sub.add_parser("discover", help="List all active agents")

    # get
    p_get = sub.add_parser("get", help="Get a specific agent")
    p_get.add_argument("--agent-id", required=True, help="Agent ID")

    # heartbeat
    p_hb = sub.add_parser("heartbeat", help="Send heartbeat")
    p_hb.add_argument("--agent-id", required=True, help="Agent ID")

    # find-skill
    p_skill = sub.add_parser("find-skill", help="Find agent for a skill")
    p_skill.add_argument("--skill-id", required=True, help="Skill ID")

    # register-all
    sub.add_parser("register-all", help="Register all agents from card files")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "register":
        caps = json.loads(args.capabilities) if args.capabilities else None
        result = register_agent(args.agent_id, args.name, args.description, args.url, caps)
        print(json.dumps(result, indent=2, default=str))

    elif args.command == "deregister":
        deregister_agent(args.agent_id)

    elif args.command == "discover":
        agents = discover_agents()
        print(json.dumps(agents, indent=2, default=str))

    elif args.command == "get":
        agent = get_agent(args.agent_id)
        if agent:
            print(json.dumps(agent, indent=2, default=str))
        else:
            print(f"Agent not found: {args.agent_id}")

    elif args.command == "heartbeat":
        ok = heartbeat(args.agent_id)
        print(f"Heartbeat {'sent' if ok else 'failed (agent not found)'}")

    elif args.command == "find-skill":
        agent = find_agent_for_skill(args.skill_id)
        if agent:
            print(json.dumps(agent, indent=2, default=str))
        else:
            print(f"No agent found for skill: {args.skill_id}")

    elif args.command == "register-all":
        agents = register_all_from_cards()
        print(f"Registered {len(agents)} agents from card files")


if __name__ == "__main__":
    main()
