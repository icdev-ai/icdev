
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""System Graph blueprint — routes for the unified Sigma.js graph dashboard page."""

import threading
import time

from flask import Blueprint, jsonify, render_template, request
from tools.security.canvas_access import check_access as _canvas_check_access

from .constants import NODE_TYPES, EDGE_TYPES
from .graph_builder import build_graph, build_search_fallback, get_node_detail

logger = get_logger(__name__)

bp = Blueprint("system_graph", __name__)

@bp.before_request
def _check_canvas_access():
    """G-02: DENY-ALL canvas access gate. Requires explicit grant in canvas_access_grants."""
    try:
        from flask import g, abort, request as _req
        # Skip health/status utility endpoints
        if _req.path.endswith(("/health", "/status", "/ping")):
            return
        user = getattr(g, "current_user", None) or {}
        user_id = str(user.get("id", "") or user.get("user_id", "") or "")
        tenant_id = str(getattr(g, "tenant_id", None) or user.get("tenant_id", "") or "")
        if not user_id or not tenant_id:
            abort(403)
        if not _canvas_check_access(user_id, tenant_id, "system_graph"):
            abort(403)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug("canvas_access check error: %s", exc)
        abort(403)


# Simple in-process cache for the full (unfiltered) graph payload — 5 min TTL
_cache_lock = threading.Lock()
_cache: dict = {}
_CACHE_TTL = 300  # seconds


def _get_cached_graph(**kwargs) -> dict:
    cache_key = str(sorted(kwargs.items()))
    with _cache_lock:
        entry = _cache.get(cache_key)
        if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
            return entry["data"]
    data = build_graph(**kwargs)
    with _cache_lock:
        _cache[cache_key] = {"data": data, "ts": time.time()}
    return data


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
