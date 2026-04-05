# [CUI // SP-CTI]
"""ICDEV Network Design Canvas — Intent-Based Validation Engine.

Pure functions for validating network topologies against user-defined
intent constraints (bandwidth thresholds, redundancy requirements,
isolation policies, latency bounds, encryption mandates).

Extends the compliance engine with custom, topology-specific policies
that users define through the UI. No Flask dependency.
"""

from collections import deque


# ── Constraint Types ──────────────────────────────────────────────────────────

CONSTRAINT_TYPES = {
    "bandwidth": {
        "name": "Bandwidth Threshold",
        "description": "Enforce minimum bandwidth on links matching a selector.",
        "rule_schema": {
            "min_bandwidth_mbps": "int — minimum link bandwidth in Mbps",
            "selector": "str — node type, label pattern, or 'all'",
        },
    },
    "redundancy": {
        "name": "Redundancy Requirement",
        "description": "Enforce minimum link count (uplinks) for selected nodes.",
        "rule_schema": {
            "min_links": "int — minimum number of connections",
            "selector": "str — node type, label pattern, or 'all_critical'",
        },
    },
    "isolation": {
        "name": "Isolation Policy",
        "description": "Require firewall/segmentation between two zones or node groups.",
        "rule_schema": {
            "zone_a": "str — zone label, node type, or boundary ID",
            "zone_b": "str — zone label, node type, or boundary ID",
            "require_firewall": "bool — must have firewall in path (default true)",
        },
    },
    "latency": {
        "name": "Latency Bound",
        "description": "Enforce maximum hop count between selected endpoints.",
        "rule_schema": {
            "max_hops": "int — maximum hop count between source and target",
            "source_selector": "str — node type or label pattern",
            "target_selector": "str — node type or label pattern",
        },
    },
    "encryption": {
        "name": "Encryption Mandate",
        "description": "Require encryption on all links matching a selector.",
        "rule_schema": {
            "selector": "str — 'all_wan', 'all_cross_zone', or node type",
            "min_level": "str — fips-140-l2, type1, any (default: any)",
        },
    },
    "custom": {
        "name": "Custom Constraint",
        "description": "User-defined constraint with a check expression.",
        "rule_schema": {
            "check": "str — built-in check name (e.g. 'no_single_point_of_failure')",
            "params": "dict — check-specific parameters",
        },
    },
}

# ── Node type sets (mirrored from compliance.py for independence) ─────────────

