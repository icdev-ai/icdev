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

import hashlib
import json
import uuid
from datetime import datetime, timezone

logger = get_logger("icdev.pipeline.twin")

# ── Snapshot retention policy (pdx-perf-01) ───────────────────────────────────
# Auto snapshots are taken on EVERY pipeline save (the blueprint auto-save hook
# passes an ``auto-save-<date>`` label) and by the twin simulator (``auto-pre-sim``).
# Left unbounded they grow ~120 identical rows/hour while a canvas is being edited.
# We classify a snapshot as AUTO purely by its label prefix and cap how many auto
# rows a pipeline retains; manual/user-labeled snapshots (any label NOT starting
# with this prefix — including the twin snapshot endpoint's default ``snapshot-``)
# are ALWAYS preserved and never pruned.
AUTO_SNAPSHOT_LABEL_PREFIX = "auto-"
AUTO_SNAPSHOT_RETENTION = 20
_DEFAULT_SNAPSHOT_LIST_LIMIT = 100


def _is_auto_snapshot_label(label) -> bool:
    """True when ``label`` marks an automatically-generated snapshot (prunable)."""
    return bool(label) and str(label).startswith(AUTO_SNAPSHOT_LABEL_PREFIX)


def _graph_fingerprint(raw) -> str:
    """Stable sha256 over a graph_json value, order-insensitive to key ordering.

    Used to detect a no-op snapshot (graph identical to the latest snapshot).
    Falls back to hashing the raw string when the blob is not parseable JSON so
    two byte-identical corrupt blobs still compare equal.
    """
    try:
        obj = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
        canon = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    except (ValueError, TypeError):
        canon = raw if isinstance(raw, str) else json.dumps(raw, sort_keys=True, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


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


def _prune_auto_snapshots(conn, pipeline_id: str, keep: int = AUTO_SNAPSHOT_RETENTION) -> int:
    """Delete AUTO-labeled snapshots for a pipeline beyond the newest ``keep``.

    Runs on the caller's connection/transaction (NOT committed here) so the
    prune commits atomically with the INSERT that triggered it. Only rows whose
    label starts with ``AUTO_SNAPSHOT_LABEL_PREFIX`` are considered — manual /
    user-labeled snapshots are never selected and therefore never deleted.

    NOTE (append-only interaction): ``pdc_snapshots`` is intentionally NOT
    append-only (see the ``APPEND_ONLY_TABLES`` comment in
    ``.claude/hooks/pre_tool_use.py``). Pruning is bounded to auto-labeled rows
    (label starts with ``AUTO_SNAPSHOT_LABEL_PREFIX``, keep=20) — the equivalent
    of log rotation; manual / user-labeled snapshots are never deleted. See the
    task pdx-perf-01 rationale documented on the PR.
    """
    rows = conn.execute(
        "SELECT id FROM pdc_snapshots WHERE pipeline_id=%s AND label LIKE %s "
        "ORDER BY created_at DESC, id DESC",
        (pipeline_id, AUTO_SNAPSHOT_LABEL_PREFIX + "%"),
    ).fetchall()
    stale_ids = [(r["id"] if not isinstance(r, (list, tuple)) else r[0]) for r in rows[keep:]]
    for sid in stale_ids:
        conn.execute("DELETE FROM pdc_snapshots WHERE id=%s", (sid,))
    if stale_ids:
        logger.info(
            "Pruned %d stale auto-snapshots for pipeline %s (retention=%d)",
            len(stale_ids), pipeline_id, keep,
        )
    return len(stale_ids)


def take_snapshot(pipeline_id: str, label: str = None, user_id: str = "system") -> dict:
    """Freeze the current pipeline DAG as a named snapshot.

    Returns the snapshot dict on success, raises on DB error.

    De-duplication (pdx-perf-01): if the current graph is byte-for-byte
    equivalent (by sha256 of the canonical graph_json) to the pipeline's most
    recent snapshot, NO new row is written and the existing latest snapshot is
    returned with ``"skipped": True``. This stops the auto-save timer from
    inserting a stream of identical rows.

    Retention (pdx-perf-01): after inserting an AUTO-labeled snapshot, older auto
    rows beyond ``AUTO_SNAPSHOT_RETENTION`` are pruned in the same transaction.
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
    current_graph_json = row["graph_json"]

    snap_label = label or f"snapshot-{_now()[:10]}"

    # De-dup against the most recent snapshot for this pipeline.
    latest = conn.execute(
        "SELECT id, graph_json, label, node_count, edge_count, created_by, created_at "
        "FROM pdc_snapshots WHERE pipeline_id=%s ORDER BY created_at DESC, id DESC LIMIT %s",
        (pipeline_id, 1),
    ).fetchone()
    if latest is not None and _graph_fingerprint(latest["graph_json"]) == _graph_fingerprint(current_graph_json):
        conn.close()
        logger.info(
            "Snapshot skipped for pipeline %s — graph unchanged since %s",
            pipeline_id, latest["id"],
        )
        return {
            "id": latest["id"],
            "pipeline_id": pipeline_id,
            "label": latest["label"],
            "node_count": latest["node_count"],
            "edge_count": latest["edge_count"],
            "created_by": latest["created_by"],
            "created_at": latest["created_at"],
            "skipped": True,
        }

    snap_id = str(uuid.uuid4())
    created_at = _now()
    conn.execute(
        """INSERT INTO pdc_snapshots
           (id, pipeline_id, label, graph_json, node_count, edge_count, created_by, created_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (snap_id, pipeline_id, snap_label, current_graph_json,
         len(nodes), len(edges), user_id, created_at),
    )
    # Bounded retention for auto snapshots only (manual snapshots are preserved).
    if _is_auto_snapshot_label(snap_label):
        _prune_auto_snapshots(conn, pipeline_id)
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
        "created_at": created_at,
    }


