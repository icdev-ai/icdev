# CUI // SP-CTI
"""SDC → GNS3 Attack Path Simulation.

Translates a Security Design Canvas STRIDE threat model into a GNS3 live-lab
traffic simulation.  For each attack path produced by replay_attack_paths():

  - Extracts the source and target node labels / IPs from the SDC graph
  - Builds a traffic matrix entry: (src_node, target_ip, path_label, tag)
  - Runs GNS3TrafficEngine (traffic-only, no ZTP) against the linked GNS3 project
  - Returns per-path verdict: "validated" (traffic passed) or "blocked"

Feature flag: GNS3_ENABLED (default false — air-gap safe).
Returns {"status": "disabled"} when flag is off; no network calls are made.

Usage::

    from tools.security_canvas.gns3_sim import simulate_attack_paths
    result = simulate_attack_paths(design_id="abc", graph={...}, project_id="my-lab")
    # result: {status, design_id, project_id, paths_tested, paths_validated,
    #          paths_blocked, paths, errors}
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from tools.databridge.feature_flags import IntegrationFeatureFlags

# ── helpers ───────────────────────────────────────────────────────────────────

_IP_RE = re.compile(
    r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b"
)


def _extract_ip(config: Dict[str, Any], label: str) -> str | None:
    """Return the first IPv4 address found in config or embedded in label."""
    for key in ("ip", "ip_address", "address", "mgmt_ip"):
        val = config.get(key, "")
        if val:
            m = _IP_RE.search(str(val))
            if m:
                return m.group(1)
    m = _IP_RE.search(label)
    return m.group(1) if m else None


def _index_nodes(graph: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Return {node_id: {label, type, ip, config}} from an SDC graph."""
    idx: Dict[str, Dict[str, Any]] = {}
    for n in graph.get("nodes", []):
        nid   = n.get("id", "")
        label = n.get("label") or n.get("name") or nid or "unknown"
        cfg   = n.get("config") or n.get("properties") or {}
        ip    = _extract_ip(cfg, label)
        idx[nid] = {"label": label, "type": n.get("type", ""), "ip": ip, "config": cfg}
    return idx


def _build_traffic_matrix(
    paths: List[Dict[str, Any]],
    node_idx: Dict[str, Dict[str, Any]],
) -> Tuple[List[Tuple[str, str, str, str]], Dict[str, str]]:
    """Convert attack paths to (src_label, dst_ip, label, tag) tuples.

    Returns (matrix, path_key_map) where path_key_map maps
    "{src_id}→{dst_id}" to the GNS3 traffic matrix label (for result matching).
    """
    matrix: List[Tuple[str, str, str, str]] = []
    path_key_map: Dict[str, str] = {}
    seen: set[str] = set()

    for i, path in enumerate(paths):
        nodes = path.get("path", [])
        if len(nodes) < 2:
            continue
        src_id = nodes[0]
        dst_id = nodes[-1]

        src_info = node_idx.get(src_id, {})
        dst_info = node_idx.get(dst_id, {})

        src_label = src_info.get("label", src_id)
        dst_label = dst_info.get("label", dst_id)
        dst_ip    = dst_info.get("ip")

        if not dst_ip:
            continue

        key = f"{src_id}→{dst_id}"
        if key in seen:
            continue
        seen.add(key)

        tag    = f"ap{i:02d}"
        label_ = f"{src_label}→{dst_label}"
        matrix.append((src_label, dst_ip, label_, tag))
        path_key_map[tag] = key

    return matrix, path_key_map


# ── public API ────────────────────────────────────────────────────────────────

