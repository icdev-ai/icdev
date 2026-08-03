# Skill Invocation (OPT-41, 2026-04-12)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Skill Invocation (OPT-41, 2026-04-12)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Skill Registry | tools/skills/registry.py | Parses .agents/skills/icdev-*/SKILL.md — frontmatter, commands, MCP refs, and the optional `paths:`/`tools:` scoping fields — into tools/skills/registry.json. `SCHEMA_VERSION` bumps force a rebuild so a stale committed cache can never drop a field | --rebuild, --list, --get SKILL, --json | Registry JSON |
| Skill Invoker | tools/skills/invoke.py | Headless CLI invoker — runs documented commands for each skill; allowlisted to `python tools/`, `python -m tools`, `python -c` only | --list, --show SKILL, --dry-run SKILL, --exec SKILL [-- ARGS...], --keep-going, --timeout, --json | Per-step stdout/stderr/rc + summary |

## Skill Capability Scoping (ars-scope-01, 2026-08-02)

A `SKILL.md` may declare two OPTIONAL frontmatter fields; both narrow the invoker's existing allowlist and never widen it. Omitting them keeps a skill's behaviour identical to before.

| Field | Constrains | Enforced against |
|-------|-----------|------------------|
| `paths:` | What the skill acts **on** | Every path-like operand of a documented command, checked *after* `$ARGUMENTS` substitution so a caller cannot widen the scope through arguments. The command's own script path is exempt. |
| `tools:` | What the skill acts **with** | The tool module executed — script path, `-m` module, or `tools.x.y` imports inside `python -c`. Accepts `tools/db/`, `tools/db/storage.py`, `tools.db`, or a glob. An unresolvable target fails closed. |

`tools:` is not `allowed-tools:` — the latter remains the Claude Code agent tool list and is unchanged.

Enforcement lives in `invoke.py` at the same seam as `_ALLOWED_PREFIXES` (`check_scope()` → `run_command()`/`invoke_skill()`), so it applies to every caller including `tools/anvil/runner.py` and `tools/agent_runtime/`. A violation is a hard stop: the command is never spawned, the remaining steps are abandoned even under `--keep-going`, and the CLI exits 1. `--dry-run` runs the same check without executing, so it doubles as a static scope audit. Scope is re-read from the live `SKILL.md` at invoke time rather than trusted from `registry.json`, because a stale cache would fail open. Tests: `tests/skills/test_scope.py`, `tests/skills/e2e_skill_invoke.py`. Authoring guide: `.agents/skills/README.md`.

