<!-- CUI // SP-CTI -->

# Generate a Config for Your Design

## The Generated YAML Structure

When you run the Ops Config Generator against a design that contains a `drift-detector`, `guardrail`, and `token-budget` node, the output file at `args/ops_config_<design_id>.yaml` looks like this:

```yaml
design_id: 42
generated_at: "2026-05-09T14:22:00Z"
il_level: IL4

tools:
  drift_detector:
    tool_path: tools/llm/model_monitor.py
    quality_warning_pct: 10.0
    quality_critical_pct: 25.0
    check_interval_minutes: 60
    baseline_window_days: 7

  token_budget:
    tool_path: tools/agent/token_tracker.py
    monthly_budget_usd: 500.00
    daily_soft_cap_usd: 20.00
    burst_allowance_pct: 15.0
    alert_on_pct: 80.0

  guardrail:
    tool_path: tools/security/ai_telemetry_logger.py
    mode: enforce
    block_on_injection: true
    pii_detection: true
    log_all_inputs: false

alert_routing:
  info: log_only
  warning: kanban_task
  critical: kanban_task_and_page
```

## Reading the Alert Routing Section

The `alert_routing` block is the most operationally significant part of the generated config. It tells each tool where to send its signals:

- `log_only` — writes to the application log, no human-visible action
- `kanban_task` — creates a Kanban card in the backlog with severity, tool name, and a timestamp
- `kanban_task_and_page` — creates the Kanban task AND sends a page alert via the notification channel configured in `args/alert_config.yaml`

Critical alerts always produce a Kanban task. You should never configure `log_only` for `critical` severity — the generator enforces this and will downgrade such a setting to `kanban_task` with a warning in the response.

## The Kanban Tasks Created

Each generated Kanban task follows a consistent structure derived from the `task_title` and `docs_link` fields in `args/aadc_node_tool_map.yaml`:

| Task Title | Config Key | CLI Reference |
|-----------|------------|---------------|
| Configure drift detection thresholds | `tools.drift_detector.quality_warning_pct` | `model_monitor.py --design-id <id>` |
| Set token budget limits | `tools.token_budget.monthly_budget_usd` | `token_tracker.py --design-id <id>` |
| Configure guardrail enforcement mode | `tools.guardrail.mode` | `ai_telemetry_logger.py --design-id <id>` |

Each task description includes the Academy mission link so engineers can trace why the task was created and what design decision produced it.

## Extending the Map: Adding a New Node Type

The YAML map at `args/aadc_node_tool_map.yaml` is the authoritative source. A new node type entry requires these six fields:

```yaml
pii-detector:
  tool_path: tools/security/pii_scanner.py
  cli_command: "python tools/security/pii_scanner.py --design-id {design_id} --mode scan"
  config_key: tools.pii_detector.scan_mode
  description: "Scans all agent inputs and outputs for PII patterns before they leave the system boundary."
  task_title: "Configure PII detection scan mode and redaction policy"
  docs_link: "https://icdev.internal/docs/tools/pii-scanner"
```

No Python changes are required. The generator reads the map at runtime. The new node type becomes available in the canvas node palette after the map is updated and the canvas service restarts.

The `cli_command` field supports `{design_id}` as a substitution token. All other fields are treated as literal strings.

## Configuration Questions

Before running the generator on your design, answer these three questions:

1. What is the correct `monthly_budget_usd` for your design's expected workload? (Check the token tracker's historical data for similar designs.)
2. Should the `guardrail` run in `enforce` mode (blocks non-compliant outputs) or `monitor` mode (logs but passes through)? For IL4 designs, `enforce` is the default.
3. Which alert severity should trigger a page for your operational context? Not every team has 24/7 on-call coverage — `critical` → `kanban_task_and_page` is appropriate only if someone will act on a page.

**Your task:** Answer the configuration questions above.
