from __future__ import annotations

from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI — PDC Pipeline Twin Engine
"""Pre-merge what-if simulation for DevSecOps pipelines.

Phase 1: snapshot a pipeline DAG, then run any delta graph through the
existing antipattern detector, SLSA assessor, and compliance engine to
produce a PASS / WARN / FAIL verdict before any change is merged.

Public API
----------
take_snapshot(pipeline_id, label=None, user_id="system") -> snapshot dict
list_snapshots(pipeline_id) -> list[dict]
simulate_delta(pipeline_id, delta_graph, baseline_snap_id=None, user_id="system") -> simulation dict
get_simulation(sim_id) -> dict | None
"""

import json
import uuid
from datetime import datetime, timezone

logger = get_logger("icdev.pipeline.twin")


class CorruptGraphError(ValueError):
    """Raised when a stored graph_json blob cannot be parsed into a graph object.

    A subclass of ValueError so existing ``except ValueError`` callers still catch
    it, while route handlers that want a distinct HTTP 422 (vs the 404 raised for a
    missing pipeline/snapshot) can catch this type explicitly first.
    """


def _parse_graph_json(raw):
    """Parse a stored graph_json value into a dict, or raise CorruptGraphError."""
    try:
        graph = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
    except (ValueError, TypeError):
        raise CorruptGraphError("corrupt graph")
    if not isinstance(graph, dict):
        raise CorruptGraphError("corrupt graph")
    return graph


# ── lazy imports so this module loads even without Flask app context ──────────

def _get_connection():
    from tools.pipeline.db.init_db import get_connection
    return get_connection()


def _now():
    return datetime.now(timezone.utc).isoformat()


# ── Snapshot ──────────────────────────────────────────────────────────────────


