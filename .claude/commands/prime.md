# Prime — ICDEV Codebase Orientation

> Execute the following sections to understand the ICDEV codebase, then summarize your understanding.

## Run

```bash
git ls-files | head -80
```

## Read

1. `CLAUDE.md` — Full system reference (GOTCHA framework, ATLAS workflow, all tools/commands)
2. `goals/manifest.md` — Index of all goal workflows
3. `tools/manifest.md` — Master list of all tools
4. `memory/MEMORY.md` — Long-term facts and preferences (if exists)
5. `.claude/commands/conditional_docs.md` — Guide for which documentation to read based on upcoming tasks

## Load Context

```bash
python tools/project/session_context_builder.py --format markdown 2>/dev/null || echo "No project context available — run /icdev-init first"
```

## Summarize

After reading the above, provide a concise summary of:
- The GOTCHA framework layers and how they interact
- Active goals and their current state
- Available tools and MCP servers
- Any memory/preferences from previous sessions
- The project's compliance posture (if initialized)
