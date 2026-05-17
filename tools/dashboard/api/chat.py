# [TEMPLATE: CUI // SP-CTI]
"""Flask Blueprint for multi-stream parallel chat API (Phase 44 — D257-D260).

Provides endpoints for creating/managing chat contexts, sending messages,
mid-stream intervention, and dirty-tracking state queries.
"""

import sys
from pathlib import Path


from flask import Blueprint, jsonify, request

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Backend imports (graceful)
# ---------------------------------------------------------------------------

try:
    from tools.dashboard.chat_manager import chat_manager

    _HAS_CHAT = True
except ImportError:
    _HAS_CHAT = False
    chat_manager = None

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------

chat_api = Blueprint("chat_api", __name__, url_prefix="/api/chat")


def _require_chat():
    """Check that chat manager is available."""
    if not _HAS_CHAT or chat_manager is None:
        return jsonify({"error": "Chat manager not available"}), 503
    return None


# ---------------------------------------------------------------------------
# Context endpoints
# ---------------------------------------------------------------------------


@chat_api.route("/contexts", methods=["POST"])
def create_context():
    """Create a new chat context.

    Body: {user_id, tenant_id?, title?, project_id?, agent_model?, system_prompt?}
    """
    err = _require_chat()
    if err:
        return err

    data = request.get_json(force=True, silent=True) or {}
    user_id = data.get("user_id", "").strip()
    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    result = chat_manager.create_context(
        user_id=user_id,
        tenant_id=data.get("tenant_id", ""),
        title=data.get("title", ""),
        project_id=data.get("project_id", ""),
        agent_model=data.get("agent_model", "sonnet"),
        system_prompt=data.get("system_prompt", ""),
    )

    if "error" in result:
        return jsonify(result), 429  # Rate limit / max concurrent
    return jsonify(result), 201


@chat_api.route("/contexts", methods=["GET"])
def list_contexts():
    """List chat contexts.

    Query params: user_id?, tenant_id?, include_closed?
    """
    err = _require_chat()
    if err:
        return err

    user_id = request.args.get("user_id", "")
    tenant_id = request.args.get("tenant_id", "")
    include_closed = request.args.get("include_closed", "false").lower() == "true"

    contexts = chat_manager.list_contexts(
        user_id=user_id,
        tenant_id=tenant_id,
        include_closed=include_closed,
    )
    return jsonify({"contexts": contexts, "total": len(contexts)})


@chat_api.route("/contexts/<context_id>", methods=["GET"])
def get_context(context_id):
    """Get context details with recent messages."""
    err = _require_chat()
    if err:
        return err

    ctx = chat_manager.get_context(context_id)
    if not ctx:
        return jsonify({"error": "Context not found"}), 404

    # Include recent messages
    messages = chat_manager.get_messages(context_id, since_turn=0, limit=50)
    ctx["messages"] = messages
    return jsonify(ctx)


# ---------------------------------------------------------------------------
# Message endpoints
# ---------------------------------------------------------------------------


@chat_api.route("/<context_id>/send", methods=["POST"])
def send_message(context_id):
    """Send a message to a context.

    Body: {content, role?}
    """
    err = _require_chat()
    if err:
        return err

    data = request.get_json(force=True, silent=True) or {}
    content = data.get("content", "").strip()
    if not content:
        return jsonify({"error": "content required"}), 400

    role = data.get("role", "user")
    result = chat_manager.send_message(context_id, content, role=role)

    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


@chat_api.route("/<context_id>/intervene", methods=["POST"])
def intervene(context_id):
    """Mid-stream intervention (D265-D267).

    Body: {message}
    """
    err = _require_chat()
    if err:
        return err

    data = request.get_json(force=True, silent=True) or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "message required"}), 400

    result = chat_manager.intervene(context_id, message)

    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


