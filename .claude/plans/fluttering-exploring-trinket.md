# Universal AI Coding Companion — Implementation Plan

## Context

ICDEV has 500+ Python CLI tools, 14 MCP servers, and 44 goal workflows — all platform-agnostic. But the orchestration layer is 100% Claude Code-specific: `CLAUDE.md`, `.claude/skills/`, `.claude/commands/`, `.claude/hooks/`, `.mcp.json`. This means ICDEV is invisible to developers using Codex, Cursor, Copilot, Gemini, or any other AI coding tool.

The AI coding tool landscape has converged on two standards:
1. **MCP** (Model Context Protocol) — supported by 9/10 major tools (all except Aider)
2. **Markdown instruction files** — every tool reads its own format (`AGENTS.md`, `GEMINI.md`, `.cursorrules`, etc.)

**Goal:** Generate tool-specific configuration files from a single source of truth (`icdev.yaml` + project state), so ICDEV works as a companion for every major AI coding tool — not just Claude Code.

## ADRs

- **D194**: Companion registry is declarative YAML — add new AI tools without code changes (D26 pattern)
- **D195**: Instruction files generated from Jinja2 templates — tool-specific formatting from universal data (D50/D186 pattern)
- **D196**: MCP is primary integration protocol — tools with MCP get full ICDEV capability; others get instruction files + CLI
- **D197**: AI tool detection is advisory — auto-detect for convenience, explicit `--platform` override for certainty (D110/D185 pattern)
- **D198**: Skill translation preserves semantic intent — each tool gets equivalent capability in its native format, not a literal copy

## Implementation Steps

Build order: Registry first, then detector, then generators (parallelizable), then CLI orchestrator, then tests, then docs.

---

### Step 1: Companion Registry

**Create:** `args/companion_registry.yaml` (~120 lines)

Declarative registry of all 10 supported AI coding tools. Each entry defines:

