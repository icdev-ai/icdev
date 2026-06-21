# CUI // SP-CTI
"""Agentic AI Design Canvas — Memory Layer assessment rules.

Checks that vector-db and persistent memory nodes are properly wired in
agentic research pipeline designs. Focuses on:
  - Embedding model presence (MEA — data integrity)
  - Chunker upstream of vector-db (MAP — pipeline completeness)
  - Audit trail for persistent memory (GOVERN — immutable audit)
  - Data validation to prevent RAG poisoning (MITRE ATLAS AML.T0020)
"""

from __future__ import annotations

from tools.agentic_ai_canvas.constants import MEMORY_LAYER_CHECKS, MEMORY_NODES


def _node_types(nodes: list[dict]) -> set[str]:
    return {n.get("type", "") for n in nodes}


def _build_adjacency(edges: list[dict]) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {}
    for e in edges:
        adj.setdefault(e.get("source", ""), set()).add(e.get("target", ""))
    return adj


def _build_reverse_adjacency(edges: list[dict]) -> dict[str, set[str]]:
    radj: dict[str, set[str]] = {}
    for e in edges:
        radj.setdefault(e.get("target", ""), set()).add(e.get("source", ""))
    return radj


def _reachable(start: str, adj: dict[str, set[str]]) -> set[str]:
    visited: set[str] = set()
    stack = [start]
    while stack:
        n = stack.pop()
        if n in visited:
            continue
        visited.add(n)
        stack.extend(adj.get(n, set()) - visited)
    return visited


def _all_upstream(node_id: str, radj: dict[str, set[str]]) -> set[str]:
    """Return all nodes transitively upstream of node_id."""
    return _reachable(node_id, radj)


# Persistent memory node types that require audit trails
_PERSISTENT_MEM_TYPES = {"vector-db", "long-term-mem"}


def check_memory_layer(nodes: list[dict],
                       edges: list[dict]) -> tuple[list[dict], float]:
    """Evaluate memory layer compliance checks for agentic research pipelines.

    Returns (findings, score_pct). If no memory nodes are present, returns
    ([], None) — design is not penalised for omitting memory.
    """
    types = _node_types(nodes)
    has_memory = bool(types & MEMORY_NODES)

    if not has_memory:
        return [], None  # type: ignore[return-value]

    has_vector_db = "vector-db" in types
    has_persistent = bool(types & _PERSISTENT_MEM_TYPES)

    if not has_vector_db and not has_persistent:
        return [], None  # type: ignore[return-value]

    node_map = {n["id"]: n for n in nodes}
    adj = _build_adjacency(edges)
    radj = _build_reverse_adjacency(edges)

    findings: list[dict] = []
    total_weight = sum(c["weight"] for c in MEMORY_LAYER_CHECKS)
    passed_weight = 0.0

    for check in MEMORY_LAYER_CHECKS:
        passed = False
        check_id = check["check"]

        if check_id == "vector_db_has_embedding_model":
            # Skip if no vector-db in design
            if not has_vector_db:
                passed = True
            else:
                passed = bool(types & {"embedding-model"})

        elif check_id == "vector_db_has_chunker":
            if not has_vector_db:
                passed = True
            else:
                vdb_ids = [n["id"] for n in nodes if n.get("type") == "vector-db"]
                passed = all(
                    bool({"chunker"} & {node_map.get(u, {}).get("type")
                                        for u in _all_upstream(vid, radj)})
                    for vid in vdb_ids
                )

        elif check_id == "persistent_memory_has_audit":
            if not has_persistent:
                passed = True
            else:
                persist_ids = [n["id"] for n in nodes
                               if n.get("type") in _PERSISTENT_MEM_TYPES]
                passed = all(
                    "audit-logger" in {node_map.get(d, {}).get("type")
                                       for d in _reachable(pid, adj)}
                    for pid in persist_ids
                )

        elif check_id == "vector_db_has_data_validator":
            if not has_vector_db:
                passed = True
            else:
                vdb_ids = [n["id"] for n in nodes if n.get("type") == "vector-db"]
                passed = all(
                    bool({"data-validator"} & {node_map.get(u, {}).get("type")
                                               for u in _all_upstream(vid, radj)})
                    for vid in vdb_ids
                )

        if passed:
            passed_weight += check["weight"]
        else:
            findings.append({
                "id": f"mem-{check['id']}",
                "framework": "NIST AI RMF (Memory Layer)",
                "function": check["function"],
                "category": check["category"],
                "severity": "HIGH" if check["weight"] >= 12 else "MEDIUM",
                "title": check["title"],
                "detail": check["description"],
                "recommendation": f"See memory layer check {check['id']} remediation guidance.",
            })

    score = round((passed_weight / total_weight) * 100, 1) if total_weight else 0.0
    return findings, score


def get_memory_layer_map(nodes: list[dict], edges: list[dict]) -> list[dict]:
    """Return per-memory-node coverage summary (embedding, chunker, audit, validator)."""
    types = _node_types(nodes)
    if not bool(types & MEMORY_NODES):
        return []

    adj = _build_adjacency(edges)
    radj = _build_reverse_adjacency(edges)
    node_map = {n["id"]: n for n in nodes}
    result = []

    for node in nodes:
        if node.get("type") not in MEMORY_NODES:
            continue
        upstream_types = {node_map.get(u, {}).get("type")
                          for u in _all_upstream(node["id"], radj)}
        downstream_types = {node_map.get(d, {}).get("type")
                            for d in _reachable(node["id"], adj)}
        result.append({
            "node_id": node["id"],
            "node_label": node.get("label", node["id"]),
            "node_type": node.get("type"),
            "has_embedding_model": "embedding-model" in types,
            "has_chunker_upstream": "chunker" in upstream_types,
            "has_audit_downstream": "audit-logger" in downstream_types,
            "has_validator_upstream": "data-validator" in upstream_types,
        })

    return result
