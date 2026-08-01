# CUI // SP-CTI
"""ICDEV™ Data Design Canvas — Column-Level Lineage Engine.

Pure functions for building, traversing, and validating column-level
data lineage DAGs from ``dd_lineage`` table records.

No Flask dependency. No LLM dependency. All logic is deterministic.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone

from tools.data_canvas.constants import COLUMN_LINEAGE_TYPES, EDGE_TYPE_COLUMN_LINEAGE

# ── Internal helpers ──────────────────────────────────────────────────────────

_REQUIRED_EDGE_FIELDS = ("source_node_id", "target_node_id")


def _node_key(entity_id: str, column_name: str) -> str:
    """Stable key for a (entity, column) pair in the DAG."""
    return f"{entity_id}::{column_name}"


def _build_forward_index(lineage_records: list[dict]) -> dict[str, list[dict]]:
    """Forward adjacency: key → list of outgoing edge records."""
    idx: dict[str, list[dict]] = defaultdict(list)
    for rec in lineage_records:
        key = _node_key(rec.get("source_node_id", ""), rec.get("column_name", ""))
        idx[key].append(rec)
    return idx


def _build_reverse_index(lineage_records: list[dict]) -> dict[str, list[dict]]:
    """Reverse adjacency: key → list of incoming edge records (for upstream traversal)."""
    idx: dict[str, list[dict]] = defaultdict(list)
    for rec in lineage_records:
        key = _node_key(rec.get("target_node_id", ""), rec.get("column_name", ""))
        idx[key].append(rec)
    return idx


# ── Public API ────────────────────────────────────────────────────────────────


def validate_lineage_edge(edge_data: dict) -> dict:
    """Validate a single lineage edge before insertion.

    Args:
        edge_data: Dict with fields matching the ``dd_lineage`` schema.

    Returns:
        Dict with ``valid`` (bool), ``errors`` (list[str]).
    """
    errors: list[str] = []

    for field in _REQUIRED_EDGE_FIELDS:
        if not edge_data.get(field):
            errors.append(f"Missing required field: {field}")

    lineage_type = edge_data.get("lineage_type", EDGE_TYPE_COLUMN_LINEAGE)
    if lineage_type not in COLUMN_LINEAGE_TYPES:
        errors.append(
            f"Invalid lineage_type '{lineage_type}'. "
            f"Must be one of: {', '.join(COLUMN_LINEAGE_TYPES)}"
        )

    src = edge_data.get("source_node_id", "")
    tgt = edge_data.get("target_node_id", "")
    if src and tgt and src == tgt:
        errors.append("source_node_id and target_node_id must differ (no self-loops)")

    return {"valid": len(errors) == 0, "errors": errors}


def build_column_lineage_dag(lineage_records: list[dict]) -> dict:
    """Build a directed acyclic graph representation from lineage records.

    Args:
        lineage_records: Rows from the ``dd_lineage`` table (list of dicts).

    Returns:
        Dict with:
            ``nodes``  — unique (entity, column) pairs as node dicts
            ``edges``  — directed edge dicts (source_key → target_key)
            ``node_count``  — int
            ``edge_count``  — int
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    for rec in lineage_records:
        src_entity = rec.get("source_node_id", "")
        tgt_entity = rec.get("target_node_id", "")
        col_name = rec.get("column_name", "")
        lineage_type = rec.get("lineage_type", EDGE_TYPE_COLUMN_LINEAGE)
        transform_desc = rec.get("transform_desc", "")
        classification = rec.get("classification", "CUI")

        src_key = _node_key(src_entity, col_name)
        tgt_key = _node_key(tgt_entity, col_name)

        if src_key not in nodes:
            nodes[src_key] = {
                "key": src_key,
                "entity_id": src_entity,
                "column_name": col_name,
                "classification": classification,
            }
        if tgt_key not in nodes:
            nodes[tgt_key] = {
                "key": tgt_key,
                "entity_id": tgt_entity,
                "column_name": col_name,
                "classification": classification,
            }

        edges.append(
            {
                "id": rec.get("id", ""),
                "source": src_key,
                "target": tgt_key,
                "lineage_type": lineage_type,
                "transform_desc": transform_desc,
                "classification": classification,
            }
        )

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