```yaml
companions:
  claude_code:
    display_name: "Claude Code"
    vendor: "Anthropic"
    instruction_file: "CLAUDE.md"
    mcp_support: true
    mcp_config_file: ".mcp.json"
    mcp_config_format: "claude"          # claude | codex_toml | json | ide_settings
    skill_format: "claude_skill"         # claude_skill | codex_skill | copilot_prompt | cursor_mdc | none
    skill_directory: ".claude/skills"
    command_directory: ".claude/commands"
    env_detection:
      env_vars: ["CLAUDE_CODE"]
      config_dirs: [".claude"]
      process_names: ["claude"]
    capabilities:
      custom_commands: true
      hooks: true
      mcp_tools: true
      inline_completion: false
    notes: "Primary platform. CLAUDE.md already exists."

  codex:
    display_name: "OpenAI Codex CLI"
    vendor: "OpenAI"
    instruction_file: "AGENTS.md"
    mcp_support: true
    mcp_config_file: ".codex/config.toml"
    mcp_config_format: "codex_toml"
    skill_format: "codex_skill"
    skill_directory: ".agents/skills"
    env_detection:
      env_vars: ["CODEX_CLI", "OPENAI_API_KEY"]
      config_dirs: [".codex", ".agents"]
      process_names: ["codex"]
    capabilities:
      custom_commands: true
      hooks: false
      mcp_tools: true

  gemini:
    display_name: "Google Gemini CLI"
    vendor: "Google"
    instruction_file: "GEMINI.md"
    mcp_support: true
    mcp_config_file: ".gemini/settings.json"
    mcp_config_format: "json"
    skill_format: "none"
    env_detection:
      env_vars: ["GEMINI_API_KEY"]
      config_dirs: [".gemini"]
    capabilities:
      custom_commands: false
      hooks: false
      mcp_tools: true

  copilot:
    display_name: "GitHub Copilot"
    vendor: "GitHub/Microsoft"
    instruction_file: ".github/copilot-instructions.md"
    mcp_support: true
    mcp_config_file: null                # VS Code settings, not file-based
    mcp_config_format: "ide_settings"
    skill_format: "copilot_prompt"
    skill_directory: ".github/prompts"
    env_detection:
      config_dirs: [".github"]
    capabilities:
      custom_commands: true
      hooks: false
      mcp_tools: true

  cursor:
    display_name: "Cursor"
    vendor: "Cursor Inc"
    instruction_file: ".cursor/rules/icdev.mdc"
    mcp_support: true
    mcp_config_format: "ide_settings"
    skill_format: "cursor_mdc"
    skill_directory: ".cursor/rules"
    env_detection:
      config_dirs: [".cursor"]
      process_names: ["cursor"]
    capabilities:
      custom_commands: false
      hooks: false
      mcp_tools: true

  windsurf:
    display_name: "Windsurf"
    vendor: "Codeium"
    instruction_file: ".windsurf/rules/icdev.md"
    mcp_support: true
    mcp_config_format: "ide_settings"
    skill_format: "none"
    env_detection:
      config_dirs: [".windsurf"]

  amazon_q:
    display_name: "Amazon Q Developer"
    vendor: "AWS"
    instruction_file: ".amazonq/rules/icdev.md"
    mcp_support: true
    mcp_config_file: ".amazonq/mcp.json"
    mcp_config_format: "json"
    skill_format: "none"
    env_detection:
      config_dirs: [".amazonq"]

  junie:
    display_name: "JetBrains Junie"
    vendor: "JetBrains"
    instruction_file: ".junie/guidelines.md"
    mcp_support: true
    mcp_config_format: "ide_settings"
    skill_format: "none"
    env_detection:
      config_dirs: [".junie"]

  cline:
    display_name: "Cline"
    vendor: "Cline"
    instruction_file: ".clinerules"
    mcp_support: true
    mcp_config_format: "json"
    skill_format: "none"
    env_detection:
      config_dirs: [".clinerules"]

  aider:
    display_name: "Aider"
    vendor: "Aider-AI"
    instruction_file: "CONVENTIONS.md"
    mcp_support: false
    skill_format: "none"
    env_detection:
      config_files: [".aider.conf.yml"]

# Default instruction style for all generated files
defaults:
  instruction_style: "full"              # full | minimal
  include_mcp_setup: true
  include_workflows: true
  include_guardrails: true
```

---

### Step 2: AI Tool Detector

**Create:** `tools/dx/tool_detector.py` (~150 LOC)

Detects which AI coding tools are present/active in the current environment.

```python
def detect_tools(directory=None) -> dict:
    """Detect AI coding tools from env vars, config dirs, processes.
    Returns: {detected: [{tool_id, display_name, confidence, evidence}],
              primary: tool_id or None, all_tools: [tool_ids]}
    """

def _check_env_vars(tool_config) -> list[str]:
    """Check if tool-specific env vars are set."""

def _check_config_dirs(directory, tool_config) -> list[str]:
    """Check if tool-specific config directories exist."""

def _check_processes(tool_config) -> list[str]:
    """Check if tool-specific processes are running (best-effort)."""
```

CLI: `python tools/dx/tool_detector.py --json`

**Pattern:** Follows D185 advisory detection — reports findings, doesn't enforce.

---

### Step 3: Instruction File Generator (THE KEY PIECE)

**Create:** `tools/dx/instruction_generator.py` (~650 LOC)

Generates tool-specific instruction files from universal project data + Jinja2 templates.

```python
def generate_instructions(directory=None, platforms=None, style="full",
                          write=False, dry_run=False) -> dict:
    """Generate instruction files for specified platforms.
    Args:
        platforms: list of tool_ids or ['all']. Default: auto-detect.
        style: 'full' (complete guide) or 'minimal' (essentials only).
        write: write files to disk.
        dry_run: preview without writing.
    Returns: {platform: {path, content, written, size_bytes}}
    """

def _collect_project_data(directory) -> dict:
    """Collect universal project data for template rendering.
    Sources: icdev.yaml (manifest_loader), session context, goals manifest,
             tools manifest, dev profile.
    Returns: {project, impact_level, classification, compliance, workflows,
              tools, guardrails, testing, dev_profile, mcp_servers}
    """

def _render_template(template_str, data, platform) -> str:
    """Render a Jinja2 template with project data."""
```

