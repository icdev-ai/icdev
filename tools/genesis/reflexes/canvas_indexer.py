#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Canvas Indexer Reflex — keep per-canvas KGs fresh for /ask.

Runs the 5-canvas indexer (PDC/BDC/DDC/ODC/IDC) on a rolling cadence so
the per-canvas /ask GraphRAG endpoints answer against current design
content. NDC and SDC are sourced from other pipelines (innovation
engine / STRIDE crosswalk seed) and are not touched here.

Design decisions:
  * GREEN tier — read each canvas's SQLite sidecar, write to kg_nodes
    / kg_edges / kg_graphs; no code mutation.
  * Zero LLM in the hot path; deterministic.
  * Cadence: 6 hours (design content changes slowly; indexer runs in
    <1s total so frequency is cheap but unnecessary).
  * Idempotent: canvas_indexer.index_canvas() deletes prior rows for
    the graph_id before inserting so a partial failure mid-cycle does
    not corrupt the KG.

Return shape (consumed by daemon.success_metric):
  {
    "run_id": "canvasidx-...",
    "canvases": 5,
    "nodes": <total nodes indexed>,
    "edges": <total edges indexed>,
    "errors": <count of failed canvases>,
    "elapsed_ms": <wall-clock>,
  }
"""
from __future__ import annotations
IMPLEMENTATION_STATUS = "full"

import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute one canvas indexing cycle.

    Called by the Genesis daemon every ``interval_seconds``.
    """
    cycle_id = f"canvasidx-{uuid.uuid4().hex[:12]}"
    start_wall = time.time()
    print(f"[canvas_indexer] cycle start  cycle_id={cycle_id}")

    try:
        from tools.knowledge_graph.canvas_indexer import index_canvas, CANVAS_CONFIG
    except ImportError as exc:
        print(f"  [canvas_indexer] ERROR: indexer unavailable: {exc}")
        return {
            "run_id": cycle_id,
            "canvases": 0,
            "nodes": 0,
            "edges": 0,
            "errors": 1,
            "elapsed_ms": int((time.time() - start_wall) * 1000),
            "error": str(exc)[:200],
        }

    total_nodes = 0
    total_edges = 0
    errors = 0
    per_canvas = []
    for canvas in CANVAS_CONFIG.keys():
        try:
            r = index_canvas(canvas)
            total_nodes += r.get("nodes", 0)
            total_edges += r.get("edges", 0)
            per_canvas.append(r)
            print(f"  [canvas_indexer] {canvas}: {r['designs']} designs -> {r['nodes']} nodes, {r['edges']} edges")
        except Exception as exc:
            errors += 1
            print(f"  [canvas_indexer] {canvas}: FAILED — {exc}")
            per_canvas.append({"canvas": canvas, "error": str(exc)[:200]})

    elapsed_ms = int((time.time() - start_wall) * 1000)
    print(f"[canvas_indexer] cycle done   nodes={total_nodes} edges={total_edges} errors={errors} elapsed={elapsed_ms}ms")

    return {
        "run_id": cycle_id,
        "canvases": len(CANVAS_CONFIG),
        "nodes": total_nodes,
        "edges": total_edges,
        "errors": errors,
        "elapsed_ms": elapsed_ms,
        "per_canvas": per_canvas,
        "success": errors == 0,
        "metric_value": float(errors),
    }


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(run({}, None), indent=2, default=str))