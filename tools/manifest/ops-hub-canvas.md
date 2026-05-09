# CUI // SP-CTI
# Tools Manifest Shard — Ops Hub Canvas (OHC)

## Overview
Unified operations canvas at `/ops` consolidating LLMOps, MLOps, and AIOps.
Canvas views delegate to Phase 70 backend tools; no code migration.

## Core Modules

| File | Purpose |
|------|---------|
| `tools/ops_hub/constants.py` | OHC_FEATURE_FLAG, CANVAS_DOMAINS, OHC_ROUTES, OSS_TOOLS, CSP_PROVIDERS, MODEL_STAGES |
| `tools/ops_hub/adapter_base.py` | OpsAdapter ABC — available/health_check/list_resources/get_metrics/push_event |
| `tools/ops_hub/adapter_registry.py` | Discovers + loads all 11 adapters; probes health; persists to ohc_adapter_status |
| `tools/ops_hub/llmops_engine.py` | Delegates to tools/llm/*; get_llmops_summary/gateway_stats/cost_report/drift_events |
| `tools/ops_hub/mlops_engine.py` | OHC DB + MLflow adapter; experiment CRUD, model registry, data drift summary |
| `tools/ops_hub/aiops_engine.py` | Delegates to tools/sre/* + tools/agent/topology; SLOs/incidents/runbooks/topology |
| `tools/ops_hub/ops_aggregator.py` | Cross-domain health score (0-100); overview JSON for /ops |
| `tools/ops_hub/__init__.py` | Package init; exports feature flag and domain constants |
| `tools/ops_hub/cli.py` | CLI: --health --json --gate --adapters |
| `tools/ops_hub/blueprint.py` | Flask blueprint: 8 page routes + 14 JSON API routes + IQE |
| `tools/ops_hub/db/init_db.py` | OHC DB init: 7 tables; get_connection() SQLite/PostgreSQL |

## Adapters

### Open Source (tools/ops_hub/adapters/)
| Adapter | Tool | Domain |
|---------|------|--------|
| `mlflow_adapter.py` | MLflow | MLOps |
| `evidently_adapter.py` | Evidently AI | MLOps/LLMOps |
| `langfuse_adapter.py` | Langfuse | LLMOps |
| `prometheus_adapter.py` | Prometheus | AIOps |
| `onnx_adapter.py` | ONNX Runtime | MLOps |
| `dvc_adapter.py` | DVC | MLOps |

### CSP (tools/ops_hub/adapters/)
| Adapter | CSP | Domain |
|---------|-----|--------|
| `sagemaker_adapter.py` | AWS SageMaker | MLOps |
| `azureml_adapter.py` | Azure ML | MLOps |
| `vertexai_adapter.py` | Vertex AI (GCP) | MLOps |
| `bedrock_guardrails_adapter.py` | AWS Bedrock | LLMOps |
| `cloudwatch_adapter.py` | AWS CloudWatch | AIOps |

## Templates (tools/dashboard/templates/ops_hub/)
- `index.html` — /ops overview: health score, domain cards, adapter grid, alerts
- `llm.html` — /ops/llm: gateway stats, drift events, prompt registry, cost
- `models.html` — /ops/models: experiment runs, model registry, data drift
- `slos.html` — /ops/slos: SLO table, burn rate, error budgets
- `incidents.html` — /ops/incidents: incident list, MTTR
- `runbooks.html` — /ops/runbooks: runbook library, execution history
- `topology.html` — /ops/topology: agent graph (canvas), SPOF alerts, node list
- `self_healing.html` — /ops/self-healing: resolution log, confidence heatmap

## CLI Commands
```bash
python tools/ops_hub/cli.py --health --json      # adapter health JSON
python tools/ops_hub/cli.py --gate               # exit 1 if status critical
python tools/ops_hub/cli.py --adapters           # per-adapter status table
```

## DB Tables (ohc_canvas.db)
- `ohc_experiments` — experiment metadata
- `ohc_experiment_runs` — run metrics/params
- `ohc_model_registry` — registered model versions
- `ohc_dataset_versions` — dataset versions with drift flags
- `ohc_adapter_status` — current adapter health (upserted)
- `ohc_adapter_health_log` — historical health (append-only, NIST AU)
- `ohc_data_drift_events` — drift detection events (append-only, NIST AU)

## MCP Tools
`ohc_overview`, `ohc_llmops_summary`, `ohc_mlops_experiments`, `ohc_model_registry`,
`ohc_slos`, `ohc_incidents`, `ohc_topology`, `ohc_adapter_health`,
`ohc_run_experiment`, `ohc_promote_model`

## Genesis Reflexes
- `llmops_drift_sweep` — SUPPORT tier, 4h cadence
- `mlops_data_drift_sweep` — SUPPORT tier, 4h cadence

## Feature Flag
`ICDEV_OPS_HUB_ENABLED=true`

## Config
`args/ops_hub_config.yaml` — adapter settings, health probe intervals, thresholds
