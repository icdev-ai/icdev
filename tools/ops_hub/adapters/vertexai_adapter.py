# CUI // SP-CTI
"""OHC — Google Vertex AI Adapter.

Vertex AI Pipelines (list_pipeline_jobs) + Model Registry (list_models, evaluations).
google-cloud-aiplatform SDK. FedRAMP High (us-gov-central1 Assured Workloads).

Graceful fallback: returns available=False if google-cloud-aiplatform is not installed.
"""

from __future__ import annotations

import os
from tools.ops_hub.adapter_base import OpsAdapter, AdapterHealth, AdapterResource, AdapterMetrics


class VertexAIAdapter(OpsAdapter):
    ADAPTER_NAME = "vertexai"
    ADAPTER_TYPE = "csp"
    DOMAIN = "mlops"

    def __init__(self):
        self._project = os.environ.get("GOOGLE_CLOUD_PROJECT",
                                        os.environ.get("OHC_GCP_PROJECT", ""))
        self._location = os.environ.get("VERTEX_AI_LOCATION",
                                         os.environ.get("OHC_GCP_LOCATION", "us-central1"))

    def available(self) -> bool:
        try:
            from google.cloud import aiplatform  # noqa: F401
            return bool(self._project)
        except ImportError:
            return False

    def _init(self):
        from google.cloud import aiplatform
        aiplatform.init(project=self._project, location=self._location)
        return aiplatform

    def health_check(self) -> AdapterHealth:
        if not self.available():
            if not _has_vertexai():
                return self._stub_health(
                    "google-cloud-aiplatform not installed — pip install google-cloud-aiplatform"
                )
            return self._stub_health("GOOGLE_CLOUD_PROJECT not set")
        try:
            import google.cloud.aiplatform as aip
            _, elapsed_ms = self._timed_probe(self._init)
            return AdapterHealth(
                available=True,
                adapter_name=self.ADAPTER_NAME,
                adapter_type=self.ADAPTER_TYPE,
                domain=self.DOMAIN,
                version=aip.__version__,
                latency_ms=elapsed_ms,
                details={"project": self._project, "location": self._location},
            )
        except Exception as exc:
            return self._stub_health(str(exc))

    def list_resources(self, resource_type: str = "", **kwargs) -> list[AdapterResource]:
        if not self.available():
            return []
        try:
            aip = self._init()
            limit = kwargs.get("limit", 20)

            if resource_type in ("models", ""):
                models = aip.Model.list(order_by="create_time desc")[:limit]
                return [AdapterResource(
                    id=m.resource_name, name=m.display_name,
                    resource_type="vertex_model",
                    status="active",
                    metadata={
                        "version": m.version_id,
                        "framework": m.metadata.get("frameworkType", ""),
                        "artifact_uri": m.artifact_uri,
                    },
                ) for m in models]

            if resource_type == "pipelines":
                jobs = aip.PipelineJob.list(
                    order_by="create_time desc",
                    max_results=limit,
                )
                return [AdapterResource(
                    id=j.resource_name, name=j.display_name,
                    resource_type="vertex_pipeline",
                    status=j.state.name.lower().replace("pipeline_state_", ""),
                    metadata={"create_time": str(j.create_time)},
                ) for j in jobs]

            if resource_type == "datasets":
                datasets = aip.TabularDataset.list()[:limit]
                return [AdapterResource(
                    id=d.resource_name, name=d.display_name,
                    resource_type="vertex_dataset",
                    status="active",
                    metadata={},
                ) for d in datasets]

        except Exception:
            pass
        return []

    def get_metrics(self, metric_name: str, **kwargs) -> AdapterMetrics:
        if not self.available():
            return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name,
                                   values=[], error="google-cloud-aiplatform not available")
        try:
            aip = self._init()
            model_resource = kwargs.get("model_resource", "")
            if not model_resource:
                return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name,
                                       values=[], error="model_resource required")
            model = aip.Model(model_resource)
            evaluations = model.list_model_evaluations()
            values = []
            for ev in evaluations:
                metrics = ev.metrics or {}
                values.append({"evaluation_id": ev.resource_name, "metrics": metrics})
            return AdapterMetrics(adapter_name=self.ADAPTER_NAME,
                                   metric_name="model_evaluation", values=values)
        except Exception as exc:
            return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name,
                                   values=[], error=str(exc))

    def push_event(self, event_type: str, payload: dict) -> dict:
        return {"status": "not_implemented", "note": "Use Vertex AI SDK directly for job submission"}


def _has_vertexai() -> bool:
    try:
        from google.cloud import aiplatform  # noqa: F401
        return True
    except ImportError:
        return False
