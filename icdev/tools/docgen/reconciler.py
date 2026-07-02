# CUI // SP-CTI
"""IDR Reconciler — merges multi-team diagram analyses into one canonical topology.

Wraps ``topology_merge.merge_topologies()`` and promotes property conflicts to
``idr_conflicts`` rows for HITL resolution (Stage 3).

Each "source" is an upload file.  After merge the reconciled graph becomes the
single source of truth; the caller is responsible for saving it to the NDC
canvas as a new topology.
"""
from __future__ import annotations

from typing import Any

from tools.logging.icdev_logger import get_logger
from tools.network.topology_merge import merge_topologies
from tools.docgen import session_manager as sm

log = get_logger(__name__)

# Properties whose cross-source disagreements are worth flagging
_CONFLICT_PROPS = frozenset(["type", "vendor", "model", "ip", "vlan", "zone"])


def reconcile(
    session_id: str,
    named_graphs: list[tuple[str, dict]],
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Merge *named_graphs* and record conflicts in the DB.

    Args:
        session_id: IDR session id — conflicts are linked here.
        named_graphs: list of ``(source_name, graph)`` pairs from diagram analyses.
        tenant_id: optional tenant for multi-tenant deployments.

    Returns:
        The merged graph dict (nodes, edges, _stitched_hosts, _stats)
        plus ``_conflicts_recorded`` count added to ``_stats``.
    """
    if not named_graphs:
        return {"nodes": [], "edges": [], "_stitched_hosts": [], "_stats": {"conflicts_recorded": 0}}

    merged = merge_topologies(named_graphs)
    conflicts_recorded = 0

    # Detect property conflicts across sources for stitched (multi-source) nodes
    stitched_labels = set(merged.get("_stitched_hosts", []))
    for node in merged.get("nodes", []):
        label = node.get("label", "")
        if label not in stitched_labels:
            continue
        sources = node.get("sources", [])
        if len(sources) < 2:
            continue

        # Build per-source property maps for this node from the original graphs
        per_source: dict[str, dict] = {}
        for src_name, graph in named_graphs:
            if src_name not in sources:
                continue
            for n in graph.get("nodes", []):
                if n.get("label", "").strip().lower() == label.strip().lower():
                    per_source[src_name] = {
                        "type": n.get("type", ""),
                        **{k: v for k, v in (n.get("properties") or {}).items()},
                    }
                    break

        if len(per_source) < 2:
            continue

        # Compare each property across sources
        src_list = list(per_source.keys())
        for prop in _CONFLICT_PROPS:
            values = {s: per_source[s].get(prop) for s in src_list if per_source[s].get(prop)}
            unique_vals = set(str(v) for v in values.values() if v)
            if len(unique_vals) > 1:
                source_a = src_list[0]
                source_b = src_list[1]
                conflict_type = "node_type" if prop == "type" else "property"
                try:
                    sm.add_conflict(
                        session_id=session_id,
                        node_label=label,
                        conflict_type=conflict_type,
                        source_a=source_a,
                        source_a_value={prop: per_source[source_a].get(prop)},
                        source_b=source_b,
                        source_b_value={prop: per_source[source_b].get(prop)},
                        tenant_id=tenant_id,
                    )
                    conflicts_recorded += 1
                    log.info(
                        "IDR conflict recorded: session=%s node=%r prop=%s a=%r b=%r",
                        session_id, label, prop,
                        per_source[source_a].get(prop),
                        per_source[source_b].get(prop),
                    )
                except Exception:
                    log.exception("Failed to record conflict for node=%r prop=%s", label, prop)

    # Flag nodes present in only one source but not others (missing_in_source)
    all_source_names = [s for s, _ in named_graphs]
    if len(all_source_names) > 1:
        for node in merged.get("nodes", []):
            node_sources = set(node.get("sources", []))
            missing_from = [s for s in all_source_names if s not in node_sources]
            if missing_from and len(node_sources) >= 1:
                present_in = list(node_sources)[0]
                absent_in = missing_from[0]
                try:
                    sm.add_conflict(
                        session_id=session_id,
                        node_label=node.get("label", ""),
                        conflict_type="missing_in_source",
                        source_a=present_in,
                        source_a_value={"present": True, "type": node.get("type")},
                        source_b=absent_in,
                        source_b_value={"present": False},
                        tenant_id=tenant_id,
                    )
                    conflicts_recorded += 1
                except Exception:
                    log.exception(
                        "Failed to record missing_in_source conflict for node=%r",
                        node.get("label"),
                    )

    stats = merged.get("_stats", {})
    stats["conflicts_recorded"] = conflicts_recorded
    merged["_stats"] = stats

    log.info(
        "IDR reconcile complete: session=%s nodes=%d edges=%d stitched=%d conflicts=%d",
        session_id,
        len(merged.get("nodes", [])),
        len(merged.get("edges", [])),
        len(merged.get("_stitched_hosts", [])),
        conflicts_recorded,
    )
    return merged