def take_snapshot(pipeline_id: str, label: str = None, user_id: str = "system") -> dict:
    """Freeze the current pipeline DAG as a named snapshot.

    Returns the snapshot dict on success, raises on DB error.
    """
    conn = _get_connection()
    row = conn.execute("SELECT graph_json, name FROM pipelines WHERE id=%s", (pipeline_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Pipeline {pipeline_id!r} not found")

    try:
        graph = _parse_graph_json(row["graph_json"])
    except CorruptGraphError:
        conn.close()
        raise
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    snap_id = str(uuid.uuid4())[:12]
    snap_label = label or f"snapshot-{_now()[:10]}"

    conn.execute(
        """INSERT INTO pdc_snapshots
           (id, pipeline_id, label, graph_json, node_count, edge_count, created_by, created_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (snap_id, pipeline_id, snap_label, row["graph_json"],
         len(nodes), len(edges), user_id, _now()),
    )
    conn.commit()
    conn.close()

    logger.info("Snapshot %s taken for pipeline %s (%d nodes)", snap_id, pipeline_id, len(nodes))
    return {
        "id": snap_id,
        "pipeline_id": pipeline_id,
        "label": snap_label,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "created_by": user_id,
        "created_at": _now(),
    }


def list_snapshots(pipeline_id: str) -> list:
    """Return all snapshots for a pipeline, newest first."""
    conn = _get_connection()
    rows = conn.execute(
        "SELECT id, pipeline_id, label, node_count, edge_count, created_by, created_at "
        "FROM pdc_snapshots WHERE pipeline_id=%s ORDER BY created_at DESC",
        (pipeline_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Delta diff ────────────────────────────────────────────────────────────────


def _diff_graphs(baseline_graph: dict, delta_graph: dict) -> dict:
    """Compute added/removed nodes and edges between two graph snapshots."""
    b_nodes = {n["id"]: n for n in baseline_graph.get("nodes", [])}
    d_nodes = {n["id"]: n for n in delta_graph.get("nodes", [])}
    b_edges = {e["id"]: e for e in baseline_graph.get("edges", [])}
    d_edges = {e["id"]: e for e in delta_graph.get("edges", [])}

    added_nodes = [d_nodes[k] for k in d_nodes if k not in b_nodes]
    removed_nodes = [b_nodes[k] for k in b_nodes if k not in d_nodes]
    added_edges = [d_edges[k] for k in d_edges if k not in b_edges]
    removed_edges = [b_edges[k] for k in b_edges if k not in d_edges]

    return {
        "added_nodes": added_nodes,
        "removed_nodes": removed_nodes,
        "added_edges": added_edges,
        "removed_edges": removed_edges,
        "node_delta": len(added_nodes) - len(removed_nodes),
        "edge_delta": len(added_edges) - len(removed_edges),
    }


# ── Verdict logic ─────────────────────────────────────────────────────────────

def _compute_verdict(antipatterns: list, compliance: dict) -> str:
    """FAIL on any critical antipattern or ≥3 failed compliance rules.
    WARN on any high antipattern or ≥1 failed compliance rule.
    PASS otherwise.
    """
    critical = [a for a in antipatterns if a.get("severity") == "critical"]
    high = [a for a in antipatterns if a.get("severity") == "high"]
    failed = compliance.get("failed", 0)

    if critical or failed >= 3:
        return "fail"
    if high or failed >= 1:
        return "warn"
    return "pass"


# ── Simulate ──────────────────────────────────────────────────────────────────


def simulate_delta(
    pipeline_id: str,
    delta_graph: dict,
    baseline_snap_id: str = None,
    user_id: str = "system",
) -> dict:
    """Run a delta graph through antipattern + SLSA + compliance analysis.

    Args:
        pipeline_id: ID of the pipeline being simulated.
        delta_graph: {"nodes": [...], "edges": [...]} representing the proposed change.
        baseline_snap_id: Optional snapshot ID to diff against. If None, takes
            a fresh snapshot of the current pipeline state.
        user_id: Audit identity.

    Returns:
        Simulation result dict with verdict, findings breakdown, and diff.
    """
    from tools.pipeline.antipattern_detector import detect_antipatterns
    from tools.pipeline.blueprint import assess_slsa, run_compliance_check

    conn = _get_connection()

    # Resolve baseline
    if baseline_snap_id:
        snap_row = conn.execute(
            "SELECT graph_json FROM pdc_snapshots WHERE id=%s", (baseline_snap_id,)
        ).fetchone()
        if not snap_row:
            conn.close()
            raise ValueError(f"Snapshot {baseline_snap_id!r} not found")
        try:
            baseline_graph = _parse_graph_json(snap_row["graph_json"])
        except CorruptGraphError:
            conn.close()
            raise
    else:
        pipe_row = conn.execute("SELECT graph_json FROM pipelines WHERE id=%s", (pipeline_id,)).fetchone()
        if not pipe_row:
            conn.close()
            raise ValueError(f"Pipeline {pipeline_id!r} not found")
        try:
            baseline_graph = _parse_graph_json(pipe_row["graph_json"])
        except CorruptGraphError:
            conn.close()
            raise
        # Auto-take a snapshot so the baseline is preserved
        conn.close()
        snap = take_snapshot(pipeline_id, label="auto-pre-sim", user_id=user_id)
        baseline_snap_id = snap["id"]
        conn = _get_connection()

    nodes = delta_graph.get("nodes", [])
    edges = delta_graph.get("edges", [])

    # Run analysis on the delta graph
    antipatterns = detect_antipatterns(nodes, edges)
    slsa = assess_slsa(nodes, edges)
    compliance = run_compliance_check(nodes, edges)
    diff = _diff_graphs(baseline_graph, delta_graph)
    verdict = _compute_verdict(antipatterns, compliance)

    critical_count = len([a for a in antipatterns if a.get("severity") == "critical"])
    high_count = len([a for a in antipatterns if a.get("severity") == "high"])
    medium_count = len([a for a in antipatterns if a.get("severity") == "medium"])

    sim_id = str(uuid.uuid4())[:12]
    conn.execute(
        """INSERT INTO pdc_simulations
           (id, pipeline_id, baseline_snap_id, delta_graph_json,
            verdict, antipatterns_json, slsa_json, compliance_json, diff_json,
            critical_count, high_count, medium_count, created_by, created_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            sim_id, pipeline_id, baseline_snap_id,
            json.dumps(delta_graph),
            verdict,
            json.dumps(antipatterns),
            json.dumps(slsa),
            json.dumps(compliance),
            json.dumps(diff),
            critical_count, high_count, medium_count,
            user_id, _now(),
        ),
    )
    conn.commit()
    conn.close()

    logger.info(
        "Simulation %s for pipeline %s: verdict=%s critical=%d high=%d",
        sim_id, pipeline_id, verdict, critical_count, high_count,
    )

    return {
        "id": sim_id,
        "pipeline_id": pipeline_id,
        "baseline_snap_id": baseline_snap_id,
        "verdict": verdict,
        "antipatterns": antipatterns,
        "slsa": slsa,
        "compliance": compliance,
        "diff": diff,
        "critical_count": critical_count,
        "high_count": high_count,
        "medium_count": medium_count,
        "created_by": user_id,
        "created_at": _now(),
    }


def get_simulation(sim_id: str) -> dict | None:
    """Retrieve a stored simulation result by ID."""
    conn = _get_connection()
    row = conn.execute("SELECT * FROM pdc_simulations WHERE id=%s", (sim_id,)).fetchone()
    conn.close()
    if not row:
        return None
    r = dict(row)
    for key in ("antipatterns_json", "slsa_json", "compliance_json", "diff_json"):
        field = key.replace("_json", "")
        r[field] = json.loads(r.pop(key, "{}") or "{}")
    return r
