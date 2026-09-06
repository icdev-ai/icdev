# Windsurf MCP Setup for ICDEV™

## Steps
1. Open Windsurf Settings > Cascade > MCP
2. Add each server below:

## Servers
  - **icdev-unified**: `python C:/AI/ICDev/tools/mcp/unified_server.py`
  - **playwright**: `node ${PLAYWRIGHT_MCP_CLI:-C:/Users/schuo/AppData/Roaming/npm/node_modules/@playwright/mcp/cli.js} --browser ${PLAYWRIGHT_MCP_BROWSER:-chromium}`

## Environment Variables
- `ICDEV_DB_PATH`: `data/icdev.db`
- `ICDEV_PROJECT_ROOT`: `.`
