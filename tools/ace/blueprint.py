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
from tools.logging.icdev_logger import get_logger
import os
from typing import Any, Optional

from flask import Blueprint, jsonify, render_template, request

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

    try:
        return render_template(
            "coworker/index.html",
            instances=instances,
            active=active,
            roles=roles,
            cfg=cfg,
        )
    except Exception as exc:  # noqa: BLE001 — JSON fallback if the template is absent
        logger.info("coworker/index.html unavailable (%s); JSON fallback", exc)
        return jsonify({"instances": instances, "active": len(active), "roles": roles})


@ace_bp.route("/roles")
def roles():
    """Role catalog — all loaded RoleTemplates."""
    try:
        from icdev.tools.ace.role_loader import RoleLoader

        role_list = RoleLoader().list_roles()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace roles load failed: %s", exc)
        role_list = []
    try:
        return render_template("coworker/roles.html", roles=role_list)
    except Exception as exc:  # noqa: BLE001
        logger.info("coworker/roles.html unavailable (%s); JSON fallback", exc)
        return jsonify({"roles": [r.role_id for r in role_list]})


@ace_bp.route("/profiles/new")
def profiles_new():
    """Profile creation page — AI Assist-powered."""
    try:
        return render_template("coworker/profile_new.html")
    except Exception as exc:
        logger.info("coworker/profile_new.html unavailable (%s); JSON fallback", exc)
        return jsonify({"page": "profile_new"})


@ace_bp.route("/trust")
def trust_leaderboard():
    """NOVA TRUST — Role trust leaderboard (score + band + last event)."""
    rows: list[dict] = []
    try:
        from icdev.tools.ace.trust_calibrator import get_trust_summary
        rows = get_trust_summary()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace trust_leaderboard: get_trust_summary failed: %s", exc)
    try:
        return render_template("ace/trust.html", rows=rows)
    except Exception as exc:  # noqa: BLE001
        logger.info("ace/trust.html unavailable (%s); JSON fallback", exc)
        return jsonify({"rows": rows, "count": len(rows)})


@ace_bp.route("/<instance_id>")
def instance_detail(instance_id: str):
    """Single instance: header, coworkers, message timeline, artifacts."""
    instance: Optional[dict] = None
    coworkers: list[dict] = []
    messages: list[dict] = []
    artifacts: list[dict] = []
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
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace instance_detail read failed for %s: %s", instance_id, exc)
    finally:
        if conn is not None:
            conn.close()

    if instance is None:
        return jsonify({"error": f"instance not found: {instance_id}"}), 404

    coworker_display = {c["id"]: (c.get("display_name") or c.get("role_id")) for c in coworkers}

    try:
        return render_template(
            "coworker/instance.html",
            instance=instance,
            coworkers=coworkers,
            messages=messages,
            artifacts=artifacts,
            coworker_display=coworker_display,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("coworker/instance.html unavailable (%s); JSON fallback", exc)
        return jsonify(
            {
                "instance": instance,
                "coworkers": coworkers,
                "messages": messages,
                "artifacts": artifacts,
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


@ace_api_bp.route("/<instance_id>/abort", methods=["POST"])
def api_abort(instance_id: str):
    """Signal all coworkers to stop; marks the instance ``cancelled``.

    (The schema CHECK constraint allows ``cancelled``, not ``aborted`` — the
    controller's ``abort()`` sets the canonical terminal state.)
    """
    try:
        ACEController.get_instance().abort(instance_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace abort failed for %s: %s", instance_id, exc)
        return jsonify({"error": str(exc)}), 500
    return jsonify({"instance_id": instance_id, "state": "cancelled", "aborted": True})


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
    """Return all dispatch topics and which roles listen to each."""
    try:
        from icdev.tools.ace.role_loader import RoleLoader
        from icdev.tools.ace.event_bus import DISPATCH_TOPICS
        roles = RoleLoader().list_roles()
        topic_map: dict[str, list[str]] = {t: [] for t in sorted(DISPATCH_TOPICS)}
        for role in roles:
            for topic in (role.communication.get("listen_topics") or []):
                if topic in topic_map:
                    topic_map[topic].append(role.role_id)
        return jsonify({"ok": True, "topics": topic_map})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
