# SaaS (Additional)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## SaaS (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| MCP SSE | tools/saas/mcp_sse.py | MCP Server-Sent Events transport for SaaS | (server) | SSE stream |
| Tenant LLM Keys | tools/saas/tenant_llm_keys.py | Per-tenant LLM API key management | --json | Key management results |

| OpenAI-compatible endpoint | tools/saas/openai_compat.py | Governed /v1/models + /v1/chat/completions over LLMRouter for Open WebUI and other OpenAI clients (owui-api-01, D-OWUI-1); capabilities in args/openai_compat.yaml | (blueprint on SaaS gateway) | OpenAI chat.completion / SSE |
| MCP Authz Survey | tools/saas/mcp_authz_survey.py | Would-deny rate of MCPToolAuthorizer for one MCP HTTP client (X-ICDEV-Client), from per-call mcp.tool.call rows in audit_platform; exit 2 when unmeasurable (owui-mcp-02) | --caller, --days, --json | Survey JSON |
