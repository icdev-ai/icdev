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
