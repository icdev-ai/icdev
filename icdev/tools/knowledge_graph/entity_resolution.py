# CUI // SP-CTI
"""Entity resolution for the DIC knowledge graph — merge co-referent nodes.

The LLM extractor runs per chunk, so the SAME entity ("Peering", "ISP",
"Exchange Point") is minted as a SEPARATE node in every chunk that mentions it.
A collection's graph ends up with dozens of "Peering" nodes, its edges scattered
across all of them — the graph looks connected but is really N shadows of each
entity. Communities built on that are blurry.

The disambiguator already has the merge machinery (merge_entities re-points every
edge onto the survivor, aliases the rest, deletes the duplicate). What it does
NOT flag is the dominant case here: the same normalized label AND type repeated
within one graph (its duplicate signals require a differing type, graph, or
surface label). This resolver closes exactly that gap: within each DIC document
graph, collapse each (normalized label, entity_type) group to one canonical node.

Scope is deliberately conservative — same normalized label and same type only.
It will NOT merge "ISP" with "Internet Service Provider" (that needs embeddings
or an abbreviation model and risks false merges); it only collapses what is
provably the same entity written the same way.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Optional

from tools.knowledge_graph.disambiguator import _normalize_label, merge_entities
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


def resolve_graph_duplicates(
    graph_id: str,
    *,
    dry_run: bool = False,
    conn=None,
    merge_fn: Optional[Callable[[str, str], dict]] = None,
) -> dict[str, Any]:
    """Collapse within-graph (normalized label, type) duplicates to one node.

    The survivor is the earliest node (stable, and its edges are already the
    oldest); the rest are merged into it — every edge re-pointed, their surface
    labels kept as aliases. Returns counts; writes nothing when dry_run.
    """
    _merge = merge_fn or merge_entities
    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, label, entity_type FROM kg_nodes WHERE graph_id = %s "
            "ORDER BY created_at, id",
            (graph_id,),
        ).fetchall()
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass

    nodes = [dict(r) if hasattr(r, "keys") else {"id": r[0], "label": r[1], "entity_type": r[2]} for r in rows]
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for n in nodes:
        key = (_normalize_label(n["label"] or ""), n["entity_type"] or "")
        if key[0]:
            groups[key].append(n)

    nodes_merged = 0
    groups_merged = 0
    for key, grp in groups.items():
        if len(grp) < 2:
            continue
        canonical = grp[0]["id"]
        groups_merged += 1
        for dup in grp[1:]:
            if dry_run:
                nodes_merged += 1
                continue
            try:
                res = _merge(dup["id"], canonical)
                if isinstance(res, dict) and res.get("status") == "error":
                    logger.debug("merge %s->%s failed: %s", dup["id"], canonical, res.get("error"))
                    continue
                nodes_merged += 1
            except Exception as exc:  # noqa: BLE001 — one bad merge must not abort the sweep
                logger.debug("merge %s->%s raised: %s", dup["id"], canonical, exc)

    return {"graph_id": graph_id, "duplicate_groups": groups_merged, "nodes_merged": nodes_merged}


def resolve_dic_entities(
    *,
    graph_ids: Optional[list[str]] = None,
    dry_run: bool = False,
    merge_fn: Optional[Callable[[str, str], dict]] = None,
) -> dict[str, Any]:
    """Resolve duplicates across all DIC document graphs (or a given subset).

    Only rag_kg_bridge graphs — canvas architecture graphs share kg_edges and
    must not be deduped as documents.
    """
    from tools.db.storage import get_connection
    from tools.knowledge_graph.community_engine import _dic_graph_ids

    conn = get_connection()
    try:
        targets = list(graph_ids) if graph_ids else sorted(_dic_graph_ids(conn))
    finally:
        try:
            conn.close()
        except Exception:
            pass

    total_groups = total_merged = 0
    for gid in targets:
        stats = resolve_graph_duplicates(gid, dry_run=dry_run, merge_fn=merge_fn)
        total_groups += stats["duplicate_groups"]
        total_merged += stats["nodes_merged"]
    result = {"graphs": len(targets), "duplicate_groups": total_groups, "nodes_merged": total_merged, "dry_run": dry_run}
    if total_merged:
        logger.info("DIC entity resolution: merged %s duplicate node(s) across %s group(s)", total_merged, total_groups)
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    p = argparse.ArgumentParser(description="Merge co-referent entities in the DIC knowledge graph")
    p.add_argument("--dry-run", action="store_true", help="Report what would merge; write nothing")
    p.add_argument("--json", dest="as_json", action="store_true")
    args = p.parse_args(argv)

    stats = resolve_dic_entities(dry_run=args.dry_run)
    print(json.dumps(stats) if args.as_json else f"entity resolution: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
