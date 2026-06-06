# CUI // SP-CTI
"""IQE security collection adapters.

Importing this module registers three collections on the module-level Executor:
  attack.nodes  — Attack graph nodes (flattened from sdc_attack_snapshots.nodes_json);
                  filter by node_type, component_id, etc.
  attack.edges  — Attack graph edges (flattened from sdc_attack_snapshots.edges_json);
                  filter by source, target, risk_score, etc.
  attack.paths  — Parameterised BFS paths: attack.paths(src, goal).
                  Returns one row per simple path from *src* to *goal*.
                  Called without args, returns all edges as 1-hop paths.
"""
from __future__ import annotations

import json
from collections import deque
from typing import Any

from tools.iqe.executor import register_collection


def nodes_adapter(conn: Any) -> list[dict]:
    """Return one row per attack-graph node across all sdc_attack_snapshots."""
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    cur = conn.execute(
        "SELECT id, component_id, nodes_json, created_at FROM sdc_attack_snapshots"
    )
    cols = [d[0] for d in cur.description]
    rows: list[dict] = []
    for raw in cur.fetchall():
        snap = dict(zip(cols, raw))
        try:
            nodes = json.loads(snap["nodes_json"] or "[]")
        except (ValueError, TypeError):
            nodes = []
        for node in nodes:
            row: dict = {
                "snapshot_id": snap["id"],
                "component_id": snap["component_id"],
                "created_at": snap["created_at"],
            }
            row.update(node)
            rows.append(row)
    return rows


def edges_adapter(conn: Any) -> list[dict]:
    """Return one row per attack-graph edge across all sdc_attack_snapshots."""
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    cur = conn.execute(
        "SELECT id, component_id, edges_json, created_at FROM sdc_attack_snapshots"
    )
    cols = [d[0] for d in cur.description]
    rows: list[dict] = []
    for raw in cur.fetchall():
        snap = dict(zip(cols, raw))
        try:
            edges = json.loads(snap["edges_json"] or "[]")
        except (ValueError, TypeError):
            edges = []
        for edge in edges:
            row: dict = {
                "snapshot_id": snap["id"],
                "component_id": snap["component_id"],
                "created_at": snap["created_at"],
            }
            row.update(edge)
            rows.append(row)
    return rows


def paths_adapter(conn: Any, src: str | None = None, goal: str | None = None) -> list[dict]:
    """BFS all simple paths from *src* to *goal* in the attack graph.

    Each result row: ``{"src": src, "goal": goal, "path": [...], "hops": int}``.
    Without args, returns all direct edges as 1-hop paths.
    """
    all_edges = edges_adapter(conn)
    if src is None or goal is None:
        return [
            {
                "src": e.get("source"),
                "goal": e.get("target"),
                "path": [e.get("source"), e.get("target")],
                "hops": 1,
            }
            for e in all_edges
            if e.get("source") and e.get("target")
        ]
    adj: dict[str, list[str]] = {}
    for edge in all_edges:
        s = edge.get("source")
        t = edge.get("target")
        if s and t:
            adj.setdefault(s, []).append(t)
    results: list[dict] = []
    queue: deque[tuple[str, list[str]]] = deque([(src, [src])])
    while queue:
        node, path = queue.popleft()
        if node == goal:
            results.append({"src": src, "goal": goal, "path": path, "hops": len(path) - 1})
            continue
        for nxt in adj.get(node, []):
            if nxt not in path:
                queue.append((nxt, path + [nxt]))
    return results


def ai_decisions_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return AI decision records for SDC from canvas_ai_decisions (main icdev.db)."""
    try:
        from tools.db.storage import get_connection as _main_conn  # noqa: PLC0415
        with _main_conn() as _c:
            cur = _c.execute(
                "SELECT * FROM canvas_ai_decisions WHERE canvas_type='sdc' ORDER BY created_at DESC"
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


register_collection("attack.nodes", nodes_adapter)
register_collection("attack.edges", edges_adapter)
register_collection("attack.paths", paths_adapter)
register_collection("security.ai_decisions", ai_decisions_adapter)


# ── NSA ZIG Collections ───────────────────────────────────────────────────────

def zig_pillars_adapter(conn: Any) -> list[dict]:
    """Return one row per ZIG pillar with current maturity score."""
    try:
        from tools.security_canvas.zig_pillar_scorer import score_all_pillars  # noqa: PLC0415
        return score_all_pillars()
    except Exception:
        return []


def zig_capabilities_adapter(conn: Any) -> list[dict]:
    """Return all 42 ZIG target capabilities with implementation status."""
    try:
        from tools.security_canvas.db.init_db import get_connection as _sc_conn  # noqa: PLC0415
        _conn = _sc_conn()
        try:
            rows = _conn.execute(
                "SELECT id, pillar_slug, title, phase, maturity_level, implementation_status, description "
                "FROM zig_capabilities ORDER BY pillar_slug, phase"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            _conn.close()
    except Exception:
        return []


def zig_activities_adapter(conn: Any) -> list[dict]:
    """Return all 91 ZIG activities with completion status."""
    try:
        from tools.security_canvas.db.init_db import get_connection as _sc_conn  # noqa: PLC0415
        _conn = _sc_conn()
        try:
            rows = _conn.execute(
                "SELECT a.id, a.title, a.phase, a.nist_control_ref, a.capability_id, "
                "c.pillar_slug, COALESCE(ac.status, 'not_started') as completion_status "
                "FROM zig_activities a "
                "JOIN zig_capabilities c ON a.capability_id=c.id "
                "LEFT JOIN zig_activity_completions ac ON a.id=ac.activity_id "
                "ORDER BY a.phase, c.pillar_slug"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            _conn.close()
    except Exception:
        return []


def zig_maturity_adapter(conn: Any) -> list[dict]:
    """Return aggregated ZIG maturity summary."""
    try:
        from tools.security_canvas.zig_pillar_scorer import score_all_pillars, aggregate_zig_score  # noqa: PLC0415
        from tools.security_canvas.zig_phase_tracker import compute_fy2027_readiness  # noqa: PLC0415
        pillar_scores = score_all_pillars()
        agg = aggregate_zig_score(pillar_scores)
        fy2027 = compute_fy2027_readiness()
        return [{"type": "aggregate", **agg, **fy2027}]
    except Exception:
        return []


def zig_gaps_adapter(conn: Any) -> list[dict]:
    """Return ZIG capability gaps (not started or planned only)."""
    try:
        from tools.security_canvas.db.init_db import get_connection as _sc_conn  # noqa: PLC0415
        _conn = _sc_conn()
        try:
            rows = _conn.execute(
                "SELECT id, pillar_slug, title, phase, maturity_level, implementation_status "
                "FROM zig_capabilities WHERE implementation_status IN ('not_started','planned') "
                "ORDER BY CASE phase WHEN 'discovery' THEN 1 WHEN 'phase1' THEN 2 ELSE 3 END, pillar_slug"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            _conn.close()
    except Exception:
        return []


register_collection("zig.pillars", zig_pillars_adapter)
register_collection("zig.capabilities", zig_capabilities_adapter)
register_collection("zig.activities", zig_activities_adapter)
register_collection("zig.maturity", zig_maturity_adapter)
register_collection("zig.gaps", zig_gaps_adapter)
