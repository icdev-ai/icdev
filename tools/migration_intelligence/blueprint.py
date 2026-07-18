#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration Intelligence Engine — Flask Blueprint.

Routes:
  GET  /migration-intel/                     → Dashboard index
  GET  /api/migration-intel/status           → Engine status
  GET  /api/migration-intel/pipeline-report  → Full pipeline report
  POST /api/migration-intel/run              → Trigger full pipeline
  POST /api/migration-intel/scan             → Canvas scan only

  # Goals
  GET  /api/migration-intel/goals            → List goals
  POST /api/migration-intel/goals            → Create goal (structured)
  POST /api/migration-intel/goals/chat       → NL chat goal parsing
  GET  /api/migration-intel/goals/<id>       → Get goal
  PATCH /api/migration-intel/goals/<id>      → Update goal

  # Opportunities
  GET  /api/migration-intel/opportunities    → List opportunities
  GET  /api/migration-intel/opportunities/<id> → Get opportunity
  POST /api/migration-intel/opportunities/<id>/strategies → Generate strategies

  # Strategies
  GET  /api/migration-intel/strategies       → List all strategies

  # Roadmaps
  GET  /api/migration-intel/roadmaps         → List roadmaps
  POST /api/migration-intel/roadmaps         → Build new roadmap
  GET  /api/migration-intel/roadmaps/<id>    → Get roadmap

  # Wishlist / Budget
  GET  /api/migration-intel/wishlist         → List wishlist items
  POST /api/migration-intel/wishlist         → Add wishlist item
  PATCH /api/migration-intel/wishlist/<id>   → Update wishlist item
  DELETE /api/migration-intel/wishlist/<id>  → Remove wishlist item

  GET  /api/migration-intel/budget-cycles    → List budget cycles
  POST /api/migration-intel/budget-cycles    → Create budget cycle
  GET  /api/migration-intel/budget-cycles/<id>/summary → Budget summary

  # Scan history
  GET  /api/migration-intel/scans            → Scan history
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, redirect, render_template, request, session

bp = Blueprint("migration_intel", __name__, url_prefix="")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_DB_PATH = str(BASE_DIR / "data" / "migration_intel.db")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# =========================================================================
# AUTH (cnr-mi-02) — mirrors mdc_login_required in tools/migration_canvas
# =========================================================================

@bp.before_request
def _mi_require_auth():
    """Gate every migration-intel route behind an authenticated session.

    cnr-mi-02: previously every route — including POST /api/migration-intel/run
    and /scan, which execute full cross-canvas pipelines — was unauthenticated.
    A blueprint-wide before_request hook (rather than a per-route decorator) is
    used so no route can silently miss the gate. Mirrors the Migration Canvas
    policy: JSON/API/mutating requests get 401, browser page requests redirect
    to /login. ICDEV_AUTH_BYPASS opts out for E2E/CI, consistent with
    tools/dashboard/auth.py. Returning None lets the request proceed.
    """
    if os.environ.get("ICDEV_AUTH_BYPASS", "").lower() in ("1", "true", "yes"):
        return None
    if session.get("user_id"):
        return None
    if (
        request.is_json
        or request.path.startswith("/api/migration-intel/")
        or request.method in ("DELETE", "POST", "PUT", "PATCH")
    ):
        return jsonify({"error": "Authentication required"}), 401
    return redirect("/login")


def _get_conn():
    # cnr-mi-01: schema is initialized once at blueprint registration, not on
    # every request. Return a StorageConnection (RLS disabled) so %s placeholders
    # translate correctly on both SQLite and PostgreSQL.
    from tools.migration_intelligence.db.init_db import get_connection
    return get_connection(_DB_PATH)


def _row_to_dict(row) -> dict:
    return dict(row) if row else {}


# =========================================================================
# PAGES
# =========================================================================

@bp.route("/migration-intel/")
@bp.route("/migration-intel")
def mi_index():
    conn = _get_conn()
    try:
        goals = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM mi_goals WHERE status='active' ORDER BY priority DESC LIMIT 20"
        ).fetchall()]
        opps = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM mi_opportunities ORDER BY composite_score DESC LIMIT 20"
        ).fetchall()]
        roadmaps = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM mi_roadmaps ORDER BY generated_at DESC LIMIT 5"
        ).fetchall()]
        wishlist_total = conn.execute(
            "SELECT COUNT(*) as c, SUM(total_cost_usd) as total FROM mi_wishlist WHERE status != 'cancelled'"
        ).fetchone()
        last_scan = _row_to_dict(conn.execute(
            "SELECT * FROM mi_scans ORDER BY started_at DESC LIMIT 1"
        ).fetchone() or {})
    finally:
        conn.close()

    return render_template(
        "migration_intelligence/index.html",
        goals=goals,
        opportunities=opps,
        roadmaps=roadmaps,
        wishlist_count=wishlist_total["c"] if wishlist_total else 0,
        wishlist_total_cost=wishlist_total["total"] or 0 if wishlist_total else 0,
        last_scan=last_scan,
    )


