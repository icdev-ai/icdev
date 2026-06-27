#!/usr/bin/env python3
# CUI // SP-CTI
"""Spec Generator — canvas-aware structured spec extractor from REFINE sessions.

Derives a canvas-specific specification document from a TFW session at the end
of a REFINE conversation. The spec captures all decisions made during the
refinement loop in a structured, machine-readable form.

Canvas-specific spec sections:
  ndc  -> topology_metadata, flow_definitions, security_policies,
           compliance_requirements
  sdc  -> service_definitions, api_contracts, auth_schemes, sla_targets
  eda  -> topic_definitions, producer_consumer_bindings, messaging_config

Public surface:
  generate_spec(session_id, canvas_type) -> dict

/spec slash command:
  Handled in blueprint.py — detects /spec content prefix, calls generate_spec,
  returns a YAML-fenced reply with spec dict attached in JSON envelope.

Data source (in priority order):
  1. diagram_refiner._SESSIONS[session_id]["answers"] — in-memory REFINE answers
     (available only if session is still active when /spec is called)
  2. nc_simulation_sessions.metadata["refine_answers"] — persisted answers
  3. nc_simulation_sessions.metadata["refined_graph_json"] — refined topology
     (special REFINE nodes embedded by diagram_refiner are parsed out)
  4. topologies.graph_json — base topology via session.topology_id
  5. Empty graph — returns minimal spec with no topology data
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.canvas.canvas_registry import get_display_name
from tools.db.storage import get_connection

SPEC_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Canvas aliases (mirrors ppsm_extractor._CANVAS_ALIASES)
# ---------------------------------------------------------------------------

_CANVAS_ALIASES: dict[str, str] = {
    "bdc": "sdc",
    "idc": "ndc",
    "odc": "ndc",
    "ddc": "eda",
    "pdc": "eda",
    "qdc": "ndc",
    "mdc": "ndc",
}


def _resolve(canvas_type: str) -> str:
    ct = canvas_type.lower()
    return _CANVAS_ALIASES.get(ct, ct)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Regex helpers — shared with ppsm_extractor patterns
# ---------------------------------------------------------------------------

_PROTO_RE = re.compile(r"\b(TCP|UDP|ICMP|GRE|ESP|AH|SCTP)\b", re.I)
_PORT_WITH_PROTO_RE = re.compile(r"(?:TCP|UDP|ICMP|GRE|ESP|AH|SCTP)[\s:/-]*(\d+)", re.I)
_PORT_BARE_RE = re.compile(r":(\d{1,5})\b|^(\d{1,5})$")
_HTTP_METHOD_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b", re.I)
_ENDPOINT_RE = re.compile(r"(/[^\s,\|]+)")
_AUTH_RE = re.compile(r"\b(OAuth2?|mTLS|JWT|OIDC|SAML|API[.\s-]?Key|Basic|Bearer|Kerberos|SPIFFE)\b", re.I)
_SLA_RE = re.compile(r"(\d+)\s*ms\b|SLA\s*[=:]\s*([^\s,]+)", re.I)
_TOPIC_RE = re.compile(
    r"topic[:\s]+([^\s,]+)"
    r"|([a-z][a-z0-9_.-]*\.(?:events?|messages?|commands?|notifications?|updates?))",
    re.I,
)
_RETENTION_RE = re.compile(r"(\d+)\s*(d(?:ays?)?|h(?:ours?)?|w(?:eeks?)?|m(?:onths?)?)\b", re.I)
_ORDERING_RE = re.compile(r"\b(ordered|unordered|fifo|partitioned|sequential|strict|total.order|partition.order|per.key.order)\b", re.I)

# Encryption patterns for NDC edge labels
_ENCRYPTION_RE = re.compile(r"\b(IPSec|mTLS|TLS\s?1\.3|MACsec|TLS)\b", re.I)

# ---------------------------------------------------------------------------
# Refinement node ID detection
# Special nodes injected by diagram_refiner.py into the refined Mermaid.
# These are parsed by mermaid_parser into graph nodes with these IDs.
# ---------------------------------------------------------------------------

_NDC_REFINE_NODE_IDS = {"BCAP", "MICROSEG", "HA", "MGMT"}
_SDC_REFINE_NODE_IDS = {"CB", "VER", "MESH", "ZT"}
_EDA_REFINE_NODE_IDS = {"DLQ", "CG", "DELIVERY", "SCHEMA"}

# Label-content extractors for refinement nodes
_BCAP_LABEL_RE = re.compile(r"BCAP\s*\\?n(.+?)[\"\]\\]|Boundary Gateway", re.I)
_MICROSEG_LABEL_RE = re.compile(r"Microseg:\s*([^\]\"\\n]+)", re.I)
_HA_LABEL_RE = re.compile(r"HA:\s*([^\]\"\\n]+)", re.I)
_MGMT_LABEL_RE = re.compile(r"Mgmt Plane:\s*([^\]\"\\n]+)", re.I)
_CB_LABEL_RE = re.compile(r"\(([^)]+)\)", re.I)  # "Circuit Breaker\n(5 s / 3 failures)"
_VER_LABEL_RE = re.compile(r"API Versioning:\s*([^\]\"\\n]+)", re.I)
_MESH_LABEL_RE = re.compile(r"Service Mesh:\s*([^\]\"\\n]+)", re.I)
_ZT_LABEL_RE = re.compile(r"Zero Trust:\s*([^\]\"\\n]+)", re.I)
_CG_LABEL_RE = re.compile(r"Consumer Group[^\(]*\(([^\)]+)\)", re.I)
_DELIVERY_LABEL_RE = re.compile(r"Delivery:\s*([^\]\"\\n]+)", re.I)
_SCHEMA_LABEL_RE = re.compile(r"Schema:\s*([^\]\"\\n]+)", re.I)

# NDC subtypes (mirrors oscal_exporter)
_NDC_FIREWALL_RE = re.compile(r"\bfw\b|\bfirewall\b|\bngfw\b|\bwaf\b|\bacl\b", re.I)
_NDC_GATEWAY_RE = re.compile(r"\bgateway\b|\bigw\b|\bnat\b|\bapi[.\s-]?gw\b|\bvpn\b", re.I)
_NDC_VPC_RE = re.compile(r"\bvpc\b|\bvnet\b|\bsubnet\b|\bsegment\b", re.I)
_NDC_LB_RE = re.compile(r"\blb\b|\balb\b|\bnlb\b|\belb\b|load.?balanc", re.I)

_SDC_DATABASE_RE = re.compile(r"\bdb\b|\bdatabase\b|\bpostgres\b|\bmysql\b|\brds\b|\bmongo\b|\belastic\b", re.I)
_SDC_API_RE = re.compile(r"\bapi\b|\brest\b|\bgraphql\b|\bgrpc\b|\bendpoint\b", re.I)
_SDC_LIBRARY_RE = re.compile(r"\blib\b|\blibrary\b|\bsdk\b|\bpackage\b", re.I)
_SDC_MICROSERVICE_RE = re.compile(r"\bmicroservice\b|\bsvc\b|\bservice\b", re.I)

_EDA_STORE_RE = re.compile(r"\bkafka\b|\bkinesis\b|\bsqs\b|\bsns\b|\bpubsub\b|\bnats\b|\bpulsar\b|\brabbitmq\b", re.I)
_EDA_PROCESSOR_RE = re.compile(r"\bprocessor\b|\bflink\b|\bksql\b|\bconsumer\b|\bworker\b", re.I)
_EDA_TOPIC_RE = re.compile(r"\btopic\b|\bevents?\b|\bmessages?\b|\bqueue\b", re.I)


# ---------------------------------------------------------------------------
# NDC subtype helper
# ---------------------------------------------------------------------------

def _ndc_subtype(label: str) -> str:
    if _NDC_FIREWALL_RE.search(label):
        return "firewall"
    if _NDC_GATEWAY_RE.search(label):
        return "gateway"
    if _NDC_VPC_RE.search(label):
        return "vpc"
    if _NDC_LB_RE.search(label):
        return "load-balancer"
    return "network-device"


def _sdc_subtype(label: str) -> str:
    if _SDC_DATABASE_RE.search(label):
        return "database"
    if _SDC_API_RE.search(label):
        return "api"
    if _SDC_LIBRARY_RE.search(label):
        return "library"
    if _SDC_MICROSERVICE_RE.search(label):
        return "microservice"
    return "service"


def _eda_subtype(label: str) -> str:
    if _EDA_STORE_RE.search(label):
        return "event-store"
    if _EDA_PROCESSOR_RE.search(label):
        return "stream-processor"
    if _EDA_TOPIC_RE.search(label):
        return "topic"
    return "event-service"


# ---------------------------------------------------------------------------
# Refinement metadata extraction from graph nodes
# Parses out the special nodes injected by diagram_refiner.py
# ---------------------------------------------------------------------------

def _extract_refine_metadata_ndc(nodes: list[dict]) -> dict[str, str]:
    """Extract NDC refinement answers embedded as special nodes."""
    meta: dict[str, str] = {}
    for node in nodes:
        nid = node.get("id", "").upper()
        label = node.get("label", "")
        if nid == "BCAP":
            m = _BCAP_LABEL_RE.search(label)
            meta["bcap"] = m.group(1).strip() if m else "DISA BCAP"
        elif nid == "MICROSEG":
            m = _MICROSEG_LABEL_RE.search(label)
            meta["microsegmentation"] = m.group(1).strip() if m else label
        elif nid == "HA":
            m = _HA_LABEL_RE.search(label)
            meta["ha_mode"] = m.group(1).strip() if m else label
        elif nid == "MGMT":
            m = _MGMT_LABEL_RE.search(label)
            meta["mgmt_plane"] = m.group(1).strip() if m else label
    return meta


def _extract_refine_metadata_sdc(nodes: list[dict]) -> dict[str, str]:
    """Extract SDC refinement answers embedded as special nodes."""
    meta: dict[str, str] = {}
    for node in nodes:
        nid = node.get("id", "").upper()
        label = node.get("label", "")
        if nid == "CB":
            m = _CB_LABEL_RE.search(label)
            meta["circuit_breaker"] = m.group(1).strip() if m else label
        elif nid == "VER":
            m = _VER_LABEL_RE.search(label)
            meta["api_versioning"] = m.group(1).strip() if m else label
        elif nid == "MESH":
            m = _MESH_LABEL_RE.search(label)
            meta["service_mesh"] = m.group(1).strip() if m else label
        elif nid == "ZT":
            m = _ZT_LABEL_RE.search(label)
            meta["zero_trust_level"] = m.group(1).strip() if m else label
    return meta


def _extract_refine_metadata_eda(nodes: list[dict]) -> dict[str, str]:
    """Extract EDA refinement answers embedded as special nodes."""
    meta: dict[str, str] = {}
    for node in nodes:
        nid = node.get("id", "").upper()
        label = node.get("label", "")
        if nid == "DLQ":
            meta["dlq"] = "Dead Letter Queue"
        elif nid == "CG":
            m = _CG_LABEL_RE.search(label)
            meta["consumer_group"] = m.group(1).strip() if m else label
        elif nid == "DELIVERY":
            m = _DELIVERY_LABEL_RE.search(label)
            meta["delivery"] = m.group(1).strip() if m else label
        elif nid == "SCHEMA":
            m = _SCHEMA_LABEL_RE.search(label)
            meta["schema_evolution"] = m.group(1).strip() if m else label
    return meta


# ---------------------------------------------------------------------------
# NDC spec builder
# ---------------------------------------------------------------------------

def _build_ndc_spec(
    nodes: list[dict],
    edges: list[dict],
    refine_answers: dict[str, str],
) -> dict[str, Any]:
    # Filter out refinement-injected nodes for topology analysis
    refine_node_ids = _NDC_REFINE_NODE_IDS | _SDC_REFINE_NODE_IDS | _EDA_REFINE_NODE_IDS
    topo_nodes = [n for n in nodes if n.get("id", "").upper() not in refine_node_ids]
    node_map = {n["id"]: n for n in topo_nodes}

    # Collect zones from nodes
    zones: list[str] = sorted({
        n.get("zone", "") for n in topo_nodes if n.get("zone")
    })

    # Detect encryption from edge labels (most common pattern)
    encryption_values: list[str] = []
    for edge in edges:
        label = edge.get("label", "")
        m = _ENCRYPTION_RE.search(label)
        if m:
            encryption_values.append(m.group(1))
    encryption = encryption_values[0] if encryption_values else refine_answers.get("encryption", "")

    # Pull remaining answers from refine_answers (in-memory or persisted)
    # or from detected graph nodes
    node_meta = _extract_refine_metadata_ndc(nodes)
    bcap = refine_answers.get("bcap") or node_meta.get("bcap", "")
    microseg = refine_answers.get("microseg") or node_meta.get("microsegmentation", "")
    ha_mode = refine_answers.get("ha") or node_meta.get("ha_mode", "")
    mgmt_plane = refine_answers.get("mgmt_plane") or node_meta.get("mgmt_plane", "")

    # Build topology_metadata
    topology_metadata: dict[str, Any] = {
        "node_count": len(topo_nodes),
        "edge_count": len(edges),
        "zones": zones,
    }
    if encryption:
        topology_metadata["encryption"] = encryption
    if bcap:
        topology_metadata["bcap"] = bcap
    if microseg:
        topology_metadata["microsegmentation"] = microseg
    if ha_mode:
        topology_metadata["ha_mode"] = ha_mode
    if mgmt_plane:
        topology_metadata["mgmt_plane"] = mgmt_plane

    topology_metadata["nodes"] = [
        {
            "id": n["id"],
            "label": n.get("label", ""),
            "subtype": _ndc_subtype(n.get("label", "")),
            **({"zone": n["zone"]} if n.get("zone") else {}),
        }
        for n in topo_nodes
    ]

    # Build flow_definitions from edges
    flow_definitions: list[dict] = []
    for edge in edges:
        label = edge.get("label", "")
        src = node_map.get(edge.get("source", ""), {})
        dst = node_map.get(edge.get("target", ""), {})

        m_port = _PORT_WITH_PROTO_RE.search(label)
        port = m_port.group(1) if m_port else ""
        if not port:
            m2 = _PORT_BARE_RE.search(label)
            port = (m2.group(1) or m2.group(2)) if m2 else "443"

        m_proto = _PROTO_RE.search(label)
        protocol = m_proto.group(1).upper() if m_proto else "TCP"

        etype = edge.get("type", "arrow")
        direction = "bidirectional" if any(t in etype for t in ("open", "both")) else "outbound"

        flow_definitions.append({
            "source": src.get("label") or edge.get("source", ""),
            "destination": dst.get("label") or edge.get("target", ""),
            "source_zone": src.get("zone") or src.get("label") or "unknown",
            "destination_zone": dst.get("zone") or dst.get("label") or "unknown",
            "protocol": protocol,
            "port": port,
            "direction": direction,
            "classification": "CUI",
        })

    # Build security_policies
    security_policies: dict[str, Any] = {}
    if encryption:
        security_policies["encryption"] = encryption
    if bcap:
        security_policies["bcap"] = bcap
    if microseg:
        security_policies["microsegmentation"] = microseg
    if ha_mode:
        security_policies["ha_mode"] = ha_mode
    if mgmt_plane:
        security_policies["mgmt_plane_separation"] = mgmt_plane

    # Compliance requirements
    compliance_requirements: dict[str, Any] = {
        "classification": "CUI // SP-CTI",
        "impact_level": "IL4",
        "frameworks": ["NIST 800-53 rev5", "CMMC 2.0 Level 2"],
        "controls": ["AC-17", "SC-8", "SC-28", "SI-2"],
    }

    return {
        "topology_metadata": topology_metadata,
        "flow_definitions": flow_definitions,
        "security_policies": security_policies,
        "compliance_requirements": compliance_requirements,
    }


# ---------------------------------------------------------------------------
# SDC spec builder
# ---------------------------------------------------------------------------

def _build_sdc_spec(
    nodes: list[dict],
    edges: list[dict],
    refine_answers: dict[str, str],
) -> dict[str, Any]:
    refine_node_ids = _NDC_REFINE_NODE_IDS | _SDC_REFINE_NODE_IDS | _EDA_REFINE_NODE_IDS
    svc_nodes = [n for n in nodes if n.get("id", "").upper() not in refine_node_ids]
    node_map = {n["id"]: n for n in svc_nodes}

    # Build upstream/downstream maps
    upstream_map: dict[str, list[str]] = {}
    downstream_map: dict[str, list[str]] = {}
    for edge in edges:
        src_id = edge.get("source", "")
        tgt_id = edge.get("target", "")
        src_label = node_map.get(src_id, {}).get("label", src_id)
        tgt_label = node_map.get(tgt_id, {}).get("label", tgt_id)
        downstream_map.setdefault(src_id, []).append(tgt_label)
        upstream_map.setdefault(tgt_id, []).append(src_label)

    # service_definitions
    service_definitions: list[dict] = []
    for node in svc_nodes:
        nid = node["id"]
        label = node.get("label", "")
        service_definitions.append({
            "name": label,
            "id": nid,
            "subtype": _sdc_subtype(label),
            "upstream": upstream_map.get(nid, []),
            "downstream": downstream_map.get(nid, []),
        })

    # api_contracts from edges
    api_contracts: list[dict] = []
    for edge in edges:
        label = edge.get("label", "")
        src = node_map.get(edge.get("source", ""), {})
        dst = node_map.get(edge.get("target", ""), {})
        dst_slug = (dst.get("label") or "api").lower().replace(" ", "-")

        m_method = _HTTP_METHOD_RE.search(label)
        method = m_method.group(1).upper() if m_method else "GET"

        m_ep = _ENDPOINT_RE.search(label)
        endpoint = m_ep.group(1) if m_ep else f"/{dst_slug}"

        m_auth = _AUTH_RE.search(label)
        auth = m_auth.group(1) if m_auth else refine_answers.get("auth_scheme", "mTLS")

        m_sla = _SLA_RE.search(label)
        if m_sla:
            sla = f"{m_sla.group(1)}ms" if m_sla.group(1) else m_sla.group(2)
        else:
            sla = "200ms"

        api_contracts.append({
            "endpoint": endpoint,
            "method": method,
            "auth": auth,
            "upstream": src.get("label") or edge.get("source", ""),
            "downstream": dst.get("label") or edge.get("target", ""),
            "sla": sla,
        })

    # auth_schemes from refine_answers + graph node detection
    node_meta = _extract_refine_metadata_sdc(nodes)
    auth_scheme = refine_answers.get("auth_scheme") or node_meta.get("auth_scheme", "mTLS")
    circuit_breaker = refine_answers.get("circuit_breaker") or node_meta.get("circuit_breaker", "")
    service_mesh = refine_answers.get("service_mesh") or node_meta.get("service_mesh", "")
    zt_level = refine_answers.get("zt_level") or node_meta.get("zero_trust_level", "")
    api_versioning = refine_answers.get("api_versioning") or node_meta.get("api_versioning", "")

    auth_schemes: dict[str, Any] = {"scheme": auth_scheme}
    if circuit_breaker:
        auth_schemes["circuit_breaker"] = circuit_breaker
    if service_mesh:
        auth_schemes["service_mesh"] = service_mesh
    if zt_level:
        auth_schemes["zero_trust_level"] = zt_level

    # sla_targets
    sla_values = [c["sla"] for c in api_contracts if c.get("sla")]
    sla_targets: dict[str, Any] = {
        "default_response_time": sla_values[0] if sla_values else "200ms",
    }
    if api_versioning:
        sla_targets["versioning"] = api_versioning

    return {
        "service_definitions": service_definitions,
        "api_contracts": api_contracts,
        "auth_schemes": auth_schemes,
        "sla_targets": sla_targets,
    }


# ---------------------------------------------------------------------------
# EDA spec builder
# ---------------------------------------------------------------------------

_RETENTION_UNIT_MAP = {"d": "d", "h": "h", "w": "w", "m": "m"}


def _build_eda_spec(
    nodes: list[dict],
    edges: list[dict],
    refine_answers: dict[str, str],
) -> dict[str, Any]:
    refine_node_ids = _NDC_REFINE_NODE_IDS | _SDC_REFINE_NODE_IDS | _EDA_REFINE_NODE_IDS
    svc_nodes = [n for n in nodes if n.get("id", "").upper() not in refine_node_ids]
    node_map = {n["id"]: n for n in svc_nodes}

    # topic_definitions from edges
    topic_definitions: list[dict] = []
    bindings_by_topic: dict[str, dict[str, Any]] = {}

    for edge in edges:
        label = edge.get("label", "")
        src = node_map.get(edge.get("source", ""), {})
        dst = node_map.get(edge.get("target", ""), {})

        m_topic = _TOPIC_RE.search(label)
        if m_topic:
            topic = m_topic.group(1) or m_topic.group(2)
        else:
            src_slug = (src.get("label") or "producer").lower().replace(" ", "_")
            topic = label.strip() or f"{src_slug}.events"

        base = topic.rsplit(".", 1)[-1]
        schema_ref = f"schemas/{base}.avsc"

        m_ret = _RETENTION_RE.search(label)
        if m_ret:
            unit = _RETENTION_UNIT_MAP.get(m_ret.group(2)[0].lower(), "d")
            retention = f"{m_ret.group(1)}{unit}"
        else:
            retention = "7d"

        m_ord = _ORDERING_RE.search(label)
        ordering = m_ord.group(1).lower() if m_ord else refine_answers.get("ordering", "ordered")

        producer = src.get("label") or edge.get("source", "")
        consumer = dst.get("label") or edge.get("target", "")

        topic_definitions.append({
            "name": topic,
            "schema_ref": schema_ref,
            "producer": producer,
            "consumer": consumer,
            "retention": retention,
            "ordering": ordering,
        })

        # Aggregate bindings by topic
        if topic not in bindings_by_topic:
            bindings_by_topic[topic] = {"topic": topic, "producers": [], "consumers": []}
        if producer and producer not in bindings_by_topic[topic]["producers"]:
            bindings_by_topic[topic]["producers"].append(producer)
        if consumer and consumer not in bindings_by_topic[topic]["consumers"]:
            bindings_by_topic[topic]["consumers"].append(consumer)

    producer_consumer_bindings = list(bindings_by_topic.values())

    # messaging_config from refine_answers + graph node detection
    node_meta = _extract_refine_metadata_eda(nodes)
    ordering = refine_answers.get("ordering") or node_meta.get("ordering", "")
    dlq = refine_answers.get("dlq") or ("Dead Letter Queue" if "dlq" in node_meta else "")
    consumer_group = refine_answers.get("consumer_group") or node_meta.get("consumer_group", "")
    delivery = refine_answers.get("delivery") or node_meta.get("delivery", "")
    schema_evol = refine_answers.get("schema") or node_meta.get("schema_evolution", "")

    messaging_config: dict[str, Any] = {}
    if ordering:
        messaging_config["ordering"] = ordering
    if dlq:
        messaging_config["dlq"] = dlq
    if consumer_group:
        messaging_config["consumer_group"] = consumer_group
    if delivery:
        messaging_config["delivery"] = delivery
    if schema_evol:
        messaging_config["schema_evolution"] = schema_evol

    return {
        "topic_definitions": topic_definitions,
        "producer_consumer_bindings": producer_consumer_bindings,
        "messaging_config": messaging_config,
    }


_SPEC_BUILDERS: dict[str, Any] = {
    "ndc": _build_ndc_spec,
    "sdc": _build_sdc_spec,
    "eda": _build_eda_spec,
}

# ---------------------------------------------------------------------------
# Database helpers (mirrors ppsm_extractor)
# ---------------------------------------------------------------------------


def _load_session(conn, session_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, canvas_type, topology_id, mode, metadata "
        "FROM nc_simulation_sessions WHERE id = %s",
        (session_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"nc_simulation_sessions: session not found: {session_id}")
    return dict(row)


def _load_graph_json(conn, session: dict[str, Any]) -> dict[str, Any]:
    """Resolve graph_json: refined_graph_json in metadata > topologies table."""
    meta_raw = session.get("metadata") or "{}"
    try:
        meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
    except json.JSONDecodeError:
        meta = {}

    if "refined_graph_json" in meta:
        return meta["refined_graph_json"]

    topology_id = session.get("topology_id")
    if topology_id:
        row = conn.execute(
            "SELECT graph_json FROM topologies WHERE id = %s",
            (topology_id,),
        ).fetchone()
        if row and row[0]:
            try:
                return json.loads(row[0]) if isinstance(row[0], str) else row[0]
            except json.JSONDecodeError:
                pass

    return {"nodes": [], "edges": []}


def _load_refine_answers(session: dict[str, Any]) -> dict[str, str]:
    """Load refine answers from session metadata if persisted."""
    meta_raw = session.get("metadata") or "{}"
    try:
        meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
    except json.JSONDecodeError:
        meta = {}
    return meta.get("refine_answers") or {}


def _get_inmemory_answers(session_id: str) -> dict[str, str]:
    """Read refine answers from diagram_refiner in-memory state if still active."""
    try:
        from tools.simulation.diagram_refiner import _SESSIONS  # type: ignore[attr-defined]
        state = _SESSIONS.get(session_id)
        if state:
            return state.get("answers", {})
    except (ImportError, AttributeError):
        pass
    return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_spec(session_id: str, canvas_type: str) -> dict[str, Any]:
    """Generate a structured spec dict for a TFW REFINE session.

    Args:
        session_id:  nc_simulation_sessions.id
        canvas_type: ndc | sdc | eda (or any registered alias)

    Returns:
        dict with keys:
          spec_version, generated_at, session_id, canvas_type, canvas_display,
          classification, plus canvas-specific sections (see module docstring).

    Raises:
        ValueError: session not found or unsupported canvas type.
    """
    resolved = _resolve(canvas_type)
    builder = _SPEC_BUILDERS.get(resolved)
    if builder is None:
        supported = list(_SPEC_BUILDERS)
        raise ValueError(
            f"No spec builder for canvas type '{canvas_type}' "
            f"(resolved: '{resolved}'). Supported: {supported}"
        )

    conn = get_connection()
    try:
        session = _load_session(conn, session_id)
        graph = _load_graph_json(conn, session)
        persisted_answers = _load_refine_answers(session)
    finally:
        conn.close()

    # Prefer in-memory answers (active session) over persisted answers
    inmemory_answers = _get_inmemory_answers(session_id)
    refine_answers = {**persisted_answers, **inmemory_answers}

    nodes: list[dict] = graph.get("nodes", [])
    edges: list[dict] = graph.get("edges", [])

    canvas_display = get_display_name(canvas_type)
    spec_sections = builder(nodes, edges, refine_answers)

    return {
        "spec_version": SPEC_VERSION,
        "generated_at": _now_iso(),
        "session_id": session_id,
        "canvas_type": resolved,
        "canvas_display": canvas_display,
        "classification": "CUI // SP-CTI",
        **spec_sections,
    }


# ---------------------------------------------------------------------------
# YAML serialization helper
# ---------------------------------------------------------------------------


def spec_to_yaml(spec: dict[str, Any]) -> str:
    """Serialize spec dict to YAML string.

    Uses PyYAML if available; falls back to JSON (valid YAML superset).
    """
    try:
        import yaml  # type: ignore[import]
        return yaml.dump(spec, default_flow_style=False, allow_unicode=True, sort_keys=False)
    except ImportError:
        return json.dumps(spec, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate a canvas-aware structured spec from a TFW REFINE session.",
    )
    p.add_argument("--session-id", required=True, help="nc_simulation_sessions.id")
    p.add_argument(
        "--canvas-type",
        required=True,
        choices=list(_SPEC_BUILDERS) + list(_CANVAS_ALIASES),
        help="Canvas type (ndc | sdc | eda or alias)",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    p.add_argument("--yaml", action="store_true", help="Emit YAML to stdout (default if neither flag)")
    p.add_argument("--out", help="Write spec to this file path (.yaml or .json)")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    spec = generate_spec(args.session_id, args.canvas_type)

    # Determine output format
    use_yaml = args.yaml or (not args.json)

    output_str = spec_to_yaml(spec) if use_yaml else json.dumps(spec, indent=2, ensure_ascii=False)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(output_str)
        print(f"Written: {out_path}")

    if args.json:
        print(json.dumps(spec, indent=2, ensure_ascii=False))
    elif args.yaml or not args.out:
        print(output_str)
    else:
        # Summary when only --out was specified
        canvas_display = get_display_name(args.canvas_type)
        resolved = _resolve(args.canvas_type)
        print(f"[{canvas_display}] Spec v{SPEC_VERSION} — {resolved.upper()} — {args.session_id[:8]}...")


if __name__ == "__main__":
    main()
