# Windsurf MCP Setup for ICDEV

## Steps
1. Open Windsurf Settings > Cascade > MCP
2. Add each server below:

## Servers
  - **icdev-unified**: `python tools/mcp/unified_server.py`
  - **playwright**: `npx @playwright/mcp@latest --isolated --config ./playwright-mcp-config.json --output-dir playwright`

## Environment Variables
- `ICDEV_DB_PATH`: `data/icdev.db`
- `ICDEV_PROJECT_ROOT`: `.`