def simulate_attack_paths(
    design_id: str,
    graph: Dict[str, Any],
    project_id: str,
    server: str = "http://localhost:3080",
    max_paths: int = 20,
) -> Dict[str, Any]:
    """Simulate SDC attack paths in GNS3 and return per-path verdicts.

    Args:
        design_id:  SDC design UUID (for result attribution only).
        graph:      Parsed SDC graph_data dict {"nodes": [...], "edges": [...]}.
        project_id: GNS3 project name or UUID to simulate against.
        server:     GNS3 server URL (default localhost:3080).
        max_paths:  Max attack paths to simulate (capped for performance).

    Returns::

        {
            "status":           "ok" | "partial" | "disabled" | "error",
            "design_id":        str,
            "project_id":       str,
            "paths_tested":     int,
            "paths_validated":  int,  # traffic passed (attack succeeded in sim)
            "paths_blocked":    int,  # traffic blocked (control effective)
            "paths":            [{"path_key", "label", "src", "dst_ip",
                                  "verdict", "avg_rtt_ms", "reachable"}],
            "errors":           [str],
        }
    """
    flag = IntegrationFeatureFlags.gns3()
    if not flag.enabled:
        return {
            "status": "disabled", "reason": flag.reason, "design_id": design_id,
            "paths_tested": 0, "paths_validated": 0, "paths_blocked": 0,
            "paths": [], "errors": [],
        }

    from tools.security_canvas.attack_path_twin import (  # noqa: PLC0415
        build_attack_graph, replay_attack_paths,
    )
    from tools.ndc.gns3_traffic_engine import GNS3TrafficEngine  # noqa: PLC0415

    errors: List[str] = []

    # 1. Build attack graph and replay paths
    try:
        attack_graph = build_attack_graph(graph)
        raw_paths    = replay_attack_paths(attack_graph, max_paths=max_paths, max_hops=8)
    except Exception as exc:
        return {
            "status": "error", "design_id": design_id,
            "error": f"Attack graph build failed: {exc}",
            "paths_tested": 0, "paths_validated": 0, "paths_blocked": 0,
            "paths": [], "errors": [str(exc)],
        }

    if not raw_paths:
        return {
            "status": "ok", "design_id": design_id, "project_id": project_id,
            "paths_tested": 0, "paths_validated": 0, "paths_blocked": 0,
            "paths": [], "errors": [],
        }

    # 2. Index nodes and build traffic matrix
    node_idx = _index_nodes(graph)
    matrix, path_key_map = _build_traffic_matrix(raw_paths, node_idx)

    if not matrix:
        return {
            "status": "ok", "design_id": design_id, "project_id": project_id,
            "paths_tested": 0, "paths_validated": 0, "paths_blocked": 0,
            "paths": [],
            "errors": ["No attack paths had resolvable destination IPs — "
                       "add ip_address to node configs"],
        }

    # 3. Run GNS3 traffic simulation (traffic-only, no ZTP)
    engine = GNS3TrafficEngine(
        server=server,
        project=project_id,
        ztp_dir=".",           # unused when do_ztp=False
        traffic_matrix=matrix,
        trace_targets=[],
        db_table="sdc_sim_flows",
        convergence_s=0,
    )

    try:
        sim_result = engine.run(do_ztp=False, do_traffic=True, wait_s=0)
    except Exception as exc:
        return {
            "status": "error", "design_id": design_id, "project_id": project_id,
            "error": f"GNS3 simulation failed: {exc}",
            "paths_tested": 0, "paths_validated": 0, "paths_blocked": 0,
            "paths": [], "errors": [str(exc)],
        }

    # 4. Map simulation flow results back to attack paths
    flows = sim_result.get("phases", {}).get("traffic", {}).get("flows", [])
    flow_by_label: Dict[str, Dict[str, Any]] = {f.get("label", ""): f for f in flows}

    path_results: List[Dict[str, Any]] = []
    validated = 0
    blocked   = 0

    for src_label, dst_ip, label_, tag in matrix:
        key     = path_key_map.get(tag, tag)
        flow    = flow_by_label.get(label_, {})
        reach   = flow.get("reachable", False)
        rtt     = flow.get("avg_rtt_ms")
        verdict = "validated" if reach else "blocked"  # validated = attacker succeeded

        if reach:
            validated += 1
        else:
            blocked += 1

        path_results.append({
            "path_key":   key,
            "tag":        tag,
            "label":      label_,
            "src":        src_label,
            "dst_ip":     dst_ip,
            "verdict":    verdict,
            "avg_rtt_ms": rtt,
            "reachable":  reach,
        })

    if sim_result.get("status") == "error":
        errors.append(sim_result.get("error", "GNS3 engine error"))

    return {
        "status":          "ok" if not errors else "partial",
        "design_id":       design_id,
        "project_id":      project_id,
        "paths_tested":    len(path_results),
        "paths_validated": validated,
        "paths_blocked":   blocked,
        "paths":           path_results,
        "errors":          errors,
    }


def validate_gns3_connectivity(server: str = "http://localhost:3080") -> Dict[str, Any]:
    """Quick health check — is GNS3 reachable and the feature flag enabled?"""
    flag = IntegrationFeatureFlags.gns3()
    if not flag.enabled:
        return {"status": "disabled", "reason": flag.reason}

    from tools.network.adapters.gns3_adapter import GNS3Adapter  # noqa: PLC0415
    adapter = GNS3Adapter(server)
    try:
        version = adapter.get_version()
        projects = adapter.list_projects()
        return {
            "status":        "ok",
            "server":        server,
            "version":       version.get("version", "unknown"),
            "project_count": len(projects),
            "projects":      [{"name": p.get("name"), "id": p.get("project_id")} for p in projects[:10]],
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
