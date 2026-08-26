# CUI // SP-CTI
"""OHC — Adapter Registry.

Discovers, loads, and health-probes all OHC adapters (OSS + CSP).
Results are persisted to ohc_adapter_status and ohc_adapter_health_log.
"""

from __future__ import annotations

import importlib
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as _futures_wait
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.ops_hub.adapter_registry")

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


# ── Bounded, concurrent probing (qa-fail-c614b0be02b65e98) ───────────────────
# A health probe is a network round-trip, and the round-trips that cost the MOST
# are the ones to adapters that are ABSENT -- an absent service is a connection
# timeout, not a refusal. Probing serially therefore made the /ops overview cost
# the SUM of every adapter's failure, i.e. the page was slowest exactly when it
# had the least to show. Measured on the live dashboard 2026-08-25: `GET /ops`
# 8.6s cold against 0.25-0.46s for every sibling /ops/* route, and 9.3s of the
# 11.34s isolated probe was two adapters not deployed on this host (`sagemaker`
# 5.17s on a rejected AWS token, `prometheus` 4.14s on an unreachable
# localhost:9090). Playwright allows 10s, so the page timed out intermittently.
#
# Fan the probes out so a sweep costs about the SLOWEST one rather than the sum,
# and cap the whole fan-out so one adapter that never answers cannot hold the
# page. The cap is a WALL-CLOCK BUDGET for the sweep; because the probes run
# concurrently it bounds each one too.

_PROBE_TIMEOUT = float(os.environ.get("ICDEV_OHC_PROBE_TIMEOUT", "5.0"))
_PROBE_MAX_WORKERS = 12

# `available` answers "did the adapter say yes". `probe_state` answers "how did
# we come to that conclusion", and the two are NOT the same question. Keeping
# them apart is the point: a probe we STOPPED WAITING FOR must never read the
# same as one that completed and found the service down, and neither is the same
# as an adapter whose class would not import. One field cannot say all three.
PROBE_OK = "ok"                    # probe completed; adapter is available
PROBE_UNAVAILABLE = "unavailable"  # probe completed; adapter reported not available
PROBE_ERROR = "error"              # the probe itself raised
PROBE_TIMEOUT = "timeout"          # we stopped waiting -- NOT a verdict on the service
PROBE_UNLOADABLE = "unloadable"    # the adapter class failed to import


def _probe_names(names: list[str], persist: bool, timeout: float | None,
                 fallback_type: str) -> list[dict]:
    """Concurrently health-probe *names*, bounded by a wall-clock budget.

    Results are returned in *names* order, and persistence runs serially on the
    calling thread -- the probes fan out, the DB writes do not.
    """
    budget = _PROBE_TIMEOUT if timeout is None else float(timeout)
    now = datetime.now(timezone.utc).isoformat()

    # Load on the calling thread. Importing an adapter module is not the slow
    # part (it is cached in _loaded after the first call), and concurrent first
    # imports are a hazard the fan-out has no reason to take on.
    adapters = {name: get_adapter(name) for name in names}

    def _fallback(name: str, state: str, error: str) -> dict:
        adapter = adapters.get(name)
        return {
            "available": False,
            "adapter_name": name,
            "adapter_type": getattr(adapter, "ADAPTER_TYPE", fallback_type),
            "domain": getattr(adapter, "DOMAIN", "unknown"),
            "version": "",
            "latency_ms": 0,
            "error": error,
            "details": {},
            "probe_state": state,
        }

    by_name: dict[str, dict] = {
        name: _fallback(name, PROBE_UNLOADABLE, "adapter class failed to load")
        for name in names if adapters.get(name) is None
    }
    live = [name for name in names if adapters.get(name) is not None]

    if live:
        executor = ThreadPoolExecutor(
            max_workers=min(len(live), _PROBE_MAX_WORKERS),
            thread_name_prefix="ohc-probe",
        )
        try:
            futures = {executor.submit(adapters[n].health_check): n for n in live}
            done, not_done = _futures_wait(futures, timeout=budget)

            for fut in done:
                name = futures[fut]
                try:
                    health_dict = dict(fut.result().to_dict())
                    health_dict.setdefault("adapter_name", name)
                    health_dict["probe_state"] = (
                        PROBE_OK if health_dict.get("available") else PROBE_UNAVAILABLE
                    )
                except Exception as exc:
                    health_dict = _fallback(name, PROBE_ERROR, str(exc))
                by_name[name] = health_dict

            for fut in not_done:
                name = futures[fut]
                logger.warning(
                    "adapter %s did not answer within the %.1fs probe budget", name, budget
                )
                by_name[name] = _fallback(
                    name, PROBE_TIMEOUT,
                    f"health probe timed out after {budget:.1f}s — no answer, "
                    f"which is not a verdict on the service",
                )
        finally:
            # Never wait on the way out: a probe that already blew the budget
            # must not block the caller a second time in shutdown().
            executor.shutdown(wait=False)

    results = [by_name[name] for name in names]

    if persist:
        for name, health_dict in zip(names, results):
            _persist_health(name, health_dict, now)

    return results


def probe_all(persist: bool = True, timeout: float | None = None) -> list[dict]:
    """Health-probe every adapter. Optionally persist results to DB."""
    return _probe_names(list(_ADAPTER_MAP), persist, timeout, fallback_type="unknown")


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
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # Never crash the probe loop due to DB errors
        logger.warning("_persist_health: best-effort INSERT into ohc_adapter_status failed (non-blocking): %s", exc)


def probe_oss(persist: bool = True, timeout: float | None = None) -> list[dict]:
    """Health-probe the 6 OSS adapters only (mlflow, evidently, langfuse, prometheus, onnx, dvc)."""
    return _probe_names(list(OSS_ADAPTERS), persist, timeout, fallback_type="oss")


def available_adapters() -> list[str]:
    """Return names of adapters that are currently available."""
    return [
        name for name in _ADAPTER_MAP
        if (a := get_adapter(name)) is not None and a.available()
    ]
