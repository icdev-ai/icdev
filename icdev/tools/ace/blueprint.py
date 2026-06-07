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
import logging
import os
from pathlib import Path
from typing import Any, Optional

from flask import Blueprint, jsonify, render_template, request

# Module-level import — must not raise at startup.  controller.py imports only the
# stdlib at module scope, so this is safe and gives the blueprint a stable handle.
from icdev.tools.ace.controller import ACEController
from icdev.tools.ace import constants as _const

logger = logging.getLogger("icdev.ace.blueprint")

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
    """Launch an ACE run (non-blocking).  Returns the new ``instance_id``."""
    data = request.get_json(silent=True) or {}
    problem_text = (data.get("problem_text") or data.get("question") or data.get("text") or "").strip()
    if not problem_text:
        return jsonify({"error": "problem_text is required"}), 400

    try:
        instance_id = ACEController.get_instance().launch(
            problem_text=problem_text,
            trigger_source=(data.get("trigger_source") or "dashboard"),
            trigger_ref=(data.get("trigger_ref") or ""),
            user_id=(data.get("user_id") or "dashboard"),
            project_id=(data.get("project_id") or ""),
            preset_label=(data.get("preset_label") or ""),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace launch failed: %s", exc)
        return jsonify({"error": str(exc)}), 500

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


@ace_api_bp.route("/<instance_id>/delete", methods=["POST"])
def api_delete(instance_id: str):
    """Delete a single inactive ACE instance and all cascaded data.

    Active instances (assembling, pending, active, paused) are rejected.
    Cascades to ace_coworkers, ace_messages, ace_artifacts, ace_agent_workflows.
    ace_audit_log is intentionally preserved (append-only).
    """
    conn = None
    try:
        conn = _db()
        # Verify the instance exists and is not active
        row = _one(
            conn.execute(
                _q(conn, "SELECT id, state FROM ace_instances WHERE id = ?"),
                (instance_id,),
            )
        )
        if row is None:
            return jsonify({"error": f"instance not found: {instance_id}"}), 404
        if row.get("state") in _ACTIVE_STATES:
            return (
                jsonify(
                    {
                        "error": (
                            f"Cannot delete active instance {instance_id} "
                            f"(state={row['state']}). Abort it first."
                        )
                    }
                ),
                409,
            )
        # Hard delete — FK ON DELETE CASCADE handles children
        conn.execute(_q(conn, "DELETE FROM ace_instances WHERE id = ?"), (instance_id,))
        conn.commit()
        return jsonify({"deleted": True, "instance_id": instance_id})
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace delete failed for %s: %s", instance_id, exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn is not None:
            conn.close()


@ace_api_bp.route("/delete-all", methods=["POST"])
def api_delete_all():
    """Bulk-delete all inactive ACE instances.

    Body: ``{"except": ["id1", "id2"]}`` — optional list of instance IDs to preserve.
    Returns the count and list of deleted IDs.
    """
    data = request.get_json(silent=True) or {}
    except_ids = data.get("except") or []
    if not isinstance(except_ids, list):
        except_ids = []
    except_ids = [str(x) for x in except_ids if x]

    deleted_ids: list[str] = []
    conn = None
    try:
        conn = _db()
        # Build the WHERE clause
        active_placeholders = ",".join(["?"] * len(_ACTIVE_STATES))
        if except_ids:
            except_placeholders = ",".join(["?"] * len(except_ids))
            sql = (
                f"SELECT id FROM ace_instances "
                f"WHERE state NOT IN ({active_placeholders}) "
                f"AND id NOT IN ({except_placeholders})"
            )
            params = list(_ACTIVE_STATES) + except_ids
        else:
            sql = (
                f"SELECT id FROM ace_instances "
                f"WHERE state NOT IN ({active_placeholders})"
            )
            params = list(_ACTIVE_STATES)

        # Materialize IDs first so we can report them
        rows = _rows(conn.execute(_q(conn, sql), tuple(params)))
        to_delete = [r["id"] for r in rows]

        if to_delete:
            placeholders = ",".join(["?"] * len(to_delete))
            conn.execute(
                _q(conn, f"DELETE FROM ace_instances WHERE id IN ({placeholders})"),
                tuple(to_delete),
            )
            conn.commit()
            deleted_ids = to_delete

        return jsonify({"deleted": len(deleted_ids), "instance_ids": deleted_ids})
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace delete-all failed: %s", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn is not None:
            conn.close()


@ace_api_bp.route("/presets", methods=["GET"])
def api_presets():
    """Return curated launch presets grouped by canvas.

    Reads ``args/ace/launch_presets.yaml`` and returns JSON shaped as::

        {"presets": [{"label", "icon", "canvas", "prompt", "suggested_roles"}]}
    """
    import yaml

    presets_path = (
        Path(__file__).resolve().parents[3] / "args" / "ace" / "launch_presets.yaml"
    )
    presets: list[dict[str, Any]] = []
    if presets_path.exists():
        try:
            with presets_path.open("r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
                presets = list(raw.get("presets", []))
        except Exception as exc:  # noqa: BLE001
            logger.warning("ace presets load failed: %s", exc)
    # Group by canvas for convenient frontend rendering
    by_canvas: dict[str, list[dict[str, Any]]] = {}
    for p in presets:
        canvas = p.get("canvas") or "general"
        by_canvas.setdefault(canvas, []).append(p)
    return jsonify({"presets": presets, "by_canvas": by_canvas})


@ace_api_bp.route("/roles", methods=["GET"])
def api_roles():
    """Return all loaded ACE roles as lightweight JSON."""
    try:
        from icdev.tools.ace.role_loader import RoleLoader

        roles = RoleLoader().list_roles()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ace roles load failed: %s", exc)
        roles = []
    return jsonify(
        {
            "roles": [
                {
                    "role_id": r.role_id,
                    "display_name": r.display_name,
                    "description": r.description,
                    "trust_tier": r.trust_tier,
                    "version": r.version,
                    "llm_function": r.llm_function,
                }
                for r in roles
            ],
            "count": len(roles),
        }
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
