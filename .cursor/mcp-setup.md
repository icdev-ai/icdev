# Cursor MCP Setup for ICDEV™

## Steps
1. Open Cursor Settings (Cmd/Ctrl + ,)
2. Search for "MCP"
3. Add each server below:

## Servers
  - **icdev-unified**: `python C:/AI/ICDev/tools/mcp/unified_server.py`
  - **playwright**: `node ${PLAYWRIGHT_MCP_CLI:-C:/Users/schuo/AppData/Roaming/npm/node_modules/@playwright/mcp/cli.js} --browser ${PLAYWRIGHT_MCP_BROWSER:-chromium}`

## Quick Setup
Copy `.mcp.json` to your Cursor MCP configuration. Cursor uses the same
JSON format as Claude Code for MCP server definitions.

## Environment Variables
Set for each server:
- `ICDEV_DB_PATH`: `data/icdev.db`
- `ICDEV_PROJECT_ROOT`: `.`