def compute_downstream_impact(
    source_entity_id: str,
    column_name: str,
    lineage_records: list[dict],
    max_depth: int = 20,
) -> dict:
    """BFS traversal from a source (entity, column) to find all downstream dependents.

    Args:
        source_entity_id: Entity node ID that owns the source column.
        column_name: Column name to trace downstream.
        lineage_records: Rows from ``dd_lineage`` for the design.
        max_depth: Maximum hops to follow.

    Returns:
        Dict with:
            ``source_entity``    — echoed source entity ID
            ``column_name``      — echoed column name
            ``impacted_nodes``   — list of (entity_id, column) pairs reached
            ``hop_count``        — max depth reached
            ``paths``            — list of traversal path dicts
            ``total_impacted``   — int count
    """
    forward_idx = _build_forward_index(lineage_records)
    start_key = _node_key(source_entity_id, column_name)

    visited: set[str] = {start_key}
    queue: deque[tuple[str, int, list[str]]] = deque()
    queue.append((start_key, 0, [start_key]))

    impacted: list[dict] = []
    paths: list[dict] = []
    max_hop = 0

    while queue:
        current_key, depth, path = queue.popleft()
        if depth >= max_depth:
            continue

        for rec in forward_idx.get(current_key, []):
            tgt_entity = rec.get("target_node_id", "")
            tgt_col = rec.get("column_name", "")
            tgt_key = _node_key(tgt_entity, tgt_col)

            if tgt_key in visited:
                continue
            visited.add(tgt_key)

            new_path = path + [tgt_key]
            impacted.append(
                {
                    "entity_id": tgt_entity,
                    "column_name": tgt_col,
                    "depth": depth + 1,
                    "lineage_type": rec.get("lineage_type", EDGE_TYPE_COLUMN_LINEAGE),
                    "transform_desc": rec.get("transform_desc", ""),
                }
            )
            paths.append({"path": new_path, "depth": depth + 1})
            max_hop = max(max_hop, depth + 1)
            queue.append((tgt_key, depth + 1, new_path))

    return {
        "source_entity": source_entity_id,
        "column_name": column_name,
        "impacted_nodes": impacted,
        "hop_count": max_hop,
        "paths": paths,
        "total_impacted": len(impacted),
    }


def compute_upstream_provenance(
    target_entity_id: str,
    column_name: str,
    lineage_records: list[dict],
    max_depth: int = 20,
) -> dict:
    """BFS traversal backwards from a target (entity, column) to find all upstream sources.

    Args:
        target_entity_id: Entity node ID that owns the target column.
        column_name: Column name to trace upstream.
        lineage_records: Rows from ``dd_lineage`` for the design.
        max_depth: Maximum hops to follow.

    Returns:
        Dict with:
            ``target_entity``    — echoed target entity ID
            ``column_name``      — echoed column name
            ``provenance_nodes`` — list of upstream (entity_id, column) pairs
            ``hop_count``        — max depth reached
            ``paths``            — list of traversal path dicts
            ``total_provenance`` — int count
    """
    reverse_idx = _build_reverse_index(lineage_records)
    start_key = _node_key(target_entity_id, column_name)

    visited: set[str] = {start_key}
    queue: deque[tuple[str, int, list[str]]] = deque()
    queue.append((start_key, 0, [start_key]))

    provenance: list[dict] = []
    paths: list[dict] = []
    max_hop = 0

    while queue:
        current_key, depth, path = queue.popleft()
        if depth >= max_depth:
            continue

        for rec in reverse_idx.get(current_key, []):
            src_entity = rec.get("source_node_id", "")
            src_col = rec.get("column_name", "")
            src_key = _node_key(src_entity, src_col)

            if src_key in visited:
                continue
            visited.add(src_key)

            new_path = [src_key] + path
            provenance.append(
                {
                    "entity_id": src_entity,
                    "column_name": src_col,
                    "depth": depth + 1,
                    "lineage_type": rec.get("lineage_type", EDGE_TYPE_COLUMN_LINEAGE),
                    "transform_desc": rec.get("transform_desc", ""),
                }
            )
            paths.append({"path": new_path, "depth": depth + 1})
            max_hop = max(max_hop, depth + 1)
            queue.append((src_key, depth + 1, new_path))

    return {
        "target_entity": target_entity_id,
        "column_name": column_name,
        "provenance_nodes": provenance,
        "hop_count": max_hop,
        "paths": paths,
        "total_provenance": len(provenance),
    }


