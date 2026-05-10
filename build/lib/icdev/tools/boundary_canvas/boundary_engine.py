# CUI // SP-CTI — ICDEV Boundary Design Canvas Engine
"""Boundary Design Canvas — Deterministic Assessment Engine.

Pure functions for boundary compliance checking, ISA lifecycle validation,
PPS matrix generation, and gap detection against boundary design graphs.

No Flask dependency — takes graph data and returns results.
No LLM dependency — all checks are deterministic.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from tools.boundary_canvas.constants import (
    ISA_LIFECYCLE_STATES,
    DEFAULT_PPS,
    BOUNDARY_NIST_CONTROLS,
)

try:
    import yaml as _yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "args" / "boundary_canvas_config.yaml"


def _load_config() -> dict:
    """Load BDC engine config from args/boundary_canvas_config.yaml."""
    if not _HAS_YAML or not _CONFIG_PATH.exists():
        return {}
    with open(_CONFIG_PATH, "r", encoding="utf-8") as _f:
        return _yaml.safe_load(_f) or {}


_BDC_CONFIG = _load_config()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _node_types(nodes):
    """Return {node_id: node_type} dict."""
    return {n["id"]: n.get("type", "") for n in nodes}


def _label_map(nodes):
    """Return {node_id: label} dict."""
    return {n["id"]: n.get("label", n["id"]) for n in nodes}


def _build_adjacency(edges):
    """Build undirected adjacency: {node_id: {neighbor_id, ...}}."""
    adj = {}
    for e in edges:
        adj.setdefault(e["source"], set()).add(e["target"])
        adj.setdefault(e["target"], set()).add(e["source"])
    return adj


def _is_system(ntype):
    """Check if a node type is a system."""
    return ntype.startswith("sys-")


def _is_boundary(ntype):
    """Check if a node type is a boundary."""
    return ntype.startswith("bnd-")


def _is_interconnection(ntype):
    """Check if a node type is an interconnection."""
    return ntype.startswith("isa-")


def _is_control(ntype):
    """Check if a node type is a control."""
    return ntype.startswith("ctrl-")


def _is_document(ntype):
    """Check if a node type is a documentation item."""
    return ntype.startswith("doc-")


def _is_external_system(ntype):
    """Check if a node type is an external or cloud/SaaS system."""
    return ntype in ("sys-external", "sys-cloud", "sys-saas")


def _is_classification_boundary(ntype):
    """Check if a boundary is a classification zone."""
    return ntype in ("bnd-classification", "bnd-scif")


def _now_iso():
    """Return current UTC timestamp as ISO string."""
    return datetime.now(timezone.utc).isoformat()


def _find_boundary_nodes(nodes):
    """Return list of boundary nodes from the graph."""
    return [n for n in nodes if _is_boundary(n.get("type", ""))]


def _find_systems_in_boundary(boundary_node, nodes, edges):
    """Find system nodes contained within a boundary.

    A system is inside a boundary if:
    1. It has a 'parent_boundary' property matching the boundary id, OR
    2. It is connected to the boundary via an edge.
    """
    bnd_id = boundary_node["id"]
    contained = set()
    # Check parent_boundary property
    for n in nodes:
        props = n.get("properties", {})
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except (json.JSONDecodeError, TypeError):
                props = {}
        if props.get("parent_boundary") == bnd_id:
            contained.add(n["id"])
    # Check edges to boundary
    for e in edges:
        if e["source"] == bnd_id and _is_system(_node_types(nodes).get(e["target"], "")):
            contained.add(e["target"])
        if e["target"] == bnd_id and _is_system(_node_types(nodes).get(e["source"], "")):
            contained.add(e["source"])
    # Check contained_nodes list on the boundary itself
    cn = boundary_node.get("contained_nodes", [])
    if isinstance(cn, str):
        try:
            cn = json.loads(cn)
        except (json.JSONDecodeError, TypeError):
            cn = []
    for nid in cn:
        contained.add(nid)
    return contained


def _get_interconnections(nodes, edges):
    """Return list of edges that are interconnections (ISA-typed edges or
    edges connecting to ISA-typed nodes)."""
    ntypes = _node_types(nodes)
    interconnections = []
    for e in edges:
        src_type = ntypes.get(e["source"], "")
        tgt_type = ntypes.get(e["target"], "")
        edge_type = e.get("type", "")
        if _is_interconnection(edge_type) or _is_interconnection(src_type) or _is_interconnection(tgt_type):
            interconnections.append(e)
    return interconnections


def _find_controls_near_boundary(boundary_id, nodes, edges):
    """Find control nodes connected to a boundary node."""
    ntypes = _node_types(nodes)
    controls = set()
    for e in edges:
        if e["source"] == boundary_id and _is_control(ntypes.get(e["target"], "")):
            controls.add(e["target"])
        if e["target"] == boundary_id and _is_control(ntypes.get(e["source"], "")):
            controls.add(e["source"])
    return controls


def _find_docs_near_boundary(boundary_id, nodes, edges):
    """Find documentation nodes connected to a boundary node."""
    ntypes = _node_types(nodes)
    docs = set()
    for e in edges:
        if e["source"] == boundary_id and _is_document(ntypes.get(e["target"], "")):
            docs.add(e["target"])
        if e["target"] == boundary_id and _is_document(ntypes.get(e["source"], "")):
            docs.add(e["source"])
    return docs


def _find_docs_near_interconnection(isa_node_id, nodes, edges):
    """Find documentation nodes connected to an interconnection node."""
    ntypes = _node_types(nodes)
    docs = set()
    for e in edges:
        if e["source"] == isa_node_id and _is_document(ntypes.get(e["target"], "")):
            docs.add(e["target"])
        if e["target"] == isa_node_id and _is_document(ntypes.get(e["source"], "")):
            docs.add(e["source"])
    return docs


# ── Rule Check Functions ─────────────────────────────────────────────────────


def _check_bnd_001(nodes, edges, boundaries):
    """BDC-BND-001: ATO boundary defined."""
    findings = []
    ato_boundaries = [b for b in boundaries if b.get("type") == "bnd-ato"]
    if not ato_boundaries:
        findings.append(
            {
                "rule_id": "BDC-BND-001",
                "severity": "CAT1",
                "title": "No ATO boundary defined",
                "description": "Design must have at least one ATO boundary to define scope of security assessment.",
                "affected_entity": None,
                "affected_type": "design",
                "status": "open",
            }
        )
    return findings


def _check_bnd_002(nodes, edges, boundaries):
    """BDC-BND-002: All systems inside a boundary."""
    findings = []
    ntypes = _node_types(nodes)
    labels = _label_map(nodes)
    # Collect all system node IDs
    system_ids = {n["id"] for n in nodes if _is_system(ntypes.get(n["id"], ""))}
    # Collect systems inside any boundary
    bounded_systems = set()
    for b in boundaries:
        contained = _find_systems_in_boundary(b, nodes, edges)
        bounded_systems.update(contained)
    # Find unbounded systems
    unbounded = system_ids - bounded_systems
    for sid in unbounded:
        findings.append(
            {
                "rule_id": "BDC-BND-002",
                "severity": "CAT1",
                "title": f"System '{labels.get(sid, sid)}' not inside any boundary",
                "description": "Every system must reside within an authorization boundary.",
                "affected_entity": sid,
                "affected_type": "node",
                "status": "open",
            }
        )
    return findings


def _check_isa_001(nodes, edges, boundaries):
    """BDC-ISA-001: ISA agreement for every external interconnection."""
    findings = []
    ntypes = _node_types(nodes)
    labels = _label_map(nodes)
    # Find ISA-type nodes (interconnections)
    isa_nodes = [n for n in nodes if _is_interconnection(ntypes.get(n["id"], ""))]
    for isa_node in isa_nodes:
        # Check if any connected system is external
        has_external = False
        for e in edges:
            peer = None
            if e["source"] == isa_node["id"]:
                peer = e["target"]
            elif e["target"] == isa_node["id"]:
                peer = e["source"]
            if peer and _is_external_system(ntypes.get(peer, "")):
                has_external = True
                break
        if has_external:
            # Check for ISA doc attached
            docs = _find_docs_near_interconnection(isa_node["id"], nodes, edges)
            doc_types = {ntypes.get(d, "") for d in docs}
            if "doc-isa" not in doc_types:
                findings.append(
                    {
                        "rule_id": "BDC-ISA-001",
                        "severity": "CAT1",
                        "title": f"No ISA agreement for '{labels.get(isa_node['id'], isa_node['id'])}'",
                        "description": "External interconnection requires an ISA document (NIST CA-3).",
                        "affected_entity": isa_node["id"],
                        "affected_type": "node",
                        "status": "open",
                    }
                )
    return findings


def _check_isa_002(nodes, edges, isa_tracker):
    """BDC-ISA-002: ISA not expired."""
    findings = []
    for entry in isa_tracker:
        status = entry.get("status", "")
        if status in ("expired", "terminated"):
            findings.append(
                {
                    "rule_id": "BDC-ISA-002",
                    "severity": "CAT1",
                    "title": f"ISA '{entry.get('interconnection_id', '')}' is {status}",
                    "description": "Expired or terminated ISA agreements represent unauthorized connections.",
                    "affected_entity": entry.get("interconnection_id"),
                    "affected_type": "isa",
                    "status": "open",
                }
            )
    return findings


def _check_isa_003(nodes, edges, boundaries):
    """BDC-ISA-003: Cross-domain solution for classification boundary crossing."""
    findings = []
    ntypes = _node_types(nodes)
    labels = _label_map(nodes)
    classification_boundaries = [b for b in boundaries if _is_classification_boundary(b.get("type", ""))]
    if len(classification_boundaries) < 2:
        return findings  # No cross-classification scenario
    # Build boundary-membership map
    boundary_membership = {}  # node_id -> set of classification boundary IDs
    for b in classification_boundaries:
        contained = _find_systems_in_boundary(b, nodes, edges)
        for nid in contained:
            boundary_membership.setdefault(nid, set()).add(b["id"])
    # Check interconnection edges
    interconnections = _get_interconnections(nodes, edges)
    for e in interconnections:
        src_zones = boundary_membership.get(e["source"], set())
        tgt_zones = boundary_membership.get(e["target"], set())
        if src_zones and tgt_zones and src_zones != tgt_zones:
            # This edge crosses classification boundaries
            edge_type = e.get("type", "")
            src_type = ntypes.get(e["source"], "")
            tgt_type = ntypes.get(e["target"], "")
            if edge_type != "isa-cross-domain" and src_type != "isa-cross-domain" and tgt_type != "isa-cross-domain":
                findings.append(
                    {
                        "rule_id": "BDC-ISA-003",
                        "severity": "CAT1",
                        "title": "Cross-classification interconnection without CDS",
                        "description": (
                            f"Interconnection between '{labels.get(e['source'], e['source'])}' and "
                            f"'{labels.get(e['target'], e['target'])}' crosses classification boundary "
                            "without a cross-domain solution (NIST AC-4(21))."
                        ),
                        "affected_entity": e.get("id", f"{e['source']}->{e['target']}"),
                        "affected_type": "edge",
                        "status": "open",
                    }
                )
    return findings


def _check_ctl_001(nodes, edges, boundaries):
    """BDC-CTL-001: Firewall at every boundary edge."""
    findings = []
    ntypes = _node_types(nodes)
    labels = _label_map(nodes)
    ato_boundaries = [b for b in boundaries if b.get("type") in ("bnd-ato", "bnd-fedramp")]
    for b in ato_boundaries:
        controls = _find_controls_near_boundary(b["id"], nodes, edges)
        control_types = {ntypes.get(c, "") for c in controls}
        if "ctrl-firewall" not in control_types:
            findings.append(
                {
                    "rule_id": "BDC-CTL-001",
                    "severity": "CAT1",
                    "title": f"No firewall at boundary '{labels.get(b['id'], b['id'])}'",
                    "description": "Authorization boundary must have a firewall at its perimeter (NIST SC-7).",
                    "affected_entity": b["id"],
                    "affected_type": "node",
                    "status": "open",
                }
            )
    return findings


def _check_ctl_002(nodes, edges, boundaries):
    """BDC-CTL-002: IDS/IPS monitoring boundary traffic."""
    findings = []
    ntypes = _node_types(nodes)
    labels = _label_map(nodes)
    ato_boundaries = [b for b in boundaries if b.get("type") in ("bnd-ato", "bnd-fedramp")]
    for b in ato_boundaries:
        controls = _find_controls_near_boundary(b["id"], nodes, edges)
        control_types = {ntypes.get(c, "") for c in controls}
        if "ctrl-ids-ips" not in control_types:
            findings.append(
                {
                    "rule_id": "BDC-CTL-002",
                    "severity": "CAT2",
                    "title": f"No IDS/IPS at boundary '{labels.get(b['id'], b['id'])}'",
                    "description": "Boundary should have IDS/IPS for intrusion detection (NIST SI-4).",
                    "affected_entity": b["id"],
                    "affected_type": "node",
                    "status": "open",
                }
            )
    return findings


def _check_ctl_003(nodes, edges, boundaries):
    """BDC-CTL-003: SIEM collecting boundary logs."""
    findings = []
    ntypes = _node_types(nodes)
    # Check if any SIEM node exists anywhere in the graph
    siem_exists = any(ntypes.get(n["id"], "") == "ctrl-siem" for n in nodes)
    if not siem_exists and boundaries:
        findings.append(
            {
                "rule_id": "BDC-CTL-003",
                "severity": "CAT2",
                "title": "No SIEM/SOC in design",
                "description": "SIEM must monitor boundary traffic and interconnection activity (NIST AU-6, SI-4).",
                "affected_entity": None,
                "affected_type": "design",
                "status": "open",
            }
        )
    return findings


def _check_ctl_004(nodes, edges, boundaries):
    """BDC-CTL-004: PPS restriction enforced."""
    findings = []
    ntypes = _node_types(nodes)
    labels = _label_map(nodes)
    isa_nodes = [n for n in nodes if _is_interconnection(ntypes.get(n["id"], ""))]
    for isa_node in isa_nodes:
        # Check if PPS filter is connected to this interconnection
        adj_controls = set()
        for e in edges:
            peer = None
            if e["source"] == isa_node["id"]:
                peer = e["target"]
            elif e["target"] == isa_node["id"]:
                peer = e["source"]
            if peer and _is_control(ntypes.get(peer, "")):
                adj_controls.add(ntypes.get(peer, ""))
        if "ctrl-pps" not in adj_controls:
            findings.append(
                {
                    "rule_id": "BDC-CTL-004",
                    "severity": "CAT2",
                    "title": f"No PPS filter on '{labels.get(isa_node['id'], isa_node['id'])}'",
                    "description": "Interconnection should have PPS filter for ports/protocols restriction (NIST CM-7).",
                    "affected_entity": isa_node["id"],
                    "affected_type": "node",
                    "status": "open",
                }
            )
    return findings


def _check_ctl_005(nodes, edges, boundaries):
    """BDC-CTL-005: MFA for external access."""
    findings = []
    ntypes = _node_types(nodes)
    labels = _label_map(nodes)
    isa_nodes = [n for n in nodes if _is_interconnection(ntypes.get(n["id"], ""))]
    for isa_node in isa_nodes:
        has_external = False
        for e in edges:
            peer = None
            if e["source"] == isa_node["id"]:
                peer = e["target"]
            elif e["target"] == isa_node["id"]:
                peer = e["source"]
            if peer and _is_external_system(ntypes.get(peer, "")):
                has_external = True
                break
        if not has_external:
            continue
        # User-facing interconnection types (not pure system-to-system)
        isa_type = ntypes.get(isa_node["id"], "")
        if isa_type in ("isa-vpn", "isa-federation"):
            adj_controls = set()
            for e in edges:
                peer = None
                if e["source"] == isa_node["id"]:
                    peer = e["target"]
                elif e["target"] == isa_node["id"]:
                    peer = e["source"]
                if peer and _is_control(ntypes.get(peer, "")):
                    adj_controls.add(ntypes.get(peer, ""))
            if "ctrl-mfa" not in adj_controls:
                findings.append(
                    {
                        "rule_id": "BDC-CTL-005",
                        "severity": "CAT1",
                        "title": f"No MFA on external access '{labels.get(isa_node['id'], isa_node['id'])}'",
                        "description": "External user access interconnections must require MFA (NIST IA-2(1)).",
                        "affected_entity": isa_node["id"],
                        "affected_type": "node",
                        "status": "open",
                    }
                )
    return findings


def _check_ctl_006(nodes, edges, boundaries):
    """BDC-CTL-006: mTLS for system-to-system connections."""
    findings = []
    ntypes = _node_types(nodes)
    labels = _label_map(nodes)
    isa_nodes = [n for n in nodes if _is_interconnection(ntypes.get(n["id"], ""))]
    for isa_node in isa_nodes:
        isa_type = ntypes.get(isa_node["id"], "")
        if isa_type not in ("isa-api", "isa-file"):
            continue
        adj_controls = set()
        for e in edges:
            peer = None
            if e["source"] == isa_node["id"]:
                peer = e["target"]
            elif e["target"] == isa_node["id"]:
                peer = e["source"]
            if peer and _is_control(ntypes.get(peer, "")):
                adj_controls.add(ntypes.get(peer, ""))
        if "ctrl-certificate" not in adj_controls:
            findings.append(
                {
                    "rule_id": "BDC-CTL-006",
                    "severity": "CAT2",
                    "title": f"No mTLS on '{labels.get(isa_node['id'], isa_node['id'])}'",
                    "description": "System-to-system interconnections should use mutual TLS (NIST IA-3, SC-8).",
                    "affected_entity": isa_node["id"],
                    "affected_type": "node",
                    "status": "open",
                }
            )
    return findings


def _check_doc_001(nodes, edges, boundaries):
    """BDC-DOC-001: PPS matrix documented."""
    findings = []
    ntypes = _node_types(nodes)
    labels = _label_map(nodes)
    ato_boundaries = [b for b in boundaries if b.get("type") in ("bnd-ato", "bnd-fedramp")]
    for b in ato_boundaries:
        docs = _find_docs_near_boundary(b["id"], nodes, edges)
        doc_types = {ntypes.get(d, "") for d in docs}
        if "doc-pps-matrix" not in doc_types:
            findings.append(
                {
                    "rule_id": "BDC-DOC-001",
                    "severity": "CAT2",
                    "title": f"No PPS matrix for boundary '{labels.get(b['id'], b['id'])}'",
                    "description": "Boundary must have a PPS matrix document listing allowed ports/protocols/services.",
                    "affected_entity": b["id"],
                    "affected_type": "node",
                    "status": "open",
                }
            )
    return findings


def _check_doc_002(nodes, edges, boundaries):
    """BDC-DOC-002: Data flow diagram present."""
    findings = []
    ntypes = _node_types(nodes)
    labels = _label_map(nodes)
    ato_boundaries = [b for b in boundaries if b.get("type") in ("bnd-ato", "bnd-fedramp")]
    for b in ato_boundaries:
        docs = _find_docs_near_boundary(b["id"], nodes, edges)
        doc_types = {ntypes.get(d, "") for d in docs}
        if "doc-dfd" not in doc_types:
            findings.append(
                {
                    "rule_id": "BDC-DOC-002",
                    "severity": "CAT2",
                    "title": f"No DFD for boundary '{labels.get(b['id'], b['id'])}'",
                    "description": "Boundary should have a data flow diagram showing data classification.",
                    "affected_entity": b["id"],
                    "affected_type": "node",
                    "status": "open",
                }
            )
    return findings


def _check_fed_001(nodes, edges, boundaries):
    """BDC-FED-001: SaaS providers FedRAMP authorized."""
    findings = []
    ntypes = _node_types(nodes)
    labels = _label_map(nodes)
    saas_nodes = [n for n in nodes if ntypes.get(n["id"], "") == "sys-saas"]
    for saas in saas_nodes:
        props = saas.get("properties", {})
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except (json.JSONDecodeError, TypeError):
                props = {}
        if not props.get("fedramp_authorized"):
            findings.append(
                {
                    "rule_id": "BDC-FED-001",
                    "severity": "CAT1",
                    "title": f"SaaS '{labels.get(saas['id'], saas['id'])}' not FedRAMP authorized",
                    "description": "SaaS provider must be FedRAMP authorized at the appropriate impact level.",
                    "affected_entity": saas["id"],
                    "affected_type": "node",
                    "status": "open",
                }
            )
    return findings


def _check_fed_002(nodes, edges, boundaries):
    """BDC-FED-002: Cloud services use BCAP/CAP."""
    findings = []
    ntypes = _node_types(nodes)
    cloud_nodes = [n for n in nodes if ntypes.get(n["id"], "") in ("sys-cloud", "sys-saas")]
    if not cloud_nodes:
        return findings
    # Check if BCAP or CAP exists in the design
    has_bcap_cap = any(ntypes.get(n["id"], "") in ("ctrl-bcap", "ctrl-cap") for n in nodes)
    if not has_bcap_cap:
        findings.append(
            {
                "rule_id": "BDC-FED-002",
                "severity": "CAT2",
                "title": "No BCAP/CAP for cloud service traffic",
                "description": "Cloud service interconnections should traverse a BCAP (DoD) or CAP (TIC 3.0).",
                "affected_entity": None,
                "affected_type": "design",
                "status": "open",
            }
        )
    return findings


# ── Main Assessment Functions ────────────────────────────────────────────────


def assess_boundary_design(graph_data, isa_tracker=None):
    """Run all BOUNDARY_COMPLIANCE_RULES against a boundary design graph.

    Args:
        graph_data: Dict with "nodes", "edges", and optionally "boundaries".
        isa_tracker: Optional list of ISA tracker entries (from bd_isa_tracker).

    Returns:
        Dict with findings list, score, summary, and NIST control mapping.
    """
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    # Extract boundary nodes from nodes list
    ntypes = _node_types(nodes)
    boundaries = [n for n in nodes if _is_boundary(ntypes.get(n["id"], ""))]
    # Also check top-level boundaries array (canvas may store separately)
    if "boundaries" in graph_data:
        for b in graph_data["boundaries"]:
            if b.get("id") not in {bn["id"] for bn in boundaries}:
                boundaries.append(b)

    if isa_tracker is None:
        isa_tracker = []

    # Run all rule checks
    all_findings = []
    all_findings.extend(_check_bnd_001(nodes, edges, boundaries))
    all_findings.extend(_check_bnd_002(nodes, edges, boundaries))
    all_findings.extend(_check_isa_001(nodes, edges, boundaries))
    all_findings.extend(_check_isa_002(nodes, edges, isa_tracker))
    all_findings.extend(_check_isa_003(nodes, edges, boundaries))
    all_findings.extend(_check_ctl_001(nodes, edges, boundaries))
    all_findings.extend(_check_ctl_002(nodes, edges, boundaries))
    all_findings.extend(_check_ctl_003(nodes, edges, boundaries))
    all_findings.extend(_check_ctl_004(nodes, edges, boundaries))
    all_findings.extend(_check_ctl_005(nodes, edges, boundaries))
    all_findings.extend(_check_ctl_006(nodes, edges, boundaries))
    all_findings.extend(_check_doc_001(nodes, edges, boundaries))
    all_findings.extend(_check_doc_002(nodes, edges, boundaries))
    all_findings.extend(_check_fed_001(nodes, edges, boundaries))
    all_findings.extend(_check_fed_002(nodes, edges, boundaries))

    # Compute score (start at 100, deduct per finding by severity)
    score = 100.0
    cat1_count = 0
    cat2_count = 0
    cat3_count = 0
    for f in all_findings:
        sev = f.get("severity", "CAT2")
        if sev == "CAT1":
            score -= 10.0
            cat1_count += 1
        elif sev == "CAT2":
            score -= 5.0
            cat2_count += 1
        else:
            score -= 2.0
            cat3_count += 1
    score = max(0.0, score)

    # Grade
    if cat1_count > 0:
        grade = "FAIL"
    elif score >= 90:
        grade = "A"
    elif score >= 80:
        grade = "B"
    elif score >= 70:
        grade = "C"
    elif score >= 60:
        grade = "D"
    else:
        grade = "F"

    # Summary stats
    total_systems = sum(1 for n in nodes if _is_system(ntypes.get(n["id"], "")))
    total_boundaries = len(boundaries)
    total_interconnections = sum(1 for n in nodes if _is_interconnection(ntypes.get(n["id"], "")))
    total_controls = sum(1 for n in nodes if _is_control(ntypes.get(n["id"], "")))
    total_docs = sum(1 for n in nodes if _is_document(ntypes.get(n["id"], "")))

    # NIST control coverage
    nist_coverage = _compute_nist_coverage(nodes, edges, boundaries)

    return {
        "findings": all_findings,
        "score": round(score, 1),
        "grade": grade,
        "total_findings": len(all_findings),
        "cat1_findings": cat1_count,
        "cat2_findings": cat2_count,
        "cat3_findings": cat3_count,
        "summary": {
            "total_systems": total_systems,
            "total_boundaries": total_boundaries,
            "total_interconnections": total_interconnections,
            "total_controls": total_controls,
            "total_documents": total_docs,
        },
        "nist_coverage": nist_coverage,
        "assessed_at": _now_iso(),
    }


def _compute_nist_coverage(nodes, edges, boundaries):
    """Map which NIST 800-53 boundary controls are covered by the design."""
    ntypes = _node_types(nodes)
    covered = {}

    # Map control types to NIST controls they satisfy
    control_nist_map = {
        "ctrl-firewall": ["SC-7", "SC-7(3)", "SC-7(5)"],
        "ctrl-ids-ips": ["SI-4"],
        "ctrl-siem": ["AU-6", "SI-4"],
        "ctrl-pps": ["CM-7", "SC-7(5)"],
        "ctrl-mfa": ["IA-2(1)", "AC-17"],
        "ctrl-certificate": ["IA-3", "SC-8", "SC-8(1)"],
        "ctrl-dlp": ["AC-4", "SC-7(9)"],
        "ctrl-proxy": ["SC-7(8)"],
        "ctrl-bcap": ["SC-7(4)", "AC-20"],
        "ctrl-cap": ["SC-7(4)", "AC-20"],
    }

    # Check which controls exist in the design
    present_controls = {ntypes.get(n["id"], "") for n in nodes if _is_control(ntypes.get(n["id"], ""))}
    for ctrl_type, nist_ids in control_nist_map.items():
        for nist_id in nist_ids:
            if ctrl_type in present_controls:
                covered[nist_id] = {
                    "control": BOUNDARY_NIST_CONTROLS.get(nist_id, nist_id),
                    "status": "covered",
                    "covered_by": ctrl_type,
                }
            elif nist_id not in covered:
                covered[nist_id] = {
                    "control": BOUNDARY_NIST_CONTROLS.get(nist_id, nist_id),
                    "status": "not_covered",
                    "covered_by": None,
                }

    # ISA docs cover CA-3
    doc_types = {ntypes.get(n["id"], "") for n in nodes if _is_document(ntypes.get(n["id"], ""))}
    if "doc-isa" in doc_types:
        covered["CA-3"] = {"control": BOUNDARY_NIST_CONTROLS["CA-3"], "status": "covered", "covered_by": "doc-isa"}
    else:
        covered.setdefault(
            "CA-3", {"control": BOUNDARY_NIST_CONTROLS["CA-3"], "status": "not_covered", "covered_by": None}
        )

    # Cross-domain nodes cover AC-4(21)
    if any(ntypes.get(n["id"], "") == "isa-cross-domain" for n in nodes):
        covered["AC-4(21)"] = {
            "control": BOUNDARY_NIST_CONTROLS["AC-4(21)"],
            "status": "covered",
            "covered_by": "isa-cross-domain",
        }
    else:
        covered.setdefault(
            "AC-4(21)", {"control": BOUNDARY_NIST_CONTROLS["AC-4(21)"], "status": "not_covered", "covered_by": None}
        )

    total = len(covered)
    covered_count = sum(1 for v in covered.values() if v["status"] == "covered")

    return {
        "controls": covered,
        "total": total,
        "covered": covered_count,
        "coverage_pct": round((covered_count / total * 100) if total else 0, 1),
    }


def compute_isa_status(graph_data, isa_tracker=None):
    """Check ISA lifecycle states for all tracked interconnections.

    Args:
        graph_data: Dict with "nodes" and "edges".
        isa_tracker: List of ISA tracker entries from bd_isa_tracker table.

    Returns:
        Dict with ISA status summary and per-ISA details.
    """
    if isa_tracker is None:
        isa_tracker = []

    nodes = graph_data.get("nodes", [])
    ntypes = _node_types(nodes)
    labels = _label_map(nodes)

    # All ISA-type nodes in the design
    isa_nodes = [n for n in nodes if _is_interconnection(ntypes.get(n["id"], ""))]

    # Map tracked ISA entries by interconnection_id
    tracked = {t.get("interconnection_id"): t for t in isa_tracker}

    results = []
    by_status = {s: 0 for s in ISA_LIFECYCLE_STATES}
    by_status["untracked"] = 0

    for isa_node in isa_nodes:
        nid = isa_node["id"]
        if nid in tracked:
            entry = tracked[nid]
            status = entry.get("status", "draft")
            results.append(
                {
                    "interconnection_id": nid,
                    "label": labels.get(nid, nid),
                    "type": ntypes.get(nid, ""),
                    "status": status,
                    "expiry_date": entry.get("expiry_date"),
                    "review_date": entry.get("review_date"),
                    "owner": entry.get("owner", ""),
                    "isa_doc_id": entry.get("isa_doc_id"),
                }
            )
            by_status[status] = by_status.get(status, 0) + 1
        else:
            results.append(
                {
                    "interconnection_id": nid,
                    "label": labels.get(nid, nid),
                    "type": ntypes.get(nid, ""),
                    "status": "untracked",
                    "expiry_date": None,
                    "review_date": None,
                    "owner": "",
                    "isa_doc_id": None,
                }
            )
            by_status["untracked"] += 1

    return {
        "interconnections": results,
        "total": len(results),
        "by_status": by_status,
        "has_expired": by_status.get("expired", 0) > 0,
        "has_expiring_soon": by_status.get("expiring_soon", 0) > 0,
    }


def generate_pps_matrix(graph_data):
    """Extract ports/protocols/services from all interconnections in the design.

    Args:
        graph_data: Dict with "nodes" and "edges".

    Returns:
        Dict with per-interconnection PPS entries and a flat summary table.
    """
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    ntypes = _node_types(nodes)
    labels = _label_map(nodes)

    matrix = []

    isa_nodes = [n for n in nodes if _is_interconnection(ntypes.get(n["id"], ""))]
    for isa_node in isa_nodes:
        nid = isa_node["id"]
        ntype = ntypes.get(nid, "")
        label = labels.get(nid, nid)

        # Get custom PPS from node properties
        props = isa_node.get("properties", {})
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except (json.JSONDecodeError, TypeError):
                props = {}

        custom_pps = props.get("pps", [])
        if custom_pps:
            entries = custom_pps
        else:
            # Fall back to defaults for this interconnection type
            entries = DEFAULT_PPS.get(ntype, [])

        # Find connected systems
        connected_systems = []
        for e in edges:
            peer = None
            if e["source"] == nid:
                peer = e["target"]
            elif e["target"] == nid:
                peer = e["source"]
            if peer and _is_system(ntypes.get(peer, "")):
                connected_systems.append(labels.get(peer, peer))

        matrix.append(
            {
                "interconnection_id": nid,
                "label": label,
                "type": ntype,
                "connected_systems": connected_systems,
                "entries": entries,
            }
        )

    # Build flat summary
    flat_rows = []
    for item in matrix:
        for entry in item["entries"]:
            flat_rows.append(
                {
                    "interconnection": item["label"],
                    "type": item["type"],
                    "port": entry.get("port", ""),
                    "protocol": entry.get("protocol", ""),
                    "service": entry.get("service", ""),
                    "direction": entry.get("direction", "bidirectional"),
                    "connected_systems": ", ".join(item["connected_systems"]),
                }
            )

    return {
        "matrix": matrix,
        "flat_rows": flat_rows,
        "total_interconnections": len(matrix),
        "total_pps_entries": len(flat_rows),
    }


def detect_boundary_gaps(assessment_result):
    """Identify missing controls, documents, and design gaps from an assessment.

    Args:
        assessment_result: Dict returned by assess_boundary_design().

    Returns:
        Dict with categorized gaps and recommended actions.
    """
    findings = assessment_result.get("findings", [])
    nist_coverage = assessment_result.get("nist_coverage", {})

    # Categorize gaps
    gaps = {
        "missing_controls": [],
        "missing_documents": [],
        "boundary_issues": [],
        "interconnection_issues": [],
        "compliance_gaps": [],
    }

    for f in findings:
        rule_id = f.get("rule_id", "")
        gap_entry = {
            "rule_id": rule_id,
            "title": f.get("title", ""),
            "severity": f.get("severity", "CAT2"),
            "affected_entity": f.get("affected_entity"),
            "recommendation": _get_recommendation(rule_id),
        }
        if rule_id.startswith("BDC-CTL-"):
            gaps["missing_controls"].append(gap_entry)
        elif rule_id.startswith("BDC-DOC-"):
            gaps["missing_documents"].append(gap_entry)
        elif rule_id.startswith("BDC-BND-"):
            gaps["boundary_issues"].append(gap_entry)
        elif rule_id.startswith("BDC-ISA-"):
            gaps["interconnection_issues"].append(gap_entry)
        elif rule_id.startswith("BDC-FED-"):
            gaps["compliance_gaps"].append(gap_entry)

    # Check uncovered NIST controls
    controls = nist_coverage.get("controls", {})
    uncovered_nist = []
    for nist_id, info in controls.items():
        if info.get("status") == "not_covered":
            uncovered_nist.append(
                {
                    "control_id": nist_id,
                    "control_name": info.get("control", nist_id),
                    "recommendation": f"Add control or documentation to satisfy NIST {nist_id}.",
                }
            )

    return {
        "gaps": gaps,
        "uncovered_nist_controls": uncovered_nist,
        "total_gaps": sum(len(v) for v in gaps.values()),
        "total_uncovered_nist": len(uncovered_nist),
        "assessment_score": assessment_result.get("score", 0),
        "assessment_grade": assessment_result.get("grade", "N/A"),
    }


def _get_recommendation(rule_id):
    """Return a recommended action for a given rule violation."""
    recommendations = {
        "BDC-BND-001": "Add an ATO Boundary (bnd-ato) node to define the authorization scope.",
        "BDC-BND-002": "Move unbounded systems inside an ATO or enclave boundary, or set parent_boundary.",
        "BDC-ISA-001": "Attach an ISA Agreement (doc-isa) document node to each external interconnection.",
        "BDC-ISA-002": "Renew or replace expired ISA agreements before the connection is unauthorized.",
        "BDC-ISA-003": "Add a Cross-Domain Solution (isa-cross-domain) node between classification zones.",
        "BDC-CTL-001": "Add a Boundary Firewall (ctrl-firewall) connected to the boundary perimeter.",
        "BDC-CTL-002": "Add an IDS/IPS (ctrl-ids-ips) node connected to the boundary.",
        "BDC-CTL-003": "Add a SIEM/SOC (ctrl-siem) node to the design for boundary monitoring.",
        "BDC-CTL-004": "Add a PPS Filter (ctrl-pps) connected to each interconnection.",
        "BDC-CTL-005": "Add an MFA Gateway (ctrl-mfa) to external user-facing interconnections.",
        "BDC-CTL-006": "Add a PKI/mTLS (ctrl-certificate) control to API/file transfer interconnections.",
        "BDC-DOC-001": "Attach a PPS Matrix (doc-pps-matrix) document to each boundary.",
        "BDC-DOC-002": "Attach a Data Flow Diagram (doc-dfd) document to each boundary.",
        "BDC-FED-001": "Set fedramp_authorized=true in SaaS node properties, or replace with FedRAMP-authorized provider.",
        "BDC-FED-002": "Add a BCAP or CAP control node for cloud traffic inspection.",
    }
    return recommendations.get(rule_id, "Review the boundary design for compliance.")
