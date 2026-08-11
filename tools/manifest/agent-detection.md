# Agent Detection (AGOV / DET)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Agent Detection
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Findings Store | tools/agent_detect/findings.py | Append-only store for detection-rule findings (`agent_findings`, migration 20260809201320). Deterministic `finding_id` so re-observing one chain does not append twice; degrades to the `hook_events` trail when the table is absent and never raises into the caller | (library) `record(rule_id=..., event_ids=[...])`, `list_findings(session_id=...)` | `{finding_id, persisted, sink, duplicate}` |

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
