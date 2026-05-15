---
ontology_id: icdev:mission:m05-mcp-protocol:step:1
step_class: icdev:Lesson
---

# What is MCP?

MCP — the Model Context Protocol — is the **USB standard for AI**. Before MCP, every agent framework had its own tool format, its own transport, its own authentication scheme. Integrating one AI tool with ten different agents meant writing ten different adapters.

MCP is a single, open protocol that defines how AI models discover and call external tools. Once a tool is wrapped as an MCP server, any MCP-compatible client (Claude, ICDEV agents, LangChain, etc.) can use it without modification.

## The three primitives

| Primitive | What it is | Example |
|-----------|-----------|---------|
| **Tool** | A function the model can call | `search_documents(query)` |
| **Resource** | A data source the model can read | `file://ssp-draft.md` |
| **Prompt** | A reusable prompt template | `stig_analysis_prompt` |

## How a tool call flows

```
Model (client)                    MCP Server (tool provider)
     │                                      │
     │─── tools/list ──────────────────────▶│
     │◀── [{name, description, schema}] ────│
     │                                      │
     │─── tools/call {name, args} ─────────▶│
     │                                      │─── execute tool()
     │                                      │◀── result
     │◀── {content: result} ────────────────│
```

The transport layer is typically JSON-RPC over stdio (local) or HTTP/SSE (remote). ICDEV's `tools/goagiq/mcp-fastapi-server/` implements the HTTP variant.

## Why MCP matters for DoD/GovCon

1. **Air-gap compatible** — stdio transport needs no network access
2. **Tool isolation** — each capability is a separate server, separately audited
3. **Authorization boundary** — your compliance scanner runs in its own process, separate from the model
4. **Standardization** — ICDEV tools registered as MCP servers are reusable across all ICDEV agents

## Your task

Implement a minimal MCP tool server that exposes a compliance checking tool, then implement the MCP client that discovers and calls it.
