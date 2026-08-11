# Agent Detection (AGOV / DET)

Declarative detection over the agent activity ICDEV already writes. Read-side
only: no new event table, and nothing here enforces anything unless an operator
opts a rule into `enforce: true`.

Append rows; never rewrite a neighbouring row. `tools/manifest/*.md` is
`merge=union` in `.gitattributes` (kax-conflict-03).

| Tool | Purpose | Input | Output |
|------|---------|-------|--------|
| `tools/agent_detect/rules.py` | YAML rule loader + single-event evaluator (agov-det-03). One rule per file under `args/agent_rules/**/*.yaml`: `id` (dot-separated), `version`, `title`, `severity`, `tags`, `enabled` (default true), `enforce` (default **false**), `deny_message` (≤512 bytes) and exactly one of `expr` or `sequence`. Conditions are STRUCTURED YAML MATCHERS, not an expression language — decided against CEL (a new third-party dependency in an air-gap/DoD repo) and against a restricted-AST evaluator (a security surface ICDEV would own and defend). Keys: `event_type`, `tool_name`, `actor`, `file_path_glob`, `command_name`, `command_matches`, `url_matches`, `argv_contains`, plus `not_` negation of each. Keys AND; the list under a key ORs; a scalar is a one-element list. **Fail safe, not open**: an unknown key, an uncompilable regex, an empty `expr`, an empty value list, a non-boolean `enforce`, both/neither of `expr`/`sequence`, or a duplicate id skips the WHOLE rule into `RuleSet.errors` with its file path — never a partial matcher (dropping the offending clause loosens the surviving AND into a match-all) and never an exception at the caller. An absent/empty rules directory yields an empty `RuleSet`, not an error. `command_name` and `argv_contains` read the agov-det-02 parsed view and do NOT fall back to substring matching on the raw command — that fallback is the documented flattened-string fail-open at `args/agent_approval_policy.yaml`:107-126. Compiled rules are cached by directory stat-signature for the latency-critical hook path (agov-det-06). Loader modelled on `tools/agent_runtime/approval_gate.py` and the tier loader in `tools/hooks/shared_checks.py`. `ICDEV_AGENT_RULES_DIR` overrides the directory. | `load_rules(rules_dir=None, refresh=False)`, `evaluate_event(event, ruleset)`, `compile_matcher(dict)`, `matches(matcher, event)`, `clear_cache()` | `RuleSet(rules, errors, directory)`; `list[RuleMatch]` (`rule_id`, `rule_version`, `severity`, `enforce`, `deny_message`, `event_id`, `session_id`, `matched_keys`) |

Library module — imported, not run. The operator CLI (`--list` / `--check` /
`--test` / `--scan`) is agov-det-07; persistence to `agent_findings` is
agov-det-05; the chain evaluator that consumes a loaded `sequence` rule is
agov-det-04 (`RuleSet.sequence_rules` is where it picks them up).

A finding is a RULE MATCH AND NOT PROOF OF EXECUTION.
> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Agent Detection
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Findings Store | tools/agent_detect/findings.py | Append-only store for detection-rule findings (`agent_findings`, migration 20260809201320). Deterministic `finding_id` so re-observing one chain does not append twice; degrades to the `hook_events` trail when the table is absent and never raises into the caller | (library) `record(rule_id=..., event_ids=[...])`, `list_findings(session_id=...)` | `{finding_id, persisted, sink, duplicate}` |
| Pre-tool-use Gate | tools/agent_detect/gate.py | The decision seam (agov-det-06). Reached from `.claude/hooks/pre_tool_use.py` and `tools/airgap/hook_compat.py::run_pre_tool_check` via `shared_checks.check_agent_rules`, so the interactive and headless paths cannot drift. Runs LAST — after every hardcoded block — and is additive: it can only add a refusal. **Enforcement authority is a DIRECTORY, not a flag**: a rule blocks only when it sets `enforce: true` AND lives in `args/agent_rules_enforce/` (`ICDEV_AGENT_ENFORCE_RULES_DIR`), which ships with no rule files; matches from the shipped pack are forced monitor-only, so flipping `enforce` there is inert. Detection and enforcement run the SAME matcher. Fails OPEN on every internal error. Latency (hook = a fresh interpreter per tool call, measured +16ms with the 14-rule seed pack): no first-party import at module scope, a zero-rule fast path that never loads the engine, a JSON side-cache for the monitor pack only, a bounded session trail instead of a DB read, and a last-step prefilter that skips the chain search for a call that cannot complete one. `ICDEV_AGENT_DETECT=0` removes it entirely | (library) `evaluate_tool_call(tool_name, tool_input, session_id=..., record=True)`, `check_tool_call(...)`, `normalize_tool_call(...)`, `read_trail(session_id)` | `GateDecision(allowed, reason, rule_id, matches, findings, skipped)`; `check_tool_call` returns the deny reason or `None` |

