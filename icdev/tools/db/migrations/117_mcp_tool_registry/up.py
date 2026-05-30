#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 117 — mcp_tool_registry table.

Creates:
  mcp_tool_registry(id, name, source, capabilities_json, updated_at)
  UNIQUE (name, source)
  idx_mcp_tr_name   ON mcp_tool_registry(name)
  idx_mcp_tr_source ON mcp_tool_registry(source)

Mutable registry — tools/agentic_ai_canvas/mcp_sync.py upserts rows here
when syncing AADC design agent/tool nodes to the MCP gateway.

Idempotent: CREATE TABLE IF NOT EXISTS.
"""

MIGRATION_ID = "117"
MIGRATION_NAME = "mcp_tool_registry"
DESCRIPTION = "Create mcp_tool_registry table for MCP gateway tool discovery"

_DDL = """
CREATE TABLE IF NOT EXISTS mcp_tool_registry (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL,
    source            TEXT NOT NULL,
    capabilities_json TEXT,
    updated_at        TEXT NOT NULL,
    UNIQUE (name, source)
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_mcp_tr_name   ON mcp_tool_registry (name)",
    "CREATE INDEX IF NOT EXISTS idx_mcp_tr_source ON mcp_tool_registry (source)",
]


def up(conn=None) -> dict:
    from tools.db.storage import get_connection
    if conn is None:
        conn = get_connection()

    conn.execute(_DDL)
    for idx in _INDEXES:
        conn.execute(idx)
    conn.commit()
    return {"status": "applied", "actions": ["mcp_tool_registry_table", "indexes_created"]}


def down(conn=None) -> dict:
    from tools.db.storage import get_connection
    if conn is None:
        conn = get_connection()
    conn.execute("DROP TABLE IF EXISTS mcp_tool_registry")
    conn.commit()
    return {"status": "rolled_back"}


if __name__ == "__main__":
    result = up()
    print(result)
