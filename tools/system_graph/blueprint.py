# CUI // SP-CTI
"""System Graph blueprint — routes for the unified Sigma.js graph dashboard page."""

from flask import Blueprint, jsonify, render_template, request

from .constants import NODE_TYPES, EDGE_TYPES
from .graph_builder import build_graph, get_node_detail

bp = Blueprint("system_graph", __name__)


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

    data = build_graph(
        sources=sources,
        filter_type=filter_type,
        filter_cluster=filter_cluster,
        filter_health=filter_health,
        search=search,
    )
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
