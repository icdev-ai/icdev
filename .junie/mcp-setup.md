# JetBrains MCP Setup for ICDEV™

## Steps
1. Open Settings > Tools > AI Assistant > Model Context Protocol (MCP)
2. Click "+" to add each server
3. Select "stdio" transport

## Servers
  - **icdev-unified**: `python tools/mcp/unified_server.py`
  - **playwright**: `cmd /c npx -y @playwright/mcp@latest`

## Environment Variables
- `ICDEV_DB_PATH`: `data/icdev.db`
- `ICDEV_PROJECT_ROOT`: `.`
