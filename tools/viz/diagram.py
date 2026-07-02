# CUI // SP-CTI
"""Diagram helpers for the Viz Kernel.

Bridges :class:`tools.viz.spec.DiagramSpec` to:
  - Mermaid source     (``to_mermaid``)          — web rendering (mermaid.js)
  - 2D node positions  (``layout``)              — PNG/SVG rendering
  - DiagramSpec         (``from_mermaid``)        — ingest existing Mermaid

Layout uses networkx when available (spring / layered), else a deterministic
grid fallback so the kernel still works with no extra deps.
"""
from __future__ import annotations

from typing import Any

from tools.viz.spec import DiagramSpec


def _node_ids(spec: DiagramSpec) -> list[str]:
    ids: list[str] = []
    for i, n in enumerate(spec.nodes):
        ids.append(str(n.get("id", n.get("label", f"n{i}"))))
    return ids


def _grid_layout(ids: list[str]) -> dict[str, tuple[float, float]]:
    """Deterministic grid in a unit square. Always available."""
    n = len(ids)
    if n == 0:
        return {}
    import math
    cols = max(1, math.ceil(math.sqrt(n)))
    pos: dict[str, tuple[float, float]] = {}
    for idx, nid in enumerate(ids):
        col = idx % cols
        row = idx // cols
        # y inverted so row 0 is at top when callers flip the axis
        pos[nid] = (float(col), float(-row))
    return pos


def layout(spec: DiagramSpec) -> dict[str, tuple[float, float]]:
    """Return ``{node_id: (x, y)}`` positions for the diagram.

    Coordinates are arbitrary units; renderers normalize to their canvas.
    """
    ids = _node_ids(spec)
    if not ids:
        return {}

    try:
        import networkx as nx

        g = nx.DiGraph()
        g.add_nodes_from(ids)
        idset = set(ids)
        for e in spec.edges:
            s = str(e.get("source", ""))
            t = str(e.get("target", ""))
            if s in idset and t in idset:
                g.add_edge(s, t)

        if spec.layout == "layered":
            try:
                pos = nx.multipartite_layout(_assign_layers(g))
                return {k: (float(v[0]), float(v[1])) for k, v in pos.items()}
            except Exception:
                pass
        if spec.layout == "grid":
            return _grid_layout(ids)
        # default: spring (deterministic seed)
        pos = nx.spring_layout(g, seed=42, k=None)
        return {k: (float(v[0]), float(v[1])) for k, v in pos.items()}
    except Exception:
        return _grid_layout(ids)


def _assign_layers(g):
    """Assign a 'subset' layer per node via longest-path depth (for multipartite)."""
    import networkx as nx

    depth: dict[str, int] = {}
    try:
        order = list(nx.topological_sort(g))
    except Exception:
        order = list(g.nodes())
    for node in order:
        preds = list(g.predecessors(node))
        depth[node] = (max((depth.get(p, 0) for p in preds), default=-1) + 1) if preds else 0
    for node in g.nodes():
        g.nodes[node]["subset"] = depth.get(node, 0)
    return g


_MERMAID_DIRECTION = {"spring": "TD", "layered": "LR", "grid": "TD"}


def to_mermaid(spec: DiagramSpec) -> str:
    """Emit a Mermaid flowchart source string for web rendering."""
    direction = _MERMAID_DIRECTION.get(spec.layout, "TD")
    lines = [f"flowchart {direction}"]
    # Sanitize ids to mermaid-safe tokens, keep a mapping for edges.
    id_map: dict[str, str] = {}
    for i, n in enumerate(spec.nodes):
        raw = str(n.get("id", n.get("label", f"n{i}")))
        safe = _safe_id(raw, i)
        id_map[raw] = safe
        label = _mm_escape(str(n.get("label", raw)))
        lines.append(f'    {safe}["{label}"]')
    for e in spec.edges:
        s = id_map.get(str(e.get("source", "")))
        t = id_map.get(str(e.get("target", "")))
        if not s or not t:
            continue
        lbl = str(e.get("label", "")).strip()
        if lbl:
            lines.append(f'    {s} -->|{_mm_escape(lbl)}| {t}')
        else:
            lines.append(f"    {s} --> {t}")
    return "\n".join(lines)


def _safe_id(raw: str, i: int) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in raw).strip("_")
    return safe or f"n{i}"


def _mm_escape(text: str) -> str:
    return text.replace('"', "'").replace("|", "/").replace("\n", " ")


def from_mermaid(source: str) -> DiagramSpec:
    """Parse Mermaid source into a DiagramSpec (best-effort, via MermaidParser)."""
    try:
        from tools.simulation.parsers.mermaid_parser import parse_mermaid

        parsed: dict[str, Any] = parse_mermaid(source)
        nodes = [
            {"id": str(n.get("id", "")), "label": str(n.get("label", n.get("id", ""))),
             "type": str(n.get("shape", "default"))}
            for n in parsed.get("nodes", [])
        ]
        edges = [
            {"source": str(e.get("source", "")), "target": str(e.get("target", "")),
             "label": str(e.get("label", ""))}
            for e in parsed.get("edges", [])
        ]
        return DiagramSpec(title="", nodes=nodes, edges=edges, layout="layered")
    except Exception:
        return DiagramSpec()
