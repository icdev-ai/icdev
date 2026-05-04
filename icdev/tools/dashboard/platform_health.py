# CUI // SP-CTI
"""Platform Health — aggregate health scoring across ICDEV™ subsystems.

Each domain reports a 0-100 score from a deterministic DB-backed check. The
composite is the unweighted mean. Health bands:
    >= 90  → healthy
    >= 70  → degraded
    <  70  → critical

Results are cached in-process for 60 seconds. `_invalidate_cache()` forces a
recompute (the dashboard hits this when the user passes `?invalidate=1`).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from tools.db.storage import get_connection

_CACHE: Dict[str, Any] = {}
_CACHE_TTL_SECONDS = 60


def _invalidate_cache() -> None:
    """Clear cached health results."""
    _CACHE.clear()


def _bucket(score: float) -> str:
    score = max(0.0, min(100.0, score))
    if score >= 90:
        return "healthy"
    if score >= 70:
        return "degraded"
    return "critical"


def _clamp(score: float) -> float:
    return round(max(0.0, min(100.0, score)), 1)


def _rollback(conn) -> None:
    """Roll back any failed transaction so subsequent queries succeed.

    Postgres aborts the whole transaction on the first error and rejects
    every following statement until ROLLBACK. SQLite is forgiving but a
    rollback is harmless.
    """
    try:
        conn.rollback()
    except Exception:
        pass


def _safe_count(conn, sql: str, params: tuple = ()) -> Optional[int]:
    """Return COUNT(*) for a query; None if the table is missing or query fails."""
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception:
        _rollback(conn)
        return None
    if row is None:
        return 0
    if isinstance(row, dict):
        try:
            return int(next(iter(row.values())) or 0)
        except (StopIteration, TypeError, ValueError):
            return None
    try:
        return int(row[0] or 0)
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _present(value: Optional[int]) -> bool:
    return value is not None


def _findings_for_missing(name: str) -> Dict[str, str]:
    return {"severity": "info", "msg": f"{name}: table not present in this backend"}


def _domain_database(conn) -> Dict[str, Any]:
    findings = []
    # Liveness: any working SELECT proves DB connectivity. Use a known-present
    # table — agents is the most stable across both SQLite and Postgres.
    probe = _safe_count(conn, "SELECT COUNT(*) FROM agents")
    if probe is None:
        findings.append({"severity": "critical", "msg": "Cannot SELECT from agents table"})
        score = 0.0
    else:
        score = 100.0
    return {"score": _clamp(score), "status": _bucket(score), "findings": findings,
            "checks": [{"name": "select_probe", "value": probe if probe is not None else "fail"}]}


def _domain_agents(conn) -> Dict[str, Any]:
    findings = []
    active = _safe_count(conn, "SELECT COUNT(*) FROM agents WHERE status='active'")
    total = _safe_count(conn, "SELECT COUNT(*) FROM agents")
    if not _present(total) or total == 0:
        score = 50.0
        findings.append({"severity": "warn", "msg": "No agents registered"})
    else:
        active_n = active or 0
        score = 100.0 * max(active_n, 0) / max(total, 1)
        if active_n < total:
            findings.append({"severity": "warn", "msg": f"{total - active_n}/{total} agents inactive"})
    return {"score": _clamp(score), "status": _bucket(score), "findings": findings,
            "checks": [{"name": "active_agents", "value": active or 0},
                       {"name": "total_agents", "value": total or 0}]}


def _domain_compliance(conn) -> Dict[str, Any]:
    findings = []
    plans = _safe_count(conn, "SELECT COUNT(*) FROM ai_oversight_plans")
    appeals_open = _safe_count(
        conn, "SELECT COUNT(*) FROM ai_accountability_appeals WHERE appeal_status='submitted'"
    )
    score = 100.0
    if _present(appeals_open) and appeals_open > 0:
        score = 100.0 - appeals_open * 5.0
        findings.append({"severity": "warn", "msg": f"{appeals_open} appeal(s) pending"})
    if not _present(plans):
        findings.append(_findings_for_missing("ai_oversight_plans"))
    return {"score": _clamp(score), "status": _bucket(score), "findings": findings,
            "checks": [{"name": "oversight_plans", "value": plans if _present(plans) else "n/a"},
                       {"name": "appeals_open", "value": appeals_open if _present(appeals_open) else "n/a"}]}


def _domain_security(conn) -> Dict[str, Any]:
    findings = []
    crit = _safe_count(
        conn, "SELECT COUNT(*) FROM security_scan_results WHERE severity='critical'"
    )
    high = _safe_count(
        conn, "SELECT COUNT(*) FROM security_scan_results WHERE severity='high'"
    )
    if not _present(crit) and not _present(high):
        findings.append(_findings_for_missing("security_scan_results"))
        score = 90.0  # presumed clean
    else:
        score = 100.0 - ((crit or 0) * 10.0 + (high or 0) * 2.0)
        if (crit or 0) > 0:
            findings.append({"severity": "critical", "msg": f"{crit} critical vulns"})
        if (high or 0) > 0:
            findings.append({"severity": "high", "msg": f"{high} high vulns"})
    return {"score": _clamp(score), "status": _bucket(score), "findings": findings,
            "checks": [{"name": "critical_vulns", "value": crit if _present(crit) else "n/a"},
                       {"name": "high_vulns", "value": high if _present(high) else "n/a"}]}


def _domain_infrastructure(conn) -> Dict[str, Any]:
    findings = []
    deploys = _safe_count(conn, "SELECT COUNT(*) FROM deploy_history")
    if not _present(deploys):
        findings.append(_findings_for_missing("deploy_history"))
        score = 80.0
    else:
        score = 100.0
    return {"score": _clamp(score), "status": _bucket(score), "findings": findings,
            "checks": [{"name": "deploy_history_rows", "value": deploys if _present(deploys) else "n/a"}]}


def _domain_canvases(conn) -> Dict[str, Any]:
    """Canvases live in their own per-canvas SQLite DBs (network_canvas.db,
    qdc_canvas.db, etc.), not the main backend. Probe the canvas-specific
    DB files on disk; absence is unhealthy, presence is healthy.
    """
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    canvas_dbs = {
        "network_canvas": project_root / "data" / "network_canvas.db",
        "qdc_canvas": project_root / "data" / "qdc_canvas.db",
        "security_canvas": project_root / "data" / "security_canvas.db",
        "boundary_canvas": project_root / "data" / "boundary_canvas.db",
        "data_canvas": project_root / "data" / "data_canvas.db",
    }
    findings = []
    checks = []
    present = 0
    for name, path in canvas_dbs.items():
        exists = path.exists()
        checks.append({"name": name, "value": "present" if exists else "missing"})
        if exists:
            present += 1
        else:
            findings.append({"severity": "info", "msg": f"{name} db not initialized"})
    score = 100.0 * present / len(canvas_dbs)
    return {"score": _clamp(score), "status": _bucket(score),
            "findings": findings, "checks": checks}


def _domain_llm(conn) -> Dict[str, Any]:
    findings = []
    audit = _safe_count(conn, "SELECT COUNT(*) FROM llm_gateway_audit")
    drift = _safe_count(conn, "SELECT COUNT(*) FROM model_drift_events")
    if not _present(audit) and not _present(drift):
        findings.append(_findings_for_missing("llm_gateway_audit/model_drift_events"))
        score = 90.0
    else:
        score = 100.0 - (drift or 0) * 5.0
        if (drift or 0) > 0:
            findings.append({"severity": "warn", "msg": f"{drift} model drift event(s)"})
    return {"score": _clamp(score), "status": _bucket(score), "findings": findings,
            "checks": [{"name": "llm_audit_rows", "value": audit if _present(audit) else "n/a"},
                       {"name": "drift_events", "value": drift if _present(drift) else "n/a"}]}


def _domain_monitoring(conn) -> Dict[str, Any]:
    findings = []
    firing = _safe_count(conn, "SELECT COUNT(*) FROM alerts WHERE status='firing'")
    if not _present(firing):
        findings.append(_findings_for_missing("alerts"))
        score = 90.0
    else:
        score = 100.0 - firing * 10.0
        if firing > 0:
            findings.append({"severity": "high", "msg": f"{firing} alert(s) firing"})
    return {"score": _clamp(score), "status": _bucket(score), "findings": findings,
            "checks": [{"name": "alerts_firing", "value": firing if _present(firing) else "n/a"}]}


def _domain_ci_cd(conn) -> Dict[str, Any]:
    findings = []
    runs = _safe_count(conn, "SELECT COUNT(*) FROM gitlab_pipeline_runs")
    if not _present(runs):
        findings.append(_findings_for_missing("gitlab_pipeline_runs"))
        score = 80.0
    else:
        score = 100.0
    return {"score": _clamp(score), "status": _bucket(score), "findings": findings,
            "checks": [{"name": "pipeline_runs", "value": runs if _present(runs) else "n/a"}]}


def _domain_marketplace(conn) -> Dict[str, Any]:
    findings = []
    versions = _safe_count(conn, "SELECT COUNT(*) FROM marketplace_versions")
    if not _present(versions):
        findings.append(_findings_for_missing("marketplace_versions"))
        score = 80.0
    else:
        score = 100.0
    return {"score": _clamp(score), "status": _bucket(score), "findings": findings,
            "checks": [{"name": "marketplace_versions", "value": versions if _present(versions) else "n/a"}]}


_DOMAINS: Dict[str, Callable[[Any], Dict[str, Any]]] = {
    "database": _domain_database,
    "agents": _domain_agents,
    "compliance": _domain_compliance,
    "security": _domain_security,
    "infrastructure": _domain_infrastructure,
    "canvases": _domain_canvases,
    "llm": _domain_llm,
    "monitoring": _domain_monitoring,
    "ci_cd": _domain_ci_cd,
    "marketplace": _domain_marketplace,
}


def get_platform_health() -> Dict[str, Any]:
    """Compute overall platform health score across all domains.

    Each domain contributes a deterministic DB-backed score. Cached for 60s.
    """
    cached = _CACHE.get("platform_health")
    if cached and time.time() - cached["_ts"] < _CACHE_TTL_SECONDS:
        return cached["data"]

    now = datetime.now(timezone.utc).isoformat()
    domains: Dict[str, Any] = {}
    conn = get_connection()
    try:
        for name, fn in _DOMAINS.items():
            domains[name] = fn(conn)
    finally:
        conn.close()

    scores = [d["score"] for d in domains.values() if d.get("score") is not None]
    composite = round(sum(scores) / max(len(scores), 1), 1)
    result = {
        "composite_score": composite,
        "composite_status": _bucket(composite),
        "cached_at": now,
        "checked_at": now,
        "domains": domains,
    }
    _CACHE["platform_health"] = {"_ts": time.time(), "data": result}
    return result


def get_domain_health(domain: str) -> Dict[str, Any]:
    """Get health for a specific domain — fresh DB query (no cache)."""
    fn = _DOMAINS.get(domain)
    if not fn:
        return {
            "domain": domain,
            "score": 0.0,
            "status": "unknown",
            "checks": [],
            "findings": [{"severity": "warn", "msg": f"Unknown domain: {domain}"}],
        }
    conn = get_connection()
    try:
        detail = fn(conn)
    finally:
        conn.close()
    return {"domain": domain, **detail}
