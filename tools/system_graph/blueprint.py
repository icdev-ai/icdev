
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""System Graph blueprint — routes for the unified Sigma.js graph dashboard page."""

import threading
import time

from flask import Blueprint, jsonify, render_template, request

from .constants import NODE_TYPES, EDGE_TYPES
from .graph_builder import build_graph, build_search_fallback, get_node_detail

logger = get_logger(__name__)

bp = Blueprint("system_graph", __name__)

# In-process cache for the full (unfiltered) graph payload.
# Strategy: stale-while-revalidate — serve stale data instantly, rebuild in background.
_cache_lock = threading.Lock()
_cache: dict = {}
_rebuilding: set = set()
_CACHE_TTL = 900        # serve fresh for 15 min
_STALE_TTL = 3600       # serve stale (while rebuilding) for up to 1 h


def _rebuild_in_background(cache_key: str, kwargs: dict) -> None:
    """Spawn a daemon thread to rebuild the graph without blocking the request."""
    with _cache_lock:
        if cache_key in _rebuilding:
            return  # already in progress
        _rebuilding.add(cache_key)

    def _worker():
        try:
            data = build_graph(**kwargs)
            with _cache_lock:
                _cache[cache_key] = {"data": data, "ts": time.time()}
            logger.info("system-graph cache refreshed (key=%s)", cache_key[:20])
        except Exception as exc:
            logger.warning("system-graph background rebuild failed: %s", exc)
        finally:
            with _cache_lock:
                _rebuilding.discard(cache_key)

    t = threading.Thread(target=_worker, daemon=True, name="sys-graph-rebuild")
    t.start()


def _get_cached_graph(**kwargs) -> dict:
    """Return graph data; uses stale-while-revalidate so the caller never blocks."""
    cache_key = str(sorted(kwargs.items()))

    with _cache_lock:
        entry = _cache.get(cache_key)

    if entry:
        age = time.time() - entry["ts"]
        if age < _CACHE_TTL:
            return entry["data"]                  # fresh — serve immediately
        if age < _STALE_TTL:
            _rebuild_in_background(cache_key, kwargs)
            return entry["data"]                  # stale but usable — serve immediately, rebuild async

    # No cache at all: build synchronously (only on very first load or after _STALE_TTL)
    data = build_graph(**kwargs)
    with _cache_lock:
        _cache[cache_key] = {"data": data, "ts": time.time()}
    return data


def _prewarm_cache() -> None:
    """Build the default graph in a background thread at startup."""
    def _worker():
        try:
            logger.info("system-graph: pre-warming cache…")
            data = build_graph()
            key = str(sorted({}.items()))
            with _cache_lock:
                _cache[key] = {"data": data, "ts": time.time()}
            logger.info(
                "system-graph: cache ready — %d nodes, %d edges",
                len(data.get("nodes", [])),
                len(data.get("edges", [])),
            )
        except Exception as exc:
            logger.warning("system-graph: pre-warm failed: %s", exc)

    t = threading.Thread(target=_worker, daemon=True, name="sys-graph-prewarm")
    t.start()


# Pre-warm immediately when this module is imported (i.e. at dashboard startup)
_prewarm_cache()


@bp.route("/system-graph")
@bp.route("/system-graph/")
def index():
    return render_template(
        "system_graph/page.html",
        node_types=list(NODE_TYPES.keys()),
        edge_types=list(EDGE_TYPES.keys()),
    )


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

@bp.route("/api/system-graph/graph")
def api_graph():
    """Return the full federated graph payload for Sigma.js rendering."""
    filter_type = request.args.get("type") or None
    filter_health = request.args.get("health") or None
    filter_cluster = request.args.get("cluster")
    if filter_cluster is not None:
        try:
            filter_cluster = int(filter_cluster)
        except ValueError:
            filter_cluster = None
    search = request.args.get("q") or None
    sources_param = request.args.get("sources") or None
    sources = sources_param.split(",") if sources_param else None

    # Only cache the unfiltered full graph; filtered/searched requests bypass cache
    if not any([filter_type, filter_health, filter_cluster, search, sources]):
        data = _get_cached_graph()
    else:
        try:
            data = build_graph(
                sources=sources,
                filter_type=filter_type,
                filter_cluster=filter_cluster,
                filter_health=filter_health,
                search=search,
            )
        except Exception as exc:
            logger.error(
                "system-graph build_graph failed (search=%r filter_type=%r): %s",
                search, filter_type, exc, exc_info=True,
            )
            if search:
                # Return degraded response: BM25/substring hits only, no layout/clusters
                data = build_search_fallback(
                    search=search, sources=sources, error=str(exc)
                )
            else:
                data = {
                    "nodes": [], "edges": [],
                    "stats": {
                        "source_counts": {},
                        "total_nodes": 0,
                        "total_edges": 0,
                        "cluster_count": 0,
                        "build_ms": 0,
                        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "partial": True,
                        "error": str(exc),
                    },
                }
    return jsonify(data)


@bp.route("/api/system-graph/node/<node_id>")
def api_node_detail(node_id: str):
    detail = get_node_detail(node_id)
    if not detail:
        return jsonify({"error": "node not found"}), 404
    return jsonify(detail)


@bp.route("/api/system-graph/node-types")
def api_node_types():
    return jsonify({"node_types": NODE_TYPES, "edge_types": EDGE_TYPES})
