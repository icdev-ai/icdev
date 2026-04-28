#!/usr/bin/env python3
# CUI // SP-CTI
"""Strategos Blueprint — Strategic Intelligence Operations.

Registers page routes (mounted at /strategos) and a separate API blueprint
(mounted at /api/strategos) covering all Strategos sub-modules:

  Pages (url_prefix="/strategos"):
    GET  /                  — Overview dashboard
    GET  /orbat             — Order of Battle tracker
    GET  /ghost             — Ghost signals / dark-vessel anomalies
    GET  /iw                — Information Warfare effects board
    GET  /wargame           — Wargame scenarios
    GET  /kg                — Knowledge graph visualiser
    GET  /hitl              — Human-in-the-Loop decision queue
    GET  /pir               — PIR/CCIR management
    GET  /interdiction      — Supply interdiction priority dashboard
    GET  /briefs            — Intelligence products list
    GET  /briefs/<id>       — Rendered brief + Approve/Annotate/Export

  Pages (additional):
    GET  /signals           — Signal priority queue (top-50 scored signals)
    GET  /simulate          — Simulation workspace (layout/panels)
    GET  /ew                — EW jamming zones + economic supply chain overlay

  API (url_prefix="/api/strategos"):
    GET    /pir                     — List PIR/CCIR/EEI requirements
    POST   /pir                     — Create a new PIR/CCIR/EEI
    PATCH  /pir/<id>                — Update/cancel a PIR
    GET    /kg                      — Knowledge-graph JSON (nodes + edges)
    POST   /hitl/<id>/resolve       — Resolve a HITL item
    GET    /interdiction            — Latest ranked interdiction targets (JSON)
    POST   /interdiction/run        — Trigger a fresh ranking run
    GET    /interdiction/coefficients — Supply-degradation attrition coefficients
    POST   /briefs                  — Generate + save a new intelligence brief
    PATCH  /briefs/<id>/approve     — Approve and annotate a brief
    GET    /briefs/<id>/export      — Export brief as PDF (or markdown fallback)
    GET    /signals                 — Filtered JSON list of prioritized signals
    POST   /signals/<id>/read       — Toggle is_read flag
    POST   /signals/<id>/promote    — Toggle promoted_to_kg flag
    POST   /signals/<id>/annotate   — Set annotation text
    POST   /signals/brief           — Bulk IIR pre-fill from selected signals
    POST   /annotate                — Add analyst annotation to any entity
    GET    /annotations/<entity_type>/<entity_id> — List annotations for entity
    GET    /simulate/nodes          — Supply chain nodes with lat/lon for map
    POST   /simulate/run            — Run scenario simulation (deterministic or Monte Carlo)
    GET    /ew/data                 — EW sites, supply nodes, chokepoints + EMCON scores (JSON)

  Wargame API (url_prefix="/api/strategos"):
    GET    /wargame                 — List wargame scenarios
    POST   /wargame                 — Create a new wargame scenario
    GET    /wargame/<id>/ooda       — OODA tempo analysis for a scenario
    GET    /wargame/<id>/lanchester — Lanchester attrition curves
    POST   /wargame/<id>/blotto     — Colonel Blotto force allocation
    POST   /wargame/<id>/nash       — Nash equilibrium for a payoff matrix
    GET    /wargame/<id>/coa        — COA entries for a scenario
    POST   /wargame/<id>/coa        — Add/update COA entry

  Info API (url_prefix="/api/strategos"):
    GET    /info/network-data       — Narrative cluster nodes + amplification edges (JSON)
"""
import json
import math
import random
import uuid
from datetime import datetime, timezone

from flask import Blueprint, flash, jsonify, redirect, render_template, request, Response, url_for

from tools.db.storage import get_connection, is_pg
from tools.intelligence.brief_generator import BRIEF_TYPES, BriefGenerator
from tools.intelligence.pir_manager import (
    coverage_gap_hours,
    create_pir,
    list_pirs,
    update_pir,
)
from tools.intelligence.annotations import (
    ANNOTATION_TYPES,
    ENTITY_TYPES,
    add_annotation,
    get_annotation_summary,
    get_annotations,
)
from tools.strategos.interdiction_ranker import (
    auto_write_interdicts_from_conflict_events,
    get_latest_ranked_targets,
    get_supply_degradation_coefficients,
    rank_interdiction_targets,
)
from tools.strategos.reverse_cascade_inference import NODE_DISRUPTION_PROFILES
from tools.strategos.ew_monitor import get_ew_data
from tools.strategos.ooda import (
    compute_tempo,
    lanchester_square,
    lanchester_linear,
    blotto_expected_payoff,
    find_nash_2x2,
    score_coa,
    DOMAINS as OODA_DOMAINS,
    COA_CRITERIA,
)

# ---------------------------------------------------------------------------
# Signal-queue helpers
# ---------------------------------------------------------------------------

_SOURCE_GRADE: dict = {
    "reuters world news": "A", "reuters": "A",
    "bbc world news": "A", "bbc": "A",
    "ap news": "A", "associated press": "A",
    "defense one": "B", "bellingcat": "B",
    "al jazeera": "B", "cisa alerts": "B", "cisa": "B",
    "twitter": "E", "telegram": "D",
}


def _stanag_grade(source: str) -> str:
    lower = (source or "").lower().strip()
    for name, grade in _SOURCE_GRADE.items():
        if lower.startswith(name) or name in lower:
            return grade
    for gov in ("gov", "mil", ".un.", "nato", "interpol"):
        if gov in lower:
            return "B"
    return "F"


def _ensure_signals_actions() -> None:
    """Add analyst-action columns if migration 057 hasn't run yet."""
    try:
        import importlib  # noqa: PLC0415
        mod = importlib.import_module("tools.db.migrations.057_sg_signals_actions.up")
        mod.up()
    except Exception:
        conn = get_connection()
        for col, defn in [
            ("is_read",        "INTEGER NOT NULL DEFAULT 0"),
            ("promoted_to_kg", "INTEGER NOT NULL DEFAULT 0"),
            ("annotation",     "TEXT"),
        ]:
            try:
                conn.execute(
                    f"ALTER TABLE sg_prioritized_signals ADD COLUMN {col} {defn}"
                )
                conn.commit()
            except Exception:
                pass
        conn.close()


def _update_signal(sig_id: int, **fields) -> bool:
    conn = get_connection()
    ph = "%s" if is_pg() else "?"
    try:
        set_clause = ", ".join(f"{k} = {ph}" for k in fields)
        vals = list(fields.values()) + [sig_id]
        result = conn.execute(
            f"UPDATE sg_prioritized_signals SET {set_clause} WHERE id = {ph}",  # nosec B608
            vals,
        )
        conn.commit()
        return (result.rowcount or 0) > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Blueprint instances
# ---------------------------------------------------------------------------

_bp = Blueprint("strategos", __name__)       # page routes (prefix: /strategos)
_api = Blueprint("strategos_api", __name__)  # API routes  (prefix: /api/strategos)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_fetch(sql: str, params=(), default=None):
    """Execute sql safely, returning default on missing-table errors."""
    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        if "no such table" in str(exc).lower():
            return default if default is not None else []
        raise
    finally:
        conn.close()


