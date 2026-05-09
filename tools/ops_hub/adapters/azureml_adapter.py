# CUI // SP-CTI
"""OHC — Azure ML Adapter.

Azure ML jobs list, model registry (list/create), pipeline run status.
azure-ai-ml SDK. Azure Government (usgovvirginia) for IL4.

Graceful fallback: returns available=False if azure-ai-ml is not installed.
"""

from __future__ import annotations

import os
from tools.ops_hub.adapter_base import OpsAdapter, AdapterHealth, AdapterResource, AdapterMetrics


class AzureMLAdapter(OpsAdapter):
    ADAPTER_NAME = "azureml"
    ADAPTER_TYPE = "csp"
    DOMAIN = "mlops"

    def __init__(self):
        self._subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
        self._resource_group = os.environ.get("AZURE_RESOURCE_GROUP", "")
        self._workspace_name = os.environ.get("AZURE_ML_WORKSPACE", "")

    def available(self) -> bool:
        try:
            from azure.ai.ml import MLClient  # noqa: F401
            return bool(self._subscription_id and self._resource_group and self._workspace_name)
        except ImportError:
            return False

    def _client(self):
        from azure.ai.ml import MLClient
        from azure.identity import DefaultAzureCredential
        return MLClient(
            credential=DefaultAzureCredential(),
            subscription_id=self._subscription_id,
            resource_group_name=self._resource_group,
            workspace_name=self._workspace_name,
        )

    def health_check(self) -> AdapterHealth:
        if not self.available():
            if not _has_azureml():
                return self._stub_health("azure-ai-ml not installed — pip install azure-ai-ml azure-identity")
            return self._stub_health("AZURE_SUBSCRIPTION_ID / AZURE_RESOURCE_GROUP / AZURE_ML_WORKSPACE not set")
        try:
            import azure.ai.ml as aml
            client, elapsed_ms = self._timed_probe(self._client)
            ws = client.workspaces.get(self._workspace_name)
            return AdapterHealth(
                available=True,
                adapter_name=self.ADAPTER_NAME,
                adapter_type=self.ADAPTER_TYPE,
                domain=self.DOMAIN,
                version=aml.__version__,
                latency_ms=elapsed_ms,
                details={"workspace": ws.name, "location": ws.location},
            )
        except Exception as exc:
            return self._stub_health(str(exc))

    def list_resources(self, resource_type: str = "", **kwargs) -> list[AdapterResource]:
        if not self.available():
            return []
        try:
            client = self._client()
            limit = kwargs.get("limit", 20)

            if resource_type in ("models", ""):
                models = list(client.models.list())[:limit]
                return [AdapterResource(
                    id=m.id or m.name, name=m.name,
                    resource_type="azureml_model",
                    status=m.stage or "active",
                    metadata={"version": m.version, "type": m.type},
                ) for m in models]

            if resource_type == "jobs":
                jobs = list(client.jobs.list())[:limit]
                return [AdapterResource(
                    id=j.id or j.name, name=j.name or j.display_name or "",
                    resource_type="azureml_job",
                    status=j.status or "unknown",
                    metadata={"type": j.type},
                ) for j in jobs]

            if resource_type == "pipelines":
                # Pipeline jobs (type=pipeline)
                jobs = [j for j in list(client.jobs.list())[:limit * 2] if j.type == "pipeline"]
                return [AdapterResource(
                    id=j.id or j.name, name=j.display_name or j.name or "",
                    resource_type="azureml_pipeline",
                    status=j.status or "unknown",
                    metadata={},
                ) for j in jobs[:limit]]

        except Exception:
            pass
        return []

    def get_metrics(self, metric_name: str, **kwargs) -> AdapterMetrics:
        return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name,
                               values=[], error="use Azure Monitor for metrics")

    def push_event(self, event_type: str, payload: dict) -> dict:
        if not self.available():
            return {"status": "unavailable"}
        try:
            client = self._client()
            if event_type == "create_job":
                from azure.ai.ml.entities import CommandJob
                job = CommandJob(
                    command=payload.get("command", "echo ohc"),
                    environment=payload.get("environment", "AzureML-sklearn-0.24-ubuntu18.04-py37-cpu:9"),
                    compute=payload.get("compute", ""),
                    display_name=payload.get("display_name", "ohc-job"),
                )
                submitted = client.jobs.create_or_update(job)
                return {"status": "ok", "job_id": submitted.id}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "not_implemented", "event_type": event_type}


def _has_azureml() -> bool:
    try:
        from azure.ai.ml import MLClient  # noqa: F401
        return True
    except ImportError:
        return False
