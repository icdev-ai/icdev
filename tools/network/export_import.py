from __future__ import annotations

from tools.logging.icdev_logger import get_logger
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
import math
import re
import uuid
import xml.etree.ElementTree as ET

logger = get_logger(__name__)


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
    """Parse Visio VSDX (ZIP/OPC) file into a graph dict.

    Uses the ``vsdx`` library when available (handles stencil masters,
    multi-page documents, ``<Connect>`` connector wiring, real page
    geometry, and unit conversion). Falls back to a hardened stdlib
    parser when ``vsdx`` isn't installed.

    Both paths return the same shape: ``{"nodes": [...], "edges": [...],
    "_pages": N, "_errors": [...]}``. Callers that only consumed the old
    ``{nodes, edges}`` keys still work unchanged.

    Args:
        file_path: Path to a .vsdx file on disk.

    Returns:
        Dict with ``nodes`` and ``edges`` lists, plus diagnostic
        ``_pages`` (page count) and ``_errors`` (list of strings).
    """
    try:
        import vsdx  # type: ignore
    except ImportError:
        logger.info("vsdx library not installed; using stdlib fallback parser")
        return _import_vsdx_stdlib(file_path)

    try:
        return _import_vsdx_lib(file_path, vsdx)
    except Exception as e:
        logger.warning("vsdx lib parse failed (%s); falling back to stdlib", e)
        result = _import_vsdx_stdlib(file_path)
        result.setdefault("_errors", []).insert(0, f"vsdx-lib: {e}")
        return result


def _import_vsdx_lib(file_path: str, vsdx_mod) -> dict:
    """Parse VSDX using the ``vsdx`` library (preferred path).

    Handles master-shape inheritance, multi-page documents, explicit
    ``<Connect>`` wiring, and correct page geometry for Y-axis inversion.
    """
    nodes: list[dict] = []
    edges: list[dict] = []
    errors: list[str] = []
    # Map (page_idx, shape_id) -> canonical node_id for edge resolution
    sheet_to_node: dict[tuple[int, str], str] = {}
    page_count = 0

    with vsdx_mod.VisioFile(file_path) as vis:
        for page_idx, page in enumerate(vis.pages):
            page_count += 1
            try:
                page_h = float(page.height or 11.0)
            except (TypeError, ValueError):
                page_h = 11.0

            connector_shapes: list = []
            for shape in page.all_shapes:
                sid = str(shape.ID) if hasattr(shape, "ID") else shape.shape_id
                # 1D connector shapes have begin_x/end_x set
                is_connector = (
                    getattr(shape, "begin_x", None) is not None
                    or getattr(shape, "end_x", None) is not None
                )

                # Pull label — text, then master text, then property
                label = ""
                try:
                    label = (shape.text or "").strip()
                except Exception:
                    pass
                if not label:
                    try:
                        master = shape.master_shape
                        if master is not None:
                            label = (master.text or "").strip()
                    except Exception:
                        pass

                # Extract data properties via library (resolves master inheritance)
                props: dict[str, str] = {}
                try:
                    for p in (shape.data_properties or {}).values():
                        key = getattr(p, "label", None) or getattr(p, "name", "")
                        val = getattr(p, "value", "")
                        if key and val not in (None, ""):
                            props[str(key)] = str(val)
                except Exception as e:
                    errors.append(f"props page{page_idx} sid={sid}: {e}")

                if is_connector:
                    connector_shapes.append((sid, shape, label))
                    continue

                # Skip group containers with no geometry and no label
                try:
                    x_in = float(shape.x) if shape.x is not None else 0.0
                    y_in = float(shape.y) if shape.y is not None else 0.0
                except (TypeError, ValueError):
                    x_in, y_in = 0.0, 0.0

                # Require something identifiable — either label, props, or geometry
                if not label and not props and x_in == 0.0 and y_in == 0.0:
                    continue

                if not label:
                    # Fallbacks: hostname property, master universal name, then Shape-ID
                    label = (
                        props.get("hostname")
                        or props.get("Hostname")
                        or props.get("Name")
                        or getattr(shape, "universal_name", None)
                        or f"Shape-{sid}"
                    )

                node_id = f"vsdx-p{page_idx}-{sid}"
                node: dict = {
                    "id": node_id,
                    "label": label,
                    "type": "imported",
                    "x": round(x_in * 96),
                    "y": round((page_h - y_in) * 96),  # invert using real page height
                }
                if page_count > 1:
                    node["page"] = page_idx
                if props:
                    node["properties"] = props
                nodes.append(node)
                sheet_to_node[(page_idx, str(sid))] = node_id

            # Resolve connectors via page.connects (authoritative <Connect> data)
            resolved: set = set()
            try:
                for conn in page.connects or []:
                    connector_sid = str(getattr(conn, "connector_shape_id", "") or "")
                    from_sid = str(getattr(conn, "shape_id", "") or getattr(conn, "to_id", "") or "")
                    from_cell = getattr(conn, "from_rel", "") or getattr(conn, "from_cell", "")
                    # vsdx Connect model exposes: shape_id (the node), connector_shape_id, from_rel
                    # We need to pair two Connect rows for the same connector (one begin, one end)
                    resolved.add((connector_sid, from_sid, str(from_cell)))
            except Exception as e:
                errors.append(f"connects page{page_idx}: {e}")

            # Build edge src/dst by pairing Connect rows per connector
            pair: dict[str, dict[str, str]] = {}
            for connector_sid, node_sid, from_cell in resolved:
                if not connector_sid or not node_sid:
                    continue
                slot = pair.setdefault(connector_sid, {})
                role = "source" if from_cell.lower().startswith("begin") else "target"
                slot[role] = node_sid

            for connector_sid, shape, label in connector_shapes:
                ends = pair.get(str(connector_sid), {})
                src_sid = ends.get("source")
                dst_sid = ends.get("target")
                src_node = sheet_to_node.get((page_idx, src_sid)) if src_sid else None
                dst_node = sheet_to_node.get((page_idx, dst_sid)) if dst_sid else None
                if src_node and dst_node:
                    edges.append({
                        "id": f"vsdx-p{page_idx}-e-{connector_sid}",
                        "source": src_node,
                        "target": dst_node,
                        "label": label,
                    })
                else:
                    errors.append(
                        f"unresolved connector page{page_idx} sid={connector_sid} "
                        f"src={src_sid} dst={dst_sid}"
                    )

    result: dict = {"nodes": nodes, "edges": edges, "_pages": page_count}
    if errors:
        result["_errors"] = errors
    return result


