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


@chat_api.route("/<context_id>/link-intake", methods=["PATCH"])
def link_intake(context_id):
    """Link an intake session to a chat context (activates RICOAS extension hooks).

    Body: {intake_session_id}
    """
    err = _require_chat()
    if err:
        return err

    data = request.get_json(force=True, silent=True) or {}
    intake_session_id = data.get("intake_session_id", "").strip()
    if not intake_session_id:
        return jsonify({"error": "intake_session_id required"}), 400

    result = chat_manager.link_intake_session(context_id, intake_session_id)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


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
# Kanban task routes (Chat-Kanban integration)
# ---------------------------------------------------------------------------


@chat_api.route("/<context_id>/tasks", methods=["GET"])
def list_context_tasks(context_id):
    """List kanban tasks linked to this chat context.

    Returns tasks tagged with dispatch_source='chat:{context_id}'.
    """
    try:
        from tools.chat.kanban_bridge import list_context_tasks as _list
        tasks = _list(context_id)
        return jsonify({"tasks": tasks, "total": len(tasks), "context_id": context_id})
    except Exception as exc:
        return jsonify({"error": str(exc), "tasks": [], "total": 0}), 500


@chat_api.route("/<context_id>/tasks", methods=["POST"])
def create_context_task(context_id):
    """Create a kanban task linked to this chat context.

    Body: {title, description?, task_type?, priority?, depends_on?}
    """
    data = request.get_json(force=True, silent=True) or {}
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    try:
        from tools.chat.kanban_bridge import create_context_task as _create
        task = _create(
            context_id,
            title=title,
            description=data.get("description", ""),
            task_type=data.get("task_type", "build"),
            priority=data.get("priority", "medium"),
            depends_on=data.get("depends_on"),
        )
        return jsonify(task), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@chat_api.route("/<context_id>/vv-chain", methods=["POST"])
def create_vv_chain(context_id):
    """Auto-create CodeLens + Coherence + E2E tasks linked to this context.

    Body: {canvas?}
    """
    data = request.get_json(force=True, silent=True) or {}
    try:
        from tools.chat.kanban_bridge import create_vv_chain as _chain
        tasks = _chain(context_id, canvas=data.get("canvas", ""))
        return jsonify({"tasks": tasks, "total": len(tasks)}), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


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
