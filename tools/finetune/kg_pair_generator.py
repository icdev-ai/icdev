#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""KG-to-FT Pair Generator — structured training pairs from knowledge graph (D-KARL-6).

Uses three strategies to generate high-quality Q&A training pairs:
    1. Entity-relationship pairs: edges → "How does A implement B?"
    2. Community cluster pairs: BFS components → cluster summary Q&A
    3. Compliance crosswalk pairs: control↔standard edges → "Which controls satisfy X?"

All pair generation is template-based (deterministic, air-gap safe).
Optional scanner-tier LLM refinement via qwen3.5 (zero Claude tokens).

Usage:
    python tools/finetune/kg_pair_generator.py --graph-id <id> --dataset-id <id> --json
    python tools/finetune/kg_pair_generator.py --graph-id <id> --strategy entity_relationship --json
    python tools/finetune/kg_pair_generator.py --graph-id <id> --strategy community_cluster --json
    python tools/finetune/kg_pair_generator.py --graph-id <id> --strategy compliance_crosswalk --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.finetune.kg_pair_generator")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _load_config() -> Dict[str, Any]:
    """Load kg_ft_pipeline config from finetune_config.yaml."""
    config_path = BASE_DIR / "args" / "finetune_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("kg_ft_pipeline", {})
    except Exception:
        return {}


DEFAULT_CONFIG = {
    "enabled": True,
    "strategies": ["entity_relationship", "community_cluster", "compliance_crosswalk"],
    "questions_per_entity": 2,
    "min_community_size": 3,
    "max_pairs_per_graph": 50,
}


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def _get_db():
    from tools.db.storage import get_connection

    conn = get_connection()
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Graph loading
# ---------------------------------------------------------------------------


def _load_graph(conn: sqlite3.Connection, graph_id: str) -> Dict[str, Any]:
    """Load graph with nodes and edges from DB."""
    graph = conn.execute("SELECT * FROM kg_graphs WHERE id = %s", (graph_id,)).fetchone()
    if not graph:
        return {"error": f"Graph not found: {graph_id}"}

    nodes = conn.execute(
        "SELECT id, label, entity_type, centrality, properties FROM kg_nodes WHERE graph_id = %s",
        (graph_id,),
    ).fetchall()

    edges = conn.execute(
        "SELECT id, source_id, target_id, relationship, weight FROM kg_edges WHERE graph_id = %s",
        (graph_id,),
    ).fetchall()

    node_map = {n["id"]: dict(n) for n in nodes}
    return {
        "graph": dict(graph),
        "nodes": [dict(n) for n in nodes],
        "edges": [dict(e) for e in edges],
        "node_map": node_map,
    }


def _find_components(nodes: List[Dict], edges: List[Dict]) -> List[Set[str]]:
    """BFS-based connected component detection."""
    adj: Dict[str, Set[str]] = defaultdict(set)
    all_ids = {n["id"] for n in nodes}

    for e in edges:
        adj[e["source_id"]].add(e["target_id"])
        adj[e["target_id"]].add(e["source_id"])

    visited: Set[str] = set()
    components: List[Set[str]] = []

    for nid in all_ids:
        if nid in visited:
            continue
        component: Set[str] = set()
        queue = deque([nid])
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            component.add(current)
            for neighbor in adj.get(current, set()):
                if neighbor not in visited:
                    queue.append(neighbor)
        components.append(component)

    return components


# ---------------------------------------------------------------------------
# Strategy 1: Entity-Relationship Pairs
# ---------------------------------------------------------------------------

RELATIONSHIP_TEMPLATES = {
    "IMPLEMENTS": "How does {source} implement {target}?",
    "SATISFIES": "How does {source} satisfy the requirements of {target}?",
    "DEPENDS_ON": "What is the dependency between {source} and {target}?",
    "PROTECTS": "How does {source} protect {target}?",
    "REFERENCES": "How does {source} reference {target}?",
    "DEPLOYED_ON": "How is {source} deployed on {target}?",
    "MEMBER_OF": "What is the relationship between {source} and {target}?",
    "OPERATES": "How does {source} operate {target}?",
    "RUNS": "What role does {source} play in running {target}?",
}

ANSWER_TEMPLATES = {
    "IMPLEMENTS": "{source} ({source_type}) implements {target} ({target_type}) as part of the compliance framework. The implementation ensures that the requirements defined by {target} are met through the capabilities of {source}.",
    "SATISFIES": "{source} ({source_type}) satisfies the requirements of {target} ({target_type}) by providing the necessary controls and mechanisms to meet the specified criteria.",
    "DEPENDS_ON": "{source} ({source_type}) depends on {target} ({target_type}) for its operation. This dependency means that {target} must be available and properly configured for {source} to function correctly.",
    "PROTECTS": "{source} ({source_type}) provides protection for {target} ({target_type}) through security controls and safeguards that mitigate risks and vulnerabilities.",
    "REFERENCES": "{source} ({source_type}) references {target} ({target_type}) as an authoritative source for requirements, standards, or guidance.",
}