def _safe_count(sql: str, params=()) -> int:
    conn = get_connection()
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else 0
    except Exception:
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@_bp.route("/")
def strategos_index():
    summary = {
        "orbat_units":   _safe_count("SELECT COUNT(*) FROM sg_orbat_units"),
        "ghost_signals": _safe_count("SELECT COUNT(*) FROM sg_ghost_signals"),
        "iw_effects":    _safe_count("SELECT COUNT(*) FROM sg_iw_effects"),
        "wargames":      _safe_count("SELECT COUNT(*) FROM sg_wargames"),
        "kg_nodes":      _safe_count("SELECT COUNT(*) FROM sg_kg_nodes"),
        "hitl_pending":  _safe_count(
            "SELECT COUNT(*) FROM sg_hitl_items WHERE status='pending'"
        ),
        "pir_active":    _safe_count(
            "SELECT COUNT(*) FROM sg_pir_requirements WHERE status='active'"
        ),
        "supply_nodes":  _safe_count("SELECT COUNT(*) FROM sg_supply_nodes"),
        "cyber_ops":     _safe_count("SELECT COUNT(*) FROM sg_conflict_events WHERE event_type='cyber_op'"),
        "briefs_total":       _safe_count("SELECT COUNT(*) FROM sg_intelligence_briefs"),
        "prioritized_signals": _safe_count(
            "SELECT COUNT(*) FROM sg_prioritized_signals WHERE is_read = 0"
        ),
    }
    return render_template("strategos/index.html", summary=summary)


@_bp.route("/orbat")
def strategos_orbat():
    selected_nation = request.args.get("nation", "").strip()
    selected_type = request.args.get("unit_type", "")
    clauses, params = [], []
    if selected_nation:
        clauses.append("nation LIKE ?")
        params.append(f"%{selected_nation}%")
    if selected_type:
        clauses.append("unit_type = ?")
        params.append(selected_type)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    units = _safe_fetch(
        f"SELECT * FROM sg_orbat_units {where} ORDER BY unit_name ASC",  # nosec B608
        params,
    )
    unit_types = [
        r["unit_type"]
        for r in _safe_fetch(
            "SELECT DISTINCT unit_type FROM sg_orbat_units ORDER BY unit_type"
        )
    ]
    return render_template(
        "strategos/orbat.html",
        units=units,
        unit_types=unit_types,
        selected_nation=selected_nation,
        selected_type=selected_type,
    )


@_bp.route("/ghost")
def strategos_ghost():
    selected_type = request.args.get("signal_type", "")
    params = [selected_type] if selected_type else []
    where = "WHERE signal_type = ?" if selected_type else ""
    signals = _safe_fetch(
        f"SELECT * FROM sg_ghost_signals {where} ORDER BY detected_at DESC LIMIT 200",  # nosec B608
        params,
    )
    signal_types = [
        r["signal_type"]
        for r in _safe_fetch(
            "SELECT DISTINCT signal_type FROM sg_ghost_signals ORDER BY signal_type"
        )
    ]
    return render_template(
        "strategos/ghost.html",
        signals=signals,
        signal_types=signal_types,
        selected_type=selected_type,
    )


@_bp.route("/iw")
def strategos_iw():
    selected_type = request.args.get("effect_type", "")
    params = [selected_type] if selected_type else []
    where = "WHERE effect_type = ?" if selected_type else ""
    effects = _safe_fetch(
        f"SELECT * FROM sg_iw_effects {where} ORDER BY detected_at DESC LIMIT 200",  # nosec B608
        params,
    )
    effect_types = [
        r["effect_type"]
        for r in _safe_fetch(
            "SELECT DISTINCT effect_type FROM sg_iw_effects ORDER BY effect_type"
        )
    ]
    return render_template(
        "strategos/iw.html",
        effects=effects,
        effect_types=effect_types,
        selected_type=selected_type,
    )


@_bp.route("/wargame")
def strategos_wargame():
    selected_state = request.args.get("state", "")
    selected_id    = request.args.get("id", "")
    ph = "%s" if is_pg() else "?"

    params = [selected_state] if selected_state else []
    where  = f"WHERE state = {ph}" if selected_state else ""
    wargames = _safe_fetch(
        f"SELECT * FROM sg_wargames {where} ORDER BY created_at DESC LIMIT 100",  # nosec B608
        params,
    )
    wargame_states = [
        r["state"]
        for r in _safe_fetch(
            "SELECT DISTINCT state FROM sg_wargames ORDER BY state"
        )
    ]

    # Active scenario — first active, or user-selected, or first in list
    active_game = None
    if selected_id:
        matches = [g for g in wargames if g.get("id") == selected_id]
        active_game = matches[0] if matches else None
    if not active_game:
        actives = [g for g in wargames if (g.get("state") or "") == "active"]
        active_game = actives[0] if actives else (wargames[0] if wargames else None)

    wargame_id = active_game["id"] if active_game else None

    # OODA tempo (uses baseline data if no DB events)
    ooda = compute_tempo(wargame_id)

    # COA entries
    coa_rows = _safe_fetch(
        f"SELECT * FROM sg_coa_entries WHERE wargame_id = {ph} ORDER BY composite_score DESC",  # nosec B608
        (wargame_id,),
    ) if wargame_id else []

    # Latest Nash scenario
    nash_row = None
    if wargame_id:
        nash_rows = _safe_fetch(
            f"SELECT * FROM sg_nash_scenarios WHERE wargame_id = {ph} ORDER BY created_at DESC LIMIT 1",  # nosec B608
            (wargame_id,),
        )
        nash_row = nash_rows[0] if nash_rows else None

    # Lanchester defaults from wargame attrition_coefficients_json
    lanchester_data = None
    if active_game:
        try:
            coeffs = json.loads(active_game.get("attrition_coefficients_json") or "{}")
            b0   = float(coeffs.get("blue_strength", 1000))
            r0   = float(coeffs.get("red_strength",  800))
            beta = float(coeffs.get("beta", 0.01))
            rho  = float(coeffs.get("rho",  0.01))
            lanchester_data = lanchester_square(b0, r0, beta, rho)
        except Exception:
            lanchester_data = lanchester_square(1000, 800)

    return render_template(
        "strategos/wargame.html",
        wargames=wargames,
        wargame_states=wargame_states,
        selected_state=selected_state,
        active_game=active_game,
        ooda=ooda,
        coa_rows=coa_rows,
        nash_row=nash_row,
        lanchester_data=lanchester_data,
        coa_criteria=list(COA_CRITERIA),
        domains=list(OODA_DOMAINS),
    )


@_bp.route("/kg")
def strategos_kg():
    nodes = _safe_fetch(
        "SELECT node_id, node_type, label FROM sg_kg_nodes ORDER BY node_type LIMIT 500"
    )
    edges = _safe_fetch(
        "SELECT source_id, target_id, relation FROM sg_kg_edges LIMIT 1000"
    )
    relation_counts_raw = _safe_fetch(
        "SELECT relation, COUNT(*) as count FROM sg_kg_edges "
        "GROUP BY relation ORDER BY count DESC LIMIT 20"
    )
    graph = {
        "nodes": nodes,
        "edges": edges,
        "relation_counts": relation_counts_raw,
    }
    return render_template("strategos/kg.html", graph=graph)


@_bp.route("/hitl")
def strategos_hitl():
    selected_status = request.args.get("status", "pending")
    items = _safe_fetch(
        "SELECT * FROM sg_hitl_items WHERE status = ? ORDER BY created_at DESC LIMIT 100",
        (selected_status,),
    )
    decision_types = ["approve", "reject", "escalate", "defer"]
    return render_template(
        "strategos/hitl.html",
        items=items,
        decision_types=decision_types,
        selected_status=selected_status,
    )


@_bp.route("/hitl/action/<int:action_id>", methods=["POST"])
def strategos_hitl_action(action_id: int):
    decision = (request.form.get("decision") or "").strip().upper()
    if decision not in ("APPROVE", "REJECT"):
        flash("Invalid decision. Must be APPROVE or REJECT.", "danger")
        return redirect(url_for("strategos.strategos_hitl"))
    conn = get_connection()
    try:
        result = conn.execute(
            "UPDATE sg_hitl_items SET status = ?, resolved_at = ? WHERE id = ?",
            (decision.lower(), _now(), action_id),
        )
        conn.commit()
        if result.rowcount == 0:
            flash(f"Item {action_id} not found.", "warning")
        else:
            flash(f"Decision recorded: {decision}", "success")
    except Exception as exc:
        flash(f"Error updating item: {exc}", "danger")
    finally:
        conn.close()
    return redirect(url_for("strategos.strategos_hitl"))