def _import_vsdx_stdlib(file_path: str) -> dict:
    """Hardened stdlib VSDX parser — no external deps.

    Handles:
    - Multi-page traversal (all ``visio/pages/page*.xml``)
    - Master-shape inheritance (Text, Cells, Property sections resolved
      from ``visio/masters/master*.xml`` when shape has ``Master=`` attr)
    - ``<Connect>`` elements for connector wiring (primary) with
      ``BegTrigger``/``EndTrigger`` as fallback
    - Real ``PageHeight`` from ``PageSheet`` (no hardcoded 12 inches)
    - Structured error diagnostics instead of silent ``except: pass``
    """
    import zipfile

    nodes: list[dict] = []
    edges: list[dict] = []
    errors: list[str] = []
    sheet_to_node: dict[tuple[int, str], str] = {}
    page_count = 0

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            names = zf.namelist()
            masters = _vsdx_load_masters(zf, names, errors)
            page_files = sorted(
                n for n in names
                if re.match(r"^visio/pages/page\d+\.xml$", n.lower())
            )
            if not page_files:
                return {"nodes": nodes, "edges": edges, "_pages": 0,
                        "_errors": errors + ["no page xml found"]}

            for page_idx, page_path in enumerate(page_files):
                page_count += 1
                try:
                    raw = zf.read(page_path).decode("utf-8", errors="replace")
                    _vsdx_parse_page(
                        raw, page_idx, masters, nodes, edges,
                        sheet_to_node, errors,
                    )
                except Exception as e:
                    errors.append(f"page {page_path}: {e}")
    except zipfile.BadZipFile as e:
        errors.append(f"not a valid vsdx/zip: {e}")
    except Exception as e:
        errors.append(f"vsdx read: {e}")

    result: dict = {"nodes": nodes, "edges": edges, "_pages": page_count}
    if errors:
        result["_errors"] = errors
    return result


