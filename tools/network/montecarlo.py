# [CUI // SP-CTI]
"""ICDEV™ Network Design Canvas — Monte Carlo Simulation Engine.

Pure functions for running Monte Carlo resilience simulations against
network topologies. Models random/named failures, cascading effects,
reachability impact, and generates risk scores with recommendations.

No Flask dependency — takes scenario configs and graph data, returns results.
"""

import math
import random


def run_monte_carlo(graph: dict, scenario_name: str, scenario_type: str, config: dict, iterations: int = 1000) -> dict:
    """Run Monte Carlo simulation: N iterations of random/named failures.

    Models both traffic rerouting (OSPF/BGP reconvergence) and reachability impact.
    Returns risk score, confidence intervals, cascading effects, and recommendations.

    Args:
        graph: Dict with "nodes" and "edges" lists.
        scenario_name: Human-readable scenario name.
        scenario_type: One of "random", "named", "circuit_change".
        config: Scenario configuration dict with keys:
            - node_failure_prob (float): Per-node failure probability (default 0.02).
            - edge_failure_prob (float): Per-edge failure probability (default 0.05).
            - named_failures (list): List of {type: "node"|"edge", id: "..."} for
              named scenario type.
            - iterations (int): Override for iteration count.
        iterations: Number of Monte Carlo iterations (capped at 10000).

    Returns:
        Dict with risk_score, reachability stats, vulnerable nodes/edges,
        redundant pairs, single-link nodes, recommendations, and narrative.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    label_map = {n["id"]: n.get("label", n["id"]) for n in nodes}

    iterations = min(iterations, 10000)  # cap

    # Build adjacency
    adj = {}
    for e in edges:
        adj.setdefault(e["source"], set()).add(e["target"])
        adj.setdefault(e["target"], set()).add(e["source"])

    # Failure probabilities per component
    node_fail_prob = config.get("node_failure_prob", 0.02)
    edge_fail_prob = config.get("edge_failure_prob", 0.05)
    named_failures = config.get("named_failures", [])  # specific nodes/edges to force-fail

    # Cloud-managed services with provider-guaranteed HA (Hyperplane, distributed backends)
    # These have published SLAs of 99.99%+ and should NOT be modeled as typical hardware
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
    CLOUD_HA_FAIL_PROB = 0.001  # 99.9% per iteration (vs 0.02 default)

    # Build node type lookup
    node_types = {n["id"]: n.get("type", "") for n in nodes}

    # Run iterations
    reachability_scores = []
    cascade_counts = []
    node_failure_freq = {n["id"]: 0 for n in nodes}
    edge_failure_freq = {e.get("id", ""): 0 for e in edges}

    for _ in range(iterations):
        # Determine failures for this iteration
        failed_nodes = set()
        failed_edges = set()

        if scenario_type == "named":
            for nf in named_failures:
                if nf.get("type") == "node":
                    failed_nodes.add(nf["id"])
                elif nf.get("type") == "edge":
                    failed_edges.add(nf["id"])
        else:
            # Random failures based on probability (cloud-managed HA gets lower prob)
            for n in nodes:
                ntype = node_types.get(n["id"], "")
                prob = CLOUD_HA_FAIL_PROB if ntype in CLOUD_HA_TYPES else node_fail_prob
                if random.random() < prob:
                    failed_nodes.add(n["id"])
            for e in edges:
                if random.random() < edge_fail_prob:
                    failed_edges.add(e.get("id", ""))

        # circuit_change scenario: also model new circuit impact
        if scenario_type == "circuit_change":
            pass  # new circuit doesn't add failures, just tests existing resilience

        # Build surviving adjacency
        surv_adj = {}
        for e in edges:
            if e.get("id", "") in failed_edges:
                edge_failure_freq[e.get("id", "")] = edge_failure_freq.get(e.get("id", ""), 0) + 1
                continue
            if e["source"] in failed_nodes or e["target"] in failed_nodes:
                continue
            surv_adj.setdefault(e["source"], set()).add(e["target"])
            surv_adj.setdefault(e["target"], set()).add(e["source"])

        for nid in failed_nodes:
            node_failure_freq[nid] = node_failure_freq.get(nid, 0) + 1

        # BFS reachability from first surviving node
        surviving = [n["id"] for n in nodes if n["id"] not in failed_nodes]
        if not surviving:
            reachability_scores.append(0.0)
            cascade_counts.append(len(nodes))
            continue

        start = surviving[0]
        visited = {start}
        queue = [start]
        while queue:
            current = queue.pop(0)
            for nb in surv_adj.get(current, set()):
                if nb not in visited and nb not in failed_nodes:
                    visited.add(nb)
                    queue.append(nb)

        reach_pct = len(visited) / max(len(surviving), 1) * 100
        reachability_scores.append(reach_pct)
        isolated = len(surviving) - len(visited)
        cascade_counts.append(isolated + len(failed_nodes))

    # Statistics
    avg_reach = round(sum(reachability_scores) / max(len(reachability_scores), 1), 2)
    min_reach = round(min(reachability_scores) if reachability_scores else 0, 2)
    max_reach = round(max(reachability_scores) if reachability_scores else 0, 2)

    # Confidence intervals (95%)
    if len(reachability_scores) > 1:
        mean = sum(reachability_scores) / len(reachability_scores)
        variance = sum((x - mean) ** 2 for x in reachability_scores) / (len(reachability_scores) - 1)
        std_dev = math.sqrt(variance)
        ci_95 = round(1.96 * std_dev / math.sqrt(len(reachability_scores)), 2)
    else:
        std_dev = 0
        ci_95 = 0

    # Risk score (0-100, higher = more resilient)
    risk_score = round(avg_reach, 1)

    # Cascading effect analysis
    avg_cascade = round(sum(cascade_counts) / max(len(cascade_counts), 1), 1)

    # ── Impact-based vulnerability analysis ──────────────────────────────
    # Score each node by HOW MANY nodes become unreachable when it alone fails
    # (not random failure frequency — that just correlates with connectivity)
    node_impact = {}
    for n in nodes:
        nid = n["id"]
        # Build adjacency without this node
        test_adj = {}
        for e in edges:
            if e["source"] == nid or e["target"] == nid:
                continue
            test_adj.setdefault(e["source"], set()).add(e["target"])
            test_adj.setdefault(e["target"], set()).add(e["source"])
        # BFS from any surviving node
        surviving = [nn["id"] for nn in nodes if nn["id"] != nid]
        if not surviving:
            node_impact[nid] = len(nodes) - 1
            continue
        start = surviving[0]
        vis = {start}
        q = [start]
        while q:
            cur = q.pop(0)
            for nb in test_adj.get(cur, set()):
                if nb not in vis and nb != nid:
                    vis.add(nb)
                    q.append(nb)
        isolated = len(surviving) - len(vis)
        node_impact[nid] = isolated  # how many nodes become unreachable

    # ── Redundancy pair detection ─────────────────────────────────────────
    # Two nodes are a "redundant pair" if they share >=2 common neighbors
    redundant_pairs = set()
    node_neighbors = {}
    for e in edges:
        node_neighbors.setdefault(e["source"], set()).add(e["target"])
        node_neighbors.setdefault(e["target"], set()).add(e["source"])
    node_list = list(node_neighbors.keys())
    for i in range(len(node_list)):
        for j in range(i + 1, len(node_list)):
            a, b = node_list[i], node_list[j]
            common = node_neighbors.get(a, set()) & node_neighbors.get(b, set())
            # Also check if they're directly connected to each other
            if b in node_neighbors.get(a, set()) and len(common) >= 1:
                redundant_pairs.add((a, b))
            elif len(common) >= 2:
                redundant_pairs.add((a, b))

    redundant_node_ids = set()
    for a, b in redundant_pairs:
        redundant_node_ids.add(a)
        redundant_node_ids.add(b)

    # ── Single-link nodes (no redundant path) ─────────────────────────────
    single_link_nodes = [
        nid for nid, neighbors in node_neighbors.items() if len(neighbors) == 1 and nid not in redundant_node_ids
    ]

    # Vulnerable = high impact AND not in a redundant pair
    vuln_nodes = sorted(
        [
            (nid, label_map.get(nid, nid), node_impact.get(nid, 0), nid in redundant_node_ids)
            for nid in node_impact
            if node_impact[nid] > 0 and nid not in redundant_node_ids
        ],
        key=lambda x: -x[2],
    )[:5]

    vuln_edges = sorted(
        [(eid, freq) for eid, freq in edge_failure_freq.items() if freq > 0 and eid], key=lambda x: -x[1]
    )[:5]

    # ── AI Recommendations (context-aware) ────────────────────────────────
    recommendations = _generate_recommendations(
        single_link_nodes,
        vuln_nodes,
        redundant_pairs,
        risk_score,
        avg_cascade,
        label_map,
        nodes,
        node_types,
        CLOUD_HA_TYPES,
    )

    result = {
        "scenario_name": scenario_name,
        "scenario_type": scenario_type,
        "iterations": iterations,
        "risk_score": risk_score,
        "avg_reachability_pct": avg_reach,
        "min_reachability_pct": min_reach,
        "max_reachability_pct": max_reach,
        "std_deviation": round(std_dev, 2),
        "confidence_interval_95": ci_95,
        "avg_cascade_impact": avg_cascade,
        "vulnerable_nodes": [
            {
                "node_id": nid,
                "node": label,
                "impact": impact,
                "is_redundant": is_red,
                "type": node_types.get(nid, "unknown"),
                "cloud_managed_ha": node_types.get(nid, "") in CLOUD_HA_TYPES,
            }
            for nid, label, impact, is_red in vuln_nodes
        ],
        "redundant_pairs": [{"a": label_map.get(a, a), "b": label_map.get(b, b)} for a, b in list(redundant_pairs)[:5]],
        "single_link_nodes": [label_map.get(n, n) for n in single_link_nodes],
        "vulnerable_edges": [{"edge_id": e, "failure_count": f} for e, f in vuln_edges],
        "recommendations": recommendations,
        "graph": graph,  # full topology for visualization
    }

    # Add narrative
    result["narrative"] = _generate_narrative(
        iterations,
        scenario_type,
        risk_score,
        avg_reach,
        min_reach,
        max_reach,
        avg_cascade,
        vuln_nodes,
        single_link_nodes,
        redundant_pairs,
        recommendations,
        label_map,
    )

    return result


def _generate_recommendations(
    single_link_nodes: list,
    vuln_nodes: list,
    redundant_pairs: set,
    risk_score: float,
    avg_cascade: float,
    label_map: dict,
    nodes: list,
    node_types: dict = None,
    cloud_ha_types: set = None,
) -> list:
    """Generate context-aware resilience recommendations.

    Args:
        single_link_nodes: List of node IDs with only one connection.
        vuln_nodes: List of (node_id, label, impact, is_redundant) tuples.
        redundant_pairs: Set of (node_a, node_b) tuples.
        risk_score: Overall risk score (0-100).
        avg_cascade: Average cascade impact count.
        label_map: Dict mapping node IDs to labels.
        nodes: List of node dicts.
        node_types: Dict mapping node IDs to type strings.
        cloud_ha_types: Set of type strings considered cloud-managed HA.

    Returns:
        List of recommendation strings.
    """
    if node_types is None:
        node_types = {}
    if cloud_ha_types is None:
        cloud_ha_types = set()
    recommendations = []

    if single_link_nodes:
        single_labels = [label_map.get(n, n) for n in single_link_nodes[:4]]
        recommendations.append(
            f"Single-connected devices with no redundant path: {', '.join(single_labels)}. "
            f"Add a second uplink to protect against link failure."
        )
        # Check if any single-link nodes are switches (distribution/core — high severity)
        single_switches = [
            nid
            for nid in single_link_nodes
            if any(n.get("type", "") in ("switch-l2", "switch-l3", "router") for n in nodes if n["id"] == nid)
        ]
        if single_switches:
            sw_labels = [label_map.get(n, n) for n in single_switches[:3]]
            recommendations.append(
                f"PRIORITY: {', '.join(sw_labels)} — distribution/core device(s) with only one uplink. "
                f"Connect each to a second core switch for redundancy."
            )

    if vuln_nodes:
        top = vuln_nodes[0]
        recommendations.append(
            f"Highest impact node: {top[1]} — if it fails, {top[2]} device(s) become unreachable. "
            f"Add a standby/failover or second path around it."
        )
        # Cloud-aware recommendations for vulnerable nodes
        for nid, label, impact, _is_red in vuln_nodes:
            ntype = node_types.get(nid, "")
            if ntype in cloud_ha_types:
                recommendations.append(
                    f"{label}: Cloud-managed service ({ntype}) with provider HA — "
                    f"low actual risk despite topology position."
                )
            elif ntype in ("aws-dx", "az-er", "gcp-ic", "oci-fc"):
                recommendations.append(
                    f"{label}: Direct connect/express route with no VPN backup. Add VPN backup for DX/ER failover."
                )

    # Single-link cloud nodes — recommend CSP-specific redundancy
    if single_link_nodes and node_types:
        cloud_single = [
            (nid, node_types.get(nid, ""))
            for nid in single_link_nodes
            if node_types.get(nid, "").startswith(("aws-", "az-", "gcp-", "oci-", "ibm-"))
        ]
        csp_patterns = {
            "aws-": "AWS: use multi-AZ attachment or redundant TGW peering",
            "az-": "Azure: add second ExpressRoute circuit or VPN GW",
            "gcp-": "GCP: add redundant Cloud Interconnect or HA VPN",
            "oci-": "OCI: add second FastConnect virtual circuit",
            "ibm-": "IBM: add redundant Direct Link connection",
        }
        for nid, ntype in cloud_single:
            for prefix, pattern in csp_patterns.items():
                if ntype.startswith(prefix):
                    recommendations.append(f"{label_map.get(nid, nid)} ({ntype}): single-link cloud node — {pattern}.")
                    break

    if redundant_pairs:
        pair_labels = [f"{label_map.get(a, a)} + {label_map.get(b, b)}" for a, b in list(redundant_pairs)[:3]]
        recommendations.append(
            f"Detected redundant pairs (good): {'; '.join(pair_labels)}. "
            f"These provide failover protection — no action needed."
        )

    if risk_score < 70:
        recommendations.append(
            "CRITICAL: Topology has significant outage risk. Implement dual-homed connections at critical sites."
        )
    elif risk_score < 90:
        recommendations.append("Moderate resilience. Focus on adding redundant links to single-connected nodes.")

    if avg_cascade > 3:
        recommendations.append(
            f"Each failure cascades to {avg_cascade} devices on average. "
            f"Consider ring or mesh topology at the core/distribution layer."
        )

    if risk_score >= 95:
        recommendations.append("Excellent resilience. All critical paths have redundancy.")

    return recommendations


def _generate_narrative(
    iterations: int,
    scenario_type: str,
    risk_score: float,
    avg_reach: float,
    min_reach: float,
    max_reach: float,
    avg_cascade: float,
    vuln_nodes: list,
    single_link_nodes: list,
    redundant_pairs: set,
    recommendations: list,
    label_map: dict,
) -> str:
    """Generate human-readable narrative summary of simulation results.

    Returns:
        Narrative string summarizing the Monte Carlo simulation results.
    """
    mc_narrative = (
        f"Monte Carlo simulation ran {iterations} iterations using {scenario_type} failure model. "
        f"The topology achieved a resilience score of {risk_score}% "
        f"({'Excellent' if risk_score >= 95 else 'Good' if risk_score >= 80 else 'Fair' if risk_score >= 60 else 'Poor'}). "
        f"On average, {avg_reach:.1f}% of nodes remained reachable during failures "
        f"(worst case: {min_reach}%, best case: {max_reach}%). "
        f"Each failure event impacts an average of {avg_cascade} node(s) through cascading effects. "
    )
    if vuln_nodes:
        mc_narrative += f"Highest-impact node: {vuln_nodes[0][1]} ({vuln_nodes[0][2]} device(s) isolated if it fails). "
    if single_link_nodes:
        mc_narrative += (
            f"Single-connected (no redundancy): {', '.join(label_map.get(n, n) for n in single_link_nodes[:3])}. "
        )
    if redundant_pairs:
        mc_narrative += f"Redundant pairs detected: {len(redundant_pairs)} (these are protected). "
    if recommendations:
        mc_narrative += f"Key recommendation: {recommendations[0]}"

    return mc_narrative
