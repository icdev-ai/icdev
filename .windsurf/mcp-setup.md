# Windsurf MCP Setup for ICDEV™

## Steps
1. Open Windsurf Settings > Cascade > MCP
2. Add each server below:

## Servers
  - **icdev-unified**: `python tools/mcp/unified_server.py`
  - **playwright**: `cmd /c npx -y @playwright/mcp@latest`

## Environment Variables
- `ICDEV_DB_PATH`: `data/icdev.db`
- `ICDEV_PROJECT_ROOT`: `.`
