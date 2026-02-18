# CUI // SP-CTI
"""Agent Memory — scoped knowledge storage per agent and project.

Provides project-scoped memory for individual agents and team-shared knowledge.
Agents can store facts, preferences, patterns, lessons learned, and decisions.
Team-shared memories use the special agent_id '_team'.

Decision D43: Per-agent + team-shared via agent_id='_team'.
"""

import argparse
import json
import logging
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "data" / "icdev.db"

logger = logging.getLogger("icdev.agent_memory")

# Valid memory types matching the DB CHECK constraint
VALID_MEMORY_TYPES = (
    "fact", "preference", "collaboration", "dispute", "pattern",
    "context", "lesson_learned", "decision",
)

# Graceful audit import
try:
    from tools.audit.audit_logger import log_event as audit_log_event
except ImportError:
    def audit_log_event(**kwargs):
        logger.debug("audit_logger unavailable — skipping audit: %s", kwargs.get("action", ""))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db(db_path=None) -> sqlite3.Connection:
    """Open a DB connection with row factory."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def store(agent_id: str, project_id: str, memory_type: str, content: str,
          importance: int = 5, task_id: str = None,
          related_agent_ids: List[str] = None, expires_at: str = None,
          db_path=None) -> str:
    """Store a memory entry for an agent within a project scope.

    Args:
        agent_id: The agent storing the memory. Use '_team' for shared memories.
        project_id: Project scope for this memory.
        memory_type: One of VALID_MEMORY_TYPES.
        content: The memory content (text, may contain JSON).
        importance: 1-10 scale (default 5). Higher = more important.
        task_id: Associated task ID (optional).
        related_agent_ids: List of related agent IDs (optional).
        expires_at: ISO timestamp for expiration (optional).
        db_path: Optional database path override.

    Returns:
        The memory ID (UUID).

    Raises:
        ValueError: If memory_type is not valid or importance is out of range.
    """
    if memory_type not in VALID_MEMORY_TYPES:
        raise ValueError(
            f"Invalid memory_type '{memory_type}'. Valid: {VALID_MEMORY_TYPES}"
        )
    if not 1 <= importance <= 10:
        raise ValueError(f"importance must be 1-10, got {importance}")

    memory_id = str(uuid.uuid4())
    related_ids_json = json.dumps(related_agent_ids) if related_agent_ids else None

    conn = _get_db(db_path)
    try:
        conn.execute(
            """INSERT INTO agent_memory
               (id, agent_id, project_id, memory_type, content, importance,
                task_id, related_agent_ids, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (memory_id, agent_id, project_id, memory_type, content,
             importance, task_id, related_ids_json, expires_at),
        )
        conn.commit()
        logger.info("Memory %s stored: agent=%s project=%s type=%s importance=%d",
                     memory_id, agent_id, project_id, memory_type, importance)
    finally:
        conn.close()

    # Audit trail
    audit_log_event(
        event_type="agent_memory_stored",
        actor=agent_id,
        action=f"Memory stored: [{memory_type}] importance={importance}",
        project_id=project_id,
        details={
            "memory_id": memory_id,
            "memory_type": memory_type,
            "importance": importance,
            "content_preview": content[:100] if len(content) > 100 else content,
        },
        classification="CUI",
    )

    return memory_id


