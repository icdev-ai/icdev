# CUI // SP-CTI
"""OHC — Adapter Registry.

Discovers, loads, and health-probes all OHC adapters (OSS + CSP).
Results are persisted to ohc_adapter_status and ohc_adapter_health_log.
"""

from __future__ import annotations

import importlib
import json
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.ops_hub.adapter_base import OpsAdapter

# Ordered list of adapter module paths → class names
_ADAPTER_MAP: dict[str, str] = {
    # OSS
    "mlflow":                "tools.ops_hub.adapters.mlflow_adapter.MLflowAdapter",
    "evidently":             "tools.ops_hub.adapters.evidently_adapter.EvidentlyAdapter",
    "langfuse":              "tools.ops_hub.adapters.langfuse_adapter.LangfuseAdapter",
    "prometheus":            "tools.ops_hub.adapters.prometheus_adapter.PrometheusAdapter",
    "onnx":                  "tools.ops_hub.adapters.onnx_adapter.ONNXAdapter",
    "dvc":                   "tools.ops_hub.adapters.dvc_adapter.DVCAdapter",
    # CSP
    "sagemaker":             "tools.ops_hub.adapters.sagemaker_adapter.SageMakerAdapter",
    "azureml":               "tools.ops_hub.adapters.azureml_adapter.AzureMLAdapter",
    "vertexai":              "tools.ops_hub.adapters.vertexai_adapter.VertexAIAdapter",
    "bedrock_guardrails":    "tools.ops_hub.adapters.bedrock_guardrails_adapter.BedrockGuardrailsAdapter",
    "cloudwatch":            "tools.ops_hub.adapters.cloudwatch_adapter.CloudWatchAdapter",
}

# Explicit list of the 6 OSS adapters for targeted probing
OSS_ADAPTERS: list[str] = ["mlflow", "evidently", "langfuse", "prometheus", "onnx", "dvc"]

_loaded: dict[str, "OpsAdapter"] = {}


def _load_adapter(name: str) -> "OpsAdapter | None":
    """Import and instantiate an adapter class. Returns None on failure."""
    dotpath = _ADAPTER_MAP.get(name)
    if not dotpath:
        return None
    module_path, cls_name = dotpath.rsplit(".", 1)
    try:
        module = importlib.import_module(module_path)
        cls = getattr(module, cls_name)
        return cls()
    except Exception:
        return None


def get_adapter(name: str) -> "OpsAdapter | None":
    """Return a cached adapter instance, loading it on first access."""
    if name not in _loaded:
        adapter = _load_adapter(name)
        if adapter is not None:
            _loaded[name] = adapter
    return _loaded.get(name)


def list_adapters() -> list[str]:
    """Return all registered adapter names."""
    return list(_ADAPTER_MAP.keys())


def probe_all(persist: bool = True) -> list[dict]:
    """Health-probe every adapter. Optionally persist results to DB."""
    results = []
    now = datetime.now(timezone.utc).isoformat()

    for name in _ADAPTER_MAP:
        adapter = get_adapter(name)
        if adapter is None:
            health_dict = {
                "available": False,
                "adapter_name": name,
                "adapter_type": "unknown",
                "domain": "unknown",
                "version": "",
                "latency_ms": 0,
                "error": "adapter class failed to load",
                "details": {},
            }
        else:
            try:
                health = adapter.health_check()
                health_dict = health.to_dict()
            except Exception as exc:
                health_dict = {
                    "available": False,
                    "adapter_name": name,
                    "adapter_type": getattr(adapter, "ADAPTER_TYPE", "unknown"),
                    "domain": getattr(adapter, "DOMAIN", "unknown"),
                    "version": "",
                    "latency_ms": 0,
                    "error": str(exc),
                    "details": {},
                }

        results.append(health_dict)

        if persist:
            _persist_health(name, health_dict, now)

    return results


