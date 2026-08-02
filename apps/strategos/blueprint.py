#!/usr/bin/env python3
from __future__ import annotations
# CUI // SP-CTI
# reload-trigger: 2026-04-30
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

from flask import Blueprint, flash, g, jsonify, make_response, redirect, render_template, request, Response, url_for

from tools.dashboard.auth import require_role
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


def _strategos_tenant_id() -> str | None:
    try:
        from tools.saas.auth.middleware import get_current_tenant_id
        return get_current_tenant_id()
    except Exception:
        return None


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
# RBAC (nav-sec-05)
# ---------------------------------------------------------------------------

# Approval / resolution / workflow-advance / authoritative-deletion class of
# state-changing routes (HITL decisions, OPORD approve+delete, F3EAD
# advance/set-phase/delete, brief approval, CCIR trigger resolution) must be
# restricted to approver-class roles — a lowest-privilege ``developer`` (whose
# own product may be under review) must not be able to approve, resolve,
# advance, or delete authoritative operational state. Mirrors the
# ``_WF_APPROVAL_ROLES`` convention established for the HITL governance gates
# in tools/workflow_hitl/blueprint.py (nav-sec-04). Every role listed here also
# appears in ``VALID_DASHBOARD_ROLES`` in tools/dashboard/auth.py.
_STRATEGOS_APPROVAL_ROLES = ("admin", "pm", "isso", "co", "cor", "reviewer")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _actor_id() -> str:
    """Resolved approver/actor identity for audit fields (nav-sec-05).

    Always sourced from the authenticated session user (``g.current_user``),
    never from a spoofable request-body field. Falls back to ``"unknown"`` only
    if no user is on the request context (which the ``@require_role`` gate
    already prevents for the routes that call this).
    """
    user = getattr(g, "current_user", None)
    if isinstance(user, dict):
        return str(user.get("id") or user.get("email") or "unknown")
    if user is not None:
        try:
            return str(user["id"])
        except Exception:
            return "unknown"
    return "unknown"


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

    # Extract scenario coefficients — used for both Lanchester and OODA scaling
    scenario_coeffs: dict = {}
    if active_game:
        try:
            raw = (
                active_game.get("attrition_coefficients_json")
                or active_game.get("outcome")
                or "{}"
            )
            scenario_coeffs = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            scenario_coeffs = {}

    b0   = float(scenario_coeffs.get("blue_strength", 1000))
    r0   = float(scenario_coeffs.get("red_strength",  800))
    beta = float(scenario_coeffs.get("beta", 0.01))
    rho  = float(scenario_coeffs.get("rho",  0.01))

    # OODA tempo — scale baselines per scenario force levels so each scenario differs
    ooda = compute_tempo(wargame_id)
    if active_game and scenario_coeffs:
        try:
            # More forces → more C2 complexity → slower cycles (power-law, mild)
            blue_ooda_scale = (b0 / 1000.0) ** 0.2
            red_ooda_scale  = (r0 / 800.0)  ** 0.2
            # Higher attrition coeff → better targeting intel → faster cyber/EW observe+orient
            cyber_scale_blue = max(0.7, 1.0 / (beta / 0.01) ** 0.15) if beta > 0 else 1.0
            cyber_scale_red  = max(0.7, 1.0 / (rho  / 0.01) ** 0.15) if rho  > 0 else 1.0

            phase_keys = (
                "observe_latency_s", "orient_latency_s",
                "decide_latency_s",  "act_latency_s",
            )
            for domain, ddata in ooda["blue"].items():
                scale = blue_ooda_scale * (
                    cyber_scale_blue if domain in ("cyber", "ew") else 1.0
                )
                for pk in phase_keys:
                    ddata[pk] = round(ddata[pk] * scale, 1)
                ddata["cycle_time_s"] = round(sum(ddata[pk] for pk in phase_keys), 1)
                ddata["command_interval_s"] = round(ddata["cycle_time_s"] * 1.5, 1)

            for domain, ddata in ooda["red"].items():
                scale = red_ooda_scale * (
                    cyber_scale_red if domain in ("cyber", "ew") else 1.0
                )
                for pk in phase_keys:
                    ddata[pk] = round(ddata[pk] * scale, 1)
                ddata["cycle_time_s"] = round(sum(ddata[pk] for pk in phase_keys), 1)
                ddata["command_interval_s"] = round(ddata["cycle_time_s"] * 1.5, 1)

            # Recompute tempo scores and radar after scaling
            all_cycles = (
                [d["cycle_time_s"] for d in ooda["blue"].values()]
                + [d["cycle_time_s"] for d in ooda["red"].values()]
            )
            max_cycle = max(all_cycles) if all_cycles else 1.0
            advantage: dict = {}
            for domain in ooda["domains"]:
                for side in ("blue", "red"):
                    c = ooda[side][domain]["cycle_time_s"]
                    ooda[side][domain]["tempo_score"] = (
                        round(1.0 - min(c / max_cycle, 1.0), 4)
                        if max_cycle > 0 else 0.5
                    )
                b_s = ooda["blue"][domain]["tempo_score"]
                r_s = ooda["red"][domain]["tempo_score"]
                if abs(b_s - r_s) < 0.05:
                    advantage[domain] = "parity"
                elif b_s > r_s:
                    advantage[domain] = "blue"
                else:
                    advantage[domain] = "red"
            ooda["advantage"] = advantage
            ooda["radar_data"]["blue"] = [
                round(ooda["blue"][d]["tempo_score"] * 100, 1) for d in ooda["domains"]
            ]
            ooda["radar_data"]["red"] = [
                round(ooda["red"][d]["tempo_score"] * 100, 1) for d in ooda["domains"]
            ]
        except Exception:
            pass  # fall back to unscaled baseline

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

    # Lanchester attrition curves
    try:
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
@require_role(*_STRATEGOS_APPROVAL_ROLES)
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