@chat_api.route("/<context_id>/messages", methods=["GET"])
def get_messages(context_id):
    """Get messages for a context.

    Query params: since? (turn number), limit?
    """
    err = _require_chat()
    if err:
        return err

    since = request.args.get("since", 0, type=int)
    limit = request.args.get("limit", 100, type=int)

    messages = chat_manager.get_messages(context_id, since_turn=since, limit=limit)
    return jsonify({"context_id": context_id, "messages": messages, "total": len(messages)})


# ---------------------------------------------------------------------------
# State endpoints
# ---------------------------------------------------------------------------


@chat_api.route("/<context_id>/state", methods=["GET"])
def get_state(context_id):
    """Get context state with dirty-tracking (Feature 4).

    Query params: since_version? (dirty version)
    """
    err = _require_chat()
    if err:
        return err

    ctx = chat_manager.get_context(context_id)
    if not ctx:
        return jsonify({"error": "Context not found"}), 404

    since_version = request.args.get("since_version", 0, type=int)

    # Get incremental updates from state tracker if available
    try:
        from tools.dashboard.state_tracker import state_tracker

        client_id = request.args.get("client_id", request.remote_addr or "unknown")
        updates = state_tracker.get_updates(client_id, context_id, since_version)
        ctx["state_updates"] = updates
    except ImportError:
        ctx["state_updates"] = {"up_to_date": True, "changes": []}

    return jsonify(ctx)


@chat_api.route("/<context_id>/close", methods=["POST"])
def close_context(context_id):
    """Close/archive a chat context."""
    err = _require_chat()
    if err:
        return err

    result = chat_manager.close_context(context_id)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


# ---------------------------------------------------------------------------
# Use Cases catalog (FORGE-pattern: reads args/use_cases.yaml)
# ---------------------------------------------------------------------------

_USE_CASES_PATH = BASE_DIR / "args" / "use_cases.yaml"


def _uc_load_yaml():
    import yaml as _yaml
    try:
        with open(_USE_CASES_PATH, "r", encoding="utf-8") as fh:
            return (_yaml.safe_load(fh) or {}).get("use_cases", [])
    except Exception:
        return []


