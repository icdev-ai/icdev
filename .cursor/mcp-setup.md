# Cursor MCP Setup for ICDEV

## Steps
1. Open Cursor Settings (Cmd/Ctrl + ,)
2. Search for "MCP"
3. Add each server below:

## Servers
  - **icdev-unified**: `python tools/mcp/unified_server.py`
  - **playwright**: `npx @playwright/mcp@latest --isolated --config ./playwright-mcp-config.json --output-dir playwright`

## Quick Setup
Copy `.mcp.json` to your Cursor MCP configuration. Cursor uses the same
JSON format as Claude Code for MCP server definitions.

## Environment Variables
Set for each server:
- `ICDEV_DB_PATH`: `data/icdev.db`
- `ICDEV_PROJECT_ROOT`: `.`
