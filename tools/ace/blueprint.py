# CUI // SP-CTI
"""ACE — Autonomous Collaborative Engine (Co-Worker Engine) — Flask Blueprint.

Mounts the Co-Worker canvas:

  Pages (``ace_bp``, ``url_prefix='/coworker'``)
    GET  /                       active teams + launch form + role catalog count
    GET  /<instance_id>          single-instance detail (coworkers, messages, artifacts)
    GET  /roles                  role catalog

  JSON API (``ace_api_bp``, ``url_prefix='/api/ace'``)
    POST /api/ace/launch         ACEController.launch() → {instance_id}
    GET  /api/ace/instances      paginated instance list
    GET  /api/ace/<id>/status    instance state + per-coworker states
    GET  /api/ace/<id>/messages  message thread (incremental via ?after=)
    GET  /api/ace/<id>/artifacts artifact list
    POST /api/ace/<id>/abort     signal stop → instance state=cancelled
    POST /api/ace/iqe-query      plain-English → IQE → rows

The pages live under ``/coworker`` (per spec) while the data endpoints live under
``/api/ace`` because the shipped ``coworker/*.html`` templates fetch ``/api/ace/...``
absolute paths.  ``ace_bp`` carries the ``/coworker`` prefix directly; the API
blueprint is registered onto the app from ``ace_bp``'s first registration so
``app.py`` only needs to import ``ace_bp``.

All data routes use ``get_canvas_connection()`` — the ``ace_*`` tables have no
``classification``/``tenant_id`` columns, so the global RLS predicate must not be
attached (see CLAUDE.md canvas DB guardrail).
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any, Optional

from tools.logging.icdev_logger import get_logger

from flask import Blueprint, Response, jsonify, render_template, request

# Module-level import — must not raise at startup.  controller.py imports only the
# stdlib at module scope, so this is safe and gives the blueprint a stable handle.
from icdev.tools.ace.controller import ACEController
from icdev.tools.ace import constants as _const

logger = get_logger("icdev.ace.blueprint")

_DB_ENV = "ICDEV_ACE_DB_URL"
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 500

# Active = still doing work; used for the "active teams" view on the index page.
_ACTIVE_STATES = ("assembling", "pending", "active", "paused")

# IQE collections for this canvas — kept in sync with app.py's _CANVAS_MAP["ace"].
_IQE_COLLECTIONS = ["ace.coworkers", "ace.sessions", "ace.suggestions"]


# --------------------------------------------------------------------------- #
# Connection + row helpers (backend-agnostic, canvas RLS-free)
# --------------------------------------------------------------------------- #
def _db():
    """A canvas connection — NO RLS predicate (ace_* tables lack the columns)."""
    from icdev.tools.db.storage import get_canvas_connection

    return get_canvas_connection(_DB_ENV)


def _q(conn, sql: str) -> str:
    """Translate ``?`` placeholders to ``%s`` for the PostgreSQL backend."""
    declared = getattr(conn, "_backend", None)
    if declared:
        is_pg = str(declared).lower().startswith(("postgre", "pg"))
    else:
        is_pg = os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql").lower() in (
            "postgresql",
            "postgres",
            "pg",
        )
    return sql.replace("?", "%s") if is_pg else sql


def _rows(cur) -> list[dict]:
    """Materialize a cursor into a list of plain dicts (backend-agnostic)."""
    desc = getattr(cur, "description", None)
    if not desc:
        return []
    cols = [d[0] for d in desc]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _one(cur) -> Optional[dict]:
    """Materialize a single cursor row into a dict, or ``None``."""
    desc = getattr(cur, "description", None)
    row = cur.fetchone()
    if row is None or not desc:
        return None
    return dict(zip([d[0] for d in desc], row))


def _render_page(template: str, **context: Any):
    """Render a PAGE template; a failure is a **500 HTML page**, never a 200 JSON body.

    Every ``ace_bp`` page route used to wrap ``render_template`` in
    ``except Exception`` and return its context as ``jsonify(...)`` -- a 200.
    That fallback was written for "the template is absent" and it swallowed
    every other failure too: when ``instance.html`` gained
    ``{{ resume_token | tojson }}`` and the running route never passed
    ``resume_token``, Jinja's ``Undefined`` raised ``TypeError`` inside
    ``render_template``, the handler logged it at INFO and answered
    ``200 application/json`` for EVERY instance (qa-fail-5f7cf03a0b0a4351,
    measured 2026-08-22). No 500, no route_smoke signal, no CUI banner -- the
    only thing that noticed was a Playwright assertion on the banner text.

    A page route that cannot render its page has failed, and a failure is
    reported as one: logged with its traceback at ERROR, answered with the
    dashboard's ``500.html`` (which extends ``base.html`` and so carries the
    classification banner), or an inline HTML error page when even that
    template is out of reach (a bare test harness). The status is 500 and the
    body is ``text/html`` in both cases. Pinned by
    tests/test_ace_instance_page_render.py, behaviourally and over this file's
    AST.
    """
    try:
        return render_template(template, **context)
    except Exception:  # noqa: BLE001 -- reported as a 500, never swallowed into a 200
        logger.exception("ace page template %s failed to render", template)
    try:
        body = render_template("500.html", message=f"{template} failed to render")
    except Exception as exc:  # noqa: BLE001 -- no dashboard 500.html on this app
        logger.debug("500.html unavailable (%s); inline error page", exc)
        from markupsafe import escape

        body = (
            "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<title>500 - Internal Server Error</title></head><body>"
            "<h1>500 - Internal Server Error</h1>"
            f"<p>{escape(template)} failed to render. The error has been logged.</p>"
            "</body></html>"
        )
    return Response(body, status=500, mimetype="text/html")


def _decode_json(value: Any) -> dict:
    """Decode a JSON(B) text/obj column to a dict, tolerating NULL/garbage."""
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        out = json.loads(value)
        return out if isinstance(out, dict) else {}
    except (TypeError, ValueError):
        return {}


def _int_arg(name: str, default: int, *, lo: int, hi: int) -> int:
    try:
        n = int(request.args.get(name, default))
    except (TypeError, ValueError):
        n = default
    return max(lo, min(n, hi))


def _pending_confidence_hitl(conn, instance_id: str) -> list[dict]:
    """Unresolved HITL approval requests for every co-worker in an instance.

    Reads ace_audit_log: a ``hitl_pending`` row is outstanding until a matching
    ``hitl_resolved`` / ``hitl_rejected`` row (same coworker_id + detail) exists.
    Covers both the confidence gate (fired at run start for low-trust co-workers)
    and the required-step gate — each renders in the instance page's approval
    banner (coworker/hitl.html) whose Approve button POSTs to /api/ace/<id>/hitl.
    """
    try:
        pending = _rows(
            conn.execute(
                _q(
                    conn,
                    "SELECT id, coworker_id, detail, created_at FROM ace_audit_log "
                    "WHERE instance_id = ? AND action = 'hitl_pending' "
                    "ORDER BY created_at DESC",
                ),
                (instance_id,),
            )
        )
        if not pending:
            return []
        resolved = _rows(
            conn.execute(
                _q(
                    conn,
                    "SELECT coworker_id, detail FROM ace_audit_log "
                    "WHERE instance_id = ? "
                    "AND action IN ('hitl_resolved', 'hitl_rejected')",
                ),
                (instance_id,),
            )
        )
        done = {(r["coworker_id"], r["detail"]) for r in resolved}
        return [r for r in pending if (r["coworker_id"], r["detail"]) not in done]
    except Exception as exc:  # noqa: BLE001 — table may be empty/absent pre-migration
        logger.warning("ace pending HITL read failed for %s: %s", instance_id, exc)
        return []


# --------------------------------------------------------------------------- #
# Blueprints
# --------------------------------------------------------------------------- #
ace_bp = Blueprint("ace", __name__, url_prefix="/coworker")
ace_api_bp = Blueprint("ace_api", __name__, url_prefix="/api/ace")

# One-time schema guard shared by both blueprints.
_state = {"db_ready": False}


def _ensure_db() -> None:
    """Create the ace_* tables once, on the first served request."""
    if _state["db_ready"]:
        return
    try:
        from icdev.tools.ace.db.init_db import init as _init_ace_db

        _init_ace_db()
        _state["db_ready"] = True
    except Exception as exc:  # noqa: BLE001 — never let init failure 500 a read
        logger.warning("ace before_request init_db failed: %s", exc)


ace_bp.before_request(_ensure_db)
ace_api_bp.before_request(_ensure_db)


@ace_bp.record_once
def _mount_api(setup_state) -> None:
    """Register the ``/api/ace`` blueprint onto the app when ``ace_bp`` mounts.

    Keeps ``app.py`` importing a single symbol (``ace_bp``) while still exposing
    the absolute ``/api/ace/*`` endpoints the templates poll.
    """
    app = setup_state.app
    if "ace_api" in app.blueprints:
        return
    try:
        app.register_blueprint(ace_api_bp)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace_api blueprint registration failed: %s", exc)


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
@ace_bp.route("")
@ace_bp.route("/")
def index():
    """Active Co-Worker teams + role catalog summary + launch entry point."""
    instances: list[dict] = []
    conn = None
    try:
        conn = _db()
        rows = _rows(
            conn.execute(
                _q(
                    conn,
                    "SELECT id, name, role_id, state, trust_tier, created_at, updated_at "
                    "FROM ace_instances ORDER BY created_at DESC LIMIT ?",
                ),
                (_DEFAULT_LIMIT,),
            )
        )
        # Per-instance coworker counts (one grouped query, joined in Python).
        counts = {
            r["instance_id"]: r["n"]
            for r in _rows(
                conn.execute(
                    _q(
                        conn,
                        "SELECT instance_id, COUNT(*) AS n FROM ace_coworkers "
                        "GROUP BY instance_id",
                    ),
                    (),
                )
            )
        }
        for r in rows:
            r["coworker_count"] = counts.get(r["id"], 0)
        instances = rows
    except Exception as exc:  # noqa: BLE001 — empty state before first launch / migration
        logger.warning("ace index read failed: %s", exc)
    finally:
        if conn is not None:
            conn.close()

    active = [i for i in instances if i.get("state") in _ACTIVE_STATES]

    try:
        roles = ACEController.list_roles()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace index role load failed: %s", exc)
        roles = []

    cfg = {
        "max_team_size": _const.MAX_TEAM_SIZE,
        "trust_tier_default": _const.TRUST_TIER_DEFAULT,
        "hitl_threshold": _const.HITL_THRESHOLD,
        "stale_instance_hours": _const.STALE_INSTANCE_HOURS,
    }

    return _render_page(
        "coworker/index.html",
        instances=instances,
        active=active,
        roles=roles,
        cfg=cfg,
    )


@ace_bp.route("/roles")
def roles():
    """Role catalog — all loaded RoleTemplates."""
    try:
        from icdev.tools.ace.role_loader import RoleLoader

        role_list = RoleLoader().list_roles()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace roles load failed: %s", exc)
        role_list = []
    return _render_page("coworker/roles.html", roles=role_list)


@ace_bp.route("/profiles/new")
def profiles_new():
    """Profile creation page — AI Assist-powered."""
    return _render_page("coworker/profile_new.html")


@ace_bp.route("/trust")
def trust_leaderboard():
    """NOVA TRUST — Role trust leaderboard (score + band + last event)."""
    rows: list[dict] = []
    try:
        from icdev.tools.ace.trust_calibrator import get_trust_summary
        rows = get_trust_summary()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace trust_leaderboard: get_trust_summary failed: %s", exc)
    return _render_page("ace/trust.html", rows=rows)


@ace_bp.route("/sessions")
def sessions_list():
    """Agent session replay — list of all saved agent_loop_sessions."""
    return _render_page("ace/sessions.html")


@ace_bp.route("/sessions/<session_id>")
def session_detail(session_id: str):
    """Agent session replay — turn-by-turn detail view for one session."""
    return _render_page("ace/session_detail.html", session_id=session_id)


@ace_bp.route("/live/<instance_id>")
def live_monitor(instance_id: str):
    """Real-time agent loop monitoring via SSE stream."""
    return _render_page("ace/live.html", instance_id=instance_id)


@ace_bp.route("/evals")
def evals_page():
    """Agent eval suite — run history and pass/fail trends."""
    return _render_page("ace/evals.html")


@ace_bp.route("/evals/trends")
def evals_trends_page():
    """Eval score trending — time-bucketed aggregation chart."""
    return _render_page("ace/trends.html")


@ace_bp.route("/<instance_id>")
def instance_detail(instance_id: str):
    """Single instance: header, coworkers, message timeline, artifacts."""
    instance: Optional[dict] = None
    coworkers: list[dict] = []
    messages: list[dict] = []
    artifacts: list[dict] = []
    pending_hitl: list[dict] = []
    # instance.html renders ``{{ resume_token | tojson }}`` -- it MUST be passed,
    # as None when there is no session. fc12cfa09 added this query to the
    # icdev/ mirror only and the next tools/ -> icdev/ sync deleted it, which is
    # how the template came to ask for a variable the running route never sent.
    resume_token: Optional[str] = None
    conn = None
    try:
        conn = _db()
        instance = _one(
            conn.execute(
                _q(
                    conn,
                    "SELECT id, name, role_id, state, trust_tier, config_json, "
                    "result_json, created_at, updated_at, completed_at "
                    "FROM ace_instances WHERE id = ?",
                ),
                (instance_id,),
            )
        )
        if instance is not None:
            # Surface context fields the template expects from config_json.
            ctx = _decode_json(instance.get("config_json"))
            instance["problem_text"] = ctx.get("problem_text", "")
            instance["trigger_source"] = ctx.get("trigger_source", "")
            instance["trigger_ref"] = ctx.get("trigger_ref", "")

            coworkers = _rows(
                conn.execute(
                    _q(
                        conn,
                        "SELECT id, role_id, display_name, state, trust_tier, "
                        "assigned_step, last_active_at FROM ace_coworkers "
                        "WHERE instance_id = ? ORDER BY created_at ASC",
                    ),
                    (instance_id,),
                )
            )
            messages = _rows(
                conn.execute(
                    _q(
                        conn,
                        "SELECT id, coworker_id, message_type, role, content, "
                        "created_at FROM ace_messages WHERE instance_id = ? "
                        "ORDER BY created_at ASC, id ASC",
                    ),
                    (instance_id,),
                )
            )
            artifacts = _rows(
                conn.execute(
                    _q(
                        conn,
                        "SELECT id, coworker_id, artifact_type, title, classification, "
                        "content_json, content_md, created_at FROM ace_artifacts "
                        "WHERE instance_id = ? ORDER BY created_at DESC",
                    ),
                    (instance_id,),
                )
            )
            pending_hitl = _pending_confidence_hitl(conn, instance_id)
            # The most recent session on this instance, for the Resume button.
            session_row = _one(
                conn.execute(
                    _q(
                        conn,
                        "SELECT resume_token FROM ace_sessions "
                        "WHERE instance_id = ? ORDER BY created_at DESC LIMIT 1",
                    ),
                    (instance_id,),
                )
            )
            if session_row:
                resume_token = session_row.get("resume_token") or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace instance_detail read failed for %s: %s", instance_id, exc)
    finally:
        if conn is not None:
            conn.close()

    if instance is None:
        return jsonify({"error": f"instance not found: {instance_id}"}), 404

    coworker_display = {c["id"]: (c.get("display_name") or c.get("role_id")) for c in coworkers}

    return _render_page(
        "coworker/instance.html",
        instance=instance,
        instance_id=instance_id,
        coworkers=coworkers,
        messages=messages,
        artifacts=artifacts,
        coworker_display=coworker_display,
        pending_hitl=pending_hitl,
        resume_token=resume_token,
    )


@ace_bp.route("/<instance_id>/resume")
def instance_resume(instance_id: str):
    """Return conversation_history for a session identified by resume_token.

    GET /coworker/<id>/resume?token=<resume_token>
    Returns JSON: {session_id, conversation_history, turn_count}
    """
    token = (request.args.get("token") or "").strip()
    if not token:
        return jsonify({"error": "token required"}), 400

    conn = None
    try:
        conn = _db()
        session = _one(
            conn.execute(
                _q(
                    conn,
                    "SELECT session_id, conversation_history, turn_count "
                    "FROM ace_sessions WHERE resume_token = ? AND instance_id = ?",
                ),
                (token, instance_id),
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace instance_resume read failed for %s: %s", instance_id, exc)
        return jsonify({"error": "db error"}), 500
    finally:
        if conn is not None:
            conn.close()

    if session is None:
        return jsonify({"error": "session not found"}), 404

    history = session["conversation_history"]
    if isinstance(history, str):
        try:
            history = json.loads(history)
        except (TypeError, ValueError):
            history = []
    if not isinstance(history, list):
        history = []

    return jsonify(
        {
            "session_id": session["session_id"],
            "conversation_history": history,
            "turn_count": session.get("turn_count") or len(history),
        }
    )


# --------------------------------------------------------------------------- #
# JSON API
# --------------------------------------------------------------------------- #
@ace_api_bp.route("/launch", methods=["POST"])
def api_launch():
    """Launch an ACE run (non-blocking).  Returns the new ``instance_id``.

    Optional: include ``dic_collection_ids: ["col-id"]`` to prepend BM25 context
    from DIC into the problem_text before the team is assembled.

    Optional: ``role_ids`` bypasses the problem classifier and uses the requested
    team. ``context_query`` overrides the DIC search query (defaults to the
    problem_text) for better retrieval of relevant document snippets.
    """
    data = request.get_json(silent=True) or {}
    problem_text = (data.get("problem_text") or data.get("question") or data.get("text") or "").strip()
    if not problem_text:
        return jsonify({"error": "problem_text is required"}), 400

    role_ids = data.get("role_ids") or []
    if isinstance(role_ids, str):
        role_ids = [role_ids]
    role_ids = [r for r in role_ids if isinstance(r, str) and r.strip()]

    # DIC context injection — prepend BM25 results from attached collections.
    # Use an explicit context_query if the caller provided one; this avoids
    # searching with a report-generation prompt and instead searches with a
    # document-focused query, yielding more relevant snippets.
    dic_collection_ids = data.get("dic_collection_ids") or []
    if isinstance(dic_collection_ids, str):
        dic_collection_ids = [dic_collection_ids]
    context_query = (data.get("context_query") or "").strip()
    if dic_collection_ids:
        context_blocks: list[str] = []
        for col_id in dic_collection_ids[:3]:
            try:
                from icdev.tools.document_intelligence.search_engine import DICSearchEngine
                engine = DICSearchEngine(tenant_id="default")
                search_query = context_query or problem_text[:500]
                results = engine.search(search_query, collection_id=col_id, top_k=5)
                if results:
                    snippets = "\n".join(
                        f"- {r.to_dict().get('content', '')[:300]}"
                        if hasattr(r, "to_dict")
                        else f"- {str(r)[:300]}"
                        for r in results
                    )
                    context_blocks.append(f"[DIC:{col_id}]\n{snippets}")
            except Exception as exc:  # noqa: BLE001
                logger.warning("ace launch: DIC context fetch failed for %s: %s", col_id, exc)
        if context_blocks:
            dic_context = "\n\n".join(context_blocks)
            problem_text = f"Context from document collections:\n{dic_context}\n\n---\n\n{problem_text}"

    try:
        instance_id = ACEController.get_instance().launch(
            problem_text=problem_text,
            trigger_source=(data.get("trigger_source") or "dashboard"),
            trigger_ref=(data.get("trigger_ref") or ""),
            user_id=(data.get("user_id") or "dashboard"),
            project_id=(data.get("project_id") or ""),
            role_ids=role_ids or None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace launch failed: %s", exc)
        return jsonify({"error": str(exc)}), 500

    # Persist the DIC context link if collections were attached
    if dic_collection_ids and instance_id:
        try:
            import uuid as _uuid
            conn = _db()
            for col_id in dic_collection_ids[:3]:
                conn.execute(
                    _q(conn,
                       "INSERT INTO coworker_dic_contexts (id, instance_id, collection_id) "
                       "VALUES (?, ?, ?)"),
                    (_uuid.uuid4().hex, instance_id, col_id),
                )
            conn.commit()
            conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("ace launch: failed to persist DIC context links: %s", exc)

    return jsonify({"instance_id": instance_id}), 202


@ace_api_bp.route("/instances", methods=["GET"])
def api_instances():
    """Paginated instance list.  ``?limit=`` (≤500) ``&offset=`` ``&state=``."""
    limit = _int_arg("limit", _DEFAULT_LIMIT, lo=1, hi=_MAX_LIMIT)
    offset = _int_arg("offset", 0, lo=0, hi=10_000_000)
    state = request.args.get("state")

    items: list[dict] = []
    total = 0
    conn = None
    try:
        conn = _db()
        where = ""
        params: list[Any] = []
        if state:
            where = "WHERE state = ?"
            params.append(state)
        total_row = _one(
            conn.execute(
                _q(conn, f"SELECT COUNT(*) AS n FROM ace_instances {where}"),
                tuple(params),
            )
        )
        total = (total_row or {}).get("n", 0)
        items = _rows(
            conn.execute(
                _q(
                    conn,
                    "SELECT id, name, role_id, state, trust_tier, created_at, updated_at, "
                    f"completed_at FROM ace_instances {where} "
                    "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                ),
                tuple(params) + (limit, offset),
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace api_instances read failed: %s", exc)
    finally:
        if conn is not None:
            conn.close()

    return jsonify(
        {"instances": items, "count": len(items), "total": total, "limit": limit, "offset": offset}
    )


@ace_api_bp.route("/<instance_id>/status", methods=["GET"])
def api_status(instance_id: str):
    """Instance state + per-coworker states (delegates to ACEController.status)."""
    result = ACEController.get_instance().status(instance_id)
    if result.get("error") == "not_found":
        return jsonify({"error": f"instance not found: {instance_id}"}), 404
    return jsonify(result)


@ace_api_bp.route("/<instance_id>/messages", methods=["GET"])
def api_messages(instance_id: str):
    """Message thread for an instance.  ``?after=<id>`` returns only newer rows."""
    after = request.args.get("after")
    limit = _int_arg("limit", _MAX_LIMIT, lo=1, hi=_MAX_LIMIT)

    items: list[dict] = []
    conn = None
    try:
        conn = _db()
        after_ts: Optional[str] = None
        if after:
            ref = _one(
                conn.execute(
                    _q(conn, "SELECT created_at FROM ace_messages WHERE id = ?"),
                    (after,),
                )
            )
            after_ts = (ref or {}).get("created_at")

        if after_ts is not None:
            # Keyset on (created_at, id) so same-second ties are not skipped or duped.
            items = _rows(
                conn.execute(
                    _q(
                        conn,
                        "SELECT id, coworker_id, message_type, role, content, created_at "
                        "FROM ace_messages WHERE instance_id = ? "
                        "AND (created_at > ? OR (created_at = ? AND id > ?)) "
                        "ORDER BY created_at ASC, id ASC LIMIT ?",
                    ),
                    (instance_id, after_ts, after_ts, after, limit),
                )
            )
        else:
            items = _rows(
                conn.execute(
                    _q(
                        conn,
                        "SELECT id, coworker_id, message_type, role, content, created_at "
                        "FROM ace_messages WHERE instance_id = ? "
                        "ORDER BY created_at ASC, id ASC LIMIT ?",
                    ),
                    (instance_id, limit),
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace api_messages read failed for %s: %s", instance_id, exc)
    finally:
        if conn is not None:
            conn.close()

    return jsonify({"messages": items, "count": len(items)})


@ace_api_bp.route("/<instance_id>/artifacts", methods=["GET"])
def api_artifacts(instance_id: str):
    """Artifact list for an instance (newest first)."""
    items: list[dict] = []
    conn = None
    try:
        conn = _db()
        items = _rows(
            conn.execute(
                _q(
                    conn,
                    "SELECT id, coworker_id, artifact_type, title, classification, "
                    "content_json, content_md, created_at FROM ace_artifacts "
                    "WHERE instance_id = ? ORDER BY created_at DESC",
                ),
                (instance_id,),
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace api_artifacts read failed for %s: %s", instance_id, exc)
    finally:
        if conn is not None:
            conn.close()

    return jsonify({"artifacts": items, "count": len(items)})


@ace_api_bp.route("/<instance_id>/hitl/pending", methods=["GET"])
def api_hitl_pending(instance_id: str):
    """List pending mid-turn HITL checkpoint requests for an instance.

    Returns ``{items: [...], count: N}`` — each item has tool_name, tool_input_json,
    coworker_id, detail, created_at.  Use ``POST /<id>/hitl`` to approve/reject.
    """
    try:
        from icdev.tools.llm.agent_hitl import get_pending_hitl
        items = get_pending_hitl(instance_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace hitl/pending failed for %s: %s", instance_id, exc)
        items = []
    return jsonify({"items": items, "count": len(items)})


@ace_api_bp.route("/<instance_id>/abort", methods=["POST"])
def api_abort(instance_id: str):
    """Signal all coworkers to stop; marks the instance ``cancelled``.

    (The schema CHECK constraint allows ``cancelled``, not ``aborted`` — the
    controller's ``abort()`` sets the canonical terminal state.)
    """
    try:
        ACEController.get_instance().abort(instance_id)
    except LookupError:
        return jsonify({"error": "not_found", "instance_id": instance_id}), 404
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace abort failed for %s: %s", instance_id, exc)
        return jsonify({"error": str(exc)}), 500
    return jsonify({"instance_id": instance_id, "state": "cancelled", "aborted": True})


@ace_api_bp.route("/<instance_id>/hitl", methods=["POST"])
def api_hitl_resolve(instance_id: str):
    """Resolve or reject a pending HITL gate for a coworker.

    Body: {coworker_id, detail, approved}
    Approved=true inserts a hitl_resolved audit row so the paused coworker
    thread resumes.  Approved=false inserts hitl_rejected and the thread
    stops after the next poll cycle.
    """
    data = request.get_json(silent=True) or {}
    coworker_id = (data.get("coworker_id") or "").strip()
    detail = (data.get("detail") or "").strip()
    approved = bool(data.get("approved", True))
    if not coworker_id or not detail:
        return jsonify({"error": "coworker_id and detail are required"}), 400
    try:
        from icdev.tools.ace.coworker_thread import HITLGate

        # Both branches go through HITLGate so the append-only ace_audit_log
        # INSERT and the unified-inbox mirror stay in ONE place (agov-inbox-05).
        # The rows written are byte-for-byte what this route wrote before.
        if approved:
            HITLGate.resolve(coworker_id, detail, instance_id)
        else:
            HITLGate.reject(coworker_id, detail, instance_id)
        return jsonify({
            "instance_id": instance_id,
            "coworker_id": coworker_id,
            "resolved": True,
            "approved": approved,
        })
    except Exception as exc:
        logger.warning("ace hitl resolve failed for %s/%s: %s", instance_id, coworker_id, exc)
        return jsonify({"error": str(exc)}), 500


@ace_api_bp.route("/sessions", methods=["GET"])
def api_sessions_list():
    """Paginated list of agent_loop_sessions with summary stats.

    Query params: limit, offset, subtype (result_subtype filter),
    coworker_id, instance_id.
    """
    from icdev.tools.llm.agent_loop_session import list_sessions

    limit = _int_arg("limit", _DEFAULT_LIMIT, lo=1, hi=_MAX_LIMIT)
    offset = _int_arg("offset", 0, lo=0, hi=10_000_000)
    subtype = (request.args.get("subtype") or "").strip()
    coworker_id = (request.args.get("coworker_id") or "").strip()
    instance_id = (request.args.get("instance_id") or "").strip()

    result = list_sessions(
        limit=limit,
        offset=offset,
        result_subtype=subtype,
        coworker_id=coworker_id,
        instance_id=instance_id,
    )
    return jsonify({**result, "limit": limit, "offset": offset})


@ace_api_bp.route("/sessions/<session_id>", methods=["GET"])
def api_session_detail(session_id: str):
    """Full session detail: metadata + parsed turn-by-turn replay.

    Returns ``turns_parsed`` — a list of structured turn dicts, each with
    ``user_input`` (str), ``assistant_text`` (str), and ``tool_calls`` (list).
    """
    from icdev.tools.llm.agent_loop_session import get_session_metadata, load_session

    meta = get_session_metadata(session_id)
    if meta is None:
        return jsonify({"error": f"session not found: {session_id}"}), 404

    messages = load_session(session_id)
    turns_parsed = _parse_turns(messages)

    return jsonify({**meta, "turns_parsed": turns_parsed})


@ace_api_bp.route("/sessions/<session_id>/eval", methods=["GET"])
def api_session_eval_get(session_id: str):
    """Return stored eval metrics for a session (if any).

    GET /api/ace/sessions/<session_id>/eval
    Returns: EvalResult fields as JSON, or {error: not_found}.
    """
    try:
        from icdev.tools.ace.evaluator import get_eval
        er = get_eval(session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace eval get failed for %s: %s", session_id, exc)
        return jsonify({"error": str(exc)}), 500
    if er is None:
        return jsonify({"error": "no eval found", "session_id": session_id}), 404
    return jsonify({
        "session_id": er.session_id,
        "outcome": er.outcome,
        "done": er.done,
        "turns_used": er.turns_used,
        "efficiency_score": er.efficiency_score,
        "total_tool_calls": er.total_tool_calls,
        "error_tool_calls": er.error_tool_calls,
        "tool_error_rate": er.tool_error_rate,
        "unique_tools": er.unique_tools,
        "tool_precision": er.tool_precision,
        "total_cost_usd": er.total_cost_usd,
        "total_input_tokens": er.total_input_tokens,
        "total_output_tokens": er.total_output_tokens,
        "reasoning_coverage": er.reasoning_coverage,
        "avg_reasoning_chars": er.avg_reasoning_chars,
        "has_error_recovery_reasoning": er.has_error_recovery_reasoning,
        "plan_stated": er.plan_stated,
        "scope_violations": er.scope_violations,
        "trust_denials": er.trust_denials,
        "llm_grade": er.llm_grade,
        "reasoning_style": er.reasoning_style,
        "graded_at": er.graded_at,
        "grading_version": er.grading_version,
    })


@ace_api_bp.route("/sessions/<session_id>/eval", methods=["POST"])
def api_session_eval_post(session_id: str):
    """Score a session and persist the eval result.

    POST /api/ace/sessions/<session_id>/eval
    Body (optional): {max_iterations: int, reasoning_style: str, judge: bool,
                      user_prompt: str, final_content: str}
    Returns: the new EvalResult as JSON.
    """
    data = request.get_json(silent=True) or {}
    max_iter = int(data.get("max_iterations") or 12)
    reasoning_style = str(data.get("reasoning_style") or "")
    run_judge = bool(data.get("judge", False))
    user_prompt = str(data.get("user_prompt") or "")
    final_content = str(data.get("final_content") or "")

    try:
        from icdev.tools.ace.evaluator import score_session, save_eval, grade_output_quality
        er = score_session(session_id, max_iterations=max_iter, reasoning_style=reasoning_style)
        if er.outcome == "not_found":
            return jsonify({"error": f"session not found: {session_id}"}), 404
        if run_judge and user_prompt and final_content:
            grade = grade_output_quality(er, user_prompt=user_prompt, final_content=final_content)
            er.llm_grade = grade
        eval_id = save_eval(er)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace eval post failed for %s: %s", session_id, exc)
        return jsonify({"error": str(exc)}), 500

    return jsonify({
        "eval_id": eval_id,
        "session_id": er.session_id,
        "outcome": er.outcome,
        "done": er.done,
        "turns_used": er.turns_used,
        "efficiency_score": er.efficiency_score,
        "total_tool_calls": er.total_tool_calls,
        "error_tool_calls": er.error_tool_calls,
        "tool_error_rate": er.tool_error_rate,
        "unique_tools": er.unique_tools,
        "tool_precision": er.tool_precision,
        "total_cost_usd": er.total_cost_usd,
        "total_input_tokens": er.total_input_tokens,
        "total_output_tokens": er.total_output_tokens,
        "reasoning_coverage": er.reasoning_coverage,
        "avg_reasoning_chars": er.avg_reasoning_chars,
        "has_error_recovery_reasoning": er.has_error_recovery_reasoning,
        "plan_stated": er.plan_stated,
        "scope_violations": er.scope_violations,
        "trust_denials": er.trust_denials,
        "llm_grade": er.llm_grade,
        "reasoning_style": er.reasoning_style,
        "graded_at": er.graded_at,
        "grading_version": er.grading_version,
    }), 201


@ace_api_bp.route("/sessions/<session_id>/eval/suggestions", methods=["GET"])
def api_session_eval_suggestions(session_id: str):
    """Return rule-based improvement suggestions for a session.

    GET /api/ace/sessions/<session_id>/eval/suggestions
    First loads the stored eval (if any); if none exists, scores on-the-fly.
    Returns: {session_id, suggestions: [{issue, suggestion, field, severity}],
              eval_summary: {outcome, efficiency_score, reasoning_coverage, tool_error_rate}}
    """
    try:
        from icdev.tools.ace.evaluator import get_eval, score_session, suggest_improvements
        er = get_eval(session_id)
        if er is None:
            er = score_session(session_id)
        if er.outcome == "not_found":
            return jsonify({"error": f"session not found: {session_id}"}), 404
        suggestions = suggest_improvements(er)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace eval suggestions failed for %s: %s", session_id, exc)
        return jsonify({"error": str(exc)}), 500

    return jsonify({
        "session_id": session_id,
        "suggestions": suggestions,
        "eval_summary": {
            "outcome": er.outcome,
            "done": er.done,
            "efficiency_score": er.efficiency_score,
            "reasoning_coverage": er.reasoning_coverage,
            "tool_error_rate": er.tool_error_rate,
            "scope_violations": er.scope_violations,
            "plan_stated": er.plan_stated,
            "turns_used": er.turns_used,
            "max_iterations": er.max_iterations,
        },
    })


@ace_api_bp.route("/sessions/<session_id>/rerun", methods=["POST"])
def api_session_rerun(session_id: str):
    """Launch a new coworker session based on a prior session's config.

    Reads the original problem_text and role_ids from the source instance,
    optionally prepends improvement suggestions to the problem_text, then
    launches a new ACE run.

    POST body (all optional):
        apply_suggestions: bool  (default true)
        extra_prompt:      str   appended after the patched problem_text

    Returns: {new_instance_id, source_session_id, source_instance_id,
              patched_prompt_preview: str (first 300 chars)}
    """
    data = request.get_json(silent=True) or {}
    apply_sugg = bool(data.get("apply_suggestions", True))
    extra_prompt = (data.get("extra_prompt") or "").strip()

    # 1. Resolve source instance from the session
    from icdev.tools.llm.agent_loop_session import get_session_metadata
    meta = get_session_metadata(session_id)
    if meta is None:
        return jsonify({"error": f"session not found: {session_id}"}), 404

    source_instance_id = meta.get("instance_id") or ""

    # 2. Load original launch config from ace_instances
    problem_text = ""
    role_ids: list[str] = []
    if source_instance_id:
        try:
            conn = _db()
            row = conn.execute(
                _q(conn, "SELECT config_json FROM ace_instances WHERE id = ?"),
                (source_instance_id,),
            ).fetchone()
            conn.close()
            if row:
                cfg = _decode_json(row[0] if isinstance(row, (list, tuple)) else row.get("config_json", ""))
                problem_text = cfg.get("problem_text", "")
                role_ids = cfg.get("role_ids") or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("ace rerun: failed to load source config for %s: %s", source_instance_id, exc)

    # 3. Apply suggestions as a guidance prefix to problem_text
    if apply_sugg:
        try:
            from icdev.tools.ace.evaluator import get_eval, score_session, suggest_improvements, apply_suggestions_to_prompt
            er = get_eval(session_id) or score_session(session_id)
            suggestions = suggest_improvements(er)
            if suggestions:
                guidance = apply_suggestions_to_prompt("", suggestions)
                if guidance.strip():
                    problem_text = guidance + (problem_text or "(re-run with improvements)")
        except Exception as exc:  # noqa: BLE001
            logger.warning("ace rerun: suggest_improvements failed for %s: %s", session_id, exc)

    if extra_prompt:
        problem_text = (problem_text or "") + "\n\n" + extra_prompt

    if not problem_text.strip():
        return jsonify({"error": "could not reconstruct problem_text from source session"}), 422

    # 4. Launch new run
    try:
        new_instance_id = ACEController.get_instance().launch(
            problem_text=problem_text,
            trigger_source="rerun",
            trigger_ref=session_id,
            user_id="dashboard",
            role_ids=role_ids or None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace rerun: launch failed for source %s: %s", session_id, exc)
        return jsonify({"error": str(exc)}), 500

    return jsonify({
        "new_instance_id": new_instance_id,
        "source_session_id": session_id,
        "source_instance_id": source_instance_id,
        "patched_prompt_preview": problem_text[:300],
    }), 202


@ace_api_bp.route("/sessions/<session_id>/eval/compare", methods=["GET"])
def api_session_eval_compare(session_id: str):
    """Compare this session's eval against a baseline session.

    GET /api/ace/sessions/<id>/eval/compare?baseline=<other_session_id>

    Returns field-by-field deltas and overall_improved flag.
    baseline is session A; current session_id is session B.
    """
    baseline_id = (request.args.get("baseline") or "").strip()
    if not baseline_id:
        return jsonify({"error": "baseline query param required"}), 400
    try:
        from icdev.tools.ace.evaluator import compare_evals
        result = compare_evals(baseline_id, session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace eval compare failed %s vs %s: %s", baseline_id, session_id, exc)
        return jsonify({"error": str(exc)}), 500
    return jsonify(result)


@ace_api_bp.route("/evals/trends", methods=["GET"])
def api_evals_trends():
    """GET /api/ace/evals/trends?days=30&role=&bucket=week

    Returns aggregated eval score trends over time.
    """
    try:
        from icdev.tools.ace.evaluator import get_eval_trends
        days = int(request.args.get("days", 30))
        role = (request.args.get("role") or "").strip() or None
        bucket = request.args.get("bucket", "week")
        data = get_eval_trends(days=days, role=role, bucket=bucket)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace eval trends failed: %s", exc)
        return jsonify({"error": str(exc)}), 500
    return jsonify({"trends": data, "days": days, "bucket": bucket})


@ace_api_bp.route("/evals", methods=["GET"])
def api_evals_list():
    """List all stored eval results.

    GET /api/ace/evals?limit=50&offset=0&outcome=success
    Returns: {evals: [...], count, total}.
    """
    limit = _int_arg("limit", _DEFAULT_LIMIT, lo=1, hi=_MAX_LIMIT)
    offset = _int_arg("offset", 0, lo=0, hi=10_000_000)
    outcome = (request.args.get("outcome") or "").strip()

    items: list[dict] = []
    total = 0
    conn = None
    try:
        from icdev.tools.db.storage import get_canvas_connection
        conn = get_canvas_connection("ACE_DB_PATH")
        where = "WHERE outcome = ?" if outcome else ""
        params: tuple = (outcome,) if outcome else ()
        cnt = _one(conn.execute(_q(conn, f"SELECT COUNT(*) AS n FROM agent_evals {where}"), params))
        total = (cnt or {}).get("n", 0)
        items = _rows(conn.execute(
            _q(conn, f"SELECT session_id, outcome, done, turns_used, efficiency_score, "
               f"tool_error_rate, total_cost_usd, reasoning_coverage, plan_stated, "
               f"scope_violations, graded_at, grading_version "
               f"FROM agent_evals {where} ORDER BY graded_at DESC LIMIT ? OFFSET ?"),
            params + (limit, offset),
        ))
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace evals list failed: %s", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn is not None:
            conn.close()

    return jsonify({"evals": items, "count": len(items), "total": total, "limit": limit, "offset": offset})


def _parse_turns(messages: list[dict]) -> list[dict]:
    """Convert raw Anthropic-format messages into structured turn dicts.

    Each turn is: {user_input, assistant_text, tool_calls: [{name, input, result, is_error}]}.
    Tool calls from the assistant message are paired with their results from the
    subsequent user message (tool_result content blocks).
    """
    turns: list[dict] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        role = msg.get("role", "")

        if role == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                user_text = content
            elif isinstance(content, list):
                # May be tool_result blocks — defer to assistant turn handling
                user_text = None
                results_by_id: dict[str, dict] = {}
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tid = block.get("tool_use_id", "")
                        results_by_id[tid] = {
                            "result": _block_text(block.get("content", "")),
                            "is_error": bool(block.get("is_error", False)),
                        }
                if results_by_id:
                    # These belong to the previous turn's tool calls — patch them in.
                    if turns:
                        for tc in turns[-1].get("tool_calls", []):
                            tid = tc.get("_id", "")
                            if tid in results_by_id:
                                tc.update(results_by_id[tid])
                    i += 1
                    continue
            else:
                user_text = str(content) if content else None

            # Peek at the next assistant message.
            asst_msg = messages[i + 1] if i + 1 < len(messages) and messages[i + 1].get("role") == "assistant" else None
            asst_text = ""
            tool_calls: list[dict] = []
            if asst_msg is not None:
                asst_content = asst_msg.get("content", "")
                if isinstance(asst_content, str):
                    asst_text = asst_content
                elif isinstance(asst_content, list):
                    for block in asst_content:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type", "")
                        if btype == "text":
                            asst_text += block.get("text", "")
                        elif btype == "tool_use":
                            tool_calls.append({
                                "_id": block.get("id", ""),
                                "name": block.get("name", ""),
                                "input": block.get("input", {}),
                                "result": None,
                                "is_error": False,
                            })

            turns.append({
                "user_input": user_text,
                "assistant_text": asst_text.strip(),
                "tool_calls": tool_calls,
            })
            i += 2 if asst_msg is not None else 1
        else:
            i += 1

    # Strip internal _id from serialized output.
    for turn in turns:
        for tc in turn.get("tool_calls", []):
            tc.pop("_id", None)
    return turns


def _block_text(content) -> str:
    """Extract text from a tool_result content value (str or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content) if content else ""


@ace_api_bp.route("/monitor-summary", methods=["GET"])
def api_monitor_summary():
    """Home-page monitor tile: active instances, HITL queue, 24h agent loop stats."""
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    conn = None
    try:
        conn = _db()

        # Active ACE instances
        ph = ", ".join(["?"] * len(_ACTIVE_STATES))
        row = _one(
            conn.execute(
                _q(conn, f"SELECT COUNT(*) AS n FROM ace_instances WHERE state IN ({ph})"),
                tuple(_ACTIVE_STATES),
            )
        )
        active_instances = int((row or {}).get("n", 0) or 0)

        # HITL items awaiting human review (table may not exist on older installs)
        hitl_pending = 0
        try:
            row = _one(
                conn.execute(
                    _q(conn, "SELECT COUNT(*) AS n FROM agent_hitl_pending WHERE status = ?"),
                    ("pending",),
                )
            )
            hitl_pending = int((row or {}).get("n", 0) or 0)
        except Exception:  # noqa: BLE001
            pass

        # Agent loop sessions in the last 24 h (table may not exist on older installs)
        sessions_24h = 0
        cost_24h_usd = 0.0
        success_rate_24h = None
        last_session_at = None
        try:
            row = _one(
                conn.execute(
                    _q(
                        conn,
                        "SELECT COUNT(*) AS n, SUM(total_cost_usd) AS cost,"
                        " SUM(CASE WHEN result_subtype = ? THEN 1 ELSE 0 END) AS ok"
                        " FROM agent_loop_sessions WHERE created_at >= ?",
                    ),
                    ("success", cutoff),
                )
            )
            if row:
                sessions_24h = int(row.get("n", 0) or 0)
                cost_24h_usd = float(row.get("cost", 0.0) or 0.0)
                ok_count = int(row.get("ok", 0) or 0)
                if sessions_24h > 0:
                    success_rate_24h = round(ok_count / sessions_24h, 3)

            ts_row = _one(conn.execute("SELECT MAX(created_at) AS ts FROM agent_loop_sessions"))
            if ts_row:
                last_session_at = ts_row.get("ts")
        except Exception:  # noqa: BLE001
            pass

        return jsonify(
            {
                "active_instances": active_instances,
                "hitl_pending": hitl_pending,
                "sessions_24h": sessions_24h,
                "cost_24h_usd": round(cost_24h_usd, 4),
                "success_rate_24h": success_rate_24h,
                "last_session_at": last_session_at,
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace monitor-summary failed: %s", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn is not None:
            conn.close()


@ace_api_bp.route("/presets", methods=["GET"])
def api_presets_list():
    """List Quick Launch presets (args/ace/launch_presets.yaml) for the coworker UI."""
    try:
        import yaml
        from icdev._paths import get_data_path

        presets_path = get_data_path("args") / "ace" / "launch_presets.yaml"
        data = yaml.safe_load(presets_path.read_text(encoding="utf-8")) or {}
        presets = data.get("presets", [])
        return jsonify({"presets": presets, "count": len(presets)})
    except Exception as exc:
        logger.warning("ace presets list failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@ace_api_bp.route("/profiles", methods=["GET"])
def api_profiles_list():
    """List all coworker profiles (both built-in and generated)."""
    try:
        from icdev.tools.ace.profile_generator import list_profiles
        return jsonify({"profiles": list_profiles()})
    except Exception as exc:
        logger.warning("ace profiles list failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@ace_api_bp.route("/profiles/suggest-name", methods=["POST"])
def api_profiles_suggest_name():
    """AI Assist: given an informal description, suggest official DoD/IC role names.

    Body: {description}
    Returns [{title, basis, rationale}] ranked best-first.
    """
    data = request.get_json(silent=True) or {}
    description = (data.get("description") or "").strip()
    if not description:
        return jsonify({"error": "description is required"}), 400
    try:
        from icdev.tools.ace.profile_generator import suggest_profile_names
        suggestions = suggest_profile_names(description)
        return jsonify({"ok": True, "suggestions": suggestions})
    except Exception as exc:
        logger.warning("ace profiles suggest-name failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@ace_api_bp.route("/profiles/preview", methods=["POST"])
def api_profiles_preview():
    """AI-assist: enrich a sparse name+description into a full profile spec.

    Body: {name, description?}
    Returns the enriched spec without writing any files — lets the user
    review and edit before committing.
    """
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    description = (data.get("description") or "").strip()
    try:
        from icdev.tools.ace.profile_generator import preview_profile
        spec = preview_profile(name, description)
        return jsonify({"ok": True, "spec": spec})
    except Exception as exc:
        logger.warning("ace profiles preview failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@ace_api_bp.route("/profiles/generate", methods=["POST"])
def api_profiles_generate():
    """Create and persist a new coworker profile.

    Body: {name, description?, spec?}
    If spec is provided (from a prior /preview call) it is used as-is,
    skipping a second LLM call.  Returns {role_id, files_written}.
    """
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    description = (data.get("description") or "").strip()
    spec_override = data.get("spec") or None
    try:
        from icdev.tools.ace.profile_generator import generate_profile
        result = generate_profile(name, description, spec_override=spec_override)
        return jsonify({"ok": True, **result}), 201
    except Exception as exc:
        logger.warning("ace profiles generate failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@ace_api_bp.route("/profiles/<role_id>", methods=["DELETE"])
def api_profiles_delete(role_id: str):
    """Delete a generated profile (built-in profiles are protected)."""
    try:
        from icdev.tools.ace.profile_generator import delete_profile
        result = delete_profile(role_id)
        if "error" in result:
            return jsonify(result), 400
        return jsonify({"ok": True, **result})
    except Exception as exc:
        logger.warning("ace profiles delete failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@ace_api_bp.route("/coworkers/<coworker_id>/stats", methods=["GET"])
def api_coworker_stats(coworker_id: str):
    """Per-coworker usage stats: message count, tool calls, sessions.

    GET /api/ace/coworkers/<coworker_id>/stats
    Returns: {coworker_id, message_count, tool_call_count, session_count,
              last_active, instances: [instance_id, ...]}
    """
    conn = None
    try:
        conn = _db()
        msg_row = _one(
            conn.execute(
                _q(conn, "SELECT COUNT(*) AS n FROM ace_messages WHERE coworker_id = ?"),
                (coworker_id,),
            )
        )
        tool_row = _one(
            conn.execute(
                _q(
                    conn,
                    "SELECT COUNT(*) AS n FROM ace_audit_log "
                    "WHERE coworker_id = ? AND action = 'agent_turn'",
                ),
                (coworker_id,),
            )
        )
        last_row = _one(
            conn.execute(
                _q(
                    conn,
                    "SELECT MAX(created_at) AS last_active FROM ace_messages "
                    "WHERE coworker_id = ?",
                ),
                (coworker_id,),
            )
        )
        inst_rows = _rows(
            conn.execute(
                _q(
                    conn,
                    "SELECT DISTINCT instance_id FROM ace_messages WHERE coworker_id = ?",
                ),
                (coworker_id,),
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace coworker_stats failed for %s: %s", coworker_id, exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn is not None:
            conn.close()

    session_count = 0
    try:
        from icdev.tools.llm.agent_loop_session import list_sessions
        sr = list_sessions(coworker_id=coworker_id, limit=1000)
        session_count = sr.get("total", 0)
    except Exception:
        pass

    return jsonify({
        "coworker_id": coworker_id,
        "message_count": (msg_row or {}).get("n", 0),
        "tool_call_count": (tool_row or {}).get("n", 0),
        "session_count": session_count,
        "last_active": (last_row or {}).get("last_active"),
        "instances": [r["instance_id"] for r in inst_rows],
    })


@ace_api_bp.route("/<instance_id>/stream", methods=["GET"])
def api_stream(instance_id: str):
    """Server-Sent Events stream for live agent events on *instance_id*.

    GET /api/ace/<instance_id>/stream
    Each SSE message is: data: {json}\n\n
    Events: agent_turn, loop_done
    The connection closes after 60 s of inactivity (no events).
    """
    from icdev.tools.ace import event_bus as _eb
    import json as _json

    timeout = float(request.args.get("timeout", 25))
    # max_pings=0 means unlimited; use a small positive value in tests to bound
    # the generator when no events are expected (e.g. ?max_pings=1).
    max_pings = int(request.args.get("max_pings", 0))

    def _generate():
        q = _eb.subscribe(instance_id)
        ping_count = 0
        try:
            while True:
                event = _eb.drain(instance_id, q, timeout=timeout)
                if event is None:
                    yield ": ping\n\n"
                    ping_count += 1
                    if max_pings and ping_count >= max_pings:
                        break
                    continue
                yield f"data: {_json.dumps(event)}\n\n"
                if event.get("type") == "loop_done":
                    break
        finally:
            _eb.unsubscribe(instance_id, q)

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@ace_api_bp.route("/iqe-query", methods=["POST"])
def api_iqe_query():
    """Plain-English → IQE → rows over the ace_* collections.

    Body: ``{question}``.  Mirrors the canvas-aware dispatcher in ``app.py``
    (``nl_to_iqe`` → ``parse`` → ``execute_query``) so the shared
    ``includes/iqe_query_widget.html`` widget renders the generated IQE plus rows.
    """
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    iqe_str = ""
    try:
        from icdev.tools.iqe.adapters import ace as _  # noqa: F401  registers collections
        from icdev.tools.iqe.executor import execute_query
        from icdev.tools.iqe.nl_to_iqe import nl_to_iqe
        from icdev.tools.iqe.parser import IQESyntaxError, parse as iqe_parse

        translated = nl_to_iqe(question, _IQE_COLLECTIONS)
        iqe_str = translated.get("iqe", "")
        explanation = translated.get("explanation", "")
        try:
            ast = iqe_parse(iqe_str)
            rows = execute_query(ast, conn=None)
        except IQESyntaxError:
            rows = []
        return jsonify(
            {
                "ok": True,
                "canvas": "ace",
                "iqe": iqe_str,
                "explanation": explanation,
                "rows": rows,
                "count": len(rows),
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace iqe-query error: %s", exc)
        return jsonify({"error": str(exc), "canvas": "ace", "iqe": iqe_str}), 500


# ---------------------------------------------------------------------------
# Event Bus API — auto-dispatch feed
# ---------------------------------------------------------------------------


@ace_api_bp.route("/events", methods=["GET"])
def api_ace_events():
    """Recent auto-dispatch event results for the event feed UI."""
    try:
        from icdev.tools.ace.event_bus import get_recent_results, pending_count
        limit = min(int(request.args.get("limit", 50)), 200)
        return jsonify({
            "ok": True,
            "pending": pending_count(),
            "results": get_recent_results(limit=limit),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@ace_api_bp.route("/events/emit", methods=["POST"])
def api_ace_events_emit():
    """Manually emit an ACE event (for testing / canvas integration)."""
    try:
        from icdev.tools.ace.event_bus import emit, DISPATCH_TOPICS
        body = request.get_json(silent=True) or {}
        topic = body.get("topic", "")
        if topic not in DISPATCH_TOPICS:
            return jsonify({"error": f"Unknown topic. Valid: {sorted(DISPATCH_TOPICS)}"}), 400
        event_id = emit(
            topic=topic,
            payload=body.get("payload", {}),
            source_canvas=body.get("source_canvas", "manual"),
            source_id=body.get("source_id", ""),
        )
        return jsonify({"ok": True, "event_id": event_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@ace_api_bp.route("/events/topics", methods=["GET"])
def api_ace_event_topics():
    """Return all dispatch topics and which roles listen to each.

    gap C-2: icdev.tools.ace.event_bus has no DISPATCH_TOPICS registry (the
    module only implements the queue itself, not a canonical topic list) —
    this endpoint has never actually been wired. 404 rather than 500 until
    that registry exists.
    """
    try:
        from icdev.tools.ace.event_bus import DISPATCH_TOPICS
    except ImportError:
        return jsonify({"error": "not_implemented", "detail": "no dispatch topic registry"}), 404
    try:
        from icdev.tools.ace.role_loader import RoleLoader
        roles = RoleLoader().list_roles()
        topic_map: dict[str, list[str]] = {t: [] for t in sorted(DISPATCH_TOPICS)}
        for role in roles:
            for topic in (role.communication.get("listen_topics") or []):
                if topic in topic_map:
                    topic_map[topic].append(role.role_id)
        return jsonify({"ok": True, "topics": topic_map})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@ace_api_bp.route("/<instance_id>/nova-state", methods=["GET"])
def api_nova_state(instance_id: str):
    """NOVA state snapshot for an instance: per-coworker trust, memory, improvements."""
    from icdev.tools.ace.trust_calibrator import INITIAL_TRUST

    cws: list[dict] = []
    conn_ace = None
    try:
        conn_ace = _db()
        cws = _rows(
            conn_ace.execute(
                _q(
                    conn_ace,
                    "SELECT id, role_id, display_name, state, trust_tier, "
                    "assigned_step, last_active_at, created_at "
                    "FROM ace_coworkers WHERE instance_id = ? ORDER BY created_at",
                ),
                (instance_id,),
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace nova-state: canvas read failed: %s", exc)
    finally:
        if conn_ace is not None:
            conn_ace.close()

    trust: dict[str, float] = {}
    fact_counts: dict[str, int] = {}
    improvements: list[dict] = []
    conn_nova = None
    try:
        from icdev.tools.db.storage import get_connection

        conn_nova = get_connection()
        for r in _rows(
            conn_nova.execute(
                _q(
                    conn_nova,
                    "SELECT role_id, new_score FROM ace_trust_ledger "
                    "ORDER BY recorded_at ASC",
                )
            )
        ):
            trust[r["role_id"]] = float(r["new_score"])
        for r in _rows(
            conn_nova.execute(
                _q(
                    conn_nova,
                    "SELECT role_id, COUNT(*) AS n FROM ace_coworker_memory GROUP BY role_id",
                )
            )
        ):
            fact_counts[r["role_id"]] = int(r["n"])
        # exa-refine-04: surface the lesson_learned evidence that motivated each
        # applied refinement, so the coworker card shows WHY, not just WHAT.
        from tools.workflow.refinement_evidence import evidence_summary

        improvements = [
            {
                "task_type": r["task_type"],
                "improvement_text": r["improvement_text"],
                "evidence_summary": evidence_summary(r.get("evidence_traces")),
            }
            for r in _rows(
                conn_nova.execute(
                    _q(
                        conn_nova,
                        "SELECT task_type, improvement_text, evidence_traces "
                        "FROM agent_improvement_artifacts "
                        "WHERE status = ? ORDER BY applied_at DESC LIMIT ?",
                    ),
                    ("applied", 20),
                )
            )
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace nova-state: nova read failed: %s", exc)
    finally:
        if conn_nova is not None:
            conn_nova.close()

    out: list[dict] = []
    for cw in cws:
        role = cw.get("role_id", "")
        out.append(
            {
                "coworker_id": cw.get("id"),
                "role_id": role,
                "display_name": cw.get("display_name"),
                "state": cw.get("state"),
                "trust_tier": cw.get("trust_tier"),
                "trust_score": float(trust.get(role, INITIAL_TRUST)),
                "fact_count": int(fact_counts.get(role, 0)),
                "recent_improvements": improvements,
            }
        )
    return jsonify({"coworkers": out})


# ---------------------------------------------------------------------------
# Chat API — multi-turn session helper
# ---------------------------------------------------------------------------

_CHAT_INSTANCE_ID = "ace-chat"


def _chat_ensure_instance(conn) -> None:
    """Ensure the synthetic chat instance exists for session FK integrity."""
    conn.execute(
        _q(
            conn,
            "INSERT OR IGNORE INTO ace_instances (id, name, role_id, state) VALUES (?, ?, ?, ?)",
        ),
        (_CHAT_INSTANCE_ID, "ACE Chat", "chat", "active"),
    )


@ace_api_bp.route("/chat", methods=["POST"])
def api_ace_chat():
    """POST /api/ace/chat — multi-turn chat with persisted session history."""
    body = request.get_json(silent=True) or {}
    session_id = body.get("session_id")
    user_message = body.get("user_message")
    if not session_id or not isinstance(session_id, str):
        return jsonify({"error": "session_id is required"}), 400
    if not user_message or not isinstance(user_message, str):
        return jsonify({"error": "user_message is required"}), 400

    conn = None
    try:
        conn = _db()
        _chat_ensure_instance(conn)

        row = _one(
            conn.execute(
                _q(
                    conn,
                    # history_json does not exist on ace_sessions; selecting it
                    # raised UndefinedColumn on PG exactly as the INSERT did, so
                    # no session ever resumed. conversation_history is the column
                    # that actually carries the turns (swp-scan-01).
                    "SELECT conversation_history, turn_count, resume_token "
                    "FROM ace_sessions WHERE session_id = ? AND instance_id = ?",
                ),
                (session_id, _CHAT_INSTANCE_ID),
            )
        )

        if row:
            raw_history = row.get("conversation_history") or "[]"
            try:
                history = json.loads(raw_history) if raw_history else []
            except (TypeError, ValueError):
                history = []
            if not isinstance(history, list):
                history = []
            turn_count = int(row.get("turn_count", 0))
            resume_token = row.get("resume_token") or f"rt-{uuid.uuid4().hex[:12]}"
        else:
            history = []
            turn_count = 0
            resume_token = f"rt-{uuid.uuid4().hex[:12]}"

        history.append({"role": "user", "content": user_message})

        assistant_message = ""
        try:
            from icdev.tools.llm.router import LLMRouter
            from icdev.tools.llm.provider import LLMRequest

            router = LLMRouter()
            req = LLMRequest(
                function="llm_generation",
                messages=history,
                model="claude-sonnet-4-6",
                max_tokens=512,
            )
            resp = router.invoke("llm_generation", req)
            assistant_message = getattr(resp, "content", "") or ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("ace chat LLM call failed: %s", exc)
            assistant_message = ""

        if not assistant_message:
            assistant_message = "ACE response to: " + user_message

        history.append({"role": "assistant", "content": assistant_message})
        turn_count += 1
        history_json = json.dumps(history)

        conn.execute(
            _q(
                conn,
                # history_json does not exist on ace_sessions and never has; the
                # same value was written twice, so the whole statement failed and
                # no chat turn was ever persisted (swp-scan-01).
                "INSERT INTO ace_sessions (session_id, instance_id, conversation_history, resume_token, turn_count) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "  conversation_history = excluded.conversation_history, "
                "  turn_count = excluded.turn_count",
            ),
            (session_id, _CHAT_INSTANCE_ID, history_json, resume_token, turn_count),
        )
        conn.commit()

        return jsonify({
            "session_id": session_id,
            "turn_count": turn_count,
            "assistant_message": assistant_message,
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace chat endpoint error: %s", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn is not None:
            conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Chat-initiated team proposals (suggest-then-confirm)
# ─────────────────────────────────────────────────────────────────────────────
#
# The implicit chat trigger writes a proposal instead of launching. These routes
# are what the action_card in the chat panel drives. Approval is the ONLY path
# that spawns agents from an implicit trigger, so the premium gate belongs on
# confirm -- and SME generation sits behind it, meaning a locked tenant can
# never burn LLM spend on a team it is not entitled to run.


@ace_api_bp.route("/suggestions/<suggestion_id>", methods=["GET"])
def api_suggestion_get(suggestion_id: str):
    """GET /api/ace/suggestions/<id> — current state of a proposal."""
    from icdev.tools.ace import chat_trigger

    proposal = chat_trigger.get_proposal(suggestion_id)
    if not proposal:
        return jsonify({"error": "unknown suggestion"}), 404

    try:
        roles = json.loads(proposal.get("proposed_roles_json") or "[]")
    except Exception:  # noqa: BLE001
        roles = []

    return jsonify({
        "suggestion_id": proposal["id"],
        "state": proposal["state"],
        "roles": roles,
        "instance_id": proposal.get("instance_id") or "",
        "expired": chat_trigger.is_expired(proposal),
        "created_at": proposal.get("created_at") or "",
    })


@ace_api_bp.route("/suggestions/<suggestion_id>/confirm", methods=["POST"])
def api_suggestion_confirm(suggestion_id: str):
    """POST /api/ace/suggestions/<id>/confirm — approve and launch.

    Tier is checked BEFORE anything is generated or launched.
    """
    from icdev.tools.ace import chat_trigger

    allowed, required_tier = _sme_tier_allows()
    if not allowed:
        return jsonify({
            "error": "upgrade_required",
            "required_tier": required_tier,
            "message": (
                "Chat-initiated co-worker teams require the "
                f"{required_tier} plan."
            ),
        }), 402

    result = chat_trigger.confirm_proposal(suggestion_id, user_id=_suggestion_actor())
    if not result["ok"]:
        status = 404 if result["error"] == "unknown suggestion" else 409
        return jsonify({"error": result["error"]}), status

    instance_id = result["instance_id"]
    return jsonify({
        "ok": True,
        "instance_id": instance_id,
        "deep_link": f"/coworker/{instance_id}",
    })


@ace_api_bp.route("/suggestions/<suggestion_id>/decline", methods=["POST"])
def api_suggestion_decline(suggestion_id: str):
    """POST /api/ace/suggestions/<id>/decline — dismiss a proposal."""
    from icdev.tools.ace import chat_trigger

    data = request.get_json(silent=True) or {}
    ok = chat_trigger.decline_proposal(suggestion_id, str(data.get("reason") or ""))
    if not ok:
        return jsonify({"error": "not declinable"}), 409
    return jsonify({"ok": True})


def _suggestion_actor() -> str:
    """Best-effort identity of whoever clicked approve, for the audit trail."""
    try:
        from flask import g

        return str(getattr(g, "current_user", "") or "system")
    except Exception:  # noqa: BLE001
        return "system"


def _sme_tier_allows() -> tuple[bool, str]:
    """Return (allowed, required_tier) for chat-initiated teams.

    Uses icdev/tools/billing/tier.py -- the module the dashboard actually
    injects as active_tier. tools/license/tier.py is a different, largely
    unreferenced module with a four-tier vocabulary including "starter", which
    component_registry silently rewrites to "community"; using it here would
    quietly disable the gate.

    Fails OPEN when the tier module is unavailable: this is an entitlement
    check, not a security boundary, and the real safety controls (trust tier,
    HITL gate, capability bundles) are enforced elsewhere regardless.
    """
    required = "professional"
    try:
        from icdev.tools.billing.tier import get_active_tier, tier_satisfies

        return bool(tier_satisfies(get_active_tier(), required)), required
    except Exception as exc:  # noqa: BLE001
        logger.debug("ace: tier check unavailable (%s); allowing", exc)
        return True, required