@_bp.route("/pir")
def strategos_pir():
    selected_type = request.args.get("type", "")
    selected_status = request.args.get("status", "active")

    pirs = list_pirs(
        pir_type=selected_type or None,
        status=selected_status or None,
    )
    gaps = coverage_gap_hours(pirs)

    # Tasking queue: group PIRs by tasked_to asset
    tasking: dict = {}
    for p in pirs:
        asset = p.get("tasked_to") or "Unassigned"
        tasking.setdefault(asset, []).append(p)

    return render_template(
        "strategos/pir.html",
        pirs=pirs,
        gaps=gaps,
        tasking=tasking,
        selected_type=selected_type,
        selected_status=selected_status,
        pir_types=["PIR", "CCIR", "EEI"],
        statuses=["active", "satisfied", "cancelled"],
    )


@_bp.route("/signals")
def strategos_signals():
    _ensure_signals_actions()

    try:
        active_pirs = list_pirs(status="active")
    except Exception:
        active_pirs = []

    f_pir = request.args.get("pir", "")
    f_source = request.args.get("source", "")
    f_min_score = request.args.get("min_score", "")
    f_date_from = request.args.get("date_from", "")
    f_date_to = request.args.get("date_to", "")
    f_read = request.args.get("read", "")

    clauses: list = []
    params: list = []
    if f_source:
        clauses.append("r.source = ?")
        params.append(f_source)
    if f_min_score:
        try:
            clauses.append("p.composite_score >= ?")
            params.append(float(f_min_score) / 10.0)
        except ValueError:
            pass
    if f_date_from:
        clauses.append("p.created_at >= ?")
        params.append(f_date_from)
    if f_date_to:
        clauses.append("p.created_at <= ?")
        params.append(f_date_to + "T23:59:59Z")
    if f_read == "unread":
        clauses.append("p.is_read = 0")
    elif f_read == "read":
        clauses.append("p.is_read = 1")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        "SELECT p.id, p.raw_signal_id, p.composite_score, "
        "p.posterior_shift_score, p.source_discriminability_score, "
        "p.temporal_recency_score, p.domain_coverage_score, "
        "p.rationale, p.run_at, p.created_at, "
        "p.is_read, p.promoted_to_kg, p.annotation, "
        "r.title, r.body, r.source, r.signal_date "
        "FROM sg_prioritized_signals p "
        "LEFT JOIN sg_raw_signals r ON r.id = p.raw_signal_id "
        f"{where} ORDER BY p.composite_score DESC LIMIT {200 if f_pir else 50}"  # nosec B608
    )
    ph = "%s" if is_pg() else "?"
    sql = sql.replace("?", ph)

    signals_raw = _safe_fetch(sql, params)

    pir_topics = [p.get("topic", "") for p in active_pirs]
    signals_enriched = []
    for s in signals_raw:
        s = dict(s)
        s["stanag_grade"] = _stanag_grade(s.get("source") or "")
        s["score_pct"] = min(round((s.get("composite_score") or 0) * 100, 1), 100)
        s["total"] = round((s.get("composite_score") or 0) * 10, 2)
        text = ((s.get("title") or "") + " " + (s.get("body") or "")).lower()
        covered_pirs = [
            t[:40] for t in pir_topics
            if any(w in text for w in t.lower().split() if len(w) >= 4)
        ]
        s["pir_coverage"] = covered_pirs
        if f_pir and not any(f_pir.lower() in cp.lower() for cp in covered_pirs):
            continue
        try:
            rat = json.loads(s.get("rationale") or "{}")
            s["rationale_display"] = "; ".join(
                f"{k}={v.get('score', 0):.2f}({v.get('note', '')})"
                for k, v in rat.items() if isinstance(v, dict)
            )
        except Exception:
            s["rationale_display"] = s.get("rationale") or ""
        signals_enriched.append(s)

    signals_enriched = signals_enriched[:50]

    source_sql = (
        "SELECT DISTINCT r.source FROM sg_prioritized_signals p "
        "LEFT JOIN sg_raw_signals r ON r.id = p.raw_signal_id "
        "WHERE r.source IS NOT NULL ORDER BY r.source"
    )
    source_list = [r.get("source") for r in _safe_fetch(source_sql) if r.get("source")]

    return render_template(
        "strategos/signals.html",
        signals=signals_enriched,
        active_pirs=active_pirs,
        source_list=source_list,
        filters={
            "pir": f_pir, "source": f_source, "min_score": f_min_score,
            "date_from": f_date_from, "date_to": f_date_to, "read": f_read,
        },
        total=len(signals_enriched),
    )


@_bp.route("/interdiction")
def strategos_interdiction():
    targets = get_latest_ranked_targets(top_n=10)
    last_run = targets[0]["computed_at"][:19] if targets else None
    supply_count = _safe_count("SELECT COUNT(*) FROM sg_supply_nodes")
    return render_template(
        "strategos/interdiction.html",
        targets=targets,
        last_run=last_run,
        supply_count=supply_count,
    )


@_bp.route("/briefs", methods=["GET"])
def strategos_briefs():
    selected_type = request.args.get("type", "")
    reviewed = request.args.get("reviewed", "")
    limit_param = request.args.get("limit", "")

    if reviewed != "" or limit_param != "":
        limit = min(int(limit_param) if limit_param else 200, 500)
        clauses, params = [], []
        if selected_type:
            clauses.append("brief_type = ?")
            params.append(selected_type)
        if reviewed == "0":
            clauses.append("analyst_reviewed = 0")
        elif reviewed == "1":
            clauses.append("analyst_reviewed = 1")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        ph = "%s" if is_pg() else "?"
        sql = (
            f"SELECT id, brief_type, title, sio_confidence, analyst_reviewed, "
            f"reviewed_by, reviewed_at, created_at "
            f"FROM sg_intelligence_briefs {where} ORDER BY created_at DESC LIMIT {limit}"  # nosec B608
        )
        sql = sql.replace("?", ph)
        briefs = _safe_fetch(sql, params)
        pending = sum(1 for b in briefs if not b.get("analyst_reviewed"))
        return jsonify({"briefs": briefs, "total": len(briefs), "pending": pending})

    params = [selected_type] if selected_type else []
    where = "WHERE brief_type = ?" if selected_type else ""
    briefs = _safe_fetch(
        f"SELECT id, brief_type, title, sio_confidence, analyst_reviewed, "
        f"reviewed_by, reviewed_at, created_at "
        f"FROM sg_intelligence_briefs {where} ORDER BY created_at DESC LIMIT 200",  # nosec B608
        params,
    )
    return render_template(
        "strategos/briefs.html",
        briefs=briefs,
        brief_types=list(BRIEF_TYPES),
        selected_type=selected_type,
    )


@_bp.route("/briefs/<brief_id>")
def strategos_brief_detail(brief_id: str):
    rows = _safe_fetch(
        "SELECT * FROM sg_intelligence_briefs WHERE id = ?", (brief_id,)
    )
    if not rows:
        return render_template("strategos/briefs.html", briefs=[], brief_types=list(BRIEF_TYPES),
                               selected_type="", error="Brief not found."), 404
    brief = rows[0]
    try:
        import markdown as md_mod
        content_html = md_mod.markdown(
            brief.get("content_md", ""), extensions=["tables", "fenced_code"]
        )
    except ImportError:
        content_html = None
    return render_template(
        "strategos/brief_detail.html",
        brief=brief,
        content_html=content_html,
    )


