#!/usr/bin/env python3
# CUI // SP-CTI
"""Multi-stream parallel chat manager (Phase 44 — D257-D260, D265-D267).

Thread-per-context execution model adapted from Agent Zero's DeferredTask pattern.
Contexts scoped to (user_id, tenant_id). Max 5 concurrent per user.
Intervention via atomic field, checked at 3 points per agent loop iteration.

Usage:
    from tools.dashboard.chat_manager import chat_manager

    ctx = chat_manager.create_context("user-1", "tenant-1", "My Chat")
    chat_manager.send_message(ctx["context_id"], "Hello!", role="user")
    chat_manager.intervene(ctx["context_id"], "Stop and do this instead")
"""

import json
import logging
import sqlite3
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("icdev.chat_manager")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Max concurrent contexts per user
MAX_CONCURRENT_PER_USER = 5


# ---------------------------------------------------------------------------
# Extension hook integration (Feature 2)
# ---------------------------------------------------------------------------

def _dispatch_hook(hook_name: str, context: dict) -> dict:
    """Dispatch extension hook if available."""
    try:
        from tools.extensions.extension_manager import extension_manager, ExtensionPoint
        ep = ExtensionPoint(hook_name)
        return extension_manager.dispatch(ep, context)
    except (ImportError, ValueError):
        return context


def _mark_dirty(context_id: str, change_type: str, data: Optional[dict] = None):
    """Mark context dirty on state tracker if available (Feature 4)."""
    try:
        from tools.dashboard.state_tracker import state_tracker
        state_tracker.mark_dirty(context_id, change_type, data)
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# ChatContext — per-context state
# ---------------------------------------------------------------------------

class ChatContext:
    """Represents a single chat stream with its own message queue and thread."""

    def __init__(
        self,
        context_id: str,
        user_id: str,
        tenant_id: str = "",
        title: str = "",
        project_id: str = "",
        agent_model: str = "sonnet",
        system_prompt: str = "",
    ):
        self.context_id = context_id
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.title = title
        self.project_id = project_id
        self.agent_model = agent_model
        self.system_prompt = system_prompt

        self.status = "active"  # active, paused, completed, error, archived
        self.message_queue: deque = deque()
        self.turn_number = 0
        self.dirty_version = 0
        self.is_processing = False
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.last_activity_at = self.created_at

        # Intervention (D265-D267)
        self._intervention_lock = threading.Lock()
        self._intervention_message: Optional[str] = None
        self._checkpoint: Optional[dict] = None

        # Agent thread
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def set_intervention(self, message: str) -> None:
        """Thread-safe set intervention message."""
        with self._intervention_lock:
            self._intervention_message = message

    def check_intervention(self) -> Optional[str]:
        """Check-and-clear intervention (returns message or None)."""
        with self._intervention_lock:
            msg = self._intervention_message
            self._intervention_message = None
            return msg

    def save_checkpoint(self, data: dict) -> None:
        """Save current progress as checkpoint."""
        self._checkpoint = {
            "turn_number": self.turn_number,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }

    def to_dict(self) -> dict:
        return {
            "context_id": self.context_id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "title": self.title,
            "project_id": self.project_id,
            "agent_model": self.agent_model,
            "status": self.status,
            "message_count": self.turn_number,
            "dirty_version": self.dirty_version,
            "queue_depth": len(self.message_queue),
            "is_processing": self.is_processing,
            "created_at": self.created_at,
            "last_activity_at": self.last_activity_at,
        }


# ---------------------------------------------------------------------------
# ChatManager — singleton managing all chat contexts
# ---------------------------------------------------------------------------

