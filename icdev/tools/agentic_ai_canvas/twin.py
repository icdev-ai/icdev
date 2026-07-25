# CUI // SP-CTI — AADC Agentic AI Canvas digital twin
"""Agentic AI Design Canvas twin — snapshot the agent topology (A2A mesh, roles,
authority/delegation graph) and simulate an agent-failure cascade before it
happens in production.

Mirrors the PDC twin hygiene (``tools/pipeline/twin.py``): sha256 snapshot dedup
+ bounded auto-snapshot retention — NOT append-only. The cascade simulation
propagates failure along the directed dependency edges of the design graph and
weights the verdict by the design's ``safety_impacting`` / ``rights_impacting``
governance flags on ``aadc_designs`` — the honest, high-consequence signal.

Public API (mirrors PDC):
    take_snapshot(design_id, label=None, user_id="system") -> dict
    list_snapshots(design_id, limit=100) -> list[dict]
    simulate_delta(design_id, delta, baseline_snap_id=None, user_id="system") -> dict
    get_simulation(sim_id) -> dict | None

``delta`` for AADC names the failing/removed agents:
    {"fail_nodes": ["agent-1"], "remove_nodes": [...]}   (aliases: nodes/failed)
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.aadc.twin")

AUTO_SNAPSHOT_LABEL_PREFIX = "auto-"
AUTO_SNAPSHOT_RETENTION = 20
_DEFAULT_LIST_LIMIT = 100
# Agent-ish node types (from tools/agentic_ai_canvas/constants.py AGENT_NODES).
_AGENT_TYPE_HINTS = ("agent", "orchestrator")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_connection():
    from tools.agentic_ai_canvas.db.init_db import get_connection

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


def _is_agent_node(node: dict) -> bool:
    t = str(node.get("type", "")).lower()
    return any(h in t for h in _AGENT_TYPE_HINTS)


def _prune_auto_snapshots(conn, design_id: str, keep: int = AUTO_SNAPSHOT_RETENTION) -> int:
    rows = conn.execute(
        "SELECT id FROM aadc_twin_snapshots WHERE design_id=%s AND label LIKE %s "
        "ORDER BY created_at DESC, id DESC",
        (design_id, AUTO_SNAPSHOT_LABEL_PREFIX + "%"),
    ).fetchall()
    stale = [(r["id"] if not isinstance(r, (list, tuple)) else r[0]) for r in rows[keep:]]
    for sid in stale:
        conn.execute("DELETE FROM aadc_twin_snapshots WHERE id=%s", (sid,))
    return len(stale)


def _design_row(conn, design_id: str):
    return conn.execute(
        "SELECT graph_json, safety_impacting, rights_impacting, autonomy_max, hitl_required "
        "FROM aadc_designs WHERE id=%s", (design_id,)
    ).fetchone()


def take_snapshot(design_id: str, label: str = None, user_id: str = "system") -> dict:
    """Freeze the current AADC agent topology as a named snapshot (dedup + retention)."""
    conn = _get_connection()
    row = _design_row(conn, design_id)
    if not row:
        conn.close()
        raise ValueError(f"AADC design {design_id!r} not found")
    d = dict(row)
    graph_json = d["graph_json"]
    graph = _parse_graph(graph_json)
    nodes, edges = graph.get("nodes", []), graph.get("edges", [])
    snap_label = label or f"snapshot-{_now()[:10]}"

    latest = conn.execute(
        "SELECT id, graph_json, label, node_count, edge_count, created_by, created_at "
        "FROM aadc_twin_snapshots WHERE design_id=%s ORDER BY created_at DESC, id DESC LIMIT %s",
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
        "INSERT INTO aadc_twin_snapshots (id, design_id, label, graph_json, node_count, edge_count, created_by, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (snap_id, design_id, snap_label, graph_json, len(nodes), len(edges), user_id, created_at),
    )
    if snap_label.startswith(AUTO_SNAPSHOT_LABEL_PREFIX):
        _prune_auto_snapshots(conn, design_id)
    conn.commit()
    conn.close()
    logger.info("AADC snapshot %s for design %s (%d agents/nodes)", snap_id, design_id, len(nodes))
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
        "FROM aadc_twin_snapshots WHERE design_id=%s ORDER BY created_at DESC LIMIT %s",
        (design_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _cascade(graph: dict, failed_ids: set) -> list[str]:
    """Propagate failure downstream along directed edges (source depends-on/feeds
    target). Returns the ordered list of impacted node ids (excluding the seeds).
    """
    adj: dict[str, list[str]] = {}
    for e in graph.get("edges", []):
        src = e.get("source") or e.get("src")
        dst = e.get("target") or e.get("dst")
        if src and dst:
            adj.setdefault(src, []).append(dst)
    impacted: list[str] = []
    seen = set(failed_ids)
    frontier = list(failed_ids)
    while frontier:
        cur = frontier.pop(0)
        for nxt in adj.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                impacted.append(nxt)
                frontier.append(nxt)
    return impacted


def simulate_delta(design_id: str, delta, baseline_snap_id: str = None,
                   user_id: str = "system") -> dict:
    """Simulate an agent-failure cascade and return a PASS/WARN/FAIL verdict.

    ``delta`` names the failing agents (``fail_nodes`` / ``remove_nodes`` /
    ``nodes`` / ``failed``). Failure propagates along directed edges; the verdict
    is weighted by the design's safety/rights-impacting flags.
    """
    conn = _get_connection()
    if baseline_snap_id:
        snap = conn.execute("SELECT graph_json FROM aadc_twin_snapshots WHERE id=%s", (baseline_snap_id,)).fetchone()
        if not snap:
            conn.close()
            raise ValueError(f"Snapshot {baseline_snap_id!r} not found")
        graph = _parse_graph(snap["graph_json"] if not isinstance(snap, (list, tuple)) else snap[0])
        d = {"safety_impacting": 0, "rights_impacting": 0}
    else:
        row = _design_row(conn, design_id)
        if not row:
            conn.close()
            raise ValueError(f"AADC design {design_id!r} not found")
        d = dict(row)
        graph = _parse_graph(d["graph_json"])

    delta = delta or {}
    if isinstance(delta, (list, tuple)):
        failed = set(delta)
    else:
        failed = set(delta.get("fail_nodes", []) or delta.get("failed", [])
                     or delta.get("remove_nodes", []) or [n.get("id") for n in delta.get("nodes", []) if n.get("id")])
    failed.discard(None)

    nodes_by_id = {n.get("id"): n for n in graph.get("nodes", []) if n.get("id")}
    impacted_ids = _cascade(graph, failed)
    impacted_agents = [nodes_by_id.get(i, {"id": i}) for i in impacted_ids]
    n_impacted = len(impacted_ids)
    total_agents = sum(1 for n in graph.get("nodes", []) if _is_agent_node(n)) or len(nodes_by_id)

    safety = bool(d.get("safety_impacting"))
    rights = bool(d.get("rights_impacting"))
    high_consequence = safety or rights

    if not failed:
        verdict = "pass"
    elif (high_consequence and n_impacted >= 1) or n_impacted > 5:
        verdict = "fail"
    elif n_impacted >= 1:
        verdict = "warn"
    else:
        verdict = "pass"  # isolated failure, no downstream cascade

    findings = []
    for a in impacted_agents:
        sev = "critical" if high_consequence else ("high" if _is_agent_node(a) else "medium")
        findings.append({"severity": sev, "category": "security",
                         "id": a.get("id"), "title": f"Agent '{a.get('label', a.get('id'))}' impacted by cascade",
                         "recommendation": "Add redundancy / a fallback handler for this agent's upstream dependency"})
    if failed and not impacted_ids:
        findings.append({"severity": "low", "category": "security", "id": None,
                         "title": "Seeded agent failure has no downstream dependents",
                         "recommendation": "Isolated agent — failure does not cascade"})

    sim_id = str(uuid.uuid4())
    diff = {"failed_seeds": sorted(failed), "impacted_count": n_impacted,
            "total_agents": total_agents, "safety_impacting": safety, "rights_impacting": rights}
    conn.execute(
        "INSERT INTO aadc_simulations (id, design_id, baseline_snap_id, delta_graph_json, verdict, findings_json, diff_json, created_by, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (sim_id, design_id, baseline_snap_id, json.dumps(delta if isinstance(delta, dict) else {"fail_nodes": list(failed)}),
         verdict, json.dumps(findings), json.dumps(diff), user_id, _now()),
    )
    conn.commit()
    conn.close()
    logger.info("AADC sim %s design %s verdict=%s (impacted=%d safety=%s rights=%s)",
                sim_id, design_id, verdict, n_impacted, safety, rights)
    return {"id": sim_id, "simulation_id": sim_id, "design_id": design_id, "verdict": verdict,
            "findings": findings, "diff": diff, "impacted_agents": impacted_ids,
            "baseline_snap_id": baseline_snap_id}


def get_simulation(sim_id: str) -> dict | None:
    conn = _get_connection()
    row = conn.execute("SELECT * FROM aadc_simulations WHERE id=%s", (sim_id,)).fetchone()
    conn.close()
    if not row:
        return None
    r = dict(row)
    r["findings"] = json.loads(r.pop("findings_json", "[]") or "[]")
    r["diff"] = json.loads(r.pop("diff_json", "{}") or "{}")
    return r