def _generate_entity_relationship_pairs(
    graph_data: Dict[str, Any],
    max_pairs: int = 50,
) -> List[Dict[str, Any]]:
    """Generate Q&A pairs from entity relationships."""
    pairs = []
    node_map = graph_data["node_map"]

    for edge in graph_data["edges"]:
        if len(pairs) >= max_pairs:
            break

        src = node_map.get(edge["source_id"], {})
        tgt = node_map.get(edge["target_id"], {})
        rel = edge["relationship"]

        src_label = src.get("label", "Unknown")
        tgt_label = tgt.get("label", "Unknown")
        src_type = src.get("entity_type", "entity")
        tgt_type = tgt.get("entity_type", "entity")

        # Generate question from template
        template = RELATIONSHIP_TEMPLATES.get(rel, "What is the relationship between {source} and {target}?")
        question = template.format(source=src_label, target=tgt_label)

        # Generate answer from template
        answer_template = ANSWER_TEMPLATES.get(
            rel,
            "{source} ({source_type}) has a {rel} relationship with {target} ({target_type}).",
        )
        answer = answer_template.format(
            source=src_label,
            target=tgt_label,
            source_type=src_type,
            target_type=tgt_type,
            rel=rel,
        )

        pairs.append(
            {
                "user_input": question,
                "expected_output": answer,
                "system_prompt": "You are a compliance and systems engineering assistant.",
                "source": "kg_entity_relationship",
                "metadata": {
                    "strategy": "entity_relationship",
                    "edge_id": edge["id"],
                    "relationship": rel,
                    "source_entity": src_label,
                    "target_entity": tgt_label,
                },
            }
        )

    return pairs


# ---------------------------------------------------------------------------
# Strategy 2: Community Cluster Pairs
# ---------------------------------------------------------------------------


def _generate_community_cluster_pairs(
    graph_data: Dict[str, Any],
    min_size: int = 3,
    max_pairs: int = 20,
) -> List[Dict[str, Any]]:
    """Generate Q&A pairs from graph community clusters."""
    pairs = []
    node_map = graph_data["node_map"]
    components = _find_components(graph_data["nodes"], graph_data["edges"])

    for component in components:
        if len(component) < min_size or len(pairs) >= max_pairs:
            continue

        # Find the entity types and labels in this community
        members = [node_map[nid] for nid in component if nid in node_map]
        type_counts: Dict[str, int] = defaultdict(int)
        for m in members:
            type_counts[m.get("entity_type", "unknown")] += 1

        dominant_type = max(type_counts, key=type_counts.get) if type_counts else "entity"
        member_labels = [m.get("label", "") for m in members]

        # Find highest centrality node
        highest_centrality = max(members, key=lambda m: m.get("centrality", 0))

        question = (
            f"What are the key entities and relationships in the {dominant_type} "
            f"cluster containing {highest_centrality.get('label', 'unknown')}?"
        )

        answer = (
            f"The cluster centered on {highest_centrality.get('label', '')} "
            f"({highest_centrality.get('entity_type', '')}) contains {len(members)} "
            f"related entities: {', '.join(member_labels[:10])}. "
            f"The dominant entity type is {dominant_type} "
            f"({type_counts[dominant_type]} of {len(members)} entities). "
            f"This cluster represents a connected group within the knowledge graph "
            f"where entities share direct or transitive relationships."
        )

        pairs.append(
            {
                "user_input": question,
                "expected_output": answer,
                "system_prompt": "You are a knowledge graph analyst explaining entity clusters.",
                "source": "kg_community_cluster",
                "metadata": {
                    "strategy": "community_cluster",
                    "community_size": len(members),
                    "dominant_type": dominant_type,
                    "central_entity": highest_centrality.get("label", ""),
                },
            }
        )

    return pairs


# ---------------------------------------------------------------------------
# Strategy 3: Compliance Crosswalk Pairs
# ---------------------------------------------------------------------------


