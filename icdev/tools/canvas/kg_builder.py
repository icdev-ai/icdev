
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""Cross-canvas Knowledge Graph builder.

Rebuilds a normalized KG from a canvas design's graph_json and persists
nodes, edges, and a build-log entry into icdev.db.  Called by every canvas
blueprint on save inside a try/except:

    rebuild_canvas_kg("idc", design_id)
"""

import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = get_logger("icdev.canvas.kg_builder")

# ---------------------------------------------------------------------------
# Canvas -> (DB filename, designs table) mapping
# ---------------------------------------------------------------------------
_CANVAS_DB_MAP: dict[str, tuple[str, str]] = {
    "idc": ("infra_canvas.db", "infra_designs"),
    "ndc": ("network_canvas.db", "topologies"),
    "sdc": ("security_canvas.db", "security_designs"),
    "bdc": ("boundary_canvas.db", "boundary_designs"),
    "pdc": ("pipeline_canvas.db", "pipelines"),
    "odc": ("observability_canvas.db", "observability_designs"),
    "ddc": ("data_canvas.db", "data_designs"),
    "qdc": ("qdc_canvas.db", "qdc_designs"),
    "mdc": ("migration_canvas.db", "migration_designs"),
}

# Data directory (relative to project root)
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

# ---------------------------------------------------------------------------
# DDL for KG tables (created if missing)
# ---------------------------------------------------------------------------
_CREATE_NODES_TABLE = """\
CREATE TABLE IF NOT EXISTS canvas_kg_nodes (
    id           TEXT PRIMARY KEY,
    canvas       TEXT NOT NULL,
    design_id    TEXT NOT NULL,
    node_id      TEXT NOT NULL,
    node_type    TEXT,
    label        TEXT,
    ontology_id  TEXT,
    metadata_json TEXT,
    updated_at   TEXT
)
"""

_CREATE_EDGES_TABLE = """\
CREATE TABLE IF NOT EXISTS canvas_kg_edges (
    id           TEXT PRIMARY KEY,
    canvas       TEXT NOT NULL,
    design_id    TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    edge_type    TEXT,
    confidence   REAL DEFAULT 1.0,
    metadata_json TEXT,
    updated_at   TEXT
)
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_kg_tables(conn: sqlite3.Connection) -> None:
    """Create canvas_kg_nodes and canvas_kg_edges if they don't exist."""
    conn.execute(_CREATE_NODES_TABLE)
    conn.execute(_CREATE_EDGES_TABLE)
    conn.commit()


