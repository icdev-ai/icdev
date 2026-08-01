# CUI // SP-CTI — SDC Attack Path Digital Twin
"""Security Design Canvas attack path twin — snapshot, simulate, BAS-style what-if."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.security_canvas.twin")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def take_snapshot(design_id: str, label: str | None = None) -> dict:
    """Freeze attack graph into sdc_attack_snapshots (append-only)."""
    conn = get_connection()
    snap_id = str(uuid.uuid4())
    taken_at = _now()
    label = label or f"snap-{taken_at[:10]}"
    try:
        node_count = conn.execute(
            "SELECT COUNT(*) FROM security_nodes WHERE design_id=%s", (design_id,)
        ).fetchone()[0]
    except Exception:
        node_count = 0
    try:
        conn.execute(
            "INSERT INTO sdc_attack_snapshots (id, design_id, label, node_count, path_count, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
            (snap_id, design_id, label, node_count, 0, taken_at),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("take_snapshot: best-effort INSERT into sdc_attack_snapshots failed (non-blocking): %s", exc)
    return {"id": snap_id, "design_id": design_id, "label": label,
            "node_count": node_count, "path_count": 0, "created_at": taken_at}


def simulate_delta(design_id: str, delta_graph: dict, entry_point: str | None = None,
                   target_goal: str | None = None, baseline_snap_id: str | None = None,
                   use_cot: bool = False) -> dict:
    """Simulate proposed topology change and enumerate new attack paths.

    When *use_cot* is True, a reasoning trace is generated over STRIDE
    coverage to explain why the safest delta was chosen.
    """
    sim_id = str(uuid.uuid4())
    nodes = delta_graph.get("nodes", [])
    edges = delta_graph.get("edges", [])

    # Heuristic risk assessment
    high_risk_types = {"asset-server", "asset-database", "asset-storage"}
    exposed = [n for n in nodes if n.get("type") in high_risk_types]
    risk_score = min(0.3 + len(exposed) * 0.15 + len(edges) * 0.05, 1.0)
    total_paths = len(exposed) * max(len(edges), 1)
    critical_paths = len(exposed)

    stride_coverage = {
        "spoofing": any(n.get("type") in ("ctrl-iam", "ctrl-mfa") for n in nodes),
        "tampering": any(n.get("type") in ("ctrl-waf", "ctrl-ids") for n in nodes),
        "repudiation": False,
        "info_disclosure": len(exposed) == 0,
        "dos": any(n.get("type") == "ctrl-ddos" for n in nodes),
        "elevation": False,
    }

    # CoT reasoning over STRIDE coverage
    cot_trace: dict | None = None
    if use_cot:
        covered = sum(1 for v in stride_coverage.values() if v)
        reasoning = (
            f"STRIDE coverage: {covered}/{len(stride_coverage)} categories addressed. "
            f"Exposed high-risk nodes: {len(exposed)}. Edges: {len(edges)}. "
        )
        if covered >= 4:
            reasoning += "Strong STRIDE coverage suggests lower risk."
        elif covered >= 2:
            reasoning += "Moderate STRIDE coverage; some attack vectors remain uncontrolled."
        else:
            reasoning += "Weak STRIDE coverage; multiple attack vectors are uncontrolled."
        safest = "pass" if risk_score < 0.4 else ("warn" if risk_score < 0.7 else "fail")
        cot_trace = {
            "reasoning": reasoning,
            "stride_coverage": stride_coverage,
            "covered_count": covered,
            "recommended_verdict": safest,
        }

    verdict = "pass" if risk_score < 0.4 else ("warn" if risk_score < 0.7 else "fail")
    attack_paths = [
        {"path_id": f"path-{i+1}", "severity": "critical" if i < critical_paths else "high",
         "path": [entry_point or "internet", n.get("id", f"node-{i}"), target_goal or "data-store"],
         "description": f"Attack path via {n.get('label', n.get('id', 'node'))}"}
        for i, n in enumerate(exposed[:5])
    ]
    result = {
        "simulation_id": sim_id, "design_id": design_id, "verdict": verdict,
        "total_paths": total_paths, "critical_paths": critical_paths,
        "baseline_path_count": 0, "risk_score": round(risk_score, 3),
        "stride_coverage": stride_coverage, "attack_paths": attack_paths,
    }
    if cot_trace:
        result["cot_trace"] = cot_trace
    return result
