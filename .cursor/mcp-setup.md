# Cursor MCP Setup for ICDEV™

## Steps
1. Open Cursor Settings (Cmd/Ctrl + ,)
2. Search for "MCP"
3. Add each server below:

## Servers
  - **icdev-unified**: `python C:/AI/ICDev/tools/mcp/mcp_debug_wrapper.py`
  - **playwright**: `cmd /c npx -y @playwright/mcp@latest`

## Quick Setup
Copy `.mcp.json` to your Cursor MCP configuration. Cursor uses the same
JSON format as Claude Code for MCP server definitions.

## Environment Variables
Set for each server:
- `ICDEV_DB_PATH`: `data/icdev.db`
- `ICDEV_PROJECT_ROOT`: `.`