# =========================================================================
# ENGINE STATUS
# =========================================================================

@bp.route("/api/migration-intel/status")
def mi_api_status():
    from tools.migration_intelligence.migration_manager import get_status
    return jsonify(get_status(db_path=_DB_PATH))


@bp.route("/api/migration-intel/pipeline-report")
def mi_api_pipeline_report():
    from tools.migration_intelligence.migration_manager import get_pipeline_report
    return jsonify(get_pipeline_report(db_path=_DB_PATH))


@bp.route("/api/migration-intel/run", methods=["POST"])
def mi_api_run():
    from tools.migration_intelligence.migration_manager import run_full_pipeline
    result = run_full_pipeline(db_path=_DB_PATH)
    return jsonify(result)


@bp.route("/api/migration-intel/scan", methods=["POST"], strict_slashes=False)
def mi_api_scan():
    from tools.migration_intelligence.opportunity_scanner import run_full_scan
    result = run_full_scan(db_path=_DB_PATH)
    return jsonify(result)


# =========================================================================
# GOALS
# =========================================================================

@bp.route("/api/migration-intel/goals", methods=["GET"])
def mi_api_goals_list():
    status = request.args.get("status")
    category = request.args.get("category")
    from tools.migration_intelligence.goal_manager import list_goals
    goals = list_goals(status=status, category=category, db_path=_DB_PATH)
    return jsonify({"goals": goals, "count": len(goals)})


@bp.route("/api/migration-intel/goals", methods=["POST"])
def mi_api_goals_create():
    data = request.get_json(force=True, silent=True) or {}
    if not data.get("title"):
        return jsonify({"error": "title required"}), 400
    from tools.migration_intelligence.goal_manager import create_goal
    result = create_goal(
        title=data["title"],
        description=data.get("description", ""),
        category=data.get("category", "infrastructure"),
        horizon_years=int(data.get("horizon_years", 5)),
        target_year=data.get("target_year"),
        priority=data.get("priority", "medium"),
        source=data.get("source", "manual"),
        owner=data.get("owner", ""),
        success_criteria=data.get("success_criteria", ""),
        kpis=data.get("kpis", []),
        tags=data.get("tags", []),
        created_by=data.get("created_by", "dashboard"),
        db_path=_DB_PATH,
    )
    return jsonify(result), 201


@bp.route("/api/migration-intel/goals/chat", methods=["POST"])
def mi_api_goals_chat():
    data = request.get_json(force=True, silent=True) or {}
    user_input = data.get("message") or data.get("input") or ""
    if not user_input.strip():
        return jsonify({"error": "message required"}), 400
    from tools.migration_intelligence.goal_manager import parse_goals_from_chat
    result = parse_goals_from_chat(user_input, created_by="chat-ui", db_path=_DB_PATH)
    return jsonify(result)


@bp.route("/api/migration-intel/goals/<goal_id>", methods=["GET"])
def mi_api_goals_get(goal_id):
    from tools.migration_intelligence.goal_manager import get_goal
    g = get_goal(goal_id, db_path=_DB_PATH)
    if not g:
        return jsonify({"error": "not found"}), 404
    return jsonify(g)


@bp.route("/api/migration-intel/goals/<goal_id>", methods=["PATCH"])
def mi_api_goals_update(goal_id):
    data = request.get_json(force=True, silent=True) or {}
    from tools.migration_intelligence.goal_manager import update_goal
    ok = update_goal(goal_id, data, db_path=_DB_PATH)
    return jsonify({"ok": ok})


# =========================================================================
# OPPORTUNITIES
# =========================================================================

@bp.route("/api/migration-intel/opportunities", methods=["GET"])
def mi_api_opps_list():
    status = request.args.get("status")
    limit = int(request.args.get("limit", 50))
    conn = _get_conn()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM mi_opportunities WHERE status=%s ORDER BY composite_score DESC LIMIT %s",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM mi_opportunities ORDER BY composite_score DESC LIMIT %s", (limit,)
            ).fetchall()
        opps = [_row_to_dict(r) for r in rows]
    finally:
        conn.close()
    return jsonify({"opportunities": opps, "count": len(opps)})


@bp.route("/api/migration-intel/opportunities/<opp_id>", methods=["GET"])
def mi_api_opps_get(opp_id):
    conn = _get_conn()
    try:
        opp = _row_to_dict(conn.execute("SELECT * FROM mi_opportunities WHERE id=%s", (opp_id,)).fetchone())
        if not opp:
            return jsonify({"error": "not found"}), 404
        strats = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM mi_strategies WHERE opportunity_id=%s ORDER BY recommended DESC", (opp_id,)
        ).fetchall()]
        aligns = [_row_to_dict(r) for r in conn.execute(
            "SELECT a.*, g.title as goal_title FROM mi_goal_alignments a "
            "JOIN mi_goals g ON a.goal_id=g.id WHERE a.opportunity_id=%s", (opp_id,)
        ).fetchall()]
    finally:
        conn.close()
    return jsonify({"opportunity": opp, "strategies": strats, "alignments": aligns})


