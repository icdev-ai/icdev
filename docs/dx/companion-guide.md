# ICDEV AI Companion Guide

ICDEV works with **10 AI coding tools** — not just Claude Code. One command generates instruction files, MCP configurations, and skill translations for every tool your team uses.

---

## Supported Tools

| Tool | Instruction File | MCP Support | Skills |
|------|-----------------|-------------|--------|
| Claude Code | `CLAUDE.md` (manual) | `.mcp.json` | `.claude/skills/` |
| OpenAI Codex | `AGENTS.md` | TOML config | `.agents/skills/` |
| Google Gemini | `GEMINI.md` | JSON config | — |
| GitHub Copilot | `.github/copilot-instructions.md` | — | `.github/prompts/` |
| Cursor | `.cursor/rules/*.mdc` | IDE settings | `.cursor/rules/` |
| Windsurf | `.windsurf/rules/*.md` | IDE settings | — |
| Amazon Q | `.amazonq/rules/*.md` | JSON config | — |
| JetBrains Junie | `.junie/guidelines.md` | IDE settings | — |
| Cline | `.clinerules` | JSON config | — |
| Aider | `CONVENTIONS.md` | — | — |

---

## Quick Start

```bash
# Auto-detect installed tools and generate everything
python tools/dx/companion.py --setup --write

# Generate for all 10 tools
python tools/dx/companion.py --setup --all --write

# Generate for specific tools only
python tools/dx/companion.py --setup --platforms codex,cursor,gemini --write

# Preview without writing files
python tools/dx/companion.py --setup --all --dry-run --json

# Detect which AI tools are installed
python tools/dx/companion.py --detect --json

# List all supported platforms
python tools/dx/companion.py --list
```

---

## What Gets Generated

### 1. Instruction Files

Each AI tool gets a project-aware instruction file in its native format:

- **Project context**: name, type, language, impact level, classification
- **MCP server awareness**: which servers are available and what they do
- **Compliance rules**: CUI markings, security gates, STIG thresholds
- **Workflow guidance**: how to use ICDEV tools via the AI coding tool
- **Dev profile**: coding standards, testing requirements, style rules

### 2. MCP Configurations

Tools with MCP support get config files pointing to ICDEV's **unified MCP server** (`icdev-unified`) — a single server exposing all 225 tools. See the [Unified MCP Setup Guide](unified-mcp-setup.md) for IDE-specific instructions.

| Tool | Config Format | Output Path |
|------|--------------|-------------|
| Codex | TOML | `.codex/mcp-config.toml` |
| Amazon Q | JSON | `.amazonq/mcp.json` |
| Gemini | JSON | `.gemini/mcp-settings.json` |
| Cline | JSON | `.cline/mcp_settings.json` |
| Cursor | Setup guide | `.cursor/mcp-setup.md` |
| Windsurf | Setup guide | `.windsurf/mcp-setup.md` |
| JetBrains | Setup guide | `.junie/mcp-setup.md` |

### 3. Skill Translations

Claude Code skills (`.claude/skills/`) are translated to equivalent formats:

| Source | Target | Format |
|--------|--------|--------|
| `/skill-name` | `$skill-name` (Codex) | `.agents/skills/*/SKILL.md` |
| `/skill-name` | `#prompt:skill-name` (Copilot) | `.github/prompts/*.prompt.md` |
| `/skill-name` | Agent-requested rule (Cursor) | `.cursor/rules/*.mdc` |

---

## Multi-Vendor Coexistence

All vendor instruction files and MCP configs coexist in the project root with zero conflict. Each AI tool reads **only its own** files and ignores everything else — the same way `.gitignore`, `.eslintrc`, and `.prettierrc` coexist today.

### Project Root After Setup