class ChatManager:
    """Manages multi-stream parallel chat contexts.

    Singleton pattern — use the module-level ``chat_manager`` instance.
    """

    def __init__(self):
        self._contexts: Dict[str, ChatContext] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Context CRUD
    # ------------------------------------------------------------------

    def create_context(
        self,
        user_id: str,
        tenant_id: str = "",
        title: str = "",
        project_id: str = "",
        agent_model: str = "sonnet",
        system_prompt: str = "",
    ) -> dict:
        """Create a new chat context. Returns context dict."""
        with self._lock:
            # Check concurrent limit per user
            user_contexts = [
                c for c in self._contexts.values()
                if c.user_id == user_id and c.status == "active"
            ]
            if len(user_contexts) >= MAX_CONCURRENT_PER_USER:
                return {
                    "error": f"Max {MAX_CONCURRENT_PER_USER} concurrent contexts per user",
                    "active_count": len(user_contexts),
                }

        context_id = f"ctx-{uuid.uuid4().hex[:12]}"
        ctx = ChatContext(
            context_id=context_id,
            user_id=user_id,
            tenant_id=tenant_id,
            title=title or f"Chat {context_id[-6:]}",
            project_id=project_id,
            agent_model=agent_model,
            system_prompt=system_prompt,
        )

        with self._lock:
            self._contexts[context_id] = ctx

        # Persist to DB
        self._db_create_context(ctx)

        # Start agent loop thread
        ctx._thread = threading.Thread(
            target=self._agent_loop,
            args=(context_id,),
            daemon=True,
        )
        ctx._thread.start()

        # Dispatch hook
        _dispatch_hook("agent_start", {"context_id": context_id, "user_id": user_id})
        _mark_dirty(context_id, "context_created", ctx.to_dict())

        logger.info("Created chat context %s for user %s", context_id, user_id)
        return ctx.to_dict()

    def list_contexts(
        self,
        user_id: str = "",
        tenant_id: str = "",
        include_closed: bool = False,
    ) -> List[dict]:
        """List chat contexts, optionally filtered."""
        with self._lock:
            results = []
            for ctx in self._contexts.values():
                if user_id and ctx.user_id != user_id:
                    continue
                if tenant_id and ctx.tenant_id != tenant_id:
                    continue
                if not include_closed and ctx.status in ("completed", "archived"):
                    continue
                results.append(ctx.to_dict())
            return results

    def get_context(self, context_id: str) -> Optional[dict]:
        """Get a single context by ID."""
        with self._lock:
            ctx = self._contexts.get(context_id)
            return ctx.to_dict() if ctx else None

    def close_context(self, context_id: str) -> dict:
        """Close/archive a chat context."""
        with self._lock:
            ctx = self._contexts.get(context_id)
            if not ctx:
                return {"error": "Context not found"}
            ctx.status = "completed"
            ctx._stop_event.set()

        self._db_update_status(context_id, "completed")
        _dispatch_hook("agent_end", {"context_id": context_id})
        _mark_dirty(context_id, "context_closed")

        logger.info("Closed chat context %s", context_id)
        return {"context_id": context_id, "status": "completed"}

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    def send_message(
        self,
        context_id: str,
        content: str,
        role: str = "user",
    ) -> dict:
        """Send a message to a context. Queued if busy."""
        with self._lock:
            ctx = self._contexts.get(context_id)
            if not ctx:
                return {"error": "Context not found"}
            if ctx.status not in ("active", "paused"):
                return {"error": f"Context is {ctx.status}"}

        # Dispatch pre-hook
        hook_ctx = _dispatch_hook("chat_message_before", {
            "context_id": context_id,
            "content": content,
            "role": role,
        })
        content = hook_ctx.get("content", content)
        role = hook_ctx.get("role", role)

        # Record message in DB
        ctx.turn_number += 1
        turn = ctx.turn_number
        self._db_insert_message(context_id, turn, role, content)

        # Queue for processing
        ctx.message_queue.append({
            "turn_number": turn,
            "role": role,
            "content": content,
        })
        ctx.last_activity_at = datetime.now(timezone.utc).isoformat()

        _mark_dirty(context_id, "new_message", {
            "turn_number": turn,
            "role": role,
            "content": content[:200],
        })

        return {
            "context_id": context_id,
            "turn_number": turn,
            "role": role,
            "queued": ctx.is_processing,
            "queue_depth": len(ctx.message_queue),
        }

    def intervene(self, context_id: str, message: str) -> dict:
        """Mid-stream intervention (D265-D267).

        Sets atomic intervention flag checked at 3 points in agent loop.
        """
        with self._lock:
            ctx = self._contexts.get(context_id)
            if not ctx:
                return {"error": "Context not found"}

        ctx.set_intervention(message)

        # Record intervention message
        ctx.turn_number += 1
        turn = ctx.turn_number
        self._db_insert_message(
            context_id, turn, "intervention", message,
            content_type="intervention",
        )

        _mark_dirty(context_id, "intervention", {
            "turn_number": turn,
            "message": message[:200],
        })

        logger.info("Intervention set on context %s", context_id)
        return {
            "context_id": context_id,
            "turn_number": turn,
            "intervention_set": True,
        }

    def get_messages(
        self,
        context_id: str,
        since_turn: int = 0,
        limit: int = 100,
    ) -> List[dict]:
        """Get messages for a context, optionally since a turn number."""
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT turn_number, role, content, content_type,
                          is_compressed, compression_tier, classification, created_at
                   FROM chat_messages
                   WHERE context_id = ? AND turn_number > ?
                   ORDER BY turn_number
                   LIMIT ?""",
                (context_id, since_turn, limit),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    # ------------------------------------------------------------------
    # Agent loop (background thread per context)
    # ------------------------------------------------------------------

    def _agent_loop(self, context_id: str) -> None:
        """Background worker thread for a chat context.

        Processes queued messages and checks for interventions.
        """
        ctx = self._contexts.get(context_id)
        if not ctx:
            return

        while not ctx._stop_event.is_set():
            # Intervention check point 1: before queue pop
            intervention = ctx.check_intervention()
            if intervention:
                self._handle_intervention(ctx, intervention)
                continue

            # Pop next message from queue
            if not ctx.message_queue:
                time.sleep(0.1)  # Small sleep to prevent busy-wait
                continue

            msg = ctx.message_queue.popleft()
            ctx.is_processing = True
            _mark_dirty(context_id, "processing_started", {"turn": msg["turn_number"]})

            # Create a task record
            task_id = f"task-{uuid.uuid4().hex[:12]}"
            self._db_create_task(task_id, context_id, "message", msg["content"])

            try:
                # Intervention check point 2: before LLM call
                intervention = ctx.check_intervention()
                if intervention:
                    ctx.save_checkpoint({"interrupted_message": msg})
                    ctx.message_queue.appendleft(msg)  # Re-queue
                    self._handle_intervention(ctx, intervention)
                    continue

                # Process message through LLM
                response = self._process_message(ctx, msg)

                # Intervention check point 3: after LLM response
                intervention = ctx.check_intervention()
                if intervention:
                    # Save current response as checkpoint
                    ctx.save_checkpoint({
                        "interrupted_message": msg,
                        "partial_response": response,
                    })
                    self._handle_intervention(ctx, intervention)
                    continue

                # Record assistant response
                ctx.turn_number += 1
                self._db_insert_message(
                    context_id, ctx.turn_number, "assistant", response,
                )

                # Dispatch post-hook
                _dispatch_hook("chat_message_after", {
                    "context_id": context_id,
                    "role": "assistant",
                    "content": response,
                    "turn_number": ctx.turn_number,
                })

                self._db_complete_task(task_id, response)
                _mark_dirty(context_id, "new_message", {
                    "turn_number": ctx.turn_number,
                    "role": "assistant",
                    "content": response[:200],
                })

            except Exception as exc:
                logger.error("Error processing message in %s: %s", context_id, exc)
                ctx.turn_number += 1
                error_msg = f"Error: {type(exc).__name__}: {exc}"
                self._db_insert_message(
                    context_id, ctx.turn_number, "system", error_msg,
                    content_type="error",
                )
                self._db_fail_task(task_id, str(exc))
                _mark_dirty(context_id, "error", {"error": error_msg[:200]})

            finally:
                ctx.is_processing = False
                ctx.last_activity_at = datetime.now(timezone.utc).isoformat()

        logger.info("Agent loop exited for context %s", context_id)

    def _process_message(self, ctx: ChatContext, msg: dict) -> str:
        """Process a single message through LLM router.

        Falls back to echo response if LLM is unavailable.
        """
        try:
            from tools.llm.router import LLMRouter
            router = LLMRouter()

            # Build conversation history for context
            messages = self.get_messages(ctx.context_id, since_turn=0, limit=50)
            conversation = []
            if ctx.system_prompt:
                conversation.append({"role": "system", "content": ctx.system_prompt})
            for m in messages:
                r = m.get("role", "user")
                if r == "intervention":
                    r = "user"
                if r in ("user", "assistant", "system"):
                    conversation.append({"role": r, "content": m["content"]})

            response = router.generate(
                function_name="chat_response",
                messages=conversation,
                model_hint=ctx.agent_model,
            )
            return response.get("content", str(response)) if isinstance(response, dict) else str(response)

        except (ImportError, Exception) as exc:
            logger.debug("LLM unavailable for chat: %s — using echo fallback", exc)
            content = msg.get("content", "")
            return f"[Agent {ctx.agent_model}] Acknowledged: {content[:500]}"

    def _handle_intervention(self, ctx: ChatContext, message: str) -> None:
        """Process an intervention message with priority."""
        logger.info("Processing intervention in context %s", ctx.context_id)

        # Process intervention through LLM
        response = self._process_message(ctx, {
            "content": f"[INTERVENTION] {message}",
            "role": "user",
        })

        # Record intervention response
        ctx.turn_number += 1
        self._db_insert_message(
            ctx.context_id, ctx.turn_number, "assistant", response,
            content_type="text",
        )

        _mark_dirty(ctx.context_id, "intervention_response", {
            "turn_number": ctx.turn_number,
            "content": response[:200],
        })

    # ------------------------------------------------------------------
    # Database operations
    # ------------------------------------------------------------------

    def _get_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        return conn

    def _db_create_context(self, ctx: ChatContext) -> None:
        try:
            conn = self._get_db()
            conn.execute(
                """INSERT INTO chat_contexts
                   (id, user_id, tenant_id, title, status, project_id,
                    agent_model, system_prompt, dirty_version, message_count,
                    classification, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CUI', ?, ?)""",
                (
                    ctx.context_id, ctx.user_id, ctx.tenant_id, ctx.title,
                    ctx.status, ctx.project_id, ctx.agent_model,
                    ctx.system_prompt, 0, 0,
                    ctx.created_at, ctx.created_at,
                ),
            )
            conn.commit()
            conn.close()
        except sqlite3.OperationalError as exc:
            logger.debug("DB write skipped (table may not exist): %s", exc)

    def _db_update_status(self, context_id: str, status: str) -> None:
        try:
            conn = self._get_db()
            conn.execute(
                "UPDATE chat_contexts SET status = ?, updated_at = ? WHERE id = ?",
                (status, datetime.now(timezone.utc).isoformat(), context_id),
            )
            conn.commit()
            conn.close()
        except sqlite3.OperationalError:
            pass

    def _db_insert_message(
        self,
        context_id: str,
        turn_number: int,
        role: str,
        content: str,
        content_type: str = "text",
        metadata: Optional[dict] = None,
    ) -> None:
        try:
            conn = self._get_db()
            conn.execute(
                """INSERT INTO chat_messages
                   (context_id, turn_number, role, content, content_type,
                    metadata, classification, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'CUI', ?)""",
                (
                    context_id, turn_number, role, content, content_type,
                    json.dumps(metadata) if metadata else None,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.execute(
                "UPDATE chat_contexts SET message_count = ?, dirty_version = dirty_version + 1, updated_at = ? WHERE id = ?",
                (turn_number, datetime.now(timezone.utc).isoformat(), context_id),
            )
            conn.commit()
            conn.close()
        except sqlite3.OperationalError as exc:
            logger.debug("DB message insert skipped: %s", exc)

    def _db_create_task(
        self, task_id: str, context_id: str, task_type: str, input_text: str
    ) -> None:
        try:
            conn = self._get_db()
            conn.execute(
                """INSERT INTO chat_tasks
                   (id, context_id, task_type, status, input_text,
                    classification, created_at)
                   VALUES (?, ?, ?, 'processing', ?, 'CUI', ?)""",
                (
                    task_id, context_id, task_type, input_text[:2000],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            conn.close()
        except sqlite3.OperationalError:
            pass

    def _db_complete_task(self, task_id: str, output_text: str) -> None:
        try:
            conn = self._get_db()
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE chat_tasks SET status = 'completed', output_text = ?, completed_at = ? WHERE id = ?",
                (output_text[:5000], now, task_id),
            )
            conn.commit()
            conn.close()
        except sqlite3.OperationalError:
            pass

    def _db_fail_task(self, task_id: str, error: str) -> None:
        try:
            conn = self._get_db()
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE chat_tasks SET status = 'failed', error_message = ?, completed_at = ? WHERE id = ?",
                (error[:2000], now, task_id),
            )
            conn.commit()
            conn.close()
        except sqlite3.OperationalError:
            pass

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def get_diagnostics(self) -> dict:
        """Return diagnostic info for monitoring."""
        with self._lock:
            return {
                "total_contexts": len(self._contexts),
                "active_contexts": sum(
                    1 for c in self._contexts.values() if c.status == "active"
                ),
                "processing": sum(
                    1 for c in self._contexts.values() if c.is_processing
                ),
                "total_queued": sum(
                    len(c.message_queue) for c in self._contexts.values()
                ),
            }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
chat_manager = ChatManager()
