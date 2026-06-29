
from tools.logging.icdev_logger import get_logger
# [TEMPLATE: CUI // SP-CTI]
"""ICDEV™ Security Design Canvas — NDC Bridge.

Bidirectional sync between Network Design Canvas and Security Design Canvas:
- Import NDC topology as SDC security design
- Sync NDC compliance findings as SDC threats
- Push SDC remediation back to NDC as compliance fixes
"""

import json
import uuid
from datetime import datetime, timezone

logger = get_logger("icdev.security_canvas.bridge")


def import_ndc_topology(topology_id: str) -> dict:
    """Import an NDC topology as an SDC security design.

    Maps NDC node types to SDC asset types, edges to data flows,
    and creates trust boundaries from VPC/VNet/VCN groupings.
    If a design already exists for the topology, updates it in place.
    """
    from tools.security_canvas.db.init_db import get_connection as sc_conn

    # Load NDC topology
    try:
        from tools.network.db.init_db import get_connection as ndc_conn

        with ndc_conn() as conn:
            row = conn.execute(
                "SELECT id, name, graph_json FROM topologies WHERE id=%s",
                (topology_id,),
            ).fetchone()
        if not row:
            return {
                "error": "NDC topology not found",
                "topology_id": topology_id,
            }
        topo_name = row[1] or row[0]
        graph = json.loads(row[2]) if isinstance(row[2], str) else row[2]
    except ImportError:
        return {"error": "NDC module not available"}

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    now = datetime.now(timezone.utc).isoformat()

    # Check if design already exists for this topology
    with sc_conn() as sconn:
        existing = sconn.execute(
            "SELECT id FROM security_designs WHERE source_topology_id=%s",
            (topology_id,),
        ).fetchone()

        sdc_graph = _convert_ndc_to_sdc(nodes, edges)

        if existing:
            design_id = existing[0]
            sconn.execute(
                "UPDATE security_designs SET graph_json=%s, updated_at=%s WHERE id=%s",
                (json.dumps(sdc_graph), now, design_id),
            )
            action = "updated"
        else:
            design_id = str(uuid.uuid4())
            sconn.execute(
                "INSERT INTO security_designs "
                "(id, name, description, graph_json, source_topology_id, "
                "classification, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    design_id,
                    f"Security: {topo_name}",
                    f"Auto-imported from NDC topology: {topo_name}",
                    json.dumps(sdc_graph),
                    topology_id,
                    "CUI",
                    now,
                    now,
                ),
            )
            action = "created"

    return {
        "design_id": design_id,
        "action": action,
        "topology_id": topology_id,
        "nodes_mapped": len(sdc_graph.get("nodes", [])),
    }


# ── VPC / VNet node types that become trust boundaries ────────────────────────
_VPC_TYPES = frozenset(
    (
        "aws-vpc",
        "az-vnet",
        "gcp-vpc",
        "oci-vcn",
        "ibm-vpc",
    )
)

# ── Encrypted / authenticated protocol sets ──────────────────────────────────
_ENCRYPTED_PROTOCOLS = frozenset(
    (
        "ipsec",
        "tls",
        "https",
        "macsec",
        "ssh",
    )
)
_AUTHENTICATED_PROTOCOLS = frozenset(
    (
        "bgp",
        "ipsec",
        "saml",
        "oauth",
        "ssh",
    )
)


