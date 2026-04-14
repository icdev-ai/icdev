# CLI Output Formatting

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## CLI Output Formatting
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Output Formatter | tools/cli/output_formatter.py | Human-friendly CLI output: colored tables, banners, scores, pipelines, key-value pairs | --human flag on any tool | Formatted terminal output |
| Enable/Disable Toggles | tools/cli/enable.py | Manage canvas and subsystem feature toggles in .env atomically; supports enable, disable, status, list subcommands | toggle names, --env-file, --json | Updated .env flags; JSON or human-readable status table |

