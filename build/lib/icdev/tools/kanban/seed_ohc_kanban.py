#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed Kanban tasks for Ops Hub Canvas (ohc-*).

8 epics, 48 tasks total. Phase gates: CodeLens + Coherence + E2E Playwright after each epic.
Execution order: foundation → engine → blueprint → templates → oss + csp (parallel) → wiring → vv

Run: python tools/kanban/seed_ohc_kanban.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.db.storage import get_connection  # noqa: E402

_NOW = datetime.now(timezone.utc).isoformat()


def _conn():
    return get_connection()


TASKS = [
    # ═══════════════════════════════════════════════════════════════════════
    # EPIC: foundation (5 tasks)
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "ohc-foundation-01",
        "title": "OHC: Create tools/ops_hub/constants.py",
        "description": (
            "Create tools/ops_hub/constants.py with:\n"
            "  OHC_FEATURE_FLAG, CANVAS_DOMAINS, OHC_ROUTES, OPS_METRIC_TYPES,\n"
            "  OSS_TOOLS (mlflow/evidently/langfuse/prometheus/onnx/dvc),\n"
            "  CSP_PROVIDERS (sagemaker/azureml/vertexai/bedrock_guardrails/cloudwatch),\n"
            "  STATUS_COLORS, SEVERITY_LEVELS, EXPERIMENT_STATES, MODEL_STAGES,\n"
            "  IQE_COLLECTIONS, OHC_NIST_CONTROLS\n\n"
            "File: tools/ops_hub/constants.py\n"
            "Classification: CUI // SP-CTI"
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": None,
    },
    {
        "id": "ohc-foundation-02",
        "title": "OHC: Create tools/ops_hub/db/init_db.py",
        "description": (
            "Create tools/ops_hub/db/init_db.py with:\n"
            "  get_connection() — reads OHC_STORAGE_BACKEND, defaults to data/ohc_canvas.db\n"
            "  SCHEMA string — 7 tables:\n"
            "    ohc_experiments, ohc_experiment_runs, ohc_model_registry,\n"
            "    ohc_dataset_versions, ohc_adapter_status, ohc_adapter_health_log,\n"
            "    ohc_data_drift_events\n"
            "  init_db(conn=None) — executes schema idempotently\n\n"
            "Pattern: follow tools/aiml_canvas/db/init_db.py\n"
            "File: tools/ops_hub/db/init_db.py\n"
            "Also create: tools/ops_hub/db/__init__.py (empty)"
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-foundation-01",
    },
    {
        "id": "ohc-foundation-03",
        "title": "OHC: Create DB migration 120_ops_hub",
        "description": (
            "Create tools/db/migrations/120_ops_hub/meta.json and up.py.\n\n"
            "meta.json: id=120, name=ops_hub, database=ohc_canvas, reversible=true\n"
            "up.py: delegates to tools.ops_hub.db.init_db.init_db()\n"
            "Entry point: up(conn) and run() standalone\n\n"
            "File: tools/db/migrations/120_ops_hub/meta.json\n"
            "File: tools/db/migrations/120_ops_hub/up.py"
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-foundation-02",
    },
    {
        "id": "ohc-foundation-04",
        "title": "OHC: Create tools/ops_hub/adapter_base.py",
        "description": (
            "Create tools/ops_hub/adapter_base.py with:\n"
            "  OpsAdapter ABC — abstract methods: available(), health_check(),\n"
            "    list_resources(), get_metrics(), push_event()\n"
            "  Dataclasses: AdapterHealth, AdapterResource, AdapterMetrics\n"
            "  Helpers: _stub_health(error_msg), _timed_probe(fn)\n\n"
            "Pattern: mirrors tools/llm/provider.py ABC structure\n"
            "File: tools/ops_hub/adapter_base.py"
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-foundation-01",
    },
    {
        "id": "ohc-foundation-05",
        "title": "OHC: Create tools/ops_hub/adapter_registry.py",
        "description": (
            "Create tools/ops_hub/adapter_registry.py:\n"
            "  _ADAPTER_MAP: dict[str, str] — maps 11 adapter names to module dotpaths\n"
            "  get_adapter(name) — loads and caches adapter instance\n"
            "  probe_all(persist=True) — health-probes all adapters, writes to\n"
            "    ohc_adapter_status (upsert) and ohc_adapter_health_log (append)\n"
            "  Returns list of AdapterHealth objects\n\n"
            "File: tools/ops_hub/adapter_registry.py"
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-foundation-04",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EPIC: engine (6 tasks)
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "ohc-engine-01",
        "title": "OHC: Create tools/ops_hub/llmops_engine.py",
        "description": (
            "Create tools/ops_hub/llmops_engine.py:\n"
            "  Delegates to tools.llm.{gateway, cost_intelligence, model_monitor,\n"
            "    prompt_registry, eval_runner}\n"
            "  Functions: get_gateway_stats(), get_cost_report(), get_cost_anomalies(),\n"
            "    get_model_health(), get_drift_events(), get_prompt_registry(),\n"
            "    get_eval_results(), get_llmops_summary()\n"
            "  get_llmops_summary() returns: {status, has_drift, has_cost_anomaly,\n"
            "    gateway, cost, model_health}\n\n"
            "File: tools/ops_hub/llmops_engine.py"
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-foundation-01",
    },
    {
        "id": "ohc-engine-02",
        "title": "OHC: Create tools/ops_hub/mlops_engine.py",
        "description": (
            "Create tools/ops_hub/mlops_engine.py:\n"
            "  Uses OHC DB tables (ohc_experiments, ohc_experiment_runs, ohc_model_registry)\n"
            "  Functions: create_experiment(), list_experiments(), create_run(),\n"
            "    finish_run() (sets end_time + duration_ms), list_runs(),\n"
            "    register_model(), transition_model_stage() (validates MODEL_STAGES),\n"
            "    list_models(), get_data_drift_summary(), get_mlops_summary()\n"
            "  Falls back to MLflow adapter when available\n\n"
            "File: tools/ops_hub/mlops_engine.py"
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-foundation-02",
    },
    {
        "id": "ohc-engine-03",
        "title": "OHC: Create tools/ops_hub/aiops_engine.py",
        "description": (
            "Create tools/ops_hub/aiops_engine.py:\n"
            "  Delegates to tools.sre.{slo_manager, incident_commander, runbook_executor}\n"
            "    and tools.agent.topology\n"
            "  Checks auto_resolution_log in icdev.db for self-healing log\n"
            "  Functions: get_slo_dashboard(), get_incidents(), get_runbooks(),\n"
            "    get_topology(), get_self_healing_log(), get_aiops_summary()\n"
            "  get_aiops_summary() returns: {status, degraded_slos, open_incidents, spof_count}\n\n"
            "File: tools/ops_hub/aiops_engine.py"
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-foundation-01",
    },
    {
        "id": "ohc-engine-04",
        "title": "OHC: Create tools/ops_hub/ops_aggregator.py",
        "description": (
            "Create tools/ops_hub/ops_aggregator.py:\n"
            "  get_ops_overview() — calls llmops_engine + mlops_engine + aiops_engine + probe_all()\n"
            "  _compute_health_score() — 0-100 score; penalise for: domain critical/degraded,\n"
            "    drift, cost anomaly, open incidents, SPOFs, low adapter availability\n"
            "  _extract_top_alerts() — returns top 5 alerts across all domains\n"
            "  Returns: {health_score, overall_status, domains, adapters, available_adapters,\n"
            "    top_alerts}\n\n"
            "File: tools/ops_hub/ops_aggregator.py"
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-engine-03",
    },
    {
        "id": "ohc-engine-05",
        "title": "OHC: Create __init__.py + cli.py",
        "description": (
            "1. Create tools/ops_hub/__init__.py — exports OHC_FEATURE_FLAG,\n"
            "   CANVAS_DOMAINS, OHC_ROUTES\n\n"
            "2. Create tools/ops_hub/cli.py — argparse CLI:\n"
            "   --health: print adapter health table\n"
            "   --json: output as JSON\n"
            "   --gate: exit 1 if overall status is critical\n"
            "   --adapters: per-adapter status table\n\n"
            "Files: tools/ops_hub/__init__.py, tools/ops_hub/cli.py"
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-engine-04",
    },
    {
        "id": "ohc-engine-06",
        "title": "OHC: Add CLI commands to docs/reference/commands.md",
        "description": (
            "Add OHC CLI commands section to docs/reference/commands.md:\n"
            "  python tools/ops_hub/cli.py --health --json\n"
            "  python tools/ops_hub/cli.py --gate\n"
            "  python tools/ops_hub/cli.py --adapters\n"
            "  python -m tools.db.migrations.120_ops_hub.up (run migration)\n\n"
            "File: docs/reference/commands.md"
        ),
        "task_type": "chore",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-engine-05",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EPIC: blueprint (3 tasks)
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "ohc-blueprint-01",
        "title": "OHC: Create blueprint.py — /ops and /ops/llm + /ops/models routes",
        "description": (
            "Create tools/ops_hub/blueprint.py:\n"
            "  create_ops_hub_blueprint() → Flask Blueprint\n"
            "  Routes: GET /ops (overview), GET /ops/llm (LLMOps), GET /ops/models (MLOps)\n"
            "  JSON API: GET /api/ops/overview, GET /api/ops/llm, GET /api/ops/models\n"
            "  POST /api/ops/models/run, POST /api/ops/models/register,\n"
            "  POST /api/ops/models/transition\n"
            "  Each page route: call engine, render ops_hub/<page>.html\n\n"
            "File: tools/ops_hub/blueprint.py"
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-engine-04",
    },
    {
        "id": "ohc-blueprint-02",
        "title": "OHC: Add /ops/slos + /ops/incidents + /ops/runbooks routes",
        "description": (
            "Add to tools/ops_hub/blueprint.py:\n"
            "  Routes: GET /ops/slos, GET /ops/incidents, GET /ops/runbooks\n"
            "  JSON API: GET /api/ops/slos, POST /api/ops/slos,\n"
            "    GET /api/ops/incidents, POST /api/ops/incidents,\n"
            "    GET /api/ops/runbooks\n\n"
            "File: tools/ops_hub/blueprint.py (append to existing)"
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-blueprint-01",
    },
    {
        "id": "ohc-blueprint-03",
        "title": "OHC: Add /ops/topology + /ops/self-healing + IQE route",
        "description": (
            "Add to tools/ops_hub/blueprint.py:\n"
            "  Routes: GET /ops/topology, GET /ops/self-healing\n"
            "  JSON API: GET /api/ops/topology, GET /api/ops/self-healing,\n"
            "    GET /api/ops/adapters\n"
            "  IQE: POST /api/iqe-query (imports and dispatches to ohc IQE adapter)\n\n"
            "File: tools/ops_hub/blueprint.py (append to existing)"
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-blueprint-02",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EPIC: templates (4 tasks)
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "ohc-templates-01",
        "title": "OHC: Create ops_hub/index.html (overview)",
        "description": (
            "Create tools/dashboard/templates/ops_hub/index.html:\n"
            "  Cross-domain health score circle (green/amber/red)\n"
            "  3 domain cards (LLMOps/MLOps/AIOps) with status dot + metrics\n"
            "  Top alerts section with severity colors\n"
            "  Adapter grid (available=green border, unavailable=gray)\n"
            "  Nav links to all 8 sub-pages\n"
            "  IQE widget: {% include 'includes/iqe_query_widget.html' %}\n\n"
            "Also mirror to icdev/tools/dashboard/templates/ops_hub/index.html"
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-blueprint-01",
    },
    {
        "id": "ohc-templates-02",
        "title": "OHC: Create ops_hub/llm.html + ops_hub/models.html",
        "description": (
            "1. tools/dashboard/templates/ops_hub/llm.html:\n"
            "   Gateway stats (invocations/blocks/models/drift status)\n"
            "   Drift events table, Prompt registry, Cost summary\n\n"
            "2. tools/dashboard/templates/ops_hub/models.html:\n"
            "   Experiment runs table with status/metrics\n"
            "   Model registry with stage badges\n"
            "   Data drift section with gauge bars\n\n"
            "Both include IQE widget. Mirror to icdev/."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-blueprint-01",
    },
    {
        "id": "ohc-templates-03",
        "title": "OHC: Create slos.html + incidents.html + runbooks.html",
        "description": (
            "1. ops_hub/slos.html: SLO table (target/current/burn rate/status),\n"
            "   error budget bars, stat cards (total/met/at-risk/breached)\n\n"
            "2. ops_hub/incidents.html: severity-bordered incident cards (open + resolved),\n"
            "   MTTR stat card, investigating count\n\n"
            "3. ops_hub/runbooks.html: runbook library cards, execution history rows\n\n"
            "All include IQE widget. Mirror to icdev/."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-blueprint-02",
    },
    {
        "id": "ohc-templates-04",
        "title": "OHC: Create topology.html + self_healing.html",
        "description": (
            "1. ops_hub/topology.html: canvas 2D graph (nodes+edges drawn by JS),\n"
            "   SPOF alert section, node list table\n\n"
            "2. ops_hub/self_healing.html: confidence heatmap (12-bucket grid),\n"
            "   resolution log with confidence bars, HITL/auto/failed rows\n\n"
            "Both include IQE widget. Mirror to icdev/."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-blueprint-03",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EPIC: oss (8 tasks)
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "ohc-oss-01",
        "title": "OHC: Create adapters/mlflow_adapter.py",
        "description": (
            "Create tools/ops_hub/adapters/mlflow_adapter.py:\n"
            "  Reads MLFLOW_TRACKING_URI, defaults to sqlite:///data/mlflow.db\n"
            "  health_check(): mlflow.get_experiment_by_name or list_experiments\n"
            "  list_resources(resource_type): experiments / models / runs\n"
            "  push_event(): create_experiment / register_model / transition_stage\n"
            "  Graceful: try import mlflow; except ImportError: return available=False\n\n"
            "File: tools/ops_hub/adapters/mlflow_adapter.py"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-foundation-04",
    },
    {
        "id": "ohc-oss-02",
        "title": "OHC: Create adapters/evidently_adapter.py",
        "description": (
            "Create tools/ops_hub/adapters/evidently_adapter.py:\n"
            "  run_drift_report(reference_data, current_data) — DataDriftPreset\n"
            "  run_model_performance_report() — RegressionPreset\n"
            "  _persist_drift_event() — writes to ohc_data_drift_events\n"
            "  Graceful: try import evidently; except ImportError: available=False\n\n"
            "File: tools/ops_hub/adapters/evidently_adapter.py"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-foundation-04",
    },
    {
        "id": "ohc-oss-03",
        "title": "OHC: Create adapters/langfuse_adapter.py",
        "description": (
            "Create tools/ops_hub/adapters/langfuse_adapter.py:\n"
            "  Reads LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY\n"
            "  available() requires SDK + credentials\n"
            "  list_resources(): traces / scores\n"
            "  get_metrics(): cost_per_model + latency_p95 from trace data\n"
            "  Graceful: try import langfuse; except ImportError: available=False\n\n"
            "File: tools/ops_hub/adapters/langfuse_adapter.py"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-foundation-04",
    },
    {
        "id": "ohc-oss-04",
        "title": "OHC: Create adapters/prometheus_adapter.py",
        "description": (
            "Create tools/ops_hub/adapters/prometheus_adapter.py:\n"
            "  _ping() checks /-/healthy\n"
            "  _http_query() / _http_query_range() use stdlib urllib\n"
            "  _file_query() parses .prom files for air-gap\n"
            "  available() = True if HTTP or file-based mode works\n"
            "  push_event(): post to pushgateway port 9091\n\n"
            "File: tools/ops_hub/adapters/prometheus_adapter.py"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-foundation-04",
    },
    {
        "id": "ohc-oss-05",
        "title": "OHC: Create adapters/onnx_adapter.py",
        "description": (
            "Create tools/ops_hub/adapters/onnx_adapter.py:\n"
            "  Reads OHC_ONNX_MODEL_DIR\n"
            "  list_resources(): scans dir for *.onnx files\n"
            "  get_metrics(): runs 5 inference passes, returns avg latency\n"
            "  _inspect_model(): returns inputs/outputs/providers\n"
            "  Graceful: try import onnxruntime; except ImportError: available=False\n\n"
            "File: tools/ops_hub/adapters/onnx_adapter.py"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-foundation-04",
    },
    {
        "id": "ohc-oss-06",
        "title": "OHC: Create adapters/dvc_adapter.py",
        "description": (
            "Create tools/ops_hub/adapters/dvc_adapter.py:\n"
            "  _dvc_bin(): shutil.which('dvc') check\n"
            "  _run(): wraps subprocess with timeout\n"
            "  list_resources(): stages / data (dvc data status)\n"
            "  get_metrics(): dvc metrics show --json\n"
            "  get_diff(): dvc diff --json\n"
            "  available() = False gracefully if dvc not installed\n\n"
            "File: tools/ops_hub/adapters/dvc_adapter.py"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-foundation-04",
    },
    {
        "id": "ohc-oss-07",
        "title": "OHC: Wire OSS adapters into adapter_registry + mlops/llmops engines",
        "description": (
            "Update tools/ops_hub/adapter_registry.py:\n"
            "  Register all 6 OSS adapters with health probes\n\n"
            "Update tools/ops_hub/mlops_engine.py:\n"
            "  Wire mlflow + evidently + onnx adapters\n\n"
            "Update tools/ops_hub/llmops_engine.py:\n"
            "  Wire langfuse adapter for trace data\n\n"
            "File: tools/ops_hub/adapter_registry.py + engine files"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-oss-06",
    },
    {
        "id": "ohc-oss-08",
        "title": "OHC: Create args/ops_hub_config.yaml",
        "description": (
            "Create args/ops_hub_config.yaml:\n"
            "  canvas: key, name, route, feature_flag\n"
            "  health: probe_interval_seconds, persist_health_log, retention_days\n"
            "  oss_adapters: mlflow, evidently, langfuse, prometheus, onnx, dvc settings\n"
            "  csp_adapters: sagemaker, azureml, vertexai, bedrock_guardrails, cloudwatch settings\n"
            "  llmops: drift_threshold, cost_anomaly_multiplier\n"
            "  mlops: model_stages, experiment_states\n"
            "  aiops: slo_burn_rate_alert, spof_min_dependents\n\n"
            "File: args/ops_hub_config.yaml"
        ),
        "task_type": "chore",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-oss-07",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EPIC: csp (6 tasks)
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "ohc-csp-01",
        "title": "OHC: Create adapters/sagemaker_adapter.py",
        "description": (
            "Create tools/ops_hub/adapters/sagemaker_adapter.py:\n"
            "  boto3 session with profile + region (us-gov-west-1 default)\n"
            "  list_resources(): experiments / models (model_package_groups) / pipelines\n"
            "  push_event(): create_trial_component\n"
            "  Graceful: try import boto3; except ImportError: available=False\n\n"
            "File: tools/ops_hub/adapters/sagemaker_adapter.py"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-foundation-04",
    },
    {
        "id": "ohc-csp-02",
        "title": "OHC: Create adapters/azureml_adapter.py",
        "description": (
            "Create tools/ops_hub/adapters/azureml_adapter.py:\n"
            "  azure-ai-ml SDK + azure.identity.DefaultAzureCredential\n"
            "  Reads AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, AZURE_ML_WORKSPACE\n"
            "  list_resources(): models / jobs / pipelines\n"
            "  Graceful: try import azure.ai.ml; except ImportError: available=False\n\n"
            "File: tools/ops_hub/adapters/azureml_adapter.py"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-foundation-04",
    },
    {
        "id": "ohc-csp-03",
        "title": "OHC: Create adapters/vertexai_adapter.py",
        "description": (
            "Create tools/ops_hub/adapters/vertexai_adapter.py:\n"
            "  google-cloud-aiplatform SDK\n"
            "  Reads GOOGLE_CLOUD_PROJECT, VERTEX_AI_LOCATION\n"
            "  _init(): aiplatform.init(project, location)\n"
            "  list_resources(): models / pipelines / datasets\n"
            "  get_metrics(): model.list_model_evaluations()\n"
            "  Graceful: try import google.cloud.aiplatform; except: available=False\n\n"
            "File: tools/ops_hub/adapters/vertexai_adapter.py"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-foundation-04",
    },
    {
        "id": "ohc-csp-04",
        "title": "OHC: Create adapters/bedrock_guardrails_adapter.py",
        "description": (
            "Create tools/ops_hub/adapters/bedrock_guardrails_adapter.py:\n"
            "  boto3 bedrock + bedrock-runtime clients\n"
            "  health_check(): list_guardrails(maxResults=1)\n"
            "  list_resources(): guardrail list\n"
            "  test_guardrail(): runtime.apply_guardrail()\n"
            "  push_event(): test_guardrail event\n"
            "  GovCloud-ready (us-gov-west-1 default)\n\n"
            "File: tools/ops_hub/adapters/bedrock_guardrails_adapter.py"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-foundation-04",
    },
    {
        "id": "ohc-csp-05",
        "title": "OHC: Create adapters/cloudwatch_adapter.py",
        "description": (
            "Create tools/ops_hub/adapters/cloudwatch_adapter.py:\n"
            "  boto3 CloudWatch client\n"
            "  _AI_NAMESPACES list (SageMaker/Bedrock/Lambda/etc.)\n"
            "  get_metrics(): get_metric_statistics()\n"
            "  get_bedrock_invocations(): InputTokenCount/OutputTokenCount/Latency\n"
            "  push_event(): put_metric_data to OHC/Custom namespace\n"
            "  Air-gap safe: fails gracefully without boto3\n\n"
            "File: tools/ops_hub/adapters/cloudwatch_adapter.py"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-foundation-04",
    },
    {
        "id": "ohc-csp-06",
        "title": "OHC: Wire CSP adapters into adapter_registry",
        "description": (
            "Update tools/ops_hub/adapter_registry.py:\n"
            "  Register all 5 CSP adapters (sagemaker/azureml/vertexai/bedrock_guardrails/cloudwatch)\n\n"
            "Update args/ops_hub_config.yaml:\n"
            "  Add CSP region/account config sections\n\n"
            "File: tools/ops_hub/adapter_registry.py + args/ops_hub_config.yaml"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-csp-05",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EPIC: wiring (9 tasks)
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "ohc-wiring-01",
        "title": "OHC: Register blueprint in tools/dashboard/app.py",
        "description": (
            "Add to _CANVAS_DEFS list in tools/dashboard/app.py:\n"
            '  ("ohc", "ICDEV_OPS_HUB_ENABLED", "tools.ops_hub.blueprint", "create_ops_hub_blueprint")\n\n'
            "Add to template context (inject_base_context):\n"
            '  "ohc_enabled": _CANVAS_FLAGS.get("ohc", False)\n\n'
            "File: tools/dashboard/app.py"
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-blueprint-03",
    },
    {
        "id": "ohc-wiring-02",
        "title": "OHC: Create tools/iqe/adapters/ohc.py",
        "description": (
            "Create tools/iqe/adapters/ohc.py following IQE adapter pattern:\n"
            "  6 collection adapters: experiments_adapter, runs_adapter, models_adapter,\n"
            "    datasets_adapter, adapters_adapter, drift_events_adapter\n"
            "  register_collection('ohc.<name>', <fn>) for each\n"
            "  _ohc_conn() helper reads from ohc_canvas.db\n"
            "  _safe_fetch() graceful fallback if table absent\n\n"
            "File: tools/iqe/adapters/ohc.py"
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-foundation-02",
    },
    {
        "id": "ohc-wiring-03",
        "title": "OHC: Add IQE dispatch entry in app.py _CANVAS_MAP",
        "description": (
            "Add to _CANVAS_MAP inside iqe_dispatch() in tools/dashboard/app.py:\n"
            '  "ohc": ("tools.iqe.adapters.ohc", ["ohc.experiments", "ohc.runs",\n'
            '    "ohc.models", "ohc.datasets", "ohc.adapters", "ohc.drift_events"])\n\n'
            "File: tools/dashboard/app.py"
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-wiring-02",
    },
    {
        "id": "ohc-wiring-04",
        "title": "OHC: Add PATH_CANVAS entry in base.html",
        "description": (
            "Add to PATH_CANVAS array in tools/dashboard/templates/base.html:\n"
            "  [/^\\/ops/, 'ohc']\n\n"
            "Place after the /ai-ml entry.\n\n"
            "File: tools/dashboard/templates/base.html"
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-wiring-01",
    },
    {
        "id": "ohc-wiring-05",
        "title": "OHC: Add nav sidebar link in base.html",
        "description": (
            "Add to Canvases nav dropdown in tools/dashboard/templates/base.html:\n"
            "  {% if ohc_enabled %}\n"
            "  <li class='nav-section-label'>Operations</li>\n"
            "  <li><a href='/ops'>Ops Hub</a></li>\n"
            "  <li><a href='/ops/llm'>LLMOps</a></li>\n"
            "  <li><a href='/ops/models'>MLOps</a></li>\n"
            "  <li><a href='/ops/slos'>SLOs</a></li>\n"
            "  <li><a href='/ops/incidents'>Incidents</a></li>\n"
            "  <li class='nav-dropdown-divider'></li>\n"
            "  {% endif %}\n\n"
            "Place after the AI/ML section.\n"
            "File: tools/dashboard/templates/base.html"
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-wiring-04",
    },
    {
        "id": "ohc-wiring-06",
        "title": "OHC: Register in args/projects.yaml",
        "description": (
            "Add OHC project entry to args/projects.yaml:\n"
            "  key: ohc\n"
            "  name: Ops Hub Canvas (OHC) — Unified LLMOps · MLOps · AIOps\n"
            "  task_prefix: ohc-\n"
            "  briefs: [foundation, engine, blueprint, templates, oss, csp, wiring, vv]\n"
            "  epics: [{key, title, priority} for all 8 epics]\n\n"
            "File: args/projects.yaml"
        ),
        "task_type": "chore",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-foundation-01",
    },
    {
        "id": "ohc-wiring-07",
        "title": "OHC: Manifest shard + append-only tables + conftest.py",
        "description": (
            "1. Create tools/manifest/ops-hub-canvas.md with tool index\n\n"
            "2. Add to APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py:\n"
            "   ohc_adapter_health_log, ohc_data_drift_events\n\n"
            "3. Add OHC table schemas to MINIMAL_ICDEV_SCHEMA in tests/conftest.py:\n"
            "   ohc_experiments, ohc_experiment_runs, ohc_model_registry,\n"
            "   ohc_dataset_versions, ohc_adapter_status, ohc_adapter_health_log,\n"
            "   ohc_data_drift_events\n\n"
            "Files: tools/manifest/ops-hub-canvas.md,\n"
            "  .claude/hooks/pre_tool_use.py, tests/conftest.py"
        ),
        "task_type": "chore",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-foundation-02",
    },
    {
        "id": "ohc-wiring-08",
        "title": "OHC: Register 10 MCP tools in tool_registry.py",
        "description": (
            "Add to tools/mcp/tool_registry.py — 10 OHC tools:\n"
            "  ohc_overview, ohc_llmops_summary, ohc_mlops_experiments,\n"
            "  ohc_model_registry, ohc_slos, ohc_incidents, ohc_topology,\n"
            "  ohc_adapter_health, ohc_run_experiment, ohc_promote_model\n\n"
            "Each entry: {category, module, handler, description, input_schema}\n\n"
            "File: tools/mcp/tool_registry.py"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-engine-05",
    },
    {
        "id": "ohc-wiring-09",
        "title": "OHC: Add 2 Genesis reflexes to reflex_registry.py",
        "description": (
            "Add to REGISTRY list in tools/genesis/reflex_registry.py:\n"
            '  ReflexEntry("llmops_drift_sweep", SUPPORT, 4.0,\n'
            '    "OHC: Quality drift check across all registered LLMs via llmops_engine")\n'
            '  ReflexEntry("mlops_data_drift_sweep", SUPPORT, 4.0,\n'
            '    "OHC: Evidently adapter data drift check for all registered datasets")\n\n'
            "File: tools/genesis/reflex_registry.py"
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-oss-07",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EPIC: vv (4 tasks)
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "ohc-vv-01",
        "title": "OHC V&V: CodeLens + bandit security scan",
        "description": (
            "Run:\n"
            "  python -m tools.code_intelligence.codelens --all --json\n"
            "    (no new critical smells)\n"
            "  python -m bandit -r tools/ops_hub/ --severity-level medium\n"
            "    (clean)\n\n"
            "Fix any issues found before marking done."
        ),
        "task_type": "test",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-wiring-09",
    },
    {
        "id": "ohc-vv-02",
        "title": "OHC V&V: Coherence + companion sync",
        "description": (
            "Run:\n"
            "  python tools/workflow/coherence_checker.py --all --fix --gate\n"
            "    (exit 0)\n"
            "  python tools/dx/companion.py --sync --write --json\n"
            "    (syncs to all AI platform configs)\n\n"
            "Fix any coherence failures before marking done."
        ),
        "task_type": "test",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-vv-01",
    },
    {
        "id": "ohc-vv-03",
        "title": "OHC V&V: Playwright E2E smoke test",
        "description": (
            "Start dev server (ICDEV_OPS_HUB_ENABLED=true flask run)\n"
            "Navigate to: /ops → /ops/llm → /ops/models → /ops/slos\n"
            "  → /ops/incidents → /ops/runbooks → /ops/topology → /ops/self-healing\n"
            "Verify all 8 pages render without 500 errors.\n"
            "Screenshot: playwright/screenshots/ohc-vv-smoke.png\n\n"
            "Use ICDEV Playwright CLI — see memory feedback_playwright_ollama.md"
        ),
        "task_type": "test",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-vv-02",
    },
    {
        "id": "ohc-vv-04",
        "title": "OHC: Feature docs docs/features/phase-71-ops-hub-canvas.md",
        "description": (
            "Create docs/features/phase-71-ops-hub-canvas.md:\n"
            "  Sections: Overview, Sub-pages (8), Adapter Layer (OSS + CSP),\n"
            "    Genesis Reflexes, DB Tables, MCP Tools, Config, Routes\n"
            "  Include feature flag, CLI commands, migration number\n\n"
            "File: docs/features/phase-71-ops-hub-canvas.md"
        ),
        "task_type": "chore",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "ohc-vv-03",
    },
]


def seed():
    conn = _conn()
    now = _NOW

    inserted = 0
    skipped = 0
    for task in TASKS:
        try:
            conn.execute(
                """INSERT INTO kanban_tasks
                   (id, title, description, task_type, priority, status,
                    scheduled_at, created_at, updated_at, depends_on_task_id,
                    dispatch_source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    task["id"],
                    task["title"],
                    task["description"],
                    task.get("task_type", "build"),
                    task.get("priority", "high"),
                    task["status"],
                    task.get("scheduled_at"),
                    now,
                    now,
                    task.get("depends_on_task_id"),
                    "seed:ohc",
                ),
            )
            inserted += 1
            dep = task.get("depends_on_task_id")
            print(f"  INSERTED: {task['id']} [{task['status']}] dep={dep}")
        except Exception as e:
            if "UNIQUE constraint" in str(e) or "duplicate key" in str(e).lower():
                skipped += 1
            else:
                print(f"  ERROR on {task['id']}: {e}")

    conn.commit()
    conn.close()
    print(f"OHC seed: {inserted} inserted, {skipped} skipped (already exist)")


if __name__ == "__main__":
    seed()
