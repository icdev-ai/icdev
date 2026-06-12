# [CUI // SP-CTI]
"""ICDEV™ NDC — Merge multiple topology graphs into one.

Real-world use case: different teams (site ops, cloud, DC, security) each
publish a PDF/VSDX for their slice of the enterprise network. NDC
ingests them separately, then this module stitches them into a single
topology by matching device labels across graphs.

``merge_topologies`` takes a list of graph dicts (``{nodes, edges}``) and
returns one graph with:

- Duplicate-label nodes collapsed to a single canonical node (preserves
  properties from all sources — later graphs don't clobber earlier ones)
- Edges rewritten to point at canonical node IDs
- Cross-graph edges discovered automatically when a shape on one page
  shares a label with a shape on another page (that's the "stitch")
- Provenance: every node/edge carries a ``sources`` list naming which
  input graphs contributed it
"""
from __future__ import annotations

import re
from typing import Iterable


def _normalize(label: str) -> str:
    """Canonicalize a label for cross-graph matching.

    Lowercase, collapse whitespace, strip punctuation beyond ``-``/``_``/``.``.
    So ``"Core-Router 01"`` == ``"core-router-01"`` == ``"core-router 01"``.
    """
    s = (label or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9._\-]", "", s)
    return s


def merge_topologies(
    graphs: Iterable[tuple[str, dict]],
    *,
    match_on: str = "label",
) -> dict:
    """Merge graphs keyed by ``(source_name, graph_dict)`` pairs.

    Args:
        graphs: Iterable of ``(source_name, graph)`` tuples. Order matters
            only for tie-breaking; property merge is union.
        match_on: ``"label"`` (default) matches nodes by normalized label.
            Switch to ``"id"`` if your ingestion already assigns stable
            asset IDs across sources.

    Returns:
        ``{nodes, edges, _sources, _stitched_hosts, _stats}`` where
        ``_stitched_hosts`` lists labels that appeared in ≥2 graphs (the
        actual cross-diagram bridges).
    """
    canonical_nodes: dict[str, dict] = {}  # key -> node
    edges_out: list[dict] = []
    edge_seen: set[tuple[str, str]] = set()  # canonical (src_key, dst_key)
    source_names: list[str] = []
    per_source_keys: dict[str, set[str]] = {}

    for source_name, graph in graphs:
        source_names.append(source_name)
        per_source_keys.setdefault(source_name, set())
        node_id_to_key: dict[str, str] = {}

        for n in graph.get("nodes", []):
            key = (
                _normalize(n.get("label", ""))
                if match_on == "label"
                else str(n.get("id", ""))
            )
            if not key:
                continue
            node_id_to_key[n["id"]] = key
            per_source_keys[source_name].add(key)

            if key not in canonical_nodes:
                canonical_nodes[key] = {
                    "id": f"merged-{key}",
                    "label": n.get("label", key),
                    "type": n.get("type", "unknown"),
                    "x": n.get("x", 0),
                    "y": n.get("y", 0),
                    "properties": dict(n.get("properties", {})),
                    "sources": [source_name],
                }
            else:
                existing = canonical_nodes[key]
                # Upgrade "unknown" / "imported" types when a later source
                # classifies more specifically
                if existing["type"] in ("unknown", "imported") and n.get("type") not in (
                    None, "unknown", "imported",
                ):
                    existing["type"] = n["type"]
                # Union properties — first source wins on conflict (don't
                # silently overwrite curated data with later imports)
                for pk, pv in (n.get("properties") or {}).items():
                    existing["properties"].setdefault(pk, pv)
                if source_name not in existing["sources"]:
                    existing["sources"].append(source_name)

        for e in graph.get("edges", []):
            src_key = node_id_to_key.get(e.get("source", ""))
            dst_key = node_id_to_key.get(e.get("target", ""))
            if not src_key or not dst_key or src_key == dst_key:
                continue
            edge_key = tuple(sorted([src_key, dst_key]))
            if edge_key in edge_seen:
                # Same link seen in another graph — record the extra source
                for existing in edges_out:
                    if tuple(sorted([
                        existing["_src_key"], existing["_dst_key"]
                    ])) == edge_key:
                        if source_name not in existing["sources"]:
                            existing["sources"].append(source_name)
                        break
                continue
            edge_seen.add(edge_key)
            edges_out.append({
                "id": f"merged-edge-{len(edges_out)}",
                "source": f"merged-{src_key}",
                "target": f"merged-{dst_key}",
                "label": e.get("label", ""),
                "type": e.get("type", "ethernet"),
                "sources": [source_name],
                "_src_key": src_key,
                "_dst_key": dst_key,
            })

    # Strip internal bookkeeping before returning
    for e in edges_out:
        e.pop("_src_key", None)
        e.pop("_dst_key", None)

    # Find "stitch points" — nodes referenced by >1 source graph
    stitched = sorted(
        n["label"] for n in canonical_nodes.values() if len(n["sources"]) > 1
    )

    return {
        "nodes": list(canonical_nodes.values()),
        "edges": edges_out,
        "_sources": source_names,
        "_stitched_hosts": stitched,
        "_stats": {
            "total_nodes": len(canonical_nodes),
            "total_edges": len(edges_out),
            "stitch_points": len(stitched),
            "per_source_node_count": {
                s: len(k) for s, k in per_source_keys.items()
            },
        },
    }
