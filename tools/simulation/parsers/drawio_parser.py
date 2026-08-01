# CUI // SP-CTI
"""
Parse draw.io mxGraph XML (.drawio/.xml) into normalized graph_json.

Handles:
  - Swimlane containers → zones list + zone reference on each contained node
  - Vertex labels and style strings (fill, stroke, shape, dashed, etc.)
  - Edge connections (source/target by mxCell id) with optional labels
  - Network, microservice, and event-flow diagram types

Output schema:
  {
    "diagram_type": "drawio",
    "direction": null,
    "nodes": [
      {
        "id": str,
        "label": str,
        "shape": str,      # rectangle | rounded | ellipse | diamond | cylinder |
                           # triangle | hexagon | parallelogram | cloud | actor |
                           # image | <vendor-shape>
        "style": dict,     # parsed key→value from style attribute
        "zone": str | null # parent zone id, or null
      }
    ],
    "edges": [
      {
        "id": int,
        "source": str,
        "target": str,
        "label": str,
        "type": str,       # arrow | open | dashed | dotted | orthogonal | curved
        "style": dict
      }
    ],
    "zones": [
      {
        "id": str,
        "label": str,
        "style": dict
      }
    ]
  }
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_drawio(xml_str: str) -> dict:
    """Parse a draw.io mxGraph XML string into normalized graph_json."""
    xml_str = xml_str.strip()

    # Strip XML declaration / byte-order mark if present
    xml_str = re.sub(r"^<\?xml[^>]*\?>", "", xml_str).strip()

    # draw.io files wrap content in <mxGraphModel>; also accept bare <root>
    try:
        root = ET.fromstring(xml_str)  # nosec B314 — parsing trusted draw.io diagram XML from local files
    except ET.ParseError as exc:
        return {
            "diagram_type": "drawio",
            "direction": None,
            "nodes": [],
            "edges": [],
            "zones": [],
            "parse_error": str(exc),
        }

    # Locate the <root> element holding the cells.
    #
    # Three shapes arrive here. A bare <root>. An <mxGraphModel> whose direct
    # child is <root> — which is what every existing caller passes. And an actual
    # file saved by draw.io, which is <mxfile> → <diagram> → <mxGraphModel> →
    # <root>: two levels deeper than find() looks.
    #
    # That third case used to fall through to the best-effort branch below, where
    # findall("mxCell") on the <mxfile> element matched nothing and the parser
    # returned an empty graph with no error. A real diagram read as "no
    # components", which is worse than a crash: an empty architecture looks like
    # a diagram nobody drew anything in, and it is indistinguishable from one
    # that genuinely has nothing in it.
    #
    # Note this returns the FIRST <root> only. A .drawio file with several tabs
    # (floor plan / topology / rack elevation) has one per tab, and collapsing
    # them into a single node list would lose which drawing a component came
    # from. Callers that need that distinction should iterate <diagram> elements
    # themselves and hand each <mxGraphModel> in separately — see
    # tools/bom/extract_grid.py::_extract_drawio.
    mx_root = root if root.tag == "root" else root.find("root")
    if mx_root is None:
        mx_root = root.find(".//root")  # <mxfile>/<diagram>/<mxGraphModel>/<root>
    if mx_root is None:
        mx_root = root  # best-effort fallback

    cells = mx_root.findall("mxCell")

    # ------------------------------------------------------------------
    # Pass 1: catalog every cell by id, detect zones (swimlane containers)
    # ------------------------------------------------------------------
    cell_map: dict[str, ET.Element] = {}
    zone_ids: set[str] = set()

    for cell in cells:
        cid = cell.get("id", "")
        if not cid:
            continue
        cell_map[cid] = cell

        # mxCell ids "0" and "1" are the invisible root/default-parent cells
        if cid in ("0", "1"):
            continue

        style_raw = cell.get("style", "")
        is_vertex = cell.get("vertex") == "1"

        if is_vertex and _is_zone(style_raw):
            zone_ids.add(cid)

    # ------------------------------------------------------------------
    # Pass 2: resolve parent chains → zone membership for each node
    # ------------------------------------------------------------------

    def _find_zone(parent_id: str) -> str | None:
        """Walk up parent chain; return the first ancestor that is a zone."""
        visited: set[str] = set()
        pid = parent_id
        while pid and pid not in visited:
            visited.add(pid)
            if pid in zone_ids:
                return pid
            parent_cell = cell_map.get(pid)
            if parent_cell is None:
                break
            pid = parent_cell.get("parent", "")
        return None

    # ------------------------------------------------------------------
    # Pass 3: build zones, nodes, edges
    # ------------------------------------------------------------------
    zones: list[dict] = []
    nodes: list[dict] = []
    edges: list[dict] = []

    # Collect zones first (ordered by appearance)
    for cell in cells:
        cid = cell.get("id", "")
        if cid not in zone_ids:
            continue
        style_raw = cell.get("style", "")
        label = _clean_label(cell.get("value", cid))
        zones.append({
            "id": cid,
            "label": label,
            "style": _parse_style(style_raw),
        })

    # Collect vertices (non-zone) and edges
    edge_idx = 0
    for cell in cells:
        cid = cell.get("id", "")
        if not cid or cid in ("0", "1"):
            continue

        style_raw = cell.get("style", "")
        is_vertex = cell.get("vertex") == "1"
        is_edge = cell.get("edge") == "1"
        parent_id = cell.get("parent", "")

        if is_vertex and cid in zone_ids:
            continue  # already handled as zone

        if is_vertex and not is_edge:
            shape = _detect_shape(style_raw)
            zone = _find_zone(parent_id)
            nodes.append({
                "id": cid,
                "label": _clean_label(cell.get("value", cid)),
                "shape": shape,
                "style": _parse_style(style_raw),
                "zone": zone,
            })

        elif is_edge:
            source = cell.get("source", "")
            target = cell.get("target", "")
            label = _clean_label(cell.get("value", ""))
            etype = _detect_edge_type(style_raw)
            edges.append({
                "id": edge_idx,
                "source": source,
                "target": target,
                "label": label,
                "type": etype,
                "style": _parse_style(style_raw),
            })
            edge_idx += 1

    return {
        "diagram_type": "drawio",
        "direction": None,
        "nodes": nodes,
        "edges": edges,
        "zones": zones,
    }


# ---------------------------------------------------------------------------
# Style string parsing
# ---------------------------------------------------------------------------

# draw.io style strings look like:
#   "swimlane;fontStyle=0;childLayout=stackLayout;horizontal=1;"
#   "rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;"
_STYLE_KV_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:=([^;]*))?")


def _parse_style(raw: str) -> dict:
    """Parse a draw.io semicolon-separated style string into a dict.

    Bare keywords (e.g. 'swimlane', 'ellipse') are stored with value True.
    """
    result: dict[str, object] = {}
    for m in _STYLE_KV_RE.finditer(raw):
        key = m.group(1)
        val_raw = m.group(2)
        if val_raw is None:
            result[key] = True
        elif val_raw == "1":
            result[key] = True
        elif val_raw == "0":
            result[key] = False
        else:
            result[key] = val_raw
    return result


# ---------------------------------------------------------------------------
# Zone detection
# ---------------------------------------------------------------------------

def _is_zone(style_raw: str) -> bool:
    """Return True when the style indicates a swimlane / container cell."""
    parsed = _parse_style(style_raw)
    # Explicit swimlane keyword
    if "swimlane" in parsed:
        return True
    # shape variants used as group containers
    shape_val = str(parsed.get("shape", "")).lower()
    if shape_val in ("group", "container", "swimlane"):
        return True
    # draw.io sometimes uses container=1 without the swimlane keyword
    if parsed.get("container") is True:
        return True
    return False


# ---------------------------------------------------------------------------
# Shape detection
# ---------------------------------------------------------------------------

# Ordered most-specific → least-specific; first match wins
_SHAPE_RULES: list[tuple[str, re.Pattern]] = [
    ("cylinder",      re.compile(r"cylinder3|shape=cylinder", re.IGNORECASE)),
    ("ellipse",       re.compile(r"\bellipse\b|shape=ellipse", re.IGNORECASE)),
    ("diamond",       re.compile(r"\brhombus\b|shape=rhombus", re.IGNORECASE)),
    ("triangle",      re.compile(r"\btriangle\b|shape=triangle", re.IGNORECASE)),
    ("hexagon",       re.compile(r"\bhexagon\b|shape=hexagon", re.IGNORECASE)),
    ("parallelogram", re.compile(r"\bparallelogram\b", re.IGNORECASE)),
    ("cloud",         re.compile(r"\bcloud\b|shape=cloud", re.IGNORECASE)),
    ("actor",         re.compile(r"\bactor\b|mxgraph\.flowchart\.actor", re.IGNORECASE)),
    ("image",         re.compile(r"\bimage\b;", re.IGNORECASE)),
    ("rounded",       re.compile(r"\brounded=1\b", re.IGNORECASE)),
]

_VENDOR_SHAPE_RE = re.compile(
    r"shape=(mxgraph\.[a-zA-Z0-9_.]+)", re.IGNORECASE
)


def _detect_shape(style_raw: str) -> str:
    """Classify the shape of a vertex from its draw.io style string."""
    for shape_name, pattern in _SHAPE_RULES:
        if pattern.search(style_raw):
            return shape_name

    # Preserve vendor-specific shape IDs (AWS, Azure, network icons, etc.)
    vm = _VENDOR_SHAPE_RE.search(style_raw)
    if vm:
        return vm.group(1)

    return "rectangle"


# ---------------------------------------------------------------------------
# Edge type detection
# ---------------------------------------------------------------------------

def _detect_edge_type(style_raw: str) -> str:
    """Classify edge connector style from a draw.io style string."""
    parsed = _parse_style(style_raw)

    edge_style = str(parsed.get("edgeStyle", "")).lower()
    dashed = parsed.get("dashed") is True
    end_arrow = str(parsed.get("endArrow", "")).lower()

    if edge_style in ("orthogonalEdgeStyle".lower(), "elbowEdgeStyle".lower()):
        return "orthogonal"
    if edge_style == "entityRelationEdgeStyle".lower():
        return "arrow"
    if edge_style in ("segmentEdgeStyle".lower(),):
        return "orthogonal"
    if edge_style in ("curved", "curvedEdgeStyle".lower()):
        return "curved"

    if dashed:
        return "dashed"

    if end_arrow == "open":
        return "open"
    if end_arrow in ("block", "classic", "classicThin", ""):
        return "arrow"

    return "arrow"


# ---------------------------------------------------------------------------
# Label cleaning
# ---------------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_ENTITY_RE = re.compile(r"&(?:amp|lt|gt|quot|apos|nbsp);")
_HTML_ENTITY_MAP = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">",
    "&quot;": '"', "&apos;": "'", "&nbsp;": " ",
}


def _clean_label(raw: str) -> str:
    """Strip HTML tags and decode common HTML entities from a cell value."""
    text = _HTML_TAG_RE.sub(" ", raw)
    for entity, char in _HTML_ENTITY_MAP.items():
        text = text.replace(entity, char)
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as fh:
            src = fh.read()
    else:
        src = sys.stdin.read()

    print(json.dumps(parse_drawio(src), indent=2))
