# CUI // SP-CTI

# Consuming ICDEV Tools from an External Agent (MCP)

> **SAG sag-mcp-01.** How a non-Claude agent (e.g. Hermes on a local kimi/ollama
> model, or any MCP-capable client) consumes ICDEV's tools — and how to do it
> safely with respect to CUI egress.

## TL;DR — no bridge server needed

`tools/mcp/unified_server.py` already exposes **447+ ICDEV tools over stdio MCP**
and is consumed by Claude Code in production today. An external agent does **not**
need a bespoke bridge (the previously proposed `hermes_bridge_server.py` is
**unnecessary**). It simply registers the existing server as an MCP command:

```bash
# Full surface (447+ tools) — only viable for large-context agents
<agent> mcp add icdev --command "python tools/mcp/unified_server.py"

# Curated, bounded surface (recommended for small local models)
<agent> mcp add icdev --command "python tools/mcp/unified_server.py --toolset security"
```

DataBridge stays an **ETL** component — do **not** evolve it into a gateway.

## Curated toolset profiles

Small local models (limited context) choke on a 447-tool list. Request a
**bounded** profile from `args/mcp_toolset_profiles.yaml`:

```bash
python tools/mcp/unified_server.py --list-toolsets
```

| Profile | CUI egress | ~Tools | Use |
|---------|-----------|--------|-----|
| `minimal` | cloud_safe | 2 | Protocol handshake probe (health/status) |
| `research` | cloud_safe | 5 | RAG + KG retrieval, no artifacts |
| `security` | cloud_safe | 7 | Dependency/code/SBOM scan, CVE triage |
| `compliance` | **local_only** | 12 | NIST/FedRAMP/CMMC/STIG + ATO artifacts |
| `kanban` | **local_only** | 7 | Task-board read/write |

Select a profile by flag or env var:

```bash
python tools/mcp/unified_server.py --toolset compliance
# or
ICDEV_MCP_TOOLSET=compliance python tools/mcp/unified_server.py
```

With no `--toolset`, the full surface is exposed (the Claude Code path is
unchanged).

## CUI egress — the important part

An external agent may run on a **cloud LLM outside the ICDEV trust boundary**.
Tool *output* egresses to that model, so exposing CUI-capable tools to a cloud
agent would leak CUI. ICDEV applies the same rule the CLI bridge uses — `ollama`
= local, everything else = cloud:

- Profiles marked **`cui_egress: local_only`** (e.g. `compliance`, `kanban`) may
  surface tools that emit CUI-marked artifacts or read classification-bearing
  data. `enforce_cui_egress()` runs **before any tool is registered** and
  **refuses** such a profile when a cloud provider is detected.
- Provider detection reads `ICDEV_LLM_PROVIDER` (or `ICDEV_AGENT_API_KEY_ENV`);
  an **unknown** provider fails safe toward "cloud".
- To run a `local_only` profile, point the agent at a local model:

  ```bash
  ICDEV_LLM_PROVIDER=ollama python tools/mcp/unified_server.py --toolset compliance
  ```

- If you *accept the risk* (e.g. an accredited cloud enclave), set the explicit
  override:

  ```bash
  ICDEV_MCP_ALLOW_CLOUD_CUI=1 python tools/mcp/unified_server.py --toolset compliance
  ```

The full decision is recorded in
[docs/security/sandbox-coverage.md](../security/sandbox-coverage.md) (Gap 32).

## Verifying against a generic (non-Claude) MCP client

`unified_server.py` speaks standard MCP over stdio (JSON-RPC 2.0) via
`tools/mcp/base_server.py` — no Claude-specific assumptions in the transport or
schemas. To sanity-check a generic client:

1. Handshake + status:
   ```bash
   python tools/mcp/unified_server.py --status --json
   ```
2. Smallest surface first (2 tools), to validate `tools/list` + one call:
   ```bash
   python tools/mcp/unified_server.py --toolset minimal
   ```
3. Confirm the bounded count matches the profile:
   ```bash
   python tools/mcp/unified_server.py --list-toolsets --json
   ```

Tool input schemas are declared in `tools/mcp/tool_registry.py` as standard JSON
Schema (`input_schema`), which any conformant MCP client consumes.

## Hermes example

```bash
# Local kimi/ollama model — compliance work, CUI stays local
ICDEV_LLM_PROVIDER=ollama \
  hermes mcp add icdev \
  --command "python tools/mcp/unified_server.py --toolset compliance"

# Cloud model — restricted to cloud-safe research tools
hermes mcp add icdev \
  --command "python tools/mcp/unified_server.py --toolset research"
```
