# CUI // SP-CTI — ICDEV Data Design Canvas ODPS Exporter
"""Open Data Product Standard (ODPS) v3 exporter for DDC graphs.

Wraps a DDC data design as an ODPS-conformant data product with:
  - Input ports  — upstream flows feeding the product (ETL, CDC, replication)
  - Output ports — downstream-consumable datasets with field schemas
  - SLAs         — computed from backup and retention controls plus compliance rules
  - Security     — classification, RBAC, encryption, audit markings

Specification references:
  https://github.com/bitol-io/open-data-product-standard
  https://github.com/agile-lab-dev/Data-Product-Specification

Stdlib-only (pyyaml for serialisation; no network/DB calls).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

try:
    import yaml as _yaml  # pyyaml>=6.0
    _HAS_YAML = True
except ImportError:  # pragma: no cover
    _HAS_YAML = False

# ── Flow type classification ──────────────────────────────────────────────────
# Flows that represent data entering the product from upstream systems
_INPUT_FLOW_TYPES = {"flow-etl", "flow-cdc", "flow-replication", "flow-backup"}
# Flows that represent data leaving the product to consumers
_OUTPUT_FLOW_TYPES = {"flow-api", "flow-export", "flow-cross-domain"}

# ODPS format label for each DDC entity type
_ENTITY_FORMAT_MAP = {
    "ent-table": "relational",
    "ent-view": "view",
    "ent-collection": "document",
    "ent-topic": "streaming",
    "ent-cache": "cache",
    "ent-queue": "queue",
    "ent-datalake": "datalake",
    "ent-warehouse": "warehouse",
    "ent-graph": "graph",
    "ent-timeseries": "timeseries",
    "ent-vector": "vector",
    "ent-file": "file",
}

# ODPS data type mapping from DDC column types
_COL_TYPE_MAP = {
    "col-pk": "string",
    "col-fk": "string",
    "col-data": "string",
    "col-pii": "string",
    "col-phi": "string",
    "col-cui": "string",
    "col-secret": "string",
    "col-encrypted": "string",
    "col-audit": "timestamp",
}

# Sensitivity classification for column types
_COL_SENSITIVITY_MAP = {
    "col-pii": "PII",
    "col-phi": "PHI",
    "col-cui": "CUI",
    "col-secret": "SECRET",
    "col-pk": "internal",
    "col-fk": "internal",
    "col-encrypted": "encrypted",
    "col-audit": "internal",
    "col-data": "standard",
}

_NIST_CONTROL_MAP = {
    "ctrl-encryption": ["SC-28", "SC-8"],
    "ctrl-masking": ["AC-3", "AC-6"],
    "ctrl-dlp": ["SI-4", "SI-12"],
    "ctrl-rbac": ["AC-2", "AC-3"],
    "ctrl-audit-log": ["AU-2", "AU-3", "AU-12"],
    "ctrl-retention": ["AU-11", "SI-12"],
    "ctrl-classification": ["MP-3", "AC-16"],
    "ctrl-backup-policy": ["CP-9", "CP-10"],
}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_graph(graph_json) -> tuple[list, list, list]:
    """Return (nodes, edges, boundaries) from a graph dict or JSON string."""
    if isinstance(graph_json, str):
        try:
            g = json.loads(graph_json)
        except json.JSONDecodeError:
            return [], [], []
    else:
        g = graph_json or {}
    return g.get("nodes", []), g.get("edges", []), g.get("boundaries", [])


def _slug(text: str) -> str:
    """Produce a URL-safe slug from arbitrary text."""
    return re.sub(r"[^a-z0-9_-]", "-", text.lower().strip()).strip("-") or "dp"


def _safe_id(node_id: str, prefix: str = "node") -> str:
    """Shorten a UUID-like node ID to a stable 8-char suffix."""
    clean = re.sub(r"[^a-z0-9]", "", node_id.lower())
    return f"{prefix}-{clean[:12]}" if clean else f"{prefix}-unknown"


def _node_label(node: dict) -> str:
    return node.get("label") or node.get("id", "unknown")


def _build_lookup(nodes: list) -> tuple[dict, dict]:
    """Return (ntypes, labels) lookup dicts keyed by node id."""
    ntypes = {n["id"]: n.get("type", "") for n in nodes}
    labels = {n["id"]: _node_label(n) for n in nodes}
    return ntypes, labels


def _entity_columns(entity_id: str, edges: list, nodes_by_id: dict, ntypes: dict) -> list[dict]:
    """Return column nodes directly connected (owned by) an entity."""
    cols = []
    for edge in edges:
        src, tgt = edge.get("source", ""), edge.get("target", "")
        # Column ownership: entity → column (or column → entity)
        if src == entity_id and ntypes.get(tgt, "").startswith("col-"):
            cols.append(nodes_by_id[tgt])
        elif tgt == entity_id and ntypes.get(src, "").startswith("col-"):
            cols.append(nodes_by_id[src])
    return cols


def _entity_controls(entity_id: str, edges: list, ntypes: dict) -> list[str]:
    """Return ctrl-* node types attached to an entity."""
    ctrl_types = []
    for edge in edges:
        src, tgt = edge.get("source", ""), edge.get("target", "")
        if src == entity_id and ntypes.get(tgt, "").startswith("ctrl-"):
            ctrl_types.append(ntypes[tgt])
        elif tgt == entity_id and ntypes.get(src, "").startswith("ctrl-"):
            ctrl_types.append(ntypes[src])
    return ctrl_types


def _entity_boundary_label(entity_id: str, boundaries: list) -> str:
    """Return the classification zone label for an entity, or empty string."""
    for bnd in boundaries:
        if bnd.get("type") == "bnd-classification":
            if entity_id in bnd.get("contained_nodes", []):
                return bnd.get("label", "")
    return ""


def _build_nist_controls(ctrl_types: list[str]) -> list[str]:
    """Map DDC control types to NIST 800-53 control IDs."""
    seen: set[str] = set()
    result = []
    for ct in ctrl_types:
        for nist_id in _NIST_CONTROL_MAP.get(ct, []):
            if nist_id not in seen:
                seen.add(nist_id)
                result.append(nist_id)
    return sorted(result)


def _sla_from_controls(ctrl_types: list[str]) -> dict:
    """Derive SLA targets from the set of controls applied to an entity."""
    has_backup = "ctrl-backup-policy" in ctrl_types
    has_retention = "ctrl-retention" in ctrl_types
    has_encryption = "ctrl-encryption" in ctrl_types
    has_audit = "ctrl-audit-log" in ctrl_types
    return {
        "availability": "99.9%" if has_backup else "99.0%",
        "rto": "4h" if has_backup else "24h",
        "rpo": "1h" if has_backup else "24h",
        "retentionPeriod": "7y" if has_retention else "1y",
        "encryptedAtRest": has_encryption,
        "auditLogged": has_audit,
    }


def _build_input_ports(edges: list, ntypes: dict, labels: dict) -> list[dict]:
    """Build ODPS inputPorts from upstream flow edges."""
    ports: list[dict] = []
    seen_ids: set[str] = set()
    for edge in edges:
        etype = edge.get("type", "")
        if etype not in _INPUT_FLOW_TYPES:
            continue
        src_id = edge.get("source", "")
        tgt_id = edge.get("target", "")
        src_type = ntypes.get(src_id, "")
        # Only process flows where the source is another entity (upstream)
        if not src_type.startswith("ent-"):
            continue
        port_id = _safe_id(f"{src_id}{tgt_id}", "inp")
        if port_id in seen_ids:
            continue
        seen_ids.add(port_id)
        ports.append({
            "id": port_id,
            "name": f"{labels.get(src_id, src_id)} -> {labels.get(tgt_id, tgt_id)}",
            "description": (
                f"Upstream {etype.replace('flow-', '')} feed from "
                f"'{labels.get(src_id, src_id)}'"
            ),
            "type": etype.replace("flow-", ""),
            "sourceSystem": labels.get(src_id, src_id),
            "targetEntity": labels.get(tgt_id, tgt_id),
            "authenticated": bool(edge.get("authenticated", False)),
            "encrypted": bool(edge.get("encrypted", False)),
        })
    return ports


def _build_output_ports(
    entities: list,
    edges: list,
    ntypes: dict,
    labels: dict,
    nodes_by_id: dict,
    boundaries: list,
    classification: str,
) -> list[dict]:
    """Build ODPS outputPorts — one port per entity, with schema fields."""
    ports = []
    for ent in entities:
        ent_id = ent["id"]
        ent_type = ent.get("type", "")
        ent_label = _node_label(ent)
        ctrl_types = _entity_controls(ent_id, edges, ntypes)
        cols = _entity_columns(ent_id, edges, nodes_by_id, ntypes)
        zone_label = _entity_boundary_label(ent_id, boundaries)
        sla = _sla_from_controls(ctrl_types)
        nist_controls = _build_nist_controls(ctrl_types)

        # Determine effective classification from zone or default
        eff_classification = zone_label or classification

        # Build schema fields from attached column nodes
        fields = []
        for col in cols:
            col_type = col.get("type", "col-data")
            fields.append({
                "name": _node_label(col),
                "type": _COL_TYPE_MAP.get(col_type, "string"),
                "sensitivity": _COL_SENSITIVITY_MAP.get(col_type, "standard"),
                "classification": eff_classification,
                "nullable": col_type not in ("col-pk",),
            })

        # Determine access control posture
        has_rbac = "ctrl-rbac" in ctrl_types
        has_masking = "ctrl-masking" in ctrl_types
        has_dlp = "ctrl-dlp" in ctrl_types

        # Build output channels (API, export, or default dataset)
        channels = []
        for edge in edges:
            etype = edge.get("type", "")
            if etype not in _OUTPUT_FLOW_TYPES:
                continue
            if edge.get("source", "") != ent_id:
                continue
            tgt_label = labels.get(edge.get("target", ""), "consumer")
            channels.append({
                "type": etype.replace("flow-", ""),
                "consumer": tgt_label,
                "authenticated": bool(edge.get("authenticated", False)),
                "encrypted": bool(edge.get("encrypted", False)),
            })

        port: dict[str, Any] = {
            "id": _safe_id(ent_id, "out"),
            "name": ent_label,
            "description": (
                f"{_ENTITY_FORMAT_MAP.get(ent_type, ent_type)} data store — "
                f"classification: {eff_classification}"
            ),
            "type": _ENTITY_FORMAT_MAP.get(ent_type, "dataset"),
            "classification": eff_classification,
            "sla": {
                "availability": sla["availability"],
                "rto": sla["rto"],
                "rpo": sla["rpo"],
                "retentionPeriod": sla["retentionPeriod"],
            },
            "security": {
                "encryptedAtRest": sla["encryptedAtRest"],
                "auditLogged": sla["auditLogged"],
                "accessControl": "RBAC" if has_rbac else "DAC",
                "masking": has_masking,
                "dlp": has_dlp,
                "nistControls": nist_controls,
            },
        }
        if fields:
            port["schema"] = {"fields": fields}
        if channels:
            port["outputChannels"] = channels

        ports.append(port)
    return ports


def _build_product_sla(entities: list, edges: list, ntypes: dict) -> dict:
    """Aggregate SLA targets across all entities — take the strictest values."""
    all_ctrl_types: list[str] = []
    for ent in entities:
        all_ctrl_types.extend(_entity_controls(ent["id"], edges, ntypes))
    sla = _sla_from_controls(list(set(all_ctrl_types)))
    return {
        "availability": sla["availability"],
        "rto": sla["rto"],
        "rpo": sla["rpo"],
        "support": {
            "tier": "standard",
            "responseTime": "4h",
            "escalationPath": "data-engineering@icdev",
        },
    }


def _build_compliance_section(entities: list, edges: list, ntypes: dict) -> dict:
    """Build ODPS compliance block from all active NIST controls."""
    all_ctrl_types: set[str] = set()
    for ent in entities:
        all_ctrl_types.update(_entity_controls(ent["id"], edges, ntypes))
    nist_controls = _build_nist_controls(list(all_ctrl_types))
    frameworks = ["NIST-800-53"]
    if any(c.startswith("ctrl-") for c in all_ctrl_types):
        frameworks.append("FedRAMP-Moderate")
    if "ctrl-encryption" in all_ctrl_types and "ctrl-audit-log" in all_ctrl_types:
        frameworks.append("CMMC-Level-2")
    return {
        "frameworks": frameworks,
        "controls": nist_controls,
        "classificationMarkings": {
            "standard": "CUI // SP-CTI",
            "authority": "NIST SP 800-171",
        },
    }


# ── Public API ────────────────────────────────────────────────────────────────

def export_odps(
    name: str,
    graph_json,
    design_id: str | None = None,
    classification: str = "CUI",
) -> bytes:
    """Export a DDC data design graph as an ODPS v3 YAML document.

    Parameters
    ----------
    name : str
        Human-readable design name (used as the data product name).
    graph_json : dict | str
        DDC graph with ``nodes``, ``edges``, and ``boundaries`` keys.
    design_id : str, optional
        Design UUID — embedded in the product ``id`` field.
    classification : str, optional
        Default classification marking (e.g. ``"CUI"``, ``"SECRET"``).

    Returns
    -------
    bytes
        UTF-8 encoded YAML document conforming to ODPS v3.
    """
    nodes, edges, boundaries = _parse_graph(graph_json)
    ntypes, labels = _build_lookup(nodes)
    nodes_by_id = {n["id"]: n for n in nodes}

    # Filter to entity nodes only
    entities = [n for n in nodes if ntypes.get(n["id"], "").startswith("ent-")]

    dp_id = f"dp-{_slug(name)}"
    if design_id:
        dp_id = f"dp-{design_id[:8]}"

    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    input_ports = _build_input_ports(edges, ntypes, labels)
    output_ports = _build_output_ports(
        entities, edges, ntypes, labels, nodes_by_id, boundaries, classification
    )
    product_sla = _build_product_sla(entities, edges, ntypes)
    compliance = _build_compliance_section(entities, edges, ntypes)

    # Collect tags from boundary labels
    tags = ["icdev", "ddc", "data-mesh"]
    for bnd in boundaries:
        bnd_label = bnd.get("label", "")
        if bnd_label:
            tags.append(_slug(bnd_label))
    tags = sorted(set(tags))

    # ── Assemble ODPS document ────────────────────────────────────────────────
    doc: dict[str, Any] = {
        "apiVersion": "odps/v3",
        "kind": "DataProduct",
        "metadata": {
            "id": dp_id,
            "name": name,
            "version": "1.0.0",
            "status": "active",
            "created": now_ts,
            "updated": now_ts,
            "classification": classification,
            "classificationMarking": "CUI // SP-CTI",
            "generator": "ICDEV DDC ODPS Exporter v1.0",
            "tags": tags,
        },
        "owner": {
            "team": "ICDEV Data Engineering",
            "contact": "data-engineering@icdev",
            "domain": "data-mesh",
        },
        "description": {
            "summary": (
                f"Data product generated from ICDEV™ Data Design Canvas (DDC) "
                f"design '{name}'. Classification: {classification}."
            ),
            "purpose": (
                "Expose governed, classified datasets through data mesh output "
                "ports with SLA guarantees and NIST 800-53 compliance markings."
            ),
        },
        "sla": product_sla,
        "compliance": compliance,
    }

    if input_ports:
        doc["inputPorts"] = input_ports
    if output_ports:
        doc["outputPorts"] = output_ports

    # Statistics summary
    n_entities = len(entities)
    n_flows = sum(
        1 for e in edges
        if e.get("type", "").startswith("flow-")
        and not e.get("type", "").startswith("flow-column")
    )
    n_controls = len({
        n["id"] for n in nodes if ntypes.get(n["id"], "").startswith("ctrl-")
    })
    doc["statistics"] = {
        "entityCount": n_entities,
        "inputPortCount": len(input_ports),
        "outputPortCount": len(output_ports),
        "flowCount": n_flows,
        "controlCount": n_controls,
        "boundaryCount": len(boundaries),
    }

    # ── Serialise to YAML ─────────────────────────────────────────────────────
    header = (
        "# CUI // SP-CTI\n"
        "# Open Data Product Standard (ODPS) v3 — generated by ICDEV™ DDC\n"
        "# Specification: https://github.com/bitol-io/open-data-product-standard\n"
        "#\n"
    )

    if _HAS_YAML:
        body = _yaml.dump(
            doc,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        )
    else:
        # Fallback: emit compact JSON so the route still works without pyyaml
        body = json.dumps(doc, indent=2, ensure_ascii=False)

    return (header + body).encode("utf-8")
