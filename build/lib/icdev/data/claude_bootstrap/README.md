# ICDEV™ Claude Bootstrap

This directory ships with the `icdev` PyPI package. It contains the
FORGE orchestration layer that makes Claude Code work with ICDEV™:

- **CLAUDE.md** — master instruction file for Claude Code
- **mcp.json** — MCP server configuration
- **.env.template** — environment variable template
- **claude/commands/** — slash command definitions (`/prime`, `/commit`, etc.)
- **claude/hooks/** — session hooks (stop, pre_tool_use, post_tool_use)
- **claude/skills/** — ICDEV-specific Claude skills

## Bootstrap a new project

```bash
mkdir my-icdev-project && cd my-icdev-project
icdev init            # copies this bootstrap into cwd
icdev-init-db         # initializes the databases
icdev-dashboard       # starts the dashboard on :5050
```

After `icdev init`, the project is ready — open it in Claude Code
and the agent will follow CLAUDE.md.
