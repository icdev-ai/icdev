# Skill Invocation (OPT-41, 2026-04-12)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Skill Invocation (OPT-41, 2026-04-12)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Skill Registry | tools/skills/registry.py | Parses .agents/skills/icdev-*/SKILL.md — frontmatter, commands, MCP refs — into tools/skills/registry.json | --rebuild, --list, --get SKILL, --json | Registry JSON |
| Skill Invoker | tools/skills/invoke.py | Headless CLI invoker — runs documented commands for each skill; allowlisted to `python tools/`, `python -m tools`, `python -c` only | --list, --show SKILL, --dry-run SKILL, --exec SKILL [-- ARGS...], --keep-going, --timeout, --json | Per-step stdout/stderr/rc + summary |