@bp.route("/api/migration-intel/opportunities/<opp_id>/strategies", methods=["POST"])
def mi_api_opps_generate_strategies(opp_id):
    from tools.migration_intelligence.strategy_generator import generate_strategies_for_opportunity
    result = generate_strategies_for_opportunity(opp_id, db_path=_DB_PATH)
    return jsonify(result)


# =========================================================================
# STRATEGIES
# =========================================================================

@bp.route("/api/migration-intel/strategies", methods=["GET"])
def mi_api_strategies_list():
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT s.*, o.title as opportunity_title FROM mi_strategies s "
            "LEFT JOIN mi_opportunities o ON s.opportunity_id=o.id "
            "ORDER BY s.created_at DESC LIMIT 100"
        ).fetchall()
        strats = [_row_to_dict(r) for r in rows]
    finally:
        conn.close()
    return jsonify({"strategies": strats, "count": len(strats)})


# =========================================================================
# ROADMAPS
# =========================================================================

@bp.route("/api/migration-intel/roadmaps", methods=["GET"])
def mi_api_roadmaps_list():
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT * FROM mi_roadmaps ORDER BY generated_at DESC").fetchall()
        roadmaps = [_row_to_dict(r) for r in rows]
    finally:
        conn.close()
    return jsonify({"roadmaps": roadmaps, "count": len(roadmaps)})


@bp.route("/api/migration-intel/roadmaps", methods=["POST"])
def mi_api_roadmaps_build():
    data = request.get_json(force=True, silent=True) or {}
    from tools.migration_intelligence.strategy_generator import build_roadmap
    result = build_roadmap(
        name=data.get("name", "Migration Roadmap"),
        horizon_years=int(data.get("horizon_years", 5)),
        db_path=_DB_PATH,
    )
    return jsonify(result), 201


@bp.route("/api/migration-intel/roadmaps/<rm_id>", methods=["GET"])
def mi_api_roadmaps_get(rm_id):
    conn = _get_conn()
    try:
        row = _row_to_dict(conn.execute("SELECT * FROM mi_roadmaps WHERE id=%s", (rm_id,)).fetchone())
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404
    # Decode payload JSON
    if row.get("payload"):
        try:
            row["payload"] = json.loads(row["payload"])
        except Exception:
            pass
    return jsonify(row)


# =========================================================================
# WISHLIST
# =========================================================================

@bp.route("/api/migration-intel/wishlist", methods=["GET"])
def mi_api_wishlist_list():
    status = request.args.get("status")
    fy = request.args.get("fiscal_year")
    conn = _get_conn()
    try:
        where, params = [], []
        if status:
            where.append("status=%s"); params.append(status)
        if fy:
            where.append("fiscal_year=%s"); params.append(int(fy))
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(
            f"SELECT * FROM mi_wishlist {clause} ORDER BY replacement_urgency DESC, fiscal_year ASC",
            params,
        ).fetchall()
        items = [_row_to_dict(r) for r in rows]
    finally:
        conn.close()
    return jsonify({"wishlist": items, "count": len(items)})


@bp.route("/api/migration-intel/wishlist", methods=["POST"])
def mi_api_wishlist_create():
    data = request.get_json(force=True, silent=True) or {}
    if not data.get("title"):
        return jsonify({"error": "title required"}), 400
    item_id = f"wl-{uuid.uuid4().hex[:10]}"
    now = _now()
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO mi_wishlist
               (id, title, description, item_type, vendor, product_model,
                quantity, unit_cost_usd, total_cost_usd, fiscal_year, quarter,
                budget_category, current_eol_date, replacement_urgency,
                linked_opportunity_id, linked_goal_ids, priority, status,
                requestor, justification, tags, notes, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                item_id,
                data["title"],
                data.get("description", ""),
                data.get("item_type", "hardware"),
                data.get("vendor", ""),
                data.get("product_model", ""),
                int(data.get("quantity", 1)),
                data.get("unit_cost_usd"),
                data.get("total_cost_usd"),
                data.get("fiscal_year"),
                data.get("quarter"),
                data.get("budget_category", "capex"),
                data.get("current_eol_date"),
                data.get("replacement_urgency", "planned"),
                data.get("linked_opportunity_id"),
                json.dumps(data.get("linked_goal_ids", [])),
                data.get("priority", "medium"),
                "wishlist",
                data.get("requestor", ""),
                data.get("justification", ""),
                json.dumps(data.get("tags", [])),
                data.get("notes", ""),
                now, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"id": item_id, "title": data["title"]}), 201


