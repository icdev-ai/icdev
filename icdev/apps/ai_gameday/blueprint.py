# CUI // SP-CTI
"""AI GameDay Flask blueprint — all /gameday/* and /api/gameday/* routes."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

import yaml

from flask import Blueprint, jsonify, render_template, request

from .constants import (
    APP_NAME, SCENARIO_SLUG, INJECT_TYPES, LEVELS,
    EVENT_ONTOLOGY_TYPES, SCOREBOARD_ONTOLOGY_FILTERS, catalog_for_render)
from . import registration as _registration
from .db import migrate
from tools.ai_game_engine.ontology import (
    resolve_scenario_ontology, resolve_role_ontology, filter_by_ontology_class,
    ONTOLOGY_NAMESPACES,
)
from tools.ai_game_engine.scenario_registry import load_scenario
from tools.ttx.engine import TTXEngine
from tools.ttx.scenario_loader import list_scenario_slugs
from tools.ttx.session_manager import get_session, list_sessions, get_session_by_code
from tools.ttx.team_manager import list_teams, get_team_by_code
from tools.ttx.inject_dispatcher import (
    get_all_injects, get_dispatched_injects,
)
from tools.ttx.leaderboard import get_leaderboard, compute_leaderboard, award_ribbons
from tools.ttx.aar_generator import generate_aar
from tools.db.storage import get_connection
from .auth import login_required, require_facilitator

bp = Blueprint("ai_gameday", __name__)
_engine = TTXEngine()
_initialized = False


def _ensure_init() -> None:
    global _initialized
    if not _initialized:
        try:
            migrate()
            _initialized = True
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("AI GameDay init failed: %s", exc)


def _gameday_tenant_id() -> str | None:
    try:
        from tools.saas.auth.middleware import get_current_tenant_id
        return get_current_tenant_id()
    except Exception:
        return None


def _session_for_tenant(session_id: int):
    """Return the session iff it belongs to the caller's tenant, else None.

    Prevents cross-tenant enumeration on the response/leaderboard/simulate-state
    endpoints, which key off sequential integer session IDs. When no tenant
    context is active (single-tenant deployments), get_session falls back to an
    unscoped lookup, matching the list-page behaviour.
    """
    return get_session(session_id, tenant_id=_gameday_tenant_id())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@bp.route("/gameday")
@login_required
def hub():
    _ensure_init()
    _tid = _gameday_tenant_id()
    active = list_sessions(state="active", tenant_id=_tid)
    pending = list_sessions(state="pending", tenant_id=_tid)
    ended = list_sessions(state="ended", tenant_id=_tid)[-10:]
    scenarios = list_scenario_slugs()
    return render_template(
        "ai_gameday/hub.html",
        app_name=APP_NAME,
        active_sessions=active,
        pending_sessions=pending,
        past_sessions=ended,
        scenarios=scenarios,
        ontology_filters=SCOREBOARD_ONTOLOGY_FILTERS,
        ontology_namespaces=ONTOLOGY_NAMESPACES,
    )


@bp.route("/gameday/session/<int:session_id>/play")
@login_required
def player_console(session_id: int):
    _ensure_init()
    session = get_session(session_id)
    if not session:
        return "Session not found", 404
    teams = list_teams(session_id)
    cfg = json.loads(session.get("config_json") or "{}")
    scenario = cfg.get("scenario", {})
    roles = scenario.get("roles", [])
    # Enrich roles with ontology tags
    for role in roles:
        role["ontology"] = resolve_role_ontology(role.get("id", ""))
    return render_template(
        "ai_gameday/player.html",
        app_name=APP_NAME,
        session=session,
        teams=teams,
        roles=roles,
        ai_tools=catalog_for_render(),
        levels=LEVELS,
        event_ontology_types=EVENT_ONTOLOGY_TYPES,
    )


@bp.route("/gameday/session/<int:session_id>/facilitate")
@require_facilitator
def facilitator_console(session_id: int):
    _ensure_init()
    session = get_session(session_id)
    if not session:
        return "Session not found", 404
    teams = list_teams(session_id)
    injects = get_all_injects(session_id)
    lb = get_leaderboard(session_id)
    # Annotate inject type for template
    for inj in injects:
        cfg = json.loads(inj.get("config_json") or "{}")
        inj["is_aadc"] = cfg.get("inject_type") == "aadc_design_challenge"
    # Enrich injects with ontology tags
    for inj in injects:
        cfg = json.loads(inj.get("config_json") or "{}")
        inj["ontology_tags_json"] = inj.get("ontology_tags_json", "{}")
    return render_template(
        "ai_gameday/facilitator.html",
        app_name=APP_NAME,
        session=session,
        teams=teams,
        injects=injects,
        leaderboard=lb,
        ontology_filters=SCOREBOARD_ONTOLOGY_FILTERS,
        event_ontology_types=EVENT_ONTOLOGY_TYPES,
    )


@bp.route("/gameday/leaderboard/<int:session_id>")
@login_required
def live_leaderboard(session_id: int):
    _ensure_init()
    session = get_session(session_id)
    if not session:
        return "Session not found", 404
    lb = get_leaderboard(session_id)
    ribbons = award_ribbons(session_id)
    from tools.ttx.constants import RIBBON_DEFS
    return render_template(
        "ai_gameday/leaderboard.html",
        app_name=APP_NAME,
        session=session,
        leaderboard=lb,
        ribbons=ribbons,
        ribbon_defs=RIBBON_DEFS,
        ontology_filters=SCOREBOARD_ONTOLOGY_FILTERS,
    )


@bp.route("/gameday/scenarios")
@require_facilitator
def scenario_manager():
    _ensure_init()
    slugs = list_scenario_slugs()
    scenario_list = []
    for slug in slugs:
        try:
            s = load_scenario(slug)
            scenario_list.append({
                "slug": slug,
                "name": s.get("name", slug),
                "session_mode": s.get("session_mode", "live"),
                "inject_count": len(s.get("injects", [])),
                "duration_minutes": s.get("duration_minutes", 120),
                "ontology": s.get("ontology", resolve_scenario_ontology(slug)),
            })
        except Exception:
            scenario_list.append({"slug": slug, "name": slug, "error": True, "ontology": resolve_scenario_ontology(slug)})
    # Also load DB-authored scenarios
    conn = get_connection()
    db_scenarios = conn.execute(
        "SELECT scenario_id, slug, name, created_at FROM ttx_scenarios WHERE is_active = 1 ORDER BY created_at DESC"
    ).fetchall()
    return render_template(
        "ai_gameday/scenario_manager.html",
        app_name=APP_NAME,
        file_scenarios=scenario_list,
        db_scenarios=[dict(r) for r in db_scenarios],
        inject_types=INJECT_TYPES,
    )


@bp.route("/gameday/scenarios/builder")
@require_facilitator
def scenario_builder():
    _ensure_init()
    inject_types = INJECT_TYPES
    ai_tools = catalog_for_render()
    # Load rubric names from DB inject templates
    conn = get_connection()
    templates = conn.execute(
        "SELECT template_id, name, inject_type FROM ttx_inject_templates ORDER BY name"
    ).fetchall()
    return render_template(
        "ai_gameday/scenario_builder.html",
        app_name=APP_NAME,
        inject_types=inject_types,
        ai_tools=ai_tools,
        inject_templates=[dict(r) for r in templates],
    )


@bp.route("/gameday/session/<int:session_id>/results")
@require_facilitator
def session_results(session_id: int):
    _ensure_init()
    session = get_session(session_id)
    if not session:
        return "Session not found", 404
    injects = get_all_injects(session_id)
    lb = get_leaderboard(session_id)
    ribbons = award_ribbons(session_id)
    from tools.ttx.constants import RIBBON_DEFS
    aar_md = generate_aar(session_id)

    conn = get_connection()
    teams = list_teams(session_id)

    # Per-team AI usage stats
    ai_stats = {}
    for team in teams:
        tid = team["team_id"]
        receipt_total = conn.execute(
            "SELECT COALESCE(SUM(receipt_count), 0) FROM ttx_scores WHERE team_id = %s", (tid,)
        ).fetchone()[0] or 0
        tools_used = conn.execute(
            "SELECT DISTINCT tool_slug FROM ttx_api_log WHERE team_id = %s", (tid,)
        ).fetchall()
        ai_stats[tid] = {
            "receipt_total": receipt_total,
            "tools": [r["tool_slug"] for r in tools_used],
        }

    # Per-inject scores: {inject_id: {team_id: {receipt_pts, judge_pts, time_bonus_pts, total_pts, aadc_score}}}
    inject_scores = {}
    for inj in injects:
        iid = inj["inject_id"]
        cfg = json.loads(inj.get("config_json") or "{}")
        is_aadc = cfg.get("inject_type") == "aadc_design_challenge"
        inj["is_aadc"] = is_aadc  # annotate for template
        rows = conn.execute(
            """SELECT r.team_id, s.receipt_pts, s.judge_pts, s.time_bonus_pts,
                      s.total_pts, s.judge_rationale_json
               FROM ttx_responses r
               LEFT JOIN ttx_scores s ON s.response_id = r.response_id
               WHERE r.inject_id = %s""",
            (iid,),
        ).fetchall()
        inject_scores[iid] = {}
        for row in rows:
            row = dict(row)
            entry = {
                "receipt_pts": row.get("receipt_pts") or 0,
                "judge_pts": row.get("judge_pts") or 0,
                "time_bonus_pts": row.get("time_bonus_pts") or 0,
                "total_pts": row.get("total_pts") or 0,
            }
            if is_aadc:
                entry["aadc_score"] = row.get("judge_pts") or 0
            inject_scores[iid][row["team_id"]] = entry

    # Enrich session with ontology
    session["ontology_tags"] = resolve_scenario_ontology(session.get("scenario_slug", ""))
    return render_template(
        "ai_gameday/session_results.html",
        app_name=APP_NAME,
        session=session,
        injects=injects,
        leaderboard=lb,
        ribbons=ribbons,
        ribbon_defs=RIBBON_DEFS,
        aar_md=aar_md,
        ai_stats=ai_stats,
        teams={t["team_id"]: t for t in teams},
        inject_scores=inject_scores,
        ontology_filters=SCOREBOARD_ONTOLOGY_FILTERS,
        event_ontology_types=EVENT_ONTOLOGY_TYPES,
    )


# ---------------------------------------------------------------------------
# API — Session management
# ---------------------------------------------------------------------------

@bp.route("/api/gameday/session", methods=["POST"])
@require_facilitator
def api_create_session():
    _ensure_init()
    data = request.get_json(force=True) or {}
    slug = data.get("scenario_slug", SCENARIO_SLUG)
    facilitator = data.get("facilitator_name", "Facilitator")
    mode = data.get("session_mode")
    try:
        session = _engine.create_session(slug, facilitator, session_mode=mode, tenant_id=_gameday_tenant_id())
        # Enrich session config with ontology tags
        conn = get_connection()
        scenario_ontology = resolve_scenario_ontology(slug)
        conn.execute(
            "UPDATE ttx_sessions SET ontology_tags_json = %s WHERE session_id = %s",
            (json.dumps(scenario_ontology), session["session_id"]),
        )
        conn.commit()
        session["ontology_tags"] = scenario_ontology
        return jsonify({"ok": True, "session": session}), 201
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/gameday/session/<int:session_id>/state", methods=["PATCH"])
@require_facilitator
def api_update_session_state(session_id: int):
    data = request.get_json(force=True) or {}
    new_state = data.get("state", "")
    try:
        if new_state == "active":
            session = _engine.start_session(session_id)
        elif new_state == "paused":
            session = _engine.pause_session(session_id)
        elif new_state == "ended":
            session = _engine.end_session(session_id)
        else:
            return jsonify({"ok": False, "error": f"Unknown state: {new_state}"}), 400
        return jsonify({"ok": True, "session": session})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@bp.route("/api/gameday/session/join", methods=["POST"])
@login_required
def api_join_session():
    _ensure_init()
    data = request.get_json(force=True) or {}
    session_code = data.get("session_code", "").strip().upper()
    team_name = data.get("team_name", "").strip()
    if not session_code:
        return jsonify({"ok": False, "error": "session_code required"}), 400
    session = get_session_by_code(session_code)
    if not session:
        return jsonify({"ok": False, "error": "Invalid session code"}), 404
    if session["state"] not in ("pending", "active"):
        return jsonify({"ok": False, "error": "Session not joinable"}), 409
    if team_name:
        team = _engine.create_team(session["session_id"], team_name)
    else:
        return jsonify({"ok": False, "error": "team_name required"}), 400
    return jsonify({"ok": True, "session": session, "team": team}), 201


@bp.route("/api/gameday/team/join", methods=["POST"])
@login_required
def api_join_team():
    _ensure_init()
    data = request.get_json(force=True) or {}
    team_code = data.get("team_code", "").strip().upper()
    player_name = data.get("player_name", "").strip()
    role_id = data.get("role_id", "")
    if not all([team_code, player_name, role_id]):
        return jsonify({"ok": False, "error": "team_code, player_name, role_id required"}), 400
    team = get_team_by_code(team_code)
    if not team:
        return jsonify({"ok": False, "error": "Invalid team code"}), 404
    session = get_session(team["session_id"])
    cfg = json.loads(session.get("config_json") or "{}")
    roles = cfg.get("scenario", {}).get("roles", [])
    member = _engine.join_team(team["team_id"], player_name, role_id, roles)
    return jsonify({"ok": True, "team": team, "member": member}), 201


# ---------------------------------------------------------------------------
# API — Injects
# ---------------------------------------------------------------------------

@bp.route("/api/gameday/session/<int:session_id>/injects", methods=["GET"])
@login_required
def api_get_injects(session_id: int):
    state_filter = request.args.get("state")
    if state_filter == "dispatched":
        injects = get_dispatched_injects(session_id)
    else:
        injects = get_all_injects(session_id)
    return jsonify({"injects": injects, "total": len(injects)})


@bp.route("/api/gameday/inject/<inject_id>/dispatch", methods=["POST"])
@require_facilitator
def api_dispatch_inject(inject_id: str):
    from tools.ttx.inject_dispatcher import dispatch_inject
    ok = dispatch_inject(inject_id)
    return jsonify({"ok": ok, "inject_id": inject_id})


@bp.route("/api/gameday/inject/<inject_id>/close", methods=["POST"])
@require_facilitator
def api_close_inject(inject_id: str):
    from tools.ttx.inject_dispatcher import close_inject
    close_inject(inject_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# API — Responses + scoring
# ---------------------------------------------------------------------------

@bp.route("/api/gameday/response", methods=["POST"])
@login_required
def api_submit_response():
    _ensure_init()
    data = request.get_json(force=True) or {}
    team_id = data.get("team_id")
    inject_id = data.get("inject_id")
    session_id = data.get("session_id")
    response_text = data.get("response_text", "")
    receipts = data.get("receipts", [])
    time_taken_s = data.get("time_taken_s")

    if not all([team_id, inject_id, session_id]):
        return jsonify({"ok": False, "error": "team_id, inject_id, session_id required"}), 400

    try:
        result = _engine.submit_response(
            team_id=int(team_id),
            inject_id=inject_id,
            session_id=int(session_id),
            response_text=response_text,
            receipts=receipts,
            time_taken_s=float(time_taken_s) if time_taken_s else None,
        )
        # For async sessions, try to unlock next inject
        session = get_session(int(session_id))
        if session and session.get("session_mode") == "async":
            unlocked = _engine.check_async_unlock(int(session_id), int(team_id))
            result["unlocked_inject"] = unlocked
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.route("/api/gameday/api-log", methods=["POST"])
@login_required
def api_log_receipt():
    """Teams call this endpoint to register a receipt when they use an ICDEV AI tool."""
    _ensure_init()
    data = request.get_json(force=True) or {}
    session_id = data.get("session_id")
    team_id = data.get("team_id")
    tool_slug = data.get("tool_slug", "unknown")
    endpoint = data.get("endpoint", "")
    result_payload = data.get("result_payload", "")

    if not all([session_id, team_id]):
        return jsonify({"ok": False, "error": "session_id and team_id required"}), 400

    call_id = str(uuid.uuid4())
    result_hash = hashlib.sha256(str(result_payload).encode()).hexdigest()[:16]

    try:
        _engine.log_api_receipt(
            session_id=int(session_id),
            team_id=int(team_id),
            tool_slug=tool_slug,
            endpoint=endpoint,
            call_id=call_id,
            result_hash=result_hash,
        )
        return jsonify({"ok": True, "call_id": call_id, "result_hash": result_hash})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# API — Response monitor (facilitator)
# ---------------------------------------------------------------------------

@bp.route("/api/gameday/session/<int:session_id>/responses", methods=["GET"])
@require_facilitator
def api_session_responses(session_id: int):
    """Return all team responses with scores for a session (facilitator use)."""
    if _session_for_tenant(session_id) is None:
        return jsonify({"responses": [], "total": 0}), 404
    conn = get_connection()
    rows = conn.execute(
        """SELECT r.response_id, r.team_id, r.inject_id, r.response_text,
                  r.submitted_at, r.time_taken_s,
                  s.receipt_pts, s.judge_pts, s.time_bonus_pts, s.total_pts,
                  s.judge_rationale_json,
                  t.team_name,
                  i.title AS inject_title, i.config_json AS inject_config_json
           FROM ttx_responses r
           LEFT JOIN ttx_scores s ON s.response_id = r.response_id
           LEFT JOIN ttx_teams t ON t.team_id = r.team_id
           LEFT JOIN ttx_injects i ON i.inject_id = r.inject_id
           WHERE r.inject_id IN (
               SELECT inject_id FROM ttx_injects WHERE session_id = %s
           )
           ORDER BY r.submitted_at DESC""",
        (session_id,),
    ).fetchall()
    out = []
    for row in rows:
        row = dict(row)
        cfg = json.loads(row.pop("inject_config_json") or "{}")
        row["is_aadc"] = cfg.get("inject_type") == "aadc_design_challenge"
        if row["is_aadc"]:
            row["aadc_score"] = row.get("judge_pts") or 0
        # Surface the fail-loud unscored marker so the monitor renders an LLM
        # outage distinctly instead of as a real judge score of 0.
        try:
            row["judge_unscored"] = bool(json.loads(row.get("judge_rationale_json") or "{}").get("unscored"))
        except Exception:
            row["judge_unscored"] = False
        # Truncate response text for display
        row["response_preview"] = (row.get("response_text") or "")[:120]
        out.append(row)
    return jsonify({"responses": out, "total": len(out)})


# ---------------------------------------------------------------------------
# API — Leaderboard
# ---------------------------------------------------------------------------

@bp.route("/api/gameday/session/<int:session_id>/leaderboard", methods=["GET"])
@login_required
def api_leaderboard(session_id: int):
    if _session_for_tenant(session_id) is None:
        return jsonify({"leaderboard": [], "total": 0}), 404
    lb = compute_leaderboard(session_id)
    ontology_class = request.args.get("ontology_class")
    exclude_class = request.args.get("exclude_class")
    if ontology_class or exclude_class:
        # Fetch inject ontology tags for filtering
        conn = get_connection()
        rows = conn.execute(
            "SELECT inject_id, ontology_tags_json FROM ttx_injects WHERE session_id = %s", (session_id,)
        ).fetchall()
        inject_tags = {r["inject_id"]: json.loads(r["ontology_tags_json"] or "{}") for r in rows}
        # Filter leaderboard entries that have responses matching the ontology class
        filtered = []
        for entry in lb:
            tid = entry.get("team_id")
            resp_rows = conn.execute(
                """SELECT r.inject_id FROM ttx_responses r
                   WHERE r.team_id = %s AND r.inject_id IN (
                       SELECT inject_id FROM ttx_injects WHERE session_id = %s
                   )""", (tid, session_id)
            ).fetchall()
            tags = [{"inject_id": r["inject_id"], "ontology_tags": inject_tags.get(r["inject_id"], {})} for r in resp_rows]
            matched = filter_by_ontology_class(tags, ontology_class=ontology_class, exclude_class=exclude_class, key="ontology_tags")
            if matched:
                entry["ontology_matches"] = len(matched)
                filtered.append(entry)
        lb = filtered
    return jsonify({"leaderboard": lb, "total": len(lb)})


@bp.route("/api/gameday/session/<int:session_id>/ribbons", methods=["GET"])
@login_required
def api_ribbons(session_id: int):
    ribbons = award_ribbons(session_id)
    return jsonify({"ribbons": ribbons})


@bp.route("/api/gameday/session/<int:session_id>/ontology", methods=["GET"])
@login_required
def api_session_ontology(session_id: int):
    """Return ontology tags for the session's scenario, roles, and injects."""
    _ensure_init()
    session = get_session(session_id)
    if not session:
        return jsonify({"ok": False, "error": "not found"}), 404
    cfg = json.loads(session.get("config_json") or "{}")
    scenario_slug = session.get("scenario_slug", "")
    scenario_ontology = resolve_scenario_ontology(scenario_slug)
    # Role ontology
    scenario = cfg.get("scenario", {})
    roles = []
    for role in scenario.get("roles", []):
        roles.append({
            "id": role.get("id"),
            "label": role.get("label"),
            "ontology": resolve_role_ontology(role.get("id", "")),
        })
    # Inject ontology from DB
    conn = get_connection()
    rows = conn.execute(
        "SELECT inject_id, slug, title, ontology_tags_json FROM ttx_injects WHERE session_id = %s",
        (session_id,),
    ).fetchall()
    injects = []
    for r in rows:
        tags = json.loads(r["ontology_tags_json"] or "{}")
        injects.append({
            "inject_id": r["inject_id"],
            "slug": r["slug"],
            "title": r["title"],
            "ontology": tags,
        })
    return jsonify({
        "ok": True,
        "session_id": session_id,
        "scenario_slug": scenario_slug,
        "scenario_ontology": scenario_ontology,
        "roles": roles,
        "injects": injects,
        "namespaces": ONTOLOGY_NAMESPACES,
    })


