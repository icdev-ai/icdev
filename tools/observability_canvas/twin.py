# CUI // SP-CTI — ODC Observability Digital Twin
"""Observability Design Canvas OTel twin — snapshot, coverage simulation, SLO projection."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from tools.db.storage import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def take_snapshot(design_id: str, label: str | None = None) -> dict:
    """Freeze OTel collector topology and instrumentation coverage into odc_twin_snapshots."""
    conn = get_connection()
    snap_id = str(uuid.uuid4())
    taken_at = _now()
    label = label or f"snap-{taken_at[:10]}"
    try:
        service_count = conn.execute(
            "SELECT COUNT(*) FROM observability_nodes WHERE design_id=%s AND node_type='service'", (design_id,)
        ).fetchone()[0]
    except Exception:
        service_count = 0
    coverage_score = 0.0
    try:
        conn.execute(
            "INSERT INTO odc_twin_snapshots (id, design_id, label, service_count, coverage_score, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
            (snap_id, design_id, label, service_count, coverage_score, taken_at),
        )
        conn.commit()
    except Exception:
        pass
    return {"id": snap_id, "design_id": design_id, "label": label,
            "service_count": service_count, "coverage_score": coverage_score, "created_at": taken_at}


def simulate_delta(design_id: str, collector_delta: dict, signals: list | None = None,
                   baseline_snap_id: str | None = None) -> dict:
    """Simulate OTel collector config change and project coverage impact."""
    sim_id = str(uuid.uuid4())
    signals = signals or ["all"]
    add_receivers = collector_delta.get("add_receivers", [])
    remove_exporters = collector_delta.get("remove_exporters", [])
    new_services = collector_delta.get("services", {})

    covered = len(add_receivers) + len(new_services)
    blind = max(0, len(remove_exporters) - len(add_receivers))
    partial = 0
    total = covered + blind + partial
    cov_score = covered / total if total else 0.5

    gaps = [
        {"severity": "high", "id": exp, "title": f"Exporter removed: {exp}",
         "recommendation": "Ensure replacement exporter is configured before removing this one"}
        for exp in remove_exporters
    ]
    verdict = "pass" if not gaps else ("warn" if len(gaps) <= 2 else "fail")

    signal_coverage = {"traces": min(cov_score + 0.1, 1.0), "metrics": cov_score, "logs": max(cov_score - 0.1, 0.0)}
    alert_delta = {"gained": len(add_receivers), "lost": len(remove_exporters)}

    return {
        "simulation_id": sim_id, "design_id": design_id, "verdict": verdict,
        "signal_coverage": signal_coverage,
        "service_coverage": {"covered": covered, "blind": blind, "partial": partial},
        "alert_delta": alert_delta, "slo_impact": "Low" if not gaps else "Medium",
        "gaps": gaps,
    }


def slo_projection(design_id: str, collector_delta: dict | None = None,
                   baseline_snap_id: str | None = None) -> dict:
    """Project error budget burn rate change from a collector delta."""
    collector_delta = collector_delta or {}
    remove_count = len(collector_delta.get("remove_exporters", []))
    add_count = len(collector_delta.get("add_receivers", []))
    current_burn = 12.0
    projected_burn = max(current_burn + remove_count * 3.5 - add_count * 1.5, 0.0)
    mttr_delta = round(remove_count * 4.0 - add_count * 1.0, 1)
    return {
        "current_burn_pct": round(current_burn, 1),
        "projected_burn_pct": round(projected_burn, 1),
        "mttr_delta_min": mttr_delta,
    }