@_bp.route("/ew")
def strategos_ew():
    data = get_ew_data()
    return render_template("strategos/ew.html", summary=data["summary"])


@_bp.route("/maritime")
def strategos_maritime():
    return render_template("strategos/maritime.html")


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@_api.route("/iw/composite", methods=["GET"])
def api_iw_composite_get():
    """GET /api/strategos/iw/composite — Latest PMESII-PT WRI assessment."""
    try:
        from tools.strategos.iw_engine import PMESIIPTCompositor  # noqa: PLC0415
        result = PMESIIPTCompositor().get_latest()
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/iw/composite", methods=["POST"])
def api_iw_composite_post():
    """POST /api/strategos/iw/composite — Submit per-dimension scores, get WRI.

    Body: { "scores": { "political": 0.6, "military": 0.8, ... }, "source": "analyst" }
    All 8 PMESII-PT dimensions accepted; missing dimensions default to 0.
    """
    data = request.get_json(silent=True) or {}
    scores = data.get("scores", {})
    source = (data.get("source") or "analyst").strip()
    try:
        from tools.strategos.iw_engine import PMESIIPTCompositor  # noqa: PLC0415
        result = PMESIIPTCompositor().compute_and_save(scores, source=source)
        return jsonify(result), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/iw/derive", methods=["POST"])
def api_iw_derive():
    """POST /api/strategos/iw/derive — Auto-derive PMESII-PT scores from DB."""
    try:
        from tools.strategos.iw_engine import PMESIIPTCompositor  # noqa: PLC0415
        result = PMESIIPTCompositor().derive_from_db()
        return jsonify(result), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/iw/signal-matrix", methods=["GET"])
def api_iw_signal_matrix():
    """GET /api/strategos/iw/signal-matrix — 14-day x signal-category heatmap counts."""
    days_back = min(int(request.args.get("days", 14)), 60)
    _CATEGORIES = [
        "military", "cyber", "information", "economic",
        "political", "infrastructure", "social", "physical",
    ]
    _IW_CAT_MAP = {
        "kinetic": "military", "electronic": "military", "cyber": "cyber",
        "deception": "information", "psyop": "information",
        "disinformation": "information", "influence": "information",
        "supply": "economic", "sanction": "economic", "blockade": "economic",
        "protest": "social", "riot": "social", "displacement": "social",
        "infrastructure": "infrastructure",
    }
    _CE_CAT_MAP = {
        "cyber_op": "cyber", "supply_disruption": "economic",
        "sanction": "economic", "blockade": "economic",
        "displacement": "social", "riot": "social", "protest": "social",
        "military_move": "military", "attack": "military",
        "infrastructure": "infrastructure",
    }

    conn = get_connection()
    try:
        from datetime import date, timedelta  # noqa: PLC0415
        today = date.today()
        day_labels = [(today - timedelta(days=days_back - 1 - i)).isoformat() for i in range(days_back)]
        day_index = {d: i for i, d in enumerate(day_labels)}

        counts: dict[str, dict[str, int]] = {cat: {d: 0 for d in day_labels} for cat in _CATEGORIES}

        # IW effects
        try:
            rows = conn.execute(
                f"SELECT effect_type, DATE(detected_at) AS d, COUNT(*) AS n "
                f"FROM sg_iw_effects "
                f"WHERE detected_at >= DATE('now', '-{days_back} days') "  # nosec B608
                f"GROUP BY effect_type, d"
            ).fetchall()
            for row in rows:
                cat = _IW_CAT_MAP.get((row[0] or "").lower(), "information")
                day = str(row[1] or "")[:10]
                if day in day_index:
                    counts[cat][day] = counts[cat].get(day, 0) + int(row[2] or 0)
        except Exception:
            pass

        # Conflict events
        try:
            rows = conn.execute(
                f"SELECT event_type, DATE(created_at) AS d, COUNT(*) AS n "
                f"FROM sg_conflict_events "
                f"WHERE created_at >= DATE('now', '-{days_back} days') "  # nosec B608
                f"GROUP BY event_type, d"
            ).fetchall()
            for row in rows:
                cat = _CE_CAT_MAP.get((row[0] or "").lower(), "military")
                day = str(row[1] or "")[:10]
                if day in day_index:
                    counts[cat][day] = counts[cat].get(day, 0) + int(row[2] or 0)
        except Exception:
            pass

        # Prioritized signals by date
        try:
            rows = conn.execute(
                f"SELECT r.signal_date, COUNT(*) AS n "
                f"FROM sg_prioritized_signals p "
                f"LEFT JOIN sg_raw_signals r ON r.id = p.raw_signal_id "
                f"WHERE r.signal_date >= DATE('now', '-{days_back} days') "  # nosec B608
                f"GROUP BY r.signal_date"
            ).fetchall()
            for row in rows:
                day = str(row[0] or "")[:10]
                if day in day_index:
                    counts["information"][day] = counts["information"].get(day, 0) + int(row[1] or 0)
        except Exception:
            pass

        matrix = [[counts[cat][d] for d in day_labels] for cat in _CATEGORIES]
        return jsonify({"days": day_labels, "categories": _CATEGORIES, "matrix": matrix})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@_api.route("/iw/survival", methods=["GET"])
def api_iw_survival():
    """GET /api/strategos/iw/survival — Kaplan-Meier-style time-to-crisis survival curve."""
    threshold = float(request.args.get("threshold", 60.0))
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT wri, created_at FROM sg_wri_assessments ORDER BY created_at ASC LIMIT 500"
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    if not rows:
        # Return synthetic demo curve when no data
        import math  # noqa: PLC0415
        times = list(range(0, 31))
        survival = [round(math.exp(-0.04 * t), 4) for t in times]
        return jsonify({"times": times, "survival": survival, "n": 0, "threshold": threshold, "demo": True})

    # Compute time-delta in hours from first observation per "run" segment
    from datetime import datetime as _dt  # noqa: PLC0415
    segments: list[dict] = []
    run_start = None
    for row in rows:
        wri = float(row[0] or 0)
        ts_raw = str(row[1] or "")
        try:
            ts = _dt.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except Exception:
            continue
        if run_start is None:
            run_start = ts
        hours = max(0.0, (ts - run_start).total_seconds() / 3600)
        crossed = wri >= threshold
        segments.append({"t": round(hours, 1), "event": crossed})
        if crossed:
            run_start = ts  # reset for next run

    if not segments:
        return jsonify({"times": [], "survival": [], "n": 0, "threshold": threshold})

    max_t = max(s["t"] for s in segments) or 24.0
    step = max(1.0, max_t / 50)
    times_float = [round(i * step, 1) for i in range(int(max_t / step) + 2)]
    n = len(segments)
    survival = []
    for t in times_float:
        events_by = sum(1 for s in segments if s["event"] and s["t"] <= t)
        surv = round(max(0.0, 1.0 - events_by / n), 4)
        survival.append(surv)

    return jsonify({"times": times_float, "survival": survival, "n": n, "threshold": threshold})