**Templates stored as string constants** (D186 pattern). Key templates:

**AGENTS.md** (Codex — the universal fallback, ~200 rendered lines):
- Project identity & classification
- GOTCHA architecture overview (condensed)
- Available CLI tools (most important 20)
- Coding standards from dev profile
- Compliance guardrails
- Workflow commands (adapted from skills: `$icdev-build`, `$icdev-test`, etc.)
- MCP server setup instructions
- Testing commands

**GEMINI.md** (Gemini CLI, ~180 rendered lines):
- Similar to AGENTS.md structure
- Gemini-specific formatting (no frontmatter)
- MCP setup via `.gemini/settings.json`

**.github/copilot-instructions.md** (Copilot, ~150 rendered lines):
- Concise coding rules (Copilot instruction files tend shorter)
- Tool commands
- Compliance requirements
- References to `.github/prompts/` for workflows

**.cursor/rules/icdev.mdc** (Cursor, ~120 rendered lines):
- MDC frontmatter: `description`, `globs`, `alwaysApply: true`
- Concise rules format (Cursor has 6000 char limit per rule)
- Key commands and guardrails

**.windsurf/rules/icdev.md** (Windsurf, ~120 rendered lines):
- Similar to Cursor but plain markdown
- Activation mode metadata

**.amazonq/rules/icdev.md** (Amazon Q, ~150 rendered lines):
- AWS-oriented (GovCloud, Bedrock references)
- Compliance-heavy format

**.junie/guidelines.md** (JetBrains, ~150 rendered lines):
- JetBrains conventions format
- Project structure guidance

**.clinerules** (Cline, ~120 rendered lines):
- Concise rule format
- MCP server category rules

**CONVENTIONS.md** (Aider, ~100 rendered lines):
- Coding conventions only (no tool integration)
- Style rules, testing requirements, compliance markings

CLI:
```bash
python tools/dx/instruction_generator.py --all --write          # Generate all
python tools/dx/instruction_generator.py --platform codex --dry-run
python tools/dx/instruction_generator.py --platform cursor,copilot --write --json
```

**Critical files to reference:**
- `tools/builder/profile_md_generator.py` — Jinja2 string constant template pattern
- `tools/project/manifest_loader.py` — project data source
- `tools/project/session_context_builder.py` — compliance/profile data source

---

### Step 4: MCP Config Generator

**Create:** `tools/dx/mcp_config_generator.py` (~250 LOC)

Translates `.mcp.json` (Claude Code format) to tool-specific MCP config formats.

```python
def generate_mcp_config(directory=None, platforms=None,
                        write=False, dry_run=False) -> dict:
    """Generate MCP configs for each platform from .mcp.json.
    Returns: {platform: {path, content, format, written}}
    """

def _load_mcp_servers(directory) -> dict:
    """Load server definitions from .mcp.json."""

def _generate_codex_toml(servers) -> str:
    """Generate [mcp] section for .codex/config.toml."""

def _generate_amazon_q_json(servers) -> str:
    """Generate .amazonq/mcp.json format."""

def _generate_cline_json(servers) -> str:
    """Generate Cline MCP settings format."""

def _generate_gemini_json(servers) -> str:
    """Generate .gemini/settings.json mcpServers section."""

def _generate_setup_instructions(platform) -> str:
    """Generate IDE-specific MCP setup instructions (Cursor, Windsurf, JetBrains)."""
```

