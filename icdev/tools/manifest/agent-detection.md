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
| `tools/agent_detect/rules.py` | YAML rule loader + single-event evaluator (agov-det-03). One rule per file under `args/agent_rules/**/*.yaml`: `id` (dot-separated), `version`, `title`, `severity`, `tags`, `enabled` (default true), `enforce` (default **false**), `deny_message` (≤512 bytes) and exactly one of `expr` or `sequence`. Conditions are STRUCTURED YAML MATCHERS, not an expression language — decided against CEL (a new third-party dependency in an air-gap/DoD repo) and against a restricted-AST evaluator (a security surface ICDEV would own and defend). Keys: `event_type`, `tool_name`, `actor`, `file_path_glob`, `command_name`, `command_matches`, `url_matches`, `argv_contains`, plus `not_` negation of each. Keys AND; the list under a key ORs; a scalar is a one-element list. **Fail safe, not open**: an unknown key, an uncompilable regex, an empty `expr`, an empty value list, a non-boolean `enforce`, both/neither of `expr`/`sequence`, or a duplicate id skips the WHOLE rule into `RuleSet.errors` with its file path — never a partial matcher (dropping the offending clause loosens the surviving AND into a match-all) and never an exception at the caller. An absent/empty rules directory yields an empty `RuleSet`, not an error. `command_name` and `argv_contains` read the agov-det-02 parsed view and do NOT fall back to substring matching on the raw command — that fallback is the documented flattened-string fail-open at `args/agent_approval_policy.yaml`:107-126. Compiled rules are cached by directory stat-signature for the latency-critical hook path (agov-det-06). Loader modelled on `tools/agent_runtime/approval_gate.py` and the tier loader in `tools/hooks/shared_checks.py`. `ICDEV_AGENT_RULES_DIR` overrides the directory. | `load_rules(rules_dir=None, refresh=False)`, `evaluate_event(event, ruleset)`, `compile_matcher(dict)`, `matches(matcher, event)`, `clear_cache()` | `RuleSet(rules, errors, directory)`; `list[RuleMatch]` (`rule_id`, `rule_version`, `severity`, `enforce`, `deny_message`, `event_id`, `session_id`, `matched_keys`) |

Library module — imported, not run. The operator CLI (`--list` / `--check` /
`--test` / `--scan`) is agov-det-07; persistence to `agent_findings` is
agov-det-05; the chain evaluator that consumes a loaded `sequence` rule is
agov-det-04 (`RuleSet.sequence_rules` is where it picks them up).

A finding is a RULE MATCH AND NOT PROOF OF EXECUTION.
