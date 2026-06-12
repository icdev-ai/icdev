# CUI // SP-CTI
"""OHC — Langfuse Adapter.

LLM observability: traces, evals, cost per model, session analytics.
Self-host: docker run -p 3000:3000 langfuse/langfuse

Graceful fallback: returns available=False if langfuse SDK is not installed.
"""

from __future__ import annotations

import os
from tools.ops_hub.adapter_base import OpsAdapter, AdapterHealth, AdapterResource, AdapterMetrics


class LangfuseAdapter(OpsAdapter):
    ADAPTER_NAME = "langfuse"
    ADAPTER_TYPE = "oss"
    DOMAIN = "llmops"

    def __init__(self):
        self._host = os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
        self._public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
        self._secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")

    def available(self) -> bool:
        try:
            import langfuse  # noqa: F401
            return bool(self._public_key and self._secret_key)
        except ImportError:
            return False

    def health_check(self) -> AdapterHealth:
        if not self.available():
            msg = ("langfuse not installed — pip install langfuse"
                   if not _has_langfuse()
                   else "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set")
            return self._stub_health(msg)
        try:
            import langfuse
            client, elapsed_ms = self._timed_probe(lambda: langfuse.Langfuse(
                host=self._host,
                public_key=self._public_key,
                secret_key=self._secret_key,
            ))
            return AdapterHealth(
                available=True,
                adapter_name=self.ADAPTER_NAME,
                adapter_type=self.ADAPTER_TYPE,
                domain=self.DOMAIN,
                version=langfuse.__version__,
                latency_ms=elapsed_ms,
                details={"host": self._host},
            )
        except Exception as exc:
            return self._stub_health(str(exc))

    def _client(self):
        import langfuse
        return langfuse.Langfuse(
            host=self._host,
            public_key=self._public_key,
            secret_key=self._secret_key,
        )

    def list_resources(self, resource_type: str = "", **kwargs) -> list[AdapterResource]:
        if not self.available():
            return []
        try:
            client = self._client()
            limit = kwargs.get("limit", 20)

            if resource_type in ("traces", ""):
                traces = client.fetch_traces(limit=limit).data
                return [AdapterResource(
                    id=t.id, name=t.name or t.id,
                    resource_type="trace",
                    status="complete" if t.latency else "pending",
                    metadata={
                        "latency_ms": t.latency,
                        "total_cost": t.totalCost,
                        "model": getattr(t, "model", ""),
                        "timestamp": str(t.timestamp),
                    },
                ) for t in traces]

            if resource_type == "scores":
                scores = client.fetch_scores(limit=limit).data
                return [AdapterResource(
                    id=s.id, name=s.name,
                    resource_type="score",
                    status="ok",
                    metadata={"value": s.value, "trace_id": s.traceId},
                ) for s in scores]

        except Exception:
            pass
        return []

    def get_metrics(self, metric_name: str, **kwargs) -> AdapterMetrics:
        if not self.available():
            return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name,
                                   values=[], error="langfuse unavailable")
        # Langfuse doesn't expose a direct metrics API; derive from traces
        try:
            resources = self.list_resources("traces", limit=100)
            if metric_name == "cost_per_model":
                cost_map: dict = {}
                for r in resources:
                    model = r.metadata.get("model", "unknown")
                    cost = r.metadata.get("total_cost") or 0
                    cost_map[model] = cost_map.get(model, 0.0) + float(cost)
                values = [{"model": m, "cost": c} for m, c in cost_map.items()]
                return AdapterMetrics(adapter_name=self.ADAPTER_NAME,
                                       metric_name=metric_name, values=values, unit="USD")
            if metric_name == "latency_p95":
                latencies = sorted([
                    r.metadata.get("latency_ms") or 0 for r in resources
                ])
                p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
                return AdapterMetrics(adapter_name=self.ADAPTER_NAME,
                                       metric_name=metric_name,
                                       values=[{"p95_ms": p95}], unit="ms")
        except Exception as exc:
            return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name,
                                   values=[], error=str(exc))
        return AdapterMetrics(adapter_name=self.ADAPTER_NAME, metric_name=metric_name, values=[])

    def push_event(self, event_type: str, payload: dict) -> dict:
        if not self.available():
            return {"status": "unavailable"}
        try:
            client = self._client()
            if event_type == "trace":
                trace = client.trace(
                    name=payload.get("name", "ohc-event"),
                    metadata=payload,
                )
                return {"status": "ok", "trace_id": trace.id}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "not_implemented", "event_type": event_type}


def _has_langfuse() -> bool:
    try:
        import langfuse  # noqa: F401
        return True
    except ImportError:
        return False
