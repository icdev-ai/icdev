# CUI // SP-CTI
"""OHC — AWS SageMaker Adapter.

SageMaker Experiments (create/list trial components),
SageMaker Model Registry (list groups, list packages, stage transition).
boto3-based, GovCloud-ready (us-gov-west-1).

Graceful fallback: returns available=False if boto3/sagemaker is not installed.
"""

from __future__ import annotations

import os
from tools.ops_hub.adapter_base import OpsAdapter, AdapterHealth, AdapterResource, AdapterMetrics


class SageMakerAdapter(OpsAdapter):
    ADAPTER_NAME = "sagemaker"
    ADAPTER_TYPE = "csp"
    DOMAIN = "mlops"

    def __init__(self):
        self._region = os.environ.get("AWS_DEFAULT_REGION",
                                       os.environ.get("OHC_AWS_REGION", "us-gov-west-1"))
        self._profile = os.environ.get("AWS_PROFILE", "")
        self._sm_client = None
        self._experiments_client = None

    def _clients(self):
        import boto3
        session = boto3.Session(profile_name=self._profile or None, region_name=self._region)
        sm = session.client("sagemaker")
        return sm

    def available(self) -> bool:
        try:
            import boto3  # noqa: F401
            return True
        except ImportError:
            return False

    def health_check(self) -> AdapterHealth:
        if not self.available():
            return self._stub_health("boto3 not installed — pip install boto3")
        try:
            sm, elapsed_ms = self._timed_probe(self._clients)
            # Light probe: list 1 model package group
            sm.list_model_package_groups(MaxResults=1)
            return AdapterHealth(
                available=True,
                adapter_name=self.ADAPTER_NAME,
                adapter_type=self.ADAPTER_TYPE,
                domain=self.DOMAIN,
                latency_ms=elapsed_ms,
                details={"region": self._region},
            )
        except Exception as exc:
            return self._stub_health(str(exc))

    def list_resources(self, resource_type: str = "", **kwargs) -> list[AdapterResource]:
        if not self.available():
            return []
        try:
            sm = self._clients()

            if resource_type == "experiments":
                resp = sm.list_experiments(MaxResults=kwargs.get("limit", 20))
                return [AdapterResource(
                    id=e["ExperimentArn"], name=e["ExperimentName"],
                    resource_type="sm_experiment",
                    status="active",
                    metadata={"arn": e["ExperimentArn"]},
                ) for e in resp.get("ExperimentSummaries", [])]

            if resource_type in ("models", ""):
                resp = sm.list_model_package_groups(MaxResults=kwargs.get("limit", 20))
                return [AdapterResource(
                    id=g["ModelPackageGroupArn"], name=g["ModelPackageGroupName"],
                    resource_type="sm_model_group",
                    status=g.get("ModelPackageGroupStatus", "unknown"),
                    metadata={"arn": g["ModelPackageGroupArn"]},
                ) for g in resp.get("ModelPackageGroupSummaryList", [])]

            if resource_type == "pipelines":
                resp = sm.list_pipelines(MaxResults=kwargs.get("limit", 20))
                return [AdapterResource(
                    id=p["PipelineArn"], name=p["PipelineName"],
                    resource_type="sm_pipeline",
                    status=p.get("PipelineStatus", "unknown"),
                    metadata={"arn": p["PipelineArn"]},
                ) for p in resp.get("PipelineSummaries", [])]

        except Exception:
            pass
        return []

    def get_metrics(self, metric_name: str, **kwargs) -> AdapterMetrics:
        return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name,
                               values=[], error="use cloudwatch_adapter for SageMaker metrics")

    def push_event(self, event_type: str, payload: dict) -> dict:
        if not self.available():
            return {"status": "unavailable"}
        try:
            sm = self._clients()
            if event_type == "create_trial_component":
                kwargs: dict = {"TrialComponentName": payload["name"]}
                if payload.get("display_name"):
                    kwargs["DisplayName"] = payload["display_name"]
                if payload.get("trial_name"):
                    sm.associate_trial_component(
                        TrialComponentName=payload["name"],
                        TrialName=payload["trial_name"],
                    )
                resp = sm.create_trial_component(**kwargs)
                return {"status": "ok", "arn": resp.get("TrialComponentArn", "")}
            if event_type == "transition_model_stage":
                sm.update_model_package(
                    ModelPackageArn=payload.get("arn", ""),
                    ModelApprovalStatus=payload.get("approval_status", "PendingManualApproval"),
                )
                return {"status": "ok"}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "not_implemented", "event_type": event_type}
