# Unified MCP Server — IDE Setup Guide

CUI // SP-CTI

ICDEV exposes **225 tools** through a single MCP server. This guide shows how to configure it in every supported AI coding tool and IDE.

---

## Prerequisites

- Python 3.10+ installed and on PATH
- ICDEV repository cloned locally
- No additional dependencies required (stdlib + existing ICDEV packages)

Verify the server starts:

```bash
cd /path/to/ICDev
python tools/mcp/unified_server.py
# Should output nothing to stdout (MCP servers use stdio for JSON-RPC)
# Press Ctrl+C to stop
```

---

## Quick Start (Auto-Generate All Configs)

If you have multiple AI tools, generate all configs at once:

```bash
# Auto-detect installed tools and generate MCP configs
python tools/dx/companion.py --setup --write

# Or generate for all 10 supported tools
python tools/dx/companion.py --setup --all --write

# After any ICDEV update, regenerate
python tools/dx/companion.py --sync --write
```

For manual setup, follow the IDE-specific instructions below.

---

## VS Code + Claude Code

Claude Code reads MCP configuration from `.mcp.json` in the project root. The unified server is already configured if you're using the ICDEV repository.

### Configuration

File: `.mcp.json`

```json
{
  "mcpServers": {
    "icdev-unified": {
      "command": "python",
      "args": ["tools/mcp/unified_server.py"],
      "env": {
        "ICDEV_DB_PATH": "data/icdev.db",
        "ICDEV_PROJECT_ROOT": "."
      }
    }
  }
}
```

### Verify

1. Open the ICDEV project in VS Code
2. Open Claude Code (Ctrl+L or Cmd+L)
3. Type: "List all available MCP tools"
4. Claude Code should report 225 tools from the `icdev-unified` server

### Notes

- Claude Code auto-starts MCP servers when it detects `.mcp.json`
- The server runs as a child process and communicates over stdio
- If you previously used 18 individual servers, you can remove them from `.mcp.json` and keep only `icdev-unified` — or keep both for backward compatibility

---

## VS Code + Cursor

Cursor supports MCP servers through its settings UI.

### Configuration

**Option A — Settings UI:**

1. Open Cursor Settings: `Cmd+Shift+J` (Mac) or `Ctrl+Shift+J` (Windows/Linux)
2. Navigate to **Features > MCP Servers**
3. Click **Add New MCP Server**
4. Enter:
   - Name: `icdev-unified`
   - Command: `python`
   - Args: `tools/mcp/unified_server.py`
5. Add environment variables:
   - `ICDEV_DB_PATH` = `data/icdev.db`
   - `ICDEV_PROJECT_ROOT` = `.`

**Option B — Config file:**

File: `.cursor/mcp.json`

```json
{
  "mcpServers": {
    "icdev-unified": {
      "command": "python",
      "args": ["tools/mcp/unified_server.py"],
      "env": {
        "ICDEV_DB_PATH": "data/icdev.db",
        "ICDEV_PROJECT_ROOT": "."
      }
    }
  }
}
```

### Verify

1. Open Cursor composer (Ctrl+I)
2. The MCP server indicator should show `icdev-unified` as connected
3. Ask: "Use the project_list tool to show all ICDEV projects"

---

## VS Code + Windsurf (Codeium)

Windsurf supports MCP servers via its configuration file.

### Configuration

File: `.windsurf/mcp.json`

```json
{
  "mcpServers": {
    "icdev-unified": {
      "command": "python",
      "args": ["tools/mcp/unified_server.py"],
      "env": {
        "ICDEV_DB_PATH": "data/icdev.db",
        "ICDEV_PROJECT_ROOT": "."
      }
    }
  }
}
```

### Verify

1. Open Windsurf Cascade (Ctrl+L)
2. Windsurf should detect and connect to the MCP server
3. Ask it to call any ICDEV tool

---

## VS Code + Cline

Cline reads MCP configuration from its own settings file.

### Configuration

File: `.cline/mcp_settings.json`

