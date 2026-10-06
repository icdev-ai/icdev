---
ontology_id: icdev:mission:m-swe-aadc-09-ops-config:step:2
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Generate a Config for Your Design

## The Generated YAML Structure

When you run the generator against a design that contains a `drift-detector`, `guardrail` and `token-budget` node, `data/aadc_ops_config/ops_config_<design_id>.yaml` looks like this. These are the real defaults from `ops_config_generator.py`; the comment header and the `monitoring` and `notes` blocks are trimmed.

```yaml
generated_at: '2026-10-06T14:22:00+00:00'
design_id: aadc-1a2b3c4d
design_name: My Design
tools:
  drift_detector:
    enabled: true
    quality_warning_pct: 15
    quality_critical_pct: 30
    latency_warning_pct: 20
    latency_critical_pct: 50
    token_inflation_warning_pct: 15
    token_inflation_critical_pct: 30
    check_interval_minutes: 60
    auto_retrain_on_critical: false
    tool_path: tools/llm/model_monitor.py
    docs_link: m-sre-ai-02-drift-detection
  guardrail:
    enabled: true
    mode: block
    log_all_events: true
    injection_detection: true
    pii_masking: true
    tool_path: tools/security/ai_telemetry_logger.py
    docs_link: m-secops-ai-01-prompt-injection
  token_budget:
    enabled: true
    monthly_budget_usd: 50.0
    warning_threshold: 0.8
    hard_stop: true
    alert_channel: kanban
    tool_path: tools/agent/token_tracker.py
    docs_link: m-sre-ai-01-llm-observability
alert_routing:
  info: log_only
  warning: kanban_task
  critical: kanban_task_and_page
```

Note the `guardrail` default `mode: block`; an `input-sanitizer` node gets the same block with `mode: sanitize`.

## Reading the Alert Routing Section

The `alert_routing` block tells each tool where its signals should go once a runtime consumes this file:

- `log_only`: application log only, no human-visible action
- `kanban_task`: a Kanban card in the backlog
- `kanban_task_and_page`: the Kanban card plus a page to whoever is on call

The generator always writes these three defaults. It does not validate or rewrite routing you edit by hand, so keeping `critical` off `log_only` is your review responsibility.

## The Kanban Tasks Built

Each task takes its title and links from the node's entry in `args/aadc_node_tool_map.yaml`:

| Task Title | Config Key |
|-----------|------------|
| Wire drift-detector node → model_monitor.py | `drift_detector` |
| Wire token-budget node → token_tracker.py | `token_budget` |
| Wire guardrail node → ai_telemetry_logger.py | `guardrail` |

The task description carries the design name and id, node type, tool path, the entry's `description` and `cli_command`, the config key, and a link to the Academy mission named in `docs_link`.

## Extending the Map: Adding a New Node Type

A new entry under `node_tool_map:` in `args/aadc_node_tool_map.yaml` uses these six fields:

```yaml
node_tool_map:
  pii-detector:
    tool_path: tools/security/<your_pii_tool>.py
    cli_command: "python tools/security/<your_pii_tool>.py --help"
    config_key: pii_detector
    description: "Scans agent inputs and outputs for PII before they leave the system boundary."
    task_title: "Wire pii-detector node → <your_pii_tool>.py"
    docs_link: m-secops-ai-01-prompt-injection
```

No Python change is needed for the mapping: the generator re-reads the YAML on every run. A node type with no specific default block in the generator gets `enabled: true` plus `tool_path` and `docs_link`. The YAML map does not add the type to the canvas palette; palette node types live in `tools/agentic_ai_canvas/constants.py`.

## Configuration Questions

Answer the fields on this step:

1. **AADC design ID**: the `aadc-...` id of the design you will generate for (shown in the canvas URL).
2. **Drift warning threshold**: the default `quality_warning_pct` is 15. Is that right for your workload?
3. **Monthly token budget (USD)**: the default `monthly_budget_usd` is 50.0. What does your expected workload actually cost?
4. **Guardrail mode**: `block` (the generator default for `guardrail`), `sanitize` (the default for `input-sanitizer`), or `log_only` for monitoring only.
5. **Create Kanban wiring tasks?** Remember the insert is not deduplicated.

**Your task:** Answer the configuration questions above.
