# Phase 47: Unified MCP Gateway Server (D301)

CUI // SP-CTI

## Overview

ICDEV previously operated 18 separate MCP servers, each spawning as its own stdio child process. This meant that AI coding tools had to manage 18 simultaneous subprocesses, and 55 CLI tools (43% of the total) had no MCP exposure at all — making them invisible to 9 of the 10 supported AI coding tools.

The Unified MCP Gateway consolidates all 225 tools and 6 resources into a **single MCP server process** with lazy module loading. Startup is fast (no handler imports until first call), and the existing 18 individual servers remain independently runnable for backward compatibility.

---

## Problem Statement

| Before | After |
|--------|-------|
| 18 separate MCP server processes | 1 unified process |
| 170 tools exposed via MCP | 225 tools exposed via MCP |
| 55 CLI tools invisible to AI tools | 0 tools invisible — full coverage |
| ~200 MB combined process memory | ~30 MB single process (lazy loading) |
| 18 entries in `.mcp.json` | 1 entry (18 kept for backward compat) |

---

## Architecture

```
AI Tool (Claude Code / Codex / Gemini / Cursor / ...)
    |
    v
unified_server.py  (inherits MCPServer from base_server.py)
    |
    +-- tool_registry.py  (declarative: 225 tool + 6 resource definitions)
    |       |
    |       +-- Existing tools -> lazy import from 18 server modules
    |       +-- New tools -> lazy import from gap_handlers.py
    |
    +-- gap_handlers.py  (55 new handler functions)
            |
            +-- Direct Python import (preferred)
            +-- Subprocess fallback (CLI --json)
```

### Key Design Properties

- **Lazy loading**: Handler modules imported via `importlib.import_module()` only on first tool call, cached thereafter in `_handler_cache`
- **Graceful degradation**: If a module fails to import, a stub handler returns `{"error": "...", "status": "pending"}` instead of crashing
- **Auto-instrumentation**: All tools inherit D284 distributed tracing from `base_server.py`
- **Zero startup overhead**: Registry is a Python dict, not dynamic introspection — server starts in <100ms

---

## Files

| File | Purpose |
|------|---------|
| `tools/mcp/unified_server.py` | Gateway server — `UnifiedMCPServer` class with lazy dispatch |
| `tools/mcp/tool_registry.py` | Declarative registry: 225 tools, 6 resources, 26 categories |
| `tools/mcp/gap_handlers.py` | 55 new handler functions for previously unexposed CLI tools |
| `tools/mcp/generate_registry.py` | Utility to regenerate registry from server introspection |
| `tests/test_unified_server.py` | 42 tests across 6 test classes |

---

## Tool Categories (26)

| Category | Count | Source |
|----------|-------|--------|
| core | 5 | core_server.py |
| compliance | 31 | compliance_server.py |
| builder | 13 | builder_server.py |
| infra | 6 | infra_server.py |
| knowledge | 5 | knowledge_server.py |
| maintenance | 4 | maintenance_server.py |
| mbse | 10 | mbse_server.py |
| modernization | 10 | modernization_server.py |
| requirements | 10 | requirements_server.py |
| supply_chain | 9 | supply_chain_server.py |
| simulation | 8 | simulation_server.py |
| integration | 10 | integration_server.py |
| marketplace | 11 | marketplace_server.py |
| devsecops | 12 | devsecops_server.py |
| gateway | 5 | gateway_server.py |
| context | 5 | context_server.py |
| innovation | 10 | innovation_server.py |
| observability | 6 | observability_server.py |
| translation | 9 | gap_handlers.py (new) |
| dx | 5 | gap_handlers.py (new) |
| cloud | 5 | gap_handlers.py (new) |
| registry | 9 | gap_handlers.py (new) |
| security_agentic | 9 | gap_handlers.py (new) |
| testing | 6 | gap_handlers.py (new) |
| installer | 4 | gap_handlers.py (new) |
| misc | 8 | gap_handlers.py (new) |

---

## Gap Handlers — 55 New Tools

The `gap_handlers.py` module exposes 55 CLI tools that previously had no MCP access. Two patterns are used:

### Pattern A — Direct Python Import (preferred)

