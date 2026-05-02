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

from flask import Blueprint, jsonify, make_response, render_template, request

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


# ---------------------------------------------------------------------------
# Wargame Turn Engine
# ---------------------------------------------------------------------------


@bp.route("/wargame/<wargame_id>/orbat-seed")
def api_wargame_orbat_seed(wargame_id: str):
    from tools.strategos.wargame_orbat import load_orbat_strengths
    try:
        result = load_orbat_strengths(wargame_id)
    except ValueError as exc:
        resp = make_response(jsonify({"error": str(exc)}), 404)
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        resp = make_response(jsonify({"error": str(exc)}), 500)
        resp.headers["X-Classification"] = "CUI"
        return resp
    resp = make_response(jsonify(result))
    resp.headers["X-Classification"] = "CUI"
    return resp


@bp.route("/wargame/<wargame_id>/turn/advance", methods=["POST"])
def api_wargame_turn_advance(wargame_id: str):
    from tools.strategos.wargame_turn_engine import advance_turn
    try:
        turn = advance_turn(wargame_id)
    except ValueError as exc:
        resp = make_response(jsonify({"error": str(exc)}), 404)
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        resp = make_response(jsonify({"error": str(exc)}), 500)
        resp.headers["X-Classification"] = "CUI"
        return resp
    resp = make_response(jsonify(turn), 201)
    resp.headers["X-Classification"] = "CUI"
    return resp


@bp.route("/wargame/<wargame_id>/turns")
def api_wargame_turns(wargame_id: str):
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT id, wargame_id, turn_number, blue_losses, red_losses, "  # nosec B608
            f"blue_remaining, red_remaining, tempo_delta, notes, created_at "
            f"FROM sg_wargame_turns WHERE wargame_id = {ph} "
            f"ORDER BY turn_number ASC",
            (wargame_id,),
        ).fetchall()
    finally:
        conn.close()
    cols = ("id", "wargame_id", "turn_number", "blue_losses", "red_losses",
            "blue_remaining", "red_remaining", "tempo_delta", "notes", "created_at")
    turns = [dict(zip(cols, r)) for r in rows]
    resp = make_response(jsonify({"wargame_id": wargame_id, "turns": turns, "total": len(turns)}))
    resp.headers["X-Classification"] = "CUI"
    return resp


# ---------------------------------------------------------------------------
# Strategos Chat API
# ---------------------------------------------------------------------------


def _require_chat_manager():
    """Return chat_manager or None if unavailable."""
    try:
        from tools.dashboard.chat_manager import chat_manager
        return chat_manager
    except ImportError:
        return None


@bp.route("/api/strategos/chat/init", methods=["POST"])
def api_strategos_chat_init():
    mgr = _require_chat_manager()
    if not mgr:
        resp = make_response(jsonify({"error": "chat_manager unavailable"}), 503)
        resp.headers["X-Classification"] = "CUI"
        return resp
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id", "local")
    page = data.get("page", "")
    from tools.strategos.strategos_chat import create_strategos_context
    ctx = create_strategos_context(user_id=user_id, page=page)
    if "error" in ctx:
        resp = make_response(jsonify(ctx), 500)
        resp.headers["X-Classification"] = "CUI"
        return resp
    resp = make_response(jsonify({
        "context_id": ctx.get("context_id"),
        "status": ctx.get("status"),
        "title": ctx.get("title"),
    }), 201)
    resp.headers["X-Classification"] = "CUI"
    return resp


@bp.route("/api/strategos/chat/<context_id>/inject", methods=["POST"])
def api_strategos_chat_inject(context_id: str):
    mgr = _require_chat_manager()
    if not mgr:
        resp = make_response(jsonify({"error": "chat_manager unavailable"}), 503)
        resp.headers["X-Classification"] = "CUI"
        return resp
    data = request.get_json(silent=True) or {}
    entity = data.get("entity", {})
    if not entity:
        resp = make_response(jsonify({"error": "entity required"}), 400)
        resp.headers["X-Classification"] = "CUI"
        return resp
    from tools.strategos.strategos_chat import inject_entity_context
    result = inject_entity_context(context_id, entity)
    if "error" in result:
        resp = make_response(jsonify(result), 404 if "not found" in result.get("error", "").lower() else 500)
        resp.headers["X-Classification"] = "CUI"
        return resp
    resp = make_response(jsonify({"status": "ok", "turn_number": result.get("turn_number")}))
    resp.headers["X-Classification"] = "CUI"
    return resp


@bp.route("/api/strategos/chat/<context_id>/send", methods=["POST"])
def api_strategos_chat_send(context_id: str):
    mgr = _require_chat_manager()
    if not mgr:
        resp = make_response(jsonify({"error": "chat_manager unavailable"}), 503)
        resp.headers["X-Classification"] = "CUI"
        return resp
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        resp = make_response(jsonify({"error": "content required"}), 400)
        resp.headers["X-Classification"] = "CUI"
        return resp
    result = mgr.send_message(context_id, content, role="user")
    if "error" in result:
        resp = make_response(jsonify(result), 404 if "not found" in result.get("error", "").lower() else 500)
        resp.headers["X-Classification"] = "CUI"
        return resp
    resp = make_response(jsonify({"status": "queued", "turn_number": result.get("turn_number")}))
    resp.headers["X-Classification"] = "CUI"
    return resp


@bp.route("/api/strategos/chat/poll/<context_id>")
def api_strategos_chat_poll(context_id: str):
    mgr = _require_chat_manager()
    if not mgr:
        resp = make_response(jsonify({"error": "chat_manager unavailable"}), 503)
        resp.headers["X-Classification"] = "CUI"
        return resp
    ctx = mgr.get_context(context_id)
    if not ctx:
        resp = make_response(jsonify({"error": "context not found"}), 404)
        resp.headers["X-Classification"] = "CUI"
        return resp
    messages = mgr.get_messages(context_id, since_turn=0, limit=100)
    resp = make_response(jsonify({
        "context_id": context_id,
        "status": ctx.get("status"),
        "is_processing": ctx.get("is_processing", False),
        "dirty_version": ctx.get("dirty_version", 0),
        "turn_number": ctx.get("turn_number", 0),
        "messages": messages,
    }))
    resp.headers["X-Classification"] = "CUI"
    return resp


def create_strategos_blueprint() -> Blueprint:
    return bp
