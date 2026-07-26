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
