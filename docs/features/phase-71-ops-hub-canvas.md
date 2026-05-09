# CUI // SP-CTI
# Phase 71 — Ops Hub Canvas (OHC)

## Overview

The Ops Hub Canvas (OHC) consolidates LLMOps, MLOps, and AIOps into a single unified canvas at `/ops`. Phase 70 shipped complete backend operations tools with no dashboard exposure. OHC adds canvas views on top, delegating to existing Phase 70 tools — no code migration or refactoring.

**Feature flag:** `ICDEV_OPS_HUB_ENABLED=true`  
**Canvas key:** `ohc`  
**Route prefix:** `/ops`  
**DB:** `data/ohc_canvas.db` (SQLite) or PostgreSQL via `OHC_STORAGE_BACKEND=postgresql`

---

## Sub-Pages (8)

| Route | Title | Data Source |
|-------|-------|-------------|
| `/ops` | Overview | `ops_aggregator.py` — cross-domain health score |
| `/ops/llm` | LLMOps | `llmops_engine.py` → `tools/llm/*` |
| `/ops/models` | MLOps | `mlops_engine.py` → OHC DB + MLflow adapter |
| `/ops/slos` | SLOs | `aiops_engine.py` → `tools/sre/slo_manager` |
| `/ops/incidents` | Incidents | `aiops_engine.py` → `tools/sre/incident_commander` |
| `/ops/runbooks` | Runbooks | `aiops_engine.py` → `tools/sre/runbook_executor` |
| `/ops/topology` | Topology | `aiops_engine.py` → `tools/agent/topology` |
| `/ops/self-healing` | Self-Healing | `aiops_engine.py` → `auto_resolution_log` |

---

## Adapter Layer

### Open Source Adapters (6)

| Adapter | Tool | Install | Domain |
|---------|------|---------|--------|
| `mlflow_adapter.py` | MLflow | `pip install mlflow` | MLOps |
| `evidently_adapter.py` | Evidently AI | `pip install evidently` | MLOps/LLMOps |
| `langfuse_adapter.py` | Langfuse | `pip install langfuse` | LLMOps |
| `prometheus_adapter.py` | Prometheus | HTTP or file-based | AIOps |
| `onnx_adapter.py` | ONNX Runtime | `pip install onnxruntime` | MLOps |
| `dvc_adapter.py` | DVC | `pip install dvc` | MLOps |

All adapters are air-gap safe: `try: import <sdk>; except ImportError: available=False`.

### CSP Adapters (5)

| Adapter | CSP | SDK | Domain |
|---------|-----|-----|--------|
| `sagemaker_adapter.py` | AWS SageMaker | `boto3` | MLOps |
| `azureml_adapter.py` | Azure ML | `azure-ai-ml` | MLOps |
| `vertexai_adapter.py` | Vertex AI (GCP) | `google-cloud-aiplatform` | MLOps |
| `bedrock_guardrails_adapter.py` | AWS Bedrock | `boto3` | LLMOps |
| `cloudwatch_adapter.py` | AWS CloudWatch | `boto3` | AIOps |

All CSP adapters default to GovCloud regions (`us-gov-west-1` for AWS) and are FedRAMP High compatible.

---

## DB Tables (ohc_canvas.db)

| Table | Purpose | Type |
|-------|---------|------|
| `ohc_experiments` | Experiment metadata | mutable |
| `ohc_experiment_runs` | Run metrics/params | mutable |
| `ohc_model_registry` | Registered model versions | mutable |
| `ohc_dataset_versions` | Dataset versions with drift flags | mutable |
| `ohc_adapter_status` | Current adapter health (upserted) | mutable |
| `ohc_adapter_health_log` | Historical health log | **append-only** (NIST AU) |
| `ohc_data_drift_events` | Drift detection events | **append-only** (NIST AU) |

Migration: `tools/db/migrations/120_ops_hub/`

---

## MCP Tools (10)

| Tool | Handler | Description |
|------|---------|-------------|
| `ohc_overview` | `get_ops_overview` | Cross-domain health score + alerts |
| `ohc_llmops_summary` | `get_llmops_summary` | Gateway, cost, drift roll-up |
| `ohc_mlops_experiments` | `list_experiments` | Experiment list with run counts |
| `ohc_model_registry` | `list_models` | Registered models by stage |
| `ohc_slos` | `get_slo_dashboard` | SLO values + burn rates |
| `ohc_incidents` | `get_incidents` | Active/resolved incidents |
| `ohc_topology` | `get_topology` | Agent graph + SPOF analysis |
| `ohc_adapter_health` | `probe_all` | Probe all 11 adapters |
| `ohc_run_experiment` | `create_run` | Create new experiment run |
| `ohc_promote_model` | `transition_model_stage` | Stage model to production |

---

## Genesis Reflexes

| Reflex | Tier | Cadence | Description |
|--------|------|---------|-------------|
| `llmops_drift_sweep` | SUPPORT | 4h | Quality drift check across all registered LLMs |
| `mlops_data_drift_sweep` | SUPPORT | 4h | Evidently data drift check for all datasets |

---

## IQE Collections (6)

- `ohc.experiments` — experiment metadata
- `ohc.runs` — experiment run metrics
- `ohc.models` — model registry
- `ohc.datasets` — dataset versions
- `ohc.adapters` — adapter health status
- `ohc.drift_events` — data drift events

IQE seed queries: `context/iqe/queries/ohc/`

---

## CLI Commands

```bash
# Adapter health check
python tools/ops_hub/cli.py --health --json

# Gate (exits 1 if status critical)
python tools/ops_hub/cli.py --gate

# Per-adapter status table
python tools/ops_hub/cli.py --adapters

# Apply migration 120
python tools/db/migrations/120_ops_hub/up.py
```

---

## Configuration

`args/ops_hub_config.yaml` — adapter settings, health probe intervals, thresholds, adapter credentials.

Key environment variables:
- `ICDEV_OPS_HUB_ENABLED=true` — enable the canvas
- `OHC_STORAGE_BACKEND=postgresql` — use PostgreSQL (default: SQLite)
- `MLFLOW_TRACKING_URI` — MLflow server URL
- `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` — Langfuse credentials
- `PROMETHEUS_URL` — Prometheus server URL
- `AWS_DEFAULT_REGION`, `AWS_PROFILE` — AWS credentials for SageMaker/Bedrock/CloudWatch
- `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `AZURE_ML_WORKSPACE` — Azure ML credentials
- `GOOGLE_CLOUD_PROJECT`, `VERTEX_AI_LOCATION` — Vertex AI credentials

---

## Architecture Notes

- Canvas views are **view + orchestration layers only** — Phase 70 tools are not modified
- All adapters extend `OpsAdapter` ABC (`adapter_base.py`)
- Health score: 0-100 computed by `ops_aggregator.py`; critical < 50, degraded 50-80, healthy ≥ 80
- Adapter registry uses lazy loading + caching; probes all 11 adapters every 5 minutes (configurable)
- Air-gap safe: all optional SDKs gracefully degrade to `available=False` stub responses
