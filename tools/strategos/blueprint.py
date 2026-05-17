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

  GET  /wargame/<id>/lanchester/monte-carlo  → Monte Carlo percentile bands JSON

  GET  /strategos/intel-brief               → Predictive Intelligence Briefings page
  GET  /strategos/briefs                    → Full intelligence briefs history page
  POST /api/strategos/intel-brief/run       → Trigger predictive pipeline → brief JSON
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


@bp.route("/wargame/<wargame_id>/lanchester/monte-carlo")
def api_wargame_lanchester_monte_carlo(wargame_id: str):
    import json as _json
    from tools.db.storage import get_connection, is_pg
    from tools.strategos.ooda import lanchester_monte_carlo

    try:
        iterations = int(request.args.get("iterations", 500))
        sigma = float(request.args.get("sigma", 0.15))
    except (ValueError, TypeError) as exc:
        resp = make_response(jsonify({"error": f"invalid query param: {exc}"}), 400)
        resp.headers["X-Classification"] = "CUI"
        return resp

    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT blue_strength, red_strength, attrition_coefficients_json "  # nosec B608
            f"FROM sg_wargames WHERE id = {ph}",
            (wargame_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        resp = make_response(jsonify({"error": f"Wargame {wargame_id!r} not found"}), 404)
        resp.headers["X-Classification"] = "CUI"
        return resp

    b0_db = float(row[0] or 0)
    r0_db = float(row[1] or 0)
    coeff: dict = {}
    if row[2]:
        try:
            coeff = _json.loads(row[2])
        except Exception:
            pass
    beta_db = float(coeff.get("beta", 0.01))
    rho_db  = float(coeff.get("rho",  0.01))

    # Allow UI overrides for b0/r0/beta/rho (user may have edited the fields)
    try:
        b0   = float(request.args["b0"])   if "b0"   in request.args else b0_db
        r0   = float(request.args["r0"])   if "r0"   in request.args else r0_db
        beta = float(request.args["beta"]) if "beta" in request.args else beta_db
        rho  = float(request.args["rho"])  if "rho"  in request.args else rho_db
    except (ValueError, TypeError) as exc:
        resp = make_response(jsonify({"error": f"invalid param: {exc}"}), 400)
        resp.headers["X-Classification"] = "CUI"
        return resp

    try:
        result = lanchester_monte_carlo(b0, r0, beta=beta, rho=rho,
                                        iterations=iterations, sigma=sigma)
    except Exception as exc:
        resp = make_response(jsonify({"error": str(exc)}), 500)
        resp.headers["X-Classification"] = "CUI"
        return resp

    resp = make_response(jsonify(result))
    resp.headers["X-Classification"] = "CUI"
    return resp


@bp.route("/wargame/<wargame_id>/ooda/live")
def api_wargame_ooda_live(wargame_id: str):
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"

    conn = get_connection()
    try:
        # Latest OODA assessment for this wargame
        row = conn.execute(
            f"SELECT id, observe_score, orient_score, decide_score, act_score, "  # nosec B608
            f"overall_score, notes, created_at "
            f"FROM sg_ooda_assessments WHERE wargame_id = {ph} "
            f"ORDER BY created_at DESC LIMIT 1",
            (wargame_id,),
        ).fetchone()

        # Signal count over last 24h — unscoped (sg_prioritized_signals has no conflict_id)
        if is_pg():
            sig_row = conn.execute(
                "SELECT COUNT(*) FROM sg_prioritized_signals "
                "WHERE created_at > NOW() - INTERVAL '24 hours'",
            ).fetchone()
        else:
            sig_row = conn.execute(
                "SELECT COUNT(*) FROM sg_prioritized_signals "
                "WHERE created_at > datetime('now', '-24 hours')",
            ).fetchone()
    finally:
        conn.close()

    signal_count_24h = int(sig_row[0]) if sig_row else 0

    if row is None:
        assessment = None
        last_updated = None
    else:
        assessment = {
            "id": row[0],
            "observe_score": row[1],
            "orient_score": row[2],
            "decide_score": row[3],
            "act_score": row[4],
            "overall_score": row[5],
            "notes": row[6],
        }
        last_updated = row[7]

    payload = {
        "wargame_id": wargame_id,
        "assessment": assessment,
        "signal_count_24h": signal_count_24h,
        "last_updated": last_updated,
    }
    resp = make_response(jsonify(payload))
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


# ---------------------------------------------------------------------------
# Diplomatic Activity Tracker (DAT)
# ---------------------------------------------------------------------------


def _dat():
    from tools.strategos.dat import (
        get_cables, get_unsc_events, get_backchannels,
        get_latest_dti, get_dti_history,
        ingest_cable, ingest_unsc_event, ingest_backchannel,
        refresh_dti, compute_dti,
    )
    return (
        get_cables, get_unsc_events, get_backchannels,
        get_latest_dti, get_dti_history,
        ingest_cable, ingest_unsc_event, ingest_backchannel,
        refresh_dti, compute_dti,
    )


@bp.route("/strategos/dat")
@bp.route("/strategos/dat/")
def strategos_dat_page():
    (get_cables, get_unsc_events, get_backchannels,
     get_latest_dti, get_dti_history, *_) = _dat()
    theater = request.args.get("theater", "global")
    latest = get_latest_dti(theater) or {}
    history = list(reversed(get_dti_history(theater, limit=48)))
    cables = get_cables(theater, limit=20)
    unsc_events = get_unsc_events(theater, limit=15)
    backchannels = get_backchannels(theater, limit=15)
    import json as _json
    return render_template(
        "strategos/dat.html",
        theater=theater,
        latest=latest,
        history=history,
        cables=cables,
        unsc_events=unsc_events,
        backchannels=backchannels,
        history_json=_json.dumps(history),
    )


@bp.route("/api/strategos/dat/score")
def api_dat_score():
    (_, _, _, get_latest_dti, _, _, _, _, _, compute_dti) = _dat()
    theater = request.args.get("theater", "global")
    live = request.args.get("live", "false").lower() == "true"
    if live:
        result = compute_dti(theater)
    else:
        result = get_latest_dti(theater) or compute_dti(theater)
    resp = make_response(jsonify(result))
    resp.headers["X-Classification"] = "CUI"
    return resp


@bp.route("/api/strategos/dat/history")
def api_dat_history():
    (_, _, _, _, get_dti_history, *_) = _dat()
    theater = request.args.get("theater", "global")
    limit = min(int(request.args.get("limit", 48)), 200)
    history = get_dti_history(theater, limit=limit)
    resp = make_response(jsonify({"theater": theater, "history": history, "total": len(history)}))
    resp.headers["X-Classification"] = "CUI"
    return resp


@bp.route("/api/strategos/dat/ingest/cable", methods=["POST"])
def api_dat_ingest_cable():
    (_, _, _, _, _, ingest_cable, *_) = _dat()
    data = request.get_json(silent=True) or {}
    try:
        result = ingest_cable(
            theater_id=data.get("theater_id", "global"),
            cable_ref=data.get("cable_ref"),
            origin=data.get("origin"),
            subject=data.get("subject"),
            tension_level=data.get("tension_level", "low"),
            keywords=data.get("keywords"),
            summary=data.get("summary"),
            received_at=data.get("received_at"),
            classification=data.get("classification", "CUI"),
        )
        resp = make_response(jsonify({"status": "ok", "cable": result}), 201)
    except Exception as exc:
        resp = make_response(jsonify({"error": str(exc)}), 500)
    resp.headers["X-Classification"] = "CUI"
    return resp


@bp.route("/api/strategos/dat/ingest/unsc", methods=["POST"])
def api_dat_ingest_unsc():
    (_, _, _, _, _, _, ingest_unsc_event, *_) = _dat()
    data = request.get_json(silent=True) or {}
    try:
        result = ingest_unsc_event(
            theater_id=data.get("theater_id", "global"),
            event_type=data.get("event_type", "scheduled"),
            topic=data.get("topic"),
            agenda_item=data.get("agenda_item"),
            emergency=bool(data.get("emergency", False)),
            veto_cast=bool(data.get("veto_cast", False)),
            walkout=bool(data.get("walkout", False)),
            participating_states=data.get("participating_states"),
            scheduled_at=data.get("scheduled_at"),
            outcome=data.get("outcome"),
        )
        resp = make_response(jsonify({"status": "ok", "event": result}), 201)
    except Exception as exc:
        resp = make_response(jsonify({"error": str(exc)}), 500)
    resp.headers["X-Classification"] = "CUI"
    return resp


@bp.route("/api/strategos/dat/ingest/backchannel", methods=["POST"])
def api_dat_ingest_backchannel():
    (_, _, _, _, _, _, _, ingest_backchannel, *_) = _dat()
    data = request.get_json(silent=True) or {}
    try:
        result = ingest_backchannel(
            theater_id=data.get("theater_id", "global"),
            channel_type=data.get("channel_type", "diplomatic"),
            parties=data.get("parties"),
            frequency_delta=float(data.get("frequency_delta", 0.0)),
            escalation_flag=bool(data.get("escalation_flag", False)),
            communication_breakdown=bool(data.get("communication_breakdown", False)),
            metadata=data.get("metadata"),
            observed_at=data.get("observed_at"),
        )
        resp = make_response(jsonify({"status": "ok", "backchannel": result}), 201)
    except Exception as exc:
        resp = make_response(jsonify({"error": str(exc)}), 500)
    resp.headers["X-Classification"] = "CUI"
    return resp


@bp.route("/api/strategos/dat/refresh", methods=["POST"])
def api_dat_refresh():
    (_, _, _, _, _, _, _, _, refresh_dti, _) = _dat()
    data = request.get_json(silent=True) or {}
    theater = data.get("theater_id", request.args.get("theater", "global"))
    try:
        result = refresh_dti(theater)
        resp = make_response(jsonify({"status": "ok", "snapshot": result}), 201)
    except Exception as exc:
        resp = make_response(jsonify({"error": str(exc)}), 500)
    resp.headers["X-Classification"] = "CUI"
    return resp


# ---------------------------------------------------------------------------
# Predictive Intel Brief — page + run API
# ---------------------------------------------------------------------------


def _intel_brief_engine():
    from tools.strategos.predictive_intel_engine import (
        generate_leadership_brief,
        list_leadership_briefs,
        get_leadership_brief,
    )
    return generate_leadership_brief, list_leadership_briefs, get_leadership_brief


@bp.route("/strategos/intel-brief")
@bp.route("/strategos/intel-brief/")
def strategos_intel_brief_page():
    """Predictive Intelligence Briefings dashboard page."""
    import json as _json
    _, list_briefs, _ = _intel_brief_engine()
    theater = request.args.get("theater", "global")
    briefs = list_briefs(theater=theater, limit=20)

    # Normalise forecast fields so template can access them as attributes
    for b in briefs:
        for key in ("forecast_24h_json", "forecast_72h_json", "forecast_7d_json"):
            if isinstance(b.get(key), str):
                try:
                    b[key] = _json.loads(b[key])
                except Exception:
                    b[key] = {}
            elif b.get(key) is None:
                b[key] = {}

    latest_brief = briefs[0] if briefs else None
    resp = make_response(render_template(
        "strategos/intel_brief.html",
        theater=theater,
        briefs=briefs,
        latest_brief=latest_brief,
        briefs_json=_json.dumps(briefs),
    ))
    resp.headers["X-Classification"] = "CUI"
    return resp


@bp.route("/strategos/briefs")
@bp.route("/strategos/briefs/")
def strategos_briefs_page():
    """Full history listing of all leadership intelligence briefs."""
    import json as _json
    _, list_briefs, _ = _intel_brief_engine()
    theater = request.args.get("theater", "global")
    limit = min(int(request.args.get("limit", 50)), 200)
    briefs = list_briefs(theater=theater, limit=limit)
    for b in briefs:
        for key in ("forecast_24h_json", "forecast_72h_json", "forecast_7d_json"):
            if isinstance(b.get(key), str):
                try:
                    b[key] = _json.loads(b[key])
                except Exception:
                    b[key] = {}
            elif b.get(key) is None:
                b[key] = {}
    resp = make_response(render_template(
        "strategos/briefs.html",
        theater=theater,
        briefs=briefs,
        briefs_json=_json.dumps(briefs),
    ))
    resp.headers["X-Classification"] = "CUI"
    return resp


@bp.route("/api/strategos/intel-brief/run", methods=["POST"])
def api_strategos_intel_brief_run():
    """Trigger the predictive intelligence pipeline and return the brief."""
    gen_brief, _, _ = _intel_brief_engine()
    data = request.get_json(silent=True) or {}
    theater = data.get("theater", "global") or "global"
    try:
        result = gen_brief(theater=theater, save=True)
    except Exception as exc:
        resp = make_response(
            jsonify({"ok": False, "error": str(exc)}), 500
        )
        resp.headers["X-Classification"] = "CUI"
        return resp

    payload = {
        "ok": True,
        "brief_id": result.get("brief_id"),
        "theater": result.get("theater"),
        "wri": result.get("sio_composite_score"),
        "threat_tier": result.get("threat_tier"),
        "iw_triggered": result.get("iw_triggered"),
        "signal_count_24h": result.get("signal_count_24h"),
        "signal_velocity": result.get("signal_velocity"),
        "p_war_posterior": result.get("p_war") or result.get("p_war_posterior"),
        "narrative_md": result.get("narrative_md", ""),
        "generated_at": result.get("generated_at"),
        "latency_ms": result.get("latency_ms"),
    }
    resp = make_response(jsonify(payload))
    resp.headers["X-Classification"] = "CUI"
    return resp


def create_strategos_blueprint() -> Blueprint:
    return bp
