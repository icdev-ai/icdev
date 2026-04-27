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
"""
import json
from datetime import datetime, timezone

from flask import Blueprint, jsonify, render_template, request, Response

from tools.db.storage import get_connection, is_pg
from tools.intelligence.brief_generator import BRIEF_TYPES, BriefGenerator
from tools.intelligence.pir_manager import (
    coverage_gap_hours,
    create_pir,
    list_pirs,
    update_pir,
)
from tools.strategos.interdiction_ranker import (
    auto_write_interdicts_from_conflict_events,
    get_latest_ranked_targets,
    get_supply_degradation_coefficients,
    rank_interdiction_targets,
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
            f"UPDATE sg_prioritized_signals SET {set_clause} WHERE id = {ph}",
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
        "briefs_total":  _safe_count("SELECT COUNT(*) FROM sg_intelligence_briefs"),
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
        f"SELECT * FROM sg_orbat_units {where} ORDER BY unit_name ASC", params
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
        f"SELECT * FROM sg_ghost_signals {where} ORDER BY detected_at DESC LIMIT 200",
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
        f"SELECT * FROM sg_iw_effects {where} ORDER BY detected_at DESC LIMIT 200",
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
    params = [selected_state] if selected_state else []
    where = "WHERE state = ?" if selected_state else ""
    wargames = _safe_fetch(
        f"SELECT * FROM sg_wargames {where} ORDER BY created_at DESC LIMIT 100",
        params,
    )
    wargame_states = [
        r["state"]
        for r in _safe_fetch(
            "SELECT DISTINCT state FROM sg_wargames ORDER BY state"
        )
    ]
    return render_template(
        "strategos/wargame.html",
        wargames=wargames,
        wargame_states=wargame_states,
        selected_state=selected_state,
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
        f"{where} ORDER BY p.composite_score DESC LIMIT 50"
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


@_bp.route("/briefs")
def strategos_briefs():
    selected_type = request.args.get("type", "")
    params = [selected_type] if selected_type else []
    where = "WHERE brief_type = ?" if selected_type else ""
    briefs = _safe_fetch(
        f"SELECT id, brief_type, title, sio_confidence, analyst_reviewed, "
        f"reviewed_by, reviewed_at, created_at "
        f"FROM sg_intelligence_briefs {where} ORDER BY created_at DESC LIMIT 200",
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


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

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
        f"SELECT is_read FROM sg_prioritized_signals WHERE id = {ph}", (sig_id,)
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
        f"SELECT promoted_to_kg FROM sg_prioritized_signals WHERE id = {ph}", (sig_id,)
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
        f"WHERE p.id IN ({placeholders})",
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
# Factory functions called from tools/dashboard/app.py
# ---------------------------------------------------------------------------

def create_strategos_blueprint():
    """Return the page blueprint (mounted at /strategos)."""
    return _bp


def create_strategos_api_blueprint():
    """Return the API blueprint (mounted at /api/strategos)."""
    return _api