```python
def handle_scan_code_patterns(args):
    from tools.security.code_pattern_scanner import CodePatternScanner
    scanner = CodePatternScanner()
    return scanner.scan_directory(args.get("project_dir"), args.get("language"))
```

### Pattern B — Subprocess Wrapper (CLI-only tools)

```python
def handle_production_audit(args):
    cmd = [sys.executable, str(BASE_DIR / "tools/testing/production_audit.py"), "--json"]
    if args.get("category"):
        cmd.extend(["--category", args["category"]])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(BASE_DIR))
    return json.loads(proc.stdout) if proc.returncode == 0 else {"error": proc.stderr}
```

---

## Usage

### Start the Unified Server

```bash
python tools/mcp/unified_server.py
```

The server communicates over stdio using JSON-RPC 2.0 with Content-Length framing (MCP protocol).

### .mcp.json Configuration

```json
{
  "icdev-unified": {
    "command": "python",
    "args": ["tools/mcp/unified_server.py"],
    "env": {
      "ICDEV_DB_PATH": "data/icdev.db",
      "ICDEV_PROJECT_ROOT": "."
    }
  }
}
```

### Run Tests

```bash
pytest tests/test_unified_server.py -v
```

42 tests covering registry completeness, server parity, gap handler coverage, server lifecycle, module validation, and representative tool calls.

---

## Adding New Tools

To add a new tool to the unified gateway:

### 1. Add the handler function

If the tool wraps an existing Python module:
```python
# In the appropriate server module (e.g., tools/mcp/compliance_server.py)
def handle_my_new_tool(args: dict) -> dict:
    # Implementation
    return {"result": "..."}
```

If the tool is entirely new:
```python
# In tools/mcp/gap_handlers.py
def handle_my_new_tool(args: dict) -> dict:
    from tools.my_module import MyClass
    return MyClass().run(args)
```

### 2. Add the registry entry

```python
# In tools/mcp/tool_registry.py, add to TOOL_REGISTRY:
"my_new_tool": {
    "category": "my_category",
    "module": "tools.mcp.gap_handlers",  # or existing server module
    "handler": "handle_my_new_tool",
    "description": "What this tool does",
    "input_schema": {
        "type": "object",
        "properties": {
            "param1": {"type": "string", "description": "..."},
        },
        "required": ["param1"],
    },
},
```

### 3. Update tests

Add the new tool name to the relevant category test in `tests/test_unified_server.py` and update the total tool count assertion.

### 4. Regenerate companion configs

```bash
python tools/dx/companion.py --sync --write
```

---

## Backward Compatibility

The 18 individual MCP servers remain fully functional:

```bash
# These still work independently
python tools/mcp/core_server.py
python tools/mcp/compliance_server.py
python tools/mcp/builder_server.py
# ... etc
```

Teams can choose:
- **Unified only** — Use `icdev-unified` in `.mcp.json`, disable individual servers
- **Individual only** — Keep existing 18-server setup unchanged
- **Mixed** — Use unified for most tools, individual servers for specific needs

---

## Architecture Decision

**D301**: Unified MCP gateway uses declarative tool registry (`tool_registry.py`) with lazy module loading. Existing 18 servers remain independently runnable (backward compat). Registry maps tool name → (module, handler, schema). Handlers imported via `importlib.import_module()` on first call, cached thereafter. New tools for 55 CLI gaps use direct Python import with subprocess fallback. All tools inherit D284 auto-instrumentation from `base_server.py`.

---

## Production Audit Results

The unified gateway passed all audit checks:

| Check | Result |
|-------|--------|
| SEC-001 SAST (Bandit) | 0 critical findings |
| SEC-002 Dependency audit | 0 vulnerable deps |
| SEC-003 Secret detection | No secrets |
| SEC-006 Code patterns | 0 critical patterns |
| INT-001 MCP validation | 20/20 servers valid |
| INT-003 Syntax check | 483 files, 0 errors |
| PRF-004 Test collection | All tests passing |

---

## Related

- [Multi-Agent System Architecture](../architecture/multi-agent-system.md) — Unified gateway section
- [AI Companion Guide](../dx/companion-guide.md) — Multi-tool MCP config generation
- [Phase 46: Observability & XAI](phase-46-observability-traceability-xai.md) — D284 auto-instrumentation
