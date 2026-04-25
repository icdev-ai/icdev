#!/usr/bin/env python3
# CUI // SP-CTI
"""ISA Generator — Inter-System Agreement document generator.

Produces a structured ISA from a TFW session topology. An ISA captures the
formal agreement between two or more systems for data exchange including
security controls, data classification, and contact responsibilities.

Canvas handling:
  ndc  -> ISA between network zones / interconnected systems
  sdc  -> ISA between services sharing APIs
  eda  -> ISA between producers and consumers sharing event streams

Public surface:
  generate_isa(session_id, canvas_type) -> dict

Data source:
  1. nc_simulation_sessions.metadata["refined_graph_json"]
  2. topologies.graph_json
  3. Empty graph
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

# ---------------------------------------------------------------------------
# Canvas aliases
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _load_session(conn, session_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, canvas_type, topology_id, mode, metadata "
        "FROM nc_simulation_sessions WHERE id = ?",
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
            "SELECT graph_json FROM topologies WHERE id = ?", (topology_id,)
        ).fetchone()
        if row and row[0]:
            try:
                return json.loads(row[0]) if isinstance(row[0], str) else row[0]
            except json.JSONDecodeError:
                pass
    return {"nodes": [], "edges": []}


# ---------------------------------------------------------------------------
# ISA construction helpers
# ---------------------------------------------------------------------------

_CLASSIFICATION_RE = re.compile(
    r"\b(secret|cui|unclassified|fouo|sensitive|confidential|pii|phi|pci)\b", re.I
)


def _infer_classification(labels: list[str]) -> str:
    for label in labels:
        m = _CLASSIFICATION_RE.search(label)
        if m:
            word = m.group(1).upper()
            if word == "SECRET":
                return "SECRET"
            if word in ("CUI", "FOUO", "SENSITIVE", "CONFIDENTIAL"):
                return "CUI"
            if word in ("PII", "PHI", "PCI"):
                return "CUI/PII"
    return "CUI"


def _build_isa_parties(nodes: list[dict], canvas_type: str) -> list[dict]:
    """Extract party system names from topology nodes."""
    if not nodes:
        return [
            {"system": "System A", "owner": "TBD", "role": "sending"},
            {"system": "System B", "owner": "TBD", "role": "receiving"},
        ]
    parties: list[dict] = []
    seen: set[str] = set()
    roles = ["sending", "receiving", "transit"]
    for i, node in enumerate(nodes[:6]):
        label = node.get("label", f"System {i+1}")
        if label in seen:
            continue
        seen.add(label)
        zone = node.get("zone", "")
        parties.append(
            {
                "system": label,
                "owner": zone or "TBD",
                "role": roles[min(i, len(roles) - 1)],
            }
        )
    return parties


def _build_data_flows(edges: list[dict], node_map: dict[str, dict]) -> list[dict]:
    flows: list[dict] = []
    for edge in edges:
        src_node = node_map.get(edge.get("source", ""), {})
        tgt_node = node_map.get(edge.get("target", ""), {})
        label = edge.get("label", "")
        flows.append(
            {
                "from_system": src_node.get("label", edge.get("source", "?")),
                "to_system": tgt_node.get("label", edge.get("target", "?")),
                "data_description": label or "Data exchange",
                "protocol": _infer_protocol(label),
                "encryption": "TLS 1.3",
                "frequency": "continuous",
            }
        )
    return flows


_PROTO_RE = re.compile(r"\b(TCP|UDP|HTTPS?|gRPC|AMQP|MQTT|Kafka|REST)\b", re.I)


def _infer_protocol(label: str) -> str:
    m = _PROTO_RE.search(label)
    return m.group(1).upper() if m else "HTTPS"


def _build_security_controls(resolved: str) -> list[dict]:
    """Return ISA security control stubs per canvas type."""
    ndc_controls = [
        {"control": "Firewall rule enforcement", "responsibility": "System Owner"},
        {"control": "Encrypted transit (IPSec/TLS)", "responsibility": "Network Admin"},
        {"control": "Port/protocol restriction", "responsibility": "ISSM"},
    ]
    sdc_controls = [
        {"control": "Mutual TLS (mTLS) authentication", "responsibility": "Service Owner"},
        {"control": "OAuth2 / OIDC token validation", "responsibility": "IAM Team"},
        {"control": "API rate limiting", "responsibility": "API Gateway Admin"},
    ]
    eda_controls = [
        {"control": "Topic-level ACL enforcement", "responsibility": "Kafka/MQ Admin"},
        {"control": "Schema validation on publish/consume", "responsibility": "Data Engineer"},
        {"control": "At-rest encryption for retained events", "responsibility": "Storage Owner"},
    ]
    mapping = {"ndc": ndc_controls, "sdc": sdc_controls, "eda": eda_controls}
    return mapping.get(resolved, ndc_controls)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_isa(session_id: str, canvas_type: str) -> dict:
    """Generate an Inter-System Agreement document for a TFW session.

    Args:
        session_id:  nc_simulation_sessions.id
        canvas_type: ndc | sdc | eda (or alias)

    Returns:
        dict with keys:
          isa_id, session_id, canvas_type, canvas_display,
          classification, parties, data_flows, security_controls,
          effective_date, review_date, status, markdown
    """
    resolved = _resolve(canvas_type)
    conn = get_connection()
    try:
        session = _load_session(conn, session_id)
        graph = _load_graph_json(conn, session)
    finally:
        conn.close()

    nodes: list[dict] = graph.get("nodes", [])
    edges: list[dict] = graph.get("edges", [])
    node_map = {n["id"]: n for n in nodes}

    all_labels = [n.get("label", "") for n in nodes] + [e.get("label", "") for e in edges]
    classification = _infer_classification(all_labels)
    parties = _build_isa_parties(nodes, resolved)
    data_flows = _build_data_flows(edges, node_map)
    security_controls = _build_security_controls(resolved)
    canvas_display = get_display_name(canvas_type)
    isa_id = f"ISA-{session_id[:8].upper()}"
    now_dt = datetime.now(timezone.utc)
    effective_date = now_dt.strftime("%Y-%m-%d")
    review_date = now_dt.replace(year=now_dt.year + 1).strftime("%Y-%m-%d")

    # Markdown summary
    party_lines = "\n".join(
        f"  - **{p['system']}** (Owner: {p['owner']}, Role: {p['role']})" for p in parties
    )
    flow_lines = "\n".join(
        f"  - {f['from_system']} → {f['to_system']}: {f['data_description']} "
        f"[{f['protocol']}, {f['encryption']}]"
        for f in data_flows
    ) or "  - No data flows detected in topology."
    ctrl_lines = "\n".join(
        f"  - {c['control']} — *{c['responsibility']}*" for c in security_controls
    )

    markdown = f"""## Inter-System Agreement — {isa_id}

**Classification:** {classification}
**Canvas:** {canvas_display}
**Effective:** {effective_date} | **Review:** {review_date}
**Status:** DRAFT

### Parties
{party_lines}

### Data Flows
{flow_lines}

### Security Controls
{ctrl_lines}

*Generated by ICDEV™ TFW Simulation. Review and sign before operational use.*
"""

    return {
        "isa_id": isa_id,
        "session_id": session_id,
        "canvas_type": canvas_type,
        "canvas_display": canvas_display,
        "classification": classification,
        "parties": parties,
        "data_flows": data_flows,
        "security_controls": security_controls,
        "effective_date": effective_date,
        "review_date": review_date,
        "status": "DRAFT",
        "markdown": markdown,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate an ISA from a TFW session.")
    p.add_argument("--session-id", required=True)
    p.add_argument("--canvas-type", required=True)
    p.add_argument("--json", action="store_true")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    result = generate_isa(args.session_id, args.canvas_type)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["markdown"])


if __name__ == "__main__":
    main()