def _convert_ndc_to_sdc(nodes: list, edges: list) -> dict:
    """Convert NDC graph nodes/edges to SDC format.

    - VPC/VNet/VCN nodes become trust boundaries
    - Other nodes are mapped via NODE_TYPE_MAPPING
    - Edges gain encrypted/authenticated flags based on protocol
    """
    from tools.security_canvas.constants import NODE_TYPE_MAPPING

    sdc_nodes = []
    sdc_edges = []
    sdc_boundaries = []

    for n in nodes:
        ndc_type = n.get("type", "")

        # VPCs become trust boundaries, not regular nodes
        if ndc_type in _VPC_TYPES:
            sdc_boundaries.append(
                {
                    "id": f"bnd-{n.get('id', '')}",
                    "type": "boundary-cloud",
                    "label": n.get("label", "Cloud VPC"),
                    "x": n.get("x", 0) - 20,
                    "y": n.get("y", 0) - 20,
                    "width": 400,
                    "height": 300,
                    "config": n.get("config", {}),
                }
            )
            continue

        sdc_type = NODE_TYPE_MAPPING.get(ndc_type, "asset-server")
        sdc_nodes.append(
            {
                "id": n.get("id", ""),
                "type": sdc_type,
                "label": n.get("label", ""),
                "x": n.get("x", 0),
                "y": n.get("y", 0),
                "config": n.get("config", {}),
                "source_node_id": n.get("id", ""),
            }
        )

    for e in edges:
        protocol = (e.get("protocol", "") or "").lower()
        sdc_edges.append(
            {
                "source": e.get("source", ""),
                "target": e.get("target", ""),
                "label": e.get("label", ""),
                "protocol": e.get("protocol", ""),
                "encrypted": 1 if protocol in _ENCRYPTED_PROTOCOLS else 0,
                "authenticated": 1 if protocol in _AUTHENTICATED_PROTOCOLS else 0,
            }
        )

    return {
        "nodes": sdc_nodes,
        "edges": sdc_edges,
        "boundaries": sdc_boundaries,
    }


def sync_ndc_compliance_to_sdc(
    topology_id: str,
    design_id: str,
) -> dict:
    """Pull NDC compliance findings and create corresponding SDC threats.

    Maps NDC compliance finding severity to SDC threat likelihood/impact
    and assigns an approximate STRIDE category based on finding content.
    """
    try:
        from tools.network.db.init_db import get_connection as ndc_conn
        from tools.security_canvas.db.init_db import get_connection as sc_conn

        with ndc_conn() as conn:
            findings = conn.execute(
                "SELECT rule_id, severity, title, description, "
                "affected_entity FROM nc_compliance_findings "
                "WHERE topology_id=%s AND status='open'",
                (topology_id,),
            ).fetchall()

        now = datetime.now(timezone.utc).isoformat()
        synced = 0

        with sc_conn() as sconn:
            for f in findings:
                # Map severity to STRIDE category (approximate)
                desc_lower = (f[3] or "").lower()
                if "encrypt" in desc_lower:
                    category = "info_disclosure"
                elif "auth" in desc_lower:
                    category = "spoofing"
                elif "log" in desc_lower:
                    category = "repudiation"
                else:
                    category = "tampering"

                is_cat1 = f[1] == "CAT1"
                threat_id = str(uuid.uuid4())
                sconn.execute(
                    "INSERT OR IGNORE INTO sc_threats "
                    "(id, design_id, threat_category, title, description, "
                    "likelihood, impact, status, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        threat_id,
                        design_id,
                        category,
                        f[2] or f[0],
                        f[3] or "",
                        "high" if is_cat1 else "medium",
                        "high" if is_cat1 else "medium",
                        "identified",
                        now,
                    ),
                )
                synced += 1

        return {
            "synced_threats": synced,
            "topology_id": topology_id,
            "design_id": design_id,
        }
    except Exception as exc:
        return {"error": str(exc)}


def push_sdc_remediation_to_ndc(
    design_id: str,
    topology_id: str,
) -> dict:
    """Push SDC remediation actions back to NDC as compliance information.

    Retrieves the most recent open remediation plan and returns the
    step count for NDC compliance team review.
    """
    try:
        from tools.security_canvas.db.init_db import get_connection as sc_conn

        with sc_conn() as sconn:
            plans = sconn.execute(
                "SELECT remediation_steps FROM sc_remediation_plans "
                "WHERE design_id=%s AND status='open' "
                "ORDER BY created_at DESC LIMIT 1",
                (design_id,),
            ).fetchone()

        if not plans:
            return {"status": "no_open_plans", "design_id": design_id}

        steps = json.loads(plans[0]) if isinstance(plans[0], str) else plans[0]
        return {
            "status": "ready",
            "design_id": design_id,
            "topology_id": topology_id,
            "remediation_steps": len(steps) if isinstance(steps, list) else 0,
            "note": "Remediation steps available for NDC compliance team to review",
        }
    except Exception as exc:
        return {"error": str(exc)}