@_api.route("/iw/historical-matches", methods=["GET"])
def api_iw_historical_matches():
    """GET /api/strategos/iw/historical-matches — Top-3 past assessments nearest to current WRI dims."""
    conn = get_connection()
    try:
        current_rows = conn.execute(
            "SELECT * FROM sg_wri_assessments ORDER BY created_at DESC LIMIT 1"
        ).fetchall()
        if not current_rows:
            return jsonify({"matches": [], "note": "no_assessments"})
        current = dict(current_rows[0])
        _DIMS = ("political", "military", "economic", "social",
                 "information", "infrastructure", "physical_environment", "time")
        cur_vec = [float(current.get(d, 0.0)) for d in _DIMS]

        history = conn.execute(
            "SELECT * FROM sg_wri_assessments ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()

    import math  # noqa: PLC0415
    scored = []
    current_id = current.get("id")
    for row in history:
        row = dict(row)
        if row.get("id") == current_id:
            continue
        vec = [float(row.get(d, 0.0)) for d in _DIMS]
        dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(cur_vec, vec)))
        similarity = round(max(0.0, 1.0 - dist / (len(_DIMS) ** 0.5)), 4)
        scored.append({
            "id": row.get("id"),
            "wri": float(row.get("wri", 0)),
            "escalation_rung": int(row.get("escalation_rung", 1)),
            "rung_label": row.get("source", ""),
            "timestamp": str(row.get("created_at", ""))[:16],
            "source": row.get("source", ""),
            "similarity": similarity,
            "dimensions": {d: round(float(row.get(d, 0.0)), 3) for d in _DIMS},
            "dominant_indicators": row.get("dominant_indicators", "[]"),
        })

    scored.sort(key=lambda x: x["similarity"], reverse=True)
    top3 = scored[:3]

    # Enrich rung labels
    from tools.strategos.iw_engine import PMESIIPTCompositor  # noqa: PLC0415
    comp = PMESIIPTCompositor()
    for m in top3:
        rung, label, color = comp.map_escalation_rung(m["wri"])
        m["escalation_rung"] = rung
        m["rung_label"] = label
        m["rung_color"] = color

    return jsonify({"matches": top3, "current_wri": float(current.get("wri", 0))})


@_api.route("/ew/data", methods=["GET"])
def api_ew_data():
    try:
        return jsonify(get_ew_data())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/pir", methods=["GET"])
