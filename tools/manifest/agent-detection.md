# Agent Detection (AGOV / DET)

Declarative detection over the agent activity ICDEV already writes. Read-side
only: no new event table, and nothing here enforces anything unless an operator
opts a rule into `enforce: true`.

Append rows; never rewrite a neighbouring row. `tools/manifest/*.md` is
`merge=union` in `.gitattributes` (kax-conflict-03).

| Tool | Purpose | Input | Output |
|------|---------|-------|--------|
| `tools/agent_detect/shell_parse.py` | Parsed shell-command view (agov-det-02) — the cause-level fix for the flattened-string fail-open at `args/agent_approval_policy.yaml`:107-126, where a `git_push` carrying `{"note": "mkdir logs"}` flattened to `git_push note=mkdir logs` and matched the `mkdir` DOWNGRADE pattern. Matching a parsed statement name cannot be satisfied by a word inside an argument. **Only a static simple command or a POSIX pipeline parses**; command substitution, parameter expansion, control flow, `;`/`&&`/`&`, subshells, brace groups, heredocs, `eval`/`source`/`exec` and an unterminated quote all yield `parsed=False` with a stable `reason` and NO statements — and a rule requiring the parsed view MUST decline there rather than fall back to substring matching, which is the original bug. Wrappers (`sudo`, `env`, `nohup`, `setsid`, `timeout`, `xargs`, `nice`, `ionice`, `stdbuf`, `doas`, `command`) are peeled to the real command with an explicit per-wrapper option table; an unknown option refuses rather than guessing where the options end. A quoted operator (`echo ">" > out`) is ambiguous once shlex strips quotes, so it refuses too. `2>err` keeps its fd, `echo 2 > x` keeps the `2` in argv. Ids are SHA-256-derived from the command, so they are stable across runs and replay-safe. stdlib only (`shlex`) and no first-party import — agov-det-06 calls it from `.claude/hooks/pre_tool_use.py`, a fresh interpreter per tool call. | `parse_command(command, dialect="posix")`, `parse_event(event)`, `command_names(command)`, `iter_statements(parsed)`, `dialect_for_tool(tool_name)` | `ParsedCommand(command, dialect, parsed, statements, reason, pipeline_id)`; `ParsedStatement(name, argv, arguments, assignments, redirects, wrappers, statement_id, pipeline_id, index, dialect, parsed)`; `.names` / `.argv` / `.to_dict()` |

Library module — imported, not run. The operator CLI is agov-det-07.

A parse REFUSAL IS NOT A SAFETY VERDICT — it means this parser declined, not
that the command was harmless.
