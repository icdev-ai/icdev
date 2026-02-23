#!/usr/bin/env python3
# CUI // SP-CTI
"""Semantic layer MCP server for on-demand context delivery (Phase 44 — D277).

Provides CLAUDE.md section retrieval, live system metadata, project context,
and role-tailored context as MCP tools over stdio.

Usage:
    python tools/mcp/context_server.py
"""

import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("icdev.context_server")

DB_PATH = BASE_DIR / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# Backend imports (graceful)
# ---------------------------------------------------------------------------

try:
    from tools.mcp.base_server import MCPServer
    _HAS_BASE = True
except ImportError:
    _HAS_BASE = False

try:
    from tools.mcp.context_indexer import ClaudeMdIndexer
    _indexer = ClaudeMdIndexer()
except ImportError:
    _indexer = None


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_fetch_docs(args: dict) -> dict:
    """Fetch CLAUDE.md section(s) by header name or keyword search.

    Args:
        section: Section header name (exact or partial match)
        keyword: Keyword to search across all sections
    """
    if not _indexer:
        return {"error": "Context indexer not available"}

    section_name = args.get("section", "")
    keyword = args.get("keyword", "")

    if section_name:
        content = _indexer.get_section(section_name)
        if content:
            return {"section": section_name, "content": content}
        return {"error": f"Section '{section_name}' not found"}

    if keyword:
        matches = _indexer.search_sections(keyword)
        results = []
        for name in matches[:5]:
            content = _indexer.get_section(name)
            results.append({"section": name, "content": content[:500] if content else ""})
        return {"keyword": keyword, "matches": results, "total": len(matches)}

    return {"error": "Provide 'section' or 'keyword' parameter"}


def handle_list_sections(args: dict) -> dict:
    """List all CLAUDE.md sections (table of contents)."""
    if not _indexer:
        return {"error": "Context indexer not available"}

    toc = _indexer.get_toc()
    return {"sections": toc, "total": len(toc)}


def handle_get_icdev_metadata(args: dict) -> dict:
    """Get live ICDEV system metadata.

    Returns project count, agent health, recent events, compliance posture,
    migration version, and system statistics.
    """
    metadata = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "classification": "CUI",
    }

    try:
        conn = _get_db()

        # Project count and status
        projects = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM projects GROUP BY status"
        ).fetchall()
        metadata["projects"] = {
            "total": sum(dict(r)["cnt"] for r in projects),
            "by_status": {dict(r)["status"]: dict(r)["cnt"] for r in projects},
        }

        # Agent health
        agents = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM agents GROUP BY status"
        ).fetchall()
        metadata["agents"] = {
            "total": sum(dict(r)["cnt"] for r in agents),
            "by_status": {dict(r)["status"]: dict(r)["cnt"] for r in agents},
        }

        # Recent events (last 10)
        events = conn.execute(
            "SELECT event_type, actor, action, created_at FROM audit_trail ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        metadata["recent_events"] = [dict(e) for e in events]

        # Compliance posture
        try:
            poam_open = conn.execute(
                "SELECT COUNT(*) as cnt FROM poam_items WHERE status = 'open'"
            ).fetchone()["cnt"]
            stig_open = conn.execute(
                "SELECT COUNT(*) as cnt FROM stig_findings WHERE status = 'Open'"
            ).fetchone()["cnt"]
            metadata["compliance"] = {
                "poam_open": poam_open,
                "stig_open": stig_open,
            }
        except sqlite3.OperationalError:
            metadata["compliance"] = {"poam_open": 0, "stig_open": 0}

        # Migration version
        try:
            ver = conn.execute(
                "SELECT version FROM schema_migrations ORDER BY applied_at DESC LIMIT 1"
            ).fetchone()
            metadata["migration_version"] = dict(ver)["version"] if ver else "unknown"
        except sqlite3.OperationalError:
            metadata["migration_version"] = "unknown"

        conn.close()
    except sqlite3.OperationalError as exc:
        metadata["error"] = f"DB not available: {exc}"

    return metadata


def handle_get_project_context(args: dict) -> dict:
    """Get project-specific context for agent use.

    Args:
        project_id: The project to get context for.
    """
    project_id = args.get("project_id", "")
    if not project_id:
        return {"error": "project_id required"}

    try:
        conn = _get_db()
        project = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not project:
            conn.close()
            return {"error": f"Project '{project_id}' not found"}

        project_dict = dict(project)

        # Get compliance status
        poam_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM poam_items WHERE project_id = ?", (project_id,)
        ).fetchone()["cnt"]

        # Get recent audit events
        events = conn.execute(
            "SELECT event_type, action, created_at FROM audit_trail WHERE project_id = ? ORDER BY created_at DESC LIMIT 5",
            (project_id,),
        ).fetchall()

        conn.close()

        return {
            "project": project_dict,
            "compliance": {"poam_items": poam_count},
            "recent_events": [dict(e) for e in events],
        }
    except sqlite3.OperationalError as exc:
        return {"error": str(exc)}


def handle_get_agent_context(args: dict) -> dict:
    """Get role-tailored context from CLAUDE.md.

    Args:
        role: Agent role (builder, compliance, security, architect, infrastructure, orchestrator)
    """
    role = args.get("role", "")
    if not role:
        return {"error": "role required"}

    if not _indexer:
        return {"error": "Context indexer not available"}

    content = _indexer.get_sections_for_role(role)
    if not content:
        return {"role": role, "content": "", "note": f"No sections mapped for role '{role}'"}

    return {"role": role, "content": content, "token_estimate": len(content) // 4}


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

def create_server():
    """Create and configure the context MCP server."""
    if not _HAS_BASE:
        logger.error("base_server.py not available — cannot start context server")
        return None

    server = MCPServer(
        name="icdev-context",
        version="1.0.0",
        description="Semantic layer — on-demand CLAUDE.md context, live metadata, role-tailored delivery",
    )

    server.register_tool(
        name="fetch_docs",
        description="Fetch CLAUDE.md section by header name or keyword search",
        handler=handle_fetch_docs,
        input_schema={
            "type": "object",
            "properties": {
                "section": {"type": "string", "description": "Section header name (exact or partial match)"},
                "keyword": {"type": "string", "description": "Keyword to search across all sections"},
            },
        },
    )

    server.register_tool(
        name="list_sections",
        description="List all CLAUDE.md sections (table of contents)",
        handler=handle_list_sections,
        input_schema={"type": "object", "properties": {}},
    )

    server.register_tool(
        name="get_icdev_metadata",
        description="Get live system metadata (projects, agents, compliance, events)",
        handler=handle_get_icdev_metadata,
        input_schema={"type": "object", "properties": {}},
    )

    server.register_tool(
        name="get_project_context",
        description="Get project-specific context for agent use",
        handler=handle_get_project_context,
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project ID"},
            },
            "required": ["project_id"],
        },
    )

    server.register_tool(
        name="get_agent_context",
        description="Get role-tailored CLAUDE.md sections for an agent",
        handler=handle_get_agent_context,
        input_schema={
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "description": "Agent role (builder, compliance, security, architect, infrastructure, orchestrator)",
                },
            },
            "required": ["role"],
        },
    )

    return server


if __name__ == "__main__":
    server = create_server()
    if server:
        server.run()
