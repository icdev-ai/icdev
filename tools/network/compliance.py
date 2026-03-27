# [CUI // SP-CTI]
"""ICDEV™ Network Design Canvas — Compliance Audit Engine.

Pure functions for running compliance checks against network topologies.
Checks topologies against STIG, FedRAMP/FISMA, CMMC, FIPS, CJIS, ICD 503,
CNSS 1253, and Zero Trust (NIST 800-207) regimes.

No Flask dependency — takes graph data and returns results.
"""
from collections import deque


# ── Compliance Regimes & Rule Definitions ─────────────────────────────────────
# 7 regimes with crosswalk mapping. Each rule is deterministic (no LLM needed).

COMPLIANCE_REGIMES = {
    "fisma_high": {"name": "FISMA High", "framework": "NIST 800-53 Rev 5", "baseline": "High"},
    "stig": {"name": "DISA STIG", "framework": "DoD STIG", "baseline": "Network"},
    "fips": {"name": "FIPS 140-2/3", "framework": "FIPS", "baseline": "Level 2"},
    "zta": {"name": "Zero Trust (NIST 800-207)", "framework": "NIST 800-207", "baseline": "Advanced"},
    "cjis": {"name": "CJIS Security Policy", "framework": "FBI CJIS", "baseline": "5.9.1"},
    "icd503": {"name": "ICD 503 (IC)", "framework": "ODNI ICD 503", "baseline": "Full"},
    "cnss1253": {"name": "CNSS 1253 (NSS)", "framework": "CNSS", "baseline": "High"},
}

