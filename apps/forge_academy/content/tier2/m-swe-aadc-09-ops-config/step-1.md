<!-- CUI // SP-CTI -->

# Design → Runtime: The Ops Config Generator

## The Gap Between Design and Execution

An AADC design in the canvas is a declaration of intent. You place nodes — `drift-detector`, `guardrail`, `token-budget`, `audit-logger`, `circuit-breaker`, `retrain-trigger`, `prompt-registry` — draw edges, run the assessment, and score it. That work lives in the `aadc_designs` table. It does not run. Nothing in `tools/` has been touched. The monitoring thresholds you implicitly assumed exist nowhere on disk.

That gap is where AI systems fail in production. Teams ship a design without ever wiring it to actual runtime tooling. Months later, token costs explode, a guardrail never fires, and audit logs contain nothing useful — because the design and the runtime were never connected.

## What the Ops Config Generator Does

The Ops Config Generator is the bridge. It reads the `graph_json` from your saved design, identifies every node type present, and does three things:

1. Maps each node to its corresponding tool in `tools/` via `args/aadc_node_tool_map.yaml`
2. Writes `args/ops_config_<design_id>.yaml` with default thresholds and configuration keys populated
3. Creates one Kanban backlog task per mapped tool — each task contains the CLI reference, config key, and a link back to the Academy mission

The mapping file is the critical design decision here. Rather than hardcoding node-to-tool relationships in Python, the generator reads them from `args/aadc_node_tool_map.yaml`. To reroute a node to a different tool — or to add support for a new node type entirely — you edit YAML, not code.

## Node → Tool Mapping

| AADC Node | Runtime Tool |
|-----------|-------------|
| `drift-detector` | `tools/llm/model_monitor.py` |
| `guardrail` | `tools/security/ai_telemetry_logger.py` |
| `circuit-breaker` | `tools/agentic_ai_canvas/safety_layer.py` |
| `token-budget` | `tools/agent/token_tracker.py` |
| `audit-logger` | `tools/compliance/classification_manager.py` |
| `retrain-trigger` | `tools/finetune/retrain_trigger.py` |
| `prompt-registry` | `tools/llm/prompt_registry.py` |

Any node type not present in `args/aadc_node_tool_map.yaml` is reported in the `unmatched_nodes` field of the response — it does not silently drop.

## What It Does NOT Do

The Ops Config Generator is a config scaffolder, not a deployment engine. It writes YAML and creates tasks. It does not start processes, deploy containers, or invoke any tool at runtime. That is the responsibility of Track C — FORGE OPS RUNTIME, a future initiative. The generator's job is to make sure that when Track C arrives, your design already has a complete, accurate configuration waiting for it.

## CLI Usage

```bash
python tools/agentic_ai_canvas/ops_config_generator.py <design_id> --create-tasks --json
```

Omit `--create-tasks` to generate the YAML only without touching Kanban. Use `--dry-run` to preview the mapping without writing any files. The `--json` flag emits machine-readable output with `config_path`, `tasks_created`, and `unmatched_nodes` fields.

```bash
# Preview without writing
python tools/agentic_ai_canvas/ops_config_generator.py <design_id> --dry-run --json

# Full generation with Kanban task creation
python tools/agentic_ai_canvas/ops_config_generator.py <design_id> --create-tasks --json
```

The generator is idempotent for the YAML: re-running it overwrites `args/ops_config_<design_id>.yaml` with refreshed defaults. Kanban task creation deduplicates by `design_id` + `node_type` — it will not create duplicate tasks for the same node on a second run.

**Your task:** In the next step, generate a config for your own AADC design.
