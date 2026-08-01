# CUI // SP-CTI
"""Genesis reflex: keep the DIC GraphRAG community summaries fresh.

The community engine clusters the DIC knowledge graph and summarises each theme
(dic_community_summaries), which the chat surface consults for global/thematic
questions. As documents are ingested the graph changes, so the summaries drift
stale. This reflex re-runs build_communities on a cadence to keep them current.

build_communities is idempotent (community_id is content-stable; a graph's prior
communities are prefix-deleted before rewrite), so a refresh replaces rather than
accumulates. It processes only DIC document graphs — canvas architecture graphs
share kg_edges and are excluded by the engine.

Registered at all three points (module + REFLEX_NAMES in tools/genesis/daemon.py
+ args/genesis_config.yaml) — miss one and it silently never runs.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import uuid
from typing import Any, Dict

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

CADENCE_HOURS = 24


class CommunityRefreshReflex:
    """Rebuild DIC community summaries so global/thematic chat answers stay current."""

    def run(self, context: Dict[str, Any], db_conn=None) -> Dict[str, Any]:
        reflex_id = context.get("reflex_id", f"cr-{uuid.uuid4().hex[:10]}")
        dry_run = bool(context.get("dry_run", False))
        result: Dict[str, Any] = {"reflex_id": reflex_id, "dry_run": dry_run}

        try:
            from tools.db.storage import get_connection
            from tools.knowledge_graph.community_engine import (
                _dic_graph_ids,
                build_communities,
            )

            conn = db_conn or get_connection()

            if dry_run:
                # Report how many DIC graphs would be (re)clustered without writing.
                graphs = len(_dic_graph_ids(conn))
                result["graphs_available"] = graphs
                return {"success": True, "metric_value": float(graphs), "details": result}

            # Resolve co-referent entities first — the per-chunk extractor mints a
            # separate node per mention, so collapsing (label,type) duplicates
            # sharpens the graph the communities are then built on.
            try:
                from tools.knowledge_graph.entity_resolution import resolve_dic_entities

                res_stats = resolve_dic_entities()
                result["entities_merged"] = res_stats.get("nodes_merged", 0)
            except Exception as exc:  # noqa: BLE001 — resolution is best-effort
                logger.debug("community refresh: entity resolution skipped: %s", exc)

            stats = build_communities(conn)
            result.update(stats)
            logger.info(
                "community refresh: %s communities across %s graph(s)",
                stats.get("communities", 0), stats.get("graphs", 0),
            )
            return {"success": True, "metric_value": float(stats.get("communities", 0)), "details": result}
        except Exception as exc:  # noqa: BLE001 — a reflex must never crash the daemon
            logger.warning("community refresh failed: %s", exc)
            return {"success": False, "metric_value": 0.0, "details": {**result, "error": str(exc)}}


def run(context: Dict[str, Any], db_conn=None) -> Dict[str, Any]:
    """Module-level entry point (Genesis daemon dispatch contract)."""
    return CommunityRefreshReflex().run(context, db_conn)
