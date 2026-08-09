# MCP Servers

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## MCP Servers
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| MCP Base Server | tools/mcp/base_server.py | Base MCP server class (JSON-RPC 2.0 stdio) | — | — |
| MCP Toolset Profiles | tools/mcp/toolset_profiles.py | Curated MCP toolset profiles for external-agent consumption (sag-mcp-01). Loads `args/mcp_toolset_profiles.yaml`, resolves a profile to a bounded validated tool set, and fail-closed CUI-egress gate (`local_only` profiles refused on cloud LLMs). Used by `unified_server.py --toolset`. | profile name | tool-name set |
| MCP Core Server | tools/mcp/core_server.py | Project management MCP server | stdio | JSON-RPC responses |
| MCP Compliance Server | tools/mcp/compliance_server.py | Compliance artifact MCP server | stdio | JSON-RPC responses |
| MCP Builder Server | tools/mcp/builder_server.py | Code generation MCP server | stdio | JSON-RPC responses |
| MCP Infra Server | tools/mcp/infra_server.py | Infrastructure MCP server | stdio | JSON-RPC responses |
| MCP Knowledge Server | tools/mcp/knowledge_server.py | Knowledge base MCP server | stdio | JSON-RPC responses |
| MCP Maintenance Server | tools/mcp/maintenance_server.py | Maintenance audit MCP server (scan, check, audit, remediate) | stdio | JSON-RPC responses |
| MCP MBSE Server | tools/mcp/mbse_server.py | MBSE MCP server (import, trace, generate, sync, assess, snapshot) | stdio | JSON-RPC responses |
| MCP Modernization Server | tools/mcp/modernization_server.py | Modernization MCP server (10 tools: register, analyze, assess, plan, generate, track, migrate) | stdio | JSON-RPC responses |
| MCP DevSecOps Server | tools/mcp/devsecops_server.py | DevSecOps/ZTA MCP server (12 tools: profile, maturity, pipeline, policy, mesh, segmentation, attestation, posture) | stdio | JSON-RPC responses |
| MCP Innovation Server | tools/mcp/innovation_server.py | Innovation Engine MCP server (10 tools: scan, score, triage, trends, generate, pipeline, status, introspect, competitive, standards) | stdio | JSON-RPC responses |
| MCP Context Server | tools/mcp/context_server.py | Semantic Layer MCP server (D277): CLAUDE.md section indexer, keyword search, role-tailored context, project/agent metadata | stdio | JSON-RPC responses |
| MCP Gateway Server | tools/mcp/gateway_server.py | Remote Command Gateway MCP server (5 tools: bind_user, list_bindings, revoke, send_command, status) | stdio | JSON-RPC responses |


## MCP Security
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| MCP Scanner | tools/mcp/mcp_scanner.py | Scans MCP server configs for vulnerability patterns (unauthenticated_transport, no_tls, wildcard_tool_names, missing_classification, privilege_escalation). `scan_mcp_servers(config_path=None) -> dict`. CLI: `--config PATH --json` | args/mcp_config.yaml or .mcp.json | JSON findings report |

## MCP Servers (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| LLM Proxy Server | tools/mcp/llm_proxy_server.py | LLM proxy MCP server for multi-provider routing | (server) | MCP endpoints |
| LSP Server | tools/mcp/lsp_server.py | Language Server Protocol MCP server | (server) | MCP endpoints |
| Outbound MCP Client | `tools/mcp_client/client.py` | Call tools on THIRD-PARTY MCP servers -- the inverse of `tools/mcp/`, which makes ICDEV a server. Two stdlib transports: `StdioTransport` (warm subprocess, newline-delimited JSON-RPC) and `HttpTransport` (JSON-RPC POST, gated by `egress_guard` before the socket opens). Deliberately stdlib rather than the `mcp` SDK so air-gapped deployments need no new dependency. `build_transport(spec) -> transport | None`. Every entry point degrades to None; none raises into an agent loop. |
| External MCP Registry | `tools/mcp_client/registry.py` | Registry and gate for external MCP tools. `get_external_registry() -> ExternalToolRegistry` with `.discover()`, `.list_tools()`, `.call(namespaced_name, arguments, classification=...)`. Four controls: tools are namespaced `ext__<server>__<tool>` so a remote server cannot shadow an ICDEV tool; only tools in the per-server `tools:` allowlist are exposed; arguments are checked against the server`s `classification_ceiling` before connecting; and air-gap mode disables every server regardless of config. Config: `args/external_mcp_servers.yaml` (disabled by default, no endpoints shipped). **Consumed by** the agent runtime (hgx-fed-01): `agent_runtime/discovery.py` surfaces allowlisted tools as `external` specs and `agent_runtime/dispatch.py` routes `ext__` calls back through `.call()`, so the controls above are enforced from the agent loop without being duplicated in it. |
| MCP Description Sanitiser | `tools/mcp_client/sanitize.py` | Neutralises attacker-controlled text from remote MCP servers. A remote server supplies its own tool names and descriptions, and those descriptions are injected into an agent prompt -- untrusted text entering the reasoning loop of an agent holding credentials, arriving whether or not the tool is called. `sanitize_description(raw, server=...)` strips fake role turns, `<|..|>`/`[INST]` markers, HTML comments and fences, collapses imperative override phrases, caps length, and frames the remainder as an untrusted quotation. `sanitize_tool_name(raw)` reduces names to `[a-z0-9_]`. `is_suspicious(raw)` reports for logging and never blocks. |
