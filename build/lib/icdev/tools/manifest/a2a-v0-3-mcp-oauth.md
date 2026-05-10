# A2A v0.3 + MCP OAuth (Phase 55)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## A2A v0.3 + MCP OAuth (Phase 55)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| A2A Agent Card Generator | tools/agent/a2a_agent_card_generator.py | Generate v0.3 Agent Cards with capabilities, protocolVersion, tasks/sendSubscribe | --all, --agent-id, --json | Agent Cards JSON |
| A2A Discovery Server | tools/agent/a2a_discovery_server.py | Agent discovery endpoint serving /.well-known/agent.json for all 15 agents | (server) | JSON-RPC discovery |
| MCP OAuth | tools/saas/mcp_oauth.py | OAuth 2.1 + HMAC offline + JWT token verification for MCP transport. Elicitation handler. Task manager. | MCPOAuthVerifier, MCPElicitationHandler, MCPTaskManager | Token verification, elicitation, tasks |