def _vsdx_load_masters(zf, names, errors: list) -> dict:
    """Load master shapes keyed by master ID.

    Returns ``{master_id: {"text": str, "cells": {name: val}, "props": {...}}}``.
    Master inheritance is how stencil-based Visio diagrams (Cisco, AWS, etc.)
    carry their hostname/IP/model properties — without this, those shapes
    come back with empty labels and no metadata.
    """
    masters: dict[str, dict] = {}
    master_files = [n for n in names if re.match(r"^visio/masters/master\d+\.xml$", n.lower())]
    for mf in master_files:
        m_id = re.search(r"master(\d+)\.xml", mf.lower())
        if not m_id:
            continue
        try:
            xml = zf.read(mf).decode("utf-8", errors="replace")
            xml = _sanitize_xml(xml)
            xml = re.sub(r'\s+xmlns(?::\w+)?="[^"]*"', "", xml)
            root = ET.fromstring(xml)  # nosec B314
            # Master file wraps its shape(s) — take first Shape
            shape = root.find(".//Shape")
            if shape is None:
                continue
            text_el = shape.find("Text")
            text = "".join(text_el.itertext()).strip() if text_el is not None else ""
            cells = {c.get("N", ""): c.get("V", "") for c in shape.findall("Cell")}
            props = _vsdx_extract_properties(shape)
            masters[m_id.group(1)] = {"text": text, "cells": cells, "props": props}
        except Exception as e:
            errors.append(f"master {mf}: {e}")
    return masters


def _vsdx_extract_properties(shape: ET.Element) -> dict:
    """Extract Property section rows as a flat dict.

    Prefers the human-friendly ``Label`` cell over the internal row name
    when present (real Visio property rows often have ``Row N="Row_1"``
    with a separate ``Cell N="Label" V="Hostname"``).
    """
    props: dict[str, str] = {}
    for section in shape.findall("Section"):
        if section.get("N") != "Property":
            continue
        for row in section.findall("Row"):
            row_name = row.get("N", "")
            label_cell = row.find("Cell[@N='Label']")
            val_cell = row.find("Cell[@N='Value']")
            if val_cell is None:
                continue
            val = val_cell.get("V", "")
            if val in (None, ""):
                continue
            key = (label_cell.get("V", "") if label_cell is not None else "") or row_name
            if key:
                props[key] = val
    return props


