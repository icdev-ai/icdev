# CUI // SP-CTI — ODC Observability Digital Twin
"""Observability Design Canvas OTel twin — snapshot, coverage simulation, SLO projection.

Every metric here is DERIVED from the design's persisted ``graph_json`` and its
``od_assessments`` history. The what-if projections (coverage delta, error-budget
burn, MTTR) are clearly-labelled heuristic ESTIMATES — each response carries
``estimate: True`` and a ``basis`` string naming the inputs used, so downstream
surfaces never present them as observed measurements (TRUST invariant).

Read/write both go through the canvas connection (``db.init_db.get_connection``)
so the snapshot write DB and the twin page read DB are the same database.
"""
from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone

from tools.observability_canvas.observability_engine import (
    compute_coverage_score,
    compute_mitre_detection_coverage,
)

from tools.logging.icdev_logger import get_logger
logger = get_logger("icdev.observability_canvas.twin")

# ── Heuristic constants (planning ESTIMATES, NOT observed telemetry) ──────────
# These translate a modeled *coverage delta* into rough error-budget / MTTR
# figures for what-if planning. They are engineering heuristics, not measured
# SLO burn. They are surfaced only alongside estimate=True + a basis string.
_BASELINE_BURN_PCT = 10.0            # nominal error-budget burn assumed at full coverage
_BURN_PCT_PER_COVERAGE_POINT = 0.6  # burn % added per 1 percentage-point of coverage lost
_MTTR_MIN_PER_COVERAGE_POINT = 0.4  # MTTR minutes added per 1 percentage-point of coverage lost
_SIGNAL_ADD_NUDGE = 0.05            # per-signal coverage bump per added receiver
_SIGNAL_REMOVE_NUDGE = 0.08         # per-signal coverage penalty per removed exporter


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_conn():
    """Canvas connection — same DB the twin page reads from (init_db.get_connection)."""
    from tools.observability_canvas.db.init_db import get_connection
    return get_connection()


def _load_design(design_id: str) -> tuple[str | None, dict | None]:
    """Return (name, parsed_graph) for a design, or (None, None) if it does not exist."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT name, graph_json FROM observability_designs WHERE id=%s", (design_id,)
    ).fetchone()
    if not row:
        return None, None
    try:
        graph = json.loads(row["graph_json"] or "{}")
    except (ValueError, TypeError):
        graph = {"nodes": [], "edges": []}
    if not isinstance(graph, dict):
        graph = {"nodes": [], "edges": []}
    return row["name"], graph


def _count_services(graph: dict) -> int:
    """Count service/component nodes in the design graph.

    Every node in an observability design graph is a component of the telemetry
    topology (sources, collectors, platforms, automation, compliance). The twin
    treats that node population as its service/component count — a truthful count
    of the design's actual content, not a phantom ``observability_nodes`` query.
    """
    if not isinstance(graph, dict):
        return 0
    nodes = graph.get("nodes", [])
    return len(nodes) if isinstance(nodes, list) else 0


def _latest_assessment_score(design_id: str) -> float | None:
    """Return the score of the most recent od_assessments row, or None if none exist."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT score FROM od_assessments WHERE design_id=%s ORDER BY created_at DESC LIMIT 1",
        (design_id,),
    ).fetchone()
    if not row:
        return None
    score = row["score"]
    return float(score) if score is not None else None


def take_snapshot(design_id: str, label: str | None = None) -> dict:
    """Persist a telemetry snapshot for a design.

    service_count is derived from the design graph; coverage_score comes from the
    design's most recent assessment (``od_assessments.score``) when present, else
    0.0 with a ``coverage_basis`` of ``"no_assessment"``.

    Raises:
        ValueError: if the design does not exist.
        Exception:  if the INSERT fails — propagated so the route returns an error
                    status instead of a fake 201 (no bare ``except: pass``).
    """
    _name, graph = _load_design(design_id)
    if graph is None:
        raise ValueError(f"observability design not found: {design_id}")

    service_count = _count_services(graph)
    score = _latest_assessment_score(design_id)
    if score is None:
        coverage_score = 0.0
        coverage_basis = "no_assessment"
    else:
        coverage_score = round(score, 2)
        coverage_basis = "od_assessments.score"

    snap_id = str(uuid.uuid4())
    taken_at = _now()
    label = label or f"snap-{taken_at[:10]}"
    payload = {
        "id": snap_id,
        "design_id": design_id,
        "label": label,
        "service_count": service_count,
        "coverage_score": coverage_score,
        "coverage_basis": coverage_basis,
        "created_at": taken_at,
    }

    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO odc_twin_snapshots "
            "(id, design_id, label, service_count, coverage_score, coverage_basis, payload_json, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (snap_id, design_id, label, service_count, coverage_score,
             coverage_basis, json.dumps(payload), taken_at),
        )
        conn.commit()
    except Exception as exc:
        logger.error("twin snapshot persist failed for design %s: %s", design_id, exc)
        raise
    return payload


