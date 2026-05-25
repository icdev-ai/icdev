# CUI // SP-CTI
"""Unified canvas export utilities.

Provides five export formats for all design canvases:
JSON, Markdown, CSV, DrawIO XML, and SVG.

No external dependencies — stdlib only.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import csv
import io
import json
import logging
import xml.etree.ElementTree as ET

logger = get_logger("icdev.canvas.export_utils")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CUI_BANNER = "CUI // SP-CTI"

# Node-type colour palette for SVG rendering
_TYPE_COLOURS = {
    "server": "#4A90D9",
    "database": "#7B68EE",
    "network": "#50C878",
    "firewall": "#E74C3C",
    "loadbalancer": "#F39C12",
    "container": "#1ABC9C",
    "storage": "#9B59B6",
    "service": "#3498DB",
    "user": "#2ECC71",
    "boundary": "#E67E22",
    "zone": "#16A085",
    "default": "#6C757D",
}


def _parse_graph(graph_json):
    """Return (nodes_list, edges_list) from *graph_json*.

    *graph_json* may be a ``dict`` that already contains ``nodes``/``edges``
    keys **or** a JSON string that deserialises to such a dict.  Missing or
    unparseable data yields empty lists rather than raising.
    """
    if graph_json is None:
        return [], []

    if isinstance(graph_json, str):
        try:
            graph_json = json.loads(graph_json)
        except (json.JSONDecodeError, TypeError):
            logger.warning("graph_json is not valid JSON; returning empty graph")
            return [], []

    if not isinstance(graph_json, dict):
        return [], []

    nodes = graph_json.get("nodes") or []
    edges = graph_json.get("edges") or []
    return list(nodes), list(edges)


def _node_attr(node, key, fallback=""):
    """Safely pull an attribute from a node dict."""
    if isinstance(node, dict):
        return node.get(key, fallback)
    return fallback


# ---------------------------------------------------------------------------
# Public API — each function returns a *str*
# ---------------------------------------------------------------------------


def export_json(name: str, graph_json, canvas_key: str) -> str:
    """Pretty-printed JSON of the full design.

    Parameters
    ----------
    name : str
        Human-readable design name.
    graph_json : dict | str
        Graph payload (nodes + edges).
    canvas_key : str
        Canvas identifier (e.g. ``"IDC"``, ``"BDC"``).
    """
    nodes, edges = _parse_graph(graph_json)
    doc = {
        "classification": _CUI_BANNER,
        "canvas_key": canvas_key,
        "name": name,
        "graph": {
            "nodes": nodes,
            "edges": edges,
        },
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(edges),
        },
    }
    return json.dumps(doc, indent=2, default=str)


def export_markdown(name: str, graph_json, canvas_key: str) -> str:
    """Markdown document with node/edge tables and stats."""
    nodes, edges = _parse_graph(graph_json)

    lines = [
        f"<!-- {_CUI_BANNER} -->",
        f"# {canvas_key.upper()} Design: {name}",
        "",
        f"> Classification: **{_CUI_BANNER}**",
        "",
        "## Nodes",
        "",
        "| ID | Type | Label |",
        "|---|---|---|",
    ]
    for n in nodes:
        nid = _node_attr(n, "id", "—")
        ntype = _node_attr(n, "type", "—")
        label = _node_attr(n, "label") or _node_attr(n, "name", "—")
        lines.append(f"| {nid} | {ntype} | {label} |")

    lines += [
        "",
        "## Edges",
        "",
        "| Source | Target | Type |",
        "|---|---|---|",
    ]
    for e in edges:
        src = _node_attr(e, "source", "—")
        tgt = _node_attr(e, "target", "—")
        etype = _node_attr(e, "type") or _node_attr(e, "label", "—")
        lines.append(f"| {src} | {tgt} | {etype} |")

    lines += [
        "",
        "## Stats",
        "",
        f"- **Nodes:** {len(nodes)}",
        f"- **Edges:** {len(edges)}",
        f"- **Canvas:** {canvas_key.upper()}",
        "",
        f"---\n*{_CUI_BANNER}*",
    ]
    return "\n".join(lines)


def export_csv(name: str, graph_json, canvas_key: str) -> str:
    """CSV with two sections: nodes then edges."""
    nodes, edges = _parse_graph(graph_json)
    buf = io.StringIO()
    writer = csv.writer(buf)

    # Header comment
    writer.writerow([f"# {_CUI_BANNER} — {canvas_key.upper()} Design: {name}"])
    writer.writerow([])

    # Nodes section
    writer.writerow(["## Nodes"])
    writer.writerow(["ID", "Type", "Label"])
    for n in nodes:
        nid = _node_attr(n, "id", "")
        ntype = _node_attr(n, "type", "")
        label = _node_attr(n, "label") or _node_attr(n, "name", "")
        writer.writerow([nid, ntype, label])

    writer.writerow([])

    # Edges section
    writer.writerow(["## Edges"])
    writer.writerow(["Source", "Target", "Type"])
    for e in edges:
        src = _node_attr(e, "source", "")
        tgt = _node_attr(e, "target", "")
        etype = _node_attr(e, "type") or _node_attr(e, "label", "")
        writer.writerow([src, tgt, etype])

    return buf.getvalue()


def export_drawio(name: str, graph_json, canvas_key: str) -> str:
    """DrawIO XML (mxGraphModel) with auto grid layout."""
    nodes, edges = _parse_graph(graph_json)

    # Grid layout parameters
    col_width = 200
    row_height = 120
    cols = max(4, 1)
    pad_x, pad_y = 40, 40
    node_w, node_h = 140, 60

    root = ET.Element("mxGraphModel")
    root.set("dx", "1326")
    root.set("dy", "798")
    root.set("grid", "1")
    root.set("gridSize", "10")
    root.set("guides", "1")

    diagram_root = ET.SubElement(root, "root")

    # Required parent cells for DrawIO
    cell0 = ET.SubElement(diagram_root, "mxCell")
    cell0.set("id", "0")
    cell1 = ET.SubElement(diagram_root, "mxCell")
    cell1.set("id", "1")
    cell1.set("parent", "0")

    # Map node ids to internal cell ids for edge wiring
    node_id_map = {}
    for idx, n in enumerate(nodes):
        nid = str(_node_attr(n, "id", f"n{idx}"))
        label = _node_attr(n, "label") or _node_attr(n, "name", nid)

        cell_id = f"node_{idx + 2}"
        node_id_map[nid] = cell_id

        col = idx % cols
        row = idx // cols
        x = pad_x + col * col_width
        y = pad_y + row * row_height

        cell = ET.SubElement(diagram_root, "mxCell")
        cell.set("id", cell_id)
        cell.set("value", label)
        cell.set("style", "rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;arcSize=20;fontSize=12;fontFamily=Helvetica;verticalAlign=middle;")
        cell.set("vertex", "1")
        cell.set("parent", "1")

        geo = ET.SubElement(cell, "mxGeometry")
        geo.set("x", str(x))
        geo.set("y", str(y))
        geo.set("width", str(node_w))
        geo.set("height", str(node_h))
        geo.set("as", "geometry")

    # Edges
    for idx, e in enumerate(edges):
        src = str(_node_attr(e, "source", ""))
        tgt = str(_node_attr(e, "target", ""))
        etype = _node_attr(e, "type") or _node_attr(e, "label", "")

        src_cell = node_id_map.get(src)
        tgt_cell = node_id_map.get(tgt)
        if not src_cell or not tgt_cell:
            logger.debug("Skipping edge %s->%s: node not found", src, tgt)
            continue

        edge_id = f"edge_{idx + len(nodes) + 2}"
        cell = ET.SubElement(diagram_root, "mxCell")
        cell.set("id", edge_id)
        cell.set("value", etype)
        cell.set("style", "endArrow=classic;html=1;strokeColor=#6c8ebf;fontSize=10;")
        cell.set("edge", "1")
        cell.set("parent", "1")
        cell.set("source", src_cell)
        cell.set("target", tgt_cell)

        geo = ET.SubElement(cell, "mxGeometry")
        geo.set("relative", "1")
        geo.set("as", "geometry")

    # Add classification as a text label at top
    comment_id = f"comment_{len(nodes) + len(edges) + 2}"
    comment = ET.SubElement(diagram_root, "mxCell")
    comment.set("id", comment_id)
    comment.set("value", f"{_CUI_BANNER} — {canvas_key.upper()} Design: {name}")
    comment.set("style", "text;html=1;fontSize=11;fontStyle=1;fillColor=none;strokeColor=none;fontColor=#999999;")
    comment.set("vertex", "1")
    comment.set("parent", "1")
    comment_geo = ET.SubElement(comment, "mxGeometry")
    comment_geo.set("x", "10")
    comment_geo.set("y", "5")
    comment_geo.set("width", "400")
    comment_geo.set("height", "20")
    comment_geo.set("as", "geometry")

    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def export_svg(name: str, graph_json, canvas_key: str) -> str:
    """Simple SVG with rectangles for nodes and lines for edges."""
    nodes, edges = _parse_graph(graph_json)

    # Layout parameters
    col_width = 220
    row_height = 100
    cols = max(4, 1)
    pad_x, pad_y = 60, 60
    node_w, node_h = 160, 50

    # Calculate viewBox dimensions
    max_col = min(len(nodes), cols) if nodes else 1
    max_row = (len(nodes) // cols) + 1 if nodes else 1
    vb_w = pad_x * 2 + max_col * col_width
    vb_h = pad_y * 2 + max_row * row_height + 30  # +30 for banner

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vb_w} {vb_h}" width="{vb_w}" height="{vb_h}">',
        f'  <title>{canvas_key.upper()} Design: {name}</title>',
        f'  <!-- {_CUI_BANNER} -->',
        '  <style>',
        '    .node-rect { stroke-width: 2; rx: 8; ry: 8; }',
        '    .node-label { font-family: Helvetica, Arial, sans-serif; font-size: 12px; fill: #fff; text-anchor: middle; dominant-baseline: central; }',
        '    .edge-line { stroke-width: 1.5; stroke: #999; marker-end: url(#arrowhead); }',
        '    .banner { font-family: Helvetica, Arial, sans-serif; font-size: 10px; fill: #999; }',
        '  </style>',
        '  <defs>',
        '    <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">',
        '      <polygon points="0 0, 8 3, 0 6" fill="#999" />',
        '    </marker>',
        '  </defs>',
        f'  <text x="10" y="18" class="banner">{_CUI_BANNER} — {canvas_key.upper()} Design: {name}</text>',
    ]

    # Build node position map
    node_positions = {}
    banner_offset = 30
    for idx, n in enumerate(nodes):
        nid = str(_node_attr(n, "id", f"n{idx}"))
        label = _node_attr(n, "label") or _node_attr(n, "name", nid)
        ntype = str(_node_attr(n, "type", "default")).lower()
        colour = _TYPE_COLOURS.get(ntype, _TYPE_COLOURS["default"])

        col = idx % cols
        row = idx // cols
        x = pad_x + col * col_width
        y = banner_offset + pad_y + row * row_height
        cx = x + node_w // 2
        cy = y + node_h // 2

        node_positions[nid] = (cx, cy)

        lines.append(
            f'  <rect x="{x}" y="{y}" width="{node_w}" height="{node_h}" '
            f'fill="{colour}" class="node-rect" stroke="{colour}" />'
        )
        # Truncate long labels
        disp = label if len(label) <= 22 else label[:20] + "..."
        lines.append(
            f'  <text x="{cx}" y="{cy}" class="node-label">{_xml_escape(disp)}</text>'
        )

    # Edges
    for e in edges:
        src = str(_node_attr(e, "source", ""))
        tgt = str(_node_attr(e, "target", ""))
        if src not in node_positions or tgt not in node_positions:
            continue
        x1, y1 = node_positions[src]
        x2, y2 = node_positions[tgt]
        lines.append(
            f'  <line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" class="edge-line" />'
        )

    lines.append("</svg>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _xml_escape(text: str) -> str:
    """Escape special XML characters in text content."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
