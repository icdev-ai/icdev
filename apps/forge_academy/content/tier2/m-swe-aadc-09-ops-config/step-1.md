---
ontology_id: icdev:mission:m-swe-aadc-09-ops-config:step:1
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Design → Runtime: The Ops Config Generator

## The Gap Between Design and Execution

An AADC design in the canvas is a declaration of intent. You place nodes — `drift-detector`, `guardrail`, `token-budget`, `audit-logger`, `circuit-breaker`, `prompt-registry` — draw edges, run the assessment, and score it. That work lives in the `aadc_designs` table. It does not run. Nothing in `tools/` has been touched. The monitoring thresholds you implicitly assumed exist nowhere on disk.

That gap is where AI systems fail in production. Teams ship a design without ever wiring it to actual runtime tooling. Months later, token costs explode, a guardrail never fires, and audit logs contain nothing useful — because the design and the runtime were never connected.

## What the Ops Config Generator Does

The Ops Config Generator (`tools/agentic_ai_canvas/ops_config_generator.py`) is the bridge. It reads the `graph_json` of your saved design, collects every distinct node type present, and:

1. Maps each node type to its runtime tool through `args/aadc_node_tool_map.yaml` (the `node_tool_map:` block)
2. Writes `data/aadc_ops_config/ops_config_<design_id>.yaml` with default thresholds for each mapped tool (set `AADC_OPS_CONFIG_DIR` to write elsewhere). Generated configs are runtime output, so they go under `data/`, never into `args/`.
3. Builds one Kanban task per mapped node type, with the tool path, CLI reference, config key and Academy mission link. The tasks are only *inserted* into the board when you ask for it: `--create-tasks` on the CLI, or `create_tasks` (default true) on the API.

The mapping file is the critical design decision. Rather than hardcoding node-to-tool relationships in Python, the generator reads them from YAML on every run. To reroute a node to a different tool, or to support a new node type, you edit YAML, not code.

## Node → Tool Mapping

`args/aadc_node_tool_map.yaml` maps 14 node types today. A selection:

| AADC Node | Runtime Tool |
|-----------|-------------|
| `drift-detector` / `baseline-snapshot` / `trusted-monitor` | `tools/llm/model_monitor.py` |
| `guardrail` / `input-sanitizer` | `tools/security/ai_telemetry_logger.py` |
| `circuit-breaker` | `tools/agentic_ai_canvas/safety_layer.py` |
| `confidence-threshold` | `tools/agentic_ai_canvas/confidence_gate.py` |
| `token-budget` | `tools/agent/token_tracker.py` |
| `audit-logger` / `compliance-reporter` | `tools/compliance/classification_manager.py` |
| `prompt-registry` | `tools/llm/prompt_registry.py` |
| `retrain-trigger` | `tools/finetune/retrain_trigger.py` (mapped, though not in the default canvas palette) |

Any node type in your graph that has no entry in the map is reported in `unmatched_nodes`; it is not silently dropped. Agent nodes such as `orchestrator` always land there, because they are the thing being configured, not a runtime tool.

## What It Does NOT Do

The Ops Config Generator is a config scaffolder, not a deployment engine. It writes YAML and builds tasks. It does not start processes, deploy containers, or invoke any tool at runtime. That is the job of Track C (FORGE OPS RUNTIME), a future initiative. The generator makes sure that when Track C arrives, your design already has a complete, accurate configuration waiting for it.

## Two ways to run it

**From the canvas.** Open your design at `/agentic-ai/canvas/<design_id>` and click **Generate Ops Config**. That opens `/agentic-ai/canvas/<design_id>/ops-config`, which calls `POST /agentic-ai/api/designs/<design_id>/ops-config` and shows the matched nodes, the generated tasks and a config preview.

**From the CLI:**

```bash
# Write the YAML only (no board writes)
python tools/agentic_ai_canvas/ops_config_generator.py <design_id> --json

# Write the YAML and insert the Kanban tasks
python tools/agentic_ai_canvas/ops_config_generator.py <design_id> --create-tasks --json
```

`<design_id>` is the canvas id, e.g. `aadc-1a2b3c4d`. With `--json` the CLI prints `config_path`, `matched_nodes`, `unmatched_nodes`, `kanban_tasks_count` and `created_task_ids` (empty unless `--create-tasks`). There is no dry-run flag: omitting `--create-tasks` is the safe preview, and it still writes the YAML file.

The YAML is overwritten on every run with refreshed defaults. The Kanban insert is **not** deduplicated: every `--create-tasks` run inserts a fresh set of tasks, so run it once per design.

**Your task:** In the next step, generate a config for your own AADC design.
