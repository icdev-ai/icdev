# CUI // SP-CTI
"""Chat context manager — lifecycle management for ICDEV chat conversations.

Operates against the ``chat_contexts`` and ``chat_messages`` tables defined in
``tools/db/init_icdev_db.py``.  Uses ``get_connection()`` (global RLS) because
these tables carry ``tenant_id`` and ``classification``.

Key responsibilities:
- Create / get / list / update / archive chat contexts
- Add and retrieve messages with turn ordering
- Persist ``coworker_instance_id`` inside ``context_config`` JSON so that a
  chat session launched as a trigger always retains the ACE instance reference

Usage::

    from icdev.tools.chat.chat_manager import ChatManager

    mgr = ChatManager(user_id="u-1", tenant_id="tenant-abc")
    ctx_id = mgr.create_context(title="Modernisation Plan", classification="CUI")
    mgr.add_message(ctx_id, role="user", content="Analyse our legacy stack")
    mgr.set_coworker_instance(ctx_id, "inst-abc123")
    print(mgr.get_coworker_instance(ctx_id))  # → "inst-abc123"
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

_CONTEXT_STATUSES = ("active", "paused", "completed", "error", "archived")
_MESSAGE_ROLES = ("user", "assistant", "system", "intervention")
_MESSAGE_CONTENT_TYPES = (
    "text", "tool_result", "error", "intervention", "summary",
    "code_block", "markdown", "phase_transition", "citation", "action_card",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str = "ctx") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# ChatManager
# ---------------------------------------------------------------------------


class ChatManager:
    """Service layer for ``chat_contexts`` / ``chat_messages``.

    Args:
        user_id: The authenticated user identifier.
        tenant_id: Tenant slug; used for RLS and context listing.
        classification: Default classification applied to new contexts/messages.
    """

    def __init__(
        self,
        user_id: str,
        tenant_id: str | None = None,
        classification: str = "CUI",
    ) -> None:
        self.user_id = user_id
        self.tenant_id = tenant_id or ""
        self.classification = classification

    # ------------------------------------------------------------------
    # Context lifecycle
    # ------------------------------------------------------------------

    def create_context(
        self,
        *,
        title: str = "",
        classification: str | None = None,
        agent_model: str = "sonnet",
        system_prompt: str | None = None,
        project_id: str | None = None,
        intake_session_id: str | None = None,
        config: dict | None = None,
    ) -> str:
        """Create a new chat context row and return its ``id``."""
        from icdev.tools.db.storage import get_connection

        ctx_id = _new_id("ctx")
        ts = _now()
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO chat_contexts
                    (id, user_id, tenant_id, title, status, agent_model,
                     system_prompt, context_config, classification,
                     intake_session_id, project_id,
                     last_activity_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ctx_id,
                    self.user_id,
                    self.tenant_id,
                    title,
                    agent_model,
                    system_prompt,
                    json.dumps(config or {}),
                    classification or self.classification,
                    intake_session_id,
                    project_id,
                    ts,
                    ts,
                    ts,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return ctx_id

    def get_context(self, context_id: str) -> dict[str, Any] | None:
        """Return a single context as a dict, or None if not found."""
        from icdev.tools.db.storage import get_connection

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM chat_contexts WHERE id = ?", (context_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["context_config"] = _safe_json(d.get("context_config"))
            return d
        finally:
            conn.close()

    def list_contexts(
        self,
        *,
        status: str | None = "active",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List contexts for this user/tenant, newest first."""
        from icdev.tools.db.storage import get_connection

        conn = get_connection()
        try:
            q = "SELECT * FROM chat_contexts WHERE user_id = ?"
            params: list[Any] = [self.user_id]
            if self.tenant_id:
                q += " AND tenant_id = ?"
                params.append(self.tenant_id)
            if status:
                q += " AND status = ?"
                params.append(status)
            q += " ORDER BY last_activity_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(q, params).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d["context_config"] = _safe_json(d.get("context_config"))
                result.append(d)
            return result
        finally:
            conn.close()

    def update_status(self, context_id: str, status: str) -> None:
        """Update the status of a context."""
        if status not in _CONTEXT_STATUSES:
            raise ValueError(f"Invalid status {status!r}. Must be one of {_CONTEXT_STATUSES}")
        from icdev.tools.db.storage import get_connection

        ts = _now()
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE chat_contexts SET status = ?, updated_at = ? WHERE id = ?",
                (status, ts, context_id),
            )
            conn.commit()
        finally:
            conn.close()

    def update_title(self, context_id: str, title: str) -> None:
        """Update the display title of a context."""
        from icdev.tools.db.storage import get_connection

        ts = _now()
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE chat_contexts SET title = ?, updated_at = ? WHERE id = ?",
                (title, ts, context_id),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # coworker_instance_id — Phase 1e of acex
    # ------------------------------------------------------------------

    def set_coworker_instance(self, context_id: str, instance_id: str) -> None:
        """Persist an ACE instance ID in this context's ``context_config``.

        Closes the gap where chat launches a co-worker but loses the reference.
        Stored under key ``coworker_instance_id`` in the ``context_config`` JSON.
        """
        from icdev.tools.db.storage import get_connection

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT context_config FROM chat_contexts WHERE id = ?", (context_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"Context not found: {context_id!r}")
            cfg = _safe_json(row[0])
            cfg["coworker_instance_id"] = instance_id
            ts = _now()
            conn.execute(
                "UPDATE chat_contexts SET context_config = ?, last_activity_at = ?, "
                "updated_at = ? WHERE id = ?",
                (json.dumps(cfg), ts, ts, context_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_coworker_instance(self, context_id: str) -> str | None:
        """Return the ACE instance ID stored in this context, or None."""
        ctx = self.get_context(context_id)
        if not ctx:
            return None
        return (ctx.get("context_config") or {}).get("coworker_instance_id")

    def update_config(self, context_id: str, updates: dict[str, Any]) -> None:
        """Merge *updates* into the context's ``context_config`` JSON."""
        from icdev.tools.db.storage import get_connection

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT context_config FROM chat_contexts WHERE id = ?", (context_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"Context not found: {context_id!r}")
            cfg = _safe_json(row[0])
            cfg.update(updates)
            ts = _now()
            conn.execute(
                "UPDATE chat_contexts SET context_config = ?, updated_at = ? WHERE id = ?",
                (json.dumps(cfg), ts, context_id),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def add_message(
        self,
        context_id: str,
        *,
        role: str,
        content: str,
        content_type: str = "text",
        classification: str | None = None,
        metadata: dict | None = None,
    ) -> int:
        """Append a message to this context and return its auto-increment id.

        Also increments ``chat_contexts.message_count`` and updates
        ``last_activity_at``.
        """
        if role not in _MESSAGE_ROLES:
            raise ValueError(f"Invalid role {role!r}. Must be one of {_MESSAGE_ROLES}")
        if content_type not in _MESSAGE_CONTENT_TYPES:
            raise ValueError(f"Invalid content_type {content_type!r}")

        from icdev.tools.db.storage import get_connection

        ts = _now()
        conn = get_connection()
        try:
            next_turn = (
                conn.execute(
                    "SELECT COALESCE(MAX(turn_number), 0) + 1 FROM chat_messages "
                    "WHERE context_id = ?",
                    (context_id,),
                ).fetchone()[0]
            )
            cur = conn.execute(
                """
                INSERT INTO chat_messages
                    (context_id, turn_number, role, content, content_type,
                     metadata, classification)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    context_id,
                    next_turn,
                    role,
                    content,
                    content_type,
                    json.dumps(metadata or {}),
                    classification or self.classification,
                ),
            )
            msg_id: int = cur.lastrowid  # type: ignore[assignment]
            conn.execute(
                "UPDATE chat_contexts "
                "SET message_count = message_count + 1, "
                "    last_activity_at = ?, updated_at = ? "
                "WHERE id = ?",
                (ts, ts, context_id),
            )
            conn.commit()
            return msg_id
        finally:
            conn.close()

    def get_messages(
        self,
        context_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return messages for *context_id* in turn order."""
        from icdev.tools.db.storage import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, turn_number, role, content, content_type, "
                "metadata, classification, is_compressed, created_at "
                "FROM chat_messages WHERE context_id = ? "
                "ORDER BY turn_number LIMIT ? OFFSET ?",
                (context_id, limit, offset),
            ).fetchall()
            result = []
            for row in rows:
                d = dict(zip(
                    ("id", "turn_number", "role", "content", "content_type",
                     "metadata", "classification", "is_compressed", "created_at"),
                    row,
                ))
                d["metadata"] = _safe_json(d.get("metadata"))
                result.append(d)
            return result
        finally:
            conn.close()

    def get_last_message(self, context_id: str) -> dict[str, Any] | None:
        """Return the most recent message, or None."""
        msgs = self.get_messages(context_id, limit=1, offset=0)
        # get_messages returns in turn order; use DESC for last
        from icdev.tools.db.storage import get_connection

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT id, turn_number, role, content, content_type, "
                "metadata, classification, is_compressed, created_at "
                "FROM chat_messages WHERE context_id = ? "
                "ORDER BY turn_number DESC LIMIT 1",
                (context_id,),
            ).fetchone()
            if not row:
                return None
            d = dict(zip(
                ("id", "turn_number", "role", "content", "content_type",
                 "metadata", "classification", "is_compressed", "created_at"),
                row,
            ))
            d["metadata"] = _safe_json(d.get("metadata"))
            return d
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def create_context(
    user_id: str,
    *,
    tenant_id: str | None = None,
    title: str = "",
    classification: str = "CUI",
    **kwargs: Any,
) -> str:
    """Module-level shorthand for ``ChatManager.create_context``."""
    mgr = ChatManager(user_id=user_id, tenant_id=tenant_id, classification=classification)
    return mgr.create_context(title=title, classification=classification, **kwargs)


def get_context(context_id: str) -> dict[str, Any] | None:
    """Module-level shorthand — user_id not needed for single-row lookup."""
    from icdev.tools.db.storage import get_connection
    import json

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM chat_contexts WHERE id = ?", (context_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["context_config"] = _safe_json(d.get("context_config"))
        return d
    finally:
        conn.close()


def set_coworker_instance(context_id: str, instance_id: str) -> None:
    """Module-level shorthand for linking an ACE instance to a chat context."""
    from icdev.tools.db.storage import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT context_config FROM chat_contexts WHERE id = ?", (context_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Context not found: {context_id!r}")
        cfg = _safe_json(row[0])
        cfg["coworker_instance_id"] = instance_id
        ts = _now()
        conn.execute(
            "UPDATE chat_contexts SET context_config = ?, last_activity_at = ?, "
            "updated_at = ? WHERE id = ?",
            (json.dumps(cfg), ts, ts, context_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_coworker_instance(context_id: str) -> str | None:
    """Return the ACE instance ID linked to *context_id*, or None."""
    ctx = get_context(context_id)
    if not ctx:
        return None
    return (ctx.get("context_config") or {}).get("coworker_instance_id")


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _safe_json(value: str | dict | None) -> dict:
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or "{}") or {}
    except Exception:
        return {}
