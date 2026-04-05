# Cursor MCP Setup for ICDEV™

## Steps
1. Open Cursor Settings (Cmd/Ctrl + ,)
2. Search for "MCP"
3. Add each server below:

## Servers
  - **icdev-unified**: `python tools/mcp/unified_server.py`
  - **playwright**: `docker run -i --rm --init --pull=never --add-host=host.docker.internal:host-gateway mcr.microsoft.com/playwright/mcp:latest --headless --isolated --caps=vision`

## Quick Setup
Copy `.mcp.json` to your Cursor MCP configuration. Cursor uses the same
JSON format as Claude Code for MCP server definitions.

## Environment Variables
Set for each server:
- `ICDEV_DB_PATH`: `data/icdev.db`
- `ICDEV_PROJECT_ROOT`: `.`