The seed rule pack lives in `args/agent_rules/` — **every shipped rule sets
`enforce: false`**. Monitor-only by default is the safety design, not a
placeholder: a pack that blocks on install takes down live sessions on its first
false positive. Enforcement is opted into per rule by an operator (agov-det-06).
See `args/agent_rules/README.md` for the schema and `tests/test_agov_rule_pack.py`
for the gate that holds it.

`agent_findings` is registered in `APPEND_ONLY_TABLES` in
`.claude/hooks/pre_tool_use.py`. A finding is an observation with no lifecycle,
so a re-evaluation appends rather than edits; mutable triage state, if it is ever
wanted, belongs in a separate table keyed on `finding_id` — the same split the
INBOX epic makes between `approval_items` and `agent_approval_log`.
| `tools/agent_detect/shell_parse.py` | Parsed shell-command view (agov-det-02) — the cause-level fix for the flattened-string fail-open at `args/agent_approval_policy.yaml`:107-126, where a `git_push` carrying `{"note": "mkdir logs"}` flattened to `git_push note=mkdir logs` and matched the `mkdir` DOWNGRADE pattern. Matching a parsed statement name cannot be satisfied by a word inside an argument. **Only a static simple command or a POSIX pipeline parses**; command substitution, parameter expansion, control flow, `;`/`&&`/`&`, subshells, brace groups, heredocs, `eval`/`source`/`exec` and an unterminated quote all yield `parsed=False` with a stable `reason` and NO statements — and a rule requiring the parsed view MUST decline there rather than fall back to substring matching, which is the original bug. Wrappers (`sudo`, `env`, `nohup`, `setsid`, `timeout`, `xargs`, `nice`, `ionice`, `stdbuf`, `doas`, `command`) are peeled to the real command with an explicit per-wrapper option table; an unknown option refuses rather than guessing where the options end. A quoted operator (`echo ">" > out`) is ambiguous once shlex strips quotes, so it refuses too. `2>err` keeps its fd, `echo 2 > x` keeps the `2` in argv. Ids are SHA-256-derived from the command, so they are stable across runs and replay-safe. stdlib only (`shlex`) and no first-party import — agov-det-06 calls it from `.claude/hooks/pre_tool_use.py`, a fresh interpreter per tool call. | `parse_command(command, dialect="posix")`, `parse_event(event)`, `command_names(command)`, `iter_statements(parsed)`, `dialect_for_tool(tool_name)` | `ParsedCommand(command, dialect, parsed, statements, reason, pipeline_id)`; `ParsedStatement(name, argv, arguments, assignments, redirects, wrappers, statement_id, pipeline_id, index, dialect, parsed)`; `.names` / `.argv` / `.to_dict()` |

Library module — imported, not run. The operator CLI is agov-det-07.

A parse REFUSAL IS NOT A SAFETY VERDICT — it means this parser declined, not
that the command was harmless.
| `tools/agent_detect/cli.py` | Operator CLI for the detection rule pack (agov-det-07). `--list` catalogs loaded rules (id, severity, kind, enforce, source path) plus skipped files and why. `--check --rules-dir <dir>` validates a directory and **exits non-zero on any invalid rule** — the load-bearing verb, because an invalid rule is INERT rather than match-all, so a typo in an enforcement directory produces something that enforces nothing and reports nothing, and the exit code is the only signal there is; it also catches a `sequence` block the loader's shape check accepts but `SequenceSpec` rejects (`max_matches: 99`), which loads clean and then never evaluates. `--test` evaluates the pack against declared-expectation fixtures in `context/agent_detect/fixtures/` and fails on zero cases, because a green run that evaluated nothing is the overstated artifact the coverage doc exists to counter. `--scan --session <id>` evaluates stored events via the agov-det-01 normalizer, read-only unless `--record`, which appends as `decision="observed"` / `enforced=False` — the CLI runs after the fact and has nothing left to deny. Exit codes: 0 passed, 1 a check failed, 2 usage. Coverage and known-missing: `docs/features/agov-det-coverage-and-limits.md`. | `--list` / `--check` / `--test` / `--scan --session <id>`, `--rules-dir`, `--fixtures`, `--limit`, `--record`, `--json` | JSON per verb: `{rules, errors, count}` / `{ok, invalid, errors}` / `{ok, cases, results}` / `{events_scanned, event_matches, chain_matches, recorded}` |

`--check` is the verb to run before copying a rule into `args/agent_rules_enforce/`. A finding is a RULE MATCH AND NOT PROOF OF
EXECUTION — `docs/features/agov-det-coverage-and-limits.md` carries the
per-source fidelity table and the known-missing list.
