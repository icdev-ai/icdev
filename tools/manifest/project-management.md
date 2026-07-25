# Project Management

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Project Management
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Project Create | tools/project/project_create.py | Create project with scaffolding | --name, --type, --classification | Project ID |
| Project List | tools/project/project_list.py | List all projects | --format | Project table |
| Project Status | tools/project/project_status.py | Project status report | --project, --format | Status report |
| Project Scaffold | tools/project/project_scaffold.py | Generate project directory structure | --project-id, --type | Directory tree |
| Manifest Loader | tools/project/manifest_loader.py | Parse/validate icdev.yaml, apply IL defaults, env overrides (D189, D193) | --dir, --file, --validate, --json | Normalized config + errors/warnings |
| Validate Manifest | tools/project/validate_manifest.py | CLI validator for icdev.yaml (thin wrapper) | --file, --dir, --json | Valid/invalid + errors |
| Session Context Builder | tools/project/session_context_builder.py | Build session context for Claude Code — project, compliance, profile, workflows (D190) | --dir, --db, --format, --init, --json | Markdown or JSON context |
| Kanban Project Sync | tools/project/kanban_project_sync.py | Auto-upsert projects.yaml from kanban task ID prefixes — called after every POST /api/kanban/tasks | --dry-run, --json | Report: new_projects, updated_projects, written |

| Prefix Scope | tools/project/prefix_scope.py | Nested task-prefix scoping for Home project cards. `child_prefixes(prefix, all)` returns prefixes strictly nested under a parent (`aadc-enh-`/`aadc-sp-` under `aadc-`) so the parent's queries can subtract them; `duplicate_prefixes(all)` flags exact duplicates, the only unresolvable case. Replaces the old drop-on-collision behavior in `_validate_projects` that silently hid whichever colliding card appeared later in projects.yaml. Pure functions — no DB, no Flask. | (library) child_prefixes(prefix, all_prefixes) / duplicate_prefixes(all_prefixes) | list[str] |
