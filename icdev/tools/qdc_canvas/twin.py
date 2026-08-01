# CUI // SP-CTI — QDC Quality Design Canvas digital twin
"""Quality Design Canvas twin — snapshot the quality-gate topology and simulate
gate/coverage changes before applying them.

Mirrors the PDC twin hygiene (``tools/pipeline/twin.py``): sha256 snapshot dedup
and bounded auto-snapshot retention — NOT append-only. The verdict reuses the
``qdc_gate_breach`` reflex's data read (latest result per ``(design_id, gate_id)``,
failing = ``status NOT IN ('pass','skip')``) plus the latest UQS score, so the
twin's honesty tracks the real gate engine rather than re-deriving quality.

Public API (mirrors PDC):
    take_snapshot(design_id, label=None, user_id="system") -> dict
    list_snapshots(design_id, limit=100) -> list[dict]
    simulate_delta(design_id, delta_graph, baseline_snap_id=None, user_id="system") -> dict
    get_simulation(sim_id) -> dict | None
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.qdc.twin")

AUTO_SNAPSHOT_LABEL_PREFIX = "auto-"
AUTO_SNAPSHOT_RETENTION = 20
_DEFAULT_LIST_LIMIT = 100


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_connection():
    # QDC's own helper routes to get_canvas_connection() on PG (qdc_* tables have
    # no tenant_id/classification RLS columns).
    from tools.qdc_canvas.db.init_db import get_connection

    return get_connection()


def _graph_fingerprint(raw) -> str:
    try:
        obj = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
        canon = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    except (ValueError, TypeError):
        canon = raw if isinstance(raw, str) else json.dumps(raw, sort_keys=True, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _parse_graph(raw) -> dict:
    try:
        g = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
    except (ValueError, TypeError):
        return {"nodes": [], "edges": []}
    return g if isinstance(g, dict) else {"nodes": [], "edges": []}


def _is_gate_node(node: dict) -> bool:
    t = str(node.get("type", "")).lower()
    return "gate" in t or "quality" in t


def _prune_auto_snapshots(conn, design_id: str, keep: int = AUTO_SNAPSHOT_RETENTION) -> int:
    rows = conn.execute(
        "SELECT id FROM qdc_twin_snapshots WHERE design_id=%s AND label LIKE %s "
        "ORDER BY created_at DESC, id DESC",
        (design_id, AUTO_SNAPSHOT_LABEL_PREFIX + "%"),
    ).fetchall()
    stale = [(r["id"] if not isinstance(r, (list, tuple)) else r[0]) for r in rows[keep:]]
    for sid in stale:
        conn.execute("DELETE FROM qdc_twin_snapshots WHERE id=%s", (sid,))
    return len(stale)


def take_snapshot(design_id: str, label: str = None, user_id: str = "system") -> dict:
    """Freeze the current QDC design graph as a named snapshot (dedup + retention)."""
    conn = _get_connection()
    row = conn.execute("SELECT graph_json FROM qdc_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"QDC design {design_id!r} not found")
    graph_json = row["graph_json"] if not isinstance(row, (list, tuple)) else row[0]
    graph = _parse_graph(graph_json)
    nodes, edges = graph.get("nodes", []), graph.get("edges", [])
    snap_label = label or f"snapshot-{_now()[:10]}"

    latest = conn.execute(
        "SELECT id, graph_json, label, node_count, edge_count, created_by, created_at "
        "FROM qdc_twin_snapshots WHERE design_id=%s ORDER BY created_at DESC, id DESC LIMIT %s",
        (design_id, 1),
    ).fetchone()
    if latest is not None:
        ld = dict(latest)
        if _graph_fingerprint(ld["graph_json"]) == _graph_fingerprint(graph_json):
            conn.close()
            return {**{k: ld[k] for k in ("id", "label", "node_count", "edge_count", "created_by", "created_at")},
                    "design_id": design_id, "skipped": True}

    snap_id = str(uuid.uuid4())
    created_at = _now()
    conn.execute(
        "INSERT INTO qdc_twin_snapshots (id, design_id, label, graph_json, node_count, edge_count, created_by, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (snap_id, design_id, snap_label, graph_json, len(nodes), len(edges), user_id, created_at),
    )
    if snap_label.startswith(AUTO_SNAPSHOT_LABEL_PREFIX):
        _prune_auto_snapshots(conn, design_id)
    conn.commit()
    conn.close()
    logger.info("QDC snapshot %s for design %s (%d nodes)", snap_id, design_id, len(nodes))
    return {"id": snap_id, "design_id": design_id, "label": snap_label,
            "node_count": len(nodes), "edge_count": len(edges),
            "created_by": user_id, "created_at": created_at}


def list_snapshots(design_id: str, limit: int = _DEFAULT_LIST_LIMIT) -> list:
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = _DEFAULT_LIST_LIMIT
    conn = _get_connection()
    rows = conn.execute(
        "SELECT id, design_id, label, node_count, edge_count, created_by, created_at "
        "FROM qdc_twin_snapshots WHERE design_id=%s ORDER BY created_at DESC LIMIT %s",
        (design_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _current_gate_posture(conn, design_id: str) -> dict:
    """Reuse the qdc_gate_breach reflex read: latest result per gate, failing =
    status NOT IN ('pass','skip'). Also fetch the latest UQS score."""
    failing = []
    try:
        rows = conn.execute(
            "SELECT r.gate_id, r.status, r.executed_at FROM qdc_gate_results r "
            "INNER JOIN (SELECT design_id, gate_id, MAX(executed_at) AS max_at "
            "            FROM qdc_gate_results WHERE design_id=%s GROUP BY design_id, gate_id) latest "
            "ON r.design_id=latest.design_id AND r.gate_id=latest.gate_id AND r.executed_at=latest.max_at "
            "WHERE r.design_id=%s AND r.status NOT IN ('pass','skip') ORDER BY r.executed_at DESC",
            (design_id, design_id),
        ).fetchall()
        failing = [dict(r) for r in rows]
    except Exception:  # noqa: BLE001 — table may be empty/absent
        failing = []
    uqs = None
    try:
        r = conn.execute(
            "SELECT uqs_score FROM qdc_uqs_history WHERE design_id=%s ORDER BY computed_at DESC LIMIT %s",
            (design_id, 1),
        ).fetchone()
        if r:
            uqs = (r["uqs_score"] if not isinstance(r, (list, tuple)) else r[0])
    except Exception:  # noqa: BLE001
        uqs = None
    return {"failing_gates": failing, "uqs_score": uqs}


def simulate_delta(design_id: str, delta_graph: dict, baseline_snap_id: str = None,
                   user_id: str = "system") -> dict:
    """Simulate a proposed gate-topology change and return a PASS/WARN/FAIL verdict.

    Combines the real current gate posture (reused from the qdc_gate_breach reflex
    read) with a structural diff of the delta: removing quality-gate nodes on a
    design that already has failing gates is the highest-risk change.
    """
    conn = _get_connection()
    if baseline_snap_id:
        snap = conn.execute("SELECT graph_json FROM qdc_twin_snapshots WHERE id=%s", (baseline_snap_id,)).fetchone()
        if not snap:
            conn.close()
            raise ValueError(f"Snapshot {baseline_snap_id!r} not found")
        baseline = _parse_graph(snap["graph_json"] if not isinstance(snap, (list, tuple)) else snap[0])
    else:
        row = conn.execute("SELECT graph_json FROM qdc_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            conn.close()
            raise ValueError(f"QDC design {design_id!r} not found")
        baseline = _parse_graph(row["graph_json"] if not isinstance(row, (list, tuple)) else row[0])

    delta = delta_graph or {}
    b_nodes = {n["id"]: n for n in baseline.get("nodes", []) if "id" in n}
    d_nodes = {n["id"]: n for n in delta.get("nodes", []) if "id" in n}
    removed_gate_nodes = [b_nodes[k] for k in b_nodes if k not in d_nodes and _is_gate_node(b_nodes[k])]
    added_gate_nodes = [d_nodes[k] for k in d_nodes if k not in b_nodes and _is_gate_node(d_nodes[k])]

    posture = _current_gate_posture(conn, design_id)
    failing = posture["failing_gates"]
    n_fail = len(failing)
    n_removed = len(removed_gate_nodes)

    if n_fail >= 3 or (n_removed and n_fail >= 1):
        verdict = "fail"
    elif n_fail >= 1 or n_removed:
        verdict = "warn"
    else:
        verdict = "pass"

    findings = []
    for f in failing:
        findings.append({"severity": "high", "category": "compliance",
                         "id": f.get("gate_id"), "title": f"Gate {f.get('gate_id')} failing ({f.get('status')})",
                         "recommendation": "Resolve the failing quality gate before applying topology changes"})
    for g in removed_gate_nodes:
        findings.append({"severity": "high" if n_fail else "medium", "category": "compliance",
                         "id": g.get("id"), "title": f"Quality gate node '{g.get('label', g.get('id'))}' removed by delta",
                         "recommendation": "Removing a quality gate lowers assurance — confirm this is intended"})

    sim_id = str(uuid.uuid4())
    diff = {"removed_gate_nodes": len(removed_gate_nodes), "added_gate_nodes": len(added_gate_nodes),
            "current_failing_gates": n_fail, "uqs_score": posture["uqs_score"]}
    conn.execute(
        "INSERT INTO qdc_simulations (id, design_id, baseline_snap_id, delta_graph_json, verdict, findings_json, diff_json, created_by, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (sim_id, design_id, baseline_snap_id, json.dumps(delta), verdict,
         json.dumps(findings), json.dumps(diff), user_id, _now()),
    )
    conn.commit()
    conn.close()
    logger.info("QDC sim %s design %s verdict=%s (fail_gates=%d removed_gates=%d)",
                sim_id, design_id, verdict, n_fail, n_removed)
    return {"id": sim_id, "simulation_id": sim_id, "design_id": design_id, "verdict": verdict,
            "findings": findings, "diff": diff, "baseline_snap_id": baseline_snap_id}


def get_simulation(sim_id: str) -> dict | None:
    conn = _get_connection()
    row = conn.execute("SELECT * FROM qdc_simulations WHERE id=%s", (sim_id,)).fetchone()
    conn.close()
    if not row:
        return None
    r = dict(row)
    for key in ("findings_json", "diff_json"):
        field = key.replace("_json", "")
        r[field] = json.loads(r.pop(key, "{}") or "{}") if key != "findings_json" else json.loads(r.pop(key, "[]") or "[]")
    return r