def summarize_lineage(lineage_records: list[dict], graph_data: dict) -> dict:
    """Compute summary metrics for a design's column-level lineage.

    Args:
        lineage_records: Rows from ``dd_lineage`` for the design.
        graph_data: Dict with "nodes", "edges", and "boundaries".

    Returns:
        Dict with total_edges, unique_columns, unique_entities,
        lineage_type_breakdown, has_cross_boundary_lineage, and orphan_nodes.
    """
    total_edges = len(lineage_records)

    unique_columns: set[str] = set()
    src_entities: set[str] = set()
    tgt_entities: set[str] = set()
    type_counts: dict[str, int] = defaultdict(int)
    has_cross_boundary = False

    # Build entity→classification mapping from graph boundaries
    node_zone: dict[str, str] = {}
    for bnd in graph_data.get("boundaries", []):
        if bnd.get("type") == "bnd-classification":
            zone_label = bnd.get("label", "UNCLASSIFIED")
            for nid in bnd.get("contained_nodes", []):
                node_zone[nid] = zone_label

    for rec in lineage_records:
        src = rec.get("source_node_id", "")
        tgt = rec.get("target_node_id", "")
        col = rec.get("column_name", "")
        lt = rec.get("lineage_type", EDGE_TYPE_COLUMN_LINEAGE)

        if col:
            unique_columns.add(col)
        if src:
            src_entities.add(src)
        if tgt:
            tgt_entities.add(tgt)

        type_counts[lt] += 1

        # Cross-boundary: source and target in different classification zones
        src_zone = node_zone.get(src)
        tgt_zone = node_zone.get(tgt)
        if src_zone and tgt_zone and src_zone != tgt_zone:
            has_cross_boundary = True

    # Orphan check: entities in graph but not in any lineage record
    graph_entity_ids = {
        n["id"]
        for n in graph_data.get("nodes", [])
        if n.get("type", "").startswith("ent-")
    }
    lineage_entity_ids = src_entities | tgt_entities
    orphan_nodes = sorted(graph_entity_ids - lineage_entity_ids)

    return {
        "total_edges": total_edges,
        "unique_columns": len(unique_columns),
        "unique_entities": len(src_entities | tgt_entities),
        "source_entities": len(src_entities),
        "target_entities": len(tgt_entities),
        "lineage_type_breakdown": dict(type_counts),
        "has_cross_boundary_lineage": has_cross_boundary,
        "orphan_entity_count": len(orphan_nodes),
        "orphan_entities": orphan_nodes,
    }


