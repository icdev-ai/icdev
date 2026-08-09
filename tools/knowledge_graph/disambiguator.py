#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Knowledge Graph Entity Disambiguator — deduplication, merging, aliasing.

Detects and resolves entity ambiguity in the ICDEV™ Knowledge Graph.
Homonyms like "AC-2" (NIST control vs abbreviation) or "Python"
(language vs project name) are identified via label matching, normalized
comparison, and embedding cosine similarity.  All operations are
deterministic — no LLM required.

Usage:
    python tools/knowledge_graph/disambiguator.py --find-duplicates --json
    python tools/knowledge_graph/disambiguator.py --find-duplicates --graph-id <id> --threshold 0.9 --json
    python tools/knowledge_graph/disambiguator.py --merge --source <id> --target <id> --json
    python tools/knowledge_graph/disambiguator.py --add-alias --node-id <id> --alias "alternate name" --json
    python tools/knowledge_graph/disambiguator.py --resolve "AC-2" --context "compliance audit NIST" --json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.knowledge_graph.disambiguator")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _get_db(db_path: Optional[str] = None):
    """Get a connection to icdev.db."""
    from tools.db.storage import get_connection

    conn = get_connection(db_path=db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _now() -> str:
    """ISO-8601 timestamp in UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gen_id(prefix: str = "kg") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Embedding cosine similarity (same struct.unpack pattern as graph_rag.py)
# ---------------------------------------------------------------------------


def _cosine_similarity(a: bytes, b: bytes) -> float:
    """Compute cosine similarity between two embedding BLOBs.

    Embeddings are stored as packed float32 arrays
    (``struct.pack(f'{len(vec)}f', *vec)``).

    Args:
        a: First embedding BLOB.
        b: Second embedding BLOB.

    Returns:
        Cosine similarity in [-1, 1], or 0.0 on error.
    """
    try:
        dim_a = len(a) // 4
        dim_b = len(b) // 4
        if dim_a != dim_b or dim_a == 0:
            return 0.0
        vec_a = struct.unpack(f"{dim_a}f", a)
        vec_b = struct.unpack(f"{dim_b}f", b)
        dot = sum(x * y for x, y in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(x * x for x in vec_a))
        norm_b = math.sqrt(sum(x * x for x in vec_b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)
    except (struct.error, TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Label normalization
# ---------------------------------------------------------------------------


def _normalize_label(label: str) -> str:
    """Normalize a label for comparison.

    Lowercases, strips whitespace, removes hyphens and underscores,
    collapses multiple spaces.
    """
    result = label.lower().strip()
    result = result.replace("-", " ").replace("_", " ")
    result = re.sub(r"\s+", " ", result).strip()
    return result


# ---------------------------------------------------------------------------
# find_duplicates
# ---------------------------------------------------------------------------


def find_duplicates(
    graph_id: Optional[str] = None,
    threshold: float = 0.85,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Find potential duplicate entities across the knowledge graph.

    Uses multiple signals:
        a. Exact label match (different entity_type) -- definite duplicates
        b. Normalized label match (case-insensitive, strip whitespace/hyphens)
        c. Embedding cosine similarity above threshold (if embeddings populated)

    Args:
        graph_id: Optional graph filter.  None searches all graphs.
        threshold: Minimum cosine similarity to flag as duplicate (default 0.85).
        db_path: Optional explicit DB path (for testing).

    Returns:
        Dict with status, duplicate_groups list, and summary counts.
    """
    conn = _get_db(db_path)
    try:
        # Fetch nodes
        if graph_id:
            rows = conn.execute(
                "SELECT id, graph_id, label, entity_type, properties, embedding FROM kg_nodes WHERE graph_id = %s",
                (graph_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, graph_id, label, entity_type, properties, embedding FROM kg_nodes"
            ).fetchall()

        nodes = [dict(r) for r in rows]

        if len(nodes) < 2:
            return {
                "status": "ok",
                "duplicate_groups": [],
                "total_groups": 0,
                "total_nodes_scanned": len(nodes),
            }

        # --- Signal (a): Exact label match, different entity_type ---
        exact_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for node in nodes:
            exact_groups[node["label"]].append(node)

        # --- Signal (b): Normalized label match ---
        norm_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for node in nodes:
            norm_key = _normalize_label(node["label"])
            norm_groups[norm_key].append(node)

        # Collect all duplicate groups
        duplicate_groups: List[Dict[str, Any]] = []
        seen_pairs: set = set()

        # (a) Exact label, different type
        for label, group in exact_groups.items():
            if len(group) < 2:
                continue
            # Check if there are different entity_types
            types = {n["entity_type"] for n in group}
            if len(types) > 1:
                pair_key = tuple(sorted(n["id"] for n in group))
                if pair_key not in seen_pairs:
                    seen_pairs.add(pair_key)
                    duplicate_groups.append(
                        {
                            "signal": "exact_label_different_type",
                            "similarity": 1.0,
                            "suggested_action": "review",
                            "entities": [
                                {
                                    "id": n["id"],
                                    "graph_id": n["graph_id"],
                                    "label": n["label"],
                                    "entity_type": n["entity_type"],
                                }
                                for n in group
                            ],
                        }
                    )

        # Also flag exact label + same type across different graphs
        for label, group in exact_groups.items():
            if len(group) < 2:
                continue
            graph_ids_in_group = {n["graph_id"] for n in group}
            types = {n["entity_type"] for n in group}
            if len(graph_ids_in_group) > 1 and len(types) == 1:
                pair_key = tuple(sorted(n["id"] for n in group))
                if pair_key not in seen_pairs:
                    seen_pairs.add(pair_key)
                    duplicate_groups.append(
                        {
                            "signal": "exact_label_cross_graph",
                            "similarity": 1.0,
                            "suggested_action": "merge",
                            "entities": [
                                {
                                    "id": n["id"],
                                    "graph_id": n["graph_id"],
                                    "label": n["label"],
                                    "entity_type": n["entity_type"],
                                }
                                for n in group
                            ],
                        }
                    )

        # (b) Normalized label match (only those not already caught by exact)
        for norm_key, group in norm_groups.items():
            if len(group) < 2:
                continue
            pair_key = tuple(sorted(n["id"] for n in group))
            if pair_key in seen_pairs:
                continue
            # Check that labels actually differ (otherwise it was an exact match)
            labels = {n["label"] for n in group}
            if len(labels) > 1:
                seen_pairs.add(pair_key)
                duplicate_groups.append(
                    {
                        "signal": "normalized_label_match",
                        "similarity": 0.95,
                        "suggested_action": "merge",
                        "entities": [
                            {
                                "id": n["id"],
                                "graph_id": n["graph_id"],
                                "label": n["label"],
                                "entity_type": n["entity_type"],
                            }
                            for n in group
                        ],
                    }
                )

        # (c) Embedding cosine similarity above threshold
        # Only compare nodes that have embeddings
        embedded_nodes = [n for n in nodes if n.get("embedding") is not None]
        if len(embedded_nodes) >= 2:
            for i in range(len(embedded_nodes)):
                for j in range(i + 1, len(embedded_nodes)):
                    n_i = embedded_nodes[i]
                    n_j = embedded_nodes[j]
                    pair_key = tuple(sorted([n_i["id"], n_j["id"]]))
                    if pair_key in seen_pairs:
                        continue
                    sim = _cosine_similarity(n_i["embedding"], n_j["embedding"])
                    if sim >= threshold:
                        seen_pairs.add(pair_key)
                        # Same type = merge candidate, different type = review
                        same_type = n_i["entity_type"] == n_j["entity_type"]
                        duplicate_groups.append(
                            {
                                "signal": "embedding_similarity",
                                "similarity": round(sim, 4),
                                "suggested_action": "merge" if same_type else "review",
                                "entities": [
                                    {
                                        "id": n_i["id"],
                                        "graph_id": n_i["graph_id"],
                                        "label": n_i["label"],
                                        "entity_type": n_i["entity_type"],
                                    },
                                    {
                                        "id": n_j["id"],
                                        "graph_id": n_j["graph_id"],
                                        "label": n_j["label"],
                                        "entity_type": n_j["entity_type"],
                                    },
                                ],
                            }
                        )

        # Sort by similarity descending
        duplicate_groups.sort(key=lambda g: g["similarity"], reverse=True)

        return {
            "status": "ok",
            "duplicate_groups": duplicate_groups,
            "total_groups": len(duplicate_groups),
            "total_nodes_scanned": len(nodes),
            "threshold": threshold,
            "graph_id": graph_id,
        }

    except Exception as exc:
        logger.error("find_duplicates failed: %s", exc)
        return {"status": "error", "error": str(exc)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# merge_entities
# ---------------------------------------------------------------------------


def merge_entities(
    source_id: str,
    target_id: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Merge source entity into target entity.

    - Re-points all edges from source to target
    - Merges properties (target wins on conflicts)
    - Adds source label as alias on target
    - Deletes source node
    - Updates graph entity_count

    Args:
        source_id: Node ID to merge away (will be deleted).
        target_id: Node ID to keep (will absorb source).
        db_path: Optional explicit DB path (for testing).

    Returns:
        Dict with merge summary.
    """
    conn = _get_db(db_path)
    try:
        # Fetch both nodes
        source_row = conn.execute(
            "SELECT id, graph_id, label, entity_type, properties FROM kg_nodes WHERE id = %s",
            (source_id,),
        ).fetchone()
        target_row = conn.execute(
            "SELECT id, graph_id, label, entity_type, properties FROM kg_nodes WHERE id = %s",
            (target_id,),
        ).fetchone()

        if not source_row:
            return {"status": "error", "error": f"Source node not found: {source_id}"}
        if not target_row:
            return {"status": "error", "error": f"Target node not found: {target_id}"}

        source = dict(source_row)
        target = dict(target_row)

        # Parse properties
        try:
            source_props = (
                json.loads(source["properties"])
                if isinstance(source["properties"], str)
                else (source["properties"] or {})
            )
        except (json.JSONDecodeError, TypeError):
            source_props = {}
        try:
            target_props = (
                json.loads(target["properties"])
                if isinstance(target["properties"], str)
                else (target["properties"] or {})
            )
        except (json.JSONDecodeError, TypeError):
            target_props = {}

        # Merge properties: source first, then target overwrites (target wins)
        merged_props = {**source_props, **target_props}

        # Add source label as alias
        aliases = merged_props.get("aliases", [])
        if not isinstance(aliases, list):
            aliases = []
        if source["label"] not in aliases and source["label"] != target["label"]:
            aliases.append(source["label"])
        merged_props["aliases"] = aliases

        # Update target node properties
        conn.execute(
            "UPDATE kg_nodes SET properties = %s, centrality = centrality WHERE id = %s",
            (json.dumps(merged_props, ensure_ascii=False), target_id),
        )

        # Re-point edges: source_id -> target_id
        edges_updated = 0

        # Edges where source is the source_id
        rows = conn.execute(
            "SELECT id, source_id, target_id FROM kg_edges WHERE source_id = %s",
            (source_id,),
        ).fetchall()
        for row in rows:
            edge = dict(row)
            new_target = edge["target_id"]
            # Avoid self-loops
            if new_target == target_id:
                conn.execute("DELETE FROM kg_edges WHERE id = %s", (edge["id"],))
            else:
                conn.execute(
                    "UPDATE kg_edges SET source_id = %s WHERE id = %s",
                    (target_id, edge["id"]),
                )
            edges_updated += 1

        # Edges where source is the target_id column
        rows = conn.execute(
            "SELECT id, source_id, target_id FROM kg_edges WHERE target_id = %s",
            (source_id,),
        ).fetchall()
        for row in rows:
            edge = dict(row)
            new_source = edge["source_id"]
            # Avoid self-loops
            if new_source == target_id:
                conn.execute("DELETE FROM kg_edges WHERE id = %s", (edge["id"],))
            else:
                conn.execute(
                    "UPDATE kg_edges SET target_id = %s WHERE id = %s",
                    (target_id, edge["id"]),
                )
            edges_updated += 1

        # Delete source node
        conn.execute("DELETE FROM kg_nodes WHERE id = %s", (source_id,))

        # Update graph entity_count
        source_graph_id = source["graph_id"]
        conn.execute(
            "UPDATE kg_graphs SET entity_count = entity_count - 1, updated_at = %s WHERE id = %s",
            (_now(), source_graph_id),
        )

        conn.commit()

        return {
            "status": "ok",
            "merged": {
                "source_id": source_id,
                "source_label": source["label"],
                "target_id": target_id,
                "target_label": target["label"],
                "edges_repointed": edges_updated,
                "properties_merged": list(merged_props.keys()),
                "aliases": aliases,
            },
        }

    except Exception as exc:
        logger.error("merge_entities failed: %s", exc)
        return {"status": "error", "error": str(exc)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# add_alias
# ---------------------------------------------------------------------------


def add_alias(
    node_id: str,
    alias: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Add an alias to a node's properties (under 'aliases' key).

    Allows disambiguation without merging -- e.g. "AC-2" node gets alias
    "Access Control Policy and Procedures".

    Args:
        node_id: The node to add an alias to.
        alias: The alias string to add.
        db_path: Optional explicit DB path (for testing).

    Returns:
        Dict with updated node info.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT id, graph_id, label, entity_type, properties FROM kg_nodes WHERE id = %s",
            (node_id,),
        ).fetchone()

        if not row:
            return {"status": "error", "error": f"Node not found: {node_id}"}

        node = dict(row)

        # Parse properties
        try:
            props = (
                json.loads(node["properties"]) if isinstance(node["properties"], str) else (node["properties"] or {})
            )
        except (json.JSONDecodeError, TypeError):
            props = {}

        # Add alias
        aliases = props.get("aliases", [])
        if not isinstance(aliases, list):
            aliases = []
        if alias not in aliases:
            aliases.append(alias)
        props["aliases"] = aliases

        # Persist
        conn.execute(
            "UPDATE kg_nodes SET properties = %s WHERE id = %s",
            (json.dumps(props, ensure_ascii=False), node_id),
        )
        conn.commit()

        return {
            "status": "ok",
            "node": {
                "id": node["id"],
                "graph_id": node["graph_id"],
                "label": node["label"],
                "entity_type": node["entity_type"],
                "aliases": aliases,
                "properties": props,
            },
        }

    except Exception as exc:
        logger.error("add_alias failed: %s", exc)
        return {"status": "error", "error": str(exc)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# resolve_ambiguous
# ---------------------------------------------------------------------------


def resolve_ambiguous(
    label: str,
    context: Optional[str] = None,
    graph_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve an ambiguous label to ranked candidate nodes.

    Given an ambiguous label, finds all matching nodes by:
        1. Exact label match
        2. Normalized label match
        3. Alias match (checking properties -> aliases)

    If context is provided, scores each match by keyword overlap between
    the context and the node's properties, entity_type, and connected
    edge relationships.

    Args:
        label: The ambiguous label to resolve.
        context: Optional context string for relevance scoring.
        graph_id: Optional graph filter.
        db_path: Optional explicit DB path (for testing).

    Returns:
        Dict with ranked candidates and confidence scores.
    """
    conn = _get_db(db_path)
    try:
        norm_label = _normalize_label(label)

        # Fetch candidate nodes
        if graph_id:
            rows = conn.execute(
                "SELECT id, graph_id, label, entity_type, properties FROM kg_nodes WHERE graph_id = %s",
                (graph_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT id, graph_id, label, entity_type, properties FROM kg_nodes").fetchall()

        candidates: List[Dict[str, Any]] = []
        for row in rows:
            node = dict(row)
            node_label = node["label"]
            node_norm = _normalize_label(node_label)

            # Parse properties for alias check
            try:
                props = (
                    json.loads(node["properties"])
                    if isinstance(node["properties"], str)
                    else (node["properties"] or {})
                )
            except (json.JSONDecodeError, TypeError):
                props = {}

            aliases = props.get("aliases", [])
            if not isinstance(aliases, list):
                aliases = []
            aliases_norm = [_normalize_label(a) for a in aliases]

            match_type = None
            if node_label == label:
                match_type = "exact"
            elif node_norm == norm_label:
                match_type = "normalized"
            elif norm_label in aliases_norm or label in aliases:
                match_type = "alias"

            if match_type:
                candidates.append(
                    {
                        "id": node["id"],
                        "graph_id": node["graph_id"],
                        "label": node_label,
                        "entity_type": node["entity_type"],
                        "aliases": aliases,
                        "match_type": match_type,
                        "properties": props,
                    }
                )

        if not candidates:
            return {
                "status": "ok",
                "label": label,
                "candidates": [],
                "total_candidates": 0,
            }

        # Score candidates
        for cand in candidates:
            base_score = 0.0
            if cand["match_type"] == "exact":
                base_score = 0.5
            elif cand["match_type"] == "normalized":
                base_score = 0.4
            elif cand["match_type"] == "alias":
                base_score = 0.3

            context_score = 0.0
            if context:
                context_terms = set(context.lower().split())
                # Build searchable text from node
                searchable_parts = [
                    cand["entity_type"].lower(),
                    json.dumps(cand["properties"]).lower(),
                ]
                # Add aliases
                for alias in cand.get("aliases", []):
                    searchable_parts.append(alias.lower())

                # Fetch connected edges for additional context
                edge_rows = conn.execute(
                    "SELECT relationship, properties FROM kg_edges WHERE source_id = %s OR target_id = %s",
                    (cand["id"], cand["id"]),
                ).fetchall()
                for edge_row in edge_rows:
                    edge = dict(edge_row)
                    searchable_parts.append(edge["relationship"].lower())
                    searchable_parts.append((edge.get("properties") or "").lower())

                searchable = " ".join(searchable_parts)
                # Count keyword overlaps
                matches = sum(1 for term in context_terms if term in searchable)
                if context_terms:
                    context_score = 0.5 * (matches / len(context_terms))

            cand["confidence"] = round(base_score + context_score, 4)

        # Sort by confidence descending
        candidates.sort(key=lambda c: c["confidence"], reverse=True)

        # Clean up output -- remove full properties dict, keep aliases
        output_candidates = []
        for cand in candidates:
            output_candidates.append(
                {
                    "id": cand["id"],
                    "graph_id": cand["graph_id"],
                    "label": cand["label"],
                    "entity_type": cand["entity_type"],
                    "aliases": cand["aliases"],
                    "match_type": cand["match_type"],
                    "confidence": cand["confidence"],
                }
            )

        return {
            "status": "ok",
            "label": label,
            "context": context,
            "candidates": output_candidates,
            "total_candidates": len(output_candidates),
        }

    except Exception as exc:
        logger.error("resolve_ambiguous failed: %s", exc)
        return {"status": "error", "error": str(exc)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Knowledge Graph Entity Disambiguator — deduplication, merging, aliasing",
    )

    # Modes (mutually exclusive)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--find-duplicates",
        action="store_true",
        help="Find potential duplicate entities",
    )
    group.add_argument(
        "--merge",
        action="store_true",
        help="Merge source entity into target entity",
    )
    group.add_argument(
        "--add-alias",
        action="store_true",
        help="Add an alias to a node",
    )
    group.add_argument(
        "--resolve",
        type=str,
        default=None,
        metavar="LABEL",
        help="Resolve an ambiguous label to candidate nodes",
    )

    # Options
    parser.add_argument("--graph-id", type=str, default=None, help="Filter by graph ID")
    parser.add_argument("--threshold", type=float, default=0.85, help="Embedding similarity threshold (default: 0.85)")
    parser.add_argument("--source", type=str, default=None, help="Source node ID (for --merge)")
    parser.add_argument("--target", type=str, default=None, help="Target node ID (for --merge)")
    parser.add_argument("--node-id", type=str, default=None, help="Node ID (for --add-alias)")
    parser.add_argument("--alias", type=str, default=None, help="Alias text (for --add-alias)")
    parser.add_argument("--context", type=str, default=None, help="Context string (for --resolve)")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")

    args = parser.parse_args()

    result: Dict[str, Any]

    if args.find_duplicates:
        result = find_duplicates(
            graph_id=args.graph_id,
            threshold=args.threshold,
        )

    elif args.merge:
        if not args.source or not args.target:
            parser.error("--merge requires --source and --target")
        result = merge_entities(source_id=args.source, target_id=args.target)

    elif args.add_alias:
        if not args.node_id or not args.alias:
            parser.error("--add-alias requires --node-id and --alias")
        result = add_alias(node_id=args.node_id, alias=args.alias)

    elif args.resolve is not None:
        result = resolve_ambiguous(
            label=args.resolve,
            context=args.context,
            graph_id=args.graph_id,
        )

    else:
        parser.error("No action specified")
        return

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        status = result.get("status", "unknown")
        if status == "error":
            print(f"Error: {result.get('error', 'Unknown error')}")
            sys.exit(1)

        if args.find_duplicates:
            groups = result.get("duplicate_groups", [])
            print(f"Nodes scanned: {result.get('total_nodes_scanned', 0)}")
            print(f"Duplicate groups found: {result.get('total_groups', 0)}")
            print(f"Threshold: {result.get('threshold', 0.85)}")
            for i, grp in enumerate(groups, 1):
                print(
                    f"\n  Group {i} [{grp['signal']}] "
                    f"(similarity: {grp['similarity']}, "
                    f"action: {grp['suggested_action']}):"
                )
                for ent in grp["entities"]:
                    print(f"    - {ent['label']} [{ent['entity_type']}] (id: {ent['id']}, graph: {ent['graph_id']})")

        elif args.merge:
            merged = result.get("merged", {})
            print(f"Merged: {merged.get('source_label')} -> {merged.get('target_label')}")
            print(f"Edges repointed: {merged.get('edges_repointed', 0)}")
            print(f"Aliases: {merged.get('aliases', [])}")

        elif args.add_alias:
            node = result.get("node", {})
            print(f"Node: {node.get('label')} [{node.get('entity_type')}]")
            print(f"Aliases: {node.get('aliases', [])}")

        elif args.resolve is not None:
            candidates = result.get("candidates", [])
            print(f"Label: {result.get('label')}")
            print(f"Context: {result.get('context', 'none')}")
            print(f"Candidates: {result.get('total_candidates', 0)}")
            for i, cand in enumerate(candidates, 1):
                print(
                    f"\n  {i}. {cand['label']} [{cand['entity_type']}] "
                    f"(confidence: {cand['confidence']}, "
                    f"match: {cand['match_type']})"
                )
                if cand.get("aliases"):
                    print(f"     aliases: {cand['aliases']}")


if __name__ == "__main__":
    main()