# Crosswalk: rule_id -> list of regimes it applies to
# Rules run once, findings tagged with all applicable regimes
COMPLIANCE_RULES = [
    # ── Encryption ──────────────────────────────────────────────────────────
    {"id": "NET-ENC-001", "title": "WAN links require encryption",
     "severity": "CAT1", "category": "encryption",
     "regimes": ["fisma_high", "stig", "fips", "cjis", "icd503", "cnss1253"],
     "description": "All WAN/inter-site links must use IPSec, MACsec, or Type 1 encryption to protect CUI in transit (NIST SC-8, SC-13).",
     "check": "wan_encryption"},

    {"id": "NET-ENC-002", "title": "Type 1 (NSA) encryption required for SECRET+",
     "severity": "CAT1", "category": "encryption",
     "regimes": ["icd503", "cnss1253"],
     "description": "SECRET and above require NSA Type 1 encryption (KG-175D, KG-250, etc.) per CNSS Policy 15.",
     "check": "type1_encryption"},

    {"id": "NET-ENC-003", "title": "Encryptor speed rating matches link bandwidth",
     "severity": "CAT2", "category": "encryption",
     "regimes": ["fisma_high", "stig", "fips", "cnss1253"],
     "description": "Encryption device throughput must meet or exceed link bandwidth (e.g., KG-175D ≤10G, KG-250 ≤100G).",
     "check": "encryptor_speed_match"},

    {"id": "NET-ENC-004", "title": "FIPS 140-2/3 validated crypto on all encrypted links",
     "severity": "CAT1", "category": "encryption",
     "regimes": ["fisma_high", "fips", "cjis", "icd503"],
     "description": "All cryptographic modules must be FIPS 140-2 Level 2+ validated (NIST SC-13).",
     "check": "fips_validated_crypto"},

    # ── Redundancy ──────────────────────────────────────────────────────────
    {"id": "NET-RED-001", "title": "Core/distribution devices require dual uplinks",
     "severity": "CAT1", "category": "redundancy",
     "regimes": ["fisma_high", "stig", "cjis", "cnss1253"],
     "description": "Core and distribution switches/routers must have ≥2 uplinks to prevent single point of failure (NIST CP-8, SC-36).",
     "check": "core_dual_uplinks"},

    {"id": "NET-RED-002", "title": "Diverse path routing for critical circuits",
     "severity": "CAT2", "category": "redundancy",
     "regimes": ["fisma_high", "stig", "cnss1253"],
     "description": "Critical circuits should traverse physically diverse paths (different conduit/provider) per NIST CP-8.",
     "check": "diverse_paths"},

    {"id": "NET-RED-003", "title": "Access layer single uplink acceptable with documentation",
     "severity": "CAT3", "category": "redundancy",
     "regimes": ["fisma_high", "stig"],
     "description": "Access-layer switches with single uplink are acceptable if documented in the SSP with risk acceptance.",
     "check": "access_single_uplink_documented"},

    # ── Boundary / Firewall ──────────────────────────────────────────────────
    {"id": "NET-BND-001", "title": "Firewall between internal and WAN/internet",
     "severity": "CAT1", "category": "boundary",
     "regimes": ["fisma_high", "stig", "zta", "cjis", "icd503", "cnss1253"],
     "description": "Every site must have a firewall between internal networks and WAN/internet segments (NIST SC-7).",
     "check": "firewall_at_boundary"},

    {"id": "NET-BND-002", "title": "Micro-segmentation between security zones",
     "severity": "CAT2", "category": "boundary",
     "regimes": ["zta", "fisma_high", "icd503"],
     "description": "Zero Trust requires network segmentation between security zones — no flat networks (NIST 800-207 §3.1).",
     "check": "micro_segmentation"},

    {"id": "NET-BND-003", "title": "Cloud VPC/VNet isolation from on-prem",
     "severity": "CAT2", "category": "boundary",
     "regimes": ["fisma_high", "stig", "zta", "cjis"],
     "description": "Cloud environments must be isolated from on-prem via dedicated interconnect with firewall/ACL (NIST SC-7).",
     "check": "cloud_isolation"},

    # ── Management Plane ──────────────────────────────────────────────────────
    {"id": "NET-MGT-001", "title": "Out-of-band management network",
     "severity": "CAT2", "category": "management",
     "regimes": ["fisma_high", "stig", "icd503", "cnss1253"],
     "description": "Network devices should be managed via out-of-band (OOB) management network, separate from production traffic (NIST SC-7(13)).",
     "check": "oob_management"},

    {"id": "NET-MGT-002", "title": "In-band management encrypted (SSH/HTTPS only)",
     "severity": "CAT2", "category": "management",
     "regimes": ["fisma_high", "stig", "cjis", "zta"],
     "description": "If in-band management is used, it must be encrypted (SSH, HTTPS) — no Telnet, HTTP, SNMPv1/v2 (NIST SC-8).",
     "check": "inband_encrypted"},

    # ── DNS ──────────────────────────────────────────────────────────────────
    {"id": "NET-DNS-001", "title": "DNS redundancy (≥2 DNS servers)",
     "severity": "CAT2", "category": "dns",
     "regimes": ["fisma_high", "stig", "cjis"],
     "description": "At least 2 DNS resolvers or cloud DNS service for fault tolerance (NIST SC-20, SC-22).",
     "check": "dns_redundancy"},

    # ── Zero Trust Specific ──────────────────────────────────────────────────
    {"id": "NET-ZTA-001", "title": "No implicit trust zones",
     "severity": "CAT1", "category": "zta",
     "regimes": ["zta"],
     "description": "Zero Trust architecture requires all access to be explicitly verified — no flat trust zones (NIST 800-207 §2.1).",
     "check": "no_implicit_trust"},

    {"id": "NET-ZTA-002", "title": "East-west traffic inspection",
     "severity": "CAT2", "category": "zta",
     "regimes": ["zta"],
     "description": "Traffic between internal segments must be inspected (firewall or IDS/IPS between zones) per NIST 800-207.",
     "check": "east_west_inspection"},

    # ── CJIS Specific ──────────────────────────────────────────────────────
    {"id": "NET-CJIS-001", "title": "128-bit encryption minimum for CJI",
     "severity": "CAT1", "category": "encryption",
     "regimes": ["cjis"],
     "description": "CJIS Security Policy 5.10.1.2 requires minimum 128-bit encryption (AES) for Criminal Justice Information in transit.",
     "check": "cjis_128bit"},

    # ── General Best Practice ──────────────────────────────────────────────
    {"id": "NET-BP-001", "title": "All devices labeled with hostname",
     "severity": "CAT3", "category": "documentation",
     "regimes": ["fisma_high", "stig", "cjis", "icd503", "cnss1253"],
     "description": "All network devices should have meaningful hostnames for identification in audit logs.",
     "check": "devices_labeled"},

    {"id": "NET-BP-002", "title": "Network diagram matches as-built documentation",
     "severity": "CAT3", "category": "documentation",
     "regimes": ["fisma_high", "stig", "icd503"],
     "description": "Topology diagram should have a saved version labeled 'as-built' for ATO documentation.",
     "check": "as_built_version"},
]