def list_snapshots(pipeline_id: str, limit: int = _DEFAULT_SNAPSHOT_LIST_LIMIT) -> list:
    """Return snapshots for a pipeline, newest first, capped at ``limit`` (default 100)."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = _DEFAULT_SNAPSHOT_LIST_LIMIT
    if limit <= 0:
        limit = _DEFAULT_SNAPSHOT_LIST_LIMIT
    conn = _get_connection()
    rows = conn.execute(
        "SELECT id, pipeline_id, label, node_count, edge_count, created_by, created_at "
        "FROM pdc_snapshots WHERE pipeline_id=%s ORDER BY created_at DESC LIMIT %s",
        (pipeline_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def latest_snapshots_by_pipeline(per_pipeline: int = 2) -> dict:
    """Return the newest ``per_pipeline`` snapshots for EVERY pipeline in one query.

    Replaces the N+1 pattern in the twin dashboard (one ``list_snapshots`` call
    per pipeline). Uses a single windowed query:
        ROW_NUMBER() OVER (PARTITION BY pipeline_id ORDER BY created_at DESC)
    filtered to ``rn <= per_pipeline``. SQLite has supported window functions
    since 3.25 (verified: this env runs 3.50.x) and PostgreSQL supports them
    natively, so one form works on both backends through the StorageConnection
    translator (which only rewrites %s → ? and leaves the window clause intact).

    Returns ``{pipeline_id: [snap_dict, ...]}`` with each list newest-first.
    """
    try:
        per_pipeline = int(per_pipeline)
    except (TypeError, ValueError):
        per_pipeline = 2
    if per_pipeline <= 0:
        per_pipeline = 2
    conn = _get_connection()
    rows = conn.execute(
        "SELECT id, pipeline_id, label, node_count, edge_count, created_by, created_at "
        "FROM ("
        "  SELECT id, pipeline_id, label, node_count, edge_count, created_by, created_at, "
        "         ROW_NUMBER() OVER (PARTITION BY pipeline_id ORDER BY created_at DESC, id DESC) AS rn "
        "  FROM pdc_snapshots"
        ") ranked WHERE rn <= %s ORDER BY pipeline_id, created_at DESC, id DESC",
        (per_pipeline,),
    ).fetchall()
    conn.close()
    out: dict = {}
    for r in rows:
        d = dict(r)
        out.setdefault(d["pipeline_id"], []).append(d)
    return out


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

    sim_id = str(uuid.uuid4())
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