def recall(agent_id: str, project_id: str, query: str = None,
           memory_type: str = None, limit: int = 10,
           db_path=None) -> list:
    """Recall memories for a specific agent within a project.

    Supports keyword search on content and filtering by memory type.
    Results are ordered by importance (DESC) then recency (DESC).

    Args:
        agent_id: The agent recalling memories.
        project_id: Project scope.
        query: Keyword search term (optional, searches content).
        memory_type: Filter by memory type (optional).
        limit: Maximum results to return (default 10).
        db_path: Optional database path override.

    Returns:
        List of memory entry dicts.
    """
    conn = _get_db(db_path)
    try:
        sql = """SELECT * FROM agent_memory
                 WHERE agent_id = ? AND project_id = ?
                 AND (expires_at IS NULL OR expires_at > datetime('now'))"""
        params: list = [agent_id, project_id]

        if memory_type:
            if memory_type not in VALID_MEMORY_TYPES:
                raise ValueError(f"Invalid memory_type filter: {memory_type}")
            sql += " AND memory_type = ?"
            params.append(memory_type)

        if query:
            sql += " AND content LIKE ?"
            params.append(f"%{query}%")

        sql += " ORDER BY importance DESC, created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        memories = [dict(row) for row in rows]

        # Update access counts
        for mem in memories:
            conn.execute(
                """UPDATE agent_memory
                   SET access_count = access_count + 1,
                       last_accessed_at = datetime('now')
                   WHERE id = ?""",
                (mem["id"],),
            )
        conn.commit()

        # Audit trail
        audit_log_event(
            event_type="agent_memory_recalled",
            actor=agent_id,
            action=f"Recalled {len(memories)} memories (query='{query or '*'}')",
            project_id=project_id,
            details={"count": len(memories), "query": query, "memory_type": memory_type},
            classification="CUI",
        )

        return memories
    finally:
        conn.close()


def recall_team(project_id: str, query: str = None,
                memory_type: str = None, limit: int = 10,
                db_path=None) -> list:
    """Recall team-shared memories (agent_id='_team') for a project.

    Team memories are accessible to all agents within a project and
    represent shared knowledge, decisions, and lessons learned.

    Args:
        project_id: Project scope.
        query: Keyword search term (optional).
        memory_type: Filter by memory type (optional).
        limit: Maximum results (default 10).
        db_path: Optional database path override.

    Returns:
        List of team memory entry dicts.
    """
    return recall(
        agent_id="_team",
        project_id=project_id,
        query=query,
        memory_type=memory_type,
        limit=limit,
        db_path=db_path,
    )


