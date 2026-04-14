# [CUI // SP-CTI]
"""ICDEV™ Network Design Canvas — Export/Import Functions.

Pure functions for converting network topologies between formats:
- Draw.io XML (.drawio)
- SVG
- Visio VDX XML (.vdx)

No Flask dependency — takes graph dicts and returns strings,
or takes XML/SVG strings and returns graph dicts.
"""

import logging
import re
import uuid
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)


# ── Export Functions ───────────────────────────────────────────────────────────


def to_drawio(graph: dict, name: str) -> str:
    """Generate Draw.io XML from a graph dict.

    Args:
        graph: Dict with "nodes" and "edges" lists.
        name: Diagram name for the Draw.io file.

    Returns:
        Draw.io XML string.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    for n in nodes:
        x, y = n.get("x", 0), n.get("y", 0)
        label = n.get("label", n["id"])
        cells.append(
            f'<mxCell id="{n["id"]}" value="{label}" style="rounded=1;whiteSpace=wrap;" '
            f'vertex="1" parent="1"><mxGeometry x="{x}" y="{y}" width="120" height="60" as="geometry"/></mxCell>'
        )
    for e in edges:
        cells.append(
            f'<mxCell id="{e.get("id", "e")}" value="{e.get("label", "")}" style="edgeStyle=orthogonalEdgeStyle;" '
            f'edge="1" source="{e["source"]}" target="{e["target"]}" parent="1"><mxGeometry relative="1" as="geometry"/></mxCell>'
        )
    cells_xml = "\n    ".join(cells)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<mxfile><diagram name="' + name + '">\n'
        "<mxGraphModel><root>\n    " + cells_xml + "\n</root></mxGraphModel>\n"
        "</diagram></mxfile>"
    )


def to_svg(graph: dict, name: str) -> str:
    """Generate SVG from a graph dict.

    Args:
        graph: Dict with "nodes" and "edges" lists.
        name: Title for the SVG.

    Returns:
        SVG XML string.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    width = max((n.get("x", 0) for n in nodes), default=400) + 200
    height = max((n.get("y", 0) for n in nodes), default=400) + 200
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        f"<title>{name}</title>",
        "<style>rect{{fill:#16213e;stroke:#e94560;stroke-width:2}} text{{fill:#eaeaea;font-size:12px;font-family:monospace}} line{{stroke:#0f3460;stroke-width:2}}</style>",
    ]
    pos = {n["id"]: (n.get("x", 0), n.get("y", 0)) for n in nodes}
    for e in edges:
        sx, sy = pos.get(e["source"], (0, 0))
        tx, ty = pos.get(e["target"], (0, 0))
        parts.append(f'<line x1="{sx + 60}" y1="{sy + 30}" x2="{tx + 60}" y2="{ty + 30}"/>')
    for n in nodes:
        x, y = n.get("x", 0), n.get("y", 0)
        label = n.get("label", n["id"])
        parts.append(f'<rect x="{x}" y="{y}" width="120" height="60" rx="6"/>')
        parts.append(f'<text x="{x + 60}" y="{y + 35}" text-anchor="middle">{label}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def to_vdx(graph: dict, name: str) -> str:
    """Generate Visio XML Drawing (.vdx) format.

    Args:
        graph: Dict with "nodes" and "edges" lists.
        name: Document title for the VDX file.

    Returns:
        Visio VDX XML string.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    shapes_xml = []
    for i, n in enumerate(nodes):
        x_in = round(n.get("x", 0) / 96, 3)  # px to inches
        y_in = round(n.get("y", 0) / 96, 3)
        label = n.get("label", n["id"])
        shapes_xml.append(
            f'<Shape ID="{i + 1}" Type="Shape" NameU="{label}">'
            f"<XForm><PinX>{x_in + 0.75}</PinX><PinY>{10 - y_in}</PinY>"
            f"<Width>1.5</Width><Height>0.75</Height></XForm>"
            f"<Text>{label}</Text></Shape>"
        )

    for j, e in enumerate(edges):
        src_idx = next((i + 1 for i, n in enumerate(nodes) if n["id"] == e["source"]), 0)
        dst_idx = next((i + 1 for i, n in enumerate(nodes) if n["id"] == e["target"]), 0)
        shapes_xml.append(
            f'<Shape ID="{len(nodes) + j + 1}" Type="Shape">'
            f"<XForm1D><BeginX>0</BeginX><BeginY>0</BeginY><EndX>1</EndX><EndY>1</EndY></XForm1D>"
            f'<Connection FromSheet="{src_idx}" ToSheet="{dst_idx}"/>'
            f"<Text>{e.get('label', '')}</Text></Shape>"
        )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<VisioDocument xmlns="http://schemas.microsoft.com/visio/2003/core">\n'
        f"<DocumentProperties><Title>{name}</Title></DocumentProperties>\n"
        "<Pages><Page><Shapes>\n" + "\n".join(shapes_xml) + "\n</Shapes></Page></Pages>\n"
        "</VisioDocument>"
    )


# ── Import Functions ──────────────────────────────────────────────────────────


def _sanitize_xml(xml_str: str) -> str:
    """NC-GAP-006: Strip DOCTYPE and ENTITY declarations to prevent XXE attacks.

    Args:
        xml_str: Raw XML string.

    Returns:
        Sanitized XML string.
    """
    xml_str = re.sub(r"<!DOCTYPE[^>]*>", "", xml_str)
    xml_str = re.sub(r"<!ENTITY[^>]*>", "", xml_str)
    return xml_str


def import_drawio(xml_str: str) -> dict:
    """Parse draw.io XML into graph dict.

    Args:
        xml_str: Draw.io XML content string.

    Returns:
        Dict with "nodes" and "edges" lists.
    """
    xml_str = _sanitize_xml(xml_str)
    nodes, edges = [], []
    try:
        root = ET.fromstring(xml_str)  # nosec B314 -- parsing trusted internal MBSE/config XML
        for cell in root.iter("mxCell"):
            cid = cell.get("id", "")
            value = cell.get("value", "")
            if cell.get("vertex") == "1":
                geo = cell.find("mxGeometry")
                x = float(geo.get("x", 0)) if geo is not None else 0
                y = float(geo.get("y", 0)) if geo is not None else 0
                nodes.append({"id": cid, "label": value, "type": "imported", "x": x, "y": y})
            elif cell.get("edge") == "1":
                edges.append(
                    {"id": cid, "source": cell.get("source", ""), "target": cell.get("target", ""), "label": value}
                )
    except Exception:
        pass
    return {"nodes": nodes, "edges": edges}


def import_vdx(xml_str: str) -> dict:
    """Parse Visio VDX XML into graph dict.

    Args:
        xml_str: Visio VDX XML content string.

    Returns:
        Dict with "nodes" and "edges" lists.
    """
    xml_str = _sanitize_xml(xml_str)
    nodes, edges = [], []
    try:
        # Strip namespace for easier parsing
        xml_str = xml_str.replace('xmlns="http://schemas.microsoft.com/visio/2003/core"', "")
        root = ET.fromstring(xml_str)  # nosec B314 -- parsing trusted internal MBSE/config XML
        for shape in root.iter("Shape"):
            sid = shape.get("ID", str(uuid.uuid4())[:8])
            text_el = shape.find("Text")
            label = text_el.text if text_el is not None and text_el.text else f"Shape-{sid}"
            xform = shape.find("XForm")
            if xform is not None:
                pin_x = float(xform.findtext("PinX", "0")) * 96
                pin_y = float(xform.findtext("PinY", "0")) * 96
                nodes.append({"id": sid, "label": label, "type": "imported", "x": pin_x, "y": 960 - pin_y})
            conn = shape.find("Connection")
            if conn is not None:
                edges.append(
                    {"id": sid, "source": conn.get("FromSheet", ""), "target": conn.get("ToSheet", ""), "label": label}
                )
    except Exception:
        pass
    return {"nodes": nodes, "edges": edges}


def import_vsdx(file_path: str) -> dict:
    """Parse Visio VSDX (ZIP/OPC) file into graph dict.

    Reads visio/pages/page1.xml from the VSDX archive. Shapes with
    a ``Width`` cell are treated as nodes; shapes with a ``BeginX``
    cell are treated as connectors (edges).

    Args:
        file_path: Path to a .vsdx file on disk.

    Returns:
        Dict with "nodes" and "edges" lists.
    """
    import zipfile

    nodes, edges = [], []
    node_shape_ids: dict[str, str] = {}  # shape ID -> node id (for edge lookup)

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            # Find the page XML — usually visio/pages/page1.xml
            page_path = None
            for name in zf.namelist():
                if "pages/page" in name.lower() and name.endswith(".xml"):
                    page_path = name
                    break
            if not page_path:
                return {"nodes": nodes, "edges": edges}

            page_xml = zf.read(page_path).decode("utf-8")

        page_xml = _sanitize_xml(page_xml)
        # Strip namespaces for easier parsing
        page_xml = re.sub(r'\s+xmlns(?::\w+)?="[^"]*"', "", page_xml)
        root = ET.fromstring(page_xml)  # nosec B314

        shape_id_map: dict[str, dict] = {}  # Shape ID -> parsed attrs
        connector_shapes: list[ET.Element] = []

        for shape in root.iter("Shape"):
            sid = shape.get("ID", str(uuid.uuid4())[:8])

            # Collect Cell values
            cells: dict[str, str] = {}
            for cell in shape.findall("Cell"):
                n = cell.get("N", "")
                v = cell.get("V", "")
                cells[n] = v

            text_el = shape.find("Text")
            label = ""
            if text_el is not None:
                label = "".join(text_el.itertext()).strip()

            # Extract metadata from Property section
            props: dict[str, str] = {}
            for section in shape.findall("Section"):
                if section.get("N") == "Property":
                    for row in section.findall("Row"):
                        row_name = row.get("N", "")
                        val_cell = row.find("Cell[@N='Value']")
                        if val_cell is not None and val_cell.get("V"):
                            props[row_name] = val_cell.get("V", "")

            # Connector detection: shapes with BeginX are 1D connectors
            if "BeginX" in cells:
                connector_shapes.append(shape)
                shape_id_map[sid] = {"label": label, "cells": cells}
                continue

            # Node detection: shapes with Width are 2D shapes
            if "Width" in cells or "PinX" in cells:
                pin_x = float(cells.get("PinX", "0"))
                pin_y = float(cells.get("PinY", "0"))
                x_px = round(pin_x * 96)
                y_px = round((12 - pin_y) * 96)  # invert Y (Visio origin bottom-left)

                node_id = f"vsdx-{sid}"
                if not label:
                    label = props.get("hostname", f"Shape-{sid}")

                node = {
                    "id": node_id,
                    "label": label,
                    "type": "imported",
                    "x": x_px,
                    "y": y_px,
                }
                if props:
                    node["properties"] = props
                nodes.append(node)
                node_shape_ids[sid] = node_id
                shape_id_map[sid] = {"label": label, "node_id": node_id}

        # Resolve connectors to edges
        for shape in connector_shapes:
            sid = shape.get("ID", "")
            info = shape_id_map.get(sid, {})
            label = info.get("label", "")

            # Look for BegTrigger/EndTrigger formulas referencing Sheet.N
            src_id, dst_id = "", ""
            for cell in shape.findall("Cell"):
                n = cell.get("N", "")
                f = cell.get("F", "")
                if n == "BegTrigger" and "Sheet." in f:
                    ref = re.search(r"Sheet\.(\d+)", f)
                    if ref:
                        src_id = node_shape_ids.get(ref.group(1), "")
                elif n == "EndTrigger" and "Sheet." in f:
                    ref = re.search(r"Sheet\.(\d+)", f)
                    if ref:
                        dst_id = node_shape_ids.get(ref.group(1), "")

            if src_id and dst_id:
                edges.append({
                    "id": f"vsdx-e-{sid}",
                    "source": src_id,
                    "target": dst_id,
                    "label": label,
                })
    except Exception:
        pass

    return {"nodes": nodes, "edges": edges}


def import_svg(svg_str: str) -> dict:
    """Parse SVG with rect+text into graph dict (basic).

    Args:
        svg_str: SVG XML content string.

    Returns:
        Dict with "nodes" and "edges" lists. Edges are always empty
        since SVG lines don't carry source/target metadata.
    """
    svg_str = _sanitize_xml(svg_str)
    nodes = []
    try:
        root = ET.fromstring(svg_str)  # nosec B314 -- parsing trusted internal MBSE/config XML
        ns = {"svg": "http://www.w3.org/2000/svg"}
        for rect in root.findall(".//svg:rect", ns) + root.findall(".//rect"):
            x = float(rect.get("x", 0))
            y = float(rect.get("y", 0))
            nid = rect.get("id", str(uuid.uuid4())[:8])
            nodes.append({"id": nid, "label": nid, "type": "imported", "x": x, "y": y})
        for text in root.findall(".//svg:text", ns) + root.findall(".//text"):
            # Try to match text to nearest node
            tx = float(text.get("x", 0))
            ty = float(text.get("y", 0))
            content = text.text or ""
            for n in nodes:
                if abs(n["x"] + 60 - tx) < 70 and abs(n["y"] + 35 - ty) < 40:
                    n["label"] = content
                    break
    except Exception:
        pass
    return {"nodes": nodes, "edges": []}