def generate_contract_assertions(
    lineage_records: list[dict],
    graph_data: dict,
) -> list[dict]:
    """Generate data contract assertions from lineage and graph data.

    These assertions check structural integrity and classification consistency
    across the data lineage chain. They do NOT fire LLM calls — all checks
    are deterministic.

    Args:
        lineage_records: Rows from ``dd_lineage`` for the design.
        graph_data: Dict with "nodes", "edges", and "boundaries".

    Returns:
        List of assertion dicts, each with:
            ``assertion_id``  — unique identifier
            ``type``          — assertion category
            ``severity``      — CAT1 / CAT2 / CAT3
            ``source_entity`` — entity ID that triggered the assertion
            ``column_name``   — involved column (may be empty)
            ``description``   — human-readable finding
            ``passed``        — bool
    """
    assertions: list[dict] = []
    ts = datetime.now(timezone.utc).isoformat()

    # Build helper indexes
    node_map: dict[str, dict] = {n["id"]: n for n in graph_data.get("nodes", [])}
    node_zone: dict[str, str] = {}
    for bnd in graph_data.get("boundaries", []):
        if bnd.get("type") == "bnd-classification":
            zone_label = bnd.get("label", "UNCLASSIFIED")
            for nid in bnd.get("contained_nodes", []):
                node_zone[nid] = zone_label

    # -- A1: No self-loop lineage edges ----------------------------------------
    for i, rec in enumerate(lineage_records):
        src = rec.get("source_node_id", "")
        tgt = rec.get("target_node_id", "")
        passed = src != tgt
        if not passed:
            assertions.append(
                {
                    "assertion_id": f"A1-{i}",
                    "type": "self_loop",
                    "severity": "CAT2",
                    "source_entity": src,
                    "column_name": rec.get("column_name", ""),
                    "description": f"Self-loop lineage detected on entity '{src}' — source and target are the same node.",
                    "passed": False,
                    "checked_at": ts,
                }
            )

    # -- A2: Valid lineage_type on every record --------------------------------
    for i, rec in enumerate(lineage_records):
        lt = rec.get("lineage_type", EDGE_TYPE_COLUMN_LINEAGE)
        passed = lt in COLUMN_LINEAGE_TYPES
        if not passed:
            assertions.append(
                {
                    "assertion_id": f"A2-{i}",
                    "type": "invalid_lineage_type",
                    "severity": "CAT3",
                    "source_entity": rec.get("source_node_id", ""),
                    "column_name": rec.get("column_name", ""),
                    "description": f"Unknown lineage_type '{lt}' — expected one of: {', '.join(COLUMN_LINEAGE_TYPES)}.",
                    "passed": False,
                    "checked_at": ts,
                }
            )

    # -- A3: Classification escalation across lineage edges --------------------
    # CUI/SECRET data must not flow into UNCLASSIFIED zones
    _ZONE_RANK: dict[str, int] = {
        "UNCLASSIFIED": 0,
        "CUI": 1,
        "SECRET": 2,
        "TOP SECRET": 3,
    }

    def _zone_rank(label: str | None) -> int:
        if not label:
            return 0
        ul = label.upper()
        for k, v in _ZONE_RANK.items():
            if k in ul:
                return v
        return 0

    for i, rec in enumerate(lineage_records):
        src = rec.get("source_node_id", "")
        tgt = rec.get("target_node_id", "")
        src_zone = node_zone.get(src)
        tgt_zone = node_zone.get(tgt)
        if src_zone and tgt_zone and _zone_rank(src_zone) > _zone_rank(tgt_zone):
            assertions.append(
                {
                    "assertion_id": f"A3-{i}",
                    "type": "classification_downgrade",
                    "severity": "CAT1",
                    "source_entity": src,
                    "column_name": rec.get("column_name", ""),
                    "description": (
                        f"Classification downgrade: column '{rec.get('column_name', '')}' "
                        f"flows from '{src_zone}' zone (entity '{src}') "
                        f"into '{tgt_zone}' zone (entity '{tgt}'). "
                        f"This violates mandatory access control."
                    ),
                    "passed": False,
                    "checked_at": ts,
                }
            )

    # -- A4: Lineage records reference existing nodes in the graph ------------
    graph_node_ids = set(node_map.keys())
    if graph_node_ids:  # Only assert when graph data is available
        for i, rec in enumerate(lineage_records):
            src = rec.get("source_node_id", "")
            tgt = rec.get("target_node_id", "")
            for role, nid in (("source", src), ("target", tgt)):
                if nid and nid not in graph_node_ids:
                    assertions.append(
                        {
                            "assertion_id": f"A4-{i}-{role}",
                            "type": "dangling_reference",
                            "severity": "CAT2",
                            "source_entity": nid,
                            "column_name": rec.get("column_name", ""),
                            "description": (
                                f"Lineage edge references {role} node '{nid}' "
                                f"which does not exist in the current graph. "
                                f"Stale lineage after node deletion?"
                            ),
                            "passed": False,
                            "checked_at": ts,
                        }
                    )

    # -- A5: Column name populated on column-level lineage edges --------------
    for i, rec in enumerate(lineage_records):
        lt = rec.get("lineage_type", EDGE_TYPE_COLUMN_LINEAGE)
        col = rec.get("column_name", "").strip()
        if lt == EDGE_TYPE_COLUMN_LINEAGE and not col:
            assertions.append(
                {
                    "assertion_id": f"A5-{i}",
                    "type": "missing_column_name",
                    "severity": "CAT3",
                    "source_entity": rec.get("source_node_id", ""),
                    "column_name": "",
                    "description": (
                        f"Lineage record with type '{lt}' is missing column_name. "
                        f"Column-level lineage must specify a column name."
                    ),
                    "passed": False,
                    "checked_at": ts,
                }
            )

    return assertions