def _generate_compliance_crosswalk_pairs(
    graph_data: Dict[str, Any],
    max_pairs: int = 20,
) -> List[Dict[str, Any]]:
    """Generate Q&A pairs from control↔standard edges."""
    pairs = []
    node_map = graph_data["node_map"]

    # Group edges by target standard
    standard_controls: Dict[str, List[str]] = defaultdict(list)

    for edge in graph_data["edges"]:
        src = node_map.get(edge["source_id"], {})
        tgt = node_map.get(edge["target_id"], {})

        # Look for control → standard or standard → control edges
        if src.get("entity_type") == "control" and tgt.get("entity_type") == "standard":
            standard_controls[tgt.get("label", "")].append(src.get("label", ""))
        elif src.get("entity_type") == "standard" and tgt.get("entity_type") == "control":
            standard_controls[src.get("label", "")].append(tgt.get("label", ""))

    for standard, controls in standard_controls.items():
        if len(pairs) >= max_pairs:
            break
        if not standard or not controls:
            continue

        question = f"Which controls satisfy the requirements of {standard}?"
        answer = (
            f"The following {len(controls)} control(s) satisfy {standard}: "
            f"{', '.join(sorted(controls))}. "
            f"Each control maps to specific requirements within the {standard} framework "
            f"and must be implemented to achieve compliance."
        )

        pairs.append(
            {
                "user_input": question,
                "expected_output": answer,
                "system_prompt": "You are a compliance crosswalk expert mapping controls to standards.",
                "source": "kg_compliance_crosswalk",
                "metadata": {
                    "strategy": "compliance_crosswalk",
                    "standard": standard,
                    "control_count": len(controls),
                    "controls": sorted(controls),
                },
            }
        )

    return pairs


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def generate_pairs_from_graph(
    graph_id: str,
    dataset_id: Optional[str] = None,
    strategies: Optional[List[str]] = None,
    store: bool = True,
) -> Dict[str, Any]:
    """Generate training pairs from a knowledge graph.

    Args:
        graph_id: Knowledge graph to process.
        dataset_id: Target FT dataset (created if not exists).
        strategies: Which strategies to run (default: all).
        store: If True, persist pairs to ft_dataset_examples.

    Returns:
        Summary with generated pairs per strategy.
    """
    config = {**DEFAULT_CONFIG, **_load_config()}
    if not config.get("enabled", True):
        return {"status": "disabled"}

    conn = _get_db()
    graph_data = _load_graph(conn, graph_id)
    if "error" in graph_data:
        conn.close()
        return {"status": "error", "error": graph_data["error"]}

    active_strategies = strategies or config.get("strategies", DEFAULT_CONFIG["strategies"])
    max_pairs = config.get("max_pairs_per_graph", 50)
    min_community = config.get("min_community_size", 3)

    all_pairs: List[Dict[str, Any]] = []
    strategy_counts: Dict[str, int] = {}

    # Run each strategy
    if "entity_relationship" in active_strategies:
        er_pairs = _generate_entity_relationship_pairs(graph_data, max_pairs)
        all_pairs.extend(er_pairs)
        strategy_counts["entity_relationship"] = len(er_pairs)

    if "community_cluster" in active_strategies:
        cc_pairs = _generate_community_cluster_pairs(graph_data, min_community, max_pairs)
        all_pairs.extend(cc_pairs)
        strategy_counts["community_cluster"] = len(cc_pairs)

    if "compliance_crosswalk" in active_strategies:
        cx_pairs = _generate_compliance_crosswalk_pairs(graph_data, max_pairs)
        all_pairs.extend(cx_pairs)
        strategy_counts["compliance_crosswalk"] = len(cx_pairs)

    # Truncate to max
    all_pairs = all_pairs[:max_pairs]

    # Store pairs if requested
    stored_count = 0
    if store and dataset_id and all_pairs:
        try:
            for pair in all_pairs:
                ch = _content_hash(pair.get("system_prompt", "") + pair["user_input"] + pair["expected_output"])
                # Check dedup
                existing = conn.execute(
                    "SELECT id FROM ft_dataset_examples WHERE content_hash = %s AND dataset_id = %s",
                    (ch, dataset_id),
                ).fetchone()
                if existing:
                    continue

                conn.execute(
                    """INSERT INTO ft_dataset_examples
                       (dataset_id, system_prompt, user_input, expected_output,
                        source, quality_score, content_hash, classification, created_at)
                       VALUES (%s, %s, %s, %s, %s, 0.7, %s, 'CUI', %s)""",
                    (
                        dataset_id,
                        pair.get("system_prompt", ""),
                        pair["user_input"],
                        pair["expected_output"],
                        pair.get("source", "kg_auto_generated"),
                        ch,
                        _now(),
                    ),
                )
                stored_count += 1
            conn.commit()
        except Exception as exc:
            logger.error("Failed to store pairs: %s", exc)

    conn.close()

    return {
        "status": "ok",
        "graph_id": graph_id,
        "dataset_id": dataset_id,
        "strategies_run": list(strategy_counts.keys()),
        "pairs_by_strategy": strategy_counts,
        "total_pairs_generated": len(all_pairs),
        "pairs_stored": stored_count,
        "pairs": all_pairs if not store else [],  # Only return pairs if not storing
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="KG-to-FT pair generator (D-KARL-6)",
    )
    parser.add_argument("--graph-id", required=True, help="Knowledge graph ID")
    parser.add_argument("--dataset-id", help="Target FT dataset ID")
    parser.add_argument("--strategy", help="Specific strategy to run")
    parser.add_argument("--no-store", action="store_true", help="Don't store pairs, just preview")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    strategies = [args.strategy] if args.strategy else None
    result = generate_pairs_from_graph(
        graph_id=args.graph_id,
        dataset_id=args.dataset_id,
        strategies=strategies,
        store=not args.no_store,
    )

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