@_bp.route("/supply")
def strategos_supply():
    import json as _supply_json

    rows = _safe_fetch(
        "SELECT node_id, label, node_type, criticality, substitutability, "
        "controlled_by, lat, lon, metadata_json FROM sg_supply_nodes "
        "WHERE lat IS NOT NULL AND lon IS NOT NULL ORDER BY substitutability LIMIT 300"
    )

    _CRIT_BASE = {"critical": 0.92, "high": 0.78, "medium": 0.55, "low": 0.32}

    def _to_side(cb: str) -> str:
        s = (cb or "").lower()
        if any(x in s for x in ("russia", " rf", "soviet")):
            return "russia"
        if any(x in s for x in ("ukraine", "ukr")):
            return "ukraine"
        if "iran" in s:
            return "iran"
        return "nato_partner"

    nodes = []
    for r in rows:
        sub  = float(r.get("substitutability") or 0.5)
        crit = (r.get("criticality") or "medium").lower()
        blast = round(_CRIT_BASE.get(crit, 0.55) * (1.0 - sub * 0.3), 3)
        meta  = {}
        try:
            meta = _supply_json.loads(r.get("metadata_json") or "{}")
        except Exception:
            pass
        nodes.append({
            "id":               r["node_id"],
            "node_name":        r["label"],
            "node_type":        r["node_type"],
            "criticality":      crit,
            "blast_radius":     blast,
            "side":             _to_side(r.get("controlled_by") or ""),
            "country":          r.get("controlled_by") or "",
            "lat":              r["lat"],
            "lon":              r["lon"],
            "systems_produced": meta.get("systems_produced", []),
            "notes":            meta.get("notes", ""),
        })

    critical_path = sorted(nodes, key=lambda n: n["blast_radius"], reverse=True)
    for i, n in enumerate(critical_path, 1):
        n["rank"] = i

    edge_rows = _safe_fetch(
        "SELECT source_id, target_id, relation FROM sg_kg_edges "
        "WHERE relation IN ('PRODUCES', 'DEPENDS_ON_SUPPLY') LIMIT 500"
    )
    edges = [
        {
            "source_node_id": e["source_id"],
            "target_node_id": e["target_id"],
            "edge_type":      e["relation"],
            "system":         "",
            "throughput_pct": 0.8,
        }
        for e in edge_rows
    ]

    return render_template(
        "strategos/supply.html",
        nodes=nodes,
        edges=edges,
        nodes_json=_supply_json.dumps(nodes),
        edges_json=_supply_json.dumps(edges),
        critical_path=critical_path[:25],
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
        # nav-sec-08: brief content is analyst/LLM-authored — markdown passes raw
        # HTML through, so sanitize the rendered output before it is emitted via
        # {{ content_html | safe }} (fail closed on sanitizer error).
        try:
            from tools.docgen.workflow import _sanitize_html
            content_html = _sanitize_html(content_html)
        except Exception:
            import html as _html
            content_html = _html.escape(content_html or "")
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


@_api.route("/maritime/feed/status")
def api_maritime_feed_status():
    """Return live feed status: {live: bool, mode: str, vessel_count: int}."""
    import os
    have_sdr = os.getenv("HAVE_SDR_HARDWARE", "false").lower() in ("1", "true", "yes")
    mode = "sdr" if have_sdr else "aishub"
    # live if any vessel track recorded in last 24 hours
    # ts column is TEXT; PG needs explicit cast to compare with interval expression
    _ts_expr = "ts::timestamptz >= NOW() - INTERVAL '24 hours'" if is_pg() else "ts >= datetime('now','-24 hours')"
    row = _safe_fetch(
        f"SELECT COUNT(*) AS cnt FROM sg_vessel_tracks WHERE {_ts_expr}",  # nosec: B608
        default=[],
    )
    recent_count = row[0]["cnt"] if row else 0
    total_row = _safe_fetch("SELECT COUNT(*) AS cnt FROM sg_vessel_tracks", default=[])
    total_count = total_row[0]["cnt"] if total_row else 0
    return jsonify({
        "live": recent_count > 0 or have_sdr,
        "mode": mode,
        "vessel_count": total_count,
        "recent_24h": recent_count,
    })


@_api.route("/maritime/tracks")
def api_maritime_tracks():
    """Return all vessel tracks grouped by MMSI for animation."""
    rows = _safe_fetch(
        "SELECT mmsi, vessel_name, vessel_type, flag, lat, lon, speed, heading, ts, "
        "COALESCE(source, 'aishub') AS source "
        "FROM sg_vessel_tracks ORDER BY mmsi, ts ASC"
    )
    vessels: dict = {}
    for r in rows:
        m = r["mmsi"]
        if m not in vessels:
            vessels[m] = {
                "mmsi":        m,
                "name":        r["vessel_name"],
                "vessel_type": r["vessel_type"],
                "flag":        r["flag"] or "??",
                "source":      r["source"],
                "track":       [],
            }
        vessels[m]["track"].append({
            "lat":     r["lat"],
            "lon":     r["lon"],
            "speed":   r["speed"],
            "heading": r["heading"],
            "ts":      r["ts"],
        })
    return jsonify({"vessels": list(vessels.values())})


# ---------------------------------------------------------------------------
# Multi-Domain Operations — Airspace COP
# ---------------------------------------------------------------------------


@_bp.route("/airspace")
@_bp.route("/airspace/")
def strategos_airspace():
    return render_template("strategos/airspace.html")


@_api.route("/airspace/aircraft")
def api_airspace_aircraft():
    """Return aircraft tracks grouped by ICAO24 for animation."""
    rows = _safe_fetch(
        "SELECT icao24, callsign, origin_country, aircraft_type, military_flag, "
        "lat, lon, baro_altitude, velocity, true_track, track_ts, source "
        "FROM sg_aircraft_tracks ORDER BY icao24, track_ts ASC",
        default=[],
    )
    aircraft: dict = {}
    for r in rows:
        ic = r["icao24"]
        if ic not in aircraft:
            aircraft[ic] = {
                "icao24":          ic,
                "callsign":        r["callsign"] or ic.upper(),
                "origin_country":  r["origin_country"] or "?",
                "aircraft_type":   r["aircraft_type"] or "unknown",
                "military_flag":   bool(r.get("military_flag")),
                "source":          r["source"] or "opensky",
                "track":           [],
            }
        aircraft[ic]["track"].append({
            "lat":       r["lat"],
            "lon":       r["lon"],
            "altitude":  r["baro_altitude"],
            "velocity":  r["velocity"],
            "heading":   r["true_track"],
            "ts":        r["track_ts"],
        })
    return jsonify({"aircraft": list(aircraft.values()), "total": len(aircraft)})


@_api.route("/airspace/uas")
def api_airspace_uas():
    """Return UAS/drone tracks grouped by uas_id."""
    rows = _safe_fetch(
        "SELECT uas_id, operator, uas_type, operator_country, threat_level, "
        "payload, lat, lon, altitude_m, speed_kts, heading, anomaly_flag, "
        "track_ts, source "
        "FROM sg_uas_tracks ORDER BY uas_id, track_ts ASC",
        default=[],
    )
    uas: dict = {}
    for r in rows:
        uid = r["uas_id"]
        if uid not in uas:
            uas[uid] = {
                "uas_id":           uid,
                "operator":         r["operator"] or "Unknown",
                "uas_type":         r["uas_type"] or "unknown",
                "operator_country": r["operator_country"] or "?",
                "threat_level":     r["threat_level"] or "low",
                "payload":          r["payload"] or "",
                "anomaly_flag":     bool(r.get("anomaly_flag")),
                "source":           r["source"] or "analyst",
                "track":            [],
            }
        uas[uid]["track"].append({
            "lat":       r["lat"],
            "lon":       r["lon"],
            "altitude":  r["altitude_m"],
            "speed":     r["speed_kts"],
            "heading":   r["heading"],
            "ts":        r["track_ts"],
        })
    return jsonify({"uas": list(uas.values()), "total": len(uas)})


@_api.route("/airspace/satellites")
def api_airspace_satellites():
    """Return satellite passes with ground tracks."""
    rows = _safe_fetch(
        "SELECT norad_id, sat_name, sat_type, max_elevation, pass_start, pass_end, "
        "ground_track_json, military_flag, source "
        "FROM sg_satellite_passes ORDER BY pass_start DESC",
        default=[],
    )
    import json as _json
    sats = []
    for r in rows:
        try:
            track = _json.loads(r.get("ground_track_json") or "[]")
        except Exception:
            track = []
        sats.append({
            "norad_id":      r["norad_id"],
            "sat_name":      r["sat_name"] or r["norad_id"],
            "sat_type":      r["sat_type"] or "other",
            "max_elevation": r["max_elevation"],
            "pass_start":    r["pass_start"],
            "pass_end":      r["pass_end"],
            "ground_track":  track,
            "military_flag": bool(r.get("military_flag")),
            "source":        r["source"] or "celestrak",
        })
    return jsonify({"satellites": sats, "total": len(sats)})


@_api.route("/airspace/ground-vehicles")
def api_airspace_ground_vehicles():
    """Return ground vehicle events as point markers."""
    country = request.args.get("country")
    threat  = request.args.get("threat_level")
    where_parts = []
    params: list = []
    ph = "%s" if is_pg() else "?"
    if country:
        where_parts.append(f"LOWER(country) = {ph}")
        params.append(country.lower())
    if threat:
        where_parts.append(f"threat_level = {ph}")
        params.append(threat)
    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    rows = _safe_fetch(
        f"SELECT event_id, vehicle_type, lat, lon, country, actor, "  # nosec B608
        f"description, event_ts, threat_level, source "
        f"FROM sg_ground_vehicle_events {where_sql} "
        f"ORDER BY event_ts DESC LIMIT 500",
        params or (),
        default=[],
    )
    events = [dict(r) for r in rows]
    return jsonify({"events": events, "total": len(events)})


@_api.route("/airspace/sync", methods=["POST"])
def api_airspace_sync():
    """Trigger demo data seeding for all 4 airspace domains."""
    results: dict = {}
    try:
        from tools.strategos.adsb_importer import seed_demo_data as seed_ac
        results["aircraft"] = seed_ac()
    except Exception as exc:
        results["aircraft"] = {"error": str(exc)}
    try:
        from tools.strategos.tle_importer import seed_demo_data as seed_sat
        results["satellites"] = seed_sat()
    except Exception as exc:
        results["satellites"] = {"error": str(exc)}
    try:
        from tools.strategos.uas_importer import seed_demo_data as seed_uas
        results["uas"] = seed_uas()
    except Exception as exc:
        results["uas"] = {"error": str(exc)}
    try:
        from tools.strategos.ground_vehicle_importer import seed_demo_data as seed_gv
        results["ground_vehicles"] = seed_gv()
    except Exception as exc:
        results["ground_vehicles"] = {"error": str(exc)}
    resp = make_response(jsonify({"status": "ok", "seeded": results}))
    resp.headers["X-Classification"] = "CUI"
    return resp


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


@_api.route("/iw/scored", methods=["POST"])
def api_iw_scored():
    """POST /api/strategos/iw/scored — Score all PMESII-PT domains via iw_scorers and compute WRI."""
    body = request.get_json(silent=True) or {}
    theater_id = body.get("theater_id", request.args.get("theater_id", "ukraine"))
    window_days = int(body.get("window_days", request.args.get("window_days", 30)))
    try:
        from tools.strategos.iw_scorers import score_all_domains  # noqa: PLC0415
        from tools.strategos.iw_engine import PMESIIPTCompositor  # noqa: PLC0415
        domain_scores = score_all_domains(theater_id, window_days)
        comp = PMESIIPTCompositor()
        dims = domain_scores["domains"]
        wri_result = comp.compute_and_save({
            "political": dims.get("political", 0.0),
            "military": dims.get("military", 0.0),
            "economic": dims.get("economic", 0.0),
            "social": 0.0,
            "information": dims.get("information", 0.0),
            "infrastructure": dims.get("infrastructure", 0.0),
            "physical_environment": 0.0,
            "time": 0.0,
        }, source="iw_scorers")
        return jsonify({
            "theater_id": theater_id,
            "window_days": window_days,
            "domain_scores": domain_scores,
            "wri": wri_result,
            "radar_data": {
                "labels": ["Military", "Economic", "Political", "Information", "Infrastructure"],
                "values": [
                    dims.get("military", 0.0),
                    dims.get("economic", 0.0),
                    dims.get("political", 0.0),
                    dims.get("information", 0.0),
                    dims.get("infrastructure", 0.0),
                ],
            },
        }), 200
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
        cutoff = day_labels[0]  # earliest date in range (YYYY-MM-DD string)
        ph = "%s" if is_pg() else "?"
        # Cross-DB timestamp→date cast
        dt_cast = "created_at::date" if is_pg() else "DATE(created_at)"

        counts: dict[str, dict[str, int]] = {cat: {d: 0 for d in day_labels} for cat in _CATEGORIES}

        # IW effects
        iw_dt = "detected_at::date" if is_pg() else "DATE(detected_at)"
        try:
            rows = conn.execute(
                f"SELECT effect_type, {iw_dt} AS d, COUNT(*) AS n "  # nosec B608
                f"FROM sg_iw_effects WHERE {iw_dt} >= {ph} "
                f"GROUP BY effect_type, {iw_dt}",
                (cutoff,),
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
                f"SELECT event_type, {dt_cast} AS d, COUNT(*) AS n "  # nosec B608
                f"FROM sg_conflict_events WHERE {dt_cast} >= {ph} "
                f"GROUP BY event_type, {dt_cast}",
                (cutoff,),
            ).fetchall()
            for row in rows:
                cat = _CE_CAT_MAP.get((row[0] or "").lower(), "military")
                day = str(row[1] or "")[:10]
                if day in day_index:
                    counts[cat][day] = counts[cat].get(day, 0) + int(row[2] or 0)
        except Exception:
            pass

        # Prioritized signals by date (signal_date is a plain DATE column)
        try:
            rows = conn.execute(
                f"SELECT r.signal_date, COUNT(*) AS n "  # nosec: B608
                f"FROM sg_prioritized_signals p "
                f"LEFT JOIN sg_raw_signals r ON r.id = p.raw_signal_id "
                f"WHERE r.signal_date >= {ph} "
                f"GROUP BY r.signal_date",
                (cutoff,),
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

    def _demo_curve(n_rows: int) -> dict:
        import math  # noqa: PLC0415
        times = list(range(0, 31))
        survival = [round(math.exp(-0.04 * t), 4) for t in times]
        return {"times": times, "survival": survival, "n": n_rows, "threshold": threshold, "demo": True}

    if not rows:
        return jsonify(_demo_curve(0))

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
        return jsonify(_demo_curve(0))

    max_t = max(s["t"] for s in segments) or 24.0
    # Fall back to demo curve when data is too sparse for a meaningful plot
    if len(segments) < 10 or max_t < 2.0:
        return jsonify(_demo_curve(len(rows)))

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


@_api.route("/sigint/events", methods=["GET"])
def api_sigint_events():
    try:
        rows = _safe_fetch(
            "SELECT * FROM sg_sigint_events ORDER BY detected_at DESC LIMIT 200"
        )
        return jsonify(rows)
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


@_api.route("/supply/sync", methods=["POST"])
def api_supply_sync():
    """Write sg_supply_nodes into sg_kg_nodes and re-apply supply edges to sg_kg_edges."""
    ph = "%s" if is_pg() else "?"
    rows = _safe_fetch(
        "SELECT node_id, label, node_type FROM sg_supply_nodes WHERE lat IS NOT NULL AND lon IS NOT NULL"
    )
    # Static logistics topology — mirrors seed_supply_kg_edges.py
    _SUPPLY_EDGES = [
        ("depot-sasebo",      "port-yokosuka",    "PRODUCES"),
        ("port-yokosuka",     "depot-camp-zama",  "DEPENDS_ON_SUPPLY"),
        ("depot-camp-zama",   "airfield-kadena",  "PRODUCES"),
        ("port-okinawa",      "airfield-kadena",  "DEPENDS_ON_SUPPLY"),
        ("port-guam",         "airfield-andersen","DEPENDS_ON_SUPPLY"),
        ("depot-red-hill",    "airfield-andersen","PRODUCES"),
        ("port-busan",        "airfield-osan",    "DEPENDS_ON_SUPPLY"),
        ("airfield-misawa",   "port-yokosuka",    "DEPENDS_ON_SUPPLY"),
        ("port-keelung",      "port-kaohsiung",   "DEPENDS_ON_SUPPLY"),
        ("port-novorossiysk", "depot-crimea",     "PRODUCES"),
        ("depot-crimea",      "airfield-saki",    "PRODUCES"),
        ("port-qingdao",      "port-zhoushan",    "PRODUCES"),
        ("port-zhoushan",     "airfield-longhua", "DEPENDS_ON_SUPPLY"),
    ]
    conn = get_connection()
    nodes_written = 0
    edges_written = 0
    try:
        for r in rows:
            try:
                conn.execute(
                    f"INSERT INTO sg_kg_nodes (node_id, node_type, label) "  # nosec B608
                    f"VALUES ({ph},{ph},{ph}) ON CONFLICT (node_id) DO NOTHING",
                    (r["node_id"], r["node_type"], r["label"]),
                )
                nodes_written += 1
            except Exception:
                pass
        for src, tgt, rel in _SUPPLY_EDGES:
            existing = conn.execute(
                f"SELECT 1 FROM sg_kg_edges WHERE source_id={ph} AND target_id={ph} AND relation={ph}",  # nosec B608
                (src, tgt, rel),
            ).fetchone()
            if not existing:
                try:
                    conn.execute(
                        f"INSERT INTO sg_kg_edges (source_id, target_id, relation, weight) VALUES ({ph},{ph},{ph},{ph})",  # nosec B608
                        (src, tgt, rel, 1.0),
                    )
                    edges_written += 1
                except Exception:
                    pass
        conn.commit()
    finally:
        conn.close()
    return jsonify({"graph": {"nodes_written": nodes_written}, "kg": {"kg_edges": edges_written}})


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
@require_role(*_STRATEGOS_APPROVAL_ROLES)
def api_briefs_approve(brief_id: str):
    data = request.get_json(silent=True) or {}
    # nav-sec-05: reviewer identity is the authenticated user, never the body.
    reviewed_by = _actor_id()
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
    except ImportError as exc:
        # SIOEngine not available — return an explicit degraded state rather than
        # fabricated scores. Emitting mock lens/composite numbers would render on
        # /strategos/oracle as a real assessment, misleading the analyst.
        return jsonify({
            "available": False,
            "reason": f"SIO assessment engine unavailable: {exc}",
        }), 503

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
        "available": True,
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
@require_role(*_STRATEGOS_APPROVAL_ROLES)
def api_hitl_resolve(item_id: str):
    data = request.get_json(silent=True) or {}
    decision = data.get("decision", "").strip()
    if not decision:
        return jsonify({"error": "decision required"}), 400
    # nav-sec-05: resolver identity is the authenticated user, never the body.
    resolved_by = _actor_id()
    conn = get_connection()
    try:
        result = conn.execute(
            "UPDATE sg_hitl_items SET status='resolved', decision=?, "
            "resolved_at=?, resolved_by=? WHERE id=? AND status='pending'",
            (decision, _now(), resolved_by, item_id),
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


@_api.route("/hitl/bulk", methods=["POST"])
@require_role(*_STRATEGOS_APPROVAL_ROLES)
def api_hitl_bulk():
    """Resolve all pending HITL items with a single decision."""
    data = request.get_json(silent=True) or {}
    decision = data.get("decision", "").strip()
    if not decision:
        return jsonify({"error": "decision required"}), 400
    # nav-sec-05: resolver identity is the authenticated user, never the body.
    resolved_by = _actor_id()
    conn = get_connection()
    try:
        result = conn.execute(
            "UPDATE sg_hitl_items SET status='resolved', decision=?, "
            "resolved_at=?, resolved_by=? WHERE status='pending'",
            (decision, _now(), resolved_by),
        )
        conn.commit()
        return jsonify({"status": "ok", "decision": decision, "updated": result.rowcount})
    except Exception as exc:
        if "no such table" in str(exc).lower():
            return jsonify({"error": "HITL module not initialised"}), 503
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@_api.route("/hitl/delete", methods=["POST"])
@require_role(*_STRATEGOS_APPROVAL_ROLES)
def api_hitl_delete():
    """Delete all pending HITL items."""
    conn = get_connection()
    try:
        result = conn.execute("DELETE FROM sg_hitl_items WHERE status='pending'")
        conn.commit()
        return jsonify({"status": "ok", "deleted": result.rowcount})
    except Exception as exc:
        if "no such table" in str(exc).lower():
            return jsonify({"error": "HITL module not initialised"}), 503
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CVE Feed (sg-cve-05)
# ---------------------------------------------------------------------------

@_api.route("/cyber/cve", methods=["GET"])
def api_cyber_cve():
    """GET /api/strategos/cyber/cve — return CVE feed entries.

    Query params:
        status : filter by status (new|active|patched|disputed|rejected)
        limit  : max rows to return (default 100)
    """
    status_filter = request.args.get("status", "").strip()
    try:
        limit = max(1, min(int(request.args.get("limit", 100)), 500))
    except ValueError:
        limit = 100

    ph = "%s" if is_pg() else "?"
    clauses, params = [], []
    if status_filter:
        clauses.append(f"status = {ph}")
        params.append(status_filter)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = _safe_fetch(
        f"SELECT cve_id, title, cvss_score, severity, is_kev, kev_due_date, "  # nosec: B608
        f"status, created_at AS published_date "
        f"FROM sg_cve_feed {where} ORDER BY created_at DESC LIMIT {ph}",
        params + [limit],
    )
    return jsonify(rows)


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


@_api.route("/wargame/<wargame_id>/assess", methods=["POST"])
def api_wargame_assess(wargame_id: str):
    try:
        from tools.strategos.wargame_advisor import get_ai_assessment
        result = get_ai_assessment(wargame_id)
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Wargame Turn Engine (ported from legacy tools/strategos/blueprint.py,
# nav-strat-05).  Backs the Advance-Turn / turns-table / ORBAT-seed / Monte
# Carlo controls in strategos/wargame.html, which fetch these under the
# /api/strategos/wargame/... prefix.
# ---------------------------------------------------------------------------

@_api.route("/wargame/<wargame_id>/orbat-seed", methods=["GET"])
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


@_api.route("/wargame/<wargame_id>/turn/advance", methods=["POST"])
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


@_api.route("/wargame/<wargame_id>/lanchester/monte-carlo", methods=["GET"])
def api_wargame_lanchester_monte_carlo(wargame_id: str):
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
            coeff = json.loads(row[2])
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


@_api.route("/wargame/<wargame_id>/turns", methods=["GET"])
def api_wargame_turns(wargame_id: str):
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
# Missing page routes
# ---------------------------------------------------------------------------

@_bp.route("/cyber")
def strategos_cyber():
    return render_template("strategos/cyber.html")


@_bp.route("/map")
def strategos_map():
    return render_template("strategos/map.html")


# ---------------------------------------------------------------------------
# KG proxy — template fetches /strategos/api/kg (not /api/strategos/kg)
# ---------------------------------------------------------------------------

@_bp.route("/api/kg")
def strategos_api_kg_proxy():
    graph_filter = request.args.get("graph", "")
    ph = "%s" if is_pg() else "?"
    if graph_filter == "war":
        from tools.strategos.war_kg import WAR_GRAPH_ID  # noqa: PLC0415
        nodes = _safe_fetch(
            f"SELECT id AS node_id, entity_type AS node_type, label FROM kg_nodes WHERE graph_id = {ph} LIMIT 500",
            (WAR_GRAPH_ID,),
        )
        edges = _safe_fetch(
            f"SELECT source_id, target_id, relationship AS relation FROM kg_edges WHERE graph_id = {ph} LIMIT 1000",
            (WAR_GRAPH_ID,),
        )
    else:
        nodes = _safe_fetch(
            "SELECT node_id, node_type, label FROM sg_kg_nodes ORDER BY node_type LIMIT 500"
        )
        edges = _safe_fetch(
            "SELECT source_id, target_id, relation FROM sg_kg_edges LIMIT 1000"
        )
    schema = {}
    if graph_filter == "war":
        try:
            from tools.strategos.war_kg import get_schema_summary  # noqa: PLC0415
            schema = get_schema_summary()
        except Exception:
            pass
    return jsonify({"nodes": nodes, "edges": edges, "schema": schema})


# ---------------------------------------------------------------------------
# Cyber sub-APIs — template fetches /strategos/api/cyber/*
# ---------------------------------------------------------------------------

@_bp.route("/api/cyber/infra-nodes")
def strategos_api_cyber_infra():
    aoi = request.args.get("aoi", "")
    # Infrastructure nodes: supply nodes + cyber-targeted conflict events
    nodes = _safe_fetch(
        "SELECT node_id AS id, label, node_type AS type, criticality, lat, lon "
        "FROM sg_supply_nodes WHERE lat IS NOT NULL AND lon IS NOT NULL"
    )
    # Overlay cyber_op events as targeted nodes
    clauses, params = ["event_type = 'cyber_op'", "lat IS NOT NULL"], []
    ph = "%s" if is_pg() else "?"
    if aoi:
        clauses.append(f"aoi = {ph}")
        params.append(aoi)
    cyber_events = _safe_fetch(
        "SELECT id, target_name AS label, target_type AS type, lat, lon, "
        "attack_vector, threat_actor, malware_family, confidence, event_ts AS detected_at "
        f"FROM sg_conflict_events WHERE {' AND '.join(clauses)} LIMIT 200",  # nosec: B608
        params,
    )
    _crit_score = {"critical": 9.5, "high": 7.5, "medium": 5.0, "low": 2.5}
    for ev in cyber_events:
        ev.setdefault("criticality", "high")
        ev["source"] = "conflict_event"
        ev["cyber_vuln_score"] = round((ev.get("confidence") or 0.5) * 10, 1)

    # CVE enrichment: join sg_cve_feed to supply nodes by vendor/product match
    ph = "%s" if is_pg() else "?"
    ilike = "ILIKE" if is_pg() else "LIKE"
    for n in nodes:
        n["source"] = "supply_node"
        base_score = _crit_score.get((n.get("criticality") or "medium").lower(), 5.0)
        term = (n.get("label") or n.get("node_type") or "").strip()
        if term:
            cve_rows = _safe_fetch(
                f"SELECT cve_id, cvss_score, is_kev FROM sg_cve_feed "  # nosec: B608
                f"WHERE (vendor {ilike} {ph} OR product {ilike} {ph}) AND cvss_score >= 7.0 "
                f"ORDER BY cvss_score DESC LIMIT 5",
                (f"%{term}%", f"%{term}%"),
            )
            cve_count_rows = _safe_fetch(
                f"SELECT COUNT(*) AS cnt FROM sg_cve_feed "  # nosec: B608
                f"WHERE (vendor {ilike} {ph} OR product {ilike} {ph}) AND cvss_score >= 7.0",
                (f"%{term}%", f"%{term}%"),
            )
        else:
            cve_rows, cve_count_rows = [], []
        total = (cve_count_rows[0].get("cnt") or 0) if cve_count_rows else 0
        has_kev = any(r.get("is_kev") for r in cve_rows)
        n["cve_count"] = total
        n["top_cves"] = [
            {"cve_id": r["cve_id"], "cvss_score": r["cvss_score"], "is_kev": bool(r.get("is_kev"))}
            for r in cve_rows
        ]
        n["has_kev"] = has_kev
        if has_kev:
            base_score = max(base_score, 9.0)
        elif total > 0:
            base_score = min(10.0, base_score + total * 0.3)
        n["cyber_vuln_score"] = round(base_score, 1)

    return jsonify({"nodes": nodes + cyber_events})


@_bp.route("/api/cyber/attack-heatmap")
def strategos_api_cyber_heatmap():
    # Count observed techniques from sg_conflict_events
    raw_ids = _safe_fetch(
        "SELECT technique_ids FROM sg_conflict_events "
        "WHERE event_type = 'cyber_op' AND technique_ids IS NOT NULL"
    )
    observed: dict = {}
    for row in raw_ids:
        for tid in (row.get("technique_ids") or "").split(","):
            tid = tid.strip()
            if tid:
                observed[tid] = observed.get(tid, 0) + 1

    # Load all tactics + techniques from sg_attack_techniques (live data)
    tech_rows = _safe_fetch(
        "SELECT DISTINCT tactic_id, tactic_name, technique_id, technique_name "
        "FROM sg_attack_techniques ORDER BY tactic_id, technique_id"
    )
    data_source = "live" if tech_rows else "static"

    # Build tactic→techniques map; de-duplicate technique_id per tactic
    tactic_order: list = []
    tactic_map: dict = {}
    seen_per_tactic: dict = {}
    for r in tech_rows:
        tid_tac = r["tactic_id"]
        if tid_tac not in tactic_map:
            tactic_order.append(tid_tac)
            tactic_map[tid_tac] = {"id": tid_tac, "label": r["tactic_name"], "cells": []}
            seen_per_tactic[tid_tac] = set()
        tech_id = r["technique_id"]
        if tech_id not in seen_per_tactic[tid_tac]:
            seen_per_tactic[tid_tac].add(tech_id)
            tactic_map[tid_tac]["cells"].append({
                "technique_id": tech_id,
                "name": r["technique_name"],
                "count": observed.get(tech_id, 0),
            })

    result = [tactic_map[t] for t in tactic_order]
    return jsonify({"tactics": result, "data_source": data_source, "technique_count": len(tech_rows)})


@_bp.route("/api/cyber/timeline")
def strategos_api_cyber_timeline():
    aoi = request.args.get("aoi", "")
    ph = "%s" if is_pg() else "?"

    base_clauses = ["lat IS NOT NULL"]
    if aoi:
        base_clauses.append(f"aoi = {ph}")

    cyber_params = ["cyber_op"] + ([aoi] if aoi else [])
    cyber_events = _safe_fetch(
        "SELECT id, event_ts, description, actor1, actor2, goldstein_scale, attack_vector, "
        "threat_actor, target_name, confidence "
        f"FROM sg_conflict_events WHERE event_type = {ph} "  # nosec: B608
        + (f"AND aoi = {ph}" if aoi else "")
        + " ORDER BY event_ts DESC LIMIT 100",
        cyber_params,
    )

    kinetic_params = (["usv_strike", "attack", "military_move"] + ([aoi] if aoi else []))
    ph_list = ",".join(ph for _ in ["usv_strike", "attack", "military_move"])
    kinetic_events = _safe_fetch(
        f"SELECT id, event_ts, description, actor1, actor2, goldstein_scale, event_type "  # nosec: B608
        f"FROM sg_conflict_events WHERE event_type IN ({ph_list}) "
        + (f"AND aoi = {ph}" if aoi else "")
        + " ORDER BY event_ts DESC LIMIT 100",
        kinetic_params,
    )

    # Simple temporal correlation: pair cyber events near kinetic events (±12h)
    correlations = []
    for c in cyber_events[:20]:
        for k in kinetic_events[:20]:
            try:
                from datetime import datetime as _dt  # noqa: PLC0415
                ct = _dt.fromisoformat(str(c.get("event_ts", "")).replace("Z", "+00:00"))
                kt = _dt.fromisoformat(str(k.get("event_ts", "")).replace("Z", "+00:00"))
                delta_h = abs((ct - kt).total_seconds()) / 3600
                if delta_h <= 12:
                    correlations.append({
                        "cyber_id": c["id"],
                        "kinetic_id": k["id"],
                        "strength": round(max(0.1, 1.0 - delta_h / 12), 2),
                    })
            except Exception:
                pass

    return jsonify({
        "cyber_events": cyber_events,
        "kinetic_events": kinetic_events,
        "correlations": correlations[:50],
    })


# ---------------------------------------------------------------------------
# Info page
# ---------------------------------------------------------------------------

@_bp.route("/info")
def strategos_info():
    return render_template("strategos/info.html")


# ---------------------------------------------------------------------------
# War Council page + API
# ---------------------------------------------------------------------------

@_bp.route("/war-council")
def strategos_war_council():
    theaters = [
        "ukraine", "taiwan", "korean_peninsula", "black_sea",
        "middle_east", "arctic", "south_china_sea", "unspecified",
    ]
    return render_template("strategos/war_council.html", theaters=theaters)


@_api.route("/war-council/generate", methods=["POST"])
def api_war_council_generate():
    data = request.get_json(silent=True) or {}
    scenario = (data.get("scenario") or "").strip()
    if not scenario:
        return jsonify({"error": "scenario is required"}), 400
    try:
        from tools.strategos.war_council import WarCouncilEngine, WarCouncilRequest  # noqa: PLC0415
        req = WarCouncilRequest(
            scenario=scenario,
            theater=data.get("theater", "unspecified"),
            commander_intent=data.get("commander_intent", ""),
            constraints=data.get("constraints", []),
            friendly_cog=data.get("friendly_cog", ""),
            friendly_cc=data.get("friendly_cc", ""),
            friendly_cv=data.get("friendly_cv", ""),
            adversary_cog=data.get("adversary_cog", ""),
            adversary_cc=data.get("adversary_cc", ""),
            adversary_cv=data.get("adversary_cv", ""),
            save_to_db=True,
        )
        engine = WarCouncilEngine()
        brief = engine.generate(req)
        return jsonify(brief.to_dict())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Commander's Dashboard
# ---------------------------------------------------------------------------

@_bp.route("/commander")
@_bp.route("/commander/")
def strategos_commander():
    return render_template("strategos/commander.html")


@_api.route("/commander/picture", methods=["GET"])
def api_commander_picture():
    try:
        from tools.strategos.commander_dashboard import get_commander_picture  # noqa: PLC0415
        pic = get_commander_picture()
        resp = make_response(jsonify(pic))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/endurance", methods=["GET"])
def api_endurance():
    try:
        from tools.strategos.commander_dashboard import get_war_endurance  # noqa: PLC0415
        data = get_war_endurance()
        resp = make_response(jsonify(data))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# INTSUM Auto-Generator
# ---------------------------------------------------------------------------

@_bp.route("/intsum")
@_bp.route("/intsum/")
def strategos_intsum():
    from tools.strategos.intsum import list_intsums  # noqa: PLC0415
    intsums = []
    try:
        intsums = list_intsums(limit=20)
    except Exception:
        pass
    return render_template("strategos/intsum.html", intsums=intsums)


@_api.route("/intsum/generate", methods=["POST"])
def api_intsum_generate():
    data = request.get_json(silent=True) or {}
    theater = data.get("theater", "global")
    lookback_hours = int(data.get("lookback_hours", 24))
    # TRUST invariant (nav-strat-01): promoting ungrounded INTSUM prose requires
    # an explicit, audited HITL override.
    force_ungrounded = bool(data.get("force_ungrounded", False))
    try:
        from tools.strategos.intsum import generate_intsum  # noqa: PLC0415
        result = generate_intsum(
            theater=theater, lookback_hours=lookback_hours,
            force_ungrounded=force_ungrounded,
        )
        if "error" in result:
            return jsonify(result), 500
        # Surface ungrounded content clearly: 202 Accepted (draft persisted but
        # not source-grounded) vs 201 Created for grounded/template output.
        grounding_status = (result.get("grounding") or {}).get("status", "")
        http_status = 202 if grounding_status == "ungrounded" else 201
        resp = make_response(jsonify(result), http_status)
        resp.headers["X-Classification"] = "CUI"
        resp.headers["X-INTSUM-Grounding"] = grounding_status or "unknown"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/intsum/list", methods=["GET"])
def api_intsum_list():
    limit = int(request.args.get("limit", 10))
    try:
        from tools.strategos.intsum import list_intsums  # noqa: PLC0415
        rows = list_intsums(limit=limit)
        resp = make_response(jsonify({"intsums": rows, "total": len(rows)}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/intsum/<intsum_id>", methods=["GET"])
def api_intsum_detail(intsum_id: str):
    try:
        from tools.strategos.intsum import get_intsum_detail  # noqa: PLC0415
        intsum = get_intsum_detail(intsum_id)
        if not intsum:
            return jsonify({"error": "not found"}), 404
        resp = make_response(jsonify(intsum))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/intsum/<intsum_id>", methods=["DELETE"])
def api_intsum_delete(intsum_id: str):
    try:
        from tools.db.storage import get_connection, is_pg  # noqa: PLC0415
        ph = "%s" if is_pg() else "?"
        conn = get_connection()
        try:
            conn.execute(
                f"DELETE FROM sg_intsum_paragraphs WHERE intsum_id = {ph}", (intsum_id,)  # nosec B608
            )
            conn.execute(
                f"DELETE FROM sg_intsums WHERE id = {ph}", (intsum_id,)  # nosec B608
            )
            conn.commit()
        finally:
            conn.close()
        resp = make_response(jsonify({"status": "deleted"}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# CCIR Trigger Engine API
# ---------------------------------------------------------------------------

@_api.route("/ccir/evaluate", methods=["POST"])
def api_ccir_evaluate():
    data = request.get_json(silent=True) or {}
    lookback_hours = int(data.get("lookback_hours", 12))
    threshold = float(data.get("threshold", 0.15))
    try:
        from tools.strategos.ccir_trigger import evaluate_ccirs  # noqa: PLC0415
        result = evaluate_ccirs(lookback_hours=lookback_hours, threshold=threshold)
        resp = make_response(jsonify(result))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/ccir/triggers", methods=["GET"])
def api_ccir_triggers():
    limit = int(request.args.get("limit", 20))
    try:
        from tools.strategos.ccir_trigger import get_recent_trigger_events  # noqa: PLC0415
        events = get_recent_trigger_events(limit=limit)
        resp = make_response(jsonify({"events": events, "total": len(events)}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/ccir/triggers/<event_id>/resolve", methods=["POST"])
@require_role(*_STRATEGOS_APPROVAL_ROLES)
def api_ccir_trigger_resolve(event_id: str):
    try:
        from tools.strategos.ccir_trigger import resolve_trigger_event  # noqa: PLC0415
        ok = resolve_trigger_event(event_id)
        resp = make_response(jsonify({"status": "resolved" if ok else "error"}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Phase 3 — OPORD Generator, Red Cell, METT-TC
# ---------------------------------------------------------------------------

@_bp.route("/opord")
@_bp.route("/opord/")
def strategos_opord():
    from tools.strategos.opord import list_opords  # noqa: PLC0415
    opords = []
    try:
        opords = list_opords(limit=20)
    except Exception:
        pass
    return render_template("strategos/opord.html", opords=opords)


@_api.route("/opord", methods=["GET"])
def api_opord_list():
    try:
        from tools.strategos.opord import list_opords  # noqa: PLC0415
        theater = request.args.get("theater", "")
        rows = list_opords(theater=theater, limit=30)
        resp = make_response(jsonify({"opords": rows}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/opord", methods=["POST"])
def api_opord_create():
    try:
        from tools.strategos.opord import create_opord  # noqa: PLC0415
        data = request.get_json(force=True, silent=True) or {}
        result = create_opord(
            title=data.get("title", ""),
            theater=data.get("theater", "unspecified"),
            scenario=data.get("scenario", ""),
            task_org=data.get("task_org", ""),
            created_by=data.get("created_by", "analyst"),
        )
        resp = make_response(jsonify(result), 201)
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/opord/<opord_id>", methods=["GET"])
def api_opord_detail(opord_id: str):
    try:
        from tools.strategos.opord import get_opord  # noqa: PLC0415
        o = get_opord(opord_id)
        if not o:
            return jsonify({"error": "OPORD not found"}), 404
        resp = make_response(jsonify(o))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/opord/<opord_id>/synthesize/<int:para_num>", methods=["POST"])
def api_opord_synthesize(opord_id: str, para_num: int):
    try:
        from tools.strategos.opord import synthesize_paragraph  # noqa: PLC0415
        data = request.get_json(force=True, silent=True) or {}
        result = synthesize_paragraph(opord_id, para_num, scenario=data.get("scenario", ""))
        resp = make_response(jsonify(result))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/opord/<opord_id>/synthesize-all", methods=["POST"])
def api_opord_synthesize_all(opord_id: str):
    try:
        from tools.strategos.opord import synthesize_all  # noqa: PLC0415
        data = request.get_json(force=True, silent=True) or {}
        result = synthesize_all(opord_id, scenario=data.get("scenario", ""))
        resp = make_response(jsonify(result))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/opord/<opord_id>/approve", methods=["POST"])
@require_role(*_STRATEGOS_APPROVAL_ROLES)
def api_opord_approve(opord_id: str):
    try:
        from tools.strategos.opord import approve_opord  # noqa: PLC0415
        data = request.get_json(force=True, silent=True) or {}
        # TRUST gate (nav-strat-02): approval is blocked for ungrounded OPORDs
        # unless an explicit, audited force override is supplied.
        # nav-sec-05: approver identity is the authenticated user, never the body.
        result = approve_opord(
            opord_id,
            approved_by=_actor_id(),
            force=bool(data.get("force", False)),
            force_reason=data.get("force_reason", ""),
        )
        http_status = 200
        if result.get("status") == "blocked":
            http_status = 409
        elif result.get("status") == "error":
            http_status = 404 if result.get("error") == "OPORD not found" else 500
        resp = make_response(jsonify(result), http_status)
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/opord/<opord_id>", methods=["DELETE"])
@require_role(*_STRATEGOS_APPROVAL_ROLES)
def api_opord_delete(opord_id: str):
    try:
        from tools.strategos.opord import delete_opord  # noqa: PLC0415
        ok = delete_opord(opord_id)
        resp = make_response(jsonify({"status": "deleted" if ok else "error"}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# Red Cell — MLCOA / MDCOA
@_bp.route("/red-cell")
@_bp.route("/red-cell/")
def strategos_red_cell():
    from tools.strategos.red_cell import list_analyses  # noqa: PLC0415
    analyses = []
    try:
        analyses = list_analyses(limit=20)
    except Exception:
        pass
    return render_template("strategos/red_cell.html", analyses=analyses)


@_api.route("/red-cell/analyses", methods=["GET"])
def api_red_cell_list():
    try:
        from tools.strategos.red_cell import list_analyses  # noqa: PLC0415
        theater = request.args.get("theater", "")
        rows = list_analyses(theater=theater, limit=20)
        resp = make_response(jsonify({"analyses": rows}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/red-cell/analyses", methods=["POST"])
def api_red_cell_create():
    try:
        from tools.strategos.red_cell import create_analysis  # noqa: PLC0415
        data = request.get_json(force=True, silent=True) or {}
        result = create_analysis(
            theater=data.get("theater", "global"),
            scenario=data.get("scenario", ""),
            created_by=data.get("created_by", "analyst"),
        )
        resp = make_response(jsonify(result), 201)
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/red-cell/analyses/<analysis_id>", methods=["GET"])
def api_red_cell_detail(analysis_id: str):
    try:
        from tools.strategos.red_cell import get_analysis  # noqa: PLC0415
        a = get_analysis(analysis_id)
        if not a:
            return jsonify({"error": "not found"}), 404
        resp = make_response(jsonify(a))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/red-cell/analyses/<analysis_id>/mlcoa", methods=["POST"])
def api_red_cell_mlcoa(analysis_id: str):
    try:
        from tools.strategos.red_cell import synthesize_mlcoa  # noqa: PLC0415
        result = synthesize_mlcoa(analysis_id)
        resp = make_response(jsonify(result))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/red-cell/analyses/<analysis_id>/mdcoa", methods=["POST"])
def api_red_cell_mdcoa(analysis_id: str):
    try:
        from tools.strategos.red_cell import synthesize_mdcoa  # noqa: PLC0415
        result = synthesize_mdcoa(analysis_id)
        resp = make_response(jsonify(result))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/red-cell/analyses/<analysis_id>", methods=["PATCH"])
def api_red_cell_update(analysis_id: str):
    try:
        from tools.strategos.red_cell import update_analysis  # noqa: PLC0415
        data = request.get_json(force=True, silent=True) or {}
        ok = update_analysis(analysis_id, data)
        resp = make_response(jsonify({"status": "updated" if ok else "no_change"}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/red-cell/analyses/<analysis_id>", methods=["DELETE"])
def api_red_cell_delete(analysis_id: str):
    try:
        from tools.strategos.red_cell import delete_analysis  # noqa: PLC0415
        ok = delete_analysis(analysis_id)
        resp = make_response(jsonify({"status": "deleted" if ok else "error"}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# METT-TC Worksheet
@_bp.route("/mett-tc")
@_bp.route("/mett-tc/")
def strategos_mett_tc():
    from tools.strategos.mett_tc import list_worksheets  # noqa: PLC0415
    worksheets = []
    try:
        worksheets = list_worksheets(limit=20)
    except Exception:
        pass
    return render_template("strategos/mett_tc.html", worksheets=worksheets)


@_api.route("/mett-tc", methods=["GET"])
def api_mett_tc_list():
    try:
        from tools.strategos.mett_tc import list_worksheets  # noqa: PLC0415
        theater = request.args.get("theater", "")
        rows = list_worksheets(theater=theater, limit=20)
        resp = make_response(jsonify({"worksheets": rows}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/mett-tc/auto-populate", methods=["GET"])
def api_mett_tc_auto():
    try:
        from tools.strategos.mett_tc import auto_populate  # noqa: PLC0415
        theater = request.args.get("theater", "unspecified")
        data = auto_populate(theater=theater)
        resp = make_response(jsonify(data))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/mett-tc", methods=["POST"])
def api_mett_tc_create():
    try:
        from tools.strategos.mett_tc import create_worksheet  # noqa: PLC0415
        data = request.get_json(force=True, silent=True) or {}
        result = create_worksheet(
            theater=data.get("theater", "unspecified"),
            operation_name=data.get("operation_name", ""),
            mission=data.get("mission", ""),
            enemy_situation=data.get("enemy_situation", ""),
            terrain_weather=data.get("terrain_weather", ""),
            troops_available=data.get("troops_available", ""),
            time_available=data.get("time_available", ""),
            civil_considerations=data.get("civil_considerations", ""),
            auto_populated=bool(data.get("auto_populated", False)),
            created_by=data.get("created_by", "analyst"),
        )
        resp = make_response(jsonify(result), 201)
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/mett-tc/<ws_id>", methods=["GET"])
def api_mett_tc_detail(ws_id: str):
    try:
        from tools.strategos.mett_tc import get_worksheet  # noqa: PLC0415
        ws = get_worksheet(ws_id)
        if not ws:
            return jsonify({"error": "worksheet not found"}), 404
        resp = make_response(jsonify(ws))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/mett-tc/<ws_id>", methods=["PATCH"])
def api_mett_tc_update(ws_id: str):
    try:
        from tools.strategos.mett_tc import update_worksheet  # noqa: PLC0415
        data = request.get_json(force=True, silent=True) or {}
        ok = update_worksheet(ws_id, data)
        resp = make_response(jsonify({"status": "updated" if ok else "no_change"}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/mett-tc/<ws_id>", methods=["DELETE"])
def api_mett_tc_delete(ws_id: str):
    try:
        from tools.strategos.mett_tc import delete_worksheet  # noqa: PLC0415
        ok = delete_worksheet(ws_id)
        resp = make_response(jsonify({"status": "deleted" if ok else "error"}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Phase 2 — IPB Canvas, I&W Indicators, F3EAD Targeting, BDA
# ---------------------------------------------------------------------------

@_bp.route("/ipb")
@_bp.route("/ipb/")
def strategos_ipb():
    from tools.strategos.ipb import list_sessions  # noqa: PLC0415
    sessions = []
    try:
        sessions = list_sessions(limit=20)
    except Exception:
        pass
    return render_template("strategos/ipb.html", sessions=sessions)


@_api.route("/ipb/sessions", methods=["GET"])
def api_ipb_sessions():
    try:
        from tools.strategos.ipb import list_sessions  # noqa: PLC0415
        rows = list_sessions(limit=20)
        resp = make_response(jsonify({"sessions": rows}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/ipb/sessions", methods=["POST"])
def api_ipb_create():
    try:
        from tools.strategos.ipb import create_session  # noqa: PLC0415
        data = request.get_json(force=True, silent=True) or {}
        theater = data.get("theater", "unspecified")
        scenario = data.get("scenario", "")
        result = create_session(theater=theater, scenario=scenario)
        resp = make_response(jsonify(result), 201)
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/ipb/sessions/<session_id>", methods=["GET"])
def api_ipb_session_detail(session_id: str):
    try:
        from tools.strategos.ipb import get_session  # noqa: PLC0415
        sess = get_session(session_id)
        if not sess:
            return jsonify({"error": "session not found"}), 404
        resp = make_response(jsonify(sess))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/ipb/sessions/<session_id>/synthesize/<int:step_num>", methods=["POST"])
def api_ipb_synthesize(session_id: str, step_num: int):
    try:
        from tools.strategos.ipb import synthesize_step  # noqa: PLC0415
        result = synthesize_step(session_id=session_id, step_num=step_num)
        resp = make_response(jsonify(result))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/ipb/sessions/<session_id>", methods=["DELETE"])
def api_ipb_delete(session_id: str):
    try:
        from tools.strategos.ipb import delete_session  # noqa: PLC0415
        ok = delete_session(session_id)
        resp = make_response(jsonify({"status": "deleted" if ok else "error"}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# I&W Indicators Board
@_bp.route("/indicators")
@_bp.route("/indicators/")
def strategos_indicators():
    return render_template("strategos/indicators.html")


@_api.route("/indicators", methods=["GET"])
def api_indicators_list():
    try:
        from tools.strategos.iw_indicators import list_indicators  # noqa: PLC0415
        theater = request.args.get("theater", "global")
        coa_id = request.args.get("coa_id", "")
        rows = list_indicators(theater=theater, coa_id=coa_id)
        resp = make_response(jsonify({"indicators": rows}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/indicators", methods=["POST"])
def api_indicators_create():
    try:
        from tools.strategos.iw_indicators import create_indicator  # noqa: PLC0415
        data = request.get_json(force=True, silent=True) or {}
        result = create_indicator(
            name=data.get("name", ""),
            coa_id=data.get("coa_id", ""),
            coa_name=data.get("coa_name", ""),
            description=data.get("description", ""),
            weight=float(data.get("weight", 1.0)),
            category=data.get("category", "general"),
            theater=data.get("theater", "global"),
        )
        resp = make_response(jsonify(result), 201)
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/indicators/<indicator_id>/observe", methods=["POST"])
def api_indicators_observe(indicator_id: str):
    try:
        from tools.strategos.iw_indicators import record_observation  # noqa: PLC0415
        data = request.get_json(force=True, silent=True) or {}
        result = record_observation(
            indicator_id=indicator_id,
            observed_status=data.get("observed_status", "not_observed"),
            source=data.get("source", ""),
            notes=data.get("notes", ""),
            confidence=float(data.get("confidence", 0.5)),
        )
        resp = make_response(jsonify(result))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/indicators/coa-probabilities", methods=["GET"])
def api_indicators_coa_probs():
    try:
        from tools.strategos.iw_indicators import compute_coa_probabilities  # noqa: PLC0415
        theater = request.args.get("theater", "global")
        results = compute_coa_probabilities(theater=theater)
        resp = make_response(jsonify({"coa_probabilities": results}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/indicators/<indicator_id>", methods=["DELETE"])
def api_indicators_delete(indicator_id: str):
    try:
        from tools.strategos.iw_indicators import delete_indicator  # noqa: PLC0415
        ok = delete_indicator(indicator_id)
        resp = make_response(jsonify({"status": "deleted" if ok else "error"}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# F3EAD Targeting Workflow
@_bp.route("/f3ead")
@_bp.route("/f3ead/")
def strategos_f3ead():
    return render_template("strategos/f3ead.html")


@_api.route("/f3ead/board", methods=["GET"])
def api_f3ead_board():
    try:
        from tools.strategos.f3ead import get_board  # noqa: PLC0415
        theater = request.args.get("theater", "")
        data = get_board(theater=theater)
        resp = make_response(jsonify(data))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/f3ead/targets", methods=["POST"])
def api_f3ead_create():
    try:
        from tools.strategos.f3ead import create_target  # noqa: PLC0415
        data = request.get_json(force=True, silent=True) or {}
        result = create_target(
            target_name=data.get("target_name", ""),
            target_type=data.get("target_type", "node"),
            description=data.get("description", ""),
            responsible_element=data.get("responsible_element", ""),
            priority=int(data.get("priority", 3)),
            theater=data.get("theater", "unspecified"),
            geo_hint=data.get("geo_hint", ""),
        )
        resp = make_response(jsonify(result), 201)
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/f3ead/targets/<target_id>/advance", methods=["POST"])
@require_role(*_STRATEGOS_APPROVAL_ROLES)
def api_f3ead_advance(target_id: str):
    try:
        from tools.strategos.f3ead import advance_phase  # noqa: PLC0415
        data = request.get_json(force=True, silent=True) or {}
        result = advance_phase(
            target_id=target_id,
            # nav-sec-05: actor identity is the authenticated user, never the body.
            actor=_actor_id(),
            notes=data.get("notes", ""),
        )
        resp = make_response(jsonify(result))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/f3ead/targets/<target_id>/phase", methods=["PATCH"])
@require_role(*_STRATEGOS_APPROVAL_ROLES)
def api_f3ead_set_phase(target_id: str):
    try:
        from tools.strategos.f3ead import set_phase  # noqa: PLC0415
        data = request.get_json(force=True, silent=True) or {}
        result = set_phase(
            target_id=target_id,
            phase=data.get("phase", "find"),
            # nav-sec-05: actor identity is the authenticated user, never the body.
            actor=_actor_id(),
            notes=data.get("notes", ""),
        )
        resp = make_response(jsonify(result))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/f3ead/targets/<target_id>", methods=["DELETE"])
@require_role(*_STRATEGOS_APPROVAL_ROLES)
def api_f3ead_delete(target_id: str):
    try:
        from tools.strategos.f3ead import delete_target  # noqa: PLC0415
        ok = delete_target(target_id)
        resp = make_response(jsonify({"status": "deleted" if ok else "error"}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# BDA — Battle Damage Assessment
@_bp.route("/bda")
@_bp.route("/bda/")
def strategos_bda():
    return render_template("strategos/bda.html")


@_api.route("/bda/assessments", methods=["GET"])
def api_bda_list():
    try:
        from tools.strategos.bda import list_assessments  # noqa: PLC0415
        theater = request.args.get("theater", "")
        limit = int(request.args.get("limit", 50))
        rows = list_assessments(theater=theater, limit=limit)
        resp = make_response(jsonify({"assessments": rows, "total": len(rows)}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/bda/assessments", methods=["POST"])
def api_bda_create():
    try:
        from tools.strategos.bda import create_assessment  # noqa: PLC0415
        data = request.get_json(force=True, silent=True) or {}
        result = create_assessment(
            target_name=data.get("target_name", ""),
            target_id=data.get("target_id", ""),
            strike_time=data.get("strike_time", ""),
            method=data.get("method", "kinetic"),
            physical_damage=data.get("physical_damage", "unknown"),
            functional_defeat=bool(data.get("functional_defeat", False)),
            confidence=float(data.get("confidence", 0.5)),
            collection_method=data.get("collection_method", "IMINT"),
            analyst_notes=data.get("analyst_notes", ""),
            theater=data.get("theater", "unspecified"),
            created_by=data.get("created_by", "analyst"),
        )
        resp = make_response(jsonify(result), 201)
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/bda/summary", methods=["GET"])
def api_bda_summary():
    try:
        from tools.strategos.bda import get_summary  # noqa: PLC0415
        theater = request.args.get("theater", "")
        summary = get_summary(theater=theater)
        resp = make_response(jsonify(summary))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# OSINT Ingestion Panel page + APIs
# ---------------------------------------------------------------------------

@_bp.route("/osint")
def strategos_osint():
    return render_template("strategos/osint.html")


@_api.route("/osint/stats", methods=["GET"])
def api_osint_stats():
    raw = _safe_count("SELECT COUNT(*) FROM sg_raw_signals")
    prioritized = _safe_count("SELECT COUNT(*) FROM sg_prioritized_signals")
    cyber = _safe_count("SELECT COUNT(*) FROM sg_conflict_events WHERE event_type = 'cyber_op'")
    kinetic = _safe_count("SELECT COUNT(*) FROM sg_conflict_events WHERE event_type != 'cyber_op'")
    last_row = _safe_fetch(
        "SELECT created_at FROM sg_raw_signals ORDER BY created_at DESC LIMIT 1"
    )
    last_run = last_row[0]["created_at"] if last_row else None
    return jsonify({
        "raw_signals": raw,
        "prioritized_signals": prioritized,
        "cyber_events": cyber,
        "kinetic_events": kinetic,
        "last_run": last_run,
    })


@_api.route("/osint/run-gdelt", methods=["POST"])
def api_osint_run_gdelt():
    try:
        from tools.strategos.gdelt_importer import run as gdelt_run  # noqa: PLC0415
        result = gdelt_run()
        return jsonify({"status": "ok", "inserted": result.get("inserted", 0), "detail": result})
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500


@_api.route("/osint/run-prestage", methods=["POST"])
def api_osint_run_prestage():
    try:
        from pathlib import Path as _Path  # noqa: PLC0415
        from tools.strategos.osint_prestage import prestage  # noqa: PLC0415
        _inbox = _Path(__file__).resolve().parents[2] / "data" / "osint_inbox"
        result = prestage(output_dir=_inbox)
        return jsonify({"status": "ok", "count": result.get("signals", 0), "detail": result})
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500


@_api.route("/osint/scan", methods=["POST"])
def api_osint_scan():
    import uuid as _uuid  # noqa: PLC0415
    from pathlib import Path as _Path  # noqa: PLC0415
    scan_id = str(_uuid.uuid4())
    body = request.get_json(silent=True) or {}
    target = (body.get("target") or "").strip()
    if not target:
        # derive a default target from the highest-priority active PIR topic
        top = _safe_fetch(
            "SELECT topic FROM sg_pir_requirements "
            "WHERE status='active' ORDER BY collection_priority ASC LIMIT 1"
        )
        target = top[0]["topic"] if top else "strategos-default"
    results: dict = {"scan_id": scan_id, "status": "ok", "target": target}
    # stage current RSS/OSINT signals into the inbox
    try:
        from tools.strategos.osint_prestage import prestage  # noqa: PLC0415
        _inbox = _Path(__file__).resolve().parents[2] / "data" / "osint_inbox"
        staged = prestage(output_dir=_inbox)
        results["staged"] = staged.get("signals", 0)
    except Exception as exc:
        results["stage_error"] = str(exc)
    # ingest staged signals for this target
    try:
        from tools.strategos.osint_harvester import harvest  # noqa: PLC0415
        _inbox = _Path(__file__).resolve().parents[2] / "data" / "osint_inbox"
        harvested = harvest(target=target, inbox_dir=_inbox)
        results["ingested"] = harvested.get("ingested", 0)
        results["harvest_status"] = harvested.get("status", "unknown")
    except Exception as exc:
        results["harvest_error"] = str(exc)
    # populate sg_raw_signals from GDELT geo-tagged events
    try:
        from tools.strategos.gdelt_importer import run as _gdelt_run  # noqa: PLC0415
        gdelt_result = _gdelt_run()
        results["gdelt_inserted"] = gdelt_result.get("inserted", 0)
        results["gdelt_status"] = gdelt_result.get("status", "unknown")
    except Exception as exc:
        results["gdelt_error"] = str(exc)
    return jsonify(results)


@_api.route("/osint/results", methods=["GET"])
def api_osint_results():
    rows = _safe_fetch(
        "SELECT * FROM sg_prioritized_signals ORDER BY created_at DESC LIMIT 100"
    )
    return jsonify(rows if rows else [])


@_bp.route("/darkweb")
@_bp.route("/darkweb/")
def strategos_darkweb_page():
    from tools.strategos.darkweb import get_signals, tor_available  # noqa: PLC0415
    status = request.args.get("status", "all")
    signals = get_signals(status=status if status != "all" else None)
    return render_template(
        "strategos/darkweb.html",
        signals=signals,
        tor_available=tor_available(),
    )


@_api.route("/darkweb/run", methods=["POST"])
def api_darkweb_run():
    try:
        from tools.strategos.darkweb import run_monitor  # noqa: PLC0415
        result = run_monitor()
        return jsonify(result)
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500


@_api.route("/darkweb/signals")
def api_darkweb_signals():
    status = request.args.get("status")
    min_score = float(request.args.get("min_score", 0))
    limit = int(request.args.get("limit", 50))
    rows = _safe_fetch(
        "SELECT * FROM sg_darkweb_signals "
        "WHERE relevance_score >= %s "
        "AND (%s::text IS NULL OR status=%s) "
        "ORDER BY relevance_score DESC LIMIT %s",
        (min_score, status, status, limit),
    )
    return jsonify({"signals": rows, "total": len(rows)})


# ---------------------------------------------------------------------------
# Map sub-APIs — template fetches /strategos/api/map/*
# ---------------------------------------------------------------------------

@_bp.route("/api/map/orbat")
def strategos_api_map_orbat():
    rows = _safe_fetch(
        "SELECT id, unit_name, unit_type, nation, strength, location, lat, lon, status "
        "FROM sg_orbat_units WHERE lat IS NOT NULL AND lon IS NOT NULL"
    )
    return jsonify({"units": rows})


@_bp.route("/api/map/ghost")
def strategos_api_map_ghost():
    rows = _safe_fetch(
        "SELECT id, signal_type, mmsi, lat, lon, confidence, source, detected_at "
        "FROM sg_ghost_signals WHERE lat IS NOT NULL AND lon IS NOT NULL "
        "ORDER BY detected_at DESC LIMIT 200"
    )
    return jsonify({"signals": rows})


@_bp.route("/api/map/events")
def strategos_api_map_events():
    rows = _safe_fetch(
        "SELECT id, event_type, actor1, actor2, lat, lon, description, event_ts "
        "FROM sg_conflict_events WHERE lat IS NOT NULL AND lon IS NOT NULL "
        "ORDER BY event_ts DESC LIMIT 300"
    )
    return jsonify({"events": rows})


@_bp.route("/api/map/supply")
def strategos_api_map_supply():
    rows = _safe_fetch(
        "SELECT node_id AS id, label, node_type, criticality, lat, lon "
        "FROM sg_supply_nodes WHERE lat IS NOT NULL AND lon IS NOT NULL"
    )
    return jsonify({"nodes": rows})


@_bp.route("/api/map/a2ad")
def strategos_api_map_a2ad():
    """A2/AD threat rings + chokepoints overlay for current theater."""
    theater_id = request.args.get("theater", "pacific")
    try:
        from tools.strategos.a2ad_mapper import A2ADMapper  # noqa: PLC0415
        mapper = A2ADMapper()
        overlay = mapper.full_overlay(theater_id)
        return jsonify(overlay)
    except Exception as exc:
        return jsonify({"type": "FeatureCollection", "features": [], "error": str(exc)}), 200


@_bp.route("/api/map/frontlines")
def strategos_api_map_frontlines():
    """Latest frontline segments for the given theater."""
    theater_id = request.args.get("theater", "ukraine")
    limit = min(int(request.args.get("limit", 100)), 500)
    ph = "%s" if is_pg() else "?"
    rows = _safe_fetch(
        f"SELECT id, snapshot_date, geometry_json, advance_km, source "
        f"FROM sg_frontline_segments WHERE theater = {ph} "
        f"ORDER BY snapshot_date DESC LIMIT {ph}",
        (theater_id, limit),
    )
    features = []
    for row in rows:
        try:
            geom = json.loads(row.get("geometry_json") or "{}")
            if geom:
                features.append({
                    "type": "Feature",
                    "geometry": geom,
                    "properties": {
                        "id": row.get("id"),
                        "snapshot_date": row.get("snapshot_date"),
                        "advance_km": row.get("advance_km", 0),
                        "source": row.get("source"),
                    },
                })
        except Exception:
            pass
    return jsonify({"type": "FeatureCollection", "features": features})


# ---------------------------------------------------------------------------
# GEOINT EO scenes API
# ---------------------------------------------------------------------------


@_api.route("/geoint/scenes", methods=["GET"])
def strategos_api_geoint_scenes():
    aoi  = request.args.get("aoi")
    status_filter = request.args.get("status")
    limit = min(int(request.args.get("limit", 200)), 500)
    ph = "%s" if is_pg() else "?"
    clauses = ["bbox_wkt IS NOT NULL"]
    params: list = []
    if aoi:
        clauses.append(f"aoi_tag = {ph}")
        params.append(aoi)
    if status_filter:
        clauses.append(f"status = {ph}")
        params.append(status_filter)
    where = "WHERE " + " AND ".join(clauses)
    params.append(limit)
    rows = _safe_fetch(
        f"SELECT id, scene_id, satellite, bbox_wkt, cloud_pct, sensing_date, "  # nosec B608
        f"thumbnail_url, relevance_score, aoi_tag, status "
        f"FROM sg_eo_signals {where} ORDER BY sensing_date DESC, relevance_score DESC LIMIT {ph}",
        params,
    )
    return jsonify({"scenes": rows, "total": len(rows)})


# ---------------------------------------------------------------------------
# OpenCTI indicators API
# ---------------------------------------------------------------------------


@_api.route("/opencti/indicators", methods=["GET"])
def api_opencti_indicators():
    itype = request.args.get("type")
    limit = int(request.args.get("limit", 50))
    ph = "%s" if is_pg() else "?"
    clauses = [f"status = {ph}"]
    params: list = ["active"]
    if itype:
        clauses.append(f"indicator_type = {ph}")
        params.append(itype)
    where = "WHERE " + " AND ".join(clauses)
    params.append(limit)
    rows = _safe_fetch(
        f"SELECT * FROM sg_opencti_indicators {where} ORDER BY confidence DESC LIMIT {ph}",  # nosec B608
        params,
    )
    return jsonify({"indicators": rows, "total": len(rows)})


# ---------------------------------------------------------------------------
# Strategos Chat API (mounted at /api/strategos)
# ---------------------------------------------------------------------------


def _require_chat_manager():
    """Return chat_manager singleton or None if unavailable."""
    try:
        from tools.dashboard.chat_manager import chat_manager
        return chat_manager
    except ImportError:
        return None


@_api.route("/chat/init", methods=["POST"])
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


@_api.route("/chat/<context_id>/inject", methods=["POST"])
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
        code = 404 if "not found" in result.get("error", "").lower() else 500
        resp = make_response(jsonify(result), code)
        resp.headers["X-Classification"] = "CUI"
        return resp
    resp = make_response(jsonify({"status": "ok", "turn_number": result.get("turn_number")}))
    resp.headers["X-Classification"] = "CUI"
    return resp


@_api.route("/chat/<context_id>/send", methods=["POST"])
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
        code = 404 if "not found" in result.get("error", "").lower() else 500
        resp = make_response(jsonify(result), code)
        resp.headers["X-Classification"] = "CUI"
        return resp
    resp = make_response(jsonify({"status": "queued", "turn_number": result.get("turn_number")}))
    resp.headers["X-Classification"] = "CUI"
    return resp


@_api.route("/chat/poll/<context_id>")
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
# Phase 4 — Sync Matrix, ISR Planner, Source Registry
# ---------------------------------------------------------------------------

@_bp.route("/sync-matrix")
@_bp.route("/sync-matrix/")
def strategos_sync_matrix():
    from tools.strategos.sync_matrix import list_matrices  # noqa: PLC0415
    matrices = []
    try:
        matrices = list_matrices(limit=20)
    except Exception:
        pass
    return render_template("strategos/sync_matrix.html", matrices=matrices)


@_api.route("/sync-matrix/matrices", methods=["GET"])
def api_sync_matrix_list():
    try:
        from tools.strategos.sync_matrix import list_matrices  # noqa: PLC0415
        theater = request.args.get("theater", "")
        rows = list_matrices(theater=theater, limit=30)
        resp = make_response(jsonify({"matrices": rows}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/sync-matrix/matrices", methods=["POST"])
def api_sync_matrix_create():
    try:
        from tools.strategos.sync_matrix import create_matrix  # noqa: PLC0415
        data = request.get_json(force=True) or {}
        result = create_matrix(
            operation_name=data.get("operation_name", "New Operation"),
            theater=data.get("theater", "unspecified"),
            time_blocks=data.get("time_blocks"),
            row_labels=data.get("row_labels"),
            created_by=data.get("created_by", "analyst"),
        )
        resp = make_response(jsonify(result), 201)
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/sync-matrix/matrices/<matrix_id>", methods=["GET"])
def api_sync_matrix_detail(matrix_id: str):
    try:
        from tools.strategos.sync_matrix import get_matrix  # noqa: PLC0415
        m = get_matrix(matrix_id)
        if not m:
            return jsonify({"error": "not found"}), 404
        resp = make_response(jsonify(m))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/sync-matrix/matrices/<matrix_id>/cell", methods=["PATCH"])
def api_sync_matrix_cell(matrix_id: str):
    try:
        from tools.strategos.sync_matrix import update_cell  # noqa: PLC0415
        data = request.get_json(force=True) or {}
        ok = update_cell(matrix_id, int(data["row"]), int(data["col"]), data.get("text", ""))
        resp = make_response(jsonify({"ok": ok}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/sync-matrix/matrices/<matrix_id>/bulk", methods=["PATCH"])
def api_sync_matrix_bulk(matrix_id: str):
    try:
        from tools.strategos.sync_matrix import bulk_update_cells  # noqa: PLC0415
        data = request.get_json(force=True) or {}
        ok = bulk_update_cells(matrix_id, data.get("cells", {}))
        resp = make_response(jsonify({"ok": ok}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/sync-matrix/matrices/<matrix_id>", methods=["DELETE"])
def api_sync_matrix_delete(matrix_id: str):
    try:
        from tools.strategos.sync_matrix import delete_matrix  # noqa: PLC0415
        ok = delete_matrix(matrix_id)
        resp = make_response(jsonify({"status": "deleted" if ok else "error"}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── ISR Planner ────────────────────────────────────────────────────────────────

@_bp.route("/isr-planner")
@_bp.route("/isr-planner/")
def strategos_isr_planner():
    from tools.strategos.isr_planner import list_requirements, list_assets, gap_analysis  # noqa: PLC0415
    reqs, assets, gaps = [], [], {}
    try:
        reqs = list_requirements()
        assets = list_assets()
        gaps = gap_analysis()
    except Exception:
        pass
    return render_template("strategos/isr_planner.html", requirements=reqs, assets=assets, gaps=gaps)


@_api.route("/isr-planner/requirements", methods=["GET"])
def api_isr_req_list():
    try:
        from tools.strategos.isr_planner import list_requirements  # noqa: PLC0415
        rows = list_requirements(theater=request.args.get("theater", ""), status=request.args.get("status", ""))
        resp = make_response(jsonify({"requirements": rows}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/isr-planner/requirements", methods=["POST"])
def api_isr_req_create():
    try:
        from tools.strategos.isr_planner import create_requirement  # noqa: PLC0415
        data = request.get_json(force=True) or {}
        result = create_requirement(
            nai=data.get("nai", ""),
            collection_type=data.get("collection_type", "IMINT"),
            priority=int(data.get("priority", 3)),
            theater=data.get("theater", "unspecified"),
            earliest=data.get("earliest", ""),
            latest=data.get("latest", ""),
            purpose=data.get("purpose", ""),
            created_by=data.get("created_by", "analyst"),
        )
        resp = make_response(jsonify(result), 201)
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/isr-planner/requirements/<req_id>/assign", methods=["PATCH"])
def api_isr_req_assign(req_id: str):
    try:
        from tools.strategos.isr_planner import assign_asset  # noqa: PLC0415
        data = request.get_json(force=True) or {}
        result = assign_asset(req_id, data.get("asset_id", ""))
        resp = make_response(jsonify(result))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/isr-planner/requirements/<req_id>/status", methods=["PATCH"])
def api_isr_req_status(req_id: str):
    try:
        from tools.strategos.isr_planner import update_requirement_status  # noqa: PLC0415
        data = request.get_json(force=True) or {}
        ok = update_requirement_status(req_id, data.get("status", "open"))
        resp = make_response(jsonify({"ok": ok}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/isr-planner/requirements/<req_id>", methods=["DELETE"])
def api_isr_req_delete(req_id: str):
    try:
        from tools.strategos.isr_planner import delete_requirement  # noqa: PLC0415
        ok = delete_requirement(req_id)
        resp = make_response(jsonify({"status": "deleted" if ok else "error"}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/isr-planner/assets", methods=["GET"])
def api_isr_asset_list():
    try:
        from tools.strategos.isr_planner import list_assets  # noqa: PLC0415
        rows = list_assets(theater=request.args.get("theater", ""))
        resp = make_response(jsonify({"assets": rows}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/isr-planner/assets", methods=["POST"])
def api_isr_asset_create():
    try:
        from tools.strategos.isr_planner import register_asset  # noqa: PLC0415
        data = request.get_json(force=True) or {}
        result = register_asset(
            asset_name=data.get("asset_name", ""),
            asset_type=data.get("asset_type", "UAV"),
            theater=data.get("theater", "unspecified"),
            notes=data.get("notes", ""),
            created_by=data.get("created_by", "analyst"),
        )
        resp = make_response(jsonify(result), 201)
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/isr-planner/assets/<asset_id>/status", methods=["PATCH"])
def api_isr_asset_status(asset_id: str):
    try:
        from tools.strategos.isr_planner import update_asset_status  # noqa: PLC0415
        data = request.get_json(force=True) or {}
        ok = update_asset_status(asset_id, data.get("status", "available"))
        resp = make_response(jsonify({"ok": ok}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/isr-planner/assets/<asset_id>", methods=["DELETE"])
def api_isr_asset_delete(asset_id: str):
    try:
        from tools.strategos.isr_planner import delete_asset  # noqa: PLC0415
        ok = delete_asset(asset_id)
        resp = make_response(jsonify({"status": "deleted" if ok else "error"}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/isr-planner/gap-analysis", methods=["GET"])
def api_isr_gap_analysis():
    try:
        from tools.strategos.isr_planner import gap_analysis  # noqa: PLC0415
        result = gap_analysis(theater=request.args.get("theater", ""))
        resp = make_response(jsonify(result))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Source Registry ────────────────────────────────────────────────────────────

@_bp.route("/sources")
@_bp.route("/sources/")
def strategos_sources():
    from tools.strategos.source_registry import list_sources, list_reports  # noqa: PLC0415
    sources, reports = [], []
    try:
        sources = list_sources()
        reports = list_reports(limit=50)
    except Exception:
        pass
    return render_template("strategos/sources.html", sources=sources, reports=reports)


@_api.route("/sources", methods=["GET"])
def api_sources_list():
    try:
        from tools.strategos.source_registry import list_sources  # noqa: PLC0415
        rows = list_sources(
            theater=request.args.get("theater", ""),
            source_type=request.args.get("source_type", ""),
        )
        resp = make_response(jsonify({"sources": rows}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/sources", methods=["POST"])
def api_sources_create():
    try:
        from tools.strategos.source_registry import create_source  # noqa: PLC0415
        data = request.get_json(force=True) or {}
        result = create_source(
            source_name=data.get("source_name", ""),
            source_type=data.get("source_type", "HUMINT"),
            reliability=data.get("reliability", "F"),
            theater=data.get("theater", "global"),
            notes=data.get("notes", ""),
            created_by=data.get("created_by", "analyst"),
        )
        resp = make_response(jsonify(result), 201)
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/sources/<source_id>", methods=["GET"])
def api_sources_detail(source_id: str):
    try:
        from tools.strategos.source_registry import list_sources  # noqa: PLC0415
        rows = list_sources()
        src = next((s for s in rows if s["id"] == source_id), None)
        if not src:
            return jsonify({"error": "not found"}), 404
        resp = make_response(jsonify(src))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/sources/<source_id>/reliability", methods=["PATCH"])
def api_sources_reliability(source_id: str):
    try:
        from tools.strategos.source_registry import update_reliability  # noqa: PLC0415
        data = request.get_json(force=True) or {}
        ok = update_reliability(source_id, data.get("reliability", "F"))
        resp = make_response(jsonify({"ok": ok}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/sources/<source_id>", methods=["DELETE"])
def api_sources_delete(source_id: str):
    try:
        from tools.strategos.source_registry import delete_source  # noqa: PLC0415
        ok = delete_source(source_id)
        resp = make_response(jsonify({"status": "deleted" if ok else "error"}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/sources/<source_id>/reports", methods=["GET"])
def api_sources_reports_list(source_id: str):
    try:
        from tools.strategos.source_registry import list_reports  # noqa: PLC0415
        rows = list_reports(source_id=source_id, limit=int(request.args.get("limit", 50)))
        resp = make_response(jsonify({"reports": rows}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/sources/<source_id>/reports", methods=["POST"])
def api_sources_report_add(source_id: str):
    try:
        from tools.strategos.source_registry import add_report  # noqa: PLC0415
        data = request.get_json(force=True) or {}
        result = add_report(
            source_id=source_id,
            content=data.get("content", ""),
            credibility=data.get("credibility", "6"),
            subject=data.get("subject", ""),
            collected_at=data.get("collected_at", ""),
        )
        resp = make_response(jsonify(result), 201)
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/iw/bayesian-update", methods=["POST"])
def api_iw_bayesian_update():
    """POST /api/strategos/iw/bayesian-update — Apply Bayesian evidence update to P(war)."""
    body = request.get_json(silent=True) or {}
    theater_id = body.get("theater_id", "ukraine")
    signals = body.get("signals", {})
    try:
        from tools.strategos.iw_bayesian import BayesianIWUpdater  # noqa: PLC0415
        upd = BayesianIWUpdater()
        if signals:
            result = upd.update_batch(theater_id, signals)
        else:
            evidence_type = body.get("evidence_type", "military_buildup")
            strength = float(body.get("strength", 0.5))
            result = upd.update(theater_id, evidence_type, strength)
        return jsonify(result), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/iw/classify", methods=["POST"])
def api_iw_classify():
    """POST /api/strategos/iw/classify — Exercise vs Pre-War GBM classification."""
    body = request.get_json(silent=True) or {}
    theater_id = body.get("theater_id", "ukraine")
    signals = body.get("signals")
    try:
        from tools.strategos.iw_bayesian import ExerciseVsPreWarClassifier  # noqa: PLC0415
        clf = ExerciseVsPreWarClassifier()
        clf.fit()
        if signals:
            result = clf.predict(signals)
            result["theater_id"] = theater_id
        else:
            result = clf.predict_from_theater(theater_id)
        return jsonify(result), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/iw/pattern-match", methods=["GET"])
def api_iw_pattern_match():
    """GET /api/strategos/iw/pattern-match — DTW historical case matching."""
    theater_id = request.args.get("theater_id", "ukraine")
    top_n = int(request.args.get("top_n", 5))
    try:
        from tools.strategos.iw_pattern_matcher import DTWPatternMatcher  # noqa: PLC0415
        matcher = DTWPatternMatcher()
        result = matcher.analyze_theater(theater_id, top_n=top_n)
        return jsonify(result), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/iw/weibull-tte", methods=["GET"])
def api_iw_weibull_tte():
    """GET /api/strategos/iw/weibull-tte — Weibull time-to-event onset estimate."""
    try:
        from tools.strategos.iw_bayesian import BayesianIWUpdater  # noqa: PLC0415
        from tools.strategos.iw_pattern_matcher import WeibullTTEEstimator  # noqa: PLC0415
        theater_id = request.args.get("theater_id", "ukraine")
        posterior = BayesianIWUpdater().get_latest_posterior(theater_id)
        tte = WeibullTTEEstimator()
        tte.fit()
        result = tte.estimate_days_to_onset(current_posterior=posterior)
        result["theater_id"] = theater_id
        return jsonify(result), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@_api.route("/sources/reports/all", methods=["GET"])
def api_sources_all_reports():
    try:
        from tools.strategos.source_registry import list_reports  # noqa: PLC0415
        rows = list_reports(limit=int(request.args.get("limit", 100)))
        resp = make_response(jsonify({"reports": rows}))
        resp.headers["X-Classification"] = "CUI"
        return resp
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


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


@_bp.route("/dat")
@_bp.route("/dat/")
def strategos_dat_page():
    (get_cables, get_unsc_events, get_backchannels,
     get_latest_dti, get_dti_history, *_) = _dat()
    theater = request.args.get("theater", "global")
    latest = get_latest_dti(theater) or {}
    history = list(reversed(get_dti_history(theater, limit=48)))
    cables = get_cables(theater, limit=20)
    unsc_events = get_unsc_events(theater, limit=15)
    backchannels = get_backchannels(theater, limit=15)
    return render_template(
        "strategos/dat.html",
        theater=theater,
        latest=latest,
        history=history,
        cables=cables,
        unsc_events=unsc_events,
        backchannels=backchannels,
    )


@_api.route("/dat/score")
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


@_api.route("/dat/history")
def api_dat_history():
    (_, _, _, _, get_dti_history, *_) = _dat()
    theater = request.args.get("theater", "global")
    limit = min(int(request.args.get("limit", 48)), 200)
    history = get_dti_history(theater, limit=limit)
    resp = make_response(jsonify({"theater": theater, "history": history, "total": len(history)}))
    resp.headers["X-Classification"] = "CUI"
    return resp


@_api.route("/dat/ingest/cable", methods=["POST"])
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


@_api.route("/dat/ingest/unsc", methods=["POST"])
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


@_api.route("/dat/ingest/backchannel", methods=["POST"])
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


@_api.route("/dat/refresh", methods=["POST"])
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
# Leadership Intelligence Brief — Predictive OSINT Analysis
# ---------------------------------------------------------------------------


@_bp.route("/leadership-brief")
@_bp.route("/leadership-brief/")
def strategos_leadership_brief():
    """Leadership Intelligence Brief dashboard."""
    return render_template("strategos/leadership_brief.html")


@_api.route("/leadership-brief/generate", methods=["POST"])
def api_leadership_brief_generate():
    """Run predictive analysis on OSINT data mesh and generate leadership briefing."""
    data = request.get_json(silent=True) or {}
    theater = (data.get("theater") or "global").strip().lower()
    save = bool(data.get("save", True))
    try:
        from tools.strategos.predictive_intel_engine import generate_leadership_brief
        result = generate_leadership_brief(theater=theater, save=save)
        resp = make_response(jsonify(result), 201)
    except Exception as exc:
        resp = make_response(jsonify({"error": str(exc)}), 500)
    resp.headers["X-Classification"] = "CUI"
    return resp


@_api.route("/leadership-brief/latest", methods=["GET"])
def api_leadership_brief_list():
    """Return recent leadership briefs."""
    theater = request.args.get("theater", "global")
    limit = min(int(request.args.get("limit", 20)), 100)
    try:
        from tools.strategos.predictive_intel_engine import list_leadership_briefs
        briefs = list_leadership_briefs(theater=theater, limit=limit)
        resp = make_response(jsonify({"briefs": briefs, "total": len(briefs)}))
    except Exception as exc:
        resp = make_response(jsonify({"error": str(exc)}), 500)
    resp.headers["X-Classification"] = "CUI"
    return resp


@_api.route("/leadership-brief/<brief_id>", methods=["GET"])
def api_leadership_brief_get(brief_id: str):
    """Return a specific leadership brief by ID."""
    try:
        from tools.strategos.predictive_intel_engine import get_leadership_brief
        brief = get_leadership_brief(brief_id)
        if brief is None:
            resp = make_response(jsonify({"error": "brief not found"}), 404)
        else:
            resp = make_response(jsonify(brief))
    except Exception as exc:
        resp = make_response(jsonify({"error": str(exc)}), 500)
    resp.headers["X-Classification"] = "CUI"
    return resp


@_api.route("/iqe-query", methods=["POST"])
def api_strategos_iqe_query():
    """IQE query endpoint for Strategos Intelligence canvas."""
    from tools.iqe.nl_to_iqe import nl_to_iqe
    from tools.iqe.parser import parse as _parse, IQESyntaxError
    from tools.iqe.executor import execute_query
    import importlib

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question required"}), 400

    collections = [
        "strategos.signals",
        "strategos.conflict_events",
        "strategos.leadership_briefs",
        "strategos.sio_assessments",
    ]

    iqe_str = ""
    try:
        importlib.import_module("tools.iqe.adapters.strategos")
        result = nl_to_iqe(question, collections)
        iqe_str = result.get("iqe", "")
        explanation = result.get("explanation", "")
        try:
            ast = _parse(iqe_str)
            rows, count = execute_query(ast)
        except IQESyntaxError as exc:
            rows, count = [], 0
            explanation = f"IQE parse error: {exc}"
    except Exception as exc:
        rows, count = [], 0
        explanation = str(exc)

    resp = make_response(jsonify({
        "ok": True,
        "canvas": "strategos",
        "iqe": iqe_str,
        "explanation": explanation,
        "results": rows,
        "row_count": count,
    }))
    resp.headers["X-Classification"] = "CUI"
    return resp


# ---------------------------------------------------------------------------
# Predictive Intelligence Briefings — page + API
# ---------------------------------------------------------------------------


@_bp.route("/intel-brief")
@_bp.route("/intel-brief/")
def strategos_intel_brief():
    """Leadership briefings dashboard — predictive analysis on OSINT data mesh."""
    import json as _json

    theater = request.args.get("theater", "global").strip() or "global"
    briefs = _safe_fetch(
        "SELECT id, theater, sio_composite_score, iw_triggered, threat_tier, "
        "signal_count_24h, conflict_event_count, signal_velocity, p_war_posterior, "
        "goldstein_avg, dti_score, forecast_24h_json, forecast_72h_json, forecast_7d_json, "
        "narrative_md, generated_at "
        "FROM sg_leadership_briefs ORDER BY generated_at DESC LIMIT 20"
    )

    # Parse forecast JSON strings to dicts so templates don't need from_json
    # Also normalize datetime fields to strings for template compatibility
    for b in briefs:
        for key in ("forecast_24h_json", "forecast_72h_json", "forecast_7d_json"):
            raw = b.get(key)
            if isinstance(raw, str):
                try:
                    b[key] = _json.loads(raw)
                except Exception:
                    b[key] = {}
        if b.get("generated_at") and not isinstance(b["generated_at"], str):
            b["generated_at"] = str(b["generated_at"])

    latest_brief = briefs[0] if briefs else None
    briefs_json = _json.dumps(briefs, default=str)

    return render_template(
        "strategos/intel_brief.html",
        theater=theater,
        briefs=briefs,
        latest_brief=latest_brief,
        briefs_json=briefs_json,
    )


@_api.route("/intel-brief/run", methods=["POST"])
def api_intel_brief_run():
    """Trigger the predictive analysis pipeline and generate a leadership brief."""
    data = request.get_json(silent=True) or {}
    theater = (data.get("theater") or "global").strip() or "global"
    try:
        from tools.strategos.predictive_analysis import PredictiveAnalysisEngine
        engine = PredictiveAnalysisEngine()
        result = engine.run(theater=theater)
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@_api.route("/intel-brief/briefs")
def api_intel_brief_list():
    """Return recent leadership briefs as JSON."""
    limit = min(int(request.args.get("limit", 50)), 200)
    theater = request.args.get("theater", "").strip()
    clauses, params = [], []
    if theater:
        clauses.append("theater = ?")
        params.append(theater)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    ph = "%s" if is_pg() else "?"
    sql = (
        f"SELECT id, theater, sio_composite_score, iw_triggered, threat_tier, "
        f"signal_count_24h, conflict_event_count, signal_velocity, p_war_posterior, "
        f"goldstein_avg, dti_score, generated_at "
        f"FROM sg_leadership_briefs {where} ORDER BY generated_at DESC LIMIT {limit}"  # nosec B608
    )
    sql = sql.replace("?", ph)
    rows = _safe_fetch(sql, params)
    return jsonify({"ok": True, "briefs": rows, "total": len(rows)})


@_api.route("/intel-brief/status")
def api_intel_brief_status():
    """Return current WRI and escalation status from the latest leadership brief."""
    row = _safe_fetch(
        "SELECT sio_composite_score, threat_tier, p_war_posterior, escalation_rung, "
        "dti_score, iw_triggered, signal_count_24h, generated_at "
        "FROM sg_leadership_briefs ORDER BY generated_at DESC LIMIT 1"
    )
    if not row:
        return jsonify({"ok": True, "status": "no_data", "wri": 0, "threat_tier": "LOW"})
    r = row[0]
    return jsonify({
        "ok": True,
        "wri": r.get("sio_composite_score", 0),
        "threat_tier": r.get("threat_tier", "LOW"),
        "p_war_posterior": r.get("p_war_posterior", 0.05),
        "dti_score": r.get("dti_score"),
        "iw_triggered": bool(r.get("iw_triggered")),
        "signal_count_24h": r.get("signal_count_24h", 0),
        "generated_at": r.get("generated_at"),
    })


# ---------------------------------------------------------------------------
# Factory functions called from tools/dashboard/app.py
# ---------------------------------------------------------------------------

def create_strategos_blueprint():
    """Return the page blueprint (mounted at /strategos)."""
    return _bp


def create_strategos_api_blueprint():
    """Return the API blueprint (mounted at /api/strategos)."""
    return _api