# ── External IQE provenance recording ────────────────────────────────────────

_EXT_PROVENANCE_DESIGN_ID = "ext-iqe-provenance"


def _ensure_ext_provenance_design(conn) -> None:
    existing = conn.execute(
        "SELECT id FROM data_designs WHERE id = ?", (_EXT_PROVENANCE_DESIGN_ID,)
    ).fetchone()
    if not existing:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO data_designs"
            " (id, name, description, classification, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (
                _EXT_PROVENANCE_DESIGN_ID,
                "External IQE Query Provenance",
                "System-managed design for tracking IQE external source lineage edges.",
                "CUI // SP-CTI",
                now,
                now,
            ),
        )
        conn.commit()


def record_external_fetch(
    connector_name: str,
    table: str,
    row_count: int,
    classification: str = "CUI // SP-CTI",
) -> "dict | None":
    """Append a dd_lineage edge recording that IQE fetched rows from an external source."""
    import uuid as _uuid

    try:
        from tools.data_canvas.db.init_db import get_connection  # noqa: PLC0415

        conn = get_connection()
        _ensure_ext_provenance_design(conn)

        edge_id = f"ext-lin-{_uuid.uuid4().hex[:10]}"
        now = datetime.now(timezone.utc).isoformat()
        source_node = f"ext.{connector_name}.{table}"
        target_node = "iqe.query.result"

        conn.execute(
            "INSERT INTO dd_lineage"
            " (id, design_id, source_node_id, target_node_id,"
            "  lineage_type, column_name, transform_desc, classification, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                edge_id,
                _EXT_PROVENANCE_DESIGN_ID,
                source_node,
                target_node,
                "col-passthrough",
                "*",
                f"IQE external fetch — {row_count} rows",
                classification,
                now,
            ),
        )
        conn.commit()

        return {
            "id": edge_id,
            "design_id": _EXT_PROVENANCE_DESIGN_ID,
            "source_node_id": source_node,
            "target_node_id": target_node,
            "lineage_type": "col-passthrough",
            "classification": classification,
            "row_count": row_count,
            "created_at": now,
        }
    except Exception as exc:  # noqa: BLE001
        from tools.logging.icdev_logger import get_logger

        get_logger("data_canvas.lineage").warning(
            "record_external_fetch failed for ext.%s.%s: %s", connector_name, table, exc
        )
        return None
