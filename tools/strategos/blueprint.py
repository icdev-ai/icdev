#!/usr/bin/env python3
# CUI // SP-CTI
"""Strategos — Flask Blueprint.

Routes:
  GET  /strategos/supply                     → DIB supply chain dashboard
  GET  /api/strategos/supply/nodes           → All DIB nodes (JSON)
  GET  /api/strategos/supply/edges           → All supply edges (JSON)
  GET  /api/strategos/supply/critical-path   → Targeting priority list (JSON)
  POST /api/strategos/supply/sync            → Rebuild graph + KG edges

  GET  /strategos/darkweb                    → Dark Web Monitor dashboard
  POST /api/strategos/darkweb/run            → Trigger monitor scan (async)
"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

bp = Blueprint("strategos", __name__, url_prefix="")


def _mapper():
    from tools.strategos.dib_mapper import (
        build_dib_graph,
        write_kg_edges,
        critical_path,
        get_nodes,
        get_edges,
    )
    return build_dib_graph, write_kg_edges, critical_path, get_nodes, get_edges


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


@bp.route("/strategos/supply")
@bp.route("/strategos/supply/")
def strategos_supply_page():
    build_dib_graph, _, _, get_nodes, get_edges = _mapper()
    build_dib_graph()
    nodes = get_nodes()
    edges = get_edges()
    import json as _json
    from tools.strategos.dib_mapper import critical_path as _cp
    cp = _cp()
    return render_template(
        "strategos/supply.html",
        nodes=nodes,
        edges=edges,
        critical_path=cp,
        nodes_json=_json.dumps(nodes),
        edges_json=_json.dumps(edges),
    )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@bp.route("/api/strategos/supply/nodes")
def api_strategos_nodes():
    _, _, _, get_nodes, _ = _mapper()
    side = request.args.get("side")
    rows = get_nodes()
    if side:
        rows = [r for r in rows if r["side"] == side]
    return jsonify({"nodes": rows, "total": len(rows)})


@bp.route("/api/strategos/supply/edges")
def api_strategos_edges():
    _, _, _, _, get_edges = _mapper()
    edge_type = request.args.get("edge_type")
    rows = get_edges()
    if edge_type:
        rows = [r for r in rows if r["edge_type"] == edge_type]
    return jsonify({"edges": rows, "total": len(rows)})


@bp.route("/api/strategos/supply/critical-path")
def api_strategos_critical_path():
    _, _, cp_fn, _, _ = _mapper()
    side = request.args.get("side")
    rows = cp_fn(side=side)
    return jsonify({"critical_path": rows, "total": len(rows)})


@bp.route("/api/strategos/supply/sync", methods=["POST"])
def api_strategos_sync():
    build_dib_graph, write_kg_edges, _, _, _ = _mapper()
    graph_result = build_dib_graph()
    kg_result = write_kg_edges()
    return jsonify({"status": "ok", "graph": graph_result, "kg": kg_result})


# ---------------------------------------------------------------------------
# Dark Web Monitor — page + API
# ---------------------------------------------------------------------------


def _darkweb():
    from tools.strategos.darkweb import get_signals, tor_available, run_monitor
    return get_signals, tor_available, run_monitor


@bp.route("/strategos/darkweb")
@bp.route("/strategos/darkweb/")
def strategos_darkweb_page():
    get_signals, tor_available_fn, _ = _darkweb()
    status = request.args.get("status", "all")
    signals = get_signals(status=status if status != "all" else None)
    return render_template(
        "strategos/darkweb.html",
        signals=signals,
        tor_available=tor_available_fn(),
    )


@bp.route("/api/strategos/darkweb/run", methods=["POST"])
def api_strategos_darkweb_run():
    _, _, run_monitor = _darkweb()
    result = run_monitor()
    return jsonify(result)


def create_strategos_blueprint() -> Blueprint:
    return bp
