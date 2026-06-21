# CUI // SP-CTI
"""OHC — AIOps Engine.

Delegates to Phase 70 tools/sre/* and tools/agent/topology.
Surfaces a unified API for /ops/slos, /ops/incidents, /ops/runbooks,
/ops/topology, and /ops/self-healing.
"""

from __future__ import annotations

from typing import Any


# ── SLOs ──────────────────────────────────────────────────────────────────────

def get_slo_dashboard() -> dict:
    """Return SLO dashboard — all SLOs with current status and burn rate."""
    try:
        from tools.sre.slo_manager import SLOManager
        mgr = SLOManager()
        return mgr.get_dashboard()
    except Exception as exc:
        return {"error": str(exc), "available": False, "slos": []}


def get_slo_details(slo_id: str) -> dict:
    """Return a single SLO with measurement history."""
    try:
        from tools.sre.slo_manager import SLOManager
        mgr = SLOManager()
        return mgr.get_slo(slo_id)
    except Exception as exc:
        return {"error": str(exc)}


def define_slo(service: str, slo_type: str, target: float,
               window_days: int = 30, classification: str = "CUI") -> dict:
    """Create or update a Service-Level Objective."""
    try:
        from tools.sre.slo_manager import SLOManager
        mgr = SLOManager()
        return mgr.define_slo(
            service=service, slo_type=slo_type,
            target=target, window_days=window_days,
            classification=classification,
        )
    except Exception as exc:
        return {"error": str(exc)}


# ── Incidents ─────────────────────────────────────────────────────────────────

def get_incidents(status: str = "", limit: int = 20) -> list[dict]:
    """Return incidents, optionally filtered by status."""
    try:
        from tools.sre.incident_commander import IncidentCommander
        ic = IncidentCommander()
        return ic.list_incidents(status=status, limit=limit)
    except Exception as exc:
        return [{"error": str(exc)}]


def get_incident_dashboard() -> dict:
    """Return incident summary — open count, MTTR trend, severity breakdown."""
    try:
        from tools.sre.incident_commander import IncidentCommander
        ic = IncidentCommander()
        return ic.get_dashboard()
    except Exception as exc:
        return {"error": str(exc), "available": False}


def create_incident(title: str, severity: str, description: str = "",
                    classification: str = "CUI") -> dict:
    """Open a new incident."""
    try:
        from tools.sre.incident_commander import IncidentCommander
        ic = IncidentCommander()
        return ic.create_incident(
            title=title, severity=severity,
            description=description, classification=classification,
        )
    except Exception as exc:
        return {"error": str(exc)}


# ── Runbooks ──────────────────────────────────────────────────────────────────

def get_runbooks(limit: int = 50) -> list[dict]:
    """Return all registered runbooks."""
    try:
        from tools.sre.runbook_executor import RunbookExecutor
        re = RunbookExecutor()
        return re.list_runbooks(limit=limit)
    except Exception as exc:
        return [{"error": str(exc)}]


def get_runbook_executions(runbook_id: str = "", limit: int = 20) -> list[dict]:
    """Return runbook execution history."""
    try:
        from tools.sre.runbook_executor import RunbookExecutor
        re = RunbookExecutor()
        return re.list_executions(runbook_id=runbook_id, limit=limit)
    except Exception as exc:
        return [{"error": str(exc)}]


# ── Agent Topology ────────────────────────────────────────────────────────────

def get_topology() -> dict:
    """Return full agent dependency graph with SPOF analysis."""
    try:
        from tools.agent.topology import AgentTopology
        topo = AgentTopology()
        graph = topo.build()
        spofs = topo.detect_spof(graph)
        return {
            "graph": graph,
            "spofs": spofs,
            "node_count": len(graph.get("nodes", [])),
            "edge_count": len(graph.get("edges", [])),
            "spof_count": len(spofs),
        }
    except Exception as exc:
        return {"error": str(exc), "available": False, "graph": {}, "spofs": []}


def get_airgap_paths() -> dict:
    """Return air-gap-safe execution paths in the topology."""
    try:
        from tools.agent.topology import AgentTopology
        topo = AgentTopology()
        return topo.get_airgap_paths()
    except Exception as exc:
        return {"error": str(exc)}


# ── Self-Healing ──────────────────────────────────────────────────────────────

def get_self_healing_log(limit: int = 30) -> list[dict]:
    """Return auto-resolution history from the self-healing subsystem."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        conn.set_security_context(None)  # rls-bypass: auto_resolution_log lacks tenant_id/classification
        rows = conn.execute("""
            SELECT * FROM auto_resolution_log
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        pass

    # Fallback: check monitor's auto_resolver
    try:
        from tools.monitor.auto_resolver import AutoResolver
        ar = AutoResolver()
        return ar.get_resolution_log(limit=limit)
    except Exception as exc:
        return [{"error": str(exc)}]


def get_self_healing_stats() -> dict:
    """Return self-healing success rate and recent activity."""
    log = get_self_healing_log(limit=100)
    total = len(log)
    successes = sum(1 for r in log if r.get("status") == "success")
    rate = round((successes / total * 100), 1) if total else 0.0
    return {
        "total_resolutions": total,
        "success_rate_pct": rate,
        "recent": log[:5],
    }


# ── Prometheus Adapter (if available) ─────────────────────────────────────────

def get_prometheus_metrics(query: str, step: str = "5m") -> dict:
    """Query Prometheus for a metric expression."""
    try:
        from tools.ops_hub.adapter_registry import get_adapter
        adapter = get_adapter("prometheus")
        if adapter and adapter.available():
            metrics = adapter.get_metrics(query, step=step)
            return metrics.to_dict()
    except Exception:
        pass
    return {"error": "Prometheus adapter unavailable", "values": []}


# ── Unified Summary ───────────────────────────────────────────────────────────

def get_aiops_summary() -> dict[str, Any]:
    """Roll-up for /ops AIOps domain card."""
    slo_data = get_slo_dashboard()
    incident_data = get_incident_dashboard()
    topology_data = get_topology()
    healing_stats = get_self_healing_stats()

    # Count degraded SLOs
    degraded_slos = sum(
        1 for s in slo_data.get("slos", [])
        if s.get("status") in ("error_budget_exhausted", "burning_fast")
    )
    open_incidents = incident_data.get("open_count", 0)
    spof_count = topology_data.get("spof_count", 0)

    status = "healthy"
    if degraded_slos > 0 or open_incidents > 0:
        status = "degraded"
    if open_incidents > 2 or spof_count > 3:
        status = "critical"

    return {
        "status": status,
        "degraded_slos": degraded_slos,
        "open_incidents": open_incidents,
        "spof_count": spof_count,
        "healing_success_rate": healing_stats.get("success_rate_pct", 0),
        "domain": "aiops",
    }
