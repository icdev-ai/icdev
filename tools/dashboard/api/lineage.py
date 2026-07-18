#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: Artifact Lineage (Phase 56, D348).

Joins digital thread, provenance, and audit trail into unified DAG data
for the lineage visualization dashboard.
"""

import sys
from tools.db.storage import get_connection, table_exists
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "data" / "icdev.db"

lineage_api = Blueprint("lineage_api", __name__, url_prefix="/api/lineage")


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    return conn


def _table_exists(conn, table_name):
    """Check if a table exists (works for both SQLite and PostgreSQL)."""
    return table_exists(conn, table_name)


@lineage_api.route("/graph", methods=["GET"])
def lineage_graph():
    """GET /api/lineage/graph — Build artifact lineage DAG for a project."""
    project_id = request.args.get("project_id", "")
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    conn = _get_db()
    nodes = []
    edges = []

    try:
        # --- Digital thread nodes (MBSE) ---
        if _table_exists(conn, "digital_thread_links"):
            rows = conn.execute(
                "SELECT id, source_type, source_id, target_type, target_id, link_type "
                "FROM digital_thread_links WHERE project_id = %s ORDER BY id",
                (project_id,),
            ).fetchall()
            seen = set()
            for r in rows:
                src_key = f"{r['source_type']}:{r['source_id']}"
                tgt_key = f"{r['target_type']}:{r['target_id']}"
                if src_key not in seen:
                    nodes.append(
                        {"id": src_key, "type": r["source_type"], "label": r["source_id"], "source": "digital_thread"}
                    )
                    seen.add(src_key)
                if tgt_key not in seen:
                    nodes.append(
                        {"id": tgt_key, "type": r["target_type"], "label": r["target_id"], "source": "digital_thread"}
                    )
                    seen.add(tgt_key)
                edges.append(
                    {"source": src_key, "target": tgt_key, "relation": r["link_type"], "origin": "digital_thread"}
                )

        # --- Provenance entities (W3C PROV) ---
        if _table_exists(conn, "prov_entities") and _table_exists(conn, "prov_relations"):
            entities = conn.execute(
                "SELECT id AS entity_id, entity_type, label FROM prov_entities WHERE project_id = %s",
                (project_id,),
            ).fetchall()
            for e in entities:
                node_id = f"prov:{e['entity_id']}"
                nodes.append(
                    {
                        "id": node_id,
                        "type": e["entity_type"],
                        "label": e["label"] or e["entity_id"],
                        "source": "provenance",
                    }
                )

            relations = conn.execute(
                "SELECT subject_id AS source_id, object_id AS target_id, relation_type FROM prov_relations WHERE project_id = %s",
                (project_id,),
            ).fetchall()
            for r in relations:
                edges.append(
                    {
                        "source": f"prov:{r['source_id']}",
                        "target": f"prov:{r['target_id']}",
                        "relation": r["relation_type"],
                        "origin": "provenance",
                    }
                )

        # --- Audit trail events ---
        if _table_exists(conn, "audit_trail"):
            audit_rows = conn.execute(
                "SELECT id, event_type, action, actor, created_at "
                "FROM audit_trail WHERE project_id = %s ORDER BY created_at DESC LIMIT 50",
                (project_id,),
            ).fetchall()
            for a in audit_rows:
                node_id = f"audit:{a['id']}"
                nodes.append(
                    {
                        "id": node_id,
                        "type": "audit_event",
                        "label": f"{a['action']} ({a['actor']})",
                        "source": "audit_trail",
                        "timestamp": a["created_at"],
                    }
                )

        # --- SBOM components ---
        if _table_exists(conn, "sbom_records"):
            sbom_rows = conn.execute(
                "SELECT id, file_path, version FROM sbom_records WHERE project_id = %s LIMIT 100",
                (project_id,),
            ).fetchall()
            for s in sbom_rows:
                node_id = f"sbom:{s['id']}"
                version = s["version"] or ""
                label = s["file_path"] or str(s["id"])
                nodes.append(
                    {
                        "id": node_id,
                        "type": "sbom_component",
                        "label": f"{label}@{version}" if version else label,
                        "source": "sbom",
                    }
                )

    finally:
        conn.close()

    return jsonify(
        {
            "project_id": project_id,
            "nodes": nodes,
            "edges": edges,
            "summary": {
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "sources": list(set(n["source"] for n in nodes)),
            },
        }
    )


@lineage_api.route("/stats", methods=["GET"])
def lineage_stats():
    """GET /api/lineage/stats — Lineage statistics across sources."""
    conn = _get_db()
    stats = {}

    try:
        for table, label in [
            ("digital_thread_links", "Digital Thread Links"),
            ("prov_entities", "Provenance Entities"),
            ("prov_relations", "Provenance Relations"),
            ("audit_trail", "Audit Events"),
            ("sbom_records", "SBOM Components"),
        ]:
            if _table_exists(conn, table):
                row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # nosec B608 -- table/column names are internal constants, not user input
                stats[table] = {"label": label, "count": row[0]}
            else:
                stats[table] = {"label": label, "count": 0, "missing": True}
    finally:
        conn.close()

    return jsonify({"stats": stats})