@bp.route("/api/gameday/ontology/concepts", methods=["GET"])
@login_required
def api_ontology_concepts():
    """Return all ontology concepts for UI reference."""
    from tools.ai_game_engine.ontology import get_all_ontology_concepts
    return jsonify({
        "concepts": get_all_ontology_concepts(),
        "namespaces": ONTOLOGY_NAMESPACES,
        "filters": SCOREBOARD_ONTOLOGY_FILTERS,
        "event_types": EVENT_ONTOLOGY_TYPES,
    })


# ---------------------------------------------------------------------------
# API — Scenario builder
# ---------------------------------------------------------------------------

@bp.route("/api/gameday/scenarios", methods=["GET"])
@login_required
def api_list_scenarios():
    conn = get_connection()
    rows = conn.execute(
        "SELECT scenario_id, slug, name, created_by, created_at FROM ttx_scenarios WHERE is_active = 1 ORDER BY created_at DESC"
    ).fetchall()
    file_slugs = list_scenario_slugs()
    return jsonify({"db_scenarios": [dict(r) for r in rows], "file_scenarios": file_slugs})


@bp.route("/api/gameday/scenarios", methods=["POST"])
@require_facilitator
def api_save_scenario():
    _ensure_init()
    data = request.get_json(force=True) or {}
    name = data.get("name", "").strip()
    slug = data.get("slug", "").strip().lower().replace(" ", "_")
    yaml_content = data.get("yaml_content", "")
    created_by = data.get("created_by", "builder")
    if not name or not slug:
        return jsonify({"ok": False, "error": "name and slug required"}), 400
    # Derive ontology tags from scenario content
    try:
        scenario_data = yaml.safe_load(yaml_content) or {}
    except Exception:
        scenario_data = {}
    from tools.ai_game_engine.scenario_registry import OntologyScenarioRegistry
    registry = OntologyScenarioRegistry()
    ontology_tags = registry._resolve_inject_ontology({"ai_tools_allowed": scenario_data.get("ai_tools_allowed", [])})
    # Also add scenario-level ontology
    scenario_ontology = resolve_scenario_ontology(slug)
    ontology_tags["scenario_classes"] = scenario_ontology.get("classes", [])
    json.dumps(ontology_tags)

    conn = get_connection()
    conn.execute(
        """INSERT INTO ttx_scenarios (slug, name, yaml_content, created_by, created_at)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT(slug) DO UPDATE SET
             name = excluded.name,
             yaml_content = excluded.yaml_content,
             created_by = excluded.created_by""",
        (slug, name, yaml_content, created_by, _now()),
    )
    conn.commit()
    return jsonify({"ok": True, "slug": slug, "ontology": ontology_tags}), 201


