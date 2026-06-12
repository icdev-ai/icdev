# ANVIL Headless Commands (OPT-42, 2026-04-12)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## ANVIL Headless Commands (OPT-42, 2026-04-12)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| ANVIL Runner | tools/anvil/runner.py | Shared runner — parses .claude/commands/*.md or delegates to tools/skills/invoke.py for icdev-* skills; allowlisted command execution, $ARGUMENTS substitution, PYTHONPATH propagation | (library) | run_command() / run_skill() |
| anvil feature | tools/anvil/feature.py | Headless wrapper for .claude/commands/feature.md | --json, --dry-run, --keep-going, --timeout, -- ARGS | Step summary |
| anvil bug | tools/anvil/bug.py | Headless wrapper for .claude/commands/bug.md | ibid | ibid |
| anvil chore | tools/anvil/chore.py | Headless wrapper for .claude/commands/chore.md | ibid | ibid |
| anvil test | tools/anvil/test.py | Headless wrapper for .claude/commands/test.md | ibid | ibid |
| anvil review | tools/anvil/review.py | Headless wrapper for .claude/commands/review.md | ibid | ibid |
| anvil commit | tools/anvil/commit.py | Headless wrapper for .claude/commands/commit.md | ibid | ibid |
| anvil status | tools/anvil/status.py | Headless wrapper — delegates to skill `icdev-status` via OPT-41 invoker | ibid | ibid |
| anvil monitor | tools/anvil/monitor.py | Headless wrapper — delegates to skill `icdev-monitor` | ibid | ibid |
| anvil maintain | tools/anvil/maintain.py | Headless wrapper — delegates to skill `icdev-maintain` | ibid | ibid |
| anvil secure | tools/anvil/secure.py | Headless wrapper — delegates to skill `icdev-secure` | ibid | ibid |
| anvil deploy | tools/anvil/deploy.py | Headless wrapper — delegates to skill `icdev-deploy` | ibid | ibid |

