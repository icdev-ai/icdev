# CUI // SP-CTI
"""OHC — Ops Aggregator.

Computes a cross-domain health score (0-100) for the /ops overview page.
Combines LLMOps + MLOps + AIOps summaries with adapter health.
"""

from __future__ import annotations

from typing import Any


def get_ops_overview() -> dict[str, Any]:
    """Full ops overview for the /ops dashboard page.

    Returns:
        health_score (0-100), per-domain summaries, adapter grid, top alerts
    """
    from tools.ops_hub.llmops_engine import get_llmops_summary
    from tools.ops_hub.mlops_engine import get_mlops_summary
    from tools.ops_hub.aiops_engine import get_aiops_summary
    from tools.ops_hub.adapter_registry import probe_all_cached

    llmops = _safe(get_llmops_summary)
    mlops = _safe(get_mlops_summary)
    aiops = _safe(get_aiops_summary)
    # cnr-ops-02: TTL-cached probe so /ops GETs don't re-probe the network + write
    # to the DB on every page load.
    adapters = _safe(lambda: probe_all_cached(persist=True))

    health_score = _compute_health_score(llmops, mlops, aiops, adapters or [])
    top_alerts = _extract_top_alerts(llmops, mlops, aiops)

    return {
        "health_score": health_score,
        "overall_status": _score_to_status(health_score),
        "domains": {
            "llmops": llmops,
            "mlops": mlops,
            "aiops": aiops,
        },
        "adapters": adapters or [],
        "top_alerts": top_alerts,
        "available_adapters": [
            a["adapter_name"] for a in (adapters or []) if a.get("available")
        ],
    }


def _safe(fn) -> Any:
    try:
        return fn()
    except Exception as exc:
        return {"error": str(exc), "status": "unknown"}


def _compute_health_score(llmops: dict, mlops: dict, aiops: dict,
                           adapters: list[dict]) -> int:
    """Score 0-100. Each domain contributes 30 pts; adapters 10 pts."""
    score = 100

    # Domain penalties
    for domain_data in [llmops, mlops, aiops]:
        domain_status = domain_data.get("status", "unknown")
        if domain_status == "critical":
            score -= 25
        elif domain_status == "degraded":
            score -= 10
        elif domain_status == "unknown":
            score -= 5

    # LLMOps-specific: drift + cost anomaly
    if llmops.get("has_drift"):
        score -= 5
    if llmops.get("has_cost_anomaly"):
        score -= 3

    # MLOps-specific: drift failures
    drift_failures = mlops.get("drift_failures", 0)
    score -= min(drift_failures * 2, 10)

    # AIOps-specific
    open_incidents = aiops.get("open_incidents", 0)
    score -= min(open_incidents * 3, 15)
    spof_count = aiops.get("spof_count", 0)
    score -= min(spof_count, 5)

    # Adapter availability bonus/penalty
    if adapters:
        available = sum(1 for a in adapters if a.get("available"))
        total = len(adapters)
        adapter_rate = available / total if total else 0
        if adapter_rate < 0.3:
            score -= 5

    return max(0, min(100, score))


def _score_to_status(score: int) -> str:
    if score >= 80:
        return "healthy"
    if score >= 50:
        return "degraded"
    return "critical"


def _extract_top_alerts(llmops: dict, mlops: dict, aiops: dict) -> list[dict]:
    alerts = []

    if llmops.get("has_drift"):
        alerts.append({"severity": "warning", "domain": "llmops",
                        "message": "LLM quality drift detected"})
    if llmops.get("has_cost_anomaly"):
        alerts.append({"severity": "warning", "domain": "llmops",
                        "message": "Cost anomaly detected — review spend dashboard"})

    drift_failures = mlops.get("drift_failures", 0)
    if drift_failures:
        alerts.append({"severity": "warning", "domain": "mlops",
                        "message": f"{drift_failures} data drift check(s) failed"})

    open_inc = aiops.get("open_incidents", 0)
    if open_inc:
        alerts.append({"severity": "error", "domain": "aiops",
                        "message": f"{open_inc} open incident(s) require attention"})

    spofs = aiops.get("spof_count", 0)
    if spofs:
        alerts.append({"severity": "warning", "domain": "aiops",
                        "message": f"{spofs} single point(s) of failure in agent topology"})

    slos_degraded = aiops.get("degraded_slos", 0)
    if slos_degraded:
        alerts.append({"severity": "error", "domain": "aiops",
                        "message": f"{slos_degraded} SLO(s) burning error budget fast"})

    return alerts[:5]