def list_snapshots(design_id: str, limit: int = 20) -> list[dict]:
    """Return persisted snapshots for a design, newest first — the twin page read path."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, design_id, label, service_count, coverage_score, coverage_basis, created_at "
        "FROM odc_twin_snapshots WHERE design_id=%s ORDER BY created_at DESC LIMIT %s",
        (design_id, limit),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "design_id": r["design_id"],
            "label": r["label"],
            "service_count": r["service_count"],
            "coverage_score": r["coverage_score"],
            "coverage_basis": r["coverage_basis"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def _apply_delta(graph: dict, add_receivers: list, remove_exporters: list,
                 new_services: dict) -> dict:
    """Apply a collector delta to a copy of the parsed design graph.

    - remove_exporters: drop nodes whose id or label matches (case-insensitive).
    - add_receivers / services: append instrumented source nodes.
    """
    g = copy.deepcopy(graph) if isinstance(graph, dict) else {"nodes": [], "edges": []}
    nodes = g.setdefault("nodes", [])
    if not isinstance(nodes, list):
        nodes = []
        g["nodes"] = nodes

    rm = {str(x).lower() for x in remove_exporters}
    if rm:
        nodes[:] = [
            n for n in nodes
            if str(n.get("id", "")).lower() not in rm
            and str(n.get("label", "")).lower() not in rm
        ]

    for r in add_receivers:
        nodes.append({"id": f"add-{r}", "type": "src-app-log", "label": str(r)})
    for svc_name in new_services:
        nodes.append({"id": f"svc-{svc_name}", "type": "src-app-log", "label": str(svc_name)})
    return g


def _signal_coverage(base_fraction: float, new_services: dict,
                     add_receivers: list, remove_exporters: list) -> dict:
    """Estimate per-signal coverage from the design baseline nudged by the delta."""
    base = max(0.0, min(1.0, base_fraction))
    nudge = _SIGNAL_ADD_NUDGE * len(add_receivers) - _SIGNAL_REMOVE_NUDGE * len(remove_exporters)
    out = {}
    for sig in ("traces", "metrics", "logs"):
        val = base + nudge
        flags = [bool(s.get(sig)) for s in new_services.values() if isinstance(s, dict)]
        if flags:
            declared_fraction = sum(flags) / len(flags)
            val = (val + declared_fraction) / 2.0
        out[sig] = round(max(0.0, min(1.0, val)), 3)
    return out


def simulate_delta(design_id: str, collector_delta: dict, signals: list | None = None,
                   baseline_snap_id: str | None = None) -> dict:
    """Simulate an OTel collector change and project coverage impact.

    Loads the design graph, computes baseline coverage (compute_coverage_score +
    MITRE detection coverage), applies the add/remove/services delta to the parsed
    graph, and recomputes. All figures are heuristic ESTIMATES (estimate=True).
    """
    collector_delta = collector_delta or {}
    signals = signals or ["all"]
    sim_id = str(uuid.uuid4())

    _name, graph = _load_design(design_id)
    graph = graph or {"nodes": [], "edges": []}

    add_receivers = list(collector_delta.get("add_receivers", []) or [])
    remove_exporters = list(collector_delta.get("remove_exporters", []) or [])
    new_services = dict(collector_delta.get("services", {}) or {})

    base = compute_coverage_score(graph)
    base_fraction = base.get("coverage_pct", 0.0) / 100.0
    mitre = compute_mitre_detection_coverage(graph)

    proj_graph = _apply_delta(graph, add_receivers, remove_exporters, new_services)
    proj = compute_coverage_score(proj_graph)
    proj_fraction = proj.get("coverage_pct", 0.0) / 100.0

    base_services = _count_services(graph)
    covered = base_services + len(add_receivers) + len(new_services)
    blind = len(remove_exporters)
    partial = 0

    signal_coverage = _signal_coverage(proj_fraction, new_services, add_receivers, remove_exporters)

    gaps = [
        {"severity": "high", "id": exp, "title": f"Exporter removed: {exp}",
         "recommendation": "Ensure a replacement exporter is configured before removing this one"}
        for exp in remove_exporters
    ]
    verdict = "pass" if not gaps else ("warn" if len(gaps) <= 2 else "fail")

    return {
        "simulation_id": sim_id,
        "design_id": design_id,
        "estimate": True,
        "basis": (
            f"design graph_json via compute_coverage_score ({len(graph.get('nodes', []))} nodes, "
            f"{base.get('coverage_pct', 0.0)}% baseline), MITRE detection coverage "
            f"({mitre.get('coverage_pct', 0.0)}%), and the requested collector delta "
            f"(+{len(add_receivers)} receivers, -{len(remove_exporters)} exporters, "
            f"{len(new_services)} services)"
        ),
        "verdict": verdict,
        "baseline_coverage_pct": round(base_fraction * 100, 1),
        "projected_coverage_pct": round(proj_fraction * 100, 1),
        "mitre_coverage_pct": mitre.get("coverage_pct", 0.0),
        "signal_coverage": signal_coverage,
        "service_coverage": {"covered": covered, "blind": blind, "partial": partial},
        "alert_delta": {"gained": len(add_receivers), "lost": len(remove_exporters)},
        "slo_impact": "Low" if not gaps else ("High" if len(gaps) > 2 else "Medium"),
        "gaps": gaps,
    }


def slo_projection(design_id: str, collector_delta: dict | None = None,
                   baseline_snap_id: str | None = None) -> dict:
    """Project error-budget burn-rate change from a collector delta.

    Burn is modeled from the design's *actual* baseline coverage (less visibility
    => faster assumed burn) and the coverage delta the change induces. The burn /
    MTTR figures use the documented module-level heuristic constants and are
    surfaced as ESTIMATES (estimate=True), never as measured SLO telemetry.
    """
    collector_delta = collector_delta or {}

    _name, graph = _load_design(design_id)
    graph = graph or {"nodes": [], "edges": []}

    add_receivers = list(collector_delta.get("add_receivers", []) or [])
    remove_exporters = list(collector_delta.get("remove_exporters", []) or [])
    new_services = dict(collector_delta.get("services", {}) or {})

    base = compute_coverage_score(graph)
    base_fraction = base.get("coverage_pct", 0.0) / 100.0
    proj_graph = _apply_delta(graph, add_receivers, remove_exporters, new_services)
    proj = compute_coverage_score(proj_graph)
    proj_fraction = proj.get("coverage_pct", 0.0) / 100.0

    # Burn scales inversely with coverage: the less you can observe, the faster the
    # (assumed) error budget burns. Heuristic — see module constants.
    current_burn = _BASELINE_BURN_PCT + (1.0 - base_fraction) * 100.0 * _BURN_PCT_PER_COVERAGE_POINT
    projected_burn = _BASELINE_BURN_PCT + (1.0 - proj_fraction) * 100.0 * _BURN_PCT_PER_COVERAGE_POINT
    coverage_delta_points = (base_fraction - proj_fraction) * 100.0
    mttr_delta = coverage_delta_points * _MTTR_MIN_PER_COVERAGE_POINT

    return {
        "estimate": True,
        "basis": (
            f"design graph_json coverage via compute_coverage_score "
            f"({round(base_fraction * 100, 1)}% baseline -> {round(proj_fraction * 100, 1)}% projected) "
            f"and the requested collector delta; burn/MTTR via documented heuristics"
        ),
        "baseline_coverage_pct": round(base_fraction * 100, 1),
        "projected_coverage_pct": round(proj_fraction * 100, 1),
        "current_burn_pct": round(max(current_burn, 0.0), 1),
        "projected_burn_pct": round(max(projected_burn, 0.0), 1),
        "mttr_delta_min": round(mttr_delta, 1),
    }