def _uc_init_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS use_case_overrides (
        id TEXT PRIMARY KEY,
        label TEXT, description TEXT, icon TEXT, badge TEXT,
        agent_model TEXT, ricoas INTEGER, boost_threshold INTEGER,
        system_prompt TEXT, seed_message TEXT,
        canvas_wiring TEXT, quick_actions TEXT,
        updated_at TEXT, updated_by TEXT
    )""")
    conn.commit()


def _uc_apply_override(base, row):
    import json as _json
    if not row:
        return base
    result = dict(base)
    for col in ("label", "description", "icon", "badge", "agent_model",
                "system_prompt", "seed_message"):
        if row[col] is not None:
            result[col] = row[col]
    if row["ricoas"] is not None:
        result["ricoas"] = bool(row["ricoas"])
    if row["boost_threshold"] is not None:
        result["boost_threshold"] = row["boost_threshold"]
    for jcol in ("canvas_wiring", "quick_actions"):
        if row[jcol] is not None:
            try:
                result[jcol] = _json.loads(row[jcol])
            except Exception:
                pass
    return result


@chat_api.route("/use-cases", methods=["GET"])
def list_use_cases():
    """Return use case catalog (YAML defaults merged with DB overrides)."""
    from tools.db.storage import get_connection as _gc
    cases = _uc_load_yaml()
    category = request.args.get("category", "").strip().lower()
    query = request.args.get("q", "").strip().lower()

    overrides = {}
    try:
        with _gc() as conn:
            for row in conn.execute("SELECT * FROM use_case_overrides").fetchall():
                overrides[row["id"]] = row
    except Exception:
        pass

    merged = [_uc_apply_override(c, overrides.get(c.get("id", ""))) for c in cases]
    if category:
        merged = [c for c in merged if c.get("category", "").lower() == category]
    if query:
        merged = [c for c in merged if query in c.get("label", "").lower()
                  or query in (c.get("description") or "").lower()]

    summary = [
        {
            "id": c.get("id", ""),
            "label": c.get("label", ""),
            "category": c.get("category", ""),
            "icon": c.get("icon", ""),
            "description": (c.get("description") or "").strip(),
            "badge": c.get("badge", ""),
            "agent_model": c.get("agent_model", "sonnet"),
            "ricoas": c.get("ricoas", False),
            "boost_threshold": c.get("boost_threshold", 70),
            "canvas_wiring": c.get("canvas_wiring", []),
            "quick_actions": c.get("quick_actions", []),
        }
        for c in merged
    ]
    return jsonify({"use_cases": summary, "total": len(summary)})


@chat_api.route("/use-cases/<use_case_id>", methods=["GET"])
def get_use_case(use_case_id):
    """Return full use case definition (YAML + DB override merged)."""
    from tools.db.storage import get_connection as _gc
    base = next((c for c in _uc_load_yaml() if c.get("id") == use_case_id), None)
    if not base:
        return jsonify({"error": "Use case not found"}), 404
    row = None
    _exc_info = None
    try:
        with _gc() as conn:
            row = conn.execute(
                "SELECT * FROM use_case_overrides WHERE id = ?", (use_case_id,)
            ).fetchone()
    except Exception as _e:
        _exc_info = str(_e)
    import logging as _log
    _log.getLogger("icdev.uc").warning(
        "get_use_case %s: row=%r exc=%r", use_case_id, bool(row), _exc_info
    )
    return jsonify(_uc_apply_override(dict(base), row))


@chat_api.route("/use-cases/<use_case_id>", methods=["PUT"])
def update_use_case(use_case_id):
    """Persist user overrides for a use case (YAML unchanged, overrides in DB)."""
    import json as _json
    from datetime import datetime, timezone
    from tools.db.storage import get_connection as _gc
    body = request.get_json(silent=True) or {}
    now = datetime.now(timezone.utc).isoformat()
    cw = body.get("canvas_wiring")
    qa = body.get("quick_actions")
    try:
        with _gc() as conn:
            _uc_init_table(conn)
            conn.execute("""
                INSERT INTO use_case_overrides
                    (id,label,description,icon,badge,agent_model,ricoas,
                     boost_threshold,system_prompt,seed_message,
                     canvas_wiring,quick_actions,updated_at,updated_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    label=excluded.label, description=excluded.description,
                    icon=excluded.icon, badge=excluded.badge,
                    agent_model=excluded.agent_model, ricoas=excluded.ricoas,
                    boost_threshold=excluded.boost_threshold,
                    system_prompt=excluded.system_prompt,
                    seed_message=excluded.seed_message,
                    canvas_wiring=excluded.canvas_wiring,
                    quick_actions=excluded.quick_actions,
                    updated_at=excluded.updated_at,
                    updated_by=excluded.updated_by
            """, (
                use_case_id,
                body.get("label"), body.get("description"), body.get("icon"),
                body.get("badge"), body.get("agent_model"),
                1 if body.get("ricoas") else 0,
                body.get("boost_threshold"),
                body.get("system_prompt"), body.get("seed_message"),
                _json.dumps(cw) if cw is not None else None,
                _json.dumps(qa) if qa is not None else None,
                now, body.get("updated_by", "dashboard-user"),
            ))
            conn.commit()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "id": use_case_id, "updated_at": now})


@chat_api.route("/use-cases/<use_case_id>/override", methods=["DELETE"])
def reset_use_case(use_case_id):
    """Delete DB override — restores YAML factory defaults."""
    from tools.db.storage import get_connection as _gc
    try:
        with _gc() as conn:
            _uc_init_table(conn)
            conn.execute("DELETE FROM use_case_overrides WHERE id=?", (use_case_id,))
            conn.commit()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "id": use_case_id, "reset": True})


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


@chat_api.route("/diagnostics", methods=["GET"])
def diagnostics():
    """Chat system diagnostics."""
    err = _require_chat()
    if err:
        return err

    return jsonify(chat_manager.get_diagnostics())
