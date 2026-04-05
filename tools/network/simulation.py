# CUI // SP-CTI — ICDEV™ Network Canvas Simulation Engine
# Pure-function simulation library — no Flask dependency.
# All functions accept (nodes, edges, params) and return dicts.
"""Network topology simulation functions extracted from network-canvas app.py.

Every public function is deterministic and framework-agnostic.
"""

from __future__ import annotations

import heapq
from collections import deque
from typing import Any


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _get_node_config(node: dict) -> dict:
    return node.get("config", {})


def _infer_ospf_cost(edge: dict, ref_bw: int) -> int:
    """Infer OSPF cost from link label (bandwidth hints)."""
    label = (edge.get("label") or "").upper()
    if "400G" in label:
        return max(1, ref_bw // 400000)
    if "100G" in label:
        return max(1, ref_bw // 100000)
    if "40G" in label:
        return max(1, ref_bw // 40000)
    if "25G" in label:
        return max(1, ref_bw // 25000)
    if "10G" in label:
        return max(1, ref_bw // 10000)
    if "GBE" in label or "GE" in label or "1G" in label:
        return max(1, ref_bw // 1000)
    if "100M" in label or "FAST" in label:
        return max(1, ref_bw // 100)
    return 10  # Default cost


# ---------------------------------------------------------------------------
# Graph traversal helpers
# ---------------------------------------------------------------------------


def _find_all_paths(nodes, edges, src, dst, max_paths=8):
    """DFS to find multiple paths (up to max_paths)."""
    adj: dict[str, set] = {}
    for e in edges:
        adj.setdefault(e["source"], set()).add(e["target"])
        adj.setdefault(e["target"], set()).add(e["source"])
    results: list[list[str]] = []
    stack = [(src, [src])]
    while stack and len(results) < max_paths:
        node, path = stack.pop()
        if node == dst:
            results.append(path)
            continue
        for nb in adj.get(node, set()):
            if nb not in path:
                stack.append((nb, path + [nb]))
    return results


def _bfs_path(nodes, edges, src_id, dst_id):
    """BFS shortest path; returns list of node labels."""
    adj: dict[str, set] = {}
    label_map = {n["id"]: n.get("label", n["id"]) for n in nodes}
    for e in edges:
        adj.setdefault(e["source"], set()).add(e["target"])
        adj.setdefault(e["target"], set()).add(e["source"])
    q: deque[list[str]] = deque([[src_id]])
    visited = {src_id}
    while q:
        path = q.popleft()
        node = path[-1]
        if node == dst_id:
            return [label_map.get(n, n) for n in path]
        for nb in adj.get(node, []):
            if nb not in visited:
                visited.add(nb)
                q.append(path + [nb])
    return []


CLOUD_HA_TYPES = {
    "aws-tgw",
    "aws-dx-gw",
    "aws-nlb",
    "aws-gwlb",
    "aws-cloudwan",
    "az-vwan",
    "az-crosslb",
    "gcp-ncc",
    "gcp-gfe",
    "oci-drg",
    "ibm-tg",
}


def _find_spof(nodes, edges):
    """Identify articulation points (bridges) using simple degree heuristic."""
    degree: dict[str, int] = {}
    label_map = {n["id"]: n.get("label", n["id"]) for n in nodes}
    type_map = {n["id"]: n.get("type", "") for n in nodes}
    for e in edges:
        degree[e["source"]] = degree.get(e["source"], 0) + 1
        degree[e["target"]] = degree.get(e["target"], 0) + 1
    spof = [
        label_map.get(nid, nid)
        for nid, deg in degree.items()
        if deg == 1 and type_map.get(nid, "") not in CLOUD_HA_TYPES
    ]
    return spof


# ---------------------------------------------------------------------------
# Protocol simulations
# ---------------------------------------------------------------------------


def _sim_bgp_bestpath(nodes, edges, params) -> dict:
    """BGP best path selection following Cisco decision process:
    Weight > LOCAL_PREF > AS_PATH length > Origin > MED > eBGP>iBGP > Router-ID
    Evaluates all paths from every BGP speaker to destination prefix."""
    node_map = {n["id"]: n for n in nodes}
    label_map = {n["id"]: n.get("label", n["id"]) for n in nodes}
    adj: dict[str, list] = {}
    for e in edges:
        adj.setdefault(e["source"], []).append(e)
        adj.setdefault(e["target"], []).append({**e, "source": e["target"], "target": e["source"]})

    # Find all BGP speakers (nodes with ASN set or protocol BGP on edges)
    bgp_speakers: list[str] = []
    for n in nodes:
        cfg = _get_node_config(n)
        if cfg.get("asn") or cfg.get("protocol") == "BGP":
            bgp_speakers.append(n["id"])
    # Also check edge protocols
    for e in edges:
        if e.get("protocol", "").upper() in ("BGP", "EBGP", "IBGP", "BGP EVPN"):
            for nid in (e["source"], e["target"]):
                if nid not in bgp_speakers:
                    bgp_speakers.append(nid)

    if len(bgp_speakers) < 2:
        return {
            "sim_type": "bgp_bestpath",
            "summary": "Need at least 2 BGP speakers (set ASN in node config)",
            "paths": [],
            "best_path": None,
        }

    # Find all paths between first and last BGP speaker
    src = bgp_speakers[0]
    dst = bgp_speakers[-1]
    all_paths = _find_all_paths(nodes, edges, src, dst, max_paths=8)

    # Score each path using BGP attributes
    scored_paths = []
    for path_ids in all_paths:
        path_labels = [label_map.get(p, p) for p in path_ids]
        # Aggregate attributes along path
        weight = 0
        local_pref = 100
        med = 0
        as_path_len = 0
        seen_asns: set = set()
        hop_details = []

        for nid in path_ids:
            cfg = _get_node_config(node_map.get(nid, {}))
            w = int(cfg.get("weight") or 0)
            lp = int(cfg.get("local_pref") or 100)
            m = int(cfg.get("med") or 0)
            asn = cfg.get("asn", "")
            if w > weight:
                weight = w
            if lp != 100:
                local_pref = max(local_pref, lp)
            med += m
            if asn and asn not in seen_asns:
                as_path_len += 1
                seen_asns.add(asn)
            hop_details.append(
                {
                    "node": label_map.get(nid, nid),
                    "asn": asn or "\u2014",
                    "local_pref": lp,
                    "med": m,
                    "weight": w,
                }
            )

        # BGP decision score (higher = better)
        # Weight (higher wins), LOCAL_PREF (higher wins), AS_PATH (shorter wins), MED (lower wins)
        score = weight * 100000 + local_pref * 1000 - as_path_len * 100 - med

        scored_paths.append(
            {
                "path": path_labels,
                "path_ids": path_ids,
                "weight": weight,
                "local_pref": local_pref,
                "med": med,
                "as_path_length": as_path_len,
                "score": score,
                "hop_details": hop_details,
            }
        )

    # Sort by score descending (best first)
    scored_paths.sort(key=lambda p: p["score"], reverse=True)
    best = scored_paths[0] if scored_paths else None

    decision_reason = ""
    if best and len(scored_paths) > 1:
        second = scored_paths[1]
        if best["weight"] > second["weight"]:
            decision_reason = f"Weight ({best['weight']} > {second['weight']})"
        elif best["local_pref"] > second["local_pref"]:
            decision_reason = f"LOCAL_PREF ({best['local_pref']} > {second['local_pref']})"
        elif best["as_path_length"] < second["as_path_length"]:
            decision_reason = f"AS_PATH length ({best['as_path_length']} < {second['as_path_length']})"
        elif best["med"] < second["med"]:
            decision_reason = f"MED ({best['med']} < {second['med']})"
        else:
            decision_reason = "Tie-break (first path)"

    return {
        "sim_type": "bgp_bestpath",
        "source": label_map.get(src, src),
        "destination": label_map.get(dst, dst),
        "paths": [{k: v for k, v in p.items() if k != "path_ids"} for p in scored_paths],
        "best_path": best["path"] if best else [],
        "decision_reason": decision_reason,
        "summary": ("Best path: " + " \u2192 ".join(best["path"]) + f" ({decision_reason})")
        if best
        else "No paths found",
    }


def _sim_bgp_propagation(nodes, edges, params) -> dict:
    """Simulate BGP route propagation wave from an originator through all eBGP/iBGP peers."""
    node_map = {n["id"]: n for n in nodes}
    label_map = {n["id"]: n.get("label", n["id"]) for n in nodes}

    # Build BGP adjacency (only BGP edges)
    bgp_adj: dict[str, list] = {}
    for e in edges:
        proto = (e.get("protocol") or "").upper()
        if "BGP" in proto:
            bgp_adj.setdefault(e["source"], []).append(e["target"])
            bgp_adj.setdefault(e["target"], []).append(e["source"])

    # Find originator (first node with ASN)
    originator = None
    for n in nodes:
        if _get_node_config(n).get("asn"):
            originator = n["id"]
            break
    if not originator:
        # Fallback: first node with BGP adjacency
        originator = next(iter(bgp_adj), nodes[0]["id"] if nodes else None)

    if not originator or not bgp_adj:
        return {
            "sim_type": "bgp_propagation",
            "summary": "No BGP sessions found. Set ASN on nodes and BGP protocol on links.",
            "waves": [],
        }

    # BFS propagation waves
    waves = []
    visited = {originator}
    frontier = [originator]
    wave_num = 0
    while frontier:
        wave_num += 1
        wave_info: dict[str, Any] = {
            "wave": wave_num,
            "nodes": [label_map.get(n, n) for n in frontier],
            "action": "Originate" if wave_num == 1 else "Receive + re-advertise",
        }
        # Apply LOCAL_PREF decrement per wave
        for nid in frontier:
            cfg = _get_node_config(node_map.get(nid, {}))
            lp = int(cfg.get("local_pref") or 100)
            wave_info.setdefault("local_prefs", {})[label_map.get(nid, nid)] = lp

        waves.append(wave_info)
        next_frontier = []
        for nid in frontier:
            for peer in bgp_adj.get(nid, []):
                if peer not in visited:
                    visited.add(peer)
                    next_frontier.append(peer)
        frontier = next_frontier

    return {
        "sim_type": "bgp_propagation",
        "originator": label_map.get(originator, originator),
        "waves": waves,
        "total_waves": len(waves),
        "nodes_reached": len(visited),
        "total_bgp_nodes": len(set(n for peers in bgp_adj.values() for n in peers) | set(bgp_adj.keys())),
        "summary": f"Route propagated in {len(waves)} waves, reached {len(visited)} nodes from {label_map.get(originator, originator)}",
    }


def _sim_ospf_spf(nodes, edges, params) -> dict:
    """Dijkstra SPF using OSPF cost per link. Cost defaults to reference_bw / link_bw."""
    node_map = {n["id"]: n for n in nodes}
    label_map = {n["id"]: n.get("label", n["id"]) for n in nodes}
    ref_bw = 100000  # 100 Gbps reference bandwidth

    # Build weighted adjacency
    adj: dict[str, list] = {}
    for e in edges:
        # Cost from node config or edge label bandwidth
        src_cfg = _get_node_config(node_map.get(e["source"], {}))
        tgt_cfg = _get_node_config(node_map.get(e["target"], {}))
        cost_src = int(src_cfg.get("ospf_cost") or 0)
        cost_tgt = int(tgt_cfg.get("ospf_cost") or 0)
        cost = max(cost_src, cost_tgt) if (cost_src or cost_tgt) else _infer_ospf_cost(e, ref_bw)
        adj.setdefault(e["source"], []).append((e["target"], cost, e))
        adj.setdefault(e["target"], []).append((e["source"], cost, e))

    # Pick source (first router-type node, or first node)
    src = None
    for n in nodes:
        if n.get("type") in ("router", "switch-l3"):
            src = n["id"]
            break
    if not src:
        src = nodes[0]["id"] if nodes else None
    if not src:
        return {"sim_type": "ospf_spf", "summary": "No nodes", "spf_tree": []}

    # Dijkstra
    dist: dict[str, float] = {src: 0}
    prev: dict[str, str | None] = {src: None}
    pq: list[tuple[float, str]] = [(0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, float("inf")):
            continue
        for v, w, _ in adj.get(u, []):
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))

    # Build SPF tree
    spf_tree = []
    for nid in sorted(dist, key=lambda x: dist[x]):
        path = []
        cur: str | None = nid
        while cur is not None:
            path.insert(0, label_map.get(cur, cur))
            cur = prev.get(cur)
        spf_tree.append(
            {
                "node": label_map.get(nid, nid),
                "cost": dist[nid],
                "path": path,
                "hops": len(path) - 1,
            }
        )

    # ECMP detection (equal cost paths)
    ecmp_pairs = []
    for nid in dist:
        ecmp_count = sum(
            1 for v, w, _ in adj.get(nid, []) if dist.get(v, float("inf")) + w == dist.get(nid, float("inf"))
        )
        if ecmp_count > 1:
            ecmp_pairs.append(label_map.get(nid, nid))

    return {
        "sim_type": "ospf_spf",
        "root": label_map.get(src, src),
        "spf_tree": spf_tree,
        "ecmp_nodes": ecmp_pairs,
        "best_path": spf_tree[-1]["path"] if spf_tree else [],
        "summary": f"SPF from {label_map.get(src, src)}: {len(spf_tree)} nodes, {len(ecmp_pairs)} ECMP candidates",
    }


def _sim_ospf_cost(nodes, edges, params) -> dict:
    """Show OSPF cost breakdown per link with load-balancing analysis."""
    node_map = {n["id"]: n for n in nodes}
    label_map = {n["id"]: n.get("label", n["id"]) for n in nodes}
    ref_bw = 100000

    link_costs = []
    for e in edges:
        src_cfg = _get_node_config(node_map.get(e["source"], {}))
        tgt_cfg = _get_node_config(node_map.get(e["target"], {}))
        cost_src = int(src_cfg.get("ospf_cost") or 0)
        cost_tgt = int(tgt_cfg.get("ospf_cost") or 0)
        cost = max(cost_src, cost_tgt) if (cost_src or cost_tgt) else _infer_ospf_cost(e, ref_bw)

        proto = (e.get("protocol") or "").upper()
        area_src = src_cfg.get("ospf_area", "0")
        area_tgt = tgt_cfg.get("ospf_area", "0")
        is_abr = area_src != area_tgt

        link_costs.append(
            {
                "link": f"{label_map.get(e['source'], '?')} \u2014 {label_map.get(e['target'], '?')}",
                "label": e.get("label", ""),
                "cost": cost,
                "protocol": proto,
                "area": area_src if area_src == area_tgt else f"{area_src}/{area_tgt} (ABR)",
                "is_abr_boundary": is_abr,
            }
        )

    link_costs.sort(key=lambda x: x["cost"])

    # Load balancing candidates: links with equal cost to same destination
    cost_groups: dict[int, list] = {}
    for lc in link_costs:
        cost_groups.setdefault(lc["cost"], []).append(lc["link"])
    lb_groups = {c: links for c, links in cost_groups.items() if len(links) > 1}

    return {
        "sim_type": "ospf_cost",
        "link_costs": link_costs,
        "load_balance_groups": lb_groups,
        "abr_boundaries": [lc["link"] for lc in link_costs if lc["is_abr_boundary"]],
        "summary": f"{len(link_costs)} links analyzed, {len(lb_groups)} equal-cost load-balance groups, "
        f"{sum(1 for lc in link_costs if lc['is_abr_boundary'])} ABR boundaries",
    }


def _sim_jumbo_mtu(nodes, edges, params) -> dict:
    """Walk all paths checking MTU at each hop. Flag where jumbo frames (9000)
    would be fragmented or dropped. Checks node MTU config and link labels."""
    desired_mtu = int(params.get("mtu_size", 9000))
    node_map = {n["id"]: n for n in nodes}
    label_map = {n["id"]: n.get("label", n["id"]) for n in nodes}
    node_ids = [n["id"] for n in nodes]

    if len(node_ids) < 2:
        return {"sim_type": "jumbo_mtu", "summary": "Need at least 2 nodes", "path_mtu": []}

    # Check MTU at every node
    node_mtus = []
    for n in nodes:
        cfg = _get_node_config(n)
        mtu = int(cfg.get("mtu") or 1500)
        node_mtus.append(
            {
                "node": label_map.get(n["id"], n["id"]),
                "node_id": n["id"],
                "mtu": mtu,
                "supports_jumbo": mtu >= desired_mtu,
                "status": "ok" if mtu >= desired_mtu else "fragmentation" if mtu >= 1500 else "drop",
            }
        )

    # Walk the default path (BFS from first to last)
    src = node_ids[0]
    dst = node_ids[-1]
    path_ids: list[str] = []
    # BFS for path
    adj: dict[str, set] = {}
    for e in edges:
        adj.setdefault(e["source"], set()).add(e["target"])
        adj.setdefault(e["target"], set()).add(e["source"])
    q: deque[list[str]] = deque([[src]])
    visited = {src}
    while q:
        p = q.popleft()
        if p[-1] == dst:
            path_ids = p
            break
        for nb in adj.get(p[-1], []):
            if nb not in visited:
                visited.add(nb)
                q.append(p + [nb])

    # Path MTU analysis
    path_mtu_detail = []
    min_mtu = desired_mtu
    bottleneck = None
    for nid in path_ids:
        cfg = _get_node_config(node_map.get(nid, {}))
        mtu = int(cfg.get("mtu") or 1500)
        if mtu < min_mtu:
            min_mtu = mtu
            bottleneck = label_map.get(nid, nid)
        path_mtu_detail.append(
            {
                "node": label_map.get(nid, nid),
                "mtu": mtu,
                "status": "ok" if mtu >= desired_mtu else "BOTTLENECK",
            }
        )

    jumbo_ready = min_mtu >= desired_mtu
    frag_nodes = [nm for nm in node_mtus if not nm["supports_jumbo"]]

    return {
        "sim_type": "jumbo_mtu",
        "desired_mtu": desired_mtu,
        "path_mtu": min_mtu,
        "jumbo_ready": jumbo_ready,
        "bottleneck": bottleneck,
        "path_detail": path_mtu_detail,
        "node_audit": node_mtus,
        "fragmentation_points": [f["node"] for f in frag_nodes],
        "summary": (
            f"Path MTU: {min_mtu} \u2014 {'Jumbo OK' if jumbo_ready else f'FRAGMENTATION at {bottleneck} (MTU {min_mtu})'}"
            f" | {len(frag_nodes)}/{len(nodes)} nodes below {desired_mtu}"
        ),
    }


def _sim_dwdm_optical(nodes, edges, params) -> dict:
    """DWDM optical quality simulation: OSNR, chromatic dispersion, PMD, attenuation per span."""
    label_map = {n["id"]: n.get("label", n["id"]) for n in nodes}
    node_map = {n["id"]: n for n in nodes}

    # Optical node types
    OPTICAL_TYPES = {
        "roadm",
        "oadm",
        "edfa",
        "transponder",
        "olt",
        "odf",
        "media-optical",
        "media-fiber",
        "patch-panel-fiber",
        "sonet-adm",
    }

    # Walk optical path (find connected optical nodes)
    optical_nodes = [n for n in nodes if n.get("type") in OPTICAL_TYPES]
    if not optical_nodes:
        return {
            "sim_type": "dwdm_optical",
            "summary": "No optical nodes found. Add ROADM, OADM, EDFA, or Transponder nodes.",
            "spans": [],
        }

    # Build spans from edges between optical nodes
    optical_ids = {n["id"] for n in optical_nodes}
    spans = []
    for e in edges:
        if e["source"] in optical_ids and e["target"] in optical_ids:
            src_type = node_map.get(e["source"], {}).get("type", "")
            tgt_type = node_map.get(e["target"], {}).get("type", "")
            label = e.get("label", "")

            # Estimate span distance from label
            distance_km = 80  # default
            for tok in label.lower().replace(",", " ").split():
                if "km" in tok:
                    try:
                        distance_km = float(tok.replace("km", ""))
                    except ValueError:
                        pass

            # Fiber attenuation: 0.2 dB/km for G.652 SMF at 1550nm
            attenuation_db = round(distance_km * 0.2, 2)
            # Connector/splice loss: 0.5 dB per node
            connector_loss = 0.5
            total_loss = round(attenuation_db + connector_loss * 2, 2)

            # EDFA gain recovery
            has_edfa = src_type == "edfa" or tgt_type == "edfa"
            edfa_gain = 20.0 if has_edfa else 0.0

            # OSNR degrades per span: start at 40 dB, lose ~3dB per span
            # Chromatic dispersion: 17 ps/(nm*km) for G.652
            cd_ps_nm = round(distance_km * 17, 1)
            # PMD: 0.1 ps/sqrt(km)
            pmd_ps = round(0.1 * (distance_km**0.5), 2)

            span_quality = "good"
            if total_loss > 20:
                span_quality = "critical"
            elif total_loss > 12:
                span_quality = "warning"

            spans.append(
                {
                    "span": f"{label_map.get(e['source'], '?')} \u2192 {label_map.get(e['target'], '?')}",
                    "distance_km": distance_km,
                    "attenuation_db": attenuation_db,
                    "connector_loss_db": connector_loss * 2,
                    "total_loss_db": total_loss,
                    "edfa_gain_db": edfa_gain,
                    "net_loss_db": round(total_loss - edfa_gain, 2),
                    "chromatic_dispersion_ps_nm": cd_ps_nm,
                    "pmd_ps": pmd_ps,
                    "quality": span_quality,
                    "protocol": e.get("protocol", ""),
                }
            )

    # System-level OSNR calculation
    total_spans = len(spans)
    total_distance = sum(s["distance_km"] for s in spans)
    total_net_loss = sum(s["net_loss_db"] for s in spans)
    total_cd = sum(s["chromatic_dispersion_ps_nm"] for s in spans)
    total_pmd = round(sum(s["pmd_ps"] ** 2 for s in spans) ** 0.5, 2)  # RSS
    # OSNR: start 40dB, each span degrades ~3dB without EDFA
    edfa_count = sum(1 for n in optical_nodes if n.get("type") == "edfa")
    osnr_db = round(40 - total_spans * 3 + edfa_count * 2.5, 1)

    system_status = (
        "excellent" if osnr_db > 25 else "good" if osnr_db > 18 else "warning" if osnr_db > 12 else "critical"
    )

    return {
        "sim_type": "dwdm_optical",
        "spans": spans,
        "system": {
            "total_spans": total_spans,
            "total_distance_km": total_distance,
            "total_net_loss_db": round(total_net_loss, 2),
            "edfa_count": edfa_count,
            "osnr_db": osnr_db,
            "total_cd_ps_nm": total_cd,
            "total_pmd_ps": total_pmd,
            "status": system_status,
        },
        "summary": f"{total_spans} spans, {total_distance}km, OSNR={osnr_db}dB ({system_status}), "
        f"{edfa_count} EDFAs, CD={total_cd} ps/nm, PMD={total_pmd} ps",
    }


def _sim_fiber_budget(nodes, edges, params) -> dict:
    """Optical power budget analysis: TX power, losses, RX sensitivity, margin."""
    label_map = {n["id"]: n.get("label", n["id"]) for n in nodes}
    node_map = {n["id"]: n for n in nodes}

    OPTICAL_TYPES = {
        "roadm",
        "oadm",
        "edfa",
        "transponder",
        "olt",
        "odf",
        "media-optical",
        "media-fiber",
        "patch-panel-fiber",
        "sonet-adm",
    }
    optical_ids = {n["id"] for n in nodes if n.get("type") in OPTICAL_TYPES}

    # TX power (dBm) and RX sensitivity by node type
    TX_POWER = {"transponder": 1.0, "roadm": 0.0, "oadm": -2.0, "olt": 5.0, "sonet-adm": 0.0}
    RX_SENS = {"transponder": -28.0, "roadm": -25.0, "oadm": -25.0, "olt": -28.0, "sonet-adm": -25.0}
    # Insertion loss by node type
    INSERT_LOSS = {
        "oadm": 5.0,
        "roadm": 7.0,
        "odf": 0.5,
        "patch-panel-fiber": 0.5,
        "edfa": -20.0,
        "media-fiber": 0.3,
        "media-optical": 0.5,
    }  # EDFA is negative = gain

    # Build optical path
    adj: dict[str, list] = {}
    for e in edges:
        if e["source"] in optical_ids and e["target"] in optical_ids:
            adj.setdefault(e["source"], []).append(e["target"])
            adj.setdefault(e["target"], []).append(e["source"])

    # Find TX and RX endpoints (transponders, OLTs, or first/last optical nodes)
    tx_node = None
    rx_node = None
    for n in nodes:
        if n.get("type") in ("transponder", "olt"):
            if tx_node is None:
                tx_node = n["id"]
            else:
                rx_node = n["id"]
    if not tx_node and optical_ids:
        tx_node = list(optical_ids)[0]
    if not rx_node and len(optical_ids) > 1:
        rx_node = list(optical_ids)[-1]

    if not tx_node or not rx_node:
        return {"sim_type": "fiber_budget", "summary": "Need at least 2 optical nodes", "budget": []}

    # BFS path
    q: deque[list[str]] = deque([[tx_node]])
    visited = {tx_node}
    path: list[str] = []
    while q:
        p = q.popleft()
        if p[-1] == rx_node:
            path = p
            break
        for nb in adj.get(p[-1], []):
            if nb not in visited:
                visited.add(nb)
                q.append(p + [nb])

    if not path:
        return {"sim_type": "fiber_budget", "summary": "No optical path found between endpoints", "budget": []}

    # Walk path and compute budget
    tx_type = node_map.get(tx_node, {}).get("type", "transponder")
    rx_type = node_map.get(rx_node, {}).get("type", "transponder")
    tx_power = TX_POWER.get(tx_type, 0.0)
    rx_sensitivity = RX_SENS.get(rx_type, -25.0)

    budget_detail = []
    cumulative_loss = 0.0
    power_level = tx_power

    budget_detail.append(
        {
            "node": label_map.get(tx_node, "?"),
            "type": tx_type,
            "action": "TX Launch",
            "loss_db": 0.0,
            "power_dbm": round(power_level, 2),
        }
    )

    for nid in path[1:]:
        ntype = node_map.get(nid, {}).get("type", "")
        loss = INSERT_LOSS.get(ntype, 1.0)
        power_level -= loss  # negative loss = gain (EDFA)
        cumulative_loss += loss
        budget_detail.append(
            {
                "node": label_map.get(nid, "?"),
                "type": ntype,
                "action": "Gain" if loss < 0 else "Loss",
                "loss_db": round(loss, 2),
                "power_dbm": round(power_level, 2),
            }
        )

    margin = round(power_level - rx_sensitivity, 2)
    status = "ok" if margin > 3 else "warning" if margin > 0 else "fail"

    return {
        "sim_type": "fiber_budget",
        "tx_node": label_map.get(tx_node, "?"),
        "rx_node": label_map.get(rx_node, "?"),
        "tx_power_dbm": tx_power,
        "rx_sensitivity_dbm": rx_sensitivity,
        "total_loss_db": round(cumulative_loss, 2),
        "rx_power_dbm": round(power_level, 2),
        "margin_db": margin,
        "status": status,
        "budget_detail": budget_detail,
        "summary": f"TX={tx_power}dBm \u2192 RX={round(power_level, 2)}dBm, "
        f"Margin={margin}dB ({status.upper()}), "
        f"Sensitivity={rx_sensitivity}dBm",
    }


# ---------------------------------------------------------------------------
# Blast Radius Simulation (Zero Trust validation)
# ---------------------------------------------------------------------------

# Device types that act as security boundaries (block lateral movement)
FIREWALL_TYPES = frozenset(
    {
        "firewall",
        "aws-nfw",
        "az-fw",
        "gcp-armor",
        "oci-waf",
        "aws-waf",
        "az-nsg",
        "oci-nsg",
    }
)

# Inline security appliances that segment but don't fully block
SECURITY_ZONE_TYPES = frozenset(
    {
        "security-zone",
        "sase-pop",
    }
)


def _sim_blast_radius(nodes: list, edges: list, params: dict) -> dict:
    """Simulate attacker blast radius from a compromised device.

    BFS expansion from a source node up to N hops, stopping traversal at
    firewall/security-boundary nodes.  Firewalls themselves are marked as
    'boundary' (attacker reached the door but can't pass through).

    Returns reachable devices per hop with risk classification.
    """
    node_map = {n["id"]: n for n in nodes}
    label_map = {n["id"]: n.get("label", n["id"]) for n in nodes}
    type_map = {n["id"]: (n.get("type") or n.get("nodeType") or "unknown") for n in nodes}

    # Build undirected adjacency
    adj: dict[str, set[str]] = {}
    for e in edges:
        adj.setdefault(e["source"], set()).add(e["target"])
        adj.setdefault(e["target"], set()).add(e["source"])

    source = params.get("source", "")
    max_hops = int(params.get("max_hops", 3))

    if not source:
        # Default to first node
        source = nodes[0]["id"] if nodes else ""
    if source not in node_map:
        return {
            "sim_type": "blast_radius",
            "error": f"Source node '{source}' not found",
            "summary": "Select a device to simulate compromise",
        }

    # BFS with hop tracking, stopping at firewalls
    visited: dict[str, int] = {source: 0}  # node_id -> hop distance
    boundary_nodes: set[str] = set()  # firewalls that blocked expansion
    frontier = [source]
    hop_layers: list[list[dict]] = []  # per-hop detail

    for hop in range(1, max_hops + 1):
        next_frontier: list[str] = []
        layer_devices: list[dict] = []

        for current in frontier:
            for neighbor in adj.get(current, set()):
                if neighbor in visited:
                    continue
                visited[neighbor] = hop
                ntype = type_map.get(neighbor, "unknown")

                if ntype in FIREWALL_TYPES:
                    # Attacker reaches the firewall but cannot pass through
                    boundary_nodes.add(neighbor)
                    layer_devices.append(
                        {
                            "id": neighbor,
                            "label": label_map.get(neighbor, neighbor),
                            "type": ntype,
                            "hop": hop,
                            "status": "blocked",
                        }
                    )
                    # Do NOT add to next_frontier — traversal stops here
                elif ntype in SECURITY_ZONE_TYPES:
                    # Security zone boundary — mark but still traversable
                    layer_devices.append(
                        {
                            "id": neighbor,
                            "label": label_map.get(neighbor, neighbor),
                            "type": ntype,
                            "hop": hop,
                            "status": "zone_boundary",
                        }
                    )
                    next_frontier.append(neighbor)
                else:
                    layer_devices.append(
                        {
                            "id": neighbor,
                            "label": label_map.get(neighbor, neighbor),
                            "type": ntype,
                            "hop": hop,
                            "status": "compromised",
                        }
                    )
                    next_frontier.append(neighbor)

        if layer_devices:
            hop_layers.append({"hop": hop, "devices": layer_devices})
        frontier = next_frontier
        if not frontier:
            break

    # Collect all reachable (compromised) node labels
    compromised = [d for layer in hop_layers for d in layer["devices"] if d["status"] == "compromised"]
    blocked = [d for layer in hop_layers for d in layer["devices"] if d["status"] == "blocked"]
    zone_boundaries = [d for layer in hop_layers for d in layer["devices"] if d["status"] == "zone_boundary"]

    total_nodes = len(nodes)
    total_compromised = len(compromised) + 1  # +1 for the source
    blast_pct = round(total_compromised / max(total_nodes, 1) * 100, 1)

    # Risk rating
    if blast_pct >= 75:
        risk = "CRITICAL"
    elif blast_pct >= 50:
        risk = "HIGH"
    elif blast_pct >= 25:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    # Zero Trust score: more firewalls blocking = better segmentation
    zt_score = 0
    if total_nodes > 1:
        blocked_ratio = len(blocked) / max(len(adj.get(source, set())), 1)
        unreachable = total_nodes - len(visited)
        zt_score = round(
            min(100, (unreachable / max(total_nodes - 1, 1)) * 60 + blocked_ratio * 40),
            1,
        )

    return {
        "sim_type": "blast_radius",
        "source": label_map.get(source, source),
        "source_id": source,
        "max_hops": max_hops,
        "hop_layers": hop_layers,
        "compromised_count": total_compromised,
        "blocked_count": len(blocked),
        "zone_boundary_count": len(zone_boundaries),
        "total_nodes": total_nodes,
        "blast_pct": blast_pct,
        "risk": risk,
        "zero_trust_score": zt_score,
        "unreachable_count": total_nodes - len(visited),
        "compromised_labels": [d["label"] for d in compromised],
        "blocked_labels": [d["label"] for d in blocked],
        "summary": (
            f"Blast radius: {total_compromised}/{total_nodes} devices "
            f"({blast_pct}%) reachable within {max_hops} hops — "
            f"Risk: {risk}, ZT Score: {zt_score}/100"
        ),
    }


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------


def _run_simulation(graph: dict, sim_type: str, params: dict) -> dict:
    """Pure-Python deterministic simulation engine."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    node_ids = [n["id"] for n in nodes]

    if sim_type == "ping":
        src = params.get("source", node_ids[0] if node_ids else "?")
        dst = params.get("destination", node_ids[-1] if len(node_ids) > 1 else "?")
        hops = _bfs_path(nodes, edges, src, dst)
        latency = len(hops) * 2.3 if hops else None
        return {
            "sim_type": "ping",
            "source": src,
            "destination": dst,
            "hops": hops,
            "latency_ms": round(latency, 2) if latency else None,
            "reachable": latency is not None,
            "summary": f"{'Reachable' if latency else 'Unreachable'} \u2014 {round(latency, 1)}ms via {len(hops)} hops"
            if latency
            else "Unreachable",
        }

    if sim_type == "traceroute":
        src = params.get("source", node_ids[0] if node_ids else "?")
        dst = params.get("destination", node_ids[-1] if len(node_ids) > 1 else "?")
        hops = _bfs_path(nodes, edges, src, dst)
        trace = [{"hop": i + 1, "node": h, "latency_ms": round((i + 1) * 2.3, 2)} for i, h in enumerate(hops)]
        return {
            "sim_type": "traceroute",
            "source": src,
            "destination": dst,
            "trace": trace,
            "summary": f"{len(trace)} hops to destination",
        }

    if sim_type == "spof":
        spof_nodes = _find_spof(nodes, edges)
        return {
            "sim_type": "spof",
            "spof_nodes": spof_nodes,
            "summary": f"{len(spof_nodes)} single points of failure detected",
        }

    if sim_type == "failover":
        spof_nodes = _find_spof(nodes, edges)
        risks = [{"node": n, "impact": "High", "recommendation": "Add redundant path"} for n in spof_nodes]
        # Estimate failover time based on protocol / BFD hints
        edge_labels = " ".join((e.get("label") or "") + " " + (e.get("protocol") or "") for e in edges).upper()
        bfd_cfg = any(_get_node_config(n).get("bfd_enabled") for n in nodes)
        bfd_detected = bfd_cfg or "BFD" in edge_labels
        if bfd_detected:
            failover_est = "<1s (BFD sub-second detection)"
        elif "BGP" in edge_labels:
            failover_est = "~90s (BGP hold-timer expiry, no BFD)"
        elif "OSPF" in edge_labels:
            failover_est = "~40s (OSPF dead-interval expiry, no BFD)"
        else:
            failover_est = "unknown (no routing protocol hints on edges)"
        return {
            "sim_type": "failover",
            "risks": risks,
            "resilience_score": max(0, 100 - len(spof_nodes) * 15),
            "bfd_detected": bfd_detected,
            "failover_estimate": failover_est,
            "summary": f"Resilience score: {max(0, 100 - len(spof_nodes) * 15)}%",
        }

    if sim_type == "load":
        utilization = []
        for e in edges:
            util = round(20 + hash(e.get("id", "")) % 60, 1)
            utilization.append(
                {
                    "edge": e.get("id", ""),
                    "label": e.get("label", ""),
                    "utilization_pct": util,
                    "status": "critical" if util > 75 else "warning" if util > 50 else "ok",
                }
            )
        avg = round(sum(u["utilization_pct"] for u in utilization) / max(len(utilization), 1), 1)
        return {
            "sim_type": "load",
            "utilization": utilization,
            "avg_utilization_pct": avg,
            "summary": f"Avg utilization: {avg}%",
        }

    if sim_type == "bgp_bestpath":
        return _sim_bgp_bestpath(nodes, edges, params)

    if sim_type == "bgp_propagation":
        return _sim_bgp_propagation(nodes, edges, params)

    if sim_type == "ospf_spf":
        return _sim_ospf_spf(nodes, edges, params)

    if sim_type == "ospf_cost":
        return _sim_ospf_cost(nodes, edges, params)

    if sim_type == "jumbo_mtu":
        return _sim_jumbo_mtu(nodes, edges, params)

    if sim_type == "dwdm_optical":
        return _sim_dwdm_optical(nodes, edges, params)

    if sim_type == "fiber_budget":
        return _sim_fiber_budget(nodes, edges, params)

    if sim_type == "blast_radius":
        return _sim_blast_radius(nodes, edges, params)

    return {"sim_type": sim_type, "error": "Unknown simulation type", "summary": "N/A"}


def _add_narrative(result: dict) -> dict:
    """Add a plain-English narrative explaining the simulation result."""
    st = result.get("sim_type", "")
    lines: list[str] = []

    if st == "ping":
        if result.get("reachable"):
            lines.append("The ping test shows that traffic CAN reach the destination from the source.")
            lines.append(
                f"The packet traverses {len(result.get('hops', []))} hops with an estimated latency of {result.get('latency_ms')}ms."
            )
            lines.append("This indicates a valid forwarding path exists in the current topology.")
        else:
            lines.append("The ping test FAILED \u2014 no reachable path exists between source and destination.")
            lines.append("This means there is a routing gap, a missing link, or a firewall blocking connectivity.")
            lines.append("Recommendation: Check for disconnected segments or add a link between the isolated nodes.")

    elif st == "traceroute":
        hops = result.get("trace", [])
        lines.append(f"The traceroute reveals a {len(hops)}-hop path to the destination.")
        if hops:
            lines.append(f"Traffic enters at {hops[0].get('node', '?')} and exits at {hops[-1].get('node', '?')}.")
            lines.append(f"Total estimated latency: {hops[-1].get('latency_ms', 0)}ms across all hops.")

    elif st == "spof":
        spofs = result.get("spof_nodes", [])
        if spofs:
            lines.append(f"WARNING: {len(spofs)} single point(s) of failure detected: {', '.join(spofs[:5])}.")
            lines.append("If any of these nodes fail, part of the network becomes unreachable.")
            lines.append("Recommendation: Add redundant links or a standby device for each SPOF.")
            lines.append(
                "Note: Cloud-managed HA services (TGW, DXGW, NLB, VWAN, NCC, DRG) are excluded — their distributed backends (Hyperplane) provide built-in redundancy."
            )
        else:
            lines.append("No single points of failure detected. The topology has good redundancy.")

    elif st == "failover":
        score = result.get("resilience_score", 0)
        risks = result.get("risks", [])
        bfd = result.get("bfd_detected", False)
        fo_est = result.get("failover_estimate", "unknown")
        lines.append(
            f"Resilience score: {score}% \u2014 {'Excellent' if score >= 90 else 'Good' if score >= 70 else 'Needs improvement' if score >= 50 else 'Critical risk'}."
        )
        lines.append(f"Estimated failover time: {fo_est}.")
        if bfd:
            lines.append("BFD (Bidirectional Forwarding Detection) is active, enabling sub-second failure detection.")
        else:
            lines.append("BFD is NOT detected on any path \u2014 consider enabling it to reduce failover time to <1s.")
        if risks:
            lines.append(f"{len(risks)} high-impact risk(s) identified.")
            for r in risks[:3]:
                lines.append(f"  - {r.get('node', '?')}: {r.get('recommendation', '')}")
        lines.append(
            "Note: Cloud-managed HA services (TGW, DXGW, NLB, VWAN, NCC, DRG) are not flagged as SPOFs due to distributed backend redundancy."
        )

    elif st == "load":
        avg = result.get("avg_utilization_pct", 0)
        critical = [u for u in result.get("utilization", []) if u.get("status") == "critical"]
        lines.append(f"Average link utilization across the topology: {avg}%.")
        if critical:
            lines.append(f"ALERT: {len(critical)} link(s) above 75% utilization \u2014 at risk of congestion.")
            for c in critical[:3]:
                lines.append(f"  - {c.get('label', c.get('edge', '?'))}: {c.get('utilization_pct')}%")
            lines.append("Recommendation: Upgrade these links or redistribute traffic.")
        else:
            lines.append("All links are within acceptable utilization thresholds.")

    elif st == "bgp_bestpath":
        best = result.get("best_path", [])
        reason = result.get("decision_reason", "")
        if best:
            lines.append(f"BGP selected the best path: {' -> '.join(best)}.")
            lines.append(f"Decision criteria: {reason}.")
            paths = result.get("paths", [])
            if len(paths) > 1:
                lines.append(
                    f"{len(paths)} candidate paths were evaluated using the Cisco BGP decision process (Weight > LOCAL_PREF > AS_PATH > MED)."
                )
        else:
            lines.append("No BGP paths found. Ensure nodes have ASN configured and links use BGP protocol.")

    elif st == "ospf_spf":
        tree = result.get("spf_tree", [])
        ecmp = result.get("ecmp_nodes", [])
        lines.append(f"OSPF Shortest Path First (Dijkstra) computed from {result.get('root', '?')}.")
        lines.append(f"{len(tree)} nodes reachable in the SPF tree.")
        if ecmp:
            lines.append(
                f"Equal-Cost Multi-Path (ECMP) candidates: {', '.join(ecmp[:5])} \u2014 these can load-balance traffic."
            )

    elif st == "dwdm_optical":
        sys_data = result.get("system", {})
        lines.append(
            f"Optical path analysis: {sys_data.get('total_spans', 0)} spans over {sys_data.get('total_distance_km', 0)}km."
        )
        lines.append(f"OSNR: {sys_data.get('osnr_db', 0)}dB ({sys_data.get('status', 'unknown')}).")
        if sys_data.get("status") in ("warning", "critical"):
            lines.append(
                "The optical signal quality is degraded. Consider adding EDFA amplifiers or reducing span distances."
            )
        lines.append(
            f"Chromatic dispersion: {sys_data.get('total_cd_ps_nm', 0)} ps/nm, PMD: {sys_data.get('total_pmd_ps', 0)} ps."
        )

    elif st == "fiber_budget":
        margin = result.get("margin_db", 0)
        result.get("status", "")
        lines.append(
            f"Optical power budget: TX={result.get('tx_power_dbm', 0)}dBm, RX={result.get('rx_power_dbm', 0)}dBm."
        )
        margin_desc = (
            "Sufficient (>3dB recommended)"
            if margin > 3
            else ("Marginal \u2014 risk of signal loss" if margin > 0 else "INSUFFICIENT \u2014 link will not work")
        )
        lines.append(f"Link margin: {margin}dB \u2014 {margin_desc}.")

    elif st == "jumbo_mtu":
        if result.get("jumbo_ready"):
            lines.append(f"Jumbo frames ({result.get('desired_mtu', 9000)} bytes) are supported end-to-end.")
        else:
            lines.append(
                f"Jumbo frames BLOCKED at {result.get('bottleneck', '?')} (MTU={result.get('path_mtu', 1500)})."
            )
            lines.append("Packets larger than the path MTU will be fragmented or dropped.")
            frag = result.get("fragmentation_points", [])
            if frag:
                lines.append(f"Upgrade MTU on: {', '.join(frag[:5])}")

    elif st == "blast_radius":
        risk = result.get("risk", "?")
        blast_pct = result.get("blast_pct", 0)
        src = result.get("source", "?")
        compromised = result.get("compromised_count", 0)
        total = result.get("total_nodes", 0)
        blocked = result.get("blocked_count", 0)
        zt = result.get("zero_trust_score", 0)
        lines.append(
            f"If '{src}' is compromised, an attacker can reach "
            f"{compromised} of {total} devices ({blast_pct}%) "
            f"within {result.get('max_hops', 3)} hops."
        )
        if blocked:
            lines.append(f"{blocked} firewall(s) blocked further lateral movement.")
        if risk in ("CRITICAL", "HIGH"):
            lines.append(
                "This indicates insufficient network segmentation. "
                "Add firewalls or security zones between segments to reduce blast radius."
            )
        elif risk == "MEDIUM":
            lines.append(
                "Some segmentation exists but could be improved. "
                "Consider adding micro-segmentation per Zero Trust principles."
            )
        else:
            lines.append("Good segmentation \u2014 the blast radius is well contained.")
        lines.append(f"Zero Trust segmentation score: {zt}/100.")

    if not lines:
        lines.append(f"Simulation type '{st}' completed. See the detailed results for analysis.")

    result["narrative"] = " ".join(lines)
    return result