def api_pir_list():
    pir_type = request.args.get("type")
    status = request.args.get("status")
    try:
        items = list_pirs(pir_type=pir_type, status=status)
        return jsonify({"items": items, "count": len(items)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/pir", methods=["POST"])
def api_pir_create():
    data = request.get_json(silent=True) or {}
    pir_type = data.get("pir_type", "PIR")
    topic = data.get("topic", "").strip()
    if not topic:
        return jsonify({"error": "topic is required"}), 400
    try:
        record = create_pir(
            pir_type=pir_type,
            topic=topic,
            description=data.get("description"),
            collection_priority=int(data.get("collection_priority", 3)),
            tasked_to=data.get("tasked_to"),
            due_by=data.get("due_by"),
        )
        return jsonify(record), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/pir/<pir_id>", methods=["PATCH"])
def api_pir_patch(pir_id: str):
    data = request.get_json(silent=True) or {}
    try:
        record = update_pir(pir_id, **data)
        if record is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(record)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/kg", methods=["GET"])
def api_kg():
    nodes = _safe_fetch(
        "SELECT node_id, node_type, label FROM sg_kg_nodes ORDER BY node_type LIMIT 500"
    )
    edges = _safe_fetch(
        "SELECT source_id, target_id, relation FROM sg_kg_edges LIMIT 1000"
    )
    return jsonify({"nodes": nodes, "edges": edges})


@_api.route("/interdiction", methods=["GET"])
def api_interdiction_latest():
    top_n = min(int(request.args.get("top_n", 10)), 50)
    try:
        targets = get_latest_ranked_targets(top_n=top_n)
        return jsonify({"targets": targets, "count": len(targets)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/interdiction/run", methods=["POST"])
def api_interdiction_run():
    data = request.get_json(silent=True) or {}
    top_n = min(int(data.get("top_n", 10)), 50)
    try:
        # Auto-write INTERDICTS edges from any new ConflictEvents
        auto_write_interdicts_from_conflict_events()
        result = rank_interdiction_targets(top_n=top_n)
        return jsonify(result), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/interdiction/coefficients", methods=["GET"])
def api_interdiction_coefficients():
    top_n = min(int(request.args.get("top_n", 10)), 50)
    try:
        result = get_supply_degradation_coefficients(top_n=top_n)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/briefs", methods=["GET"])
def api_briefs_list():
    brief_type = request.args.get("type", "").strip()
    reviewed = request.args.get("reviewed", "")
    limit = min(int(request.args.get("limit", 200)), 500)
    clauses, params = [], []
    if brief_type:
        clauses.append("brief_type = ?")
        params.append(brief_type)
    if reviewed == "0":
        clauses.append("analyst_reviewed = 0")
    elif reviewed == "1":
        clauses.append("analyst_reviewed = 1")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    ph = "%s" if is_pg() else "?"
    sql = (
        f"SELECT id, brief_type, title, sio_confidence, analyst_reviewed, "
        f"reviewed_by, reviewed_at, created_at "
        f"FROM sg_intelligence_briefs {where} ORDER BY created_at DESC LIMIT {limit}"  # nosec B608
    )
    sql = sql.replace("?", ph)
    briefs = _safe_fetch(sql, params)
    pending = sum(1 for b in briefs if not b.get("analyst_reviewed"))
    return jsonify({"briefs": briefs, "total": len(briefs), "pending": pending})


@_api.route("/briefs", methods=["POST"])
def api_briefs_create():
    data = request.get_json(silent=True) or {}
    brief_type = data.get("brief_type", "").strip()
    title = data.get("title", "").strip()
    context_dict = data.get("context", {})
    sio_confidence = data.get("sio_confidence")

    if brief_type not in BRIEF_TYPES:
        return jsonify({"error": f"brief_type must be one of {BRIEF_TYPES}"}), 400
    if not title:
        return jsonify({"error": "title is required"}), 400

    try:
        gen = BriefGenerator()
        record = gen.generate_and_save(
            brief_type=brief_type,
            title=title,
            context_dict=context_dict,
            sio_confidence=float(sio_confidence) if sio_confidence is not None else None,
        )
        return jsonify(record), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/briefs/<brief_id>/approve", methods=["PATCH"])
def api_briefs_approve(brief_id: str):
    data = request.get_json(silent=True) or {}
    reviewed_by = data.get("reviewed_by", "analyst").strip()
    annotations = data.get("annotations", "").strip()
    conn = get_connection()
    try:
        result = conn.execute(
            """UPDATE sg_intelligence_briefs
               SET analyst_reviewed=1, reviewed_by=?, reviewed_at=?, annotations=?
               WHERE id=? AND analyst_reviewed=0""",
            (reviewed_by, _now(), annotations or None, brief_id),
        )
        conn.commit()
        if result.rowcount == 0:
            return jsonify({"error": "brief not found or already reviewed"}), 404
        return jsonify({"status": "approved", "reviewed_by": reviewed_by})
    except Exception as exc:
        if "no such table" in str(exc).lower():
            return jsonify({"error": "briefs module not initialised"}), 503
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@_api.route("/briefs/<brief_id>/export", methods=["GET"])
def api_briefs_export(brief_id: str):
    rows = _safe_fetch(
        "SELECT * FROM sg_intelligence_briefs WHERE id = ?", (brief_id,)
    )
    if not rows:
        return jsonify({"error": "brief not found"}), 404
    brief = rows[0]
    content_md = brief.get("content_md", "")

    fmt = request.args.get("format", "pdf").lower()
    if fmt == "pdf":
        gen = BriefGenerator()
        pdf_bytes = gen.export_pdf(content_md)
        if pdf_bytes:
            filename = f"brief-{brief_id[:8]}.pdf"
            return Response(
                pdf_bytes,
                mimetype="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
    # Fallback: serve markdown
    filename = f"brief-{brief_id[:8]}.md"
    return Response(
        content_md,
        mimetype="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@_bp.route("/oracle")
def strategos_oracle():
    return render_template("strategos/oracle.html")


@_bp.route("/simulate")
def strategos_simulate():
    return render_template("strategos/simulate.html")


@_api.route("/oracle", methods=["GET"])
def api_oracle():
    """Return latest OracleAssessment as JSON."""
    # ── Run or read assessment ──────────────────────────────────────────────
    try:
        from intelligence.oracle.sio_engine import SIOEngine, get_latest_assessment
        try:
            assessment = SIOEngine().run_all()
            lenses_raw = assessment.lenses
            composite_score = assessment.composite_score
            iw_triggered = assessment.iw_triggered
            timestamp = assessment.timestamp
        except Exception:
            # Fallback: read latest stored rows without re-running lenses
            latest = get_latest_assessment()
            lenses_raw = latest["lenses"]
            composite_score = latest["composite_score"]
            iw_triggered = latest["iw_triggered"]
            timestamp = latest["timestamp"]
    except ImportError:
        # SIOEngine not available — return mock data so the page still renders
        lenses_raw = {
            "threat_posture":    {"score": 5.0, "nato_reliability": "C3", "narrative": "Mock data — SIOEngine not available.", "confidence": 0.5},
            "behavior_pattern":  {"score": 4.5, "nato_reliability": "C3", "narrative": "Mock data — SIOEngine not available.", "confidence": 0.5},
            "intent_assessment": {"score": 5.5, "nato_reliability": "C3", "narrative": "Mock data — SIOEngine not available.", "confidence": 0.5},
            "convergence":       {"score": 6.0, "nato_reliability": "C3", "narrative": "Mock data — SIOEngine not available.", "confidence": 0.5},
        }
        composite_score = 5.25
        iw_triggered = False
        timestamp = datetime.now(timezone.utc).isoformat()

    # ── Build lean lenses dict for the API response ─────────────────────────
    lenses_out = {}
    for lens_name, lens_data in lenses_raw.items():
        lenses_out[lens_name] = {
            "score": float(lens_data.get("score", 0.0)),
            "nato_reliability": lens_data.get("nato_reliability", "F6"),
            "narrative": lens_data.get("narrative", ""),
        }

    # ── Sparkline: last 14 convergence rows ─────────────────────────────────
    sparkline = []
    try:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT score FROM sg_sio_assessments "
                "WHERE lens_source='convergence' "
                "ORDER BY created_at DESC LIMIT 14"
            ).fetchall()
            sparkline = [float(r[0]) for r in reversed(rows)]
        except Exception:
            pass
        finally:
            conn.close()
    except Exception:
        pass

    if len(sparkline) < 14:
        sparkline = ([0.0] * (14 - len(sparkline))) + sparkline

    return jsonify({
        "composite_score": composite_score,
        "iw_triggered": iw_triggered,
        "lenses": lenses_out,
        "sparkline": sparkline,
        "timestamp": timestamp,
    })


@_api.route("/signals", methods=["GET"])
def api_signals_list():
    rows = _safe_fetch(
        "SELECT p.id, p.composite_score, p.is_read, p.promoted_to_kg, "
        "p.annotation, r.title, r.source, p.created_at "
        "FROM sg_prioritized_signals p "
        "LEFT JOIN sg_raw_signals r ON r.id = p.raw_signal_id "
        "ORDER BY p.composite_score DESC LIMIT 50",
    )
    return jsonify({"signals": rows, "total": len(rows)})


@_api.route("/signals/<int:sig_id>/read", methods=["POST"])
def api_signals_toggle_read(sig_id: int):
    ph = "%s" if is_pg() else "?"
    rows = _safe_fetch(
        f"SELECT is_read FROM sg_prioritized_signals WHERE id = {ph}", (sig_id,)  # nosec B608
    )
    if not rows:
        return jsonify({"error": "not found"}), 404
    current = rows[0].get("is_read", 0)
    new_val = 0 if current else 1
    _update_signal(sig_id, is_read=new_val)
    return jsonify({"id": sig_id, "is_read": bool(new_val)})


@_api.route("/signals/<int:sig_id>/promote", methods=["POST"])
def api_signals_toggle_promote(sig_id: int):
    ph = "%s" if is_pg() else "?"
    rows = _safe_fetch(
        f"SELECT promoted_to_kg FROM sg_prioritized_signals WHERE id = {ph}", (sig_id,)  # nosec B608
    )
    if not rows:
        return jsonify({"error": "not found"}), 404
    current = rows[0].get("promoted_to_kg", 0)
    new_val = 0 if current else 1
    _update_signal(sig_id, promoted_to_kg=new_val)
    return jsonify({"id": sig_id, "promoted_to_kg": bool(new_val)})


@_api.route("/signals/<int:sig_id>/annotate", methods=["POST"])
def api_signals_annotate(sig_id: int):
    body = request.get_json(silent=True) or {}
    text = (body.get("annotation") or "").strip()
    ok = _update_signal(sig_id, annotation=text or None)
    if not ok:
        return jsonify({"error": "not found"}), 404
    return jsonify({"id": sig_id, "annotation": text})


@_api.route("/signals/brief", methods=["POST"])
def api_signals_brief():
    body = request.get_json(silent=True) or {}
    signal_ids = body.get("signal_ids") or []
    if not signal_ids:
        return jsonify({"error": "signal_ids required"}), 400

    ph = "%s" if is_pg() else "?"
    placeholders = ",".join(ph for _ in signal_ids)
    rows = _safe_fetch(
        f"SELECT p.id, p.composite_score, p.rationale, "
        f"r.title, r.body, r.source, r.signal_date "
        f"FROM sg_prioritized_signals p "
        f"LEFT JOIN sg_raw_signals r ON r.id = p.raw_signal_id "
        f"WHERE p.id IN ({placeholders})",  # nosec B608
        signal_ids,
    )

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    iir_lines = [
        "INFORMATION INTELLIGENCE REPORT (IIR) — DRAFT",
        f"DTG: {now_str}",
        f"Source Count: {len(rows)}",
        "",
        "1. SUMMARY OF REPORTING",
    ]
    for i, r in enumerate(rows, 1):
        iir_lines.append(
            f"  ({i}) [{r.get('source', '?')}] {r.get('title', '(no title)')}"
            f" — Score: {round((r.get('composite_score') or 0) * 10, 1)}/10"
        )
    iir_lines += [
        "",
        "2. ASSESSMENT",
        "  [ANALYST NOTE — complete before dissemination]",
        "",
        "3. COLLECTION GAPS IDENTIFIED",
        "  [ANALYST NOTE — cross-check PIR coverage]",
        "",
        "4. RECOMMENDED ACTION",
        "  [ANALYST NOTE]",
    ]
    return jsonify({
        "iir_template": "\n".join(iir_lines),
        "signals": rows,
        "count": len(rows),
    })


@_api.route("/hitl/<item_id>/resolve", methods=["POST"])
def api_hitl_resolve(item_id: str):
    data = request.get_json(silent=True) or {}
    decision = data.get("decision", "").strip()
    if not decision:
        return jsonify({"error": "decision required"}), 400
    conn = get_connection()
    try:
        result = conn.execute(
            "UPDATE sg_hitl_items SET status='resolved', decision=?, "
            "resolved_at=?, resolved_by='analyst' WHERE id=? AND status='pending'",
            (decision, _now(), item_id),
        )
        conn.commit()
        if result.rowcount == 0:
            return jsonify({"error": "item not found or already resolved"}), 404
        return jsonify({"status": "ok", "decision": decision})
    except Exception as exc:
        if "no such table" in str(exc).lower():
            return jsonify({"error": "HITL module not initialised"}), 503
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Analyst Annotation Layer (sg-analyst-06b)
# ---------------------------------------------------------------------------

@_api.route("/annotate", methods=["POST"])
def api_annotate():
    """POST /api/strategos/annotate — add an analyst annotation to any entity.

    Body (JSON):
        entity_type     : one of ENTITY_TYPES
        entity_id       : string ID of the target entity
        annotation_type : one of ANNOTATION_TYPES
        content         : annotation text (max 2 000 chars)
        analyst_id      : optional — defaults to "analyst"
    """
    data = request.get_json(silent=True) or {}
    entity_type = (data.get("entity_type") or "").strip()
    entity_id = (data.get("entity_id") or "").strip()
    annotation_type = (data.get("annotation_type") or "").strip()
    content = (data.get("content") or "").strip()
    analyst_id = (data.get("analyst_id") or "analyst").strip() or "analyst"

    if not entity_type:
        return jsonify({"error": f"entity_type required; valid: {ENTITY_TYPES}"}), 400
    if not entity_id:
        return jsonify({"error": "entity_id required"}), 400
    if not annotation_type:
        return jsonify({"error": f"annotation_type required; valid: {ANNOTATION_TYPES}"}), 400
    if not content:
        return jsonify({"error": "content required"}), 400

    try:
        # Ensure table exists (idempotent — safe to call on every first use)
        import importlib  # noqa: PLC0415
        try:
            mod = importlib.import_module(
                "tools.db.migrations.060_sg_analyst_annotations.up"
            )
            mod.up()
        except Exception:
            pass

        row = add_annotation(
            entity_type=entity_type,
            entity_id=entity_id,
            annotation_type=annotation_type,
            content=content,
            analyst_id=analyst_id,
        )
        return jsonify({"status": "ok", "annotation": row}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/annotations/<entity_type>/<entity_id>", methods=["GET"])
def api_get_annotations(entity_type: str, entity_id: str):
    """GET /api/strategos/annotations/<entity_type>/<entity_id>

    Returns all annotations + a per-type summary for the given entity.
    """
    if entity_type not in ENTITY_TYPES:
        return jsonify({"error": f"entity_type must be one of {ENTITY_TYPES}"}), 400

    try:
        annotations = get_annotations(entity_type, entity_id)
        summary = get_annotation_summary(entity_type, entity_id)
        return jsonify({"annotations": annotations, "summary": summary})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Simulate endpoints
# ---------------------------------------------------------------------------

@_api.route("/simulate/nodes", methods=["GET"])
def api_simulate_nodes():
    """GET /api/strategos/simulate/nodes — supply chain nodes with lat/lon."""
    rows = _safe_fetch(
        "SELECT node_id, label, node_type, criticality, lat, lon "
        "FROM sg_supply_nodes WHERE lat IS NOT NULL AND lon IS NOT NULL",
    )
    return jsonify({"nodes": rows, "total": len(rows)})


@_api.route("/simulate/run", methods=["POST"])
def api_simulate_run():
    """POST /api/strategos/simulate/run — deterministic or Monte Carlo.

    Body: {
        "events": [
            {
                "node_id": str,
                "node_type": str,
                "label": str,
                "destruction_pct": int,
                "repair_timeline_days": int,
                "timestamp_offset_days": int
            }
        ],
        "mode": "deterministic" | "monte_carlo",
        "iterations": int  (Monte Carlo only, default 100)
    }
    """
    data = request.get_json(silent=True) or {}
    events = data.get("events", [])
    mode = data.get("mode", "deterministic")
    iterations = max(10, min(int(data.get("iterations", 100)), 1000))

    if not events:
        return jsonify({"error": "No events provided"}), 400

    # Fetch total node count for network disruption ratio
    total_nodes = 1
    try:
        conn = get_connection()
        try:
            row = conn.execute("SELECT COUNT(*) FROM sg_supply_nodes").fetchone()
            total_nodes = max(1, row[0])
        except Exception:
            pass
        finally:
            conn.close()
    except Exception:
        pass

    _DEFAULT_PROFILE = {"lag_mean": 7, "lag_std": 3, "lag_weight": 0.70}

    def _compute_cascade(events_list, noise: bool = False) -> list[dict]:
        effects = []
        for ev in events_list:
            node_type = ev.get("node_type", "depot")
            profile = NODE_DISRUPTION_PROFILES.get(node_type, _DEFAULT_PROFILE)
            lag_weight = profile.get("lag_weight", 0.70)
            lag_mean = profile.get("lag_mean", 7)
            lag_std = profile.get("lag_std", 3)
            offset = int(ev.get("timestamp_offset_days", 0))
            destruction = int(ev.get("destruction_pct", 50)) / 100.0

            if noise:
                # Box-Muller Gaussian sample
                u1 = max(1e-9, random.random())
                u2 = random.random()
                z = math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
                sampled_lag = max(0, lag_mean + lag_std * z)
            else:
                sampled_lag = float(lag_mean)

            effective_degradation = destruction * lag_weight
            onset_day = offset + sampled_lag
            effects.append({
                "node_id":    ev.get("node_id", ""),
                "node_type":  node_type,
                "label":      ev.get("label", ev.get("node_id", "")),
                "degradation":    round(effective_degradation, 4),
                "onset_day":      round(onset_day, 1),
                "repair_days":    int(ev.get("repair_timeline_days", lag_mean)),
            })
        return effects

    if mode == "deterministic":
        cascade = _compute_cascade(events, noise=False)
        degradations = [c["degradation"] for c in cascade]
        onset_days   = [c["onset_day"]   for c in cascade]
        mean_deg     = sum(degradations) / len(degradations) if degradations else 0.0
        mean_onset   = sum(onset_days)   / len(onset_days)   if onset_days   else 0.0
        net_dis      = len(events) / total_nodes

        summary = {
            "mean_degradation":      round(mean_deg, 4),
            "mean_onset_days":       round(mean_onset, 2),
            "network_disruption_pct": round(min(1.0, net_dis), 4),
        }
        return jsonify({
            "mode":             "deterministic",
            "events_processed": len(events),
            "summary":          summary,
            "cascade_effects":  cascade,
        })

    else:  # monte_carlo
        all_mean_degs = []
        all_onset_days = []
        for _ in range(iterations):
            cascade_i = _compute_cascade(events, noise=True)
            iter_degs = [c["degradation"] for c in cascade_i]
            iter_onset = [c["onset_day"] for c in cascade_i]
            if iter_degs:
                all_mean_degs.append(sum(iter_degs) / len(iter_degs))
            if iter_onset:
                all_onset_days.append(sum(iter_onset) / len(iter_onset))

        def _percentile(lst, p):
            if not lst:
                return 0.0
            s = sorted(lst)
            idx = int(math.ceil(p / 100.0 * len(s))) - 1
            return s[max(0, idx)]

        mean_deg   = sum(all_mean_degs) / len(all_mean_degs) if all_mean_degs else 0.0
        mean_onset = sum(all_onset_days) / len(all_onset_days) if all_onset_days else 0.0
        p10 = _percentile(all_mean_degs, 10)
        p90 = _percentile(all_mean_degs, 90)
        net_dis = len(events) / total_nodes

        # Deterministic cascade for display purposes
        cascade_det = _compute_cascade(events, noise=False)

        summary = {
            "mean_degradation":       round(mean_deg, 4),
            "mean_onset_days":        round(mean_onset, 2),
            "network_disruption_pct": round(min(1.0, net_dis), 4),
            "p10_degradation":        round(p10, 4),
            "p90_degradation":        round(p90, 4),
        }
        return jsonify({
            "mode":             "monte_carlo",
            "iterations":       iterations,
            "events_processed": len(events),
            "summary":          summary,
            "cascade_effects":  cascade_det,
        })


# ---------------------------------------------------------------------------
# Wargame API routes
# ---------------------------------------------------------------------------

def _ensure_ooda_tables() -> None:
    try:
        import importlib
        mod = importlib.import_module("tools.db.migrations.062_sg_ooda.up")
        mod.up()
    except Exception:
        pass


@_api.route("/wargame", methods=["GET"])
def api_wargame_list():
    rows = _safe_fetch("SELECT * FROM sg_wargames ORDER BY created_at DESC LIMIT 100")
    return jsonify({"wargames": rows, "total": len(rows)})


@_api.route("/wargame", methods=["POST"])
def api_wargame_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    gid = str(uuid.uuid4())
    ph  = "%s" if is_pg() else "?"
    coeffs = json.dumps({
        "blue_strength": float(data.get("blue_strength", 1000)),
        "red_strength":  float(data.get("red_strength",  800)),
        "beta": float(data.get("beta", 0.01)),
        "rho":  float(data.get("rho",  0.01)),
    })
    conn = get_connection()
    try:
        conn.execute(
            f"INSERT INTO sg_wargames (id, name, scenario, state, blue_force, red_force, "
            f"attrition_coefficients_json) VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})",  # nosec B608
            (
                gid,
                name,
                data.get("scenario", ""),
                data.get("state", "pending"),
                data.get("blue_force", ""),
                data.get("red_force", ""),
                coeffs,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"id": gid, "name": name}), 201


@_api.route("/wargame/<wargame_id>/ooda", methods=["GET"])
def api_wargame_ooda(wargame_id: str):
    try:
        result = compute_tempo(wargame_id)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/wargame/<wargame_id>/lanchester", methods=["GET"])
def api_wargame_lanchester(wargame_id: str):
    model  = request.args.get("model", "square")
    b0     = float(request.args.get("b0",   1000))
    r0     = float(request.args.get("r0",   800))
    beta   = float(request.args.get("beta", 0.01))
    rho    = float(request.args.get("rho",  0.01))
    # Pull coefficients from DB if available
    rows = _safe_fetch(
        "SELECT attrition_coefficients_json FROM sg_wargames WHERE id = ?",
        (wargame_id,),
    )
    if rows and rows[0].get("attrition_coefficients_json"):
        try:
            c = json.loads(rows[0]["attrition_coefficients_json"])
            b0   = float(c.get("blue_strength", b0))
            r0   = float(c.get("red_strength",  r0))
            beta = float(c.get("beta", beta))
            rho  = float(c.get("rho",  rho))
        except Exception:
            pass

    try:
        fn = lanchester_linear if model == "linear" else lanchester_square
        result = fn(b0, r0, beta, rho)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/wargame/<wargame_id>/blotto", methods=["POST"])
def api_wargame_blotto(wargame_id: str):
    data = request.get_json(silent=True) or {}
    blue_forces = data.get("blue_forces")
    red_forces  = data.get("red_forces")
    if not blue_forces or not red_forces:
        return jsonify({"error": "blue_forces and red_forces arrays required"}), 400
    try:
        result = blotto_expected_payoff(
            [float(x) for x in blue_forces],
            [float(x) for x in red_forces],
        )
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/wargame/<wargame_id>/nash", methods=["POST"])
def api_wargame_nash(wargame_id: str):
    data = request.get_json(silent=True) or {}
    payoff_blue = data.get("payoff_blue")
    payoff_red  = data.get("payoff_red")
    strategy_names = data.get("strategy_names")
    if not payoff_blue or not payoff_red:
        return jsonify({"error": "payoff_blue and payoff_red matrices required"}), 400
    try:
        result = find_nash_2x2(payoff_blue, payoff_red, strategy_names)
        # Persist
        _ensure_ooda_tables()
        nid = str(uuid.uuid4())
        ph  = "%s" if is_pg() else "?"
        conn = get_connection()
        try:
            conn.execute(
                f"INSERT INTO sg_nash_scenarios "
                f"(id, wargame_id, name, blue_strategies, red_strategies, "
                f"payoff_blue, payoff_red, pure_equilibria, mixed_equilibrium) "
                f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",  # nosec B608
                (
                    nid,
                    wargame_id,
                    data.get("name", "Nash Scenario"),
                    json.dumps((strategy_names or [[], []])[0]),
                    json.dumps((strategy_names or [[], []])[1]),
                    json.dumps(payoff_blue),
                    json.dumps(payoff_red),
                    json.dumps(result.get("pure_equilibria", [])),
                    json.dumps(result.get("mixed_equilibrium")),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        result["id"] = nid
        return jsonify(result), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/wargame/<wargame_id>/coa", methods=["GET"])
def api_wargame_coa_list(wargame_id: str):
    ph = "%s" if is_pg() else "?"
    rows = _safe_fetch(
        f"SELECT * FROM sg_coa_entries WHERE wargame_id = {ph} ORDER BY composite_score DESC",  # nosec B608
        (wargame_id,),
    )
    return jsonify({"coas": rows, "total": len(rows)})


@_api.route("/wargame/<wargame_id>/coa", methods=["POST"])
def api_wargame_coa_create(wargame_id: str):
    _ensure_ooda_tables()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    side = (data.get("side") or "blue").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    if side not in ("blue", "red"):
        return jsonify({"error": "side must be blue or red"}), 400

    criteria_vals = {c: float(data.get(c, 0.5)) for c in COA_CRITERIA}
    scored = score_coa([{"name": name, **criteria_vals}])
    composite = scored[0]["composite_score"] if scored else 0.5

    cid = str(uuid.uuid4())
    ph  = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        conn.execute(
            f"INSERT INTO sg_coa_entries "
            f"(id, wargame_id, side, name, description, speed, surprise, mass, "
            f"economy_of_force, maneuver, sustainability, composite_score) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",  # nosec B608
            (
                cid, wargame_id, side, name,
                data.get("description", ""),
                criteria_vals["speed"],
                criteria_vals["surprise"],
                criteria_vals["mass"],
                criteria_vals["economy_of_force"],
                criteria_vals["maneuver"],
                criteria_vals["sustainability"],
                composite,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"id": cid, "name": name, "composite_score": composite}), 201


# ---------------------------------------------------------------------------
# Info endpoints
# ---------------------------------------------------------------------------

@_api.route("/info/network-data", methods=["GET"])
def get_info_network_data():
    """Return narrative cluster nodes and amplification link edges for network viz."""
    nodes = [
        {"id": "nc-1", "label": "State-Sponsored Disinformation", "size": 42},
        {"id": "nc-2", "label": "Domestic Radicalization Narrative", "size": 35},
        {"id": "nc-3", "label": "Economic Grievance Amplification", "size": 28},
        {"id": "nc-4", "label": "Anti-Alliance Messaging", "size": 31},
        {"id": "nc-5", "label": "Election Integrity Doubt", "size": 39},
        {"id": "nc-6", "label": "Military Casualty Inflation", "size": 22},
    ]
    links = [
        {"source": "nc-1", "target": "nc-2", "strength": 0.82},
        {"source": "nc-1", "target": "nc-5", "strength": 0.74},
        {"source": "nc-2", "target": "nc-3", "strength": 0.61},
        {"source": "nc-3", "target": "nc-4", "strength": 0.55},
        {"source": "nc-4", "target": "nc-5", "strength": 0.68},
        {"source": "nc-1", "target": "nc-6", "strength": 0.47},
        {"source": "nc-5", "target": "nc-6", "strength": 0.53},
    ]
    return jsonify({"nodes": nodes, "links": links})


# ---------------------------------------------------------------------------
# Factory functions called from tools/dashboard/app.py
# ---------------------------------------------------------------------------

def create_strategos_blueprint():
    """Return the page blueprint (mounted at /strategos)."""
    return _bp


def create_strategos_api_blueprint():
    """Return the API blueprint (mounted at /api/strategos)."""
    return _api