def _read_graph_json(canvas_key: str, design_id: str) -> dict:
    """Read graph_json from the canvas's own SQLite DB.

    Returns the parsed dict or an empty dict on failure.
    """
    if canvas_key not in _CANVAS_DB_MAP:
        raise ValueError(f"Unknown canvas key: {canvas_key!r}")

    db_file, table = _CANVAS_DB_MAP[canvas_key]
    db_path = _DATA_DIR / db_file

    if not db_path.exists():
        raise FileNotFoundError(f"Canvas DB not found: {db_path}")

    from tools.db.storage import get_connection
    conn = get_connection(str(db_path))
    try:
        row = conn.execute(
            f"SELECT graph_json FROM [{table}] WHERE id = ?",  # noqa: S608  # nosec B608
            (design_id,),
        ).fetchone()
        if row is None:
            raise LookupError(
                f"Design {design_id!r} not found in {table}"
            )
        raw = row["graph_json"]
        if raw is None:
            return {"nodes": [], "edges": []}
        return json.loads(raw) if isinstance(raw, str) else raw
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def rebuild_canvas_kg(canvas_key: str, design_id: str) -> dict:
    """Rebuild the Knowledge Graph for a single canvas design.

    Parameters
    ----------
    canvas_key : str
        Short canvas identifier (e.g. ``"idc"``, ``"sdc"``).
    design_id : str
        UUID / primary-key of the design row in the canvas DB.

    Returns
    -------
    dict
        Status payload with node/edge counts and duration.
    """
    t0 = time.perf_counter()
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        graph = _read_graph_json(canvas_key, design_id)
    except Exception as exc:
        logger.warning("kg_builder: failed to read graph_json — %s", exc)
        return {
            "status": "error",
            "canvas": canvas_key,
            "design_id": design_id,
            "error": str(exc),
        }

    nodes_raw = graph.get("nodes") or []
    edges_raw = graph.get("edges") or []

    # -- Persist into icdev.db -------------------------------------------
    try:
        # Import here to avoid circular imports at module level
        from tools.db.storage import get_connection  # type: ignore[import-untyped]

        icdev_conn = get_connection()
        # get_connection() returns a StorageConnection wrapper; grab the
        # underlying sqlite3 connection when available.
        raw_conn: sqlite3.Connection = getattr(icdev_conn, "conn", icdev_conn)

        _ensure_kg_tables(raw_conn)

        # Delete stale KG data for this (canvas, design_id)
        raw_conn.execute(
            "DELETE FROM canvas_kg_nodes WHERE canvas = ? AND design_id = ?",
            (canvas_key, design_id),
        )
        raw_conn.execute(
            "DELETE FROM canvas_kg_edges WHERE canvas = ? AND design_id = ?",
            (canvas_key, design_id),
        )

        # Lazy-import ontology_bridge to avoid circular deps at module load
        try:
            from tools.canvas.ontology_bridge import get_ontology_id as _get_onto_id
        except Exception:
            _get_onto_id = None  # type: ignore[assignment]

        # Insert nodes
        for node in nodes_raw:
            node_id = node.get("id", str(uuid.uuid4()))
            node_type = node.get("type", node.get("node_type", ""))
            label = node.get("label", node.get("name", ""))
            metadata = {
                k: v
                for k, v in node.items()
                if k not in ("id", "type", "node_type", "label", "name")
            }

            # reasoning_step nodes carry CoT/CoD trace fields — promote to metadata
            if node_type == "reasoning_step":
                metadata.setdefault("step_name", node.get("step_name", ""))
                metadata.setdefault("model_id", node.get("model_id", ""))
                metadata.setdefault("chain_mode", node.get("chain_mode", ""))
                metadata.setdefault("trace_id", node.get("trace_id", ""))
                metadata.setdefault("round_num", node.get("round_num", 0))

            ontology_id = None
            if _get_onto_id and node_type:
                try:
                    ontology_id = _get_onto_id(canvas_key, node_type)
                except Exception:
                    pass
            raw_conn.execute(
                "INSERT INTO canvas_kg_nodes "
                "(id, canvas, design_id, node_id, node_type, label, ontology_id, metadata_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    canvas_key,
                    design_id,
                    node_id,
                    node_type,
                    label,
                    ontology_id,
                    json.dumps(metadata, default=str),
                    now_iso,
                ),
            )

        # Insert edges
        for edge in edges_raw:
            source = edge.get("source", edge.get("source_id", ""))
            target = edge.get("target", edge.get("target_id", ""))
            edge_type = edge.get("type", edge.get("edge_type", ""))
            metadata = {
                k: v
                for k, v in edge.items()
                if k
                not in ("source", "target", "source_id", "target_id", "type", "edge_type")
            }
            raw_conn.execute(
                "INSERT INTO canvas_kg_edges "
                "(id, canvas, design_id, source_id, target_id, edge_type, metadata_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    canvas_key,
                    design_id,
                    source,
                    target,
                    edge_type,
                    json.dumps(metadata, default=str),
                    now_iso,
                ),
            )

        # Build-log entry
        duration_ms = (time.perf_counter() - t0) * 1000
        build_id = str(uuid.uuid4())
        raw_conn.execute(
            "INSERT INTO canvas_kg_build_log "
            "(build_id, canvas, design_id, nodes_upserted, edges_upserted, duration_ms, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                build_id,
                canvas_key,
                design_id,
                len(nodes_raw),
                len(edges_raw),
                round(duration_ms, 2),
                now_iso,
            ),
        )

        raw_conn.commit()

    except Exception as exc:
        logger.warning("kg_builder: failed to write KG to icdev.db — %s", exc)
        return {
            "status": "error",
            "canvas": canvas_key,
            "design_id": design_id,
            "error": str(exc),
        }

    duration_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "kg_builder: %s/%s — %d nodes, %d edges in %.1f ms",
        canvas_key,
        design_id,
        len(nodes_raw),
        len(edges_raw),
        duration_ms,
    )

    return {
        "status": "ok",
        "canvas": canvas_key,
        "design_id": design_id,
        "nodes_upserted": len(nodes_raw),
        "edges_upserted": len(edges_raw),
        "duration_ms": round(duration_ms, 2),
    }


