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

The generator surfaces gaps in `unmatched_nodes`, but that field only reports node types present in your graph that have no mapping entry. It cannot tell you about nodes you did not add. The design completeness check is your responsibility, not the generator's.

## Re-running the Generator

Re-running on the same design is safe for the YAML: `data/aadc_ops_config/ops_config_<design_id>.yaml` is overwritten with refreshed defaults every time. If you edited that file by hand, those edits are lost, so treat the generated file as a starting point to copy from, not as your live configuration.

It is **not** safe for the board. `create_kanban_tasks()` inserts every task it is given, with no lookup for an existing task on the same design and node type. Each `--create-tasks` run (or each click on the ops-config page, which creates tasks by default) adds another full set. Generate the YAML as often as you like; create tasks once.

```json
{
  "config_path": ".../data/aadc_ops_config/ops_config_aadc-1a2b3c4d.yaml",
  "matched_nodes": ["drift-detector", "guardrail", "token-budget"],
  "unmatched_nodes": ["orchestrator", "semantic-cache"],
  "kanban_tasks_count": 3,
  "created_task_ids": []
}
```

An entry in `unmatched_nodes` is an action item, not an error. Either add the node type to `args/aadc_node_tool_map.yaml` or accept that it has no runtime tool. Agent nodes such as `orchestrator` are expected there.

## Integration with FORGE IGNITE

The generator is also reachable from FORGE IGNITE. On an idea's detail page, when the idea carries an `aadc_design_id` and has a detailed feasibility study, a **Generate Ops Config →** button links to `/agentic-ai/canvas/<aadc_design_id>/ops-config`, the same page the canvas opens. A pilot can go from idea to monitoring plan without hunting for the design.

## Classification and Source Control

The generated file starts with a `# CUI // SP-CTI` header. It is a CUI artifact: operational thresholds, tool paths and alert routing reveal system architecture. Because it is written under `data/` rather than `args/`, it stays out of the source-controlled config directory. Do not copy it into a public or unclassified repository.

For air-gapped environments, follow the transfer process in `docs/ops/airgap-runbook.md`. The YAML is human-readable so it can be reviewed during transfer without tooling.

## Reflection Questions

Answer the fields on this step:

1. **What ops/safety nodes are NOT in your design that should be?** Use the minimum-viable table above. Add them to the canvas and regenerate.
2. **Which Kanban task will you wire up first, and why?**
3. **Would you add any custom node → tool mappings to `args/aadc_node_tool_map.yaml`?** If so, describe them.

To go further: your design has a `drift-detector` but no `baseline-snapshot`, so what does drift detection compare against on its first run? And a teammate's hand edit to the YAML vanished after a regenerate, so what process change prevents that?

**Your task:** Answer the reflection questions to complete this mission.