@bp.route("/api/gameday/inject-templates", methods=["GET"])
@login_required
def api_inject_templates():
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM ttx_inject_templates ORDER BY name"
    ).fetchall()
    return jsonify({"templates": [dict(r) for r in rows]})


@bp.route("/api/gameday/inject-templates", methods=["POST"])
@require_facilitator
def api_save_inject_template():
    _ensure_init()
    data = request.get_json(force=True) or {}
    name = data.get("name", "").strip()
    inject_type = data.get("inject_type", "custom")
    body_md = data.get("body_md", "")
    rubric_json = json.dumps(data.get("rubric", {}))
    ai_tools_json = json.dumps(data.get("ai_tools", []))
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400
    conn = get_connection()
    conn.execute(
        """INSERT INTO ttx_inject_templates
           (name, inject_type, body_md, rubric_json, ai_tools_json, created_at)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (name, inject_type, body_md, rubric_json, ai_tools_json, _now()),
    )
    conn.commit()
    return jsonify({"ok": True}), 201


# ---------------------------------------------------------------------------
# Simulation view
# ---------------------------------------------------------------------------

@bp.route("/gameday/simulation")
@login_required
def simulation_landing():
    """Session picker for the simulation. Auto-redirects if exactly one active session."""
    _ensure_init()
    from flask import redirect
    _tid = _gameday_tenant_id()
    active = list_sessions(state="active", tenant_id=_tid)
    pending = list_sessions(state="pending", tenant_id=_tid)
    ended = list_sessions(state="ended", tenant_id=_tid)[-10:]
    if len(active) == 1 and not pending:
        return redirect(f"/gameday/session/{active[0]['session_id']}/simulate")
    return render_template(
        "ai_gameday/simulation_picker.html",
        app_name=APP_NAME,
        active_sessions=active,
        pending_sessions=pending,
        past_sessions=ended,
    )


@bp.route("/gameday/session/<int:session_id>/simulate")
@login_required
def simulate_view(session_id: int):
    _ensure_init()
    session = get_session(session_id)
    if not session:
        return "Session not found", 404
    cfg = json.loads(session.get("config_json") or "{}")
    scenario = cfg.get("scenario", {})
    return render_template(
        "ai_gameday/simulate.html",
        app_name=APP_NAME,
        session=session,
        scenario_name=scenario.get("name", session.get("scenario_slug", "Scenario")),
    )


@bp.route("/api/gameday/session/<int:session_id>/simulate-state")
@login_required
def api_simulate_state(session_id: int):
    _ensure_init()
    session = _session_for_tenant(session_id)
    if not session:
        return jsonify({"ok": False, "error": "not found"}), 404
    injects = sorted(get_all_injects(session_id), key=lambda x: x.get("sequence_num") or 0)
    lb = compute_leaderboard(session_id)
    conn = get_connection()
    teams_raw = list_teams(session_id)
    teams_out = []
    for t in teams_raw:
        tid = t["team_id"]
        mc = conn.execute(
            "SELECT COUNT(*) FROM ttx_team_members WHERE team_id = %s", (tid,)
        ).fetchone()[0] or 0
        rc = conn.execute(
            "SELECT COUNT(*) FROM ttx_responses WHERE team_id = %s", (tid,)
        ).fetchone()[0] or 0
        rt = conn.execute(
            "SELECT COALESCE(SUM(receipt_count),0) FROM ttx_scores WHERE team_id = %s", (tid,)
        ).fetchone()[0] or 0
        tools_used = [r["tool_slug"] for r in conn.execute(
            "SELECT DISTINCT tool_slug FROM ttx_api_log WHERE team_id = %s", (tid,)
        ).fetchall()]
        teams_out.append({**dict(t), "member_count": mc, "response_count": rc,
                          "receipt_total": rt, "tools_used": tools_used})
    return jsonify({"ok": True, "session": dict(session), "injects": injects,
                    "leaderboard": lb, "teams": teams_out})


# ---------------------------------------------------------------------------
# API — AAR export
# ---------------------------------------------------------------------------

@bp.route("/api/gameday/session/<int:session_id>/aar", methods=["GET"])
@require_facilitator
def api_aar_markdown(session_id: int):
    md = generate_aar(session_id)
    return md, 200, {"Content-Type": "text/plain; charset=utf-8"}


# ---------------------------------------------------------------------------
# AI League — autonomous 4-team tournament (Red/Blue/Gold/Green)
# Pages + API. Backing engine lives in tools/gameday/* (db, leaderboard_engine,
# game_master, round_manager). Routes were missing from this blueprint, which
# caused health-prober HTTP HEAD 404s on /api/gameday/ai-league/* (issue #15).
# ---------------------------------------------------------------------------

@bp.route("/gameday/ai-league")
@login_required
def ai_league_hub():
    _ensure_init()
    from tools.gameday import db as _gdb
    from tools.gameday.game_master import get_or_create_active_tournament
    from tools.gameday.leaderboard_engine import get_round_scores, refresh_leaderboard
    tournament = get_or_create_active_tournament()
    tid = tournament["id"]
    return render_template(
        "gameday/ai_league.html",
        tournament=tournament,
        tournaments=_gdb.list_tournaments(),
        leaderboard=refresh_leaderboard(tid),
        round_scores=get_round_scores(tid),
    )


@bp.route("/gameday/ai-league/team/<team_key>")
@login_required
def ai_league_team(team_key: str):
    _ensure_init()
    from tools.gameday.game_master import get_or_create_active_tournament
    from tools.gameday.leaderboard_engine import get_team_detail
    tournament = get_or_create_active_tournament()
    return render_template(
        "gameday/team_detail.html",
        team_key=team_key,
        detail=get_team_detail(tournament["id"], team_key),
        tournament=tournament,
    )


@bp.route("/gameday/ai-league/ops")
@require_facilitator
def ai_league_ops():
    _ensure_init()
    from tools.gameday import db as _gdb
    from tools.gameday.game_master import get_or_create_active_tournament
    tournament = get_or_create_active_tournament()
    return render_template(
        "gameday/round_ops.html",
        tournament=tournament,
        events=_gdb.get_tournament_llmops(tournament["id"]),
    )


@bp.route("/api/gameday/ai-league/start", methods=["POST"])
@require_facilitator
def ai_league_start():
    _ensure_init()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip() or None
    try:
        round_count = int(data.get("round_count", 5))
    except (TypeError, ValueError):
        round_count = 5
    round_count = max(1, min(round_count, 10))
    from tools.gameday.game_master import GameMaster
    gm = GameMaster(tournament_name=name, round_count=round_count)
    # run_tournament() drives LLM rounds via the router and can take minutes —
    # run it off the request thread so the endpoint returns immediately.
    import threading

    def _run():
        # run_tournament() already records status='aborted' + the error on the
        # tournament row before re-raising, so a failed run is visible on the
        # AI League ops page (/gameday/ai-league/ops) rather than a silent no-op.
        try:
            gm.run_tournament()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("AI League tournament run failed: %s", exc)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "tournament_name": gm.tournament_name, "round_count": round_count})


@bp.route("/api/gameday/ai-league/leaderboard", methods=["GET"])
@login_required
def ai_league_leaderboard_api():
    _ensure_init()
    from tools.gameday.game_master import get_or_create_active_tournament
    from tools.gameday.leaderboard_engine import refresh_leaderboard
    tournament = get_or_create_active_tournament()
    return jsonify({
        "ok": True,
        "tournament_id": tournament["id"],
        "leaderboard": refresh_leaderboard(tournament["id"]),
    })


@bp.route("/api/gameday/ai-league/team/<team_key>", methods=["GET"])
@login_required
def ai_league_team_api(team_key: str):
    _ensure_init()
    from tools.gameday.game_master import get_or_create_active_tournament
    from tools.gameday.leaderboard_engine import get_team_detail
    tournament = get_or_create_active_tournament()
    return jsonify({"ok": True, "team": get_team_detail(tournament["id"], team_key)})


# ---------------------------------------------------------------------------
# Pre-session registration + snake-draft team formation (gdx-reg-01)
#
# The templates for this shipped long before the routes did — register.html and
# registrations.html sat in both trees rendered by nothing, and both tables
# existed only in the consolidated schema snapshot. The card notes the routes
# were never present in any commit: this is unfinished work, not a revert.
# ---------------------------------------------------------------------------

def _session_roles(session: dict) -> list:
    """Roles for a session, from its own scenario definition.

    Same source the /play console uses. Never a hardcoded list — a scenario with
    different roles registers against those.
    """
    cfg = json.loads(session.get("config_json") or "{}")
    return cfg.get("scenario", {}).get("roles", []) or []


@bp.route("/gameday/session/<int:session_id>/register")
def registration_page(session_id: int):
    """Player-facing registration form.

    Deliberately NOT @login_required: players join a session from a link and a
    join code before they have an account. Writes are still scoped to a real
    session id, and the facilitator roster view below is gated.
    """
    _ensure_init()
    session = get_session(session_id)
    if not session:
        return "Session not found", 404
    return render_template(
        "ai_gameday/register.html",
        app_name=APP_NAME,
        session=session,
        roles=_session_roles(session),
    )


@bp.route("/gameday/session/<int:session_id>/registrations")
@require_facilitator
def registrations_page(session_id: int):
    """Facilitator roster + draft board."""
    _ensure_init()
    session = get_session(session_id)
    if not session:
        return "Session not found", 404
    # register.html iterates a list; registrations.html indexes ROLES[role_id].
    roles = _session_roles(session)
    return render_template(
        "ai_gameday/registrations.html",
        app_name=APP_NAME,
        session=session,
        roles={str(r.get("id") or ""): r for r in roles},
        registrations=_registration.list_registrations(session_id),
        teams=_registration.get_formation_plan(session_id),
    )


@bp.route("/api/gameday/session/<int:session_id>/match-skill", methods=["POST"])
def api_match_skill(session_id: int):
    _ensure_init()
    session = get_session(session_id)
    if not session:
        return jsonify({"ok": False, "error": "Session not found"}), 404
    data = request.get_json(force=True) or {}
    match = _registration.match_skill_to_role(
        data.get("stated_skill", ""), _session_roles(session)
    )
    if not match:
        return jsonify({"ok": False, "error": "This scenario defines no roles"}), 400
    return jsonify({"ok": True, "match": match})


@bp.route("/api/gameday/session/<int:session_id>/register", methods=["POST"])
def api_register_player(session_id: int):
    _ensure_init()
    session = get_session(session_id)
    if not session:
        return jsonify({"ok": False, "error": "Session not found"}), 404
    try:
        reg = _registration.create_registration(
            session_id, request.get_json(force=True) or {}
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "registration": reg}), 201


@bp.route("/api/gameday/registration/<int:registration_id>", methods=["DELETE"])
@require_facilitator
def api_delete_registration(registration_id: int):
    _ensure_init()
    if not _registration.delete_registration(registration_id):
        return jsonify({"ok": False, "error": "Registration not found"}), 404
    return jsonify({"ok": True})


@bp.route("/api/gameday/session/<int:session_id>/form-teams", methods=["POST"])
@require_facilitator
def api_form_teams(session_id: int):
    _ensure_init()
    session = get_session(session_id)
    if not session:
        return jsonify({"ok": False, "error": "Session not found"}), 404
    data = request.get_json(force=True) or {}
    roster = _registration.list_registrations(session_id)
    if not roster:
        return jsonify({"ok": False, "error": "No registrations to draft"}), 400
    max_teams = data.get("max_teams") or session.get("max_teams") or 8
    teams = _registration.snake_draft(roster, int(max_teams))
    _registration.save_formation_plan(session_id, teams)
    return jsonify({
        "ok": True,
        "teams": teams,
        "num_teams": len(teams),
        "total_players": len(roster),
    })


@bp.route("/api/gameday/session/<int:session_id>/formation-plan/move", methods=["POST"])
@require_facilitator
def api_move_in_formation_plan(session_id: int):
    _ensure_init()
    data = request.get_json(force=True) or {}
    try:
        teams = _registration.move_player(
            session_id,
            int(data.get("registration_id") or 0),
            int(data.get("target_team_slot") or 0),
            str(data.get("target_team_name") or ""),
        )
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "teams": teams})


@bp.route("/api/gameday/session/<int:session_id>/confirm-teams", methods=["POST"])
@require_facilitator
def api_confirm_teams(session_id: int):
    _ensure_init()
    try:
        counts = _registration.confirm_formation(session_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, **counts})


@bp.route("/api/gameday/session/<int:session_id>/scenario-recommendation")
@require_facilitator
def api_scenario_recommendation(session_id: int):
    """Score every scenario on disk against the registered roster.

    'Technical' here is derived from how each scenario describes its own roles,
    not from a hardcoded list of job titles.
    """
    _ensure_init()
    session = get_session(session_id)
    if not session:
        return jsonify({"ok": False, "error": "Session not found"}), 404

    roster = _registration.list_registrations(session_id)
    options = []
    for slug in list_scenario_slugs():
        try:
            scenario = load_scenario(slug)
        except Exception:  # noqa: BLE001 — a broken pack must not hide the rest
            continue
        options.append({
            "slug": slug,
            "label": scenario.get("name") or slug.replace("_", " ").title(),
            "description": (scenario.get("description") or "")[:160],
            "icon": scenario.get("icon") or "🎯",
            "fit_score": _registration.scenario_fit(roster, scenario),
        })
    options.sort(key=lambda o: o["fit_score"], reverse=True)

    return jsonify({
        "ok": True,
        "tech_ratio": _registration.technical_ratio(roster),
        "reasoning": _registration.recommendation_reasoning(roster, options),
        "recommended_slug": options[0]["slug"] if options else "",
        "all_options": options,
    })


@bp.route("/api/gameday/session/<int:session_id>/scenario", methods=["PATCH"])
@require_facilitator
def api_set_session_scenario(session_id: int):
    """Swap a pending session's scenario after seeing who registered."""
    _ensure_init()
    session = get_session(session_id)
    if not session:
        return jsonify({"ok": False, "error": "Session not found"}), 404
    slug = str((request.get_json(force=True) or {}).get("scenario_slug") or "").strip()
    if not slug:
        return jsonify({"ok": False, "error": "scenario_slug required"}), 400
    if slug not in list_scenario_slugs():
        return jsonify({"ok": False, "error": f"Unknown scenario: {slug}"}), 404
    try:
        scenario = load_scenario(slug)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"Scenario failed to load: {exc}"}), 400

    cfg = json.loads(session.get("config_json") or "{}")
    cfg["scenario"] = scenario
    conn = get_connection()
    conn.execute(
        "UPDATE ttx_sessions SET scenario_slug = %s, config_json = %s, "
        "ontology_tags_json = %s WHERE session_id = %s",
        (slug, json.dumps(cfg), json.dumps(resolve_scenario_ontology(slug)), session_id),
    )
    conn.commit()
    return jsonify({"ok": True, "scenario_slug": slug})
