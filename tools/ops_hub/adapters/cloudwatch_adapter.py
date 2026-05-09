# CUI // SP-CTI
"""OHC — AWS CloudWatch Adapter.

AI/ML workload metrics from CloudWatch namespaces:
  AWS/SageMaker, AWS/Bedrock, AWS/Lambda, custom OHC namespace.

boto3-based, GovCloud-ready (us-gov-west-1).
Graceful fallback: returns available=False if boto3 is not installed.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from tools.ops_hub.adapter_base import OpsAdapter, AdapterHealth, AdapterResource, AdapterMetrics

# AI/ML CloudWatch namespaces relevant to LLMOps/MLOps/AIOps
_AI_NAMESPACES = [
    "AWS/SageMaker",
    "AWS/Bedrock",
    "AWS/Lambda",
    "/aws/sagemaker/TrainingJobs",
    "/aws/sagemaker/Endpoints",
    "/aws/bedrock/modelInvocations",
]


class CloudWatchAdapter(OpsAdapter):
    ADAPTER_NAME = "cloudwatch"
    ADAPTER_TYPE = "csp"
    DOMAIN = "aiops"

    def __init__(self):
        self._region = os.environ.get("AWS_DEFAULT_REGION",
                                       os.environ.get("OHC_AWS_REGION", "us-gov-west-1"))
        self._profile = os.environ.get("AWS_PROFILE", "")

    def _client(self):
        import boto3
        session = boto3.Session(profile_name=self._profile or None, region_name=self._region)
        return session.client("cloudwatch")

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
            client, elapsed_ms = self._timed_probe(self._client)
            client.list_metrics(Namespace="AWS/SageMaker", MaxResults=1)
            return AdapterHealth(
                available=True,
                adapter_name=self.ADAPTER_NAME,
                adapter_type=self.ADAPTER_TYPE,
                domain=self.DOMAIN,
                latency_ms=elapsed_ms,
                details={"region": self._region, "namespaces": _AI_NAMESPACES},
            )
        except Exception as exc:
            return self._stub_health(str(exc))

    def list_resources(self, resource_type: str = "", **kwargs) -> list[AdapterResource]:
        """List available CloudWatch metrics in AI/ML namespaces."""
        if not self.available():
            return []
        try:
            client = self._client()
            namespace = kwargs.get("namespace", "AWS/SageMaker")
            resp = client.list_metrics(
                Namespace=namespace,
                MaxResults=kwargs.get("limit", 50),
            )
            return [AdapterResource(
                id=f"{m['Namespace']}/{m['MetricName']}",
                name=m["MetricName"],
                resource_type="cloudwatch_metric",
                status="active",
                metadata={
                    "namespace": m["Namespace"],
                    "dimensions": m.get("Dimensions", []),
                },
            ) for m in resp.get("Metrics", [])]
        except Exception:
            return []

    def get_metrics(self, metric_name: str, **kwargs) -> AdapterMetrics:
        """Fetch CloudWatch metric statistics for an AI/ML metric."""
        if not self.available():
            return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name,
                                   values=[], error="boto3 not installed")
        try:
            client = self._client()
            namespace = kwargs.get("namespace", "AWS/SageMaker")
            dimensions = kwargs.get("dimensions", [])
            stat = kwargs.get("stat", "Average")
            period_hours = int(kwargs.get("period_hours", 24))

            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(hours=period_hours)

            resp = client.get_metric_statistics(
                Namespace=namespace,
                MetricName=metric_name,
                Dimensions=dimensions,
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,
                Statistics=[stat],
            )
            values = sorted([
                {"timestamp": str(dp["Timestamp"]), "value": dp[stat], "unit": dp["Unit"]}
                for dp in resp.get("Datapoints", [])
            ], key=lambda x: x["timestamp"])

            return AdapterMetrics(adapter_name=self.ADAPTER_NAME,
                                   metric_name=metric_name, values=values,
                                   unit=values[0]["unit"] if values else "")
        except Exception as exc:
            return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name,
                                   values=[], error=str(exc))

    def get_bedrock_invocations(self, model_id: str = "", hours: int = 24) -> dict:
        """Get Bedrock model invocation metrics (TokenCount, Latency, InvocationCount)."""
        metrics = {}
        for metric_name in ["InputTokenCount", "OutputTokenCount", "InvocationLatency", "Invocations"]:
            result = self.get_metrics(
                metric_name,
                namespace="AWS/Bedrock",
                dimensions=[{"Name": "ModelId", "Value": model_id}] if model_id else [],
                period_hours=hours,
            )
            metrics[metric_name] = result.values
        return metrics

    def push_event(self, event_type: str, payload: dict) -> dict:
        """Put a custom metric to CloudWatch."""
        if not self.available():
            return {"status": "unavailable"}
        try:
            client = self._client()
            if event_type == "put_metric":
                client.put_metric_data(
                    Namespace=payload.get("namespace", "OHC/Custom"),
                    MetricData=[{
                        "MetricName": payload.get("metric_name", "ohc.event"),
                        "Value": float(payload.get("value", 1.0)),
                        "Unit": payload.get("unit", "Count"),
                        "Dimensions": payload.get("dimensions", []),
                    }],
                )
                return {"status": "ok"}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "not_implemented", "event_type": event_type}
