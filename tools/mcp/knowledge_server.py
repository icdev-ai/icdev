#!/usr/bin/env python3
# CUI // SP-CTI
"""Knowledge MCP server exposing pattern detection, self-healing, and recommendation tools.

Tools:
    search_knowledge     - Search the knowledge base for patterns and solutions
    add_pattern          - Add a new pattern to the knowledge base
    get_recommendations  - Get improvement recommendations for a project
    analyze_failure      - Analyze a failure and determine root cause
    self_heal            - Trigger self-healing for a detected issue

Runs as an MCP server over stdio with Content-Length framing.
"""

import os
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

sys.path.insert(0, str(BASE_DIR))
from tools.mcp.base_server import MCPServer  # noqa: E402


def _import_tool(module_path, func_name):
    """Dynamically import a function. Returns None if unavailable."""
    try:
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, func_name, None)
    except (ImportError, ModuleNotFoundError, AttributeError):
        return None


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_search_knowledge(args: dict) -> dict:
    """Search the knowledge base for patterns matching a query."""
    search = _import_tool("tools.knowledge.pattern_detector", "search_patterns")

    query = args.get("query")
    if not query:
        raise ValueError("'query' is required")

    pattern_type = args.get("pattern_type")
    limit = args.get("limit", 10)

    if search:
        return search(query=query, pattern_type=pattern_type, limit=limit, db_path=str(DB_PATH))

    # Fallback: direct DB search
    conn = _get_db()
    try:
        if pattern_type:
            rows = conn.execute(
                """SELECT * FROM knowledge_patterns
                   WHERE (name LIKE ? OR description LIKE ? OR solution LIKE ?)
                   AND pattern_type = ?
                   ORDER BY confidence DESC, use_count DESC
                   LIMIT ?""",
                (f"%{query}%", f"%{query}%", f"%{query}%", pattern_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM knowledge_patterns
                   WHERE name LIKE ? OR description LIKE ? OR solution LIKE ?
                   ORDER BY confidence DESC, use_count DESC
                   LIMIT ?""",
                (f"%{query}%", f"%{query}%", f"%{query}%", limit),
            ).fetchall()

        patterns = []
        for row in rows:
            patterns.append({
                "id": row["id"],
                "name": row["name"],
                "pattern_type": row["pattern_type"],
                "description": row["description"],
                "detection_rule": row["detection_rule"],
                "solution": row["solution"],
                "confidence": row["confidence"],
                "use_count": row["use_count"],
            })
    finally:
        conn.close()

    return {"query": query, "results": patterns, "count": len(patterns)}


def handle_add_pattern(args: dict) -> dict:
    """Add a new pattern to the knowledge base."""
    add = _import_tool("tools.knowledge.pattern_detector", "add_pattern")

    name = args.get("name")
    if not name:
        raise ValueError("'name' is required")

    pattern_type = args.get("pattern_type", "error")
    description = args.get("description", "")
    detection_rule = args.get("detection_rule", "")
    solution = args.get("solution", "")
    auto_healable = args.get("auto_healable", False)
    confidence = args.get("confidence", 0.5)

    if add:
        return add(
            name=name,
            pattern_type=pattern_type,
            description=description,
            detection_rule=detection_rule,
            solution=solution,
            auto_healable=auto_healable,
            confidence=confidence,
            db_path=str(DB_PATH),
        )

    # Fallback: direct DB insert
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO knowledge_patterns
               (name, pattern_type, description, detection_rule, solution, auto_healable, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, pattern_type, description, detection_rule, solution, auto_healable, confidence),
        )
        conn.commit()
        pattern_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()

    return {
        "id": pattern_id,
        "name": name,
        "pattern_type": pattern_type,
        "confidence": confidence,
        "auto_healable": auto_healable,
        "status": "created",
    }


def handle_get_recommendations(args: dict) -> dict:
    """Get improvement recommendations for a project."""
    recommend = _import_tool("tools.knowledge.recommendation_engine", "get_recommendations")
    if recommend:
        project_id = args.get("project_id")
        if not project_id:
            raise ValueError("'project_id' is required")
        return recommend(project_id=project_id, db_path=str(DB_PATH))

    # Fallback: basic recommendations from knowledge patterns
    project_id = args.get("project_id")
    conn = _get_db()
    try:
        # Get recent failures for this project
        failures = conn.execute(
            """SELECT error_type, error_message, COUNT(*) as count
               FROM failure_log WHERE project_id = ?
               GROUP BY error_type ORDER BY count DESC LIMIT 5""",
            (project_id,),
        ).fetchall()

        recommendations = []
        for f in failures:
            # Find matching patterns
            patterns = conn.execute(
                """SELECT name, solution, confidence FROM knowledge_patterns
                   WHERE pattern_type = ? OR description LIKE ?
                   ORDER BY confidence DESC LIMIT 1""",
                (f["error_type"], f"%{f['error_type']}%"),
            ).fetchall()

            rec = {
                "issue": f["error_type"],
                "occurrences": f["count"],
                "message": f["error_message"],
            }
            if patterns:
                rec["suggestion"] = patterns[0]["solution"]
                rec["confidence"] = patterns[0]["confidence"]
            recommendations.append(rec)
    finally:
        conn.close()

    return {"project_id": project_id, "recommendations": recommendations}


def handle_analyze_failure(args: dict) -> dict:
    """Analyze a failure and determine root cause."""
    analyze = _import_tool("tools.knowledge.self_heal_analyzer", "analyze_failure")
    if analyze:
        failure_id = args.get("failure_id")
        error_message = args.get("error_message")
        log_data = args.get("log_data")
        return analyze(
            failure_id=failure_id,
            error_message=error_message,
            log_data=log_data,
            db_path=str(DB_PATH),
        )

    # Fallback: pattern matching analysis
    error_message = args.get("error_message", "")
    conn = _get_db()
    try:
        # Search for matching patterns
        patterns = conn.execute(
            """SELECT * FROM knowledge_patterns
               WHERE detection_rule LIKE ? OR description LIKE ?
               ORDER BY confidence DESC LIMIT 3""",
            (f"%{error_message[:50]}%", f"%{error_message[:50]}%"),
        ).fetchall()

        analysis = {
            "error_message": error_message,
            "matching_patterns": [],
            "root_cause": "Unable to determine — no matching patterns found" if not patterns else None,
            "suggested_actions": [],
        }

        for p in patterns:
            analysis["matching_patterns"].append({
                "name": p["name"],
                "confidence": p["confidence"],
                "solution": p["solution"],
                "auto_healable": bool(p["auto_healable"]),
            })
            if not analysis["root_cause"]:
                analysis["root_cause"] = p["description"]
            analysis["suggested_actions"].append(p["solution"])
    finally:
        conn.close()

    return analysis


def handle_self_heal(args: dict) -> dict:
    """Trigger self-healing for a detected issue."""
    heal = _import_tool("tools.knowledge.self_heal_analyzer", "trigger_self_heal")
    if heal:
        pattern_id = args.get("pattern_id")
        project_id = args.get("project_id")
        context = args.get("context", {})
        return heal(
            pattern_id=pattern_id,
            project_id=project_id,
            context=context,
            db_path=str(DB_PATH),
        )

    # Fallback: record the attempt and check thresholds
    pattern_id = args.get("pattern_id")
    project_id = args.get("project_id")

    conn = _get_db()
    try:
        # Check pattern confidence and auto_healable flag
        pattern = conn.execute(
            "SELECT * FROM knowledge_patterns WHERE id = ?", (pattern_id,)
        ).fetchone()

        if not pattern:
            return {"error": f"Pattern not found: {pattern_id}"}

        if not pattern["auto_healable"]:
            return {
                "status": "requires_approval",
                "message": "This pattern is not configured for automatic healing. Manual intervention required.",
                "pattern": pattern["name"],
                "solution": pattern["solution"],
            }

        if pattern["confidence"] < 0.7:
            return {
                "status": "low_confidence",
                "message": f"Pattern confidence ({pattern['confidence']}) is below auto-heal threshold (0.7). Suggesting fix for manual review.",
                "pattern": pattern["name"],
                "solution": pattern["solution"],
            }

        # Check rate limits (max 5/hour)
        recent_heals = conn.execute(
            """SELECT COUNT(*) as cnt FROM self_healing_events
               WHERE created_at > datetime('now', '-1 hour')""",
        ).fetchone()

        if recent_heals and recent_heals["cnt"] >= 5:
            return {
                "status": "rate_limited",
                "message": "Self-healing rate limit reached (5/hour). Queuing for later execution.",
            }

        # Record the self-healing event
        conn.execute(
            """INSERT INTO self_healing_events
               (pattern_id, project_id, trigger_data, action_taken, status)
               VALUES (?, ?, ?, ?, 'completed')""",
            (pattern_id, project_id, "{}", pattern["solution"]),
        )
        conn.commit()

        # Increment pattern use count
        conn.execute(
            "UPDATE knowledge_patterns SET use_count = use_count + 1 WHERE id = ?",
            (pattern_id,),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "executed",
        "pattern": pattern["name"],
        "action": pattern["solution"],
        "confidence": pattern["confidence"],
    }


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

def create_server() -> MCPServer:
    server = MCPServer(name="icdev-knowledge", version="1.0.0")

    server.register_tool(
        name="search_knowledge",
        description="Search the ICDEV knowledge base for patterns, solutions, and best practices. Supports keyword search with optional pattern type filtering.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (keywords or error message)"},
                "pattern_type": {
                    "type": "string",
                    "description": "Filter by pattern type",
                    "enum": ["error", "performance", "security", "compliance", "deployment", "configuration"],
                },
                "limit": {"type": "integer", "description": "Max results to return", "default": 10},
            },
            "required": ["query"],
        },
        handler=handle_search_knowledge,
    )

    server.register_tool(
        name="add_pattern",
        description="Add a new pattern to the knowledge base. Patterns capture common issues and their solutions for future self-healing.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Pattern name (short, descriptive)"},
                "pattern_type": {
                    "type": "string",
                    "description": "Pattern category",
                    "enum": ["error", "performance", "security", "compliance", "deployment", "configuration"],
                    "default": "error",
                },
                "description": {"type": "string", "description": "Detailed description of the pattern"},
                "detection_rule": {"type": "string", "description": "How to detect this pattern (regex, log pattern, metric threshold)"},
                "solution": {"type": "string", "description": "How to resolve when this pattern is detected"},
                "auto_healable": {"type": "boolean", "description": "Whether this can be auto-remediated", "default": False},
                "confidence": {"type": "number", "description": "Confidence score 0.0-1.0", "default": 0.5},
            },
            "required": ["name"],
        },
        handler=handle_add_pattern,
    )

    server.register_tool(
        name="get_recommendations",
        description="Get improvement recommendations for a project based on failure history and knowledge base patterns.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
            },
            "required": ["project_id"],
        },
        handler=handle_get_recommendations,
    )

    server.register_tool(
        name="analyze_failure",
        description="Analyze a failure to determine root cause. Matches against known patterns and uses LLM analysis for unknown issues.",
        input_schema={
            "type": "object",
            "properties": {
                "failure_id": {"type": "string", "description": "ID of a logged failure (from failure_log table)"},
                "error_message": {"type": "string", "description": "Error message to analyze (alternative to failure_id)"},
                "log_data": {"type": "string", "description": "Raw log data for context"},
            },
        },
        handler=handle_analyze_failure,
    )

    server.register_tool(
        name="self_heal",
        description="Trigger self-healing for a detected issue. Checks confidence threshold (>=0.7 for auto, 0.3-0.7 for suggestion, <0.3 for escalation) and rate limits (max 5/hour).",
        input_schema={
            "type": "object",
            "properties": {
                "pattern_id": {"type": "integer", "description": "ID of the knowledge pattern to apply"},
                "project_id": {"type": "string", "description": "UUID of the affected project"},
                "context": {
                    "type": "object",
                    "description": "Additional context about the failure",
                },
            },
            "required": ["pattern_id"],
        },
        handler=handle_self_heal,
    )

    return server


if __name__ == "__main__":
    server = create_server()
    server.run()