_FIREWALL_TYPES = {
    "firewall",
    "aws-nfw",
    "az-fw",
    "gcp-armor",
    "oci-waf",
    "aws-waf",
}
_ENCRYPTOR_TYPES = {
    "fips-140-l1",
    "fips-140-l2",
    "fips-140-l3",
    "fips-140-l4",
    "hsm",
    "type1-encryptor",
    "kg-175d",
    "kg-175g",
    "kg-250",
    "kg-340",
    "kg-245x",
    "kg-255",
    "macsec",
}
_TYPE1_TYPES = {
    "type1-encryptor",
    "kg-175d",
    "kg-175g",
    "kg-250",
    "kg-340",
    "kg-245x",
    "kg-255",
}
_WAN_TYPES = {
    "cloud",
    "aws-dx",
    "az-er",
    "gcp-ic",
    "oci-fc",
    "ibm-dl",
    "aws-vpn",
    "az-vpn-gw",
    "gcp-vpn",
    "ibm-vpn",
    "sdwan-overlay",
    "internet-exchange",
}
_CORE_TYPES = {"router", "switch-l3", "mpls-pe", "mpls-p", "route-reflector"}
_ENCRYPTED_PROTOS = {
    "ipsec",
    "ipsec esp",
    "gre/ipsec",
    "mtls",
    "tls",
    "dtls",
    "macsec",
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_bandwidth_label(label: str) -> int:
    """Extract bandwidth in Mbps from an edge label like '10G', '100M'."""
    label = (label or "").upper().strip()
    if "400G" in label:
        return 400_000
    if "100G" in label:
        return 100_000
    if "40G" in label:
        return 40_000
    if "25G" in label:
        return 25_000
    if "10G" in label:
        return 10_000
    if "1G" in label:
        return 1_000
    if "100M" in label:
        return 100
    if "10M" in label:
        return 10
    return 0


def _matches_selector(node: dict, selector: str, node_types: dict) -> bool:
    """Check if a node matches a selector string."""
    if not selector or selector == "all":
        return True
    sel = selector.lower()
    ntype = node_types.get(node["id"], "").lower()
    nlabel = (node.get("label") or "").lower()

    if sel == "all_critical":
        return ntype in _CORE_TYPES or "core" in nlabel or "dist" in nlabel
    if sel.startswith("type:"):
        return ntype == sel[5:]
    if sel.startswith("label:"):
        return sel[6:] in nlabel
    # Direct type or substring match
    return ntype == sel or sel in nlabel or sel in ntype


def _build_adjacency(edges: list) -> dict:
    """Build bidirectional adjacency map from edge list."""
    adj = {}
    for e in edges:
        adj.setdefault(e["source"], set()).add(e["target"])
        adj.setdefault(e["target"], set()).add(e["source"])
    return adj


def _shortest_hop_count(adj: dict, src: str, dst: str) -> int:
    """BFS shortest hop count between two nodes. Returns -1 if unreachable."""
    if src == dst:
        return 0
    visited = {src}
    queue = deque([(src, 0)])
    while queue:
        cur, hops = queue.popleft()
        for nb in adj.get(cur, set()):
            if nb == dst:
                return hops + 1
            if nb not in visited:
                visited.add(nb)
                queue.append((nb, hops + 1))
    return -1


def _path_has_firewall(adj: dict, node_types: dict, src: str, dst: str) -> bool:
    """Check if any path between src and dst traverses a firewall node."""
    visited = {src}
    queue = deque([(src, [src])])
    while queue:
        cur, path = queue.popleft()
        if cur == dst:
            return any(node_types.get(n, "") in _FIREWALL_TYPES for n in path[1:-1])
        for nb in adj.get(cur, set()):
            if nb not in visited:
                visited.add(nb)
                queue.append((nb, path + [nb]))
    return False


# ── Per-constraint-type validators ────────────────────────────────────────────


def _check_bandwidth(rule: dict, nodes: list, edges: list, node_types: dict, adj: dict, label_map: dict) -> list:
    """Validate bandwidth threshold constraint."""
    violations = []
    min_bw = rule.get("min_bandwidth_mbps", 0)
    selector = rule.get("selector", "all")

    selected_ids = {n["id"] for n in nodes if _matches_selector(n, selector, node_types)}
    if not selected_ids:
        return violations

    for e in edges:
        src, tgt = e["source"], e["target"]
        if src not in selected_ids and tgt not in selected_ids:
            continue
        link_bw = _parse_bandwidth_label(e.get("label", ""))
        if link_bw == 0:
            # Unknown bandwidth — flag if we can't determine
            link_bw_str = e.get("label", "") or "unlabeled"
            violations.append(
                {
                    "constraint_type": "bandwidth",
                    "severity": "CAT3",
                    "message": f"Link {label_map.get(src, src)} \u2014 {label_map.get(tgt, tgt)} "
                    f"has no parseable bandwidth label ({link_bw_str}), "
                    f"required \u2265{min_bw} Mbps",
                    "affected_entity": f"{label_map.get(src, src)} \u2014 {label_map.get(tgt, tgt)}",
                    "affected_type": "edge",
                }
            )
        elif link_bw < min_bw:
            violations.append(
                {
                    "constraint_type": "bandwidth",
                    "severity": "CAT2",
                    "message": f"Link {label_map.get(src, src)} \u2014 {label_map.get(tgt, tgt)} "
                    f"bandwidth {link_bw} Mbps < required {min_bw} Mbps",
                    "affected_entity": f"{label_map.get(src, src)} \u2014 {label_map.get(tgt, tgt)}",
                    "affected_type": "edge",
                }
            )
    return violations


def _check_redundancy(rule: dict, nodes: list, edges: list, node_types: dict, adj: dict, label_map: dict) -> list:
    """Validate redundancy requirement constraint."""
    violations = []
    min_links = rule.get("min_links", 2)
    selector = rule.get("selector", "all_critical")

    for n in nodes:
        if not _matches_selector(n, selector, node_types):
            continue
        link_count = len(adj.get(n["id"], set()))
        if link_count < min_links:
            violations.append(
                {
                    "constraint_type": "redundancy",
                    "severity": "CAT1" if link_count == 0 else "CAT2",
                    "message": f"{label_map.get(n['id'], n['id'])} has {link_count} link(s), "
                    f"required \u2265{min_links}",
                    "affected_entity": label_map.get(n["id"], n["id"]),
                    "affected_type": "node",
                }
            )
    return violations


def _check_isolation(rule: dict, nodes: list, edges: list, node_types: dict, adj: dict, label_map: dict) -> list:
    """Validate isolation policy constraint."""
    violations = []
    zone_a = rule.get("zone_a", "")
    zone_b = rule.get("zone_b", "")
    require_fw = rule.get("require_firewall", True)

    if not zone_a or not zone_b:
        return violations

    zone_a_ids = {n["id"] for n in nodes if _matches_selector(n, zone_a, node_types)}
    zone_b_ids = {n["id"] for n in nodes if _matches_selector(n, zone_b, node_types)}

    if not zone_a_ids or not zone_b_ids:
        return violations

    # Check if there's a direct edge between the zones without a firewall
    for a_id in zone_a_ids:
        for b_id in zone_b_ids:
            # Check direct connection
            if b_id in adj.get(a_id, set()):
                if require_fw:
                    # Direct link with no firewall in between
                    violations.append(
                        {
                            "constraint_type": "isolation",
                            "severity": "CAT1",
                            "message": f"Direct link between {label_map.get(a_id, a_id)} "
                            f"(zone: {zone_a}) and {label_map.get(b_id, b_id)} "
                            f"(zone: {zone_b}) without firewall",
                            "affected_entity": f"{label_map.get(a_id, a_id)} \u2014 {label_map.get(b_id, b_id)}",
                            "affected_type": "edge",
                        }
                    )
            elif adj.get(a_id) and adj.get(b_id):
                # Multi-hop path — check if firewall is in the path
                if require_fw and not _path_has_firewall(adj, node_types, a_id, b_id):
                    violations.append(
                        {
                            "constraint_type": "isolation",
                            "severity": "CAT1",
                            "message": f"Path from {label_map.get(a_id, a_id)} (zone: {zone_a}) "
                            f"to {label_map.get(b_id, b_id)} (zone: {zone_b}) "
                            f"has no firewall",
                            "affected_entity": f"{label_map.get(a_id, a_id)} \u2014 {label_map.get(b_id, b_id)}",
                            "affected_type": "edge",
                        }
                    )
    return violations


def _check_latency(rule: dict, nodes: list, edges: list, node_types: dict, adj: dict, label_map: dict) -> list:
    """Validate latency bound (max hop count) constraint."""
    violations = []
    max_hops = rule.get("max_hops", 5)
    src_sel = rule.get("source_selector", "all")
    tgt_sel = rule.get("target_selector", "all")

    src_ids = {n["id"] for n in nodes if _matches_selector(n, src_sel, node_types)}
    tgt_ids = {n["id"] for n in nodes if _matches_selector(n, tgt_sel, node_types)}

    checked = set()
    for s in src_ids:
        for t in tgt_ids:
            if s == t:
                continue
            pair = tuple(sorted([s, t]))
            if pair in checked:
                continue
            checked.add(pair)
            hops = _shortest_hop_count(adj, s, t)
            if hops < 0:
                violations.append(
                    {
                        "constraint_type": "latency",
                        "severity": "CAT1",
                        "message": f"No path between {label_map.get(s, s)} and {label_map.get(t, t)} (unreachable)",
                        "affected_entity": f"{label_map.get(s, s)} \u2014 {label_map.get(t, t)}",
                        "affected_type": "edge",
                    }
                )
            elif hops > max_hops:
                violations.append(
                    {
                        "constraint_type": "latency",
                        "severity": "CAT2",
                        "message": f"Path {label_map.get(s, s)} \u2192 {label_map.get(t, t)} "
                        f"is {hops} hops (max {max_hops})",
                        "affected_entity": f"{label_map.get(s, s)} \u2014 {label_map.get(t, t)}",
                        "affected_type": "edge",
                    }
                )
    return violations


def _check_encryption(rule: dict, nodes: list, edges: list, node_types: dict, adj: dict, label_map: dict) -> list:
    """Validate encryption mandate constraint."""
    violations = []
    selector = rule.get("selector", "all_wan")
    min_level = rule.get("min_level", "any")

    enc_node_ids = {n["id"] for n in nodes if node_types.get(n["id"]) in _ENCRYPTOR_TYPES}

    for e in edges:
        src, tgt = e["source"], e["target"]
        proto = (e.get("protocol") or "").lower()

        # Determine if this edge matches the selector
        match = False
        if selector == "all_wan":
            match = node_types.get(src) in _WAN_TYPES or node_types.get(tgt) in _WAN_TYPES
        elif selector == "all_cross_zone":
            # Different node types = different zones (simplified)
            match = node_types.get(src) != node_types.get(tgt)
        elif selector == "all":
            match = True
        else:
            match = _matches_selector(
                {"id": src, "label": label_map.get(src, src)}, selector, node_types
            ) or _matches_selector({"id": tgt, "label": label_map.get(tgt, tgt)}, selector, node_types)

        if not match:
            continue

        has_enc_proto = proto in _ENCRYPTED_PROTOS
        has_enc_device = src in enc_node_ids or tgt in enc_node_ids
        has_adjacent_enc = bool((adj.get(src, set()) & enc_node_ids) or (adj.get(tgt, set()) & enc_node_ids))

        if not has_enc_proto and not has_enc_device and not has_adjacent_enc:
            violations.append(
                {
                    "constraint_type": "encryption",
                    "severity": "CAT1",
                    "message": f"Link {label_map.get(src, src)} \u2014 {label_map.get(tgt, tgt)} "
                    f"is not encrypted (selector: {selector})",
                    "affected_entity": f"{label_map.get(src, src)} \u2014 {label_map.get(tgt, tgt)}",
                    "affected_type": "edge",
                }
            )
        elif min_level == "type1" and has_enc_device:
            # Check if the encryptor is Type 1
            relevant = (
                (enc_node_ids & {src, tgt})
                or (enc_node_ids & adj.get(src, set()))
                or (enc_node_ids & adj.get(tgt, set()))
            )
            if relevant and not any(node_types.get(eid) in _TYPE1_TYPES for eid in relevant):
                violations.append(
                    {
                        "constraint_type": "encryption",
                        "severity": "CAT2",
                        "message": f"Link {label_map.get(src, src)} \u2014 {label_map.get(tgt, tgt)} "
                        f"encryption is not Type 1 (required by policy)",
                        "affected_entity": f"{label_map.get(src, src)} \u2014 {label_map.get(tgt, tgt)}",
                        "affected_type": "edge",
                    }
                )
    return violations


def _check_custom(rule: dict, nodes: list, edges: list, node_types: dict, adj: dict, label_map: dict) -> list:
    """Validate custom constraint (built-in named checks)."""
    violations = []
    check = rule.get("check", "")
    params = rule.get("params", {})

    if check == "no_single_point_of_failure":
        # Every node with degree >= 2 should not be a bridge
        for n in nodes:
            nid = n["id"]
            neighbors = adj.get(nid, set())
            if len(neighbors) < 2:
                continue
            # Test if removing this node disconnects the graph
            remaining = {nd["id"] for nd in nodes if nd["id"] != nid}
            if not remaining:
                continue
            start = next(iter(remaining))
            visited = {start}
            queue = deque([start])
            while queue:
                cur = queue.popleft()
                for nb in adj.get(cur, set()):
                    if nb != nid and nb not in visited:
                        visited.add(nb)
                        queue.append(nb)
            if visited != remaining:
                violations.append(
                    {
                        "constraint_type": "custom",
                        "severity": "CAT1",
                        "message": f"{label_map.get(nid, nid)} is a single point of failure "
                        f"(removing it disconnects the topology)",
                        "affected_entity": label_map.get(nid, nid),
                        "affected_type": "node",
                    }
                )

    elif check == "max_node_count":
        max_count = params.get("max", 50)
        if len(nodes) > max_count:
            violations.append(
                {
                    "constraint_type": "custom",
                    "severity": "CAT3",
                    "message": f"Topology has {len(nodes)} nodes (max {max_count})",
                    "affected_entity": "topology",
                    "affected_type": "topology",
                }
            )

    elif check == "required_node_type":
        required_type = params.get("type", "")
        min_count = params.get("min_count", 1)
        actual = sum(1 for n in nodes if node_types.get(n["id"]) == required_type)
        if actual < min_count:
            violations.append(
                {
                    "constraint_type": "custom",
                    "severity": "CAT2",
                    "message": f"Topology has {actual} node(s) of type '{required_type}' (required \u2265{min_count})",
                    "affected_entity": "topology",
                    "affected_type": "topology",
                }
            )

    return violations


# ── Dispatcher ────────────────────────────────────────────────────────────────

_CHECKERS = {
    "bandwidth": _check_bandwidth,
    "redundancy": _check_redundancy,
    "isolation": _check_isolation,
    "latency": _check_latency,
    "encryption": _check_encryption,
    "custom": _check_custom,
}


# ── Main validation entry point ──────────────────────────────────────────────


def validate_intent_policy(
    topology_id: str,
    graph: dict,
    constraints: list,
    policy_name: str = "",
) -> dict:
    """Validate a topology against a list of intent constraints.

    Args:
        topology_id: UUID of the topology.
        graph: Dict with "nodes" and "edges" lists.
        constraints: List of constraint dicts, each with:
            - constraint_type: one of CONSTRAINT_TYPES keys
            - rule_json: dict with type-specific parameters
            - severity: override severity (optional)
            - description: human label (optional)
        policy_name: Name of the policy for reporting.

    Returns:
        Dict with violations, pass/fail counts, and overall status.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    node_types = {n["id"]: n.get("type", "") for n in nodes}
    label_map = {n["id"]: n.get("label", n["id"]) for n in nodes}
    adj = _build_adjacency(edges)

    all_violations = []
    passed = 0
    failed = 0

    for constraint in constraints:
        ctype = constraint.get("constraint_type", "")
        rule = constraint.get("rule_json", {})
        if isinstance(rule, str):
            import json

            try:
                rule = json.loads(rule)
            except Exception:
                rule = {}

        checker = _CHECKERS.get(ctype)
        if not checker:
            continue

        violations = checker(rule, nodes, edges, node_types, adj, label_map)

        # Override severity if specified on the constraint
        override_sev = constraint.get("severity")
        if override_sev and violations:
            for v in violations:
                v["severity"] = override_sev

        # Tag violations with constraint metadata
        desc = constraint.get("description", "")
        cid = constraint.get("id", "")
        for v in violations:
            v["constraint_id"] = cid
            v["constraint_description"] = desc

        if violations:
            failed += 1
            all_violations.extend(violations)
        else:
            passed += 1

    total = passed + failed
    status = "PASS" if failed == 0 else "FAIL"
    score_pct = round(passed / max(total, 1) * 100, 1)

    return {
        "topology_id": topology_id,
        "policy_name": policy_name,
        "total_constraints": total,
        "passed": passed,
        "failed": failed,
        "score_pct": score_pct,
        "status": status,
        "violations": all_violations,
        "cat1_count": sum(1 for v in all_violations if v.get("severity") == "CAT1"),
        "cat2_count": sum(1 for v in all_violations if v.get("severity") == "CAT2"),
        "cat3_count": sum(1 for v in all_violations if v.get("severity") == "CAT3"),
    }
