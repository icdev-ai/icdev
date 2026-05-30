# CUI // SP-CTI
"""OHC — AWS Bedrock Guardrails Adapter.

Content filtering, PII redaction, topic blocking, grounding checks.
Extends the existing tools/llm/bedrock_provider.py boto3 client.
GovCloud-ready (us-gov-west-1).

Graceful fallback: returns available=False if boto3 is not installed.
"""

from __future__ import annotations

import os
from tools.ops_hub.adapter_base import OpsAdapter, AdapterHealth, AdapterResource, AdapterMetrics


class BedrockGuardrailsAdapter(OpsAdapter):
    ADAPTER_NAME = "bedrock_guardrails"
    ADAPTER_TYPE = "csp"
    DOMAIN = "llmops"

    def __init__(self):
        self._region = os.environ.get("AWS_DEFAULT_REGION",
                                       os.environ.get("OHC_AWS_REGION", "us-gov-west-1"))
        self._profile = os.environ.get("AWS_PROFILE", "")

    def _client(self):
        import boto3
        session = boto3.Session(profile_name=self._profile or None, region_name=self._region)
        return session.client("bedrock")

    def _runtime_client(self):
        import boto3
        session = boto3.Session(profile_name=self._profile or None, region_name=self._region)
        return session.client("bedrock-runtime")

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
            resp = client.list_guardrails(maxResults=1)
            guardrail_count = len(resp.get("guardrails", []))
            return AdapterHealth(
                available=True,
                adapter_name=self.ADAPTER_NAME,
                adapter_type=self.ADAPTER_TYPE,
                domain=self.DOMAIN,
                latency_ms=elapsed_ms,
                details={"region": self._region, "guardrail_count": guardrail_count},
            )
        except Exception as exc:
            return self._stub_health(str(exc))

    def list_resources(self, resource_type: str = "", **kwargs) -> list[AdapterResource]:
        if not self.available():
            return []
        try:
            client = self._client()
            resp = client.list_guardrails(maxResults=kwargs.get("limit", 20))
            return [AdapterResource(
                id=g["guardrailId"], name=g["name"],
                resource_type="bedrock_guardrail",
                status=g.get("status", "READY").lower(),
                metadata={
                    "arn": g.get("guardrailArn", ""),
                    "version": g.get("version", ""),
                    "description": g.get("description", ""),
                },
            ) for g in resp.get("guardrails", [])]
        except Exception:
            return []

    def get_metrics(self, metric_name: str, **kwargs) -> AdapterMetrics:
        return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name,
                               values=[], error="use cloudwatch_adapter for Bedrock metrics")

    def test_guardrail(self, guardrail_id: str, guardrail_version: str,
                        text: str, source: str = "INPUT") -> dict:
        """Apply a guardrail to a test text and return the result."""
        if not self.available():
            return {"available": False}
        try:
            runtime = self._runtime_client()
            resp = runtime.apply_guardrail(
                guardrailIdentifier=guardrail_id,
                guardrailVersion=guardrail_version,
                source=source,
                content=[{"text": {"text": text}}],
            )
            return {
                "action": resp.get("action"),
                "outputs": resp.get("outputs", []),
                "assessments": resp.get("assessments", []),
            }
        except Exception as exc:
            return {"error": str(exc)}

    def push_event(self, event_type: str, payload: dict) -> dict:
        if event_type == "test_guardrail":
            return self.test_guardrail(
                guardrail_id=payload.get("guardrail_id", ""),
                guardrail_version=payload.get("version", "DRAFT"),
                text=payload.get("text", ""),
                source=payload.get("source", "INPUT"),
            )
        return {"status": "not_implemented", "event_type": event_type}
