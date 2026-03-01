# [TEMPLATE: CUI // SP-CTI]
"""Health-aware agent-skill routing module.

Routes skill invocations to the healthiest available agent, respecting
heartbeat staleness windows and current task load. Provides routing table
introspection for the orchestrator and dashboard.

Decision D-DISP-1: Dispatcher mode awareness in route_skill (Phase 61).
"""

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "data" / "icdev.db"

logger = logging.getLogger("icdev.skill_router")


# ---------------------------------------------------------------------------
# Database helper
# ---------------------------------------------------------------------------
def _get_db(db_path: Path = None) -> sqlite3.Connection:
    """Get a database connection with row factory."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict, parsing JSON capabilities."""
    if row is None:
        return None
    d = dict(row)
    if d.get("capabilities") and isinstance(d["capabilities"], str):
        try:
            d["capabilities"] = json.loads(d["capabilities"])
        except json.JSONDecodeError:
            pass
    return d


def _audit_log(event_type: str, actor: str, action: str,
               project_id: str = None, details: dict = None,
               db_path: Path = None):
    """Best-effort audit trail logging."""
    try:
        from tools.audit.audit_logger import log_event
        log_event(
            event_type=event_type,
            actor=actor,
            action=action,
            project_id=project_id,
            details=details,
            classification="CUI",
            db_path=db_path,
        )
    except Exception as exc:
        logger.debug("Audit logging failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Staleness helpers
# ---------------------------------------------------------------------------
def _parse_timestamp(ts_str: str) -> Optional[datetime]:
    """Parse a SQLite timestamp string to a timezone-aware datetime.

    Handles both ISO-8601 and SQLite default formats.
    Returns None if parsing fails.
    """
    if not ts_str:
        return None
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(ts_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _is_stale(last_heartbeat: str, staleness_seconds: int) -> bool:
    """Check if a heartbeat timestamp is older than the staleness window.

    Args:
        last_heartbeat: Timestamp string from the database.
        staleness_seconds: Maximum age in seconds before considered stale.

    Returns:
        True if the heartbeat is stale or unparseable.
    """
    dt = _parse_timestamp(last_heartbeat)
    if dt is None:
        return True
    now = datetime.now(timezone.utc)
    age = (now - dt).total_seconds()
    return age > staleness_seconds


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def discover_agents_healthy(
    staleness_seconds: int = 120,
    db_path: Path = None,
) -> List[dict]:
    """Return active agents whose last_heartbeat is within the staleness window.

    Args:
        staleness_seconds: Maximum heartbeat age in seconds (default: 120).
        db_path: Optional database path override.

    Returns:
        List of agent dicts that are active AND not stale.
    """
    conn = _get_db(db_path)
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM agents WHERE status = 'active' ORDER BY name")
        rows = c.fetchall()

        healthy = []
        for row in rows:
            agent = _row_to_dict(row)
            hb = agent.get("last_heartbeat", "")
            if not _is_stale(hb, staleness_seconds):
                agent["healthy"] = True
                agent["stale"] = False
                healthy.append(agent)
            else:
                logger.debug(
                    "Agent '%s' excluded -- heartbeat stale (last: %s, window: %ds)",
                    agent.get("id", "?"), hb, staleness_seconds,
                )

        return healthy
    finally:
        conn.close()


def get_agent_load(agent_id: str, db_path: Path = None) -> dict:
    """Get the current task load for an agent.

    Counts in-progress tasks from the a2a_tasks table (status = 'working')
    and queued tasks (status = 'submitted').

    Args:
        agent_id: The agent to check.
        db_path: Optional database path override.

    Returns:
        Dict with agent_id, working_count, queued_count, total_active.
    """
    conn = _get_db(db_path)
    try:
        c = conn.cursor()

        # Count from a2a_tasks table
        working = 0
        queued = 0
        try:
            c.execute(
                "SELECT COUNT(*) FROM a2a_tasks WHERE assigned_agent = ? AND status = 'working'",
                (agent_id,),
            )
            working = c.fetchone()[0]

            c.execute(
                "SELECT COUNT(*) FROM a2a_tasks WHERE assigned_agent = ? AND status = 'submitted'",
                (agent_id,),
            )
            queued = c.fetchone()[0]
        except sqlite3.OperationalError:
            # a2a_tasks table may not exist; fallback to agent_subtasks
            try:
                c.execute(
                    "SELECT COUNT(*) FROM agent_subtasks WHERE agent_id = ? AND status = 'working'",
                    (agent_id,),
                )
                working = c.fetchone()[0]

                c.execute(
                    "SELECT COUNT(*) FROM agent_subtasks WHERE agent_id = ? AND status = 'queued'",
                    (agent_id,),
                )
                queued = c.fetchone()[0]
            except sqlite3.OperationalError:
                pass

        return {
            "agent_id": agent_id,
            "working_count": working,
            "queued_count": queued,
            "total_active": working + queued,
        }
    finally:
        conn.close()


def route_skill(
    skill_id: str,
    db_path: Path = None,
    staleness_seconds: int = 120,
    project_id: str = None,
) -> Optional[dict]:
    """Find the healthiest agent for a given skill.

    Routing algorithm:
    1. Check dispatcher mode -- if enabled and the skill is blocked for the
       orchestrator, redirect to the appropriate domain agent (D-DISP-1).
    2. Use agent_registry.find_agent_for_skill to get candidate agents.
    3. Check each candidate's heartbeat against staleness window.
    4. If multiple agents handle the skill, pick the least-loaded.
    5. Log audit event if all candidates are stale.

    Args:
        skill_id: The skill to route.
        db_path: Optional database path override.
        staleness_seconds: Maximum heartbeat age (default: 120s).
        project_id: Optional project ID for dispatcher mode checks.

    Returns:
        Agent dict with load info appended, or None if no healthy agent found.
    """
    effective_db = db_path or DB_PATH

    # Phase 61: Dispatcher mode awareness (D-DISP-1)
    # If dispatcher mode is active and the orchestrator requests a blocked skill,
    # redirect to the domain agent that owns the skill.
    try:
        from tools.agent.dispatcher_mode import (
            is_dispatcher_mode, is_tool_allowed, get_redirect_agent,
        )
        if is_dispatcher_mode(project_id=project_id, db_path=effective_db):
            if not is_tool_allowed(skill_id, project_id=project_id,
                                   db_path=effective_db):
                redirect_agent = get_redirect_agent(skill_id)
                if redirect_agent:
                    logger.info(
                        "Dispatcher mode: skill '%s' blocked for orchestrator, "
                        "routing to '%s'",
                        skill_id, redirect_agent,
                    )
                    _audit_log(
                        event_type="dispatcher_mode.skill_redirect",
                        actor="skill-router",
                        action=(
                            f"Dispatcher mode redirected skill '{skill_id}' "
                            f"to {redirect_agent}"
                        ),
                        project_id=project_id,
                        details={
                            "skill_id": skill_id,
                            "redirected_to": redirect_agent,
                        },
                        db_path=effective_db,
                    )
                    # Look up the redirect agent directly
                    conn = _get_db(effective_db)
                    try:
                        c = conn.cursor()
                        c.execute(
                            "SELECT * FROM agents WHERE id = ? AND status = 'active'",
                            (redirect_agent,),
                        )
                        row = c.fetchone()
                        if row:
                            agent = _row_to_dict(row)
                            agent["load"] = get_agent_load(
                                redirect_agent, db_path=effective_db,
                            )
                            agent["dispatcher_redirect"] = True
                            return agent
                    finally:
                        conn.close()
    except ImportError:
        pass  # dispatcher_mode module not available -- skip check

    # Step 1: Find all agents that can handle this skill
    candidates = _find_all_agents_for_skill(skill_id, effective_db)

    if not candidates:
        logger.warning("No agent registered for skill '%s'", skill_id)
        return None

    # Step 2: Filter by staleness
    healthy_candidates = []
    stale_candidates = []

    for agent in candidates:
        hb = agent.get("last_heartbeat", "")
        if _is_stale(hb, staleness_seconds):
            stale_candidates.append(agent)
        else:
            healthy_candidates.append(agent)

    # Step 3: If all stale, log audit and return None
    if not healthy_candidates:
        agent_ids = [a.get("id", "?") for a in stale_candidates]
        logger.warning(
            "All agents for skill '%s' are stale: %s", skill_id, agent_ids,
        )
        _audit_log(
            event_type="agent_health_stale",
            actor="skill-router",
            action=(
                f"All agents for skill '{skill_id}' are stale "
                f"(window: {staleness_seconds}s): {agent_ids}"
            ),
            details={
                "skill_id": skill_id,
                "stale_agents": agent_ids,
                "staleness_seconds": staleness_seconds,
            },
            db_path=effective_db,
        )
        return None

    # Step 4: Pick least-loaded among healthy candidates
    best_agent = None
    best_load = float("inf")

    for agent in healthy_candidates:
        load = get_agent_load(agent["id"], db_path=effective_db)
        agent["load"] = load
        total_active = load.get("total_active", 0)

        if total_active < best_load:
            best_load = total_active
            best_agent = agent

    if best_agent:
        logger.info(
            "Routed skill '%s' to agent '%s' (load: %d active tasks)",
            skill_id, best_agent["id"], best_load,
        )

    return best_agent


def _find_all_agents_for_skill(skill_id: str, db_path: Path = None) -> List[dict]:
    """Find ALL active agents that handle the given skill.

    Unlike agent_registry.find_agent_for_skill which returns the first match,
    this returns all matching agents for load-based selection.

    Args:
        skill_id: The skill to search for.
        db_path: Optional database path override.

    Returns:
        List of agent dicts that advertise the skill.
    """
    conn = _get_db(db_path)
    matches = []
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
                    sid = skill.get("id", skill) if isinstance(skill, dict) else skill
                    if sid == skill_id:
                        matches.append(agent)
                        break
    finally:
        conn.close()

    # Fallback: check agent card files
    if not matches:
        try:
            from tools.a2a.agent_registry import AGENT_CARDS_DIR
            if AGENT_CARDS_DIR.exists():
                for card_file in AGENT_CARDS_DIR.glob("*.json"):
                    try:
                        with open(card_file, "r") as f:
                            card = json.load(f)
                        for skill in card.get("skills", []):
                            if skill.get("id") == skill_id:
                                matches.append({
                                    "id": card_file.stem + "-agent",
                                    "name": card.get("name", ""),
                                    "description": card.get("description", ""),
                                    "url": card.get("url", ""),
                                    "capabilities": card,
                                    "status": "active",
                                    "last_heartbeat": "",
                                    "source": "agent_card_file",
                                })
                                break
                    except (json.JSONDecodeError, IOError):
                        continue
        except ImportError:
            pass

    return matches


def get_routing_table(db_path: Path = None, staleness_seconds: int = 120) -> dict:
    """Return the full routing table: skill_id -> [agent_id, ...] with health status.

    Builds a complete map of which skills are served by which agents,
    including health and load information.

    Args:
        db_path: Optional database path override.
        staleness_seconds: Maximum heartbeat age for health classification.

    Returns:
        Dict mapping skill_id to list of agent entries with health/load info.
    """
    effective_db = db_path or DB_PATH
    conn = _get_db(effective_db)
    routing: Dict[str, list] = {}

    try:
        c = conn.cursor()
        c.execute("SELECT * FROM agents WHERE status = 'active' ORDER BY name")
        rows = c.fetchall()

        for row in rows:
            agent = _row_to_dict(row)
            agent_id = agent.get("id", "")
            hb = agent.get("last_heartbeat", "")
            stale = _is_stale(hb, staleness_seconds)
            load = get_agent_load(agent_id, db_path=effective_db)

            caps = agent.get("capabilities")
            if not isinstance(caps, dict):
                continue

            skills = caps.get("skills", [])
            for skill in skills:
                sid = skill.get("id", skill) if isinstance(skill, dict) else skill
                skill_name = skill.get("name", sid) if isinstance(skill, dict) else sid

                entry = {
                    "agent_id": agent_id,
                    "agent_name": agent.get("name", ""),
                    "agent_url": agent.get("url", ""),
                    "skill_name": skill_name,
                    "healthy": not stale,
                    "stale": stale,
                    "last_heartbeat": hb,
                    "load": load,
                }

                if sid not in routing:
                    routing[sid] = []
                routing[sid].append(entry)

    finally:
        conn.close()

    return routing


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    """CLI for skill routing and agent health introspection."""
    parser = argparse.ArgumentParser(
        description="ICDEV Skill Router -- health-aware agent-skill routing"
    )
    parser.add_argument(
        "--route-skill",
        help="Find the healthiest agent for a skill ID",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="List all healthy agents",
    )
    parser.add_argument(
        "--routing-table",
        action="store_true",
        help="Display full skill-to-agent routing table",
    )
    parser.add_argument(
        "--agent-load",
        help="Get task load for a specific agent ID",
    )
    parser.add_argument(
        "--staleness",
        type=int,
        default=120,
        help="Heartbeat staleness window in seconds (default: 120)",
    )
    parser.add_argument("--project-id", help="Project ID for dispatcher mode checks")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--db-path", help="Override database path")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    db_path = Path(args.db_path) if args.db_path else None

    if args.route_skill:
        agent = route_skill(
            skill_id=args.route_skill,
            db_path=db_path,
            staleness_seconds=args.staleness,
            project_id=args.project_id,
        )

        if agent:
            if args.json:
                print(json.dumps(agent, indent=2, default=str))
            else:
                load = agent.get("load", {})
                print(f"Skill: {args.route_skill}")
                print(f"Routed to: {agent['id']} ({agent.get('name', '')})")
                print(f"URL: {agent.get('url', 'N/A')}")
                print(f"Active tasks: {load.get('total_active', 0)}")
                print(f"  Working: {load.get('working_count', 0)}")
                print(f"  Queued: {load.get('queued_count', 0)}")
                print(f"Last heartbeat: {agent.get('last_heartbeat', 'N/A')}")
                if agent.get("dispatcher_redirect"):
                    print("  (redirected by dispatcher mode)")
                print("Classification: CUI // SP-CTI")
        else:
            if args.json:
                print(json.dumps({
                    "skill_id": args.route_skill,
                    "routed_agent": None,
                    "reason": "No healthy agent found for skill",
                    "classification": "CUI",
                }, indent=2))
            else:
                print(f"No healthy agent found for skill: {args.route_skill}")

    elif args.health:
        agents = discover_agents_healthy(
            staleness_seconds=args.staleness,
            db_path=db_path,
        )

        if args.json:
            print(json.dumps({
                "healthy_agents": agents,
                "count": len(agents),
                "staleness_seconds": args.staleness,
                "classification": "CUI",
            }, indent=2, default=str))
        else:
            print(f"Healthy agents (staleness window: {args.staleness}s):")
            print("Classification: CUI // SP-CTI")
            print(f"{'Agent ID':<30} {'Name':<25} {'URL':<35} {'Last Heartbeat'}")
            print("-" * 120)
            for agent in agents:
                print(
                    f"{agent.get('id', 'N/A'):<30} "
                    f"{agent.get('name', 'N/A'):<25} "
                    f"{agent.get('url', 'N/A'):<35} "
                    f"{agent.get('last_heartbeat', 'N/A')}"
                )
            if not agents:
                print("  (no healthy agents found)")

    elif args.routing_table:
        table = get_routing_table(
            db_path=db_path,
            staleness_seconds=args.staleness,
        )

        if args.json:
            print(json.dumps({
                "routing_table": table,
                "skill_count": len(table),
                "staleness_seconds": args.staleness,
                "classification": "CUI",
            }, indent=2, default=str))
        else:
            print(f"Routing Table (staleness window: {args.staleness}s):")
            print("Classification: CUI // SP-CTI")
            print()
            for skill_id, agents in sorted(table.items()):
                print(f"  {skill_id}:")
                for entry in agents:
                    health = "HEALTHY" if entry["healthy"] else "STALE"
                    load = entry.get("load", {}).get("total_active", 0)
                    print(
                        f"    -> {entry['agent_id']} ({entry['agent_name']}) "
                        f"[{health}] load={load}"
                    )
                print()
            if not table:
                print("  (no skills registered)")

    elif args.agent_load:
        load = get_agent_load(args.agent_load, db_path=db_path)

        if args.json:
            load["classification"] = "CUI"
            print(json.dumps(load, indent=2))
        else:
            print(f"Agent: {load['agent_id']}")
            print(f"Working tasks: {load['working_count']}")
            print(f"Queued tasks: {load['queued_count']}")
            print(f"Total active: {load['total_active']}")
            print("Classification: CUI // SP-CTI")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