```json
{
  "mcpServers": {
    "icdev-unified": {
      "command": "python",
      "args": ["tools/mcp/unified_server.py"],
      "env": {
        "ICDEV_DB_PATH": "data/icdev.db",
        "ICDEV_PROJECT_ROOT": "."
      }
    }
  }
}
```

### Verify

1. Open the Cline extension panel
2. Check that `icdev-unified` appears in the MCP server list
3. Cline can now use all 225 ICDEV tools

---

## OpenAI Codex CLI

Codex CLI uses TOML-based MCP configuration.

### Configuration

File: `.codex/mcp-config.toml`

```toml
[icdev-unified]
command = "python"
args = ["tools/mcp/unified_server.py"]

[icdev-unified.env]
ICDEV_DB_PATH = "data/icdev.db"
ICDEV_PROJECT_ROOT = "."
```

### Verify

```bash
codex --mcp-config .codex/mcp-config.toml
# Ask: "Use the project_list tool"
```

---

## Google Gemini CLI

Gemini CLI supports MCP servers via JSON configuration.

### Configuration

File: `.gemini/settings.json`

```json
{
  "mcpServers": {
    "icdev-unified": {
      "command": "python",
      "args": ["tools/mcp/unified_server.py"],
      "env": {
        "ICDEV_DB_PATH": "data/icdev.db",
        "ICDEV_PROJECT_ROOT": "."
      }
    }
  }
}
```

### Verify

```bash
gemini
# Ask: "List the MCP tools available"
```

---

## Amazon Q Developer

Amazon Q supports MCP through JSON configuration.

### Configuration

File: `.amazonq/mcp.json`

```json
{
  "mcpServers": {
    "icdev-unified": {
      "command": "python",
      "args": ["tools/mcp/unified_server.py"],
      "env": {
        "ICDEV_DB_PATH": "data/icdev.db",
        "ICDEV_PROJECT_ROOT": "."
      }
    }
  }
}
```

### Verify

1. Open Amazon Q in your IDE
2. The MCP server should connect automatically
3. Tools appear in Q's available actions

---

## JetBrains IDEs (Junie / AI Assistant)

JetBrains IDEs (IntelliJ, PyCharm, WebStorm, etc.) support MCP through the AI Assistant or Junie plugin.

### Configuration

**Option A — IDE Settings:**

1. Open Settings: `Ctrl+Alt+S` (Windows/Linux) or `Cmd+,` (Mac)
2. Navigate to **Tools > AI Assistant > MCP Servers** (or **Junie > MCP**)
3. Click **+** to add a new server
4. Enter:
   - Name: `icdev-unified`
   - Command: `python`
   - Arguments: `tools/mcp/unified_server.py`
5. Add environment variables:
   - `ICDEV_DB_PATH` = `data/icdev.db`
   - `ICDEV_PROJECT_ROOT` = `.`

**Option B — Config file:**

File: `.junie/mcp.json`

```json
{
  "mcpServers": {
    "icdev-unified": {
      "command": "python",
      "args": ["tools/mcp/unified_server.py"],
      "env": {
        "ICDEV_DB_PATH": "data/icdev.db",
        "ICDEV_PROJECT_ROOT": "."
      }
    }
  }
}
```

### Verify

1. Open the AI Assistant panel
2. Check that `icdev-unified` is listed as a connected tool server
3. Ask the assistant to use any ICDEV tool

---

## GitHub Copilot (VS Code)

GitHub Copilot's MCP support is accessed through agent mode in Copilot Chat.

### Configuration

File: `.vscode/mcp.json`

```json
{
  "servers": {
    "icdev-unified": {
      "type": "stdio",
      "command": "python",
      "args": ["tools/mcp/unified_server.py"],
      "env": {
        "ICDEV_DB_PATH": "data/icdev.db",
        "ICDEV_PROJECT_ROOT": "."
      }
    }
  }
}
```

### Verify

1. Open Copilot Chat in VS Code
2. Switch to **Agent** mode (`@workspace` or the agent mode toggle)
3. MCP tools from `icdev-unified` should be available

---

## Aider

Aider does not natively support MCP. However, you can use ICDEV tools via the CLI:

```bash
# Aider can execute shell commands, so tools are accessible via:
python tools/testing/production_audit.py --json
python tools/compliance/ssp_generator.py --project-id "proj-123"
# etc.
```

