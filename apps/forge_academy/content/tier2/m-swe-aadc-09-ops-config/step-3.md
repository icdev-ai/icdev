---
ontology_id: icdev:mission:m-swe-aadc-09-ops-config:step:3
step_class: icdev:Assessment
---

<!-- CUI // SP-CTI -->

# Ops Config Review

## Minimum Viable Production Design

Passing the AADC assessment and generating a config are not the same as being production-ready. A design can score above the passing threshold while still missing critical operational nodes. Before treating any AADC design as production-eligible, verify it contains at minimum:

| Required Node | Paired With | Rationale |
|--------------|-------------|-----------|
| `drift-detector` | `baseline-snapshot` | Without a baseline, drift detection has no reference point — it fires on everything or nothing |
| `token-budget` | *(standalone)* | Any autonomous agent left without a budget cap has created a billing incident in every known production failure mode |
| `guardrail` or `input-sanitizer` | *(either)* | Prompt injection defense is non-negotiable at IL4; the guardrail covers output too, input-sanitizer is input-only |
| `audit-logger` | *(standalone)* | NIST AU-2 compliance requirement; append-only, never omit |
| `circuit-breaker` | *(required for autonomous agents)* | Any agent with a tool-use loop must have a termination condition outside the agent's own judgment |

The generator surfaces gaps in the `unmatched_nodes` field of the JSON response — but that field only reports nodes present in your graph that have no mapping entry. It cannot tell you about nodes you did not add. The design completeness check is your responsibility, not the generator's.

## Idempotency and Kanban Deduplication

Running the generator twice on the same design is safe. The YAML at `args/ops_config_<design_id>.yaml` is overwritten with refreshed defaults on every run. If you have made manual edits to the config file, those edits are lost — treat the generated file as a starting point to copy from, not as your live configuration.

Kanban task creation uses a deduplication check: before inserting a new task, the generator queries for any existing backlog task where `design_id` and `node_type` both match. If a match is found, the task is skipped and counted in `tasks_skipped` in the response. This means you can safely re-run after adding new nodes to your design — only new node types produce new tasks.

```json
{
  "config_path": "args/ops_config_42.yaml",
  "tasks_created": 2,
  "tasks_skipped": 3,
  "unmatched_nodes": ["custom-classifier"],
  "il_level": "IL4"
}
```

An entry in `unmatched_nodes` is an action item, not an error. Either add the node type to `args/aadc_node_tool_map.yaml` or remove the node from your design if it was added by mistake.

## Integration with FORGE IGNITE

The Ops Config Generator is not only invoked from the CLI. When an idea in FORGE IGNITE reaches the Pilot stage and carries an `aadc_design_id` on its record, the IGNITE scorecard renders a "Generate Ops Config →" button. Clicking it runs the generator in `--create-tasks` mode against the associated design and pre-populates the pilot's monitoring plan section with the resulting config path and task IDs.

This integration closes the loop between ideation and operational readiness: a pilot that reaches Pilot stage without a monitoring plan is now visibly incomplete in the UI, not just in a checklist.

## Classification and Source Control

The generated config file lives in `args/` and is included in `.gitignore` for cloud-connected repositories. It is a CUI artifact: it contains operational thresholds, tool paths, and alert routing that reveal system architecture. Do not commit it to a public or unclassified repository.

For air-gapped environments, the `args/` directory is managed through the classified transfer process documented in `docs/ops/airgap-runbook.md`. The YAML format is human-readable so it can be reviewed during the transfer process without tooling.

## Reflection Questions

1. Your design has a `drift-detector` but no `baseline-snapshot`. The generator creates a task and writes the config. What does the drift detector actually compare against on first run?

2. A teammate regenerated the ops config after updating the token budget threshold manually in the YAML. Their manual edit is gone. What process change prevents this from happening again?

3. The `unmatched_nodes` response field contains `["semantic-cache"]`. You did not add that node type to the map. What are your two options, and when would you choose each?

**Your task:** Answer the reflection questions to complete this mission.