```
ICDev/
├── CLAUDE.md                          # Claude Code (source of truth)
├── AGENTS.md                          # OpenAI Codex
├── GEMINI.md                          # Google Gemini CLI
├── CONVENTIONS.md                     # Aider
├── .mcp.json                          # Claude Code MCP config
├── .clinerules                        # Cline
├── .claude/                           # Claude Code commands, hooks, settings
│   ├── commands/
│   ├── hooks/
│   └── settings.json
├── .codex/                            # Codex MCP config
│   └── mcp-config.toml
├── .gemini/                           # Gemini MCP config
│   └── mcp-settings.json
├── .amazonq/                          # Amazon Q rules + MCP config
│   ├── rules/icdev.md
│   └── mcp.json
├── .github/                           # Copilot instructions + prompt skills
│   ├── copilot-instructions.md
│   └── prompts/
├── .cursor/                           # Cursor rules + MCP setup guide
│   └── rules/icdev.mdc
├── .windsurf/                         # Windsurf rules + MCP setup guide
│   └── rules/icdev.md
├── .junie/                            # JetBrains Junie guidelines
│   └── guidelines.md
└── ...
```

### Why This Works

- **No cross-tool interference** — Claude Code never reads `.cursor/`, Codex never reads `.claude/`, Gemini ignores both
- **Single source of truth** — `CLAUDE.md` is maintained manually; all other files are generated from it
- **Team flexibility** — Any developer can open the repo with their preferred tool and get full ICDEV context
- **Git-friendly** — All files commit to the repo, so teammates get configs automatically on `git pull`

### Keeping Files in Sync

After modifying `CLAUDE.md`, `.mcp.json`, or `.claude/commands/`, regenerate all companion files:

```bash
# Resync all generated files from current CLAUDE.md
python tools/dx/companion.py --sync --write

# Preview what would change without writing
python tools/dx/companion.py --sync --dry-run --json
```

The companion system reads the current state of `CLAUDE.md` and `.mcp.json` on every run — generated files always reflect the latest project configuration.

---

## icdev.yaml Companion Section

Add a `companion:` section to your `icdev.yaml` to control which tools get generated:

```yaml
companion:
  tools:
    - codex
    - cursor
    - gemini
    - copilot
  auto_sync: false        # Regenerate on icdev.yaml changes
  instruction_style: full  # full | minimal
```

If omitted, the companion system auto-detects installed tools.

---

## Individual Tools

### Tool Detector

```bash
# Detect AI tools from env vars, config directories, and config files
python tools/dx/tool_detector.py --json
```

### Instruction Generator

```bash
# Generate instruction files for specific platforms
python tools/dx/instruction_generator.py --platform codex --write --json

# Generate for all platforms
python tools/dx/instruction_generator.py --all --write
```

### MCP Config Generator

```bash
# Generate MCP configs from .mcp.json
python tools/dx/mcp_config_generator.py --all --write --json

# Single platform
python tools/dx/mcp_config_generator.py --platform amazon_q --write
```

### Skill Translator

```bash
# List available Claude Code skills
python tools/dx/skill_translator.py --list

# Translate to all supported platforms
python tools/dx/skill_translator.py --all --write --json

# Translate specific skills to specific platform
python tools/dx/skill_translator.py --platform codex --skills icdev-build,icdev-test --write
```

---

## Architecture

The companion system follows ICDEV's existing patterns:

- **D194**: Companion registry is declarative YAML — add new tools without code changes
- **D195**: Instruction files generated from Jinja2 templates (D186 pattern)
- **D196**: MCP is the primary integration protocol — 9/10 tools support it
- **D197**: Tool detection is advisory — explicit `--platform` override available
- **D198**: Skill translation preserves semantic intent, not literal copy

```
icdev.yaml (companion: section)
    |
    v
args/companion_registry.yaml   <-- Add new tools here
    |
    +-- tools/dx/tool_detector.py          (detect installed tools)
    +-- tools/dx/instruction_generator.py  (generate instruction files)
    +-- tools/dx/mcp_config_generator.py   (generate MCP configs)
    +-- tools/dx/skill_translator.py       (translate skills)
    |
    v
tools/dx/companion.py   <-- Single CLI entry point
```