For Aider instruction context, generate the `CONVENTIONS.md` file:

```bash
python tools/dx/companion.py --setup --platforms aider --write
```

---

## Troubleshooting

### Server won't start

```bash
# Check Python is available
python --version

# Check the server can import all dependencies
python -c "from tools.mcp.unified_server import create_server; s = create_server(); print(f'{len(s._tools)} tools registered')"
# Expected output: "225 tools registered"
```

### Server starts but tools fail

Tools use lazy loading — a module is only imported when first called. If a specific tool fails:

```bash
# Test a specific tool's handler directly
python -c "
from tools.mcp.tool_registry import TOOL_REGISTRY
import importlib
entry = TOOL_REGISTRY['project_list']
mod = importlib.import_module(entry['module'])
fn = getattr(mod, entry['handler'])
print(fn({}))
"
```

### Python path issues

The server automatically adds the project root to `sys.path`. If you're running from a different directory, set the environment variable:

```bash
ICDEV_PROJECT_ROOT=/path/to/ICDev python /path/to/ICDev/tools/mcp/unified_server.py
```

### Windows-specific

On Windows, use `python` (not `python3`). If Python isn't on PATH, use the full path:

```json
{
  "icdev-unified": {
    "command": "C:\\Python311\\python.exe",
    "args": ["tools/mcp/unified_server.py"],
    "env": {
      "ICDEV_DB_PATH": "data/icdev.db",
      "ICDEV_PROJECT_ROOT": "."
    }
  }
}
```

### macOS/Linux — python vs python3

Some systems only have `python3`. Adjust the command:

```json
{
  "icdev-unified": {
    "command": "python3",
    "args": ["tools/mcp/unified_server.py"]
  }
}
```

### Checking which tools are available

```bash
# List all 225 tools grouped by category
python -c "
from collections import Counter
from tools.mcp.tool_registry import TOOL_REGISTRY
cats = Counter(e['category'] for e in TOOL_REGISTRY.values())
for cat, count in sorted(cats.items()):
    print(f'  {cat}: {count} tools')
print(f'Total: {len(TOOL_REGISTRY)} tools')
"
```

---

## Migrating from 18 Individual Servers

If you previously used 18 individual MCP server entries in `.mcp.json`:

### Step 1 — Add the unified server

Add `icdev-unified` to your `.mcp.json` (see VS Code + Claude Code section above).

### Step 2 — Test

Verify all tools work through the unified server. Every tool that worked via individual servers works identically through the unified gateway.

### Step 3 — Remove individual servers (optional)

Once satisfied, you can remove the 18 individual entries from `.mcp.json`. The unified server provides all the same tools plus 55 additional ones.

Before:
```json
{
  "mcpServers": {
    "icdev-core": { "command": "python", "args": ["tools/mcp/core_server.py"], ... },
    "icdev-compliance": { "command": "python", "args": ["tools/mcp/compliance_server.py"], ... },
    "icdev-builder": { "command": "python", "args": ["tools/mcp/builder_server.py"], ... },
    ... (15 more entries)
  }
}
```

After:
```json
{
  "mcpServers": {
    "icdev-unified": {
      "command": "python",
      "args": ["tools/mcp/unified_server.py"],
      "env": {
        "ICDEV_DB_PATH": "data/icdev.db",
        "ICDEV_PROJECT_ROOT": "."
      }
    }
  }
}
```

### Step 4 — Regenerate companion configs

```bash
python tools/dx/companion.py --sync --write
```

This regenerates MCP configs for all 10 AI tools to point to the unified server.

---

## Reference

| Resource | Path |
|----------|------|
| Unified server | `tools/mcp/unified_server.py` |
| Tool registry | `tools/mcp/tool_registry.py` |
| Gap handlers | `tools/mcp/gap_handlers.py` |
| Tests | `tests/test_unified_server.py` |
| Feature docs | `docs/features/phase-47-unified-mcp-gateway.md` |
| Companion guide | `docs/dx/companion-guide.md` |
| Architecture | `docs/architecture/multi-agent-system.md` |