def inject_context(agent_id: str, project_id: str, max_memories: int = 5,
                   db_path=None) -> str:
    """Build a context string from recent/important memories for system prompt injection.

    Combines the agent's own memories with team-shared memories to create
    a context block suitable for prepending to a system prompt.

    Args:
        agent_id: The agent requesting context.
        project_id: Project scope.
        max_memories: Maximum memories to include (default 5).
        db_path: Optional database path override.

    Returns:
        Formatted context string for system prompt injection.
    """
    # Get agent-specific memories (up to half the budget)
    agent_budget = max(1, max_memories // 2)
    team_budget = max_memories - agent_budget

    agent_memories = recall(
        agent_id=agent_id,
        project_id=project_id,
        limit=agent_budget,
        db_path=db_path,
    )

    team_memories = recall_team(
        project_id=project_id,
        limit=team_budget,
        db_path=db_path,
    )

    if not agent_memories and not team_memories:
        return ""

    lines = ["## Agent Memory Context", ""]

    if agent_memories:
        lines.append(f"### Your Memories ({agent_id})")
        for mem in agent_memories:
            importance_marker = "*" * min(mem.get("importance", 5), 5)
            lines.append(f"- [{mem['memory_type']}] {importance_marker} {mem['content']}")
        lines.append("")

    if team_memories:
        lines.append("### Team Shared Knowledge")
        for mem in team_memories:
            importance_marker = "*" * min(mem.get("importance", 5), 5)
            lines.append(f"- [{mem['memory_type']}] {importance_marker} {mem['content']}")
        lines.append("")

    return "\n".join(lines)


def record_collaboration(project_id: str, agent_a_id: str, agent_b_id: str,
                         collaboration_type: str, task_id: str = None,
                         workflow_id: str = None, outcome: str = None,
                         lesson_learned: str = None, duration_ms: int = None,
                         db_path=None) -> int:
    """Record a collaboration event in agent_collaboration_history.

    Args:
        project_id: Project scope.
        agent_a_id: First participating agent.
        agent_b_id: Second participating agent.
        collaboration_type: Type of collaboration (review, debate, consensus, etc.).
        task_id: Associated task ID (optional).
        workflow_id: Associated workflow ID (optional).
        outcome: Outcome of collaboration (optional).
        lesson_learned: Key takeaway (optional).
        duration_ms: Duration in milliseconds (optional).
        db_path: Optional database path override.

    Returns:
        The collaboration history record ID.
    """
    conn = _get_db(db_path)
    try:
        cursor = conn.execute(
            """INSERT INTO agent_collaboration_history
               (project_id, agent_a_id, agent_b_id, collaboration_type,
                task_id, workflow_id, outcome, lesson_learned, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, agent_a_id, agent_b_id, collaboration_type,
             task_id, workflow_id, outcome, lesson_learned, duration_ms),
        )
        conn.commit()
        record_id = cursor.lastrowid
        logger.info("Collaboration #%d recorded: %s <-> %s [%s] outcome=%s",
                     record_id, agent_a_id, agent_b_id, collaboration_type, outcome)

        # If there's a lesson learned, also store it as a team memory
        if lesson_learned:
            store(
                agent_id="_team",
                project_id=project_id,
                memory_type="lesson_learned",
                content=lesson_learned,
                importance=7,
                task_id=task_id,
                related_agent_ids=[agent_a_id, agent_b_id],
                db_path=db_path,
            )

        return record_id
    finally:
        conn.close()


def prune(agent_id: str = None, project_id: str = None,
          max_age_days: int = 90, db_path=None) -> int:
    """Remove expired or old low-importance memories.

    Deletes memories that:
    1. Have an expires_at that has passed, OR
    2. Are older than max_age_days AND have importance <= 3

    High-importance memories (>= 7) are never pruned by age.

    Args:
        agent_id: Filter by agent (optional).
        project_id: Filter by project (optional).
        max_age_days: Age threshold for low-importance pruning (default 90).
        db_path: Optional database path override.

    Returns:
        Number of memories deleted.
    """
    conn = _get_db(db_path)
    try:
        cutoff_date = (datetime.utcnow() - timedelta(days=max_age_days)).isoformat()

        # Build the WHERE clause
        conditions = [
            "(expires_at IS NOT NULL AND expires_at < datetime('now'))",
            f"(created_at < ? AND importance <= 3)",
        ]
        base_condition = f"({conditions[0]} OR {conditions[1]})"
        params: list = [cutoff_date]

        extra_conditions = []
        if agent_id:
            extra_conditions.append("agent_id = ?")
            params.append(agent_id)
        if project_id:
            extra_conditions.append("project_id = ?")
            params.append(project_id)

        where_clause = base_condition
        if extra_conditions:
            where_clause += " AND " + " AND ".join(extra_conditions)

        # Never prune high-importance memories by age
        where_clause += " AND NOT (importance >= 7 AND expires_at IS NULL)"

        sql = f"DELETE FROM agent_memory WHERE {where_clause}"

        cursor = conn.execute(sql, params)
        conn.commit()
        deleted = cursor.rowcount
        logger.info("Pruned %d memories (max_age=%d days, agent=%s, project=%s)",
                     deleted, max_age_days, agent_id or "*", project_id or "*")
        return deleted
    finally:
        conn.close()


def get_collaboration_history(project_id: str = None, agent_id: str = None,
                              limit: int = 50, db_path=None) -> list:
    """Get collaboration history records.

    Args:
        project_id: Filter by project (optional).
        agent_id: Filter by agent participation (optional).
        limit: Maximum records to return.
        db_path: Optional database path override.

    Returns:
        List of collaboration history dicts.
    """
    conn = _get_db(db_path)
    try:
        sql = "SELECT * FROM agent_collaboration_history WHERE 1=1"
        params: list = []

        if project_id:
            sql += " AND project_id = ?"
            params.append(project_id)
        if agent_id:
            sql += " AND (agent_a_id = ? OR agent_b_id = ?)"
            params.extend([agent_id, agent_id])

        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    """CLI for agent memory operations."""
    parser = argparse.ArgumentParser(
        description="ICDEV Agent Memory — scoped knowledge storage per agent and project"
    )
    sub = parser.add_subparsers(dest="command", help="Memory command")

    # Store
    p_store = sub.add_parser("store", help="Store a memory entry")
    p_store.add_argument("--agent-id", required=True, help="Agent ID (or '_team' for shared)")
    p_store.add_argument("--project-id", required=True, help="Project ID")
    p_store.add_argument("--type", required=True, choices=VALID_MEMORY_TYPES,
                         help="Memory type")
    p_store.add_argument("--content", required=True, help="Memory content")
    p_store.add_argument("--importance", type=int, default=5, help="Importance 1-10")
    p_store.add_argument("--task-id", help="Associated task ID")
    p_store.add_argument("--related-agents", help="Comma-separated related agent IDs")
    p_store.add_argument("--expires", help="ISO timestamp for expiration")

    # Recall
    p_recall = sub.add_parser("recall", help="Recall memories")
    p_recall.add_argument("--agent-id", required=True, help="Agent ID")
    p_recall.add_argument("--project-id", required=True, help="Project ID")
    p_recall.add_argument("--query", help="Keyword search")
    p_recall.add_argument("--type", dest="mem_type", choices=VALID_MEMORY_TYPES,
                          help="Filter by type")
    p_recall.add_argument("--limit", type=int, default=10, help="Max results")

    # Recall team
    p_team = sub.add_parser("recall-team", help="Recall team-shared memories")
    p_team.add_argument("--project-id", required=True, help="Project ID")
    p_team.add_argument("--query", help="Keyword search")
    p_team.add_argument("--type", dest="mem_type", choices=VALID_MEMORY_TYPES,
                        help="Filter by type")
    p_team.add_argument("--limit", type=int, default=10, help="Max results")

    # Inject context
    p_inject = sub.add_parser("inject", help="Build context string for prompt injection")
    p_inject.add_argument("--agent-id", required=True, help="Agent ID")
    p_inject.add_argument("--project-id", required=True, help="Project ID")
    p_inject.add_argument("--max-memories", type=int, default=5, help="Max memories")

    # Prune
    p_prune = sub.add_parser("prune", help="Prune expired/old memories")
    p_prune.add_argument("--agent-id", help="Filter by agent")
    p_prune.add_argument("--project-id", help="Filter by project")
    p_prune.add_argument("--max-age-days", type=int, default=90, help="Age threshold")

    # Collaboration history
    p_collab = sub.add_parser("collaborations", help="View collaboration history")
    p_collab.add_argument("--project-id", help="Filter by project")
    p_collab.add_argument("--agent-id", help="Filter by agent")
    p_collab.add_argument("--limit", type=int, default=50, help="Max results")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "store":
        related = [a.strip() for a in args.related_agents.split(",")] if args.related_agents else None
        memory_id = store(
            agent_id=args.agent_id,
            project_id=args.project_id,
            memory_type=args.type,
            content=args.content,
            importance=args.importance,
            task_id=args.task_id,
            related_agent_ids=related,
            expires_at=args.expires,
        )
        print(json.dumps({"memory_id": memory_id, "status": "stored"}, indent=2))

    elif args.command == "recall":
        memories = recall(
            agent_id=args.agent_id,
            project_id=args.project_id,
            query=args.query,
            memory_type=args.mem_type,
            limit=args.limit,
        )
        print(json.dumps(memories, indent=2, default=str))

    elif args.command == "recall-team":
        memories = recall_team(
            project_id=args.project_id,
            query=args.query,
            memory_type=args.mem_type,
            limit=args.limit,
        )
        print(json.dumps(memories, indent=2, default=str))

    elif args.command == "inject":
        context_str = inject_context(
            agent_id=args.agent_id,
            project_id=args.project_id,
            max_memories=args.max_memories,
        )
        if context_str:
            print(context_str)
        else:
            print("(no memories found for context injection)")

    elif args.command == "prune":
        count = prune(
            agent_id=args.agent_id,
            project_id=args.project_id,
            max_age_days=args.max_age_days,
        )
        print(json.dumps({"pruned": count, "max_age_days": args.max_age_days}, indent=2))

    elif args.command == "collaborations":
        history = get_collaboration_history(
            project_id=args.project_id,
            agent_id=args.agent_id,
            limit=args.limit,
        )
        print(json.dumps(history, indent=2, default=str))


if __name__ == "__main__":
    main()