Output formats:
- **Codex**: TOML `[mcp.servers.icdev-core]` entries in `.codex/config.toml`
- **Amazon Q**: JSON in `.amazonq/mcp.json` (same structure as Claude's, different path)
- **Gemini**: JSON in `.gemini/settings.json` under `mcpServers` key
- **Cline**: JSON (slightly different structure with `mcpServers` key)
- **Cursor/Windsurf/JetBrains**: Setup instructions markdown (IDE settings, not file-based)

CLI:
```bash
python tools/dx/mcp_config_generator.py --all --write --json
python tools/dx/mcp_config_generator.py --platform codex --dry-run
```

---

### Step 5: Skill Translator

**Create:** `tools/dx/skill_translator.py` (~400 LOC)

Translates `.claude/skills/*/SKILL.md` to tool-specific skill/command formats.

```python
def translate_skills(directory=None, platforms=None, skills=None,
                     write=False, dry_run=False) -> dict:
    """Translate Claude Code skills to other tool formats.
    Args:
        skills: list of skill names or None for all.
    Returns: {platform: {skill_name: {path, content, written}}}
    """

def _parse_claude_skill(skill_dir) -> dict:
    """Parse a .claude/skills/*/SKILL.md into structured data.
    Returns: {name, description, context, allowed_tools,
              usage, steps, hard_prompts, examples, error_handling}
    """

def _translate_to_codex_skill(skill_data) -> str:
    """Generate .agents/skills/{name}/SKILL.md for Codex."""

def _translate_to_copilot_prompt(skill_data) -> str:
    """Generate .github/prompts/{name}.prompt.md for Copilot."""

def _translate_to_cursor_rule(skill_data) -> str:
    """Generate .cursor/rules/{name}.mdc for Cursor."""
```

**Translation mappings:**

| Source (Claude) | Codex Output | Copilot Output | Cursor Output |
|----------------|-------------|----------------|---------------|
| `---` frontmatter | YAML frontmatter (same) | YAML frontmatter (mode, tools) | MDC frontmatter (description, globs) |
| `/icdev-build` | `$icdev-build` | `#prompt:icdev-build` | Agent-requested rule |
| MCP tool refs | MCP tool refs (same) | CLI command equivalents | CLI command equivalents |
| `allowed-tools` | Keep as-is | Map to `tools:` list | N/A |
| Steps (markdown) | Steps (same format) | Steps (same format) | Condensed rules |

Only 3 target formats support skills/commands: Codex, Copilot, Cursor. Other tools get their workflows embedded in the instruction file instead.

CLI:
```bash
python tools/dx/skill_translator.py --all --write --json
python tools/dx/skill_translator.py --platform codex --skills icdev-build,icdev-test
```

---

### Step 6: Companion CLI (Orchestrator)

**Create:** `tools/dx/companion.py` (~200 LOC)

Single entry point that orchestrates all generators.

```python
def setup_companion(directory=None, platforms=None, write=False,
                    dry_run=False, detect=False) -> dict:
    """Full companion setup: detect tools, generate all configs.
    Returns: {detected_tools, instruction_files, mcp_configs,
              skill_translations, summary}
    """
```

CLI:
```bash
# Auto-detect tools and generate everything
python tools/dx/companion.py --setup --write

# Generate for specific platforms
python tools/dx/companion.py --setup --platforms codex,cursor,copilot --write

# Detect only (no generation)
python tools/dx/companion.py --detect --json

# Preview without writing
python tools/dx/companion.py --setup --all --dry-run --json

# Regenerate after icdev.yaml changes
python tools/dx/companion.py --sync --write
```

**Also create:** `tools/dx/__init__.py` (CUI header only)

---

### Step 7: Manifest Loader Update

**Modify:** `tools/project/manifest_loader.py`

Add support for `companion:` section in icdev.yaml:

```yaml
companion:
  tools: [claude_code, codex, copilot, cursor, gemini]
  auto_sync: true
  instruction_style: full
```

Changes:
- Add `companion` to the default config structure in `_apply_defaults()`
- Add validation for companion tool names in `validate_manifest()`
- No new env var overrides needed

---

### Step 8: Tests

**Create:** `tests/test_tool_detector.py` (~80 LOC)
- test_detect_claude_code (config dir exists)
- test_detect_codex (env var set)
- test_detect_nothing (empty directory)
- test_detect_multiple (claude + cursor)
- test_primary_selection (highest confidence wins)

**Create:** `tests/test_instruction_generator.py` (~200 LOC)
- test_agents_md_content (project data rendered)
- test_gemini_md_content
- test_cursor_mdc_frontmatter (MDC format correct)
- test_copilot_instructions_content
- test_all_platforms (generates for all 10)
- test_write_flag_creates_files
- test_dry_run_no_write
- test_minimal_style (shorter output)
- test_missing_manifest_still_works (graceful degradation)
- test_collect_project_data_structure

**Create:** `tests/test_mcp_config_generator.py` (~100 LOC)
- test_codex_toml_format
- test_amazon_q_json_format
- test_gemini_json_format
- test_cline_json_format
- test_ide_setup_instructions (Cursor/Windsurf/JetBrains)
- test_all_servers_included

**Create:** `tests/test_skill_translator.py` (~120 LOC)
- test_parse_claude_skill (frontmatter + body)
- test_codex_skill_format
- test_copilot_prompt_format
- test_cursor_mdc_format
- test_all_skills_translated
- test_step_content_preserved

**Create:** `tests/test_companion.py` (~80 LOC)
- test_setup_all (orchestrates everything)
- test_detect_mode
- test_sync_mode
- test_dry_run

---

### Step 9: Documentation & CLAUDE.md Updates

**Modify:** `docs/dx/README.md`
- Update Tier 2 description: "Conversational" → "Conversational (Claude Code, Codex, Gemini, Copilot, Cursor, and more)"
- Add new doc row for "AI Tool Companion Guide"

**Create:** `docs/dx/companion-guide.md` (~150 lines)
- Overview: ICDEV works with 10 AI coding tools
- Quick setup: `python tools/dx/companion.py --setup --write`
- Per-tool setup instructions (condensed)
- MCP integration guide
- FAQ

**Modify:** `tools/manifest.md`
- Add DX Companion section with all new tools

**Modify:** `CLAUDE.md`
- Add ADRs D194-D198
- Add CLI commands for companion tools
- Add Existing Goals table entry: "AI Companion"
- Update Session Start Protocol to mention companion detection

---

## Dependency Graph

```
Step 1 (registry) ──┬──> Step 2 (detector)
                    ├──> Step 3 (instruction gen)
                    ├──> Step 4 (mcp config gen)
                    └──> Step 5 (skill translator)
                              |
Steps 2-5 ──────────> Step 6 (companion CLI)
Step 1 ─────────────> Step 7 (manifest_loader update)
Steps 1-7 ──────────> Step 8 (tests)
Steps 1-8 ──────────> Step 9 (docs + CLAUDE.md)
```

Steps 2, 3, 4, 5, 7 can run in parallel after Step 1.

---

## Verification

1. **Auto-detect**: `python tools/dx/tool_detector.py --json` — should detect Claude Code (we're in it)
2. **Generate AGENTS.md**: `python tools/dx/instruction_generator.py --platform codex --dry-run --json` — valid AGENTS.md content
3. **Generate all**: `python tools/dx/companion.py --setup --all --dry-run --json` — all 10 platforms
4. **Write files**: `python tools/dx/companion.py --setup --all --write` — creates AGENTS.md, GEMINI.md, .cursor/rules/icdev.mdc, etc.
5. **MCP configs**: `python tools/dx/mcp_config_generator.py --platform codex --dry-run` — valid TOML
6. **Skills**: `python tools/dx/skill_translator.py --platform codex --skills icdev-build --dry-run` — valid Codex skill
7. **Tests**: `pytest tests/test_tool_detector.py tests/test_instruction_generator.py tests/test_mcp_config_generator.py tests/test_skill_translator.py tests/test_companion.py -v`
