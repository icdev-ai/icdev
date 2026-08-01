# CUI // SP-CTI
"""AADC → MCP Tool Registry Sync.

Reads agent/tool nodes from an AADC design and upserts them into
mcp_tool_registry so the MCP gateway is aware of them.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agentic_ai_canvas.mcp_sync")


# Node types that represent tools/agents the MCP gateway should know about
_SYNCABLE_TYPES = frozenset({
    "llm",
    "llm-local",
    "autonomous-agent",
    "orchestrator",
    "sub-agent",
    "researcher-agent",
    "writer-agent",
    "reviewer-agent",
    "mcp-server",
    "mcp-gateway",
    "tool-chain",
    "external-api",
})


def sync_design_to_mcp(design_id: str) -> Dict[str, Any]:
    """Upsert MCP tool registry rows for each agent/tool node in the design.

    Returns {"synced": N, "nodes": [list of synced node labels]}.
    Non-fatal: returns {"synced": 0, "error": str} on failure.
    """
    try:
        from tools.agentic_ai_canvas.db.init_db import get_connection as _aadc_conn
        from tools.db.storage import get_connection as _main_conn
    except ImportError as exc:
        return {"synced": 0, "error": f"import failed: {exc}"}

    # Load design graph
    conn = None
    try:
        conn = _aadc_conn()
        row = conn.execute(
            "SELECT graph_json, name FROM aadc_designs WHERE id=%s", (design_id,)
        ).fetchone()
    except Exception as exc:
        return {"synced": 0, "error": f"design load failed: {exc}"}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    if not row:
        return {"synced": 0, "error": "design not found"}

    try:
        graph = json.loads(row["graph_json"] or '{"nodes":[],"edges":[]}')
    except (json.JSONDecodeError, TypeError):
        graph = {"nodes": [], "edges": []}

    nodes_to_sync: List[dict] = [
        n for n in graph.get("nodes", [])
        if n.get("type", "") in _SYNCABLE_TYPES
    ]

    if not nodes_to_sync:
        return {"synced": 0, "nodes": []}

    source = f"aadc:{design_id}"
    synced_labels: List[str] = []
    now = datetime.now(timezone.utc).isoformat()

    mconn = None
    try:
        mconn = _main_conn()
        for node in nodes_to_sync:
            label = node.get("label") or node.get("id", "unknown")
            caps = json.dumps({
                "description": node.get("description", ""),
                "node_type": node.get("type", ""),
                "design_id": design_id,
            })
            # PostgreSQL upsert
            try:
                mconn.execute(
                    """
                    INSERT INTO mcp_tool_registry (name, source, capabilities_json, updated_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (name, source) DO UPDATE
                      SET capabilities_json = EXCLUDED.capabilities_json,
                          updated_at = EXCLUDED.updated_at
                    """,
                    (label, source, caps, now),
                )
                synced_labels.append(label)
            except Exception:
                # Fallback for SQLite / non-PG: try plain insert, ignore dup
                try:
                    mconn.execute(
                        "INSERT OR REPLACE INTO mcp_tool_registry "
                        "(name, source, capabilities_json, updated_at) VALUES (%s,%s,%s,%s)",
                        (label, source, caps, now),
                    )
                    synced_labels.append(label)
                except Exception as _exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
                    logger.warning(
                        "sync_design_to_mcp: best-effort INSERT into mcp_tool_registry failed (non-blocking): %s",
                        _exc,
                    )

        mconn.commit()
    except Exception as exc:
        return {"synced": 0, "error": f"registry write failed: {exc}"}
    finally:
        if mconn is not None:
            try:
                mconn.close()
            except Exception:
                pass

    return {"synced": len(synced_labels), "nodes": synced_labels}
