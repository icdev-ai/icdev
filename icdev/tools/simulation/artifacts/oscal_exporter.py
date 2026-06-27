#!/usr/bin/env python3
# CUI // SP-CTI
"""OSCAL Exporter — canvas-aware OSCAL Component Definition generator.

Generates an OSCAL 1.1.2 Component Definition document from a TFW session's
topology graph. Component types are determined by canvas type:

  ndc  -> network components (type: "interconnection")
           Node labels classified as: firewall, gateway, vpc, load-balancer,
           network-device
  sdc  -> software components (type: "software")
           Node labels classified as: microservice, api, library, database,
           service
  eda  -> data components (type: "service")
           Node labels classified as: topic, stream-processor, event-store,
           event-service

Control implementations are sourced from cis_generator (T16) when available;
falls back to empty implementations if T16 is not yet deployed.

Public surface:
  generate_oscal(session_id, canvas_type) -> dict

Data source (in priority order):
  1. nc_simulation_sessions.metadata["refined_graph_json"]  (diagram_refiner output)
  2. topologies.graph_json  (via nc_simulation_sessions.topology_id)
  3. Empty graph  (returns minimal Component Definition with no components)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.canvas.canvas_registry import get_display_name
from tools.db.storage import get_connection

# ---------------------------------------------------------------------------
# OSCAL constants
# ---------------------------------------------------------------------------

OSCAL_VERSION = "1.1.2"
OSCAL_NS = "http://csrc.nist.gov/ns/oscal/1.0"
ICDEV_NS = "https://icdev.io/ns/simulation/1.0"

# CIS Controls v8 profile URI used as source for control implementations
CIS_PROFILE_URI = "https://www.cisecurity.org/controls/v8"

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

# OSCAL component type per resolved canvas type
_CANVAS_COMPONENT_TYPE: dict[str, str] = {
    "ndc": "interconnection",
    "sdc": "software",
    "eda": "service",
}


def _resolve(canvas_type: str) -> str:
    ct = canvas_type.lower()
    return _CANVAS_ALIASES.get(ct, ct)


# ---------------------------------------------------------------------------
# Node subtype classifiers
# ---------------------------------------------------------------------------

_NDC_FIREWALL_RE = re.compile(r"\bfw\b|\bfirewall\b|\bngfw\b|\bwaf\b|\bacl\b", re.I)
_NDC_GATEWAY_RE = re.compile(r"\bgateway\b|\bigw\b|\bnat\b|\bapi[.\s-]?gw\b|\bvpn\b", re.I)
_NDC_VPC_RE = re.compile(r"\bvpc\b|\bvnet\b|\bsubnet\b|\bsegment\b", re.I)
_NDC_LB_RE = re.compile(r"\blb\b|\balb\b|\bnlb\b|\belb\b|load.?balanc", re.I)
_NDC_ROUTER_RE = re.compile(r"\brouter\b|\bswitch\b|\bhub\b", re.I)

_SDC_MICROSERVICE_RE = re.compile(r"\bmicroservice\b|\bsvc\b|\bservice\b", re.I)
_SDC_API_RE = re.compile(r"\bapi\b|\brest\b|\bgraphql\b|\bgrpc\b|\bendpoint\b", re.I)
_SDC_LIBRARY_RE = re.compile(r"\blib\b|\blibrary\b|\bsdk\b|\bpackage\b", re.I)
_SDC_DATABASE_RE = re.compile(r"\bdb\b|\bdatabase\b|\bpostgres\b|\bmysql\b|\brds\b|\bmongo\b|\belastic\b", re.I)

_EDA_TOPIC_RE = re.compile(r"\btopic\b|\bevents?\b|\bmessages?\b|\bqueue\b", re.I)
_EDA_PROCESSOR_RE = re.compile(r"\bprocessor\b|\bflink\b|\bksql\b|\bkafka.streams\b|\bconsumer\b|\bworker\b", re.I)
_EDA_STORE_RE = re.compile(r"\bkafka\b|\bkinesis\b|\bsqs\b|\bsns\b|\bpubsub\b|\bnats\b|\bpulsar\b|\bactivemq\b|\brabbitmq\b", re.I)


def _ndc_subtype(label: str) -> str:
    if _NDC_FIREWALL_RE.search(label):
        return "firewall"
    if _NDC_GATEWAY_RE.search(label):
        return "gateway"
    if _NDC_VPC_RE.search(label):
        return "vpc"
    if _NDC_LB_RE.search(label):
        return "load-balancer"
    if _NDC_ROUTER_RE.search(label):
        return "router"
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


_SUBTYPE_FNS: dict[str, Any] = {
    "ndc": _ndc_subtype,
    "sdc": _sdc_subtype,
    "eda": _eda_subtype,
}

# ---------------------------------------------------------------------------
# OSCAL helpers
# ---------------------------------------------------------------------------


def _generate_uuid() -> str:
    return str(uuid.uuid4())


def _oscal_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _build_metadata(session_id: str, canvas_type: str, canvas_display: str) -> dict:
    now = _oscal_timestamp()
    return {
        "title": f"Component Definition — {canvas_display} Session {session_id}",
        "last-modified": now,
        "version": "1.0",
        "oscal-version": OSCAL_VERSION,
        "roles": [
            {"id": "system-owner", "title": "System Owner"},
            {"id": "isso", "title": "Information System Security Officer"},
            {"id": "component-provider", "title": "Component Provider"},
        ],
        "parties": [
            {
                "uuid": _generate_uuid(),
                "type": "organization",
                "name": "ICDEV™ Simulation Engine",
                "remarks": "Auto-generated by ICDEV™ TFW canvas simulation.",
            }
        ],
        "remarks": (
            f"Generated by ICDEV™ OSCAL Exporter. "
            f"Canvas: {canvas_display}. "
            f"Session: {session_id}. "
            f"Classification: CUI // SP-CTI."
        ),
    }


def _build_control_implementations(controls: list[dict]) -> list[dict]:
    """Build the control-implementations block from cis_generator controls."""
    if not controls:
        return []

    impl_reqs = []
    for ctrl in controls:
        control_id = ctrl.get("control_id", "")
        if not control_id:
            continue
        # Normalize to OSCAL lowercase hyphenated format
        oscal_cid = re.sub(r"\((\d+)\)", r".\1", control_id.strip().lower())

        description = ctrl.get("description") or ctrl.get("title") or (
            f"Control {oscal_cid}: {ctrl.get('implementation_status', 'planned')}."
        )

        req: dict = {
            "uuid": _generate_uuid(),
            "control-id": oscal_cid,
            "description": description,
            "props": [
                {
                    "name": "implementation-status",
                    "ns": OSCAL_NS,
                    "value": ctrl.get("implementation_status", "planned"),
                },
            ],
        }
        if ctrl.get("title") and ctrl.get("title") != description:
            req["props"].append(
                {
                    "name": "control-title",
                    "ns": ICDEV_NS,
                    "value": ctrl["title"],
                }
            )
        impl_reqs.append(req)

    if not impl_reqs:
        return []

    return [
        {
            "uuid": _generate_uuid(),
            "source": CIS_PROFILE_URI,
            "description": "CIS Controls v8 implementations derived from canvas simulation.",
            "implemented-requirements": impl_reqs,
        }
    ]


# ---------------------------------------------------------------------------
# Component builders per canvas type
# ---------------------------------------------------------------------------


def _build_ndc_components(
    nodes: list[dict],
    edges: list[dict],
    controls: list[dict],
) -> list[dict]:
    """Build OSCAL components for NDC (network) nodes."""
    ctrl_impls = _build_control_implementations(controls)
    components: list[dict] = []

    node_map = {n["id"]: n for n in nodes}
    # Collect edge relationships for props
    edge_targets: dict[str, list[str]] = {}
    for edge in edges:
        src = edge.get("source", "")
        tgt_node = node_map.get(edge.get("target", ""), {})
        tgt_label = tgt_node.get("label", edge.get("target", ""))
        edge_targets.setdefault(src, []).append(tgt_label)

    for node in nodes:
        label = node.get("label", "unknown")
        node_id = node.get("id", _generate_uuid())
        subtype = _ndc_subtype(label)
        zone = node.get("zone", "")

        props = [
            {"name": "canvas-type", "ns": ICDEV_NS, "value": "ndc"},
            {"name": "node-id", "ns": ICDEV_NS, "value": node_id},
            {"name": "subtype", "ns": ICDEV_NS, "value": subtype},
            {"name": "classification", "ns": OSCAL_NS, "value": "CUI"},
        ]
        if zone:
            props.append({"name": "network-zone", "ns": ICDEV_NS, "value": zone})
        connected_to = edge_targets.get(node_id, [])
        if connected_to:
            props.append(
                {
                    "name": "connects-to",
                    "ns": ICDEV_NS,
                    "value": ", ".join(connected_to[:5]),
                }
            )

        comp: dict = {
            "uuid": _generate_uuid(),
            "type": "interconnection",
            "title": label,
            "description": (
                f"{subtype.replace('-', ' ').title()} component '{label}'"
                + (f" in zone '{zone}'" if zone else "")
                + ". Canvas: Network Design Canvas."
            ),
            "props": props,
        }
        if ctrl_impls:
            comp["control-implementations"] = ctrl_impls
        components.append(comp)

    return components


def _build_sdc_components(
    nodes: list[dict],
    edges: list[dict],
    controls: list[dict],
) -> list[dict]:
    """Build OSCAL components for SDC (software) nodes."""
    ctrl_impls = _build_control_implementations(controls)
    components: list[dict] = []

    node_map = {n["id"]: n for n in nodes}
    upstream_map: dict[str, list[str]] = {}
    downstream_map: dict[str, list[str]] = {}
    for edge in edges:
        src_id = edge.get("source", "")
        tgt_id = edge.get("target", "")
        src_label = node_map.get(src_id, {}).get("label", src_id)
        tgt_label = node_map.get(tgt_id, {}).get("label", tgt_id)
        downstream_map.setdefault(src_id, []).append(tgt_label)
        upstream_map.setdefault(tgt_id, []).append(src_label)

    for node in nodes:
        label = node.get("label", "unknown")
        node_id = node.get("id", _generate_uuid())
        subtype = _sdc_subtype(label)

        props = [
            {"name": "canvas-type", "ns": ICDEV_NS, "value": "sdc"},
            {"name": "node-id", "ns": ICDEV_NS, "value": node_id},
            {"name": "subtype", "ns": ICDEV_NS, "value": subtype},
            {"name": "classification", "ns": OSCAL_NS, "value": "CUI"},
        ]
        upstreams = upstream_map.get(node_id, [])
        if upstreams:
            props.append({"name": "upstream", "ns": ICDEV_NS, "value": ", ".join(upstreams[:5])})
        downstreams = downstream_map.get(node_id, [])
        if downstreams:
            props.append({"name": "downstream", "ns": ICDEV_NS, "value": ", ".join(downstreams[:5])})

        comp: dict = {
            "uuid": _generate_uuid(),
            "type": "software",
            "title": label,
            "description": (
                f"{subtype.replace('-', ' ').title()} component '{label}'. "
                f"Canvas: Security Design Canvas."
            ),
            "props": props,
        }
        if ctrl_impls:
            comp["control-implementations"] = ctrl_impls
        components.append(comp)

    return components


def _build_eda_components(
    nodes: list[dict],
    edges: list[dict],
    controls: list[dict],
) -> list[dict]:
    """Build OSCAL components for EDA (event-driven / data) nodes."""
    ctrl_impls = _build_control_implementations(controls)
    components: list[dict] = []

    node_map = {n["id"]: n for n in nodes}
    producer_map: dict[str, list[str]] = {}
    consumer_map: dict[str, list[str]] = {}
    for edge in edges:
        src_id = edge.get("source", "")
        tgt_id = edge.get("target", "")
        src_label = node_map.get(src_id, {}).get("label", src_id)
        tgt_label = node_map.get(tgt_id, {}).get("label", tgt_id)
        producer_map.setdefault(tgt_id, []).append(src_label)
        consumer_map.setdefault(src_id, []).append(tgt_label)

    for node in nodes:
        label = node.get("label", "unknown")
        node_id = node.get("id", _generate_uuid())
        subtype = _eda_subtype(label)

        props = [
            {"name": "canvas-type", "ns": ICDEV_NS, "value": "eda"},
            {"name": "node-id", "ns": ICDEV_NS, "value": node_id},
            {"name": "subtype", "ns": ICDEV_NS, "value": subtype},
            {"name": "classification", "ns": OSCAL_NS, "value": "CUI"},
        ]
        producers = producer_map.get(node_id, [])
        if producers:
            props.append({"name": "producers", "ns": ICDEV_NS, "value": ", ".join(producers[:5])})
        consumers = consumer_map.get(node_id, [])
        if consumers:
            props.append({"name": "consumers", "ns": ICDEV_NS, "value": ", ".join(consumers[:5])})

        comp: dict = {
            "uuid": _generate_uuid(),
            "type": "service",
            "title": label,
            "description": (
                f"{subtype.replace('-', ' ').title()} component '{label}'. "
                f"Canvas: Event-Driven Architecture."
            ),
            "props": props,
        }
        if ctrl_impls:
            comp["control-implementations"] = ctrl_impls
        components.append(comp)

    return components


_COMPONENT_BUILDERS: dict[str, Any] = {
    "ndc": _build_ndc_components,
    "sdc": _build_sdc_components,
    "eda": _build_eda_components,
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


# ---------------------------------------------------------------------------
# CIS controls integration (T16 — optional dependency)
# ---------------------------------------------------------------------------


def _load_cis_controls(session_id: str, canvas_type: str) -> list[dict]:
    """Load CIS controls from cis_generator if available (T16 dependency)."""
    try:
        from tools.simulation.artifacts.cis_generator import get_controls  # type: ignore[import]
        return get_controls(session_id, canvas_type)
    except ImportError:
        return []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_oscal(session_id: str, canvas_type: str) -> dict:
    """Generate an OSCAL 1.1.2 Component Definition for a TFW session.

    Args:
        session_id:  nc_simulation_sessions.id
        canvas_type: ndc | sdc | eda (or any registered alias)

    Returns:
        dict — OSCAL Component Definition document (1.1.2).
        Top-level key: "component-definition".
        Components are typed per canvas:
          ndc -> "interconnection" (firewall, gateway, vpc, ...)
          sdc -> "software" (microservice, api, library, ...)
          eda -> "service" (topic, stream-processor, event-store, ...)

    Raises:
        ValueError: session not found or unsupported canvas type.
    """
    resolved = _resolve(canvas_type)
    builder = _COMPONENT_BUILDERS.get(resolved)
    if builder is None:
        supported = list(_COMPONENT_BUILDERS)
        raise ValueError(
            f"No OSCAL exporter for canvas type '{canvas_type}' "
            f"(resolved: '{resolved}'). Supported: {supported}"
        )

    conn = get_connection()
    try:
        session = _load_session(conn, session_id)
        graph = _load_graph_json(conn, session)
    finally:
        conn.close()

    nodes: list[dict] = graph.get("nodes", [])
    edges: list[dict] = graph.get("edges", [])

    controls = _load_cis_controls(session_id, canvas_type)
    components = builder(nodes, edges, controls)

    canvas_display = get_display_name(canvas_type)
    doc_uuid = _generate_uuid()

    return {
        "component-definition": {
            "uuid": doc_uuid,
            "metadata": _build_metadata(session_id, resolved, canvas_display),
            "components": components,
            "back-matter": {
                "resources": [
                    {
                        "uuid": _generate_uuid(),
                        "title": "CIS Controls v8",
                        "description": "Center for Internet Security Controls v8 benchmark.",
                        "rlinks": [{"href": CIS_PROFILE_URI}],
                    }
                ]
            },
        }
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate a canvas-aware OSCAL Component Definition from a TFW session.",
    )
    p.add_argument("--session-id", required=True, help="nc_simulation_sessions.id")
    p.add_argument(
        "--canvas-type",
        required=True,
        choices=list(_COMPONENT_BUILDERS) + list(_CANVAS_ALIASES),
        help="Canvas type (ndc | sdc | eda or alias)",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    p.add_argument("--out", help="Write OSCAL JSON to this file path")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    doc = generate_oscal(args.session_id, args.canvas_type)

    cd = doc.get("component-definition", {})
    n_components = len(cd.get("components", []))

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
        print(f"Written: {out_path}")

    if args.json:
        print(json.dumps(doc, indent=2))
    else:
        canvas_display = get_display_name(args.canvas_type)
        print(f"[{canvas_display}] OSCAL Component Definition — {n_components} component(s)")
        for comp in cd.get("components", []):
            subtype = next(
                (p["value"] for p in comp.get("props", []) if p["name"] == "subtype"),
                "unknown",
            )
            print(f"  {comp['type']:14s}  {subtype:20s}  {comp['title']}")


if __name__ == "__main__":
    main()
