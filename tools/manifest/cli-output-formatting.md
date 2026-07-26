# CLI Output Formatting

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## CLI Output Formatting
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Output Formatter | tools/cli/output_formatter.py | Human-friendly CLI output: colored tables, banners, scores, pipelines, key-value pairs | --human flag on any tool | Formatted terminal output |
| Enable/Disable Toggles | tools/cli/enable.py | Manage canvas and subsystem feature toggles in .env atomically; supports enable, disable, status, list subcommands | toggle names, --env-file, --json | Updated .env flags; JSON or human-readable status table |
| Project Init | tools/cli/init.py | `icdev init` — scaffolds a new ICDEV™ project from the installed package by copying the FORGE orchestration layer (goals/, tools/, args/, context/, hardprompts/, CLAUDE.md) into the user's working directory; prompts for an install profile (or `--profile <name>`/`none`, non-TTY-safe) that shapes the generated .env | destination path, `--profile`, `--force`, `--minimal`, `--list`, `--json` | Scaffolded project files + complete .env |
| Env Generator | tools/cli/env_generator.py | Registry-driven .env composer used by `icdev init`: renders EVERY component env flag from args/component_registry.yaml (enabled live, rest commented with name/URL/impact) and composes it onto the template's non-component keys; honors an install profile's enabled components + env overrides | template text, registry, optional enabled_keys/env_overrides | Complete commented .env text |
| Audit Export CLI | tools/cli/audit.py | `icdev audit` — compliance evidence export subcommand; supports `export` with SOC2 framework, optional evidence collection, HTML/JSON output formats | --framework, --tenant-id, --format, --output, --collect, --since, --json | Compliance evidence report (HTML or JSON); coverage summary |
| Project Init | tools/cli/init.py | `icdev init` — scaffolds a new ICDEV™ project from the installed package by copying the FORGE orchestration layer (goals/, tools/, args/, context/, hardprompts/, CLAUDE.md) into the user's working directory | destination path, --json | Scaffolded project files |

