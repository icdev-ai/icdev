# [TEMPLATE: CUI // SP-CTI]
"""Canvas → KG indexer.

Reads design rows (with graph_json) from each canvas's SQLite sidecar
database and writes their nodes/edges into the main kg_nodes / kg_edges
tables so the per-canvas `/ask` endpoint (GraphRAG) can answer questions
over real design content. Idempotent — re-indexing a canvas clears its
graph rows first.

Supported canvases (all have a uniform graph_json shape):
  PDC  — pipelines            → graph_id=pdc-designs
  BDC  — boundary_designs     → graph_id=bdc-designs
  DDC  — data_designs         → graph_id=ddc-designs
  ODC  — observability_designs→ graph_id=odc-designs
  IDC  — infra_designs        → graph_id=idc-designs

NDC and SDC already have KGs (ndc-network-intelligence, sdc-kg-*) via
other pipelines; not touched here.

CLI:
    python -m tools.knowledge_graph.canvas_indexer --canvas pdc --json
    python -m tools.knowledge_graph.canvas_indexer --canvas all --json
"""
from __future__ import annotations

import argparse
import importlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from tools.db.storage import get_connection

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Canvas slug → (sidecar module path for get_connection, designs table, human name, fallback SQLite path)
# Each canvas's own get_connection() respects SQLite / PostgreSQL backend env
# flags (e.g. IDC_STORAGE_BACKEND=postgresql routes IDC designs into a PG db
# named "infra_canvas" instead of the .db file). Using the canvas's helper
# keeps the indexer in lockstep with live save traffic regardless of backend.
CANVAS_CONFIG: Dict[str, Tuple[str, str, str, str]] = {
    "pdc": ("tools.pipeline.db.init_db",            "pipelines",             "Pipeline Design Canvas",       "data/pipeline_canvas.db"),
    "bdc": ("tools.boundary_canvas.db.init_db",     "boundary_designs",      "Boundary Design Canvas",       "data/boundary_canvas.db"),
    "ddc": ("tools.data_canvas.db.init_db",         "data_designs",          "Data Design Canvas",           "data/data_canvas.db"),
    "odc": ("tools.observability_canvas.db.init_db","observability_designs", "Observability Design Canvas",  "data/observability_canvas.db"),
    "idc": ("tools.infra_canvas.db.init_db",        "infra_designs",         "Infrastructure Design Canvas", "data/infra_canvas.db"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _graph_id(canvas: str) -> str:
    return f"{canvas}-designs"


def _load_designs(conn_module: str, tbl: str, fallback_sqlite: str) -> List[Tuple[str, str, str]]:
    """Return list of (design_id, design_name, graph_json_str) tuples.

    Uses the canvas's own get_connection() so both SQLite and PostgreSQL
    storage backends work. Falls back to a direct sqlite3 read of the
    sidecar file only if the canvas module isn't importable (keeps the
    indexer working in slim / test envs).
    """
    rows: List[Tuple[str, str, str]] = []
    try:
        mod = importlib.import_module(conn_module)
        conn = mod.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT id, name, graph_json FROM {tbl} WHERE graph_json IS NOT NULL")  # nosec B608 -- fixed allowlist
            for r in cur.fetchall():
                if hasattr(r, "keys"):  # dict-like row (PG/sqlite-Row)
                    rows.append((r["id"], r["name"], r["graph_json"]))
                else:
                    rows.append((r[0], r[1], r[2]))
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return rows
    except Exception:
        # Fallback: direct sqlite read of the sidecar file
        abs_path = _REPO_ROOT / fallback_sqlite
        if not abs_path.exists():
            return []
        c = sqlite3.connect(str(abs_path))
        try:
            cur = c.cursor()
            cur.execute(f"SELECT id, name, graph_json FROM {tbl} WHERE graph_json IS NOT NULL")  # nosec B608
            return [(r[0], r[1], r[2]) for r in cur.fetchall()]
        finally:
            c.close()


def index_canvas(canvas: str) -> Dict[str, int]:
    """Index one canvas. Returns counters."""
    if canvas not in CANVAS_CONFIG:
        raise ValueError(f"unknown canvas '{canvas}'")
    conn_module, tbl, human, fallback = CANVAS_CONFIG[canvas]
    gid = _graph_id(canvas)
    designs = _load_designs(conn_module, tbl, fallback)

    nodes_seen = 0
    edges_seen = 0
    node_rows: List[Tuple[str, str, str, str, str, str]] = []
    edge_rows: List[Tuple[str, str, str, str, str, str, str]] = []
    # ensure node IDs are unique across designs by prefixing with design_id
    for design_id, design_name, gjs in designs:
        try:
            gj = json.loads(gjs) if gjs else {}
        except Exception:
            continue
        local_to_global: Dict[str, str] = {}
        for n in gj.get("nodes", []) or []:
            local = n.get("id")
            if not local:
                continue
            global_id = f"{gid}:{design_id}:{local}"
            local_to_global[local] = global_id
            ntype = n.get("type") or "unknown"
            label = n.get("label") or ntype
            props = {
                "design_id": design_id,
                "design_name": design_name,
                "node_type": ntype,
                "x": n.get("x"),
                "y": n.get("y"),
                "config": n.get("config") or {},
            }
            node_rows.append((
                global_id, gid, label, f"{canvas}_{ntype}",
                json.dumps(props, default=str), _now(),
            ))
            nodes_seen += 1
        for e in gj.get("edges", []) or []:
            src = local_to_global.get(e.get("source"))
            tgt = local_to_global.get(e.get("target"))
            if not src or not tgt:
                continue
            eid = f"{gid}:{design_id}:{e.get('id') or f'{src}->{tgt}'}"
            edge_rows.append((
                eid, gid, src, tgt,
                e.get("label") or "connects", "1.0",
                json.dumps({"design_id": design_id}), _now(),
            ))
            edges_seen += 1

    # Write to main kg store. Clear prior rows for this graph's nodes
    # + edges first (no FKs into them). kg_graphs is UPSERTed rather
    # than deleted because kg_retrieval_log.graph_id references it —
    # a DELETE would violate the FK mid-cycle.
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM kg_edges WHERE graph_id = %s", (gid,))
    cur.execute("DELETE FROM kg_nodes WHERE graph_id = %s", (gid,))

    for row in node_rows:
        cur.execute(
            "INSERT INTO kg_nodes (id, graph_id, label, entity_type, properties, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            row,
        )
    for row in edge_rows:
        cur.execute(
            "INSERT INTO kg_edges (id, graph_id, source_id, target_id, relationship, weight, properties, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            row,
        )
    cur.execute(
        """INSERT INTO kg_graphs (id, project_id, name, description, entity_count, edge_count, metadata, created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (id) DO UPDATE SET
             project_id = EXCLUDED.project_id,
             name = EXCLUDED.name,
             description = EXCLUDED.description,
             entity_count = EXCLUDED.entity_count,
             edge_count = EXCLUDED.edge_count,
             metadata = EXCLUDED.metadata,
             updated_at = EXCLUDED.updated_at""",
        (
            gid, gid, human, f"Canvas-indexed {human} designs",
            nodes_seen, edges_seen,
            json.dumps({"source": "canvas_indexer", "conn_module": conn_module, "designs_table": tbl}),
            _now(), _now(),
        ),
    )
    conn.commit()
    return {
        "canvas": canvas,
        "graph_id": gid,
        "designs": len(designs),
        "nodes": nodes_seen,
        "edges": edges_seen,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--canvas", choices=list(CANVAS_CONFIG.keys()) + ["all"], required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    results = []
    canvases = list(CANVAS_CONFIG.keys()) if args.canvas == "all" else [args.canvas]
    for c in canvases:
        try:
            results.append(index_canvas(c))
        except Exception as exc:
            results.append({"canvas": c, "error": str(exc)[:300]})

    if args.json:
        print(json.dumps({"results": results}, indent=2))
    else:
        for r in results:
            if "error" in r:
                print(f"  [{r['canvas']}] ERROR: {r['error']}")
            else:
                print(f"  [{r['canvas']}] {r['designs']} designs -> {r['nodes']} nodes, {r['edges']} edges (graph_id={r['graph_id']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
