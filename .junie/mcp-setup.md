# JetBrains MCP Setup for ICDEV

## Steps
1. Open Settings > Tools > AI Assistant > Model Context Protocol (MCP)
2. Click "+" to add each server
3. Select "stdio" transport

## Servers
  - **playwright**: `npx @playwright/mcp@latest --isolated --config ./playwright-mcp-config.json --output-dir playwright`
  - **icdev-core**: `python tools/mcp/core_server.py`
  - **icdev-compliance**: `python tools/mcp/compliance_server.py`
  - **icdev-builder**: `python tools/mcp/builder_server.py`
  - **icdev-infra**: `python tools/mcp/infra_server.py`
  - **icdev-knowledge**: `python tools/mcp/knowledge_server.py`
  - **icdev-maintenance**: `python tools/mcp/maintenance_server.py`
  - **icdev-mbse**: `python tools/mcp/mbse_server.py`
  - **icdev-modernization**: `python tools/mcp/modernization_server.py`
  - **icdev-requirements**: `python tools/mcp/requirements_server.py`
  - **icdev-supply-chain**: `python tools/mcp/supply_chain_server.py`
  - **icdev-simulation**: `python tools/mcp/simulation_server.py`
  - **icdev-integration**: `python tools/mcp/integration_server.py`
  - **icdev-marketplace**: `python tools/mcp/marketplace_server.py`
  - **icdev-devsecops**: `python tools/mcp/devsecops_server.py`
  - **icdev-gateway**: `python tools/mcp/gateway_server.py`
  - **icdev-context**: `python tools/mcp/context_server.py`
  - **icdev-innovation**: `python tools/mcp/innovation_server.py`
  - **icdev-observability**: `python tools/mcp/observability_server.py`

## Environment Variables
- `ICDEV_DB_PATH`: `data/icdev.db`
- `ICDEV_PROJECT_ROOT`: `.`