# ── TTL cache for probe_all (cnr-ops-02) ─────────────────────────────────────
# probe_all(persist=True) runs a live health check against every adapter (network
# I/O) AND writes a row per adapter to the DB. The /ops overview page and the
# adapters grid both invoked it on EVERY GET, so each dashboard load hammered the
# network and the DB. Cache the result process-wide for a short TTL (pattern:
# tools/dashboard/canvas_aggregator.py) so repeat loads are cheap; the first hit
# after expiry re-probes and re-persists.
import time as _time  # noqa: E402

_PROBE_CACHE: dict[str, tuple[float, list[dict]]] = {}
_PROBE_TTL = 60.0  # seconds


def probe_all_cached(persist: bool = True, ttl: float = _PROBE_TTL) -> list[dict]:
    """Return ``probe_all(persist)`` results, cached for *ttl* seconds.

    Keyed on ``persist`` so a caller that wants a non-persisting probe never
    receives a persisted-cache entry (and vice-versa).
    """
    key = f"persist={persist}"
    entry = _PROBE_CACHE.get(key)
    now = _time.monotonic()
    if entry is not None and (now - entry[0]) < ttl:
        return entry[1]
    results = probe_all(persist=persist)
    _PROBE_CACHE[key] = (now, results)
    return results


def invalidate_probe_cache() -> None:
    """Clear the probe_all TTL cache (e.g. after a forced adapter re-probe)."""
    _PROBE_CACHE.clear()


def _persist_health(name: str, health: dict, now: str) -> None:
    """Write adapter health to ohc_adapter_status and ohc_adapter_health_log."""
    try:
        from tools.ops_hub.db.init_db import get_connection
        conn = get_connection()
        conn.execute("""
            INSERT INTO ohc_adapter_status
                (id, adapter_name, adapter_type, domain, available, version,
                 probe_result, last_probe, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(adapter_name) DO UPDATE SET
                available    = excluded.available,
                adapter_type = excluded.adapter_type,
                domain       = excluded.domain,
                version      = excluded.version,
                probe_result = excluded.probe_result,
                last_probe   = excluded.last_probe,
                updated_at   = excluded.updated_at
        """, (
            str(uuid.uuid4()),
            name,
            health.get("adapter_type", "unknown"),
            health.get("domain", "unknown"),
            1 if health.get("available") else 0,
            health.get("version", ""),
            json.dumps(health.get("details", {})),
            now,
            now,
        ))
        conn.execute("""
            INSERT INTO ohc_adapter_health_log
                (id, adapter_name, status, latency_ms, error_msg, checked_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            str(uuid.uuid4()),
            name,
            "healthy" if health.get("available") else "unavailable",
            health.get("latency_ms", 0),
            health.get("error", ""),
            now,
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass  # Never crash the probe loop due to DB errors


def probe_oss(persist: bool = True) -> list[dict]:
    """Health-probe the 6 OSS adapters only (mlflow, evidently, langfuse, prometheus, onnx, dvc)."""
    results = []
    now = datetime.now(timezone.utc).isoformat()

    for name in OSS_ADAPTERS:
        adapter = get_adapter(name)
        if adapter is None:
            health_dict = {
                "available": False,
                "adapter_name": name,
                "adapter_type": "oss",
                "domain": "unknown",
                "version": "",
                "latency_ms": 0,
                "error": "adapter class failed to load",
                "details": {},
            }
        else:
            try:
                health = adapter.health_check()
                health_dict = health.to_dict()
            except Exception as exc:
                health_dict = {
                    "available": False,
                    "adapter_name": name,
                    "adapter_type": "oss",
                    "domain": getattr(adapter, "DOMAIN", "unknown"),
                    "version": "",
                    "latency_ms": 0,
                    "error": str(exc),
                    "details": {},
                }

        results.append(health_dict)

        if persist:
            _persist_health(name, health_dict, now)

    return results


def available_adapters() -> list[str]:
    """Return names of adapters that are currently available."""
    return [
        name for name in _ADAPTER_MAP
        if (a := get_adapter(name)) is not None and a.available()
    ]
