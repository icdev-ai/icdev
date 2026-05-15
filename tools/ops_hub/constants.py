# CUI // SP-CTI
"""OHC — Ops Hub Canvas constants.

Shared constants for the unified LLMOps · MLOps · AIOps canvas at /ops.
"""

from __future__ import annotations

# ── Feature Flag ───────────────────────────────────────────────────────────────
OHC_FEATURE_FLAG = "ICDEV_OPS_HUB_ENABLED"

# ── Canvas Domains ─────────────────────────────────────────────────────────────
CANVAS_DOMAINS = ["llmops", "mlops", "aiops"]

# ── Sub-Page Routes ────────────────────────────────────────────────────────────
OHC_ROUTES = {
    "overview":     "/ops",
    "llmops":       "/ops/llm",
    "mlops":        "/ops/models",
    "slos":         "/ops/slos",
    "incidents":    "/ops/incidents",
    "runbooks":     "/ops/runbooks",
    "topology":     "/ops/topology",
    "self_healing": "/ops/self-healing",
}

# ── Ops Metric Types ───────────────────────────────────────────────────────────
OPS_METRIC_TYPES = [
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "token_throughput_tps",
    "error_rate_pct",
    "cost_usd",
    "quality_score",
    "drift_score",
    "availability_pct",
    "slo_burn_rate",
]

# ── Adapter Types ──────────────────────────────────────────────────────────────
ADAPTER_TYPES = ["oss", "csp"]

# ── Open Source Tools ──────────────────────────────────────────────────────────
OSS_TOOLS = {
    "mlflow": {
        "label": "MLflow",
        "domain": "mlops",
        "description": "Experiment tracking, model registry, model serving",
        "local_mode": "SQLite tracking server (mlflow ui --backend-store-uri sqlite:///mlflow.db)",
        "install": "pip install mlflow",
        "docs": "https://mlflow.org",
        "air_gap_ready": True,
        "python_import": "mlflow",
    },
    "evidently": {
        "label": "Evidently AI",
        "domain": "mlops/llmops",
        "description": "Data drift, model monitoring, data quality reports",
        "local_mode": "Pure Python — no server required",
        "install": "pip install evidently",
        "docs": "https://docs.evidentlyai.com",
        "air_gap_ready": True,
        "python_import": "evidently",
    },
    "langfuse": {
        "label": "Langfuse",
        "domain": "llmops",
        "description": "LLM observability — traces, evals, cost, prompt management",
        "local_mode": "Docker self-host: docker run langfuse/langfuse",
        "install": "pip install langfuse",
        "docs": "https://langfuse.com/docs",
        "air_gap_ready": False,  # requires Docker
        "python_import": "langfuse",
    },
    "prometheus": {
        "label": "Prometheus",
        "domain": "aiops",
        "description": "Metrics scraping, alerting, time-series storage",
        "local_mode": "HTTP API (query/query_range) or file-based pushgateway",
        "install": "pip install prometheus-client",
        "docs": "https://prometheus.io/docs",
        "air_gap_ready": True,
        "python_import": "prometheus_client",
    },
    "onnx": {
        "label": "ONNX Runtime",
        "domain": "mlops",
        "description": "Cross-framework model inference, validation, inspection",
        "local_mode": "Pure Python — pip install onnxruntime",
        "install": "pip install onnxruntime",
        "docs": "https://onnxruntime.ai",
        "air_gap_ready": True,
        "python_import": "onnxruntime",
    },
    "dvc": {
        "label": "DVC",
        "domain": "mlops",
        "description": "Dataset versioning, pipeline tracking, data lineage",
        "local_mode": "CLI (dvc data status, dvc repro)",
        "install": "pip install dvc",
        "docs": "https://dvc.org/doc",
        "air_gap_ready": True,
        "python_import": None,  # CLI-based, no direct Python import
    },
}

# ── CSP Native Tools ───────────────────────────────────────────────────────────
CSP_PROVIDERS = {
    "sagemaker": {
        "label": "AWS SageMaker",
        "domain": "mlops",
        "description": "Experiments, Model Registry, Model Monitor, Pipelines",
        "sdk": "boto3",
        "govcloud_region": "us-gov-west-1",
        "fedramp_level": "FedRAMP High",
        "air_gap_ready": False,
        "il_support": [2, 4],
    },
    "azureml": {
        "label": "Azure ML",
        "domain": "mlops",
        "description": "Pipelines, Model Registry, Managed Online Endpoints",
        "sdk": "azure-ai-ml",
        "govcloud_region": "usgovvirginia",
        "fedramp_level": "FedRAMP High",
        "air_gap_ready": False,
        "il_support": [2, 4],
    },
    "vertexai": {
        "label": "Vertex AI",
        "domain": "mlops",
        "description": "Pipelines, Model Registry, Model Evaluation, Prediction",
        "sdk": "google-cloud-aiplatform",
        "govcloud_region": "us-gov-central1",
        "fedramp_level": "FedRAMP High",
        "air_gap_ready": False,
        "il_support": [2, 4],
    },
    "bedrock_guardrails": {
        "label": "Bedrock Guardrails",
        "domain": "llmops",
        "description": "Content filtering, PII redaction, topic blocking, grounding checks",
        "sdk": "boto3",
        "govcloud_region": "us-gov-west-1",
        "fedramp_level": "FedRAMP High",
        "air_gap_ready": False,
        "il_support": [2, 4],
    },
    "cloudwatch": {
        "label": "CloudWatch",
        "domain": "aiops",
        "description": "AI/ML workload metrics — SageMaker, Bedrock, Lambda namespaces",
        "sdk": "boto3",
        "govcloud_region": "us-gov-west-1",
        "fedramp_level": "FedRAMP High",
        "air_gap_ready": False,
        "il_support": [2, 4],
    },
}

