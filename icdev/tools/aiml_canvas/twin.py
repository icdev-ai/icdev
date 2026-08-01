# CUI // SP-CTI — AIML (AI/ML Design Canvas) digital twin
"""AI/ML Design Canvas twin — snapshot the AI/ML architecture graph and simulate
architecture changes before applying them (twx-cov-02, wave-2).

Mirrors the QDC/PDC twin hygiene: sha256 snapshot dedup + bounded auto-snapshot
retention (NOT append-only). The verdict is grounded in the real governance
assessments the canvas already computes (``aiml_assessments`` — latest per
framework, failing = ``passed = 0``); removing architecture nodes on a design
that already fails a framework is the highest-risk change. The twin never
re-derives compliance — it reuses the assessment engine's verdict.

Public API (mirrors QDC):
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

logger = get_logger("icdev.aiml.twin")

AUTO_SNAPSHOT_LABEL_PREFIX = "auto-"
AUTO_SNAPSHOT_RETENTION = 20
_DEFAULT_LIST_LIMIT = 100


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_connection():
    from tools.aiml_canvas.db.init_db import get_connection

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


def _prune_auto_snapshots(conn, design_id: str, keep: int = AUTO_SNAPSHOT_RETENTION) -> int:
    rows = conn.execute(
        "SELECT id FROM aiml_twin_snapshots WHERE design_id=%s AND label LIKE %s "
        "ORDER BY created_at DESC, id DESC",
        (design_id, AUTO_SNAPSHOT_LABEL_PREFIX + "%"),
    ).fetchall()
    stale = [(r["id"] if not isinstance(r, (list, tuple)) else r[0]) for r in rows[keep:]]
    for sid in stale:
        conn.execute("DELETE FROM aiml_twin_snapshots WHERE id=%s", (sid,))
    return len(stale)


def take_snapshot(design_id: str, label: str = None, user_id: str = "system") -> dict:
    """Freeze the current AI/ML architecture graph as a named snapshot (dedup + retention)."""
    conn = _get_connection()
    row = conn.execute("SELECT graph_json FROM aiml_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"AIML design {design_id!r} not found")
    graph_json = row["graph_json"] if not isinstance(row, (list, tuple)) else row[0]
    graph = _parse_graph(graph_json)
    nodes, edges = graph.get("nodes", []), graph.get("edges", [])
    snap_label = label or f"snapshot-{_now()[:10]}"

    latest = conn.execute(
        "SELECT id, graph_json, label, node_count, edge_count, created_by, created_at "
        "FROM aiml_twin_snapshots WHERE design_id=%s ORDER BY created_at DESC, id DESC LIMIT %s",
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
        "INSERT INTO aiml_twin_snapshots (id, design_id, label, graph_json, node_count, edge_count, created_by, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (snap_id, design_id, snap_label, graph_json, len(nodes), len(edges), user_id, created_at),
    )
    if snap_label.startswith(AUTO_SNAPSHOT_LABEL_PREFIX):
        _prune_auto_snapshots(conn, design_id)
    conn.commit()
    conn.close()
    logger.info("AIML snapshot %s for design %s (%d nodes)", snap_id, design_id, len(nodes))
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
        "FROM aiml_twin_snapshots WHERE design_id=%s ORDER BY created_at DESC LIMIT %s",
        (design_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _current_assessment_posture(conn, design_id: str) -> dict:
    """Reuse the AI/ML governance assessments: latest assessment per framework,
    failing = passed=0. Returns failing frameworks + the minimum score."""
    failing = []
    min_score = None
    try:
        rows = conn.execute(
            "SELECT a.framework_id, a.framework_name, a.score, a.passed, a.created_at FROM aiml_assessments a "
            "INNER JOIN (SELECT framework_id, MAX(created_at) AS max_at FROM aiml_assessments "
            "            WHERE design_id=%s GROUP BY framework_id) latest "
            "ON a.framework_id=latest.framework_id AND a.created_at=latest.max_at "
            "WHERE a.design_id=%s ORDER BY a.created_at DESC",
            (design_id, design_id),
        ).fetchall()
        for r in rows:
            d = dict(r)
            score = d.get("score")
            if score is not None:
                min_score = score if min_score is None else min(min_score, score)
            if not d.get("passed"):
                failing.append(d)
    except Exception:  # noqa: BLE001 — table may be empty/absent
        failing = []
    return {"failing_frameworks": failing, "min_score": min_score}


def simulate_delta(design_id: str, delta_graph: dict, baseline_snap_id: str = None,
                   user_id: str = "system") -> dict:
    """Simulate a proposed AI/ML architecture change and return PASS/WARN/FAIL.

    Combines the real current governance posture (reused from ``aiml_assessments``)
    with a structural diff of the delta: removing architecture nodes on a design
    that already fails a framework is the highest-risk change.
    """
    conn = _get_connection()
    if baseline_snap_id:
        snap = conn.execute("SELECT graph_json FROM aiml_twin_snapshots WHERE id=%s", (baseline_snap_id,)).fetchone()
        if not snap:
            conn.close()
            raise ValueError(f"Snapshot {baseline_snap_id!r} not found")
        baseline = _parse_graph(snap["graph_json"] if not isinstance(snap, (list, tuple)) else snap[0])
    else:
        row = conn.execute("SELECT graph_json FROM aiml_designs WHERE id=%s", (design_id,)).fetchone()
        if not row:
            conn.close()
            raise ValueError(f"AIML design {design_id!r} not found")
        baseline = _parse_graph(row["graph_json"] if not isinstance(row, (list, tuple)) else row[0])

    delta = delta_graph or {}
    b_nodes = {n["id"]: n for n in baseline.get("nodes", []) if "id" in n}
    d_nodes = {n["id"]: n for n in delta.get("nodes", []) if "id" in n}
    removed_nodes = [b_nodes[k] for k in b_nodes if k not in d_nodes]
    added_nodes = [d_nodes[k] for k in d_nodes if k not in b_nodes]

    posture = _current_assessment_posture(conn, design_id)
    failing = posture["failing_frameworks"]
    n_fail = len(failing)
    n_removed = len(removed_nodes)

    if n_fail >= 3 or (n_removed and n_fail >= 1):
        verdict = "fail"
    elif n_fail >= 1 or n_removed:
        verdict = "warn"
    else:
        verdict = "pass"

    findings = []
    for f in failing:
        findings.append({"severity": "high", "category": "compliance",
                         "id": f.get("framework_id"),
                         "title": f"AI governance framework '{f.get('framework_name', f.get('framework_id'))}' not passed (score {f.get('score')})",
                         "recommendation": "Remediate the failing AI governance framework before changing the architecture"})
    for g in removed_nodes:
        findings.append({"severity": "high" if n_fail else "medium", "category": "compliance",
                         "id": g.get("id"),
                         "title": f"Architecture node '{g.get('label', g.get('id'))}' removed by delta",
                         "recommendation": "Removing an AI/ML architecture node may drop a governance control — confirm intent"})

    sim_id = str(uuid.uuid4())
    diff = {"removed_nodes": n_removed, "added_nodes": len(added_nodes),
            "current_failing_frameworks": n_fail, "min_assessment_score": posture["min_score"]}
    conn.execute(
        "INSERT INTO aiml_simulations (id, design_id, baseline_snap_id, delta_graph_json, verdict, findings_json, diff_json, created_by, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (sim_id, design_id, baseline_snap_id, json.dumps(delta), verdict,
         json.dumps(findings), json.dumps(diff), user_id, _now()),
    )
    conn.commit()
    conn.close()
    logger.info("AIML sim %s design %s verdict=%s (fail_fw=%d removed=%d)",
                sim_id, design_id, verdict, n_fail, n_removed)
    return {"id": sim_id, "simulation_id": sim_id, "design_id": design_id, "verdict": verdict,
            "findings": findings, "diff": diff, "baseline_snap_id": baseline_snap_id}


def get_simulation(sim_id: str) -> dict | None:
    conn = _get_connection()
    row = conn.execute("SELECT * FROM aiml_simulations WHERE id=%s", (sim_id,)).fetchone()
    conn.close()
    if not row:
        return None
    r = dict(row)
    r["findings"] = json.loads(r.pop("findings_json", "[]") or "[]")
    r["diff"] = json.loads(r.pop("diff_json", "{}") or "{}")
    return r