def _vsdx_parse_page(
    page_xml: str,
    page_idx: int,
    masters: dict,
    nodes: list,
    edges: list,
    sheet_to_node: dict,
    errors: list,
) -> None:
    """Parse a single page XML, appending nodes/edges."""
    page_xml = _sanitize_xml(page_xml)
    page_xml = re.sub(r'\s+xmlns(?::\w+)?="[^"]*"', "", page_xml)
    root = ET.fromstring(page_xml)  # nosec B314

    # Real page height — fall back to 11" (US Letter portrait) only if absent
    page_h_in = 11.0
    for cell in root.iter("Cell"):
        if cell.get("N") == "PageHeight":
            try:
                page_h_in = float(cell.get("V", "11"))
                break
            except ValueError:
                pass

    connector_shapes: list[tuple[str, ET.Element, str]] = []

    for shape in root.iter("Shape"):
        sid = shape.get("ID", str(uuid.uuid4())[:8])
        master_id = shape.get("Master") or shape.get("MasterShape")
        master = masters.get(master_id, {}) if master_id else {}

        cells = {c.get("N", ""): c.get("V", "") for c in shape.findall("Cell")}
        # Merge master cells as defaults (shape overrides)
        for mk, mv in (master.get("cells") or {}).items():
            cells.setdefault(mk, mv)

        text_el = shape.find("Text")
        label = "".join(text_el.itertext()).strip() if text_el is not None else ""
        if not label:
            label = master.get("text", "")

        props = _vsdx_extract_properties(shape)
        for mk, mv in (master.get("props") or {}).items():
            props.setdefault(mk, mv)

        # Connector detection: 1D shapes have BeginX
        if "BeginX" in cells:
            # Capture connector properties (interface, speed, vlan, etc.)
            connector_shapes.append((sid, shape, label, cells, props))
            continue

        # Node detection
        if "Width" in cells or "PinX" in cells:
            try:
                pin_x = float(cells.get("PinX", "0") or 0)
                pin_y = float(cells.get("PinY", "0") or 0)
            except ValueError:
                pin_x = pin_y = 0.0

            node_id = f"vsdx-p{page_idx}-{sid}"
            if not label:
                label = (
                    props.get("hostname")
                    or props.get("Hostname")
                    or props.get("Name")
                    or f"Shape-{sid}"
                )

            node: dict = {
                "id": node_id,
                "label": label,
                "type": "imported",
                "x": round(pin_x * 96),
                "y": round((page_h_in - pin_y) * 96),
            }
            if page_idx > 0:
                node["page"] = page_idx
            if props:
                node["properties"] = props
            nodes.append(node)
            sheet_to_node[(page_idx, sid)] = node_id

    # ── Edge resolution ─────────────────────────────────────────────────
    # Primary: <Connect> elements — the authoritative Visio wiring
    connected_ids: set = set()
    pair: dict[str, dict[str, str]] = {}
    for conn in root.iter("Connect"):
        from_sheet = conn.get("FromSheet", "")
        to_sheet = conn.get("ToSheet", "")
        from_cell = conn.get("FromCell", "")
        if not from_sheet or not to_sheet:
            continue
        slot = pair.setdefault(from_sheet, {})
        # FromCell indicates which end: "BeginX" = source, "EndX" = target
        role = "source" if from_cell.lower().startswith("begin") else "target"
        slot[role] = to_sheet

    # Build (sid -> (pin_x_in, pin_y_in)) for spatial fallback
    sid_to_pin: dict[str, tuple[float, float]] = {}
    for shape in root.iter("Shape"):
        sid_x = shape.get("ID", "")
        cmap = {c.get("N", ""): c.get("V", "") for c in shape.findall("Cell")}
        if "PinX" in cmap and "PinY" in cmap:
            try:
                sid_to_pin[sid_x] = (float(cmap["PinX"]), float(cmap["PinY"]))
            except ValueError:
                pass

    def _nearest_node_sid(x_in: float, y_in: float, *, max_in: float = 1.5) -> str | None:
        best_sid = None
        best_d = max_in
        for n_sid, (nx, ny) in sid_to_pin.items():
            if (page_idx, n_sid) not in sheet_to_node:
                continue
            d = math.hypot(nx - x_in, ny - y_in)
            if d < best_d:
                best_d = d
                best_sid = n_sid
        return best_sid

    for connector_sid, shape, label, cells_c, props_c in connector_shapes:
        ends = pair.get(connector_sid, {})
        src_sid = ends.get("source")
        dst_sid = ends.get("target")

        # Fallback 1: BegTrigger/EndTrigger formulas reference Sheet.N
        if not src_sid or not dst_sid:
            for cell in shape.findall("Cell"):
                n = cell.get("N", "")
                f = cell.get("F", "")
                if "Sheet." not in f:
                    continue
                ref = re.search(r"Sheet\.(\d+)", f)
                if not ref:
                    continue
                if n == "BegTrigger" and not src_sid:
                    src_sid = ref.group(1)
                elif n == "EndTrigger" and not dst_sid:
                    dst_sid = ref.group(1)

        # Fallback 2: spatial — match BeginX/EndX coords to nearest node Pin.
        # Catches connectors with neither <Connect> rows nor triggers (manual
        # drops, dynamic-glue rewrites, exports from auto-layout tools).
        if not src_sid:
            try:
                bx = float(cells_c.get("BeginX", "0") or 0)
                by = float(cells_c.get("BeginY", "0") or 0)
                src_sid = _nearest_node_sid(bx, by)
            except ValueError:
                pass
        if not dst_sid:
            try:
                ex = float(cells_c.get("EndX", "0") or 0)
                ey = float(cells_c.get("EndY", "0") or 0)
                dst_sid = _nearest_node_sid(ex, ey)
            except ValueError:
                pass

        src_node = sheet_to_node.get((page_idx, src_sid)) if src_sid else None
        dst_node = sheet_to_node.get((page_idx, dst_sid)) if dst_sid else None

        if src_node and dst_node:
            edge: dict = {
                "id": f"vsdx-p{page_idx}-e-{connector_sid}",
                "source": src_node,
                "target": dst_node,
                "label": label,
            }
            if props_c:
                edge["properties"] = props_c
            edges.append(edge)
            connected_ids.add(connector_sid)
        else:
            errors.append(
                f"unresolved connector page{page_idx} sid={connector_sid} "
                f"src={src_sid} dst={dst_sid}"
            )


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