# ── Status Colors ──────────────────────────────────────────────────────────────
STATUS_COLORS = {
    "healthy":      "#22c55e",
    "degraded":     "#f59e0b",
    "critical":     "#ef4444",
    "unknown":      "#94a3b8",
    "unavailable":  "#64748b",
}

# ── Severity Levels ────────────────────────────────────────────────────────────
SEVERITY_LEVELS = {
    "sev1": {"label": "SEV1 — Critical",   "color": "#ef4444", "slo_impact": "CRITICAL"},
    "sev2": {"label": "SEV2 — High",       "color": "#f97316", "slo_impact": "HIGH"},
    "sev3": {"label": "SEV3 — Medium",     "color": "#f59e0b", "slo_impact": "MEDIUM"},
    "sev4": {"label": "SEV4 — Low",        "color": "#22c55e", "slo_impact": "LOW"},
}

SEVERITY_VALUES = list(SEVERITY_LEVELS.keys())

# ── Experiment Run States ──────────────────────────────────────────────────────
EXPERIMENT_STATES = ["running", "completed", "failed", "killed", "scheduled"]
MODEL_STAGES = ["none", "staging", "production", "archived"]
ADAPTER_HEALTH_STATES = ["healthy", "degraded", "unavailable"]

# ── IQE Collections ───────────────────────────────────────────────────────────
IQE_COLLECTIONS = [
    "ohc.experiments",
    "ohc.models",
    "ohc.slos",
    "ohc.incidents",
    "ohc.topology",
    "ohc.adapters",
]

# ── NIST 800-53 Controls ──────────────────────────────────────────────────────
OHC_NIST_CONTROLS = [
    "AU-2",   # Event logging — LLM gateway audit
    "AU-3",   # Content of audit records
    "AU-6",   # Audit log review, analysis, reporting
    "AU-12",  # Audit record generation
    "SI-4",   # System monitoring — AIOps
    "SI-5",   # Security alerts / advisories
    "IR-4",   # Incident handling
    "IR-5",   # Incident monitoring (MTTR)
    "IR-6",   # Incident reporting
    "CA-7",   # Continuous monitoring — SLO burn rate
    "SA-11",  # Developer security testing — model eval
]


# ── Ontology Mapping (OHC -> ICDEV observability ontology) ───────────────────
OHC_ONTOLOGY_MAP: dict[str, str] = {
    "latency_p50_ms": "https://icdev.dev/ontology/observability#Metric.latency.p50.ms",
    "latency_p95_ms": "https://icdev.dev/ontology/observability#Metric.latency.p95.ms",
    "latency_p99_ms": "https://icdev.dev/ontology/observability#Metric.latency.p99.ms",
    "token_throughput_tps": "https://icdev.dev/ontology/observability#Metric.token.throughput.tps",
    "error_rate_pct": "https://icdev.dev/ontology/observability#Metric.error.rate.pct",
    "cost_usd": "https://icdev.dev/ontology/observability#Metric.cost.usd",
    "quality_score": "https://icdev.dev/ontology/observability#Metric.quality.score",
    "drift_score": "https://icdev.dev/ontology/observability#Metric.drift.score",
    "availability_pct": "https://icdev.dev/ontology/observability#Metric.availability.pct",
    "slo_burn_rate": "https://icdev.dev/ontology/observability#Metric.slo.burn.rate",
    "mlflow": "https://icdev.dev/ontology/observability#OpsTool.Mlflow",
    "evidently": "https://icdev.dev/ontology/observability#OpsTool.Evidently",
    "langfuse": "https://icdev.dev/ontology/observability#OpsTool.Langfuse",
    "prometheus": "https://icdev.dev/ontology/observability#OpsTool.Prometheus",
    "onnx": "https://icdev.dev/ontology/observability#OpsTool.Onnx",
    "dvc": "https://icdev.dev/ontology/observability#OpsTool.Dvc",
    "sagemaker": "https://icdev.dev/ontology/observability#CSPService.Sagemaker",
    "azureml": "https://icdev.dev/ontology/observability#CSPService.Azureml",
    "vertexai": "https://icdev.dev/ontology/observability#CSPService.Vertexai",
    "bedrock_guardrails": "https://icdev.dev/ontology/observability#CSPService.Bedrock.Guardrails",
    "cloudwatch": "https://icdev.dev/ontology/observability#CSPService.Cloudwatch",
    "sev1": "https://icdev.dev/ontology/observability#Severity.SEV1",
    "sev2": "https://icdev.dev/ontology/observability#Severity.SEV2",
    "sev3": "https://icdev.dev/ontology/observability#Severity.SEV3",
    "sev4": "https://icdev.dev/ontology/observability#Severity.SEV4",
    "running": "https://icdev.dev/ontology/observability#ExperimentState.Running",
    "completed": "https://icdev.dev/ontology/observability#ExperimentState.Completed",
    "failed": "https://icdev.dev/ontology/observability#ExperimentState.Failed",
    "killed": "https://icdev.dev/ontology/observability#ExperimentState.Killed",
    "scheduled": "https://icdev.dev/ontology/observability#ExperimentState.Scheduled",
    "none": "https://icdev.dev/ontology/observability#ModelStage.None",
    "staging": "https://icdev.dev/ontology/observability#ModelStage.Staging",
    "production": "https://icdev.dev/ontology/observability#ModelStage.Production",
    "archived": "https://icdev.dev/ontology/observability#ModelStage.Archived",
    "healthy": "https://icdev.dev/ontology/observability#HealthState.Healthy",
    "degraded": "https://icdev.dev/ontology/observability#HealthState.Degraded",
    "unavailable": "https://icdev.dev/ontology/observability#HealthState.Unavailable",
}