def upsert_from_dic(
    doc_id: str,
    entities: list[dict],
    relationships: list[dict],
    canvas: str = "dic",
) -> dict:
    """Write DIC-extracted entities and relationships into canvas_kg_nodes/edges.

    Entities: [{id, label, type, metadata (opt)}]
    Relationships: [{source, target, type}]
    canvas: defaults to 'dic' — appears in the Ontology canvas as its own source.
    """
    if not doc_id or (not entities and not relationships):
        return {"status": "skipped", "canvas": canvas, "design_id": doc_id}

    t0 = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        from icdev.tools.db.storage import get_connection

        icdev_conn = get_connection()
        raw_conn = getattr(icdev_conn, "conn", icdev_conn)
        _ensure_kg_tables(raw_conn)

        raw_conn.execute(
            "DELETE FROM canvas_kg_nodes WHERE canvas = ? AND design_id = ?",
            (canvas, doc_id),
        )
        raw_conn.execute(
            "DELETE FROM canvas_kg_edges WHERE canvas = ? AND design_id = ?",
            (canvas, doc_id),
        )

        node_row_ids: dict[str, str] = {}
        for ent in entities:
            ent_id = str(ent.get("id") or str(uuid.uuid4()))
            row_id = str(uuid.uuid4())
            node_row_ids[ent_id] = row_id
            raw_conn.execute(
                "INSERT INTO canvas_kg_nodes "
                "(id, canvas, design_id, node_id, node_type, label, ontology_id, metadata_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row_id,
                    canvas,
                    doc_id,
                    ent_id,
                    ent.get("type", "concept"),
                    ent.get("label", ent_id),
                    None,
                    json.dumps(ent.get("metadata") or {}, default=str),
                    now_iso,
                ),
            )

        for rel in relationships:
            raw_conn.execute(
                "INSERT INTO canvas_kg_edges "
                "(id, canvas, design_id, source_id, target_id, edge_type, confidence, metadata_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    canvas,
                    doc_id,
                    str(rel.get("source", "")),
                    str(rel.get("target", "")),
                    rel.get("type", "related_to"),
                    float(rel.get("confidence", 1.0)),
                    json.dumps(rel.get("metadata") or {}, default=str),
                    now_iso,
                ),
            )

        raw_conn.commit()
        icdev_conn.close()
    except Exception as exc:
        logger.warning("kg_builder.upsert_from_dic: %s", exc)
        return {"status": "error", "canvas": canvas, "design_id": doc_id, "error": str(exc)}

    duration_ms = (time.time() - t0) * 1000
    logger.info(
        "kg_builder.upsert_from_dic: %s/%s — %d nodes, %d edges in %.1f ms",
        canvas, doc_id, len(entities), len(relationships), duration_ms,
    )
    return {
        "status": "ok",
        "canvas": canvas,
        "design_id": doc_id,
        "nodes_upserted": len(entities),
        "edges_upserted": len(relationships),
        "duration_ms": round(duration_ms, 2),
    }
