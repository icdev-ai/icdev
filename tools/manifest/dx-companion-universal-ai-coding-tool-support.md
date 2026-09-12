# DX Companion — Universal AI Coding Tool Support (D194-D198)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## DX Companion — Universal AI Coding Tool Support (D194-D198)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Companion CLI | tools/dx/companion.py | Single entry point: detect tools, generate instructions, MCP configs, translate skills (D194) | --setup, --detect, --list, --platforms, --write, --json | Summary + file paths |
| Tool Detector | tools/dx/tool_detector.py | Detect installed AI coding tools from env, config dirs, config files (D197) | --dir, --json | Detected tools + confidence |
| Instruction Generator | tools/dx/instruction_generator.py | Generate instruction files for 9 AI tools from Jinja2 templates (D195) | --platform, --all, --write, --json | Instruction file content + paths |
| MCP Config Generator | tools/dx/mcp_config_generator.py | Translate .mcp.json to tool-specific MCP config formats (D196) | --platform, --all, --write, --json | Config file content + paths |
| Skill Translator | tools/dx/skill_translator.py | Translate Claude Code skills to Codex/Copilot/Cursor formats (D198) | --platform, --all, --skills, --write, --json | Translated skill content + paths |
| Companion Registry | args/companion_registry.yaml | Declarative registry of 10 supported AI coding tools (D194) | (data) | Tool definitions |
| Mirror Parity Auditor | tools/dx/mirror_parity.py | Byte-level (SHA256) parity audit of tools/<path> vs icdev/tools/<path>; reconciles drift by copying tools/→icdev/ (never deletes icdev-only) | --paths, --fix, --gate, --json | Per-subtree drift report |


## Mirror Parity — full-repo sweep (2026-07-26)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Mirror Parity | tools/dx/mirror_parity.py | SHA256 parity audit of `tools/` vs `icdev/tools/`. `--all` auto-discovers every package with an icdev twin (200 today) rather than using a curated list — `coherence_checker.check_mirror_drift` audits 8 named packages and only `*.py`, which is why a drifted `pg_consolidated.sql` was invisible. Public API: `discover_mirrored_paths()`, `audit_all(fix=False)`, `audit_path(subpath, fix=False)`. Baseline: `args/mirror_drift_baseline.yaml`, gated by `tests/test_mirror_drift_baseline.py`. | `--all [--json] [--fix] [--gate]`, or `--paths a,b` | JSON `{packages_audited, packages_with_drift, content_drift, missing_from_mirror, mirror_only, reports}` |

## External Tool Index — declared PATH binaries (xrv-route-01)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| External Tool Index | tools/dx/tool_index.py | ONE declared list of the external BINARIES ICDEV shells to (`args/tool_index.yaml`, 36 entries), probed on PATH with a three-state verdict. `which(name)` is the ONE lookup new code calls — it refuses an undeclared name rather than silently answering, and an excluded name raises carrying the reason it is excluded. `present` requires the binary to ANSWER its `version_cmd`; `unmeasurable` (on PATH, would not answer) is never folded into `present` or `absent`. `meets_min` is True/False/None and never False for an unmeasurable comparison; `present_pct` is None over an empty denominator. `--call-sites` re-derives consumers from the tree with `ast`, so `used_by` (principal consumers, not exhaustive) is checkable rather than rotting. Consumed by `health_check.check_external_tools`. Report only; no gate. | `--refresh [--json]`, `--name <n>`, `--validate`, `--call-sites <n>`, `--index <path>` | JSON `{declared, counts{present,absent,unmeasurable}, present_pct, required_absent, below_min_version, os_overrides_declared, excluded[], tools[]}` |
