# Safety Hooks

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Safety Hooks
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Pre-Tool-Use Hook | .claude/hooks/pre_tool_use.py | Blocks dangerous rm, .env access, audit modifications | tool_name, tool_input | Allow/block |

