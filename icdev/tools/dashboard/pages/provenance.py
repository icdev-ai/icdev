#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: GovChain / blockchain provenance verification endpoints.

Mounted at /api/govchain-provenance to keep the W3C PROV-AGENT lineage surface
(tools/dashboard/api/traces.py::provenance_api, /api/provenance/*) distinct from
this blockchain-anchoring surface. The two previously shared /api/provenance and
had colliding Python variable names; registration order decided which won.

GET  /api/govchain-provenance?project_id=<id>        — list registry entries with trust scores
GET  /api/govchain-provenance/graph?project_id=<id>   — DAG nodes/edges for visualization
POST /api/govchain-provenance/verify                   — verify a specific registry entry
GET  /api/govchain-provenance/blockchain-status        — GovChain queue depth and anchor stats
"""

import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, table_exists

govchain_provenance_api = Blueprint(
    "govchain_provenance_api", __name__, url_prefix="/api/govchain-provenance"
)
# Backward-compat alias for existing importers of the old variable name.
provenance_api = govchain_provenance_api

DB_PATH = BASE_DIR / "data" / "icdev.db"


def _get_db():
    return get_connection(db_path=str(DB_PATH))


def _table_exists(conn, table_name):
    """Check if a table exists (works for both SQLite and PostgreSQL)."""
    return table_exists(conn, table_name)


@provenance_api.route("", methods=["GET"])
def list_provenance():
    """GET /api/govchain-provenance?project_id=<id>"""
    project_id = request.args.get("project_id", "")
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    conn = _get_db()
    try:
        if not _table_exists(conn, "source_citation_registry"):
            return jsonify({"error": "source_citation_registry not initialized"}), 500

        rows = conn.execute(
            """SELECT id, citation_type, source_table, source_record_id, source_doc,
                      source_hash, anchor_hash, merkle_root, blockchain_tx_id,
                      classification, trust_score, created_at
               FROM source_citation_registry
               WHERE project_id = %s
               ORDER BY created_at DESC""",
            (project_id,),
        ).fetchall()

        entries = []
        for r in rows:
            entries.append(
                {
                    "id": r["id"],
                    "citation_type": r["citation_type"],
                    "source_table": r["source_table"],
                    "source_record_id": r["source_record_id"],
                    "source_doc": r["source_doc"],
                    "source_hash": r["source_hash"],
                    "merkle_root": r["merkle_root"],
                    "blockchain_tx_id": r["blockchain_tx_id"],
                    "blockchain_verified": bool(r["blockchain_tx_id"]),
                    "classification": r["classification"],
                    "trust_score": r["trust_score"] or 0.0,
                    "created_at": r["created_at"],
                }
            )

        avg_score = round(sum(e["trust_score"] for e in entries) / len(entries), 2) if entries else 0.0

        return jsonify(
            {
                "project_id": project_id,
                "entries": entries,
                "total": len(entries),
                "average_trust_score": avg_score,
                "blockchain_verified_count": sum(1 for e in entries if e["blockchain_verified"]),
            }
        )
    finally:
        conn.close()


@provenance_api.route("/graph", methods=["GET"])
def provenance_graph():
    """GET /api/govchain-provenance/graph?project_id=<id> — DAG for D3/Cytoscape"""
    project_id = request.args.get("project_id", "")
    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    conn = _get_db()
    try:
        nodes = []
        edges = []

        # Registry entries as nodes
        if _table_exists(conn, "source_citation_registry"):
            rows = conn.execute(
                """SELECT id, citation_type, source_table, source_doc, source_hash,
                          merkle_root, blockchain_tx_id, trust_score
                   FROM source_citation_registry WHERE project_id = %s""",
                (project_id,),
            ).fetchall()
            for r in rows:
                nodes.append(
                    {
                        "id": r["id"],
                        "type": r["citation_type"],
                        "label": r["source_doc"] or f"{r['source_table']}:{r['id'][:8]}",
                        "blockchain_verified": bool(r["blockchain_tx_id"]),
                        "trust_score": r["trust_score"] or 0.0,
                        "source": "registry",
                    }
                )

        # Audit trail nodes
        if _table_exists(conn, "audit_trail"):
            rows = conn.execute(
                "SELECT id, event_type, action, actor, hash, previous_hash, signature FROM audit_trail WHERE project_id = %s ORDER BY id LIMIT 50",
                (project_id,),
            ).fetchall()
            for r in rows:
                node_id = f"audit:{r['id']}"
                nodes.append(
                    {
                        "id": node_id,
                        "type": "audit_event",
                        "label": f"{r['event_type']} ({r['actor']})",
                        "hash": r["hash"],
                        "previous_hash": r["previous_hash"],
                        "signed": bool(r["signature"]),
                        "source": "audit_trail",
                    }
                )
                # Chain edges
                if r["previous_hash"]:
                    edges.append(
                        {
                            "source": f"audit:{r['id']-1}",
                            "target": node_id,
                            "relation": "previous_hash",
                            "origin": "audit_chain",
                        }
                    )

        return jsonify(
            {
                "project_id": project_id,
                "nodes": nodes,
                "edges": edges,
                "summary": {
                    "total_nodes": len(nodes),
                    "total_edges": len(edges),
                    "blockchain_verified_nodes": sum(1 for n in nodes if n.get("blockchain_verified")),
                },
            }
        )
    finally:
        conn.close()


@provenance_api.route("/verify", methods=["POST"])
def verify_entry():
    """POST /api/govchain-provenance/verify — verify a registry entry

    Body: {"registry_id": "scr-..."}
    """
    data = request.get_json(force=True, silent=True) or {}
    registry_id = data.get("registry_id", "")
    if not registry_id:
        return jsonify({"error": "registry_id required"}), 400

    try:
        from tools.blockchain.provenance_verifier import verify_citation

        result = verify_citation(registry_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@provenance_api.route("/blockchain-status", methods=["GET"])
def blockchain_status():
    """GET /api/govchain-provenance/blockchain-status — GovChain queue depth and anchor stats."""
    conn = _get_db()
    try:
        result = {
            "pending_queue_depth": 0,
            "total_anchored": 0,
            "total_unanchored": 0,
            "anchor_pct": 0.0,
            "recent_tx_ids": [],
            "fabric_enabled": False,
        }

        # Pending queue
        if _table_exists(conn, "govchain_pending_operations"):
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM govchain_pending_operations WHERE status='pending'"
            ).fetchone()
            result["pending_queue_depth"] = row["cnt"] if row else 0

            flushed = conn.execute(
                "SELECT COUNT(*) as cnt FROM govchain_pending_operations WHERE status='flushed'"
            ).fetchone()
            result["total_flushed"] = flushed["cnt"] if flushed else 0

        # Anchor stats from registry
        if _table_exists(conn, "source_citation_registry"):
            totals = conn.execute(
                "SELECT COUNT(*) as total, SUM(CASE WHEN blockchain_tx_id IS NOT NULL THEN 1 ELSE 0 END) as anchored FROM source_citation_registry"
            ).fetchone()
            if totals and totals["total"]:
                result["total_anchored"] = totals["anchored"] or 0
                total = totals["total"]
                result["total_unanchored"] = total - result["total_anchored"]
                result["anchor_pct"] = round(result["total_anchored"] / total * 100, 1)

            # 5 most recent TX IDs
            recent = conn.execute(
                "SELECT id, blockchain_tx_id, citation_type, created_at FROM source_citation_registry WHERE blockchain_tx_id IS NOT NULL ORDER BY created_at DESC LIMIT 5"
            ).fetchall()
            result["recent_tx_ids"] = [
                {"registry_id": r["id"], "tx_id": r["blockchain_tx_id"], "citation_type": r["citation_type"], "created_at": r["created_at"]}
                for r in recent
            ]

        # Fabric enabled status
        try:
            from tools.blockchain.blockchain_config import get_config
            cfg = get_config()
            result["fabric_enabled"] = cfg.is_enabled()
            result["air_gapped"] = cfg.is_air_gapped()
        except Exception:
            pass

        return jsonify(result)
    finally:
        conn.close()