@bp.route("/api/migration-intel/wishlist/<item_id>", methods=["PATCH"])
def mi_api_wishlist_update(item_id):
    data = request.get_json(force=True, silent=True) or {}
    allowed = {"title", "description", "vendor", "product_model", "quantity", "unit_cost_usd",
               "total_cost_usd", "fiscal_year", "quarter", "budget_category", "current_eol_date",
               "replacement_urgency", "linked_opportunity_id", "priority", "status",
               "requestor", "justification", "notes"}
    cols = {k: v for k, v in data.items() if k in allowed}
    if not cols:
        return jsonify({"error": "no valid fields"}), 400
    cols["updated_at"] = _now()
    set_clause = ", ".join(f"{k}=?" for k in cols)
    conn = _get_conn()
    try:
        conn.execute(f"UPDATE mi_wishlist SET {set_clause} WHERE id=%s", [*cols.values(), item_id])
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@bp.route("/api/migration-intel/wishlist/<item_id>", methods=["DELETE"])
def mi_api_wishlist_delete(item_id):
    conn = _get_conn()
    try:
        conn.execute("UPDATE mi_wishlist SET status='cancelled', updated_at=%s WHERE id=%s", (_now(), item_id))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


# =========================================================================
# BUDGET CYCLES
# =========================================================================

@bp.route("/api/migration-intel/budget-cycles", methods=["GET"])
def mi_api_budget_list():
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT * FROM mi_budget_cycles ORDER BY fiscal_year DESC").fetchall()
        cycles = [_row_to_dict(r) for r in rows]
    finally:
        conn.close()
    return jsonify({"budget_cycles": cycles, "count": len(cycles)})


@bp.route("/api/migration-intel/budget-cycles", methods=["POST"])
def mi_api_budget_create():
    data = request.get_json(force=True, silent=True) or {}
    if not data.get("fiscal_year") or not data.get("name"):
        return jsonify({"error": "fiscal_year and name required"}), 400
    cycle_id = f"bc-{uuid.uuid4().hex[:8]}"
    now = _now()
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO mi_budget_cycles
               (id, name, fiscal_year, budget_type, total_budget_usd,
                allocated_usd, committed_usd, spent_usd, status, notes, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,0,0,0,%s,%s,%s,%s)""",
            (
                cycle_id, data["name"], int(data["fiscal_year"]),
                data.get("budget_type", "annual"),
                data.get("total_budget_usd"),
                "planning",
                data.get("notes", ""),
                now, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"id": cycle_id}), 201


@bp.route("/api/migration-intel/budget-cycles/<cycle_id>/summary", methods=["GET"])
def mi_api_budget_summary(cycle_id):
    conn = _get_conn()
    try:
        cycle = _row_to_dict(conn.execute(
            "SELECT * FROM mi_budget_cycles WHERE id=%s", (cycle_id,)
        ).fetchone() or {})
        if not cycle:
            return jsonify({"error": "not found"}), 404

        fy = cycle.get("fiscal_year")
        items = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM mi_wishlist WHERE fiscal_year=%s AND status != 'cancelled'", (fy,)
        ).fetchall()]

        by_category: dict = {}
        for it in items:
            cat = it.get("budget_category", "capex")
            by_category.setdefault(cat, {"count": 0, "total_cost_usd": 0.0})
            by_category[cat]["count"] += 1
            by_category[cat]["total_cost_usd"] += float(it.get("total_cost_usd") or 0)

        total_budgeted = sum(d["total_cost_usd"] for d in by_category.values())
        budget_remaining = (cycle.get("total_budget_usd") or 0) - total_budgeted
    finally:
        conn.close()

    return jsonify({
        "cycle": cycle,
        "wishlist_items": len(items),
        "by_category": by_category,
        "total_budgeted": total_budgeted,
        "budget_remaining": budget_remaining,
    })


# =========================================================================
# SCAN HISTORY
# =========================================================================

@bp.route("/api/migration-intel/scans", methods=["GET"])
def mi_api_scans_list():
    limit = int(request.args.get("limit", 20))
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM mi_scans ORDER BY started_at DESC LIMIT %s", (limit,)
        ).fetchall()
        scans = [_row_to_dict(r) for r in rows]
    finally:
        conn.close()
    return jsonify({"scans": scans, "count": len(scans)})


def create_migration_intel_blueprint():
    # cnr-mi-01: initialize the schema ONCE at registration instead of on every
    # request (_get_conn previously ran a full executescript per call).
    try:
        from tools.migration_intelligence.db.init_db import init_db
        init_db(_DB_PATH)
    except Exception:  # noqa: BLE001 — never block dashboard startup on DB init
        pass
    return bp