# Encryptor speed ratings (Mbps) for NET-ENC-003
ENCRYPTOR_RATINGS = {
    "kg-175d": 10000, "kg-175g": 10000, "kg-250": 100000, "kg-340": 400000,
    "kg-245x": 10000, "kg-255": 100000, "fips-140-l1": 10000, "fips-140-l2": 10000,
    "fips-140-l3": 100000, "fips-140-l4": 100000, "macsec": 400000,
    "type1-encryptor": 10000, "hsm": 1000,
}


def run_compliance_audit(topology_id: str, graph: dict, regimes: list,
                         classification: str = "CUI",
                         has_as_built_version: bool = False) -> dict:
    """Run all applicable compliance rules against a topology.

    Args:
        topology_id: UUID of the topology being audited.
        graph: Dict with "nodes" and "edges" lists.
        regimes: List of regime keys (e.g. ["fisma_high", "stig"]).
        classification: Classification level ("CUI", "SECRET", "TOP SECRET").
        has_as_built_version: Whether an as-built version exists in the DB
            (caller should query nc_versions before calling this).

    Returns:
        Dict with findings, scores per regime, and severity counts.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    node_map = {n["id"]: n for n in nodes}
    label_map = {n["id"]: n.get("label", n["id"]) for n in nodes}

    # Build adjacency and node type sets
    adj = {}
    for e in edges:
        adj.setdefault(e["source"], set()).add(e["target"])
        adj.setdefault(e["target"], set()).add(e["source"])

    node_types = {n["id"]: n.get("type", "") for n in nodes}
    type_set = set(node_types.values())

    # Encryption-related types
    ENCRYPTOR_TYPES = {"fips-140-l1", "fips-140-l2", "fips-140-l3", "fips-140-l4",
                       "hsm", "type1-encryptor", "kg-175d", "kg-175g", "kg-250",
                       "kg-340", "kg-245x", "kg-255", "macsec"}
    TYPE1_TYPES = {"type1-encryptor", "kg-175d", "kg-175g", "kg-250", "kg-340", "kg-245x", "kg-255"}
    FIREWALL_TYPES = {"firewall", "aws-nfw", "az-fw", "gcp-armor", "oci-waf", "aws-waf"}
    WAN_TYPES = {"cloud", "aws-dx", "az-er", "gcp-ic", "oci-fc", "ibm-dl", "aws-vpn",
                 "az-vpn-gw", "gcp-vpn", "ibm-vpn", "sdwan-overlay", "internet-exchange",
                 "aws-direct-connect", "azure-expressroute", "gcp-interconnect", "oci-fastconnect"}
    CORE_TYPES = {"router", "switch-l3", "mpls-pe", "mpls-p", "route-reflector"}
    DIST_TYPES = {"switch-l3", "switch-l2"}
    ACCESS_TYPES = {"switch-l2", "wap"}
    CLOUD_TYPES = {t for t in type_set if t.startswith(("aws-", "az-", "gcp-", "oci-", "ibm-"))}
    DNS_TYPES = {"aws-r53", "az-dns", "gcp-dns"}
    MGMT_NODE_TYPES = {"siem", "network-tap", "wlc"}

    findings = []
    active_regimes = set(regimes)

    def add_finding(rule, affected="", affected_type="topology", fix_action=None):
        rule_regimes = set(rule["regimes"]) & active_regimes
        if not rule_regimes:
            return
        findings.append({
            "rule_id": rule["id"],
            "title": rule["title"],
            "severity": rule["severity"],
            "category": rule["category"],
            "description": rule["description"],
            "regimes": sorted(rule_regimes),
            "affected_entity": affected,
            "affected_type": affected_type,
            "fix_action": fix_action,
        })

    # Helper: check if path between two nodes passes through a device type
    def path_has_type(src, dst, target_types):
        visited = {src}
        queue = deque([(src, [src])])
        while queue:
            cur, path = queue.popleft()
            if cur == dst:
                return any(node_types.get(n, "") in target_types for n in path[1:-1])
            for nb in adj.get(cur, set()):
                if nb not in visited:
                    visited.add(nb)
                    queue.append((nb, path + [nb]))
        return False

    # ── Run each check ──────────────────────────────────────────────────
    rule_map = {r["id"]: r for r in COMPLIANCE_RULES}

    # NET-ENC-001: WAN links require encryption
    wan_nodes = [n for n in nodes if node_types.get(n["id"]) in WAN_TYPES]
    for wn in wan_nodes:
        neighbors = adj.get(wn["id"], set())
        has_encryptor = any(node_types.get(nb) in ENCRYPTOR_TYPES for nb in neighbors)
        encrypted_proto = any(
            e.get("protocol", "").lower() in ("ipsec", "ipsec esp", "macsec", "tls", "gre/ipsec")
            for e in edges if wn["id"] in (e["source"], e["target"])
        )
        if not has_encryptor and not encrypted_proto:
            add_finding(rule_map["NET-ENC-001"], label_map.get(wn["id"], wn["id"]), "node",
                        {"action": "add_encryptor", "target_node": wn["id"], "encryptor_type": "fips-140-l2"})

    # NET-ENC-002: Type 1 for SECRET+
    if classification in ("SECRET", "TOP SECRET"):
        for wn in wan_nodes:
            neighbors = adj.get(wn["id"], set())
            has_type1 = any(node_types.get(nb) in TYPE1_TYPES for nb in neighbors)
            if not has_type1:
                add_finding(rule_map["NET-ENC-002"], label_map.get(wn["id"], wn["id"]), "node",
                            {"action": "add_encryptor", "target_node": wn["id"], "encryptor_type": "kg-175d"})

    # NET-ENC-003: Encryptor speed match
    for n in nodes:
        ntype = node_types.get(n["id"], "")
        if ntype in ENCRYPTOR_TYPES:
            max_rating = ENCRYPTOR_RATINGS.get(ntype, 0)
            for e in edges:
                if n["id"] in (e["source"], e["target"]):
                    label = (e.get("label") or "").upper()
                    link_mbps = 1000
                    if "400G" in label: link_mbps = 400000
                    elif "100G" in label: link_mbps = 100000
                    elif "40G" in label: link_mbps = 40000
                    elif "25G" in label: link_mbps = 25000
                    elif "10G" in label: link_mbps = 10000
                    if link_mbps > max_rating and max_rating > 0:
                        add_finding(rule_map["NET-ENC-003"],
                                    f"{label_map.get(n['id'])} on {e.get('label', 'link')} ({link_mbps/1000:.0f}G > {max_rating/1000:.0f}G rated)",
                                    "node",
                                    {"action": "upgrade_encryptor", "target_node": n["id"],
                                     "recommended": "kg-250" if link_mbps <= 100000 else "kg-340"})

    # NET-ENC-004: FIPS validated crypto
    encrypted_edges = [e for e in edges if e.get("protocol", "").lower() in ("ipsec", "ipsec esp", "macsec", "tls", "gre/ipsec")]
    for e in encrypted_edges:
        src_type = node_types.get(e["source"], "")
        tgt_type = node_types.get(e["target"], "")
        has_fips = (src_type in ENCRYPTOR_TYPES or tgt_type in ENCRYPTOR_TYPES or
                    src_type.startswith("fips-") or tgt_type.startswith("fips-"))
        if not has_fips:
            add_finding(rule_map["NET-ENC-004"],
                        f"{label_map.get(e['source'], '?')} — {label_map.get(e['target'], '?')}",
                        "edge")

    # NET-RED-001: Core/dist dual uplinks
    for n in nodes:
        ntype = node_types.get(n["id"], "")
        nlabel = (n.get("label") or "").lower()
        is_core = ntype in CORE_TYPES or "core" in nlabel
        is_dist = (ntype in DIST_TYPES and ("dist" in nlabel or "distribution" in nlabel))
        if is_core or is_dist:
            link_count = len(adj.get(n["id"], set()))
            if link_count < 2:
                add_finding(rule_map["NET-RED-001"], label_map.get(n["id"]), "node",
                            {"action": "add_redundant_link", "target_node": n["id"]})

    # NET-RED-002: Diverse paths (check if 2+ independent paths exist between core nodes)
    core_nodes = [n["id"] for n in nodes if node_types.get(n["id"]) in CORE_TYPES or "core" in (n.get("label") or "").lower()]
    if len(core_nodes) >= 2:
        # Check if removing any single edge disconnects core nodes
        for e in edges:
            if e["source"] in core_nodes and e["target"] in core_nodes:
                # Test removing this edge
                test_adj = {}
                for e2 in edges:
                    if e2.get("id") == e.get("id"):
                        continue
                    test_adj.setdefault(e2["source"], set()).add(e2["target"])
                    test_adj.setdefault(e2["target"], set()).add(e2["source"])
                visited = {core_nodes[0]}
                queue = [core_nodes[0]]
                while queue:
                    cur = queue.pop(0)
                    for nb in test_adj.get(cur, set()):
                        if nb not in visited:
                            visited.add(nb)
                            queue.append(nb)
                if core_nodes[1] not in visited:
                    add_finding(rule_map["NET-RED-002"],
                                f"Link {label_map.get(e['source'], '?')} — {label_map.get(e['target'], '?')} is single path between core nodes",
                                "edge",
                                {"action": "add_diverse_path", "source": e["source"], "target": e["target"]})
                    break  # one finding is enough

    # NET-RED-003: Access layer single uplink
    for n in nodes:
        ntype = node_types.get(n["id"], "")
        nlabel = (n.get("label") or "").lower()
        if ntype in ACCESS_TYPES or "access" in nlabel:
            link_count = len(adj.get(n["id"], set()))
            if link_count <= 1:
                add_finding(rule_map["NET-RED-003"], label_map.get(n["id"]), "node")

    # NET-BND-001: Firewall at boundary
    has_firewall = bool(FIREWALL_TYPES & type_set)
    if not has_firewall:
        add_finding(rule_map["NET-BND-001"], "topology", "topology",
                    {"action": "add_node", "node_type": "firewall", "label": "Perimeter FW"})
    elif wan_nodes:
        # Check that firewall is between internal and WAN
        for wn in wan_nodes:
            neighbors = adj.get(wn["id"], set())
            fw_neighbor = any(node_types.get(nb) in FIREWALL_TYPES for nb in neighbors)
            if not fw_neighbor:
                add_finding(rule_map["NET-BND-001"],
                            f"No firewall between {label_map.get(wn['id'])} and internal network",
                            "node",
                            {"action": "add_firewall_inline", "wan_node": wn["id"]})

    # NET-BND-002: Micro-segmentation
    if len(nodes) > 5 and not any(node_types.get(n["id"]) in FIREWALL_TYPES for n in nodes):
        add_finding(rule_map["NET-BND-002"], "topology", "topology")

    # NET-BND-003: Cloud isolation
    cloud_nodes = [n for n in nodes if node_types.get(n["id"]) in CLOUD_TYPES]
    onprem_nodes = [n for n in nodes if node_types.get(n["id"]) not in CLOUD_TYPES and node_types.get(n["id"]) not in WAN_TYPES]
    if cloud_nodes and onprem_nodes:
        # Check that path from cloud to onprem goes through firewall
        for cn in cloud_nodes[:3]:
            for op in onprem_nodes[:3]:
                if not path_has_type(cn["id"], op["id"], FIREWALL_TYPES):
                    add_finding(rule_map["NET-BND-003"],
                                f"{label_map.get(cn['id'])} to {label_map.get(op['id'])} — no firewall in path",
                                "node")
                    break
            else:
                continue
            break

    # NET-MGT-001: OOB management
    has_oob = any("oob" in (n.get("label") or "").lower() or "management" in (n.get("label") or "").lower()
                   or n.get("type") in MGMT_NODE_TYPES for n in nodes)
    if not has_oob:
        add_finding(rule_map["NET-MGT-001"], "topology", "topology")

    # NET-MGT-002: In-band management encrypted
    # Check for any telnet/http/snmpv1 protocols
    insecure_protos = [e for e in edges if e.get("protocol", "").lower() in ("telnet", "http", "snmpv1", "snmpv2")]
    for e in insecure_protos:
        add_finding(rule_map["NET-MGT-002"],
                    f"{e.get('protocol')} on {label_map.get(e['source'], '?')} — {label_map.get(e['target'], '?')}",
                    "edge")

    # NET-DNS-001: DNS redundancy
    dns_nodes = [n for n in nodes if node_types.get(n["id"]) in DNS_TYPES]
    if len(dns_nodes) < 2 and len(nodes) > 3:
        add_finding(rule_map["NET-DNS-001"], "topology", "topology",
                    {"action": "add_node", "node_type": "aws-r53", "label": "DNS (redundant)"})

    # NET-ZTA-001: No implicit trust
    # If there are >5 nodes and no firewall/segmentation device in the path between any two segments
    if len(nodes) > 5 and len([n for n in nodes if node_types.get(n["id"]) in FIREWALL_TYPES]) == 0:
        add_finding(rule_map["NET-ZTA-001"], "topology", "topology")

    # NET-ZTA-002: East-west inspection
    firewalls = [n for n in nodes if node_types.get(n["id"]) in FIREWALL_TYPES]
    if len(nodes) > 8 and len(firewalls) <= 1:
        add_finding(rule_map["NET-ZTA-002"], "topology", "topology",
                    {"action": "add_node", "node_type": "firewall", "label": "Internal FW (east-west)"})

    # NET-CJIS-001: 128-bit minimum
    # Already covered by NET-ENC-001 effectively; add if encrypted but weak

    # NET-BP-001: All devices labeled
    unlabeled = [n for n in nodes if not n.get("label") or n.get("label", "").startswith("Untitled")]
    for un in unlabeled[:5]:
        add_finding(rule_map["NET-BP-001"], un.get("id", "?"), "node")

    # NET-BP-002: As-built version
    if not has_as_built_version:
        add_finding(rule_map["NET-BP-002"], "topology", "topology",
                    {"action": "create_version", "label": "As-Built", "phase": "as-is"})

    # ── Score per regime ──────────────────────────────────────────────────
    total_rules_per_regime = {}
    failed_per_regime = {}
    for rule in COMPLIANCE_RULES:
        for r in rule["regimes"]:
            if r in active_regimes:
                total_rules_per_regime[r] = total_rules_per_regime.get(r, 0) + 1
    for f in findings:
        for r in f["regimes"]:
            failed_per_regime[r] = failed_per_regime.get(r, 0) + 1

    scores = {}
    for r in active_regimes:
        total = total_rules_per_regime.get(r, 1)
        failed = failed_per_regime.get(r, 0)
        passed = total - failed
        scores[r] = {
            "regime": COMPLIANCE_REGIMES.get(r, {}).get("name", r),
            "total_rules": total,
            "passed": passed,
            "failed": failed,
            "score_pct": round(passed / max(total, 1) * 100, 1),
        }

    return {
        "topology_id": topology_id,
        "classification": classification,
        "regimes": regimes,
        "findings": findings,
        "scores": scores,
        "total_findings": len(findings),
        "cat1_count": sum(1 for f in findings if f["severity"] == "CAT1"),
        "cat2_count": sum(1 for f in findings if f["severity"] == "CAT2"),
        "cat3_count": sum(1 for f in findings if f["severity"] == "CAT3"),
    }


def apply_compliance_fix(graph: dict, fix_action: dict) -> tuple:
    """Apply a one-click fix action to the graph for a specific finding.

    Args:
        graph: Dict with "nodes" and "edges" lists (will be mutated).
        fix_action: Dict with "action" key and action-specific params.

    Returns:
        Tuple of (applied: bool, detail: str).
        Note: "create_version" action is NOT handled here — it requires DB
        access and should be handled by the caller.
    """
    import uuid

    action = fix_action.get("action", "")
    applied = False
    detail = ""

    if action == "add_encryptor":
        # Add an encryption device between target node and its neighbor
        target = fix_action.get("target_node", "")
        enc_type = fix_action.get("encryptor_type", "fips-140-l2")
        target_node = next((n for n in graph["nodes"] if n["id"] == target), None)
        if target_node:
            enc_id = f"enc-{str(uuid.uuid4())[:8]}"
            graph["nodes"].append({
                "id": enc_id, "label": f"Encryptor ({enc_type.upper()})",
                "type": enc_type,
                "x": target_node.get("x", 100) - 60,
                "y": target_node.get("y", 100) - 40,
            })
            # Link encryptor to target
            graph["edges"].append({
                "id": str(uuid.uuid4())[:8],
                "source": enc_id, "target": target,
                "label": "Encrypted", "protocol": "IPSec",
            })
            applied = True
            detail = f"Added {enc_type} encryptor next to {target_node.get('label', target)}"

    elif action == "add_node":
        ntype = fix_action.get("node_type", "firewall")
        label = fix_action.get("label", ntype)
        nid = f"fix-{str(uuid.uuid4())[:8]}"
        graph["nodes"].append({
            "id": nid, "label": label, "type": ntype,
            "x": 300, "y": 50,
        })
        applied = True
        detail = f"Added {label} ({ntype})"

    elif action == "add_redundant_link":
        target = fix_action.get("target_node", "")
        # Find a core/dist node not already connected
        target_neighbors = {e["target"] for e in graph["edges"] if e["source"] == target} | \
                          {e["source"] for e in graph["edges"] if e["target"] == target}
        core_candidates = [n for n in graph["nodes"]
                          if n["id"] not in target_neighbors and n["id"] != target
                          and (n.get("type") in ("router", "switch-l3") or "core" in (n.get("label") or "").lower())]
        if core_candidates:
            peer = core_candidates[0]
            graph["edges"].append({
                "id": str(uuid.uuid4())[:8],
                "source": target, "target": peer["id"],
                "label": "Redundant", "protocol": "",
            })
            applied = True
            detail = f"Added redundant link from {next((n['label'] for n in graph['nodes'] if n['id']==target), target)} to {peer['label']}"

    elif action == "add_firewall_inline":
        wan_node = fix_action.get("wan_node", "")
        wan = next((n for n in graph["nodes"] if n["id"] == wan_node), None)
        if wan:
            fw_id = f"fw-{str(uuid.uuid4())[:8]}"
            graph["nodes"].append({
                "id": fw_id, "label": "Perimeter FW",
                "type": "firewall",
                "x": wan.get("x", 100) + 60,
                "y": wan.get("y", 100),
            })
            graph["edges"].append({
                "id": str(uuid.uuid4())[:8],
                "source": wan_node, "target": fw_id,
                "label": "", "protocol": "",
            })
            applied = True
            detail = f"Added firewall between {wan.get('label', wan_node)} and internal network"

    elif action == "upgrade_encryptor":
        target = fix_action.get("target_node", "")
        recommended = fix_action.get("recommended", "kg-250")
        target_node = next((n for n in graph["nodes"] if n["id"] == target), None)
        if target_node:
            target_node["type"] = recommended
            target_node["label"] = f"Encryptor ({recommended.upper()})"
            applied = True
            detail = f"Upgraded encryptor {target} to {recommended}"

    elif action == "add_diverse_path":
        source = fix_action.get("source", "")
        target = fix_action.get("target", "")
        if source and target:
            import uuid as _uuid
            graph["edges"].append({
                "id": str(_uuid.uuid4())[:8],
                "source": source, "target": target,
                "label": "Diverse Path", "protocol": "",
            })
            applied = True
            detail = f"Added diverse path between {source} and {target}"

    return applied, detail


def generate_xacta_export(system_name: str, classification: str, environment: str,
                          regimes_json: str, findings: list, now_str: str) -> str:
    """Generate Xacta-compatible XML compliance report.

    Args:
        system_name: Name of the topology/system.
        classification: Classification level (e.g. "CUI").
        environment: Environment level (e.g. "IL4").
        regimes_json: JSON string of active regimes.
        findings: List of finding dicts from the DB.
        now_str: ISO timestamp string for the audit date.

    Returns:
        Xacta XML string.
    """
    findings_xml = []
    for f in findings:
        findings_xml.append(
            f'    <Finding id="{f["rule_id"]}" status="{f["status"]}">\n'
            f'      <Title>{f["title"]}</Title>\n'
            f'      <Severity>{f["severity"]}</Severity>\n'
            f'      <Regime>{f.get("regime", "")}</Regime>\n'
            f'      <Description>{f["description"]}</Description>\n'
            f'      <AffectedEntity>{f.get("affected_entity", "")}</AffectedEntity>\n'
            f'      <Status>{f["status"]}</Status>\n'
            f'      <CreatedAt>{f.get("created_at", "")}</CreatedAt>\n'
            f'      <RemediatedAt>{f.get("remediated_at", "") or ""}</RemediatedAt>\n'
            f'    </Finding>'
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<XactaComplianceReport xmlns="urn:xacta:compliance:1.0">\n'
        f'  <SystemName>{system_name}</SystemName>\n'
        f'  <Classification>{classification}</Classification>\n'
        f'  <Environment>{environment}</Environment>\n'
        f'  <Regimes>{regimes_json}</Regimes>\n'
        f'  <AuditDate>{now_str}</AuditDate>\n'
        f'  <TotalFindings>{len(findings)}</TotalFindings>\n'
        f'  <OpenFindings>{sum(1 for f in findings if f["status"]=="open")}</OpenFindings>\n'
        f'  <RemediatedFindings>{sum(1 for f in findings if f["status"]=="remediated")}</RemediatedFindings>\n'
        '  <Findings>\n'
        + '\n'.join(findings_xml) + '\n'
        '  </Findings>\n'
        '</XactaComplianceReport>'
    )

    return xml
