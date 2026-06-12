#!/usr/bin/env python3
# CUI // SP-CTI
"""DFD Generator — canvas-aware Data Flow Diagram generator.

Produces a Mermaid-formatted DFD from a TFW session topology. Nodes are
classified into DFD element types (process, data-store, external-entity) by
label heuristics. Edges become labelled data flows.

Canvas handling mirrors ppsm_extractor aliases:
  ndc  -> DFD showing network data flows between zones
  sdc  -> DFD showing API / service data flows
  eda  -> DFD showing event/message flows between producers and consumers

Public surface:
  generate_dfd(session_id, canvas_type) -> dict
    Keys: mermaid (str), elements (list), flows (list), canvas_type (str)

Data source (same priority chain as ppsm_extractor):
  1. nc_simulation_sessions.metadata["refined_graph_json"]
  2. topologies.graph_json  (via topology_id)
  3. Empty graph
"""

from __future__ import annotations

import argparse
import json
import re
import sys
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


# ---------------------------------------------------------------------------
# Node classifiers
# ---------------------------------------------------------------------------

_STORE_RE = re.compile(
    r"\bdb\b|\bdatabase\b|\bcache\b|\bstore\b|\brepository\b|\brepo\b"
    r"|\bstorage\b|\brds\b|\bmongo\b|\bpostgres\b|\bmysql\b|\bredis\b"
    r"|\belastic\b|\bkafka\b|\bs3\b|\bblob\b|\bqueue\b",
    re.I,
)
_EXTERNAL_RE = re.compile(
    r"\buser\b|\bclient\b|\bbrowser\b|\bactor\b|\badmin\b|\boperator\b"
    r"|\bexternal\b|\bpartner\b|\bthird.?party\b|\biot\b|\bsensor\b",
    re.I,
)


def _classify_node(label: str) -> str:
    """Return 'process', 'data-store', or 'external-entity'."""
    if _STORE_RE.search(label):
        return "data-store"
    if _EXTERNAL_RE.search(label):
        return "external-entity"
    return "process"


# ---------------------------------------------------------------------------
# Mermaid DFD shapes
# ---------------------------------------------------------------------------

_NODE_SHAPES = {
    "process": ("((", "))"),
    "data-store": ("[(", ")]"),
    "external-entity": ("[", "]"),
}


def _safe_id(raw: str) -> str:
    """Sanitise a node id for Mermaid (remove spaces/special chars)."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", raw)


def _short_label(label: str, max_len: int = 24) -> str:
    return label[:max_len] + ("…" if len(label) > max_len else "")


def _build_mermaid(
    nodes: list[dict],
    edges: list[dict],
    resolved: str,
) -> tuple[str, list[dict], list[dict]]:
    """Return (mermaid_src, elements_list, flows_list)."""
    elements: list[dict] = []
    node_map: dict[str, dict] = {}

    lines: list[str] = ["flowchart LR"]

    for node in nodes:
        raw_id = node.get("id", node.get("label", "node"))
        label = node.get("label", raw_id)
        ntype = _classify_node(label)
        safe = _safe_id(raw_id)
        open_b, close_b = _NODE_SHAPES[ntype]
        short = _short_label(label)
        lines.append(f'    {safe}{open_b}"{short}"{close_b}')
        elements.append({"id": raw_id, "label": label, "type": ntype})
        node_map[raw_id] = {"safe_id": safe, "label": label, "type": ntype}

    flows: list[dict] = []
    for edge in edges:
        src_id = edge.get("source", "")
        tgt_id = edge.get("target", "")
        edge_label = edge.get("label", "")
        src = node_map.get(src_id)
        tgt = node_map.get(tgt_id)
        if not src or not tgt:
            continue
        flow_label = _short_label(edge_label, 20) if edge_label else "→"
        lines.append(f'    {src["safe_id"]} -- "{flow_label}" --> {tgt["safe_id"]}')
        flows.append(
            {
                "from": src_id,
                "to": tgt_id,
                "label": edge_label,
                "from_type": src["type"],
                "to_type": tgt["type"],
            }
        )

    # If no edges, link nodes in declaration order as a chain
    if not flows and len(elements) > 1:
        for i in range(len(elements) - 1):
            a = node_map[elements[i]["id"]]["safe_id"]
            b = node_map[elements[i + 1]["id"]]["safe_id"]
            lines.append(f"    {a} --> {b}")
            flows.append(
                {
                    "from": elements[i]["id"],
                    "to": elements[i + 1]["id"],
                    "label": "",
                    "from_type": elements[i]["type"],
                    "to_type": elements[i + 1]["type"],
                }
            )

    return "\n".join(lines), elements, flows


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
# Public API
# ---------------------------------------------------------------------------


def generate_dfd(session_id: str, canvas_type: str) -> dict:
    """Generate a Data Flow Diagram for a TFW session.

    Args:
        session_id:  nc_simulation_sessions.id
        canvas_type: ndc | sdc | eda (or alias)

    Returns:
        dict with keys:
          mermaid      (str)  — Mermaid flowchart source
          elements     (list) — classified nodes
          flows        (list) — data flows / edges
          canvas_type  (str)
          canvas_display (str)
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
    mermaid_src, elements, flows = _build_mermaid(nodes, edges, resolved)
    canvas_display = get_display_name(canvas_type)

    return {
        "mermaid": mermaid_src,
        "elements": elements,
        "flows": flows,
        "canvas_type": canvas_type,
        "canvas_display": canvas_display,
        "session_id": session_id,
        "total_elements": len(elements),
        "total_flows": len(flows),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate a canvas-aware Data Flow Diagram from a TFW session.",
    )
    p.add_argument("--session-id", required=True)
    p.add_argument("--canvas-type", required=True)
    p.add_argument("--json", action="store_true")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    result = generate_dfd(args.session_id, args.canvas_type)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"[{result['canvas_display']}] DFD — {result['total_elements']} element(s), "
              f"{result['total_flows']} flow(s)")
        print(result["mermaid"])


if __name__ == "__main__":
    main()
