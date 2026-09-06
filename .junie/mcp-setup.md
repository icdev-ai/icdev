# JetBrains MCP Setup for ICDEV™

## Steps
1. Open Settings > Tools > AI Assistant > Model Context Protocol (MCP)
2. Click "+" to add each server
3. Select "stdio" transport

## Servers
  - **icdev-unified**: `python C:/AI/ICDev/tools/mcp/unified_server.py`
  - **playwright**: `node ${PLAYWRIGHT_MCP_CLI:-C:/Users/schuo/AppData/Roaming/npm/node_modules/@playwright/mcp/cli.js} --browser ${PLAYWRIGHT_MCP_BROWSER:-chromium}`

## Environment Variables
- `ICDEV_DB_PATH`: `data/icdev.db`
- `ICDEV_PROJECT_ROOT`: `.`
