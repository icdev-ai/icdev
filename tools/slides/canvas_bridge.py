# CUI // SP-CTI
"""Canvas → presentation bridge.

Pulls design canvases (their node/edge ``graph_json``) into slide decks as
native, on-theme diagram slides (Viz Kernel ``DiagramSpec``), and provides an
auto-aggregate "overview" across all canvases. Anything that can't be
reconstructed natively is captured as an image by the client (the capture
endpoint accepts an image payload as the fallback).

Enumeration is best-effort and defensive: each canvas exposes a ``get_connection``
and a design table of ``{id, name, graph_json}`` rows; a missing/empty table is
skipped, never raised.
"""
from __future__ import annotations

import json
from typing import Any

# canvas_key → (db init module exposing get_connection(), design table, display name)
CANVAS_DESIGN_SOURCES: dict[str, dict[str, str]] = {
    "agentic_ai": {"module": "tools.agentic_ai_canvas.db.init_db", "table": "aadc_designs", "name": "Agentic AI"},
    "aiml": {"module": "tools.aiml_canvas.db.init_db", "table": "aiml_designs", "name": "AI/ML Model"},
    "boundary": {"module": "tools.boundary_canvas.db.init_db", "table": "boundary_designs", "name": "ATO Boundary"},
    "data": {"module": "tools.data_canvas.db.init_db", "table": "data_designs", "name": "Data"},
    "infra": {"module": "tools.infra_canvas.db.init_db", "table": "infra_designs", "name": "Infrastructure"},
    "migration": {"module": "tools.migration_canvas.db.init_db", "table": "migration_designs", "name": "Migration"},
    "mission": {"module": "tools.mission_canvas.db.init_db", "table": "mission_designs", "name": "Mission"},
    "observability": {"module": "tools.observability_canvas.db.init_db", "table": "observability_designs", "name": "Observability"},
    "security": {"module": "tools.security_canvas.db.init_db", "table": "security_designs", "name": "Security"},
}


def _row_get(row: Any, key: str, default=None):
    try:
        if hasattr(row, "keys"):
            return row[key] if key in row.keys() else default
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _conn_for(canvas_key: str):
    src = CANVAS_DESIGN_SOURCES.get(canvas_key)
    if not src:
        return None
    import importlib
    mod = importlib.import_module(src["module"])
    if hasattr(mod, "init_db"):
        try:
            mod.init_db()
        except Exception:
            pass
    return mod.get_connection()


def list_designs(limit_per_canvas: int = 50) -> list[dict]:
    """Enumerate all canvas designs (best-effort) → [{canvas_key, canvas_name, id, name}]."""
    out: list[dict] = []
    for key, src in CANVAS_DESIGN_SOURCES.items():
        conn = None
        try:
            conn = _conn_for(key)
            if conn is None:
                continue
            rows = conn.execute(
                f"SELECT * FROM {src['table']} ORDER BY id DESC LIMIT ?",  # nosec B608 (table from static map)
                (limit_per_canvas,),
            ).fetchall()
            for r in rows:
                did = _row_get(r, "id")
                if did is None:
                    continue
                out.append({
                    "canvas_key": key,
                    "canvas_name": src["name"],
                    "id": did,
                    "name": str(_row_get(r, "name", f"Design {did}") or f"Design {did}"),
                })
        except Exception:
            continue
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    return out


def load_graph(canvas_key: str, design_id: Any) -> dict | None:
    """Return a design's graph_json dict, or None."""
    src = CANVAS_DESIGN_SOURCES.get(canvas_key)
    if not src:
        return None
    conn = None
    try:
        conn = _conn_for(canvas_key)
        if conn is None:
            return None
        row = conn.execute(
            f"SELECT graph_json, name FROM {src['table']} WHERE id = ?",  # nosec B608
            (design_id,),
        ).fetchone()
        if not row:
            return None
        gj = _row_get(row, "graph_json")
        name = _row_get(row, "name")
        graph = json.loads(gj) if isinstance(gj, str) else (gj or {})
        if isinstance(graph, dict):
            graph.setdefault("_name", name)
        return graph
    except Exception:
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def graph_to_diagram_slide(graph_json: dict, title: str, theme: str = "midnight_executive") -> dict:
    """Build a slide dict (diagram element, native DiagramSpec) from canvas graph_json."""
    from tools.viz.spec import DiagramSpec
    from tools.viz import elements as _elements

    nodes = [
        {"id": str(n.get("id", i)), "label": str(n.get("label", n.get("name", n.get("id", i)))),
         "type": str(n.get("type", "default"))}
        for i, n in enumerate(graph_json.get("nodes", []))
    ]
    edges = [
        {"source": str(e.get("source", "")), "target": str(e.get("target", "")),
         "label": str(e.get("label", e.get("type", "")))}
        for e in graph_json.get("edges", [])
    ]
    spec = DiagramSpec(title=title, nodes=nodes, edges=edges, layout="spring")
    slide = {
        "slide_type": "content", "title": title,
        "speaker_notes": f"Imported from the {title} canvas design.",
        "diagram": spec.to_dict(),
    }
    slide["elements"] = _elements.elements_to_dicts(_elements.auto_layout(slide))
    return slide


def design_to_slide(canvas_key: str, design_id: Any, theme: str = "midnight_executive") -> dict | None:
    """Pull a specific canvas design and convert it to a diagram slide."""
    graph = load_graph(canvas_key, design_id)
    if not graph or not graph.get("nodes"):
        return None
    src = CANVAS_DESIGN_SOURCES.get(canvas_key, {})
    title = str(graph.get("_name") or f"{src.get('name', canvas_key)} Design")
    return graph_to_diagram_slide(graph, title, theme)


def build_overview_slides(theme: str = "midnight_executive", max_designs: int = 20) -> list[dict]:
    """Auto-aggregate: a title slide + one diagram slide per canvas design."""
    designs = list_designs()
    slides: list[dict] = [{
        "slide_type": "title", "title": "Canvas Designs Overview", "bullets": [],
        "speaker_notes": f"Auto-generated overview of {len(designs)} canvas design(s).",
    }]
    for d in designs[:max_designs]:
        s = design_to_slide(d["canvas_key"], d["id"], theme)
        if s:
            s["title"] = f"{d['canvas_name']}: {d['name']}"[:80]
            slides.append(s)
    slides.append({"slide_type": "outro", "title": "Thank You",
                   "bullets": ["Explore each canvas at its dashboard"], "speaker_notes": ""})
    return slides
