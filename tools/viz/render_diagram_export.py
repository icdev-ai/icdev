# CUI // SP-CTI
"""Editable-diagram exporters for the Viz Kernel: draw.io and Excalidraw.

Turns a :class:`tools.viz.spec.DiagramSpec` into formats users can open and
edit in external tools:

  - to_drawio(spec)      → draw.io / diagrams.net XML (.drawio)  [stdlib only]
  - to_excalidraw(spec)  → Excalidraw scene JSON (.excalidraw)   [stdlib only]

draw.io reuses the battle-tested ``tools/canvas/export_utils.export_drawio``
(grid layout, mxGraphModel). Excalidraw builds a minimal but valid scene with
rounded rectangles, bound text, and arrows, positioned via the shared
``tools.viz.diagram.layout`` so it matches the PNG/SVG renderings.
"""
from __future__ import annotations

import json

from tools.viz.palette import get_palette
from tools.viz.spec import DiagramSpec
from tools.viz import diagram as _diagram


def _graph_json(spec: DiagramSpec) -> dict:
    """Adapt a DiagramSpec to the canvas graph_json shape (nodes/edges)."""
    return {
        "nodes": [
            {"id": str(n.get("id", n.get("label", f"n{i}"))),
             "label": str(n.get("label", n.get("id", f"n{i}"))),
             "type": str(n.get("type", "default"))}
            for i, n in enumerate(spec.nodes)
        ],
        "edges": [
            {"source": str(e.get("source", "")), "target": str(e.get("target", "")),
             "type": str(e.get("label", e.get("type", "")))}
            for e in spec.edges
        ],
    }


def to_drawio(spec: DiagramSpec, name: str = "ICDEV Diagram",
              canvas_key: str = "VIZ") -> str:
    """Export a DiagramSpec to draw.io / diagrams.net XML."""
    from tools.canvas.export_utils import export_drawio
    return export_drawio(name or spec.title or "ICDEV Diagram", _graph_json(spec), canvas_key)


# ── Excalidraw scene format ──────────────────────────────────────────────────

_NODE_W, _NODE_H = 180.0, 70.0


def _det_seed(token: str) -> int:
    """Deterministic positive int seed from a token (no RNG → resume-safe)."""
    h = 0
    for ch in token:
        h = (h * 131 + ord(ch)) & 0x7FFFFFFF
    return h or 1


def to_excalidraw(spec: DiagramSpec, theme: str = "midnight_executive") -> str:
    """Export a DiagramSpec to an Excalidraw scene JSON string (.excalidraw)."""
    pal = get_palette(theme)
    stroke = pal.hex("accent")
    fill = pal.hex("dark")
    text_color = pal.hex("text")

    pos = _diagram.layout(spec)
    # Scale layout units → excalidraw pixels.
    screen: dict[str, tuple[float, float]] = {}
    if pos:
        xs = [p[0] for p in pos.values()]
        ys = [p[1] for p in pos.values()]
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        sx = 900.0 / ((maxx - minx) or 1)
        sy = 560.0 / ((maxy - miny) or 1)
        for k, (x, y) in pos.items():
            screen[k] = (100 + (x - minx) * sx, 80 + (y - miny) * sy)

    elements: list[dict] = []
    rect_id: dict[str, str] = {}
    id_map = {str(n.get("id", n.get("label", f"n{i}"))): n
              for i, n in enumerate(spec.nodes)}

    # Rectangles + bound text
    for nid, (cx, cy) in screen.items():
        node = id_map.get(nid, {})
        label = str(node.get("label", nid))
        rid = f"rect-{_det_seed(nid)}"
        tid = f"text-{_det_seed(nid)}"
        rect_id[nid] = rid
        x = cx - _NODE_W / 2
        y = cy - _NODE_H / 2
        elements.append({
            "id": rid, "type": "rectangle", "x": x, "y": y,
            "width": _NODE_W, "height": _NODE_H, "angle": 0,
            "strokeColor": stroke, "backgroundColor": fill, "fillStyle": "solid",
            "strokeWidth": 2, "strokeStyle": "solid", "roughness": 0, "opacity": 100,
            "roundness": {"type": 3}, "seed": _det_seed(rid), "version": 1,
            "versionNonce": _det_seed(rid + "n"), "isDeleted": False,
            "groupIds": [], "frameId": None, "boundElements": [{"type": "text", "id": tid}],
            "updated": 1, "link": None, "locked": False,
        })
        elements.append({
            "id": tid, "type": "text", "x": x + 10, "y": y + _NODE_H / 2 - 10,
            "width": _NODE_W - 20, "height": 20, "angle": 0,
            "strokeColor": text_color, "backgroundColor": "transparent",
            "fillStyle": "solid", "strokeWidth": 1, "strokeStyle": "solid",
            "roughness": 0, "opacity": 100, "seed": _det_seed(tid), "version": 1,
            "versionNonce": _det_seed(tid + "n"), "isDeleted": False, "groupIds": [],
            "frameId": None, "roundness": None, "boundElements": [], "updated": 1,
            "link": None, "locked": False, "fontSize": 16, "fontFamily": 2,
            "text": label, "textAlign": "center", "verticalAlign": "middle",
            "containerId": rid, "originalText": label, "lineHeight": 1.25,
        })

    # Arrows between bound rectangles
    for ei, e in enumerate(spec.edges):
        s = str(e.get("source", ""))
        t = str(e.get("target", ""))
        if s not in screen or t not in screen:
            continue
        x1, y1 = screen[s]
        x2, y2 = screen[t]
        aid = f"arrow-{ei}-{_det_seed(s + t)}"
        elements.append({
            "id": aid, "type": "arrow", "x": x1, "y": y1,
            "width": x2 - x1, "height": y2 - y1, "angle": 0,
            "strokeColor": stroke, "backgroundColor": "transparent",
            "fillStyle": "solid", "strokeWidth": 2, "strokeStyle": "solid",
            "roughness": 0, "opacity": 100, "seed": _det_seed(aid), "version": 1,
            "versionNonce": _det_seed(aid + "n"), "isDeleted": False, "groupIds": [],
            "frameId": None, "roundness": {"type": 2}, "boundElements": [], "updated": 1,
            "link": None, "locked": False, "points": [[0, 0], [x2 - x1, y2 - y1]],
            "lastCommittedPoint": None,
            "startBinding": {"elementId": rect_id[s], "focus": 0, "gap": 4},
            "endBinding": {"elementId": rect_id[t], "focus": 0, "gap": 4},
            "startArrowhead": None, "endArrowhead": "arrow",
        })

    scene = {
        "type": "excalidraw",
        "version": 2,
        "source": "ICDEV™ Viz Kernel",
        "elements": elements,
        "appState": {"viewBackgroundColor": pal.hex("bg"), "gridSize": None},
        "files": {},
    }
    return json.dumps(scene, indent=2)
