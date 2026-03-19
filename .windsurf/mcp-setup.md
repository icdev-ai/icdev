# Windsurf MCP Setup for ICDEV

## Steps
1. Open Windsurf Settings > Cascade > MCP
2. Add each server below:

## Servers
  - **icdev-unified**: `python tools/mcp/unified_server.py`
  - **playwright**: `docker run -i --rm --init --pull=never --add-host=host.docker.internal:host-gateway mcr.microsoft.com/playwright/mcp:latest --headless --isolated --caps=vision`

## Environment Variables
- `ICDEV_DB_PATH`: `data/icdev.db`
- `ICDEV_PROJECT_ROOT`: `.`
