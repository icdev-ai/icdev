# CUI // SP-CTI
"""OHC — MLflow Adapter.

Extends tools/observability/mlflow_exporter.py to add:
- Experiment CRUD
- Model Registry (register, stage transition, list)
- Full tracking server integration

Graceful fallback: returns available=False if mlflow is not installed.
Local mode: sqlite:///mlflow.db (no server required)
"""

from __future__ import annotations

import os
from tools.ops_hub.adapter_base import OpsAdapter, AdapterHealth, AdapterResource, AdapterMetrics


class MLflowAdapter(OpsAdapter):
    ADAPTER_NAME = "mlflow"
    ADAPTER_TYPE = "oss"
    DOMAIN = "mlops"

    def __init__(self):
        self._tracking_uri = os.environ.get(
            "MLFLOW_TRACKING_URI",
            os.environ.get("OHC_MLFLOW_URI", "sqlite:///data/mlflow.db"),
        )

    def available(self) -> bool:
        try:
            import mlflow  # noqa: F401
            return True
        except ImportError:
            return False

    def health_check(self) -> AdapterHealth:
        if not self.available():
            return self._stub_health("mlflow not installed — pip install mlflow")
        try:
            import mlflow
            fn = lambda: mlflow.set_tracking_uri(self._tracking_uri) or mlflow.MlflowClient()
            client, elapsed_ms = self._timed_probe(fn)
            return AdapterHealth(
                available=True,
                adapter_name=self.ADAPTER_NAME,
                adapter_type=self.ADAPTER_TYPE,
                domain=self.DOMAIN,
                version=mlflow.__version__,
                latency_ms=elapsed_ms,
                details={"tracking_uri": self._tracking_uri},
            )
        except Exception as exc:
            return self._stub_health(str(exc))

    def list_resources(self, resource_type: str = "", **kwargs) -> list[AdapterResource]:
        if not self.available():
            return []
        try:
            import mlflow
            mlflow.set_tracking_uri(self._tracking_uri)
            client = mlflow.MlflowClient()

            if resource_type == "experiments":
                exps = client.search_experiments()
                return [AdapterResource(
                    id=e.experiment_id, name=e.name,
                    resource_type="experiment",
                    status="active" if e.lifecycle_stage == "active" else "deleted",
                    metadata={"artifact_location": e.artifact_location},
                ) for e in exps]

            if resource_type == "models":
                models = client.search_registered_models()
                return [AdapterResource(
                    id=m.name, name=m.name,
                    resource_type="registered_model",
                    status="active",
                    metadata={"latest_versions": len(m.latest_versions)},
                ) for m in models]

            if resource_type == "runs":
                exp_id = kwargs.get("experiment_id", "")
                if not exp_id:
                    return []
                runs = client.search_runs([exp_id], max_results=kwargs.get("limit", 20))
                return [AdapterResource(
                    id=r.info.run_id, name=r.info.run_name or r.info.run_id,
                    resource_type="run",
                    status=r.info.status.lower(),
                    metadata={"metrics": r.data.metrics, "params": r.data.params},
                ) for r in runs]

        except Exception:
            pass
        return []

    def get_metrics(self, metric_name: str, **kwargs) -> AdapterMetrics:
        if not self.available():
            return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name,
                                   values=[], error="mlflow not installed")
        try:
            import mlflow
            mlflow.set_tracking_uri(self._tracking_uri)
            client = mlflow.MlflowClient()
            run_id = kwargs.get("run_id", "")
            if not run_id:
                return AdapterMetrics(adapter_name=self.ADAPTER_NAME,
                                       metric_name=metric_name, values=[])
            history = client.get_metric_history(run_id, metric_name)
            values = [{"step": m.step, "value": m.value, "timestamp": m.timestamp}
                      for m in history]
            return AdapterMetrics(adapter_name=self.ADAPTER_NAME,
                                   metric_name=metric_name, values=values)
        except Exception as exc:
            return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name,
                                   values=[], error=str(exc))

    def push_event(self, event_type: str, payload: dict) -> dict:
        """Sync events back to MLflow tracking server."""
        if not self.available():
            return {"status": "unavailable"}
        try:
            import mlflow
            mlflow.set_tracking_uri(self._tracking_uri)
            client = mlflow.MlflowClient()

            if event_type == "create_experiment":
                eid = client.create_experiment(payload["name"])
                return {"status": "ok", "experiment_id": eid}

            if event_type == "register_model":
                result = mlflow.register_model(
                    model_uri=payload.get("artifact_path", ""),
                    name=payload["name"],
                )
                return {"status": "ok", "version": result.version}

            if event_type == "transition_stage":
                client.transition_model_version_stage(
                    name=payload["name"],
                    version=payload["version"],
                    stage=payload["stage"].capitalize(),
                )
                return {"status": "ok"}

        except Exception as exc:
            return {"status": "error", "error": str(exc)}

        return {"status": "not_implemented", "event_type": event_type}
